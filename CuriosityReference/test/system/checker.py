#!/usr/bin/env python3
"""Builder's checker: reads <run>/record/*.jsonl and writes verdicts.json (fprime-test-oracle §3).
usage: checker.py <record-dir> [verdicts.json] [--run <run-id>]
Predicate keys are the plan's text verbatim (plan.md §3 STD / ABS / pass rules); `failed` lists them unchanged."""
import hashlib, json, os, re, sys

MARKS = {"L0-1": (10, 11), "L0-2": (20, 21), "L2-1": (30, 31), "L2-2": (40, 41), "L2-3": (50, 51), "L2-5": (60, 61),
         "L3-1": (70, 73), "L5-1": (80, 81), "L5-2": (90, 91), "L5-3": (100, 101), "L6-1": (110, 111), "L7-1": (120, 121)}
WORD = re.compile(r"[-\w]+")
SWEEP_METRIC = "t(SweepDone VERIFY) \u2212 t(SWEEP sent) \u2264 1200 s"
DUMP_RULE = ("for every k: event framTester.DumpData(offset=0x3FF00+8k, length=8, data0, data1) bytes == xorshift32 pattern"
             "(seed 0xF1F1, offset 0x3FF00+8k\u2026+7)")

def load(record, name):
    with open(os.path.join(record, name)) as fh:
        return [json.loads(l) for l in fh if l.strip()]

def toks(e):
    out = []
    for w in WORD.findall(e["args"]["text"]):
        try:
            out.append(int(w, 0) if w.lower().startswith("0x") else int(w))
        except ValueError:
            out.append(w)
    return out

def fields(e):
    """Named args per the FramTester / FramDriver / CmdDispatcher event formats."""
    n = e["name"].split(".")[-1]; t = toks(e)
    try:
        if n == "Marker": return {"id": t[2]}
        if n == "RequestReturned": return {"op": t[1], "client": t[3], "offset": t[5], "length": t[7], "status": t[9]}
        if n == "WriteDone": return {"client": t[4], "offset": t[6], "length": t[8], "status": t[9]}
        if n == "VerifyDone": return {"client": t[4], "offset": t[6], "length": t[8], "status": t[9], "mismatches": t[11]}
        if n == "RawDone": return {"op": t[2], "client": t[5], "offset": t[7], "length": t[9], "status": t[10]}
        if n == "SweepDone": return {"kind": t[2], "chunks": t[4], "errors": t[6]}
        if n == "DumpData":
            m = re.search(r"offset (\d+) length (\d+): ([0-9a-fA-F]+) ([0-9a-fA-F]+)$", e["args"]["text"])
            return {"offset": int(m.group(1)), "length": int(m.group(2)), "data0": int(m.group(3), 16), "data1": int(m.group(4), 16)} if m else {}
        if n == "RequestRejected": return {"op": t[1], "client": t[4], "offset": t[7], "length": t[9], "status": t[11], "cause": t[12]}
        if n in ("OpCodeDispatched", "OpCodeCompleted", "OpCodeError"): return {"opcode": t[1]}
    except (IndexError, TypeError):
        pass
    return {}

def xorshift(seed, offset, i):
    v = (seed ^ (offset + i)) & 0xFFFFFFFF
    v ^= (v << 13) & 0xFFFFFFFF; v ^= v >> 17; v ^= (v << 5) & 0xFFFFFFFF
    return v & 0xFF

def is_marker(e, mid):
    return e["name"].endswith("framTester.Marker") and fields(e).get("id") == mid

