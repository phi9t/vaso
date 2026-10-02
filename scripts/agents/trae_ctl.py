#!/usr/bin/env python3
"""Lead-side control of the TRAE app-server thread (interim, until vaso-broker).

  trae_ctl.py status            thread status, active turn, goal, context usage
  trae_ctl.py send FILE|-       steer the active turn, or start a turn if idle
  trae_ctl.py new-thread --ticket ID --kickoff FILE [--goal FILE] [--budget N]
      per-ticket rotation: interrupt + clear the goal on the current thread,
      start a fresh thread (same model/sandbox), send the kickoff, set the
      goal with a token budget, and repoint ./thread (re-run attach.sh in
      the human's pane to follow it)
  trae_ctl.py watch [--min-context PCT]
      block until an attention event: goal no longer active, a turn fails,
      context left below PCT (default 20), or the server goes away; print it
      and exit (zero-token wait for the lead)

Reads port/token/thread from this directory. Single writer: only the lead
runs `send`; the human types in the attached TUI.
"""
import json, os, sys, time
from pathlib import Path

# Broker state (port, token, thread, logs) lives in the estate, never in git:
# $TRAE_BROKER_DIR, default $VASO_ESTATE_ROOT/agents/trae/broker.
import os  # noqa: E402

HERE = Path(os.environ.get("TRAE_BROKER_DIR") or
            Path(os.environ.get("VASO_ESTATE_ROOT", "")) / "agents" / "trae" / "broker")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aps_client import WS  # noqa: E402

def _state(name: str) -> str:
    try:
        return (HERE / name).read_text().strip()
    except OSError:
        return ""


PORT = _state("port")
TOKEN = _state("token")
THREAD = _state("thread")
LOG = HERE / "ctl.log"


class Client:
    def __init__(self):
        self.ws = WS(f"ws://127.0.0.1:{PORT}", TOKEN)
        self.nid = 0
        self.backlog = []
        self.call("initialize", {"clientInfo": {"name": "vaso-lead-ctl", "version": "0.1"},
                                 "capabilities": {"experimentalApi": True}})

    def call(self, method, params, timeout=120):
        self.nid += 1
        self.ws.send({"id": self.nid, "method": method, "params": params})
        while True:
            m = self.ws.recv(timeout)
            if "id" in m and "method" in m:  # server->client request
                self.ws.send({"id": m["id"], "result": {"decision": "decline"}})
                continue
            if m.get("id") == self.nid and "method" not in m:
                if "error" in m:
                    raise SystemExit(f"{method}: {json.dumps(m['error'])[:400]}")
                return m["result"]
            self.backlog.append(m)

    def next_event(self, timeout):
        if self.backlog:
            return self.backlog.pop(0)
        return self.ws.recv(timeout)


def log(line):
    with LOG.open("a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")


def snapshot(c):
    r = c.call("thread/read", {"threadId": THREAD, "includeTurns": False})
    th = r.get("thread", r)
    out = {"status": th.get("status")}
    try:
        out["context"] = c.call("thread/contextUsage", {"threadId": THREAD})
    except SystemExit as e:
        out["context"] = str(e)
    try:
        out["goal"] = c.call("thread/goal/get", {"threadId": THREAD})
    except SystemExit as e:
        out["goal"] = str(e)
    return out


def active_turn_id_fast(c):
    """Cheap: status via thread/read (no turns), turn id from the rollout tail.

    thread/resume and includeTurns=True load the whole rollout (90+ s on a
    600 MB thread); the rollout's last task_started/turn event carries the id.
    """
    r = c.call("thread/read", {"threadId": THREAD, "includeTurns": False})
    th = r.get("thread", r)
    if (th.get("status") or {}).get("type") != "active":
        return None, False
    path = th.get("path")
    if not path:
        return None, True
    import re as _re
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 8 * 1024 * 1024))
        tail = fh.read().decode("utf-8", "replace")
    return last_turn_id(tail), True


def last_turn_id(text: str):
    """Last turn id recorded in a rollout tail (None if absent)."""
    import re as _re
    ids = _re.findall(r'"turn_id":"([0-9a-f-]{36})"', text)
    return ids[-1] if ids else None


