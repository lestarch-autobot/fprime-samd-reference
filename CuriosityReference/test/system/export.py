#!/usr/bin/env python3
"""Export a run's archive (<run>/record/raw/) to <run>/record/{events,channels,commands}.jsonl + export-counts.json
(fprime-test-oracle §2; no verdicts here).
usage: export.py <run-dir>            (legacy: export.py <archive-dir> <record-dir>)
Layout: raw/fprime-cli-*/{event,channel}.log are the recorder's logs (events + channels); raw/cmd/fprime-cli-*/command.log
are the command sender's logs; raw/fprime-gds-*/ (GDS internals) and raw/flash.log are not parsed."""
import glob, json, os, re, sys

TIME = re.compile(r"^TB_PROC_TIME: (?P<fsw>[\d.]+)s, ")
EV = re.compile(r"^TB_PROC_TIME: (?P<fsw>[\d.]+)s, context=\d+,\((?P<gen>.*?)\),(?P<name>[^,]+),(?P<id>\d+),EventSeverity\.(?P<sev>\w+),(?P<text>.*)$")
CH = re.compile(r"^TB_PROC_TIME: (?P<fsw>[\d.]+)s, context=\d+,\((?P<gen>.*?)\),(?P<name>[^,]+),(?P<id>\d+),(?P<value>.*)$")
CM = re.compile(r"^(?P<sent>\S+?),\((?P<gen>.*?)\),(?P<name>[^,]+),(?P<op>\d+),(?P<args>.*)$")
NUM = re.compile(r"-?\d+")

def read_log(path):
    """Lines of one log file with the recorder's duplicate rows removed: within one boot (epoch segment of one file —
    FSW time restarts near 0 on a reboot, detected as a fall-back of > 1 s) two distinct rows can never be byte-identical
    (µs-resolved FSW time), so a set per segment is exact. Rows identical across a reboot are real and kept.
    Returns (kept_lines, raw_line_count)."""
    kept, seen, last, raw = [], set(), None, 0
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            raw += 1
            m = TIME.match(line)
            if m:
                fsw = float(m["fsw"])
                if last is not None and fsw < last - 1.0:
                    seen = set()
                last = fsw
            if line in seen:
                continue
            seen.add(line); kept.append(line)
    return kept, raw

def rows(archive, fname, sub=""):
    out, raw = [], 0
    for f in sorted(glob.glob(os.path.join(archive, sub, "fprime-cli-*", fname))):
        k, r = read_log(f); out += k; raw += r
    return out, raw

def stamp(rows):
    """Archive order is kept; FSW time restarts at 0 on a reboot, so an ordering time t = epoch*1e6 + fsw is added,
    where epoch increments whenever fsw falls back by more than 1 s."""
    epoch = 0; last = None
    for r in rows:
        if last is not None and r["fsw"] < last - 1.0:
            epoch += 1
        last = r["fsw"]
        r["epoch"] = epoch
        r["t"] = epoch * 1e6 + r["fsw"]

def opcode_of(text):
    m = re.search(r"Opcode (0x[0-9a-fA-F]+|\d+)", text)
    return int(m.group(1), 0) if m else None

def main(run=None, archive=None, record=None):
    if run:
        archive = os.path.join(run, "record", "raw"); record = os.path.join(run, "record")
    os.makedirs(record, exist_ok=True)
    ev_raw, ev_n = rows(archive, "event.log")
    events = []
    for l in ev_raw:
        m = EV.match(l)
        if m:
            events.append({"fsw": float(m["fsw"]), "gen": m["gen"], "kind": "event", "severity": m["sev"], "name": m["name"],
                           "id": int(m["id"]), "args": {"text": m["text"], "nums": [int(n) for n in NUM.findall(m["text"])]}})
    stamp(events)
    ch_raw, ch_n = rows(archive, "channel.log"); channels = []
    for l in ch_raw:
        m = CH.match(l)
        if m:
            v = m["value"].strip()
            try:
                v = int(v)
            except ValueError:
                pass
            channels.append({"fsw": float(m["fsw"]), "gen": m["gen"], "kind": "channel", "name": m["name"], "id": int(m["id"]), "value": v})
    stamp(channels)
    cm_raw, cm_n = rows(archive, "command.log", "cmd"); commands = []
    for l in cm_raw:
        m = CM.match(l)
        if m:
            commands.append({"sent": m["sent"], "gen": m["gen"], "kind": "command", "opcode": int(m["op"]), "name": m["name"],
                             "args": {"raw": m["args"], "nums": [int(n) for n in NUM.findall(m["args"])]}, "completion": "NONE"})
    commands.sort(key=lambda r: r["sent"])
    # completion: the dispatcher's response row for this opcode, paired in order of sending (archive order of the
    # cmdDisp.OpCodeCompleted / OpCodeError rows); "NONE" when no unpaired response exists
    resp = [(e["name"].split(".")[-1], opcode_of(e["args"]["text"])) for e in events if e["name"].split(".")[-1] in ("OpCodeCompleted", "OpCodeError")]
    used = set()
    for c in commands:
        for i, (kind, op) in enumerate(resp):
            if i not in used and op == c["opcode"]:
                c["completion"] = kind; used.add(i); break
    counts = {}
    for name, data in (("events.jsonl", events), ("channels.jsonl", channels), ("commands.jsonl", commands)):
        with open(os.path.join(record, name), "w") as fh:
            for r in data:
                fh.write(json.dumps(r) + "\n")
        counts[name] = len(data)
    counts["raw"] = {"event.log lines": ev_n, "channel.log lines": ch_n, "command.log lines": cm_n,
                     "event.log after dedup": len(ev_raw), "channel.log after dedup": len(ch_raw), "command.log after dedup": len(cm_raw)}
    counts["clocks"] = {"fsw": "FSW time (TB_PROC_TIME) of the row; t = epoch*1e6 + fsw orders across reboots",
                        "sent": "ground wall clock of the command sender (not comparable with fsw)"}
    with open(os.path.join(record, "export-counts.json"), "w") as fh:
        json.dump(counts, fh, indent=1)
    print(json.dumps({k: counts[k] for k in ("events.jsonl", "channels.jsonl", "commands.jsonl", "raw")}))

if __name__ == "__main__":
    if len(sys.argv) == 2:
        main(run=sys.argv[1])
    else:
        main(archive=sys.argv[1], record=sys.argv[2])
