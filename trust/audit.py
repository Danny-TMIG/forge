"""Append-only Merkle-anchored audit log with O(log n) inclusion proofs."""
from __future__ import annotations
import base64, fcntl, json, os, sqlite3, time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from . import merkle
from .canonical import canonical
from .keys import layer_ensure

FORGE_HOME = Path(os.environ.get("FORGE_HOME", Path.home() / ".forge"))
AUDIT_DIR  = FORGE_HOME / "trust" / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH       = AUDIT_DIR / "audit.db"
FRONTIER_PATH = AUDIT_DIR / "frontier.json"
LOCK_PATH     = AUDIT_DIR / ".lock"

_lock = Lock()
_conn: sqlite3.Connection | None = None
_frontier = merkle.Frontier()

@contextmanager
def _file_lock():
    f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX); yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN); f.close()

def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB_PATH), isolation_level=None,
                                check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=FULL")
        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS leaves (seq INTEGER PRIMARY KEY, blob BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS nodes (level INTEGER, idx INTEGER, hash BLOB, PRIMARY KEY (level, idx));
            CREATE TABLE IF NOT EXISTS roots (seq INTEGER PRIMARY KEY, root BLOB, sig BLOB, ts REAL, layer TEXT);
        """)
        _load_frontier()
    return _conn

def _save_frontier(): FRONTIER_PATH.write_text(json.dumps(_frontier.snapshot()))

def _load_frontier():
    global _frontier
    if FRONTIER_PATH.exists():
        try:
            snap = json.loads(FRONTIER_PATH.read_text())
            cand = merkle.Frontier.from_snapshot(snap)
            have = _conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0]
            if cand.count <= have: _frontier = cand
        except Exception: pass
    _sync_frontier()

def _sync_frontier():
    global _frontier
    have = _conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0]
    if _frontier.count == have: return
    fr = merkle.Frontier()
    for (blob,) in _conn.execute("SELECT blob FROM leaves ORDER BY seq"):
        fr.append(merkle.leaf_hash(blob))
    _frontier = fr; _save_frontier()

def _lookup(level, idx):
    row = _db().execute("SELECT hash FROM nodes WHERE level=? AND idx=?", (level, idx)).fetchone()
    if row is None: raise KeyError(f"missing node ({level},{idx})")
    return row[0]

def _leaf_bytes(seq):
    row = _db().execute("SELECT blob FROM leaves WHERE seq=?", (seq,)).fetchone()
    if row is None: raise KeyError(f"missing leaf {seq}")
    return row[0]

def append(layer, event, data):
    with _lock, _file_lock():
        db = _db()
        seq = db.execute("SELECT COALESCE(MAX(seq)+1, 0) FROM leaves").fetchone()[0]
        entry = {"v": 1, "seq": seq, "ts": time.time(), "layer": layer, "event": event, "data": data}
        blob = canonical(entry); lh = merkle.leaf_hash(blob)
        db.execute("INSERT INTO leaves (seq, blob) VALUES (?,?)", (seq, blob))
        new_nodes = _frontier.append(lh)
        db.executemany("INSERT OR REPLACE INTO nodes (level, idx, hash) VALUES (?,?,?)", new_nodes)
        root = _frontier.root(); sig = layer_ensure(layer).priv.sign(root)
        db.execute("INSERT INTO roots (seq, root, sig, ts, layer) VALUES (?,?,?,?,?)",
                   (seq, root, sig, entry["ts"], layer))
        _save_frontier()
        entry.update({
            "leaf_hash": base64.b64encode(lh).decode(),
            "root": base64.b64encode(root).decode(),
            "root_sig": base64.b64encode(sig).decode(),
            "tree_size": _frontier.count,
        })
        return entry

def prove(seq, tree_size=None):
    db = _db()
    if tree_size is None:
        tree_size = db.execute("SELECT COUNT(*) FROM leaves").fetchone()[0]
    if not (0 <= seq < tree_size): raise ValueError(f"leaf {seq} not in [0,{tree_size})")
    proof = merkle.inclusion_proof(seq, tree_size, _lookup)
    row = db.execute("SELECT root, sig, layer, ts FROM roots WHERE seq=?", (tree_size - 1,)).fetchone()
    if row is None: raise ValueError(f"no root checkpoint at tree_size={tree_size}")
    root, sig, layer, ts = row
    return {
        "leaf_seq": seq,
        "leaf_blob": base64.b64encode(_leaf_bytes(seq)).decode(),
        "tree_size": tree_size,
        "root": base64.b64encode(root).decode(),
        "root_sig": base64.b64encode(sig).decode(),
        "root_layer": layer, "root_ts": ts,
        "proof": [[d, base64.b64encode(h).decode()] for d, h in proof],
    }

def verify_proof(p, trusted_root_pub=None):
    try:
        blob = base64.b64decode(p["leaf_blob"])
        root = base64.b64decode(p["root"])
        proof = [(d, base64.b64decode(h)) for d, h in p["proof"]]
    except (KeyError, ValueError) as e:
        return False, f"malformed proof: {e}"
    if not merkle.verify_inclusion(blob, root, proof):
        return False, "merkle path does not reconstruct root"
    if trusted_root_pub is not None:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            Ed25519PublicKey.from_public_bytes(trusted_root_pub).verify(
                base64.b64decode(p["root_sig"]), root)
        except InvalidSignature:
            return False, "root signature invalid against trusted key"
    return True, ""

def walk(verify=True):
    db = _db()
    rows = list(db.execute("SELECT seq, blob FROM leaves ORDER BY seq"))
    out = []; fr = merkle.Frontier()
    for seq, blob in rows:
        entry = json.loads(blob); fr.append(merkle.leaf_hash(blob))
        if verify:
            root_row = db.execute("SELECT root FROM roots WHERE seq=?", (seq,)).fetchone()
            if root_row is None: return False, out, f"missing root checkpoint at seq {seq}"
            if root_row[0] != fr.root(): return False, out, f"root mismatch at seq {seq}"
        entry["tree_size"] = seq + 1; out.append(entry)
    return True, out, ""

def proof_stats():
    db = _db()
    return {
        "leaves": db.execute("SELECT COUNT(*) FROM leaves").fetchone()[0],
        "roots":  db.execute("SELECT COUNT(*) FROM roots").fetchone()[0],
        "size":   DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "db":     str(DB_PATH),
    }