def active_turn_id(c):
    r = c.call("thread/read", {"threadId": THREAD, "includeTurns": True})
    turns = (r.get("thread", r).get("turns") or [])
    for t in reversed(turns):
        if t.get("status") == "inProgress":
            return t["id"]
    return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    c = Client()
    if cmd not in ("send", "new-thread"):
        c.call("thread/resume", {"threadId": THREAD}, timeout=300)
    if cmd == "status":
        print(json.dumps(snapshot(c), indent=1)[:3000])
    elif cmd == "send":
        src = sys.argv[2]
        text = sys.stdin.read() if src == "-" else Path(src).read_text()
        tid, active = active_turn_id_fast(c)
        mode = None
        if active and tid:
            try:
                c.call("turn/steer", {"threadId": THREAD, "expectedTurnId": tid,
                                      "input": [{"type": "text", "text": text}]})
                mode = f"steer turn={tid} fast"
            except SystemExit as e:  # stale id or thread not loaded: slow path
                log(f"FAST_STEER_FAILED {str(e)[:200]}")
        if mode is None and active:
            c.call("thread/resume", {"threadId": THREAD}, timeout=300)
            tid = active_turn_id(c)
            c.call("turn/steer", {"threadId": THREAD, "expectedTurnId": tid,
                                  "input": [{"type": "text", "text": text}]})
            mode = f"steer turn={tid} slow"
        if mode is None:
            c.call("thread/resume", {"threadId": THREAD}, timeout=300)
            r = c.call("turn/start", {"threadId": THREAD, "input": [{"type": "text", "text": text}]})
            mode = f"start turn={r['turn']['id']}"
        log(f"SEND {mode} bytes={len(text)} src={src}")
        print(f"SENT {mode}")
    elif cmd == "new-thread":
        args = sys.argv[2:]
        def opt(name, default=None):
            return args[args.index(name) + 1] if name in args else default
        ticket, kickoff = opt("--ticket"), opt("--kickoff")
        if not ticket or not kickoff:
            raise SystemExit("new-thread needs --ticket and --kickoff")
        goal = Path(opt("--goal")).read_text() if opt("--goal") else None
        budget = int(opt("--budget", "60000000"))
        old = THREAD
        tid, active = active_turn_id_fast(c)
        try:
            c.call("thread/goal/clear", {"threadId": old})
        except SystemExit as e:
            log(f"GOAL_CLEAR_FAILED {str(e)[:160]}")
        if active and tid:
            try:
                c.call("turn/interrupt", {"threadId": old, "turnId": tid})
            except SystemExit as e:
                log(f"INTERRUPT_FAILED {str(e)[:160]}")
        r = c.call("thread/start", {"cwd": "$REPO_ROOT",
                                    "approvalPolicy": "never", "sandbox": "danger-full-access",
                                    "model": "GPT-5.5", "ephemeral": False,
                                    "config": {"model_reasoning_effort": "xhigh",
                                               "model_reasoning_summary": "detailed"}})
        new = r["thread"]["id"]
        c.call("turn/start", {"threadId": new, "input": [{"type": "text", "text": Path(kickoff).read_text()}]})
        if goal:
            c.call("thread/goal/set", {"threadId": new, "objective": goal, "tokenBudget": budget})
        (HERE / "thread").write_text(new)
        with (HERE / "threads.log").open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ticket={ticket} new={new} old={old} budget={budget}\n")
        log(f"NEW_THREAD ticket={ticket} new={new} old={old}")
        print(f"NEW_THREAD {new} (old {old}); re-run attach.sh in the TRAE pane")
    elif cmd == "watch":
        min_ctx = float(sys.argv[sys.argv.index("--min-context") + 1]) if "--min-context" in sys.argv else 20.0
        try:
            last_goal_status = (c.call("thread/goal/get", {"threadId": THREAD}).get("goal") or {}).get("status")
        except SystemExit:
            last_goal_status = None
        while True:
            try:
                m = c.next_event(600)
            except Exception as e:  # timeout or socket closed
                if type(e).__name__ in ("timeout", "TimeoutError"):
                    continue
                print(f"ATTENTION server-gone {e!r}"); log("ATTENTION server-gone"); return 3
            meth, p = m.get("method", ""), m.get("params", {})
            if meth == "turn/completed":
                st = p.get("turn", {}).get("status")
                tag = "turn-done" if st in ("completed", None) else f"turn-{st}"
                print(f"ATTENTION {tag} {json.dumps(p)[:300]}"); log(f"ATTENTION {tag}"); return 0 if tag == "turn-done" else 2
            elif "goal" in meth:
                gst = (p.get("goal") or p).get("status") if isinstance(p, dict) else None
                if gst != last_goal_status and gst not in ("active", None):
                    print(f"ATTENTION goal-{gst} {json.dumps(p)[:300]}"); log(f"ATTENTION goal-{gst}"); return 2
                last_goal_status = gst
            elif meth == "thread/tokenUsage/updated":
                info = p.get("tokenUsage", p)
                win = info.get("modelContextWindow")
                last = (info.get("last") or {}).get("totalTokens") or (info.get("total") or {}).get("totalTokens")
                if win and last and 100.0 * (1 - last / win) < min_ctx:
                    print(f"ATTENTION context-low left={100*(1-last/win):.0f}%"); log("ATTENTION context-low"); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
