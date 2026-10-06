"""verify-hub — real gate.

Spawned subprocess `z3 -smt2 <file>`. Bounded timeout. Deterministic
parsing. No exceptions for expected failures.

REQ-VER-001: response is {ok: bool, result: sat|unsat|unknown|error, ...}
REQ-VER-002: subprocess timeout <= 300s, default 30s
REQ-VER-003: source limited to MAX_SMT_BYTES = 1 MiB
REQ-VER-004: only 'sat' | 'unsat' | 'unknown' | 'error' returned as result
REQ-VER-005: temp file removed on every path
"""
from __future__ import annotations
import atexit, os, pathlib, shutil, subprocess, tempfile
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from forge.mesh import Mesh

MAX_SMT_BYTES = 1 * 1024 * 1024
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 300


def _find_z3() -> str | None:
    for c in (os.environ.get("Z3_BIN"), "z3",
              "/opt/homebrew/bin/z3", "/usr/local/bin/z3"):
        if c and shutil.which(c):
            return shutil.which(c) or c
    return None


def _classify(stdout: str, stderr: str, rc: int) -> tuple[str, str]:
    first = (stdout or "").strip().splitlines()
    line = first[0].strip() if first else ""
    if line == "sat":
        return "sat", ""
    if line == "unsat":
        return "unsat", ""
    if line == "unknown":
        return "unknown", (stderr or "")[:2000]
    if rc != 0:
        return "error", (stderr or stdout or "")[:2000]
    return "error", f"unrecognized z3 output: {line[:200]!r}"


class VerifyHub:
    def __init__(self, port: int = 8090):
        self.port = port
        self.mesh = Mesh("verify-hub")
        self.z3 = _find_z3()
        self.app = FastAPI(title="forge verify-hub")
        self._wire()
        atexit.register(self.shutdown)

    def _check(self, smt: str, timeout: int) -> dict:
        if not isinstance(smt, str):
            return {"ok": False, "result": "error", "error": "smt must be str"}
        b = smt.encode("utf-8")
        if len(b) > MAX_SMT_BYTES:
            return {"ok": False, "result": "error",
                    "error": f"smt exceeds {MAX_SMT_BYTES} bytes"}
        if not self.z3:
            return {"ok": False, "result": "error", "error": "z3 not found"}
        t = max(1, min(int(timeout), MAX_TIMEOUT))
        fd, path = tempfile.mkstemp(suffix=".smt2")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b)
            try:
                r = subprocess.run(
                    [self.z3, "-smt2", path],
                    capture_output=True, text=True, timeout=t,
                )
            except subprocess.TimeoutExpired:
                return {"ok": False, "result": "error",
                        "error": f"timeout after {t}s"}
            result, detail = _classify(r.stdout, r.stderr, r.returncode)
            out = {"ok": result in ("sat", "unsat"), "result": result,
                   "rc": r.returncode}
            if detail:
                out["detail"] = detail
            if r.stdout:
                out["raw"] = r.stdout[:2000]
            return out
        finally:
            try: os.unlink(path)
            except OSError: pass

    def _wire(self):
        @self.app.get("/health")
        def health():
            return {"ok": bool(self.z3), "z3": self.z3}

        @self.app.post("/rpc")
        async def rpc(req: Request):
            return JSONResponse(self.mesh.dispatch(await req.json()))

        @self.mesh.method("check_smt")
        def check_smt(p):
            if "smt" not in p:
                return {"ok": False, "result": "error",
                        "error": "missing 'smt'"}
            return self._check(p["smt"], p.get("timeout", DEFAULT_TIMEOUT))

        @self.mesh.method("status")
        def status(_):
            return {"z3": self.z3, "ready": bool(self.z3)}

    def run(self):
        instance = os.environ.get("FORGE_VERIFY_INSTANCE") or f"verify-hub-{os.getpid()}"
        self.mesh.register(self.port,
                           props={"kind": "verify", "instance": instance})
        uvicorn.run(self.app, host="127.0.0.1", port=self.port, log_level="warning")

    def shutdown(self):
        self.mesh.close()


def main():
    VerifyHub(int(os.environ.get("FORGE_VERIFY_PORT", "8090"))).run()


if __name__ == "__main__":
    main()
