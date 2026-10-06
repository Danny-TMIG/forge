"""generate -> verify, one loop. No new hub."""
from __future__ import annotations
import hashlib, re, textwrap
from forge.mesh import Mesh

MAX_SMT_BYTES = 512 * 1024
MAX_RETRIES = 3

_ENCODE = textwrap.dedent("""\
    You encode claims as SMT-LIB 2.0 for the z3 solver.

    Output ONLY the SMT-LIB 2.0 script, nothing else.
    Rules:
      - No markdown fences, no prose, no comments.
      - Numbers are literals: write 10, not a (declare-const ten Int).
      - Declare only the variables named in the claim.
      - To prove the claim, encode its NEGATION.
      - End with exactly one (check-sat).
      - Do NOT include (get-model) or (get-value).

    Example:
      Claim: there is no integer x with x > 10 and x < 5
      Script:
        (declare-const x Int)
        (assert (> x 10))
        (assert (< x 5))
        (check-sat)

    If the claim cannot be encoded in SMT-LIB, output exactly: UNENCODABLE

    Claim:
    <<<
    {claim}
    >>>
""")

_RETRY = textwrap.dedent("""\
    Your previous SMT-LIB script failed to parse.

    z3 error:
    <<<
    {error}
    >>>

    Produce a corrected SMT-LIB 2.0 script. Same rules as before.
    Numbers are literals, declare only named variables, end with (check-sat).
    Output only the script. If not encodable, output exactly: UNENCODABLE
""")


def _strip(s):
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()

def _ask(mesh, msgs, mt=1024):
    o = mesh.call("model-hub","ask",{"messages":msgs,"max_tokens":mt,"temperature":0.0})
    return o["choices"][0]["message"]["content"]

def _check(mesh, smt, t):
    return mesh.call("verify-hub","check_smt",{"smt":smt,"timeout":t})

def _witness(mesh, smt, t):
    if "(check-sat)" not in smt: return None
    smt2 = smt.replace("(check-sat)","(check-sat)\n(get-model)",1)
    r = _check(mesh, smt2, t); raw = r.get("raw","")
    m = re.search(r"\(model\b.*", raw, re.DOTALL)
    return m.group(0)[:2000] if m else None

def prove(claim, retries=MAX_RETRIES, timeout=30):
    retries = max(0, min(int(retries), MAX_RETRIES))
    mesh = Mesh("forge-prove"); attempts = []
    try:
        msgs = [{"role":"user","content":_ENCODE.format(claim=claim)}]
        smt = _strip(_ask(mesh, msgs))
        if smt == "UNENCODABLE": return {"verdict":"unencodable","attempts":attempts}
        for i in range(retries+1):
            if len(smt.encode()) > MAX_SMT_BYTES:
                smt = smt.encode()[:MAX_SMT_BYTES].decode("utf-8","ignore")
            h = hashlib.sha256(smt.encode()).hexdigest()[:16]
            r = _check(mesh, smt, timeout)
            attempts.append({"i":i,"hash":h,"result":r.get("result"),
                             "detail":(r.get("detail") or "")[:400]})
            res = r.get("result")
            if res == "unsat": return {"verdict":"proven","smt":smt,"hash":h,"attempts":attempts}
            if res == "sat":
                return {"verdict":"refuted","smt":smt,"hash":h,
                        "witness":_witness(mesh,smt,timeout),"attempts":attempts}
            if res == "unknown": return {"verdict":"unknown","smt":smt,"hash":h,"attempts":attempts}
            if i == retries:
                return {"verdict":"unencodable","smt":smt,"hash":h,
                        "error":(r.get("detail") or "")[:400],"attempts":attempts}
            err = r.get("detail") or r.get("raw") or "parse error"
            msgs += [{"role":"assistant","content":smt},
                     {"role":"user","content":_RETRY.format(error=err)}]
            smt = _strip(_ask(mesh, msgs))
        return {"verdict":"unencodable","attempts":attempts}
    finally: mesh.close()