class Window:
    """Plan §3: rows from the last Marker(START) that precedes the first Marker(END) following it, through that
    Marker(END) row plus the END MARK's own cmdDisp rows (at most two). Channels by ordering time t; commands by
    sent time between the matching MARK sendings."""
    def __init__(self, events, channels, commands, start_id, end_id):
        self.ok = False; self.events = []; self.channels = []; self.commands = []
        self.lines = None; self.ch_lines = None; self.cmd_lines = None
        si = [i for i, e in enumerate(events) if is_marker(e, start_id)]
        if not si:
            return
        ei = [i for i, e in enumerate(events) if is_marker(e, end_id) and i > si[0]]
        if not ei:
            return
        b = ei[0]; a = max(i for i in si if i < b)
        n = 0
        while n < 2 and b < len(events) - 1 and "cmdDisp." in events[b + 1]["name"]:
            b += 1; n += 1
        self.ok = True
        self.events = events[a:b + 1]; self.lines = (a + 1, b + 1)
        t0, t1 = events[a]["t"], events[b]["t"]
        ci = [i for i, c in enumerate(channels) if t0 <= c["t"] <= t1]
        self.channels = [channels[i] for i in ci]
        self.ch_lines = (ci[0] + 1, ci[-1] + 1) if ci else None
        ms = [(i, c) for i, c in enumerate(commands) if c["name"].endswith("framTester.MARK") and c["args"]["nums"]]
        s_cmd = [i for i, c in ms if c["args"]["nums"][0] == start_id]
        e_cmd = [i for i, c in ms if c["args"]["nums"][0] == end_id and s_cmd and i > s_cmd[0]]
        if s_cmd and e_cmd:
            ce = e_cmd[0]; cs = max(i for i in s_cmd if i < ce)
            self.commands = commands[cs:ce + 1]; self.cmd_lines = (cs + 1, ce + 1)
    def ev(self, name, **kw):
        out = []
        for e in self.events:
            if not e["name"].endswith(name): continue
            f = fields(e)
            if all(f.get(k) == v for k, v in kw.items()): out.append(e)
        return out
    def count(self, name, **kw):
        return len(self.ev(name, **kw))
    def precedes(self, a, b):
        ea, eb = self.ev(a[0], **a[1]), self.ev(b[0], **b[1])
        # plan §3: `precedes` compares t, archive index on ties
        return bool(ea) and bool(eb) and (ea[0]["t"], self.events.index(ea[0])) < (eb[0]["t"], self.events.index(eb[0]))
    def samples(self, name):
        return [c["value"] for c in self.channels if c["name"].endswith(name)]
    def first(self, name):
        s = self.samples(name); return s[0] if s else None
    def last(self, name):
        s = self.samples(name); return s[-1] if s else None
    def delta(self, name):
        s = self.samples(name)
        return (s[-1] - s[0]) if len(s) >= 2 and all(isinstance(x, int) for x in (s[0], s[-1])) else None
    def evidence(self):
        out = []
        if self.lines: out.append("events.jsonl:%d-%d" % self.lines)
        if self.ch_lines: out.append("channels.jsonl:%d-%d" % self.ch_lines)
        if self.cmd_lines: out.append("commands.jsonl:%d-%d" % self.cmd_lines)
        return out

def std(w):
    c = {"both markers present": w.ok}
    c["commands_sent == count(event cmdDisp.OpCodeDispatched)"] = w.ok and bool(w.commands) and len(w.commands) == w.count("cmdDisp.OpCodeDispatched")
    c["event cmdDisp.OpCodeError count == 0"] = w.ok and w.count("cmdDisp.OpCodeError") == 0
    c["event cmdDisp.TooManyCommands count == 0"] = w.ok and w.count("cmdDisp.TooManyCommands") == 0
    c["delta(framer.DroppedPackets) == 0"] = w.ok and w.delta("framer.DroppedPackets") == 0
    c["fram samples >= 2"] = w.ok and len(w.samples("framDriver.StartupState")) >= 2
    return c

def abs_(w):
    return {"event framTester.UnexpectedCompletion count == 0": w.count("framTester.UnexpectedCompletion") == 0,
            "event framTester.BufferMismatch count == 0": w.count("framTester.BufferMismatch") == 0,
            "event framTester.RawDone count == 0": w.count("framTester.RawDone") == 0,
            "event framTester.CommandRejected count == 0": w.count("framTester.CommandRejected") == 0}

def cnt(w, a, key, name, n, **kw):
    a[key] = w.count(name, **kw) == n

def dlt(w, a, ch, n):
    a["delta(%s) == %d" % (ch, n)] = w.delta(ch) == n

