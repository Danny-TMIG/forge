from __future__ import annotations
import os, pathlib, signal, subprocess, sys, time
JOBS = pathlib.Path.home() / ".forge" / "jobs"; JOBS.mkdir(parents=True, exist_ok=True)

def _d(n): return JOBS / n
def _ws(d, s):
    (d/"status").write_text(s); (d/"status_at").write_text(str(time.time()))
def _r(d, n):
    f = d/n; return f.read_text().strip() if f.exists() else ""

def _tail(p, n):
    if not p.exists(): return ""
    try:
        with open(p,"rb") as f:
            f.seek(0,2); size=f.tell(); f.seek(max(0,size-65536))
            data = f.read().decode("utf-8","replace")
        return "\n".join(data.splitlines()[-n:])
    except Exception as e: return f"<tail failed: {e}>"

def start(name, data, model, adapter=None, iters=200, batch_size=4, lr=1e-5, layers=8, resume=False):
    d = _d(name)
    if d.exists(): return {"ok":False,"error":"job exists","job":name}
    d.mkdir(parents=True)
    adapter = adapter or str(pathlib.Path.home()/".forge"/"adapters"/name)
    pathlib.Path(adapter).mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable,"-m","mlx_lm.lora","--model",model,
           "--data",str(pathlib.Path(data).expanduser()),
           "--adapter-path",adapter,"--train","--iters",str(iters),
           "--batch-size",str(batch_size),"--learning-rate",str(lr),
           "--num-layers",str(layers)]
    if resume:
        cmd += ["--resume-adapter-file", str(pathlib.Path(adapter)/"adapters.npz")]
    (d/"cmd").write_text("\n".join(cmd)); (d/"model").write_text(model)
    (d/"data").write_text(str(pathlib.Path(data).expanduser())); (d/"adapter").write_text(adapter)
    (d/"started").write_text(str(time.time())); _ws(d, "starting")
    with open(d/"stdout","w") as so, open(d/"stderr","w") as se:
        p = subprocess.Popen(cmd, stdout=so, stderr=se, start_new_session=True)
    (d/"pid").write_text(str(p.pid)); _ws(d, "running")
    return {"ok":True,"job":name,"pid":p.pid,"adapter":adapter,"log":str(d/"stderr")}

def status(name):
    d = _d(name)
    if not d.exists(): return {"ok":False,"error":"no such job","job":name}
    pid_s = _r(d,"pid"); alive = False
    if pid_s.isdigit():
        try: os.kill(int(pid_s),0); alive=True
        except OSError: alive=False
    rec = _r(d,"status")
    if rec == "running" and not alive: _ws(d,"exited"); rec = "exited"
    adapter = _r(d,"adapter"); art = {}
    if adapter and pathlib.Path(adapter).exists():
        for f in pathlib.Path(adapter).iterdir():
            if f.is_file(): art[f.name] = f.stat().st_size
    return {"ok":True,"job":name,"status":rec,"alive":alive,
            "pid":int(pid_s) if pid_s.isdigit() else None,
            "started":float(_r(d,"started") or 0),
            "elapsed_s":round(time.time()-float(_r(d,"started") or time.time()),1),
            "model":_r(d,"model"),"adapter":adapter,"artifacts":art,
            "log_tail":_tail(d/"stderr",20)}

def stop(name):
    d = _d(name)
    if not d.exists(): return {"ok":False,"error":"no such job"}
    pid_s = _r(d,"pid")
    if not pid_s.isdigit(): return {"ok":False,"error":"no pid"}
    try:
        os.killpg(os.getpgid(int(pid_s)), signal.SIGTERM); _ws(d,"stopped")
        return {"ok":True,"job":name}
    except OSError as e: return {"ok":False,"error":str(e)}

def list_jobs():
    return {"jobs":[status(d.name) for d in sorted(JOBS.iterdir()) if d.is_dir()]}

def logs(name, tail=200):
    d = _d(name)
    if not d.exists(): return {"ok":False,"error":"no such job"}
    return {"job":name,"stderr_tail":_tail(d/"stderr",tail),"stdout_tail":_tail(d/"stdout",tail)}
