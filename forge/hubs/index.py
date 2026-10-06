"""index-hub — SQLite WAL + FTS5 + quadra feature-hash vectors.

Search is hybrid: RRF merge of BM25 (FTS5) and cosine (quadra).
Embeddings probe model-hub once; fall back to local quadra hashing
if the endpoint is unavailable. Mode reported in stats().
"""
from __future__ import annotations

import atexit
import concurrent.futures as cf
import hashlib
import os
import pathlib
import sqlite3
import struct
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from forge.mesh import Mesh


DB = pathlib.Path(os.environ.get(
    "FORGE_INDEX_DB",
    str(pathlib.Path.home() / ".forge" / "index.db"),
))
EMBED_URL   = lambda: os.environ.get("FORGE_EMBED_URL",   "http://127.0.0.1:8080/v1/embeddings")
EMBED_MODEL = lambda: os.environ.get("FORGE_EMBED_MODEL", "nomic-embed-text-latest.gguf")


# ---------------------------------------------------------------- vec utils

def _pack(v):    return struct.pack(f"{len(v)}f", *v)
def _unpack(b):  return list(struct.unpack(f"{len(b)//4}f", b))


def _cos(a, b):
    if not a or not b:
        return 0.0
    d  = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return d / (na * nb + 1e-9)


# ---------------------------------------------------------------- quadra hashing

