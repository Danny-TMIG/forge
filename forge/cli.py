from __future__ import annotations
import time
import json
import typer
from rich import print as rprint
from rich.table import Table
from forge.mesh import Mesh

app = typer.Typer(no_args_is_help=True, help="forge — talk to the mesh")

def _mesh(): return Mesh("forge-cli")

@app.command()
def hubs():
    """List hubs on the mesh."""
    m = _mesh()
    try:
        t = Table("hub","host","port","props")
        for n,p in sorted(m.discover(2.0).items()):
            t.add_row(n, p.host, str(p.port), json.dumps(p.props))
        rprint(t)
    finally: m.close()

@app.command()
def status():
    """Status of every hub."""
    m = _mesh()
    try:
        for n in m.discover(1.5):
            try: rprint(f"[bold]{n}[/bold]: {json.dumps(m.call(n,'status',{}))}")
            except Exception as e: rprint(f"[bold]{n}[/bold]: [red]{e}[/red]")
    finally: m.close()

@app.command()
def ask(text: str, model: str = "", max_tokens: int = 512):
    """Ask model-hub."""
    m = _mesh()
    try:
        p = {"messages":[{"role":"user","content":text}],"max_tokens":max_tokens}
        if model: p["model"] = model
        out = m.call("model-hub","ask",p)
        rprint(out["choices"][0]["message"]["content"])
    finally: m.close()

@app.command()
def invoke(hub: str, method: str, params: str = typer.Option("{}", "--params", "-p")):
    """Raw JSON-RPC."""
    m = _mesh()
    try: rprint(json.dumps(m.call(hub,method,json.loads(params)),indent=2))
    finally: m.close()

@app.command()
def verify(smt_file: str, timeout: int = 30):
    """Check an SMT-LIB file via verify-hub."""
    from pathlib import Path
    src = Path(smt_file).expanduser().read_text()
    m = _mesh()
    try: rprint(m.call("verify-hub","check_smt",{"smt":src,"timeout":timeout}))
    finally: m.close()

@app.command()
def verify_status():
    m = _mesh()
    try:
        for p in m.peers_of("verify-hub"):
            inst = p.props.get("instance","?")
            try: rprint(f"[bold]{inst}[/bold]  {m.call('verify-hub','status',{})}")
            except Exception as e: rprint(f"[bold]{inst}[/bold]  [red]{e}[/red]")
    finally: m.close()

@app.command()
def prove(claim: str, retries: int = 3, timeout: int = 30,
          no_cache: bool = False, suggest: bool = False):
    """Prove or refute a claim. Uses the proof index unless --no-cache."""
    from forge import proofcache
    out = proofcache.prove(claim, use_cache=not no_cache,
                           retries=retries, timeout=timeout, suggest=suggest)
    v = out["verdict"]
    color = {"proven": "green", "refuted": "red",
             "unknown": "yellow", "unencodable": "yellow"}[v]
    tag = " [dim](cached)[/dim]" if out.get("cached") else ""
    rprint(f"[bold {color}]{v}[/bold {color}]{tag}")
    if out.get("age_s") is not None:
        rprint(f"  [dim]cached {out['age_s']}s ago[/dim]")
    if out.get("hash"):
        rprint(f"  hash: {out['hash']}")
    if out.get("smt"):
        rprint("[dim]--- smt ---[/dim]"); rprint(out["smt"])
    if out.get("witness"):
        rprint("[dim]--- counterexample ---[/dim]"); rprint(out["witness"])
    if out.get("suggestions"):
        rprint("[dim]--- similar proofs ---[/dim]")
        for s_ in out["suggestions"]:
            rprint(f"  {s_['verdict']:10}  {s_['claim'][:80]}")

@app.command()
def proof_history(n: int = 20):
    """Recent proofs, newest first."""
    from forge import proofcache
    for row in proofcache.history(n):
        ts = time.strftime("%Y-%m-%d %H:%M",
                           time.localtime(row.get("ts", 0)))
        rprint(f"[dim]{ts}[/dim]  [bold]{row.get('verdict')}[/bold]  "
               f"{row.get('claim','')[:80]}")

@app.command()
def proof_stats():
    """Proof index stats."""
    from forge import proofcache
    s = proofcache.stats()
    rprint(f"proofs indexed:  {s.get('proofs', 0)}")
    rprint(f"total docs:      {s.get('docs', 0)}")
    rprint(f"by kind:         {s.get('by_kind', {})}")
    rprint(f"embed mode:      {s.get('embed_mode')}")
    rprint(f"size:            {s.get('size_mb')} MB")
    rprint(f"db:              {s.get('db')}")

@app.command()
def train(name: str, data: str,
          model: str = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
          iters: int = 200, batch_size: int = 4, lr: float = 1e-5,
          layers: int = 8, adapter: str = "", resume: bool = False):
    """Start a LoRA training job."""
    from forge import training
    rprint(training.start(name, data, model, adapter=adapter or None,
                          iters=iters, batch_size=batch_size, lr=lr,
                          layers=layers, resume=resume))

@app.command()
def train_status(name: str = ""):
    from forge import training
    if name: rprint(training.status(name))
    else:
        for j in training.list_jobs()["jobs"]:
            rprint(f"[bold]{j['job']}[/bold]  {j['status']}  {j['elapsed_s']}s")

@app.command()
def train_stop(name: str):
    from forge import training; rprint(training.stop(name))

@app.command()
def train_logs(name: str, tail: int = 100):
    from forge import training; rprint(training.logs(name, tail))

@app.command()
def index_put(kind: str, ref: str, body: str):
    m = Mesh("forge-cli")
    try: rprint(m.call("index-hub", "put", {"kind": kind, "ref": ref, "body": body}))
    finally: m.close()

@app.command()
def index_ingest(path: str, kind: str = "file"):
    m = Mesh("forge-cli")
    try: rprint(m.call("index-hub", "ingest_file", {"path": path, "kind": kind}))
    finally: m.close()

@app.command()
def index_search(q: str, k: int = 10, kind: str = ""):
    m = Mesh("forge-cli")
    try:
        p = {"q": q, "k": k}
        if kind: p["kind"] = kind
        for h in m.call("index-hub", "search", p)["hits"]:
            rprint(f"[bold]{h['score']}[/bold]  {h['kind']}  {h['ref']}")
    finally: m.close()

@app.command()
def index_stats():
    m = Mesh("forge-cli")
    try: rprint(m.call("index-hub", "stats", {}))
    finally: m.close()

if __name__ == "__main__": app()
