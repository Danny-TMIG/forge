"""proofcache — verified claims as retrievable artifacts.

Cross-cut: verify-hub (verdicts) × index-hub (retrievables).
Not a hub. Uses two existing hubs. Adds no axis.
"""
from __future__ import annotations
import hashlib, json, time
from forge.mesh import Mesh


def _canonical(claim: str) -> str:
    return " ".join(claim.lower().split())


def _claim_hash(claim: str) -> str:
    return hashlib.sha256(_canonical(claim).encode()).hexdigest()[:16]


def _exact(mesh: Mesh, claim: str) -> dict | None:
    try:
        r = mesh.call("index-hub", "get",
                      {"kind": "proof", "ref": _claim_hash(claim)})
    except Exception:
        return None
    if not r or not r.get("body"):
        return None
    try:
        return json.loads(r["body"])
    except Exception:
        return None


def _similar(mesh: Mesh, claim: str, k: int = 3) -> list[dict]:
    try:
        out = mesh.call("index-hub", "search",
                        {"q": claim, "k": k, "kind": "proof"})
    except Exception:
        return []
    result = []
    for h in out.get("hits", []):
        try:
            doc = json.loads(h["body"])
            result.append({**doc, "score": h["score"]})
        except Exception:
            continue
    return result


def _store(mesh: Mesh, claim: str, result: dict) -> None:
    body = json.dumps({
        "claim": claim,
        "verdict": result["verdict"],
        "smt": (result.get("smt") or "")[:8000],
        "witness": result.get("witness"),
        "hash": result.get("hash", ""),
        "attempts": len(result.get("attempts", [])),
        "ts": time.time(),
    }, sort_keys=True)
    try:
        mesh.call("index-hub", "put", {
            "kind": "proof", "ref": _claim_hash(claim), "body": body,
        })
    except Exception:
        pass


def prove(claim: str, use_cache: bool = True, retries: int = 3,
          timeout: int = 30, suggest: bool = False) -> dict:
    from forge.loop import prove as _prove
    mesh = Mesh("forge-proofcache")
    try:
        if use_cache:
            hit = _exact(mesh, claim)
            if hit:
                return {
                    "verdict": hit["verdict"],
                    "smt": hit.get("smt", ""),
                    "hash": hit.get("hash", ""),
                    "witness": hit.get("witness"),
                    "attempts": [],
                    "cached": True,
                    "age_s": round(time.time() - hit.get("ts", time.time()), 1),
                }
        result = _prove(claim, retries=retries, timeout=timeout)
        _store(mesh, claim, result)
        result["cached"] = False
        if suggest:
            result["suggestions"] = [
                s for s in _similar(mesh, claim)
                if _canonical(s.get("claim", "")) != _canonical(claim)
            ][:3]
        return result
    finally:
        mesh.close()


def history(limit: int = 20) -> list[dict]:
    mesh = Mesh("forge-proofcache")
    try:
        out = mesh.call("index-hub", "list", {"kind": "proof", "limit": limit})
    except Exception:
        return []
    finally:
        mesh.close()
    rows = []
    for d in out.get("docs", []):
        try:
            rows.append(json.loads(d["body"]))
        except Exception:
            continue
    return rows


def stats() -> dict:
    mesh = Mesh("forge-proofcache")
    try:
        s = mesh.call("index-hub", "stats", {})
        s["proofs"] = s.get("by_kind", {}).get("proof", 0)
        return s
    finally:
        mesh.close()
