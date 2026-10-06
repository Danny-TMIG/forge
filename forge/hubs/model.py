"""model-hub — hybrid inference router.

Three backends behind one OpenAI-compatible endpoint:
  MLX      — default for any non-GGUF model name
  llama.cpp — any .gguf, including .Q1_0 1-bit
  bitnet   — CPU-only 1-bit (.i2_s.gguf, name contains bitnet/1bit)
"""
from __future__ import annotations
import atexit, os, shutil, signal, subprocess, sys, time
import httpx, uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from forge.mesh import Mesh

DEFAULT_MLX = os.environ.get("FORGE_MLX_MODEL",
    "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")
DEFAULT_LLAMA = os.environ.get("FORGE_LLAMA_MODEL", "")
BITNET_BIN = os.environ.get("FORGE_BITNET_BIN",
    str(__import__("pathlib").Path.home() / ".forge/bitnet/BitNet/build/bin/llama-server"))
BITNET_MODEL = os.environ.get("FORGE_BITNET_MODEL",
    str(__import__("pathlib").Path.home() / ".forge/bitnet/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf"))


class Backend:
    name = "backend"
    def __init__(self, port): self.port, self.proc, self.model = port, None, None
    def start(self, model):
        if self.proc and self.model == model and self.proc.poll() is None: return
        self.stop()
        self.model = model
        self._spawn(model)
        self._wait()
    def _spawn(self, model): raise NotImplementedError
    def _wait(self, timeout=600.0):
        url = f"http://127.0.0.1:{self.port}/v1/models"
        end = time.time() + timeout
        while time.time() < end:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} exited during startup")
            try:
                if httpx.get(url, timeout=1.0).status_code == 200: return
            except Exception: pass
            time.sleep(0.5)
        raise RuntimeError(f"{self.name} not ready after {timeout}s")
    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try: self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired: self.proc.kill()
        self.proc, self.model = None, None
    @property
    def base(self): return f"http://127.0.0.1:{self.port}"
    @property
    def running(self): return self.proc is not None and self.proc.poll() is None


class MLX(Backend):
    name = "mlx"
    def _spawn(self, model):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mlx_lm.server", "--model", model, "--port", str(self.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


