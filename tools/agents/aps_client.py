#!/usr/bin/env python3
"""Minimal stdlib JSON-RPC client for the traecli app-server (websocket or stdio lab).

Usage: aps.py URL SCRIPT.json   where SCRIPT is a list of steps:
  {"call": method, "params": {...}, "save": "name"}   request; stores result under name
  {"wait": notification-method, "timeout": s, "match": {"k": "v"}}
  {"sleep": s}
Values "$name.path.to.field" in params are substituted from saved results.
Every frame received is appended to FRAMES (env APS_LOG) as JSON lines.
"""
import base64, json, os, socket, struct, sys, time
from urllib.parse import urlparse


class WS:
    def __init__(self, url, token=None):
        u = urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        hdr = [f"GET {u.path or '/'} HTTP/1.1", f"Host: {u.hostname}:{u.port}", "Upgrade: websocket",
               "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if token:
            hdr.append(f"Authorization: Bearer {token}")
        self.s.sendall(("\r\n".join(hdr) + "\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(4096)
        head, self.buf = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise SystemExit(f"handshake failed: {head[:200]!r}")

    def send(self, obj):
        data = json.dumps(obj).encode()
        n = len(data)
        h = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                             bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else
                             bytes([0x80 | 127]) + struct.pack(">Q", n))
        mask = os.urandom(4)
        self.s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout):
        self.s.settimeout(timeout)
        msg = b""
        while True:
            b0, b1 = self._need(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._need(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._need(8))[0]
            payload = self._need(n)
            op = b0 & 0x0F
            if op == 0x9:  # ping -> pong
                self.s.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if op == 0x8:
                raise EOFError
            msg += payload
            if b0 & 0x80:
                return json.loads(msg)


def lookup(saved, expr):
    name, *path = expr[1:].split(".")
    v = saved[name]
    for p in path:
        v = v[int(p)] if isinstance(v, list) else v[p]
    return v


def subst(obj, saved):
    if isinstance(obj, str) and obj.startswith("$"):
        return lookup(saved, obj)
    if isinstance(obj, dict):
        return {k: subst(v, saved) for k, v in obj.items()}
    if isinstance(obj, list):
        return [subst(v, saved) for v in obj]
    return obj


def main():
    url, script = sys.argv[1], json.load(open(sys.argv[2]))
    log = open(os.environ.get("APS_LOG", "frames.jsonl"), "a")
    ws = WS(url, os.environ.get("APS_TOKEN"))
    saved, pending, nid = {}, [], 0

    def frame(timeout):
        m = ws.recv(timeout)
        log.write(json.dumps({"t": time.time(), "in": m}) + "\n"); log.flush()
        if "id" in m and "method" in m:  # server->client request: decline politely
            ws.send({"id": m["id"], "result": {"decision": "decline"}})
        return m

    for step in script:
        if "call" in step:
            nid += 1
            req = {"id": nid, "method": step["call"], "params": subst(step.get("params", {}), saved)}
            ws.send(req)
            log.write(json.dumps({"t": time.time(), "out": req}) + "\n")
            while True:
                m = frame(step.get("timeout", 30))
                if m.get("id") == nid and "method" not in m:
                    break
                pending.append(m)
            ok = "error" not in m
            print(f"CALL {step['call']} {'OK' if ok else 'ERR ' + json.dumps(m['error'])[:300]}", flush=True)
            if ok and "save" in step:
                saved[step["save"]] = m["result"]
            if ok and step.get("show"):
                print(json.dumps(m["result"])[:step["show"]], flush=True)
        elif "wait" in step:
            deadline = time.time() + step.get("timeout", 60)
            got = None
            for m in pending:
                if m.get("method") == step["wait"]:
                    got = m; break
            if got:
                pending.remove(got)
            while not got and time.time() < deadline:
                try:
                    m = frame(max(0.1, deadline - time.time()))
                except socket.timeout:
                    break
                if m.get("method") == step["wait"]:
                    got = m
            print(f"WAIT {step['wait']} {'GOT' if got else 'TIMEOUT'}", flush=True)
            if got and "save" in step:
                saved[step["save"]] = got.get("params")
        elif "sleep" in step:
            end = time.time() + step["sleep"]
            while time.time() < end:
                try:
                    frame(end - time.time())
                except socket.timeout:
                    break


if __name__ == "__main__":
    main()