def _project(keys, dim: int) -> list[float]:
    """Signed feature hashing: string keys onto dim floats, L2-normalized."""
    vec = [0.0] * dim
    for k in keys:
        h = hashlib.blake2b(k.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign
    norm = (sum(x * x for x in vec) ** 0.5) or 1.0
    return [x / norm for x in vec]


def _view_words(texts, dim):
    out = []
    for t in texts:
        toks = t.lower().split() or ["_empty"]
        out.append(_project(toks, dim))
    return out


def _view_char2(texts, dim):
    out = []
    for t in texts:
        s = t.lower()
        if len(s) < 2:
            out.append(_project([s or "_"], dim)); continue
        out.append(_project([s[i:i+2] for i in range(len(s) - 1)], dim))
    return out


def _view_char3(texts, dim):
    out = []
    for t in texts:
        s = t.lower()
        if len(s) < 3:
            out.append(_project([s or "_"], dim)); continue
        out.append(_project([s[i:i+3] for i in range(len(s) - 2)], dim))
    return out


def _view_wordpair(texts, dim):
    out = []
    for t in texts:
        toks = t.lower().split()
        if len(toks) < 2:
            out.append(_project(toks or ["_"], dim)); continue
        pairs = [f"{toks[i]}|{toks[i+1]}" for i in range(len(toks) - 1)]
        out.append(_project(pairs, dim))
    return out


def hash_embed_quadra(texts: list[str], dim: int) -> list[list[float]]:
    """Four parallel projections, concatenated, re-normalized.

    dim is split evenly across the four views (W/B/T/P).
    Threads are real: blake2b releases the GIL.
    """
    per = max(16, dim // 4)
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [
            ex.submit(_view_words,    texts, per),
            ex.submit(_view_char2,    texts, per),
            ex.submit(_view_char3,    texts, per),
            ex.submit(_view_wordpair, texts, per),
        ]
        parts = [f.result() for f in futures]
    out: list[list[float]] = []
    for i in range(len(texts)):
        cat = parts[0][i] + parts[1][i] + parts[2][i] + parts[3][i]
        norm = (sum(x * x for x in cat) ** 0.5) or 1.0
        out.append([x / norm for x in cat])
    return out


def hash_embed(text: str, dim: int) -> list[float]:
    return hash_embed_quadra([text], dim)[0]


# ---------------------------------------------------------------- RRF

def _rrf(rankings, k: int = 60) -> dict[int, float]:
    s: dict[int, float] = {}
    for r in rankings:
        for i, key in enumerate(r):
            s[key] = s.get(key, 0.0) + 1.0 / (k + i + 1)
    return s


# ---------------------------------------------------------------- hub

class IndexHub:
    def __init__(self, port: int = 8095):
        self.port = port
        self.mesh = Mesh("index-hub")
        self.db = DB
        self.con = self._open()
        self._embed_mode: str | None = None
        self._embed_reason: str = "not probed"
        self._embed_dim: int = 1024     # quadra: 4 views x 256
        self.app = FastAPI(title="forge index-hub")
        self._wire()
        atexit.register(self.shutdown)

    def _open(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db, timeout=30, isolation_level=None,
                              check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=30000")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS docs (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                ref TEXT NOT NULL,
                body TEXT NOT NULL,
                vec BLOB,
                created REAL,
                UNIQUE(kind, ref)
            );
            CREATE INDEX IF NOT EXISTS docs_kind ON docs(kind);
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                ref, body, content='docs', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
              INSERT INTO docs_fts(rowid, ref, body) VALUES (new.id, new.ref, new.body);
            END;
            CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
              INSERT INTO docs_fts(docs_fts, rowid, ref, body)
                VALUES('delete', old.id, old.ref, old.body);
            END;
            CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
              INSERT INTO docs_fts(docs_fts, rowid, ref, body)
                VALUES('delete', old.id, old.ref, old.body);
              INSERT INTO docs_fts(rowid, ref, body) VALUES (new.id, new.ref, new.body);
            END;
        """)
        return con

    def _probe_embed(self):
        try:
            r = httpx.post(EMBED_URL(),
                           json={"model": EMBED_MODEL(), "input": ["probe"]},
                           timeout=3)
            if r.status_code == 200:
                self._embed_mode = "remote"
                self._embed_reason = "endpoint reachable"
                return
            self._embed_mode = "hash"
            self._embed_reason = f"endpoint returned {r.status_code}"
        except Exception as e:
            self._embed_mode = "hash"
            self._embed_reason = f"endpoint unreachable: {type(e).__name__}"

    def _embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        if self._embed_mode is None:
            self._probe_embed()
        if self._embed_mode == "remote":
            try:
                r = httpx.post(EMBED_URL(),
                               json={"model": EMBED_MODEL(), "input": list(texts)},
                               timeout=300)
                r.raise_for_status()
                return [d["embedding"] for d in r.json()["data"]]
            except Exception as e:
                self._embed_mode = "hash"
                self._embed_reason = f"remote failed mid-flight: {e}"
        return hash_embed_quadra(list(texts), self._embed_dim)

    def _fts(self, q: str) -> str:
        toks = [t for t in "".join(
            c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
        return " OR ".join(toks) if toks else '""'

    def _wire(self):
        @self.app.get("/health")
        def _():
            return {"ok": True, "db": str(self.db),
                    "embed_mode": self._embed_mode or "unprobed"}

        @self.app.post("/rpc")
        async def rpc(req: Request):
            return JSONResponse(self.mesh.dispatch(await req.json()))

        @self.mesh.method("put")
        def put(p):
            vec = p.get("vec") or self._embed(p["body"][:4000])[0]
            self.con.execute("BEGIN IMMEDIATE")
            try:
                self.con.execute(
                    "INSERT OR REPLACE INTO docs(kind, ref, body, vec, created) "
                    "VALUES (?,?,?,?,?)",
                    (p["kind"], p["ref"], p["body"], _pack(vec), time.time()),
                )
                self.con.execute("COMMIT")
            except Exception:
                self.con.execute("ROLLBACK")
                raise
            return {"ok": True, "kind": p["kind"], "ref": p["ref"],
                    "dim": len(vec)}

        @self.mesh.method("search")
        def search(p):
            q, k = p["q"], p.get("k", 10)
            kind = p.get("kind")
            qv = self._embed(q)[0]
            where = " WHERE kind=?" if kind else ""
            args = (kind,) if kind else ()
            rows = self.con.execute(
                f"SELECT id, vec FROM docs{where}", args).fetchall()
            scored = [(_cos(qv, _unpack(r[1])), r[0]) for r in rows if r[1]]
            scored.sort(reverse=True)
            vec_rank = [i for _, i in scored][:200]
            try:
                fts_rank = [r[0] for r in self.con.execute(
                    "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? LIMIT 200",
                    (self._fts(q),)).fetchall()]
            except sqlite3.OperationalError:
                fts_rank = []
            scores = _rrf([vec_rank, fts_rank])
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
            if not top:
                return {"hits": []}
            ids = [t[0] for t in top]
            ph = ",".join("?" * len(ids))
            meta = {r[0]: r for r in self.con.execute(
                f"SELECT id, kind, ref, body FROM docs WHERE id IN ({ph})",
                ids).fetchall()}
            return {"hits": [
                {"score": round(scores[i], 5),
                 "kind": meta[i][1], "ref": meta[i][2], "body": meta[i][3][:400]}
                for i in ids if i in meta]}

        @self.mesh.method("ingest_file")
        def ingest_file(p):
            path = pathlib.Path(p["path"]).expanduser()
            kind = p.get("kind", "file")
            targets = [path] if path.is_file() else list(path.rglob("*"))
            docs = []
            for f in targets:
                try:
                    if not f.is_file() or f.stat().st_size > 2_000_000:
                        continue
                    body = f.read_text(errors="ignore")
                except Exception:
                    continue
                if body.strip():
                    docs.append({"kind": kind, "ref": str(f), "body": body})
            if not docs:
                return {"n": 0}
            B = 32
            self.con.execute("BEGIN IMMEDIATE")
            try:
                for i in range(0, len(docs), B):
                    chunk = docs[i:i+B]
                    vecs = self._embed([d["body"][:4000] for d in chunk])
                    for d, v in zip(chunk, vecs):
                        self.con.execute(
                            "INSERT OR REPLACE INTO docs(kind, ref, body, vec, created) "
                            "VALUES (?,?,?,?,?)",
                            (d["kind"], d["ref"], d["body"], _pack(v), time.time()))
                self.con.execute("COMMIT")
            except Exception:
                self.con.execute("ROLLBACK")
                raise
            return {"n": len(docs)}

        @self.mesh.method("get")
        def get_doc(p):
            r = self.con.execute(
                "SELECT kind, ref, body, vec, created FROM docs WHERE kind=? AND ref=?",
                (p["kind"], p["ref"]),
            ).fetchone()
            if not r:
                return None
            return {"kind": r[0], "ref": r[1], "body": r[2], "created": r[4]}

        @self.mesh.method("list")
        def list_docs(p):
            kind = p.get("kind")
            limit = min(int(p.get("limit", 50)), 500)
            if kind:
                rows = self.con.execute(
                    "SELECT kind, ref, body, created FROM docs "
                    "WHERE kind=? ORDER BY created DESC LIMIT ?",
                    (kind, limit),
                ).fetchall()
            else:
                rows = self.con.execute(
                    "SELECT kind, ref, body, created FROM docs "
                    "ORDER BY created DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return {"docs": [{"kind": r[0], "ref": r[1], "body": r[2],
                              "created": r[3]} for r in rows]}

        @self.mesh.method("stats")
        def stats(_):
            n = self.con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            bk = dict(self.con.execute(
                "SELECT kind, COUNT(*) FROM docs GROUP BY kind").fetchall())
            return {"docs": n, "by_kind": bk, "db": str(self.db),
                    "size_mb": round(self.db.stat().st_size/1e6, 2) if self.db.exists() else 0,
                    "embed_mode": self._embed_mode or "unprobed",
                    "embed_reason": self._embed_reason,
                    "embed_dim": self._embed_dim}

        @self.mesh.method("reset")
        def reset(_):
            self.con.execute("DELETE FROM docs")
            self.con.execute("VACUUM")
            return {"ok": True}

    def run(self):
        self.mesh.register(self.port, props={"kind": "index"})
        uvicorn.run(self.app, host="127.0.0.1", port=self.port, log_level="warning")

    def shutdown(self):
        try:
            self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.con.close()
        except Exception:
            pass
        self.mesh.close()


def main():
    IndexHub(int(os.environ.get("FORGE_INDEX_PORT", "8095"))).run()


if __name__ == "__main__":
    main()