class Llama(Backend):
    name = "llama.cpp"
    def _spawn(self, model):
        b = shutil.which("llama-server")
        if not b: raise RuntimeError("llama-server not on PATH — brew install llama.cpp")
        self.proc = subprocess.Popen(
            [b, "-m", model, "--port", str(self.port), "-c", "8192",
             "-ngl", "999", "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


class BitNet(Backend):
    name = "bitnet-cpu"
    def _spawn(self, model):
        if not os.path.exists(BITNET_BIN):
            raise RuntimeError(f"bitnet binary missing: {BITNET_BIN}")
        if not os.path.exists(model):
            raise RuntimeError(f"bitnet model missing: {model}")
        self.proc = subprocess.Popen(
            [BITNET_BIN, "-m", model, "--port", str(self.port),
             "--host", "127.0.0.1", "--ctx-size", "4096",
             "--threads", str(os.cpu_count() or 4), "--parallel", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


class ModelHub:
    def __init__(self, port=8080, mlx_port=8081, llama_port=8082, bitnet_port=8083):
        self.port = port
        self.mesh = Mesh("model-hub")
        self.mlx = MLX(mlx_port)
        self.llama = Llama(llama_port)
        self.bitnet = BitNet(bitnet_port)
        self.app = FastAPI(title="forge model-hub")
        self._wire()
        atexit.register(self.shutdown)

    def _pick(self, model: str) -> Backend:
        m = (model or "").lower()
        if "bitnet" in m or "1bit" in m or m.endswith(".i2_s.gguf"):
            return self.bitnet
        if m.endswith(".gguf"):
            return self.llama
        return self.mlx

    def _wire(self):
        @self.app.get("/v1/models")
        def _list_models():
            return {"object": "list", "data": [
                {"id": "mlx", "object": "model"},
                {"id": "llama.cpp", "object": "model"},
                {"id": "bitnet-cpu", "object": "model"},
            ]}

        @self.app.get("/v1/hybrid")
        def _hybrid():
            return {
                "backends": {
                    "mlx":    {"kind": "gpu",      "running": self.mlx.running,    "model": self.mlx.model},
                    "llama":  {"kind": "hybrid",   "running": self.llama.running,  "model": self.llama.model},
                    "bitnet": {"kind": "cpu-1bit", "running": self.bitnet.running, "model": self.bitnet.model},
                },
                "routing": {
                    "bitnet": "name contains bitnet/1bit or ends .i2_s.gguf",
                    "llama":  "name ends .gguf",
                    "mlx":    "everything else (GPU)",
                },
            }

        @self.app.post("/v1/chat/completions")
        async def _chat(req: Request):
            body = await req.json()
            m = body.get("model", "")
            be = self._pick(m)
            target = m or (BITNET_MODEL if be is self.bitnet else
                           (DEFAULT_LLAMA if be is self.llama else DEFAULT_MLX))
            if not be.model or be.model != target:
                be.start(target)
            async with httpx.AsyncClient(timeout=None) as c:
                if body.get("stream"):
                    async def gen():
                        async with c.stream("POST", f"{be.base}/v1/chat/completions", json=body) as r:
                            async for ch in r.aiter_bytes():
                                yield ch
                    return StreamingResponse(gen(), media_type="text/event-stream")
                r = await c.post(f"{be.base}/v1/chat/completions", json=body)
                return JSONResponse(r.json(), status_code=r.status_code)

        @self.app.post("/v1/embeddings")
        async def _embeds(req: Request):
            body = await req.json()
            model = body.get("model") or DEFAULT_LLAMA
            if not model:
                return JSONResponse(
                    {"error": "no embedding model configured",
                     "detail": "set FORGE_LLAMA_MODEL to a .gguf embedding model path"},
                    status_code=503)
            try:
                self.llama.start(model)
            except Exception as e:
                return JSONResponse({"error": "embedding backend unavailable",
                                     "detail": str(e)}, status_code=503)
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(f"{self.llama.base}/v1/embeddings", json=body)
                return JSONResponse(r.json(), status_code=r.status_code)

        @self.app.post("/rpc")
        async def _rpc(req: Request):
            return JSONResponse(self.mesh.dispatch(await req.json()))

        @self.mesh.method("ask")
        def _ask(p):
            m = p.get("model") or DEFAULT_MLX
            be = self._pick(m)
            be.start(m)
            r = httpx.post(f"{be.base}/v1/chat/completions",
                json={"model": m, "messages": p["messages"],
                      "max_tokens": p.get("max_tokens", 512),
                      "temperature": p.get("temperature", 0.2)},
                timeout=p.get("timeout", 600))
            r.raise_for_status()
            return r.json()

        @self.mesh.method("status")
        def _status(_):
            return {
                "mlx":    {"running": self.mlx.running,    "model": self.mlx.model},
                "llama":  {"running": self.llama.running,  "model": self.llama.model},
                "bitnet": {"running": self.bitnet.running, "model": self.bitnet.model},
            }

        @self.mesh.method("stop")
        def _stop(_):
            self.mlx.stop(); self.llama.stop(); self.bitnet.stop()
            return {"ok": True}

    def run(self):
        self.mesh.register(self.port, props={"api": "openai"})
        uvicorn.run(self.app, host="127.0.0.1", port=self.port, log_level="warning")

    def shutdown(self):
        self.mlx.stop(); self.llama.stop(); self.bitnet.stop(); self.mesh.close()


def main():
    ModelHub(
        int(os.environ.get("FORGE_HUB_PORT", "8080")),
        int(os.environ.get("FORGE_MLX_PORT", "8081")),
        int(os.environ.get("FORGE_LLAMA_PORT", "8082")),
        int(os.environ.get("FORGE_BITNET_PORT", "8083")),
    ).run()


if __name__ == "__main__":
    main()