def rules(tid, ev, ch, cm):
    """Returns ({window-label: (complete-dict, all_of-dict)}, [windows], notes)."""
    notes = []
    if tid == "L3-1":
        w1 = Window(ev, ch, cm, 70, 71); r = Window(ev, ch, cm, 71, 72); w2 = Window(ev, ch, cm, 72, 73)
        c1, a1 = std(w1), abs_(w1)
        cnt(w1, a1, "event framTester.WriteDone(client=0, offset=0x020000, length=64, status=OK) count == 1", "framTester.WriteDone", 1, client=0, offset=0x20000, length=64, status="OK")
        cr = {"both markers present": r.ok}
        if r.ok:
            # R spans the reboot: boot 1 = rows in the epoch of Marker(71), boot 2 = later epochs (exporter epochs, C6)
            ep = r.events[0]["epoch"]
            b1 = [x for x in r.channels if x["epoch"] == ep and x["name"].endswith("framer.DroppedPackets")]
            b2 = [x for x in r.channels if x["epoch"] > ep and x["name"].endswith("framer.DroppedPackets")]
            cr["last(framer.DroppedPackets) in R-boot1 == last(framer.DroppedPackets) in W1"] = bool(b1) and b1[-1]["value"] == w1.last("framer.DroppedPackets")
            cr["last(framer.DroppedPackets) in R-boot2 == 0"] = bool(b2) and b2[-1]["value"] == 0
        else:
            cr["last(framer.DroppedPackets) in R-boot1 == last(framer.DroppedPackets) in W1"] = False
            cr["last(framer.DroppedPackets) in R-boot2 == 0"] = False
        ar = {}
        cnt(r, ar, "event framDriver.StartupComplete count == 1", "framDriver.StartupComplete", 1)
        cnt(r, ar, "event framDriver.DeviceIdMismatch count == 0", "framDriver.DeviceIdMismatch", 0)
        cnt(r, ar, "event framDriver.BlockProtectSet count == 0", "framDriver.BlockProtectSet", 0)
        cnt(r, ar, "event framDriver.StartupReadFailed count == 0", "framDriver.StartupReadFailed", 0)
        ar["last(framDriver.StartupState) == COMPLETE"] = r.last("framDriver.StartupState") == "COMPLETE"
        c2, a2 = std(w2), abs_(w2)
        cnt(w2, a2, "event framTester.VerifyDone(client=0, offset=0x020000, length=64, status=OK, mismatches=0) count == 1", "framTester.VerifyDone", 1, client=0, offset=0x20000, length=64, status="OK", mismatches=0)
        a2["first(framDriver.WritesCompleted) == 0"] = w2.first("framDriver.WritesCompleted") == 0
        k1, k2 = w1.last("rg8Hz.CycleCount"), w2.last("rg8Hz.CycleCount")
        a2["last(rg8Hz.CycleCount) in W2 < last(rg8Hz.CycleCount) in W1"] = isinstance(k1, int) and isinstance(k2, int) and k2 < k1
        return {"W1": (c1, a1), "R": (cr, ar), "W2": (c2, a2)}, [w1, r, w2], notes
    s, e = MARKS[tid]
    w = Window(ev, ch, cm, s, e)
    c = std(w); a = abs_(w)
    if tid == "L0-1":
        a["last(framDriver.StartupState) == COMPLETE"] = w.last("framDriver.StartupState") == "COMPLETE"
        a["last(framDriver.DeviceIdMatch) == true"] = str(w.last("framDriver.DeviceIdMatch")).lower() == "true"
        sr = w.last("framDriver.StatusRegister")
        a["last(framDriver.StatusRegister) & 0x0C == 0"] = isinstance(sr, int) and (sr & 0x0C) == 0
        a["last(framDriver.StatusRegister) & 0xBD == 0x00"] = isinstance(sr, int) and (sr & 0xBD) == 0
    elif tid == "L0-2":
        c["commands_sent == 3"] = len(w.commands) == 3
        a = {}
        cnt(w, a, "event framTester.Marker(id=0xA5A5) count == 1", "framTester.Marker", 1, id=0xA5A5)
        a["event framTester.Marker(id=20) precedes event framTester.Marker(id=0xA5A5)"] = w.precedes(("framTester.Marker", {"id": 20}), ("framTester.Marker", {"id": 0xA5A5}))
        a["event framTester.Marker(id=0xA5A5) precedes event framTester.Marker(id=21)"] = w.precedes(("framTester.Marker", {"id": 0xA5A5}), ("framTester.Marker", {"id": 21}))
    elif tid == "L2-1":
        cnt(w, a, "event framTester.RequestReturned(op=WRITE, client=0, offset=0, length=64, status=ACCEPTED) count == 1", "framTester.RequestReturned", 1, op="WRITE", client=0, offset=0, length=64, status="ACCEPTED")
        a["event framTester.RequestReturned(op=WRITE, client=0, offset=0, length=64, status=ACCEPTED) precedes event framTester.WriteDone(client=0, offset=0, length=64, status=OK)"] = \
            w.precedes(("framTester.RequestReturned", dict(op="WRITE", client=0, offset=0, length=64, status="ACCEPTED")), ("framTester.WriteDone", dict(client=0, offset=0, length=64, status="OK")))
        cnt(w, a, "event framTester.WriteDone(client=0, offset=0, length=64, status=OK) count == 1", "framTester.WriteDone", 1, client=0, offset=0, length=64, status="OK")
        cnt(w, a, "event framTester.VerifyDone(client=0, offset=0, length=64, status=OK, mismatches=0) count == 1", "framTester.VerifyDone", 1, client=0, offset=0, length=64, status="OK", mismatches=0)
        a["event framTester.WriteDone(client=0, offset=0, length=64) precedes event framTester.VerifyDone(client=0, offset=0, length=64)"] = \
            w.precedes(("framTester.WriteDone", dict(client=0, offset=0, length=64)), ("framTester.VerifyDone", dict(client=0, offset=0, length=64)))
        dlt(w, a, "framDriver.WritesCompleted", 1); dlt(w, a, "framDriver.ReadsCompleted", 1); dlt(w, a, "framDriver.RequestsRejected", 0); dlt(w, a, "framDriver.SpiErrors", 0)
        cnt(w, a, "event framDriver.RequestRejected count == 0", "framDriver.RequestRejected", 0)
        cnt(w, a, "event framDriver.SpiError count == 0", "framDriver.SpiError", 0)
    elif tid == "L2-2":
        cnt(w, a, "event framTester.WriteDone(client=0, offset=0x3FFC0, length=64, status=OK) count == 1", "framTester.WriteDone", 1, client=0, offset=0x3FFC0, length=64, status="OK")
        cnt(w, a, "event framTester.VerifyDone(client=0, offset=0x3FFC0, length=64, status=OK, mismatches=0) count == 1", "framTester.VerifyDone", 1, client=0, offset=0x3FFC0, length=64, status="OK", mismatches=0)
        cnt(w, a, "event framTester.RequestReturned(op=READ, client=0, offset=0x3FFC1, length=64, status=OUT_OF_RANGE) count == 1", "framTester.RequestReturned", 1, op="READ", client=0, offset=0x3FFC1, length=64, status="OUT_OF_RANGE")
        cnt(w, a, "event framDriver.RequestRejected(op=READ, client=0, offset=0x3FFC1, length=64, status=OUT_OF_RANGE) count == 1", "framDriver.RequestRejected", 1, op="READ", client=0, offset=0x3FFC1, length=64, status="OUT_OF_RANGE")
        dlt(w, a, "framDriver.ReadsCompleted", 1); dlt(w, a, "framDriver.WritesCompleted", 1); dlt(w, a, "framDriver.RequestsRejected", 1)
        cnt(w, a, "event framTester.VerifyDone count == 1", "framTester.VerifyDone", 1)
    elif tid == "L2-3":
        cnt(w, a, "event framTester.WriteDone(client=1, offset=0x010000, length=32, status=OK) count == 1", "framTester.WriteDone", 1, client=1, offset=0x10000, length=32, status="OK")
        cnt(w, a, "event framTester.VerifyDone(client=1, offset=0x010000, length=32, status=OK, mismatches=0) count == 1", "framTester.VerifyDone", 1, client=1, offset=0x10000, length=32, status="OK", mismatches=0)
        cnt(w, a, "event framTester.VerifyDone(client=0, offset=0x010000, length=32, status=OK, mismatches=0) count == 1", "framTester.VerifyDone", 1, client=0, offset=0x10000, length=32, status="OK", mismatches=0)
        cnt(w, a, "event framTester.WriteDone(client=0) count == 0", "framTester.WriteDone", 0, client=0)
        dlt(w, a, "framDriver.ReadsCompleted", 2); dlt(w, a, "framDriver.WritesCompleted", 1)
    elif tid == "L2-5":
        cnt(w, a, "event framTester.SweepDone(kind=WRITE, chunks=4096, errors=0) count == 1", "framTester.SweepDone", 1, kind="WRITE", chunks=4096, errors=0)
        cnt(w, a, "event framTester.SweepDone(kind=VERIFY, chunks=4096, errors=0) count == 1", "framTester.SweepDone", 1, kind="VERIFY", chunks=4096, errors=0)
        a["event framTester.SweepDone(kind=WRITE) precedes event framTester.SweepDone(kind=VERIFY)"] = w.precedes(("framTester.SweepDone", {"kind": "WRITE"}), ("framTester.SweepDone", {"kind": "VERIFY"}))
        dlt(w, a, "framDriver.WritesCompleted", 4096); dlt(w, a, "framDriver.ReadsCompleted", 4096); dlt(w, a, "framDriver.SpiErrors", 0); dlt(w, a, "framDriver.RequestsRejected", 0)
        dlt(w, a, "framTester.BytesVerified", 262144)
        # metric: both times on the FSW clock — SWEEP's own cmdDisp.OpCodeDispatched row stands for "SWEEP sent"
        sw = [x for x in w.commands if x["name"].endswith("framTester.SWEEP")]
        disp = [x for x in w.ev("cmdDisp.OpCodeDispatched") if sw and fields(x).get("opcode") == sw[0]["opcode"]]
        done = w.ev("framTester.SweepDone", kind="VERIFY")
        if disp and done:
            dt = done[0]["t"] - disp[0]["t"]; a[SWEEP_METRIC] = 0 <= dt <= 1200.0; notes.append("L2-5 elapsed=%.1f s" % dt)
        else:
            a[SWEEP_METRIC] = False
    elif tid == "L5-1":
        cnt(w, a, "event framTester.RequestReturned(op=READ, client=1, offset=0x030000, length=64, status=BUSY) count == 1", "framTester.RequestReturned", 1, op="READ", client=1, offset=0x30000, length=64, status="BUSY")
        cnt(w, a, "event framDriver.RequestRejected(op=READ, client=1, offset=0x030000, length=64, status=BUSY) count == 1", "framDriver.RequestRejected", 1, op="READ", client=1, offset=0x30000, length=64, status="BUSY")
        cnt(w, a, "event framTester.VerifyDone(client=1, offset=0x030000, length=64, status=OK) count == 1", "framTester.VerifyDone", 1, client=1, offset=0x30000, length=64, status="OK")
        dlt(w, a, "framDriver.ReadsCompleted", 1); dlt(w, a, "framDriver.RequestsRejected", 1); dlt(w, a, "framDriver.WritesCompleted", 0)
    elif tid == "L5-2":
        cnt(w, a, "event framTester.RequestReturned(op=READ, client=0, offset=0x100, length=0, status=OUT_OF_RANGE) count == 1", "framTester.RequestReturned", 1, op="READ", client=0, offset=0x100, length=0, status="OUT_OF_RANGE")
        cnt(w, a, "event framTester.RequestReturned(op=WRITE, client=0, offset=0x100, length=65, status=OUT_OF_RANGE) count == 1", "framTester.RequestReturned", 1, op="WRITE", client=0, offset=0x100, length=65, status="OUT_OF_RANGE")
        cnt(w, a, "event framDriver.RequestRejected(status=OUT_OF_RANGE) count == 2", "framDriver.RequestRejected", 2, status="OUT_OF_RANGE")
        dlt(w, a, "framDriver.RequestsRejected", 2); dlt(w, a, "framDriver.ReadsCompleted", 0); dlt(w, a, "framDriver.WritesCompleted", 0)
        cnt(w, a, "event framTester.WriteDone count == 0", "framTester.WriteDone", 0)
        cnt(w, a, "event framTester.VerifyDone count == 0", "framTester.VerifyDone", 0)
    elif tid == "L5-3":
        cnt(w, a, "event framTester.WriteDone(client=0, offset=0x034000, length=64, status=OK) count == 1", "framTester.WriteDone", 1, client=0, offset=0x34000, length=64, status="OK")
        cnt(w, a, "event framTester.VerifyDone(client=0, offset=0x034000, length=64, status=OK, mismatches=0) count == 1", "framTester.VerifyDone", 1, client=0, offset=0x34000, length=64, status="OK", mismatches=0)
        cnt(w, a, "event framDriver.RequestRejected count == 0", "framDriver.RequestRejected", 0)
        dlt(w, a, "framDriver.RequestsRejected", 0); dlt(w, a, "framDriver.WritesCompleted", 1); dlt(w, a, "framDriver.ReadsCompleted", 1)
    elif tid == "L6-1":
        cnt(w, a, "event framTester.VerifyDone(client=0, offset=0x035000, length=64, status=OK, mismatches=64) count == 1", "framTester.VerifyDone", 1, client=0, offset=0x35000, length=64, status="OK", mismatches=64)
        dlt(w, a, "framDriver.ReadsCompleted", 1)
    elif tid == "L7-1":
        cnt(w, a, "event framTester.WriteDone(status=OK) count == 4", "framTester.WriteDone", 4, status="OK")
        cnt(w, a, "event framTester.DumpData count == 32", "framTester.DumpData", 32)
        ok = True
        for k in range(32):
            off = 0x3FF00 + 8 * k
            d = w.ev("framTester.DumpData", offset=off, length=8)
            if len(d) != 1: ok = False; continue
            f = fields(d[0]); got = [(f["data0"] >> (8 * (3 - i))) & 0xFF for i in range(4)] + [(f["data1"] >> (8 * (3 - i))) & 0xFF for i in range(4)]
            if got != [xorshift(0xF1F1, off, i) for i in range(8)]: ok = False
        a[DUMP_RULE] = ok
        dlt(w, a, "framDriver.SpiErrors", 0); dlt(w, a, "framDriver.RequestsRejected", 0)
        cnt(w, a, "event framDriver.RequestRejected count == 0", "framDriver.RequestRejected", 0)
    return {"W": (c, a)}, [w], notes

