from __future__ import annotations
import socket, time
from dataclasses import dataclass, field
from typing import Any, Callable
import httpx
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf, ServiceListener

SERVICE_TYPE = "_forge._tcp.local."

@dataclass
class Peer:
    name: str; host: str; port: int
    props: dict[str, str] = field(default_factory=dict)
    @property
    def base(self) -> str: return f"http://{self.host}:{self.port}"

class _L(ServiceListener):
    def __init__(self, add, rem): self.add, self.rem = add, rem
    def add_service(self, zc, t, n):
        i = zc.get_service_info(t, n)
        if i:
            host = socket.inet_ntoa(i.addresses[0]) if i.addresses else i.server
            props = {k.decode(): (v.decode() if isinstance(v, bytes) else v)
                     for k, v in (i.properties or {}).items() if v is not None}
            self.add(Peer(i.server.split(".")[0], host, i.port, props))
    def update_service(self, zc, t, n): self.add_service(zc, t, n)
    def remove_service(self, zc, t, n): self.rem(n)

class Mesh:
    def __init__(self, hub_name: str):
        self.hub_name, self.peers = hub_name, {}
        self._zc = Zeroconf(); self._info = None
        self._handlers: dict[str, Callable] = {}
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, _L(self._on_add, self._on_rem))
    def register(self, port: int, props=None):
        info = ServiceInfo(SERVICE_TYPE, f"{self.hub_name}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton("127.0.0.1")], port=port,
            properties={"hub": self.hub_name, **(props or {})},
            server=f"{socket.gethostname()}.local.")
        self._zc.register_service(info); self._info = info
    def method(self, name):
        def d(fn): self._handlers[name] = fn; return fn
        return d
    def dispatch(self, body):
        rid, m, p = body.get("id"), body.get("method"), body.get("params") or {}
        h = self._handlers.get(m)
        if not h:
            return {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"no {m}"}}
        try: return {"jsonrpc":"2.0","id":rid,"result":h(p)}
        except Exception as e:
            return {"jsonrpc":"2.0","id":rid,"error":{"code":-32000,"message":str(e)}}
    def discover(self, timeout=1.5):
        end = time.time()+timeout
        while time.time()<end: time.sleep(0.05)
        return dict(self.peers)
    def call(self, hub, method, params=None, timeout=120.0):
        if hub not in self.peers:
            end = time.time()+2.0
            while time.time()<end and hub not in self.peers: time.sleep(0.05)
        peer = self.peers.get(hub)
        if not peer: raise RuntimeError(f"hub '{hub}' not on mesh: {list(self.peers)}")
        r = httpx.post(f"{peer.base}/rpc",
            json={"jsonrpc":"2.0","id":1,"method":method,"params":params or {}},
            timeout=timeout)
        r.raise_for_status(); d = r.json()
        if "error" in d: raise RuntimeError(d["error"]["message"])
        return d["result"]
    def close(self):
        if self._info:
            try: self._zc.unregister_service(self._info)
            except Exception: pass
        self._zc.close()
    def _on_add(self, p):
        h = p.props.get("hub", p.name)
        if h != self.hub_name: self.peers[h] = p
    def _on_rem(self, n):
        short = n.split(".")[0]
        for k, v in list(self.peers.items()):
            if v.name == short: del self.peers[k]