def record_sha(record):
    p = os.path.join(os.path.dirname(os.path.abspath(record.rstrip("/"))), "record.sha256")
    if os.path.exists(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    h = hashlib.sha256()  # dry run: no seal.py hash-record yet; tie the verdicts to the three exports
    for f in ("events.jsonl", "channels.jsonl", "commands.jsonl"):
        h.update(open(os.path.join(record, f), "rb").read())
    return "exports:" + h.hexdigest()

def main(record, out, run):
    ev, ch, cm = load(record, "events.jsonl"), load(record, "channels.jsonl"), load(record, "commands.jsonl")
    tests = {}
    for tid in MARKS:
        parts, wins, notes = rules(tid, ev, ch, cm)
        complete = all(v for c, _ in parts.values() for v in c.values())
        failed = []
        for c, a in parts.values():
            failed += [k for k, x in c.items() if not x]
            failed += [k for k, x in a.items() if not x] if complete else []
        failed = list(dict.fromkeys(failed))
        v = "UNVERIFIED" if not complete else ("PASS" if not failed else "FAIL")
        evidence = list(dict.fromkeys(e for w in wins for e in w.evidence()))
        entry = {"verdict": v, "failed": failed, "evidence": evidence, "notes": notes}
        if len(parts) == 1:
            entry["complete"], entry["all_of"] = parts["W"]
        else:
            entry["complete"] = {lab: c for lab, (c, _) in parts.items()}; entry["all_of"] = {lab: a for lab, (_, a) in parts.items()}
        tests[tid] = entry
    with open(out, "w") as fh:
        json.dump({"run": run, "record_sha256": record_sha(record), "checker": "builder", "tests": tests}, fh, indent=1)
    print(json.dumps({k: v["verdict"] for k, v in tests.items()}))

if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    run = sys.argv[sys.argv.index("--run") + 1] if "--run" in sys.argv else os.path.basename(os.path.dirname(os.path.abspath(args[0].rstrip("/"))))
    main(args[0], args[1] if len(args) > 1 else os.path.join(os.path.dirname(os.path.abspath(args[0].rstrip("/"))), "verdicts.json"), run)
