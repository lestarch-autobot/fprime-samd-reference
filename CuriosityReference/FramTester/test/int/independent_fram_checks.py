#!/usr/bin/env python3
"""Independent checker for the 12 FRAM plan tests (ADV-A, belt-and-suspenders).

Written from the plan TEXT (``aux/plan.md``) and the replay API (``docs/replay-api.md``,
``fth.replay``) only.  ``fth/engine.py``, ``planted_faults.py``, the predicate
sections of ``plan.json`` and ``tests/test_engine_grammar.py`` were NOT read.
``plan.json`` is opened only for data: marker names, ``event_patterns`` and each test's
window marker ids.

Contract::

    python independent_fram_checks.py <record-dir> <plan.json> <out.json>
    -> {"tests": {ID: {"verdict": "PASS|FAIL|UNVERIFIED", "failed": [plan sentence, ...]}}}

v2 (plan @ 73ca764, 14 tests) with the anchor rulings:
  R3 a false ``complete`` item -> UNVERIFIED; ``failed`` lists ONLY the failed completeness items,
     all_of / absence / metrics are not evaluated (torn and intact-but-incomplete windows alike);
  R2 a ``precedes`` / ``t()`` operand that is a missing UUT event -> the predicate fails (FAIL);
     a missing ``… sent`` command timestamp -> UNVERIFIED;
  R1 an epoch change inside a ``reset: false`` window -> UNVERIFIED; no epoch change inside a
     ``reset: true`` window -> UNVERIFIED (the window ``reset`` flag is plan.json window data).
Otherwise a false ``all_of`` / ``metrics`` item -> FAIL, else PASS.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

from fth.replay import Record, Window, load_record

# plan §3 "Packets (named channel sets for `<packet> samples >= n`): fram = framDriver.StartupState,
# framDriver.DeviceIdMatch, framDriver.StatusRegister, framDriver.ReadsCompleted, framDriver.WritesCompleted,
# framDriver.RequestsRejected, framDriver.SpiErrors, framTester.BytesWritten, framTester.BytesVerified,
# framTester.Mismatches, framTester.RejectionsSeen; Health = rg8Hz.CycleCount".  A sample is one arrival
# (distinct t) of the packet, counted as the smallest per-channel distinct-t count over its channels.
FRAM_PACKET = [
    "framDriver.StartupState", "framDriver.DeviceIdMatch", "framDriver.StatusRegister",
    "framDriver.ReadsCompleted", "framDriver.WritesCompleted", "framDriver.RequestsRejected",
    "framDriver.SpiErrors", "framTester.BytesWritten", "framTester.BytesVerified",
    "framTester.Mismatches", "framTester.RejectionsSeen",
]
UNVERIFIED = "UNVERIFIED"   # sentinel a metric may return (R2: missing `… sent` timestamp)
REC: Record | None = None   # the loaded record (L3-2 compares a window's epoch with the record's first)

Pred = tuple[str, Callable[[dict[str, Window]], bool]]


# ----------------------------------------------------------------------------- primitives
def _key(e):
    return (e.t, e.idx)


def count(w: Window, short: str, **args) -> int:
    return len(w.events(short, **args))


def precedes(w: Window, a: tuple[str, dict], b: tuple[str, dict]) -> bool:
    """plan §0: 'A precedes B = the first A row is before the first B row; if either is
    absent the predicate fails'; §3: 'precedes compares t, archive index on ties'."""
    ra, rb = w.events(a[0], **a[1]), w.events(b[0], **b[1])
    if not ra or not rb:
        return False
    return _key(ra[0]) < _key(rb[0])


def samples(w: Window, ch: str):
    return w.channel(ch)


def delta(w: Window, ch: str):
    """plan §3: 'delta(ch) = last(ch) - first(ch) over the window rows and requires >= 2
    samples of ch in the window, otherwise the predicate fails'."""
    s = samples(w, ch)
    if distinct(s) < 2:
        return None
    return s[-1].value - s[0].value


def first(w: Window, ch: str):
    s = samples(w, ch)
    return s[0].value if s else None


def last(w: Window, ch: str):
    s = samples(w, ch)
    return s[-1].value if s else None


def distinct(rows) -> int:
    """A sample is one telemetry arrival: rows duplicated by the exporter (same t) count once."""
    return len({r.t for r in rows})


def fram_samples(w: Window) -> int:
    return min(distinct(samples(w, c)) for c in FRAM_PACKET)


def dispatched_count(w: Window) -> int:
    return count(w, "cmdDisp.OpCodeDispatched")


# ----------------------------------------------------------------------------- predicate builders
def P_count(short: str, n: int, **args) -> Pred:
    alist = ", ".join(f"{k}={v}" for k, v in args.items())
    text = f"event {short}({alist}) count == {n}" if args else f"event {short} count == {n}"
    return text, lambda W, w="W": count(W[w], short, **{k: _val(v) for k, v in args.items()}) == n


def P_count_in(win: str, short: str, n: int, **args) -> Pred:
    text, _ = P_count(short, n, **args)
    return text, lambda W: count(W[win], short, **{k: _val(v) for k, v in args.items()}) == n


def _val(v):
    """Plan literals: hex like 0x3FFC0, ints, enum/bool words -> record values via to_value."""
    from fth.replay import to_value
    return to_value(v)


def P_precedes(a_short, a_args, b_short, b_args, win="W") -> Pred:
    def fmt(s, a):
        return f"event {s}({', '.join(f'{k}={v}' for k, v in a.items())})" if a else f"event {s}"
    text = f"{fmt(a_short, a_args)} precedes {fmt(b_short, b_args)}"
    return text, lambda W: precedes(W[win], (a_short, {k: _val(v) for k, v in a_args.items()}),
                                    (b_short, {k: _val(v) for k, v in b_args.items()}))


def P_delta(ch: str, n: int, win="W") -> Pred:
    return f"delta({ch}) == {n}", lambda W: delta(W[win], ch) == n


def P_first(ch: str, v, win="W") -> Pred:
    return f"first({ch}) == {v}", lambda W: first(W[win], ch) == _val(v)


def P_last(ch: str, v, win="W") -> Pred:
    return f"last({ch}) == {v}", lambda W: last(W[win], ch) == _val(v)


def P_mask(ch: str, mask: str, v: str, win="W") -> Pred:
    def f(W):
        x = last(W[win], ch)
        return isinstance(x, int) and (x & int(mask, 0)) == int(v, 0)
    return f"last({ch}) & {mask} == {v}", f


def P_commands_sent(n: int, win="W") -> Pred:
    return f"commands_sent == {n}", lambda W: W[win].commands_sent == n


def P_markers(win="W") -> Pred:
    return "both markers present", lambda W: W[win].ok


def P_sent_eq_dispatched(win="W") -> Pred:
    return ("commands_sent == count(event cmdDisp.OpCodeDispatched)",
            lambda W: W[win].commands_sent == dispatched_count(W[win]))


def STD(win="W") -> list[Pred]:
    """plan §3 'Standard complete block': both markers present; commands_sent ==
    count(event cmdDisp.OpCodeDispatched); event cmdDisp.OpCodeError count == 0; event
    cmdDisp.TooManyCommands count == 0; delta(framer.DroppedPackets) == 0; fram samples >= 2."""
    return [
        P_markers(win),
        P_sent_eq_dispatched(win),
        P_count_in(win, "cmdDisp.OpCodeError", 0),
        P_count_in(win, "cmdDisp.TooManyCommands", 0),
        P_delta("framer.DroppedPackets", 0, win),
        ("fram samples >= 2", lambda W: fram_samples(W[win]) >= 2),
    ]


def RESET(win: str, reset: bool) -> Pred:
    """R1 — plan window data ``reset``: a `reset: true` window ("(reset window)", plan L3-1) must
    contain the exporter's epoch boundary; any other window must not."""
    if reset:
        return ("epoch change inside the window (reset window)", lambda W: len(W[win].epochs) >= 2)
    return ("no epoch change inside the window", lambda W: len(W[win].epochs) <= 1)


def ABS(win="W") -> list[Pred]:
    """plan §3 'Standard absence block (ABS)': UnexpectedCompletion 0; BufferMismatch 0;
    RawDone 0 (unless the test lists one); CommandRejected 0."""
    return [
        P_count_in(win, "framTester.UnexpectedCompletion", 0),
        P_count_in(win, "framTester.BufferMismatch", 0),
        P_count_in(win, "framTester.RawDone", 0),
        P_count_in(win, "framTester.CommandRejected", 0),
    ]


# ----------------------------------------------------------------------------- L7-1 generator
def xorshift32(v: int) -> int:
    """plan §0: v ^= v << 13; v ^= v >> 17; v ^= v << 5 on U32 with wrap, one iteration."""
    v &= 0xFFFFFFFF
    v ^= (v << 13) & 0xFFFFFFFF
    v ^= v >> 17
    v ^= (v << 5) & 0xFFFFFFFF
    return v & 0xFFFFFFFF


def pattern(seed: int, offset: int, n: int) -> bytes:
    """plan §0: pattern byte i of (seed, offset) = xorshift32(seed ^ (offset + i)) & 0xFF."""
    return bytes(xorshift32(seed ^ (offset + i)) & 0xFF for i in range(n))


assert pattern(0x1001, 0, 4) == bytes.fromhex("21006342")          # §0 test vector
assert pattern(0xF1F1, 0x3FF00, 4) == bytes.fromhex("ffdebd9c")    # §0 test vector
assert sum(a != b for a, b in zip(pattern(0xB00B, 0x035000, 64), pattern(0xB00C, 0x035000, 64))) == 64


def dump_image_ok(w: Window) -> bool:
    """plan L7-1: for every k: event framTester.DumpData(offset=0x3FF00+8k, length=8, data0,
    data1) bytes == xorshift32 pattern(seed 0xF1F1, offset 0x3FF00+8k..+7).

    data0/data1 are read from the raw hex groups of the event text (``raw_args``), each a U32
    rendered with %x (leading zeros dropped), taken as the big-endian image of bytes 0-3 / 4-7
    (the §0 test vector FF DE BD 9C renders as ``ffdebd9c``)."""
    for k in range(32):
        off = 0x3FF00 + 8 * k
        rows = [e for e in w.events("framTester.DumpData", offset=off, length=8)]
        if len(rows) != 1:
            return False
        e = rows[0]
        try:
            d0 = int(e.raw_args["data0"], 16)
            d1 = int(e.raw_args["data1"], 16)
        except (KeyError, ValueError):
            return False
        if d0.to_bytes(4, "big") + d1.to_bytes(4, "big") != pattern(0xF1F1, off, 8):
            return False
    return True


# ----------------------------------------------------------------------------- L2-5 metric
def sweep_elapsed(w: Window):
    """plan L2-5 metrics: 't(event framTester.SweepDone(kind=VERIFY)) - t(framTester.SWEEP sent)
    <= 1200 s (the ground sending precedes it by the uplink latency, < 1 s)'.  `t(X sent)` is the
    commands.jsonl `sent` field (ground wall clock, plan §0) which the record cannot compare with
    FSW time, so the span is measured on the FSW clock from the SWEEP's own cmdDisp.OpCodeDispatched
    row (< 1 s after the sending).  Returns UNVERIFIED when the SWEEP sending / its timestamp is
    missing (R2), None when the UUT event is missing (R2: the predicate fails)."""
    sweeps = [c for c in w.cmds if c.short == "framTester.SWEEP"]
    if not sweeps or not sweeps[0].sent:
        return UNVERIFIED
    done = w.events("framTester.SweepDone", kind="VERIFY")
    disp = [e for e in w.events("cmdDisp.OpCodeDispatched") if e.args.get("opCode") == sweeps[0].opcode]
    if not done or not disp or disp[0].epoch != done[0].epoch:
        return None
    return done[0].fsw - disp[0].fsw


def l32_later_epoch(W) -> bool:
    """plan L3-2: 'window W lies in a later boot epoch than the record's first'."""
    return REC is not None and bool(W["W"].epochs) and W["W"].epochs[0] > REC.epochs[0]


# ----------------------------------------------------------------------------- L3-1 segments
def _segments(w: Window):
    ep = w.epochs
    if not w.ok or len(ep) < 2:
        return None, None
    return w.segment(ep[0]), w.segment(ep[-1])


def r_boot1_dropped(W):
    b1, _ = _segments(W["R"])
    if b1 is None:
        return False
    a, b = last(b1, "framer.DroppedPackets"), last(W["W1"], "framer.DroppedPackets")
    return a is not None and b is not None and a == b


def r_boot2_dropped(W):
    _, b2 = _segments(W["R"])
    if b2 is None:
        return False
    return last(b2, "framer.DroppedPackets") == 0


def w2_cycle_lt_w1(W):
    a, b = last(W["W2"], "rg8Hz.CycleCount"), last(W["W1"], "rg8Hz.CycleCount")
    return isinstance(a, int) and isinstance(b, int) and a < b


# ----------------------------------------------------------------------------- the 12 tests (plan §3, verbatim)
RR, WD, VD, RJ, SD = ("framTester.RequestReturned", "framTester.WriteDone", "framTester.VerifyDone",
                      "framDriver.RequestRejected", "framTester.SweepDone")

TESTS: dict[str, dict] = {
    "L0-1": {
        "complete": STD(),
        "all_of": ABS() + [
            P_last("framDriver.StartupState", "COMPLETE"),
            P_last("framDriver.DeviceIdMatch", "true"),
            P_mask("framDriver.StatusRegister", "0x0C", "0"),
            P_mask("framDriver.StatusRegister", "0xBD", "0x00"),
        ],
    },
    "L0-2": {
        "complete": STD() + [P_commands_sent(3)],
        "all_of": [
            P_count("framTester.Marker", 1, id="0xA5A5"),
            P_precedes("framTester.Marker", {"id": 20}, "framTester.Marker", {"id": "0xA5A5"}),
            P_precedes("framTester.Marker", {"id": "0xA5A5"}, "framTester.Marker", {"id": 21}),
        ],
    },
    "L2-1": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(RR, 1, op="WRITE", client=0, offset=0, length=64, status="ACCEPTED"),
            P_precedes(RR, dict(op="WRITE", client=0, offset=0, length=64, status="ACCEPTED"),
                       WD, dict(client=0, offset=0, length=64, status="OK")),
            P_count(WD, 1, client=0, offset=0, length=64, status="OK"),
            P_count(VD, 1, client=0, offset=0, length=64, status="OK", mismatches=0),
            P_precedes(WD, dict(client=0, offset=0, length=64), VD, dict(client=0, offset=0, length=64)),
            P_delta("framDriver.WritesCompleted", 1),
            P_delta("framDriver.ReadsCompleted", 1),
            P_delta("framDriver.RequestsRejected", 0),
            P_delta("framDriver.SpiErrors", 0),
            P_count(RJ, 0),
            P_count("framDriver.SpiError", 0),
        ],
    },
    "L2-2": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(WD, 1, client=0, offset="0x3FFC0", length=64, status="OK"),
            P_count(VD, 1, client=0, offset="0x3FFC0", length=64, status="OK", mismatches=0),
            P_count(RR, 1, op="READ", client=0, offset="0x3FFC1", length=64, status="OUT_OF_RANGE"),
            P_count(RJ, 1, op="READ", client=0, offset="0x3FFC1", length=64, status="OUT_OF_RANGE"),
            P_delta("framDriver.ReadsCompleted", 1),
            P_delta("framDriver.WritesCompleted", 1),
            P_delta("framDriver.RequestsRejected", 1),
            P_count(VD, 1),
        ],
    },
    "L2-3": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(RR, 1, op="WRITE", client=1, offset="0x010000", length=32, status="ACCEPTED"),
            P_precedes(RR, dict(op="WRITE", client=1, offset="0x010000", length=32, status="ACCEPTED"),
                       WD, dict(client=1, offset="0x010000", length=32, status="OK")),
            P_count(WD, 1, client=1, offset="0x010000", length=32, status="OK"),
            P_count(VD, 1, client=1, offset="0x010000", length=32, status="OK", mismatches=0),
            P_count(VD, 1, client=0, offset="0x010000", length=32, status="OK", mismatches=0),
            P_count(WD, 0, client=0),
            P_delta("framDriver.ReadsCompleted", 2),
            P_delta("framDriver.WritesCompleted", 1),
        ],
    },
    "L2-5": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(SD, 1, kind="WRITE", chunks=4096, errors=0),
            P_count(SD, 1, kind="VERIFY", chunks=4096, errors=0),
            P_precedes(SD, {"kind": "WRITE"}, SD, {"kind": "VERIFY"}),
            P_delta("framDriver.WritesCompleted", 4096),
            P_delta("framDriver.ReadsCompleted", 4096),
            P_delta("framDriver.SpiErrors", 0),
            P_delta("framDriver.RequestsRejected", 0),
        ],
        "metrics": [
            P_delta("framTester.BytesVerified", 262144),
            ("t(event framTester.SweepDone(kind=VERIFY)) - t(framTester.SWEEP sent) <= 1200 s",
             lambda W: (lambda x: x if x == UNVERIFIED else (x is not None and x <= 1200))(sweep_elapsed(W["W"]))),
        ],
    },
    "L3-1": {
        "complete": STD("W1") + STD("W2") + [
            P_markers("R"),
            ("boot-1 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot1 == last(framer.DroppedPackets) in W1", r_boot1_dropped),
            ("boot-2 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot2 == 0", r_boot2_dropped),
        ],
        "all_of": ABS("W1") + [
            P_count_in("W1", WD, 1, client=0, offset="0x020000", length=64, status="OK"),
            P_count_in("R", "framDriver.StartupComplete", 1),
            P_count_in("R", "framDriver.DeviceIdMismatch", 0),
            P_count_in("R", "framDriver.BlockProtectSet", 0),
            P_count_in("R", "framDriver.StartupReadFailed", 0),
            P_last("framDriver.StartupState", "COMPLETE", "R"),
        ] + ABS("W2") + [
            P_count_in("W2", VD, 1, client=0, offset="0x020000", length=64, status="OK", mismatches=0),
            P_first("framDriver.WritesCompleted", 0, "W2"),
            ("last(rg8Hz.CycleCount) in W2 < last(rg8Hz.CycleCount) in W1", w2_cycle_lt_w1),
        ],
    },
    "L3-2": {
        "complete": STD("W1") + STD("W2") + [
            P_markers("R"),
            ("boot-1 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot1 == last(framer.DroppedPackets) in W1", r_boot1_dropped),
            ("boot-2 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot2 == 0", r_boot2_dropped),
        ],
        "all_of": ABS("W1") + [
            P_count_in("W1", WD, 1, client=0, offset="0x010000", length=32, status="OK"),
            P_count_in("R", "framDriver.StartupComplete", 1),
            P_count_in("R", "framDriver.DeviceIdMismatch", 0),
            P_count_in("R", "framDriver.BlockProtectSet", 0),
            P_count_in("R", "framDriver.StartupReadFailed", 0),
            P_last("framDriver.StartupState", "COMPLETE", "R"),
        ] + ABS("W2") + [
            P_count_in("W2", VD, 1, client=0, offset="0x010000", length=32, status="OK", mismatches=0),
            P_first("framDriver.WritesCompleted", 0, "W2"),
            ("last(rg8Hz.CycleCount) in W2 < last(rg8Hz.CycleCount) in W1", w2_cycle_lt_w1),
        ],
    },
    "L5-1": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(RR, 1, op="READ", client=1, offset="0x030000", length=64, status="BUSY"),
            P_count(RJ, 1, op="READ", client=1, offset="0x030000", length=64, status="BUSY"),
            P_count(VD, 1, client=1, offset="0x030000", length=64, status="OK"),
            P_delta("framDriver.ReadsCompleted", 1),
            P_delta("framDriver.RequestsRejected", 1),
            P_delta("framDriver.WritesCompleted", 0),
        ],
    },
    "L5-4": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(RR, 1, op="READ", client=0, offset="0x031000", length=64, status="BUSY"),
            P_count(RJ, 1, op="READ", client=0, offset="0x031000", length=64, status="BUSY"),
            P_count(VD, 1, client=0, offset="0x031000", length=64, status="OK"),
            P_delta("framDriver.ReadsCompleted", 1),
            P_delta("framDriver.RequestsRejected", 1),
            P_delta("framDriver.WritesCompleted", 0),
        ],
    },
    "L5-2": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(RR, 1, op="READ", client=0, offset="0x100", length=0, status="OUT_OF_RANGE"),
            P_count(RR, 1, op="WRITE", client=0, offset="0x100", length=65, status="OUT_OF_RANGE"),
            P_count(RJ, 2, status="OUT_OF_RANGE"),
            P_delta("framDriver.RequestsRejected", 2),
            P_delta("framDriver.ReadsCompleted", 0),
            P_delta("framDriver.WritesCompleted", 0),
            P_count(WD, 0),
            P_count(VD, 0),
        ],
    },
    "L5-3": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(WD, 1, client=0, offset="0x034000", length=64, status="OK"),
            P_count(VD, 1, client=0, offset="0x034000", length=64, status="OK", mismatches=0),
            P_count(RJ, 0),
            P_delta("framDriver.RequestsRejected", 0),
            P_delta("framDriver.WritesCompleted", 1),
            P_delta("framDriver.ReadsCompleted", 1),
        ],
    },
    "L6-1": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(VD, 1, client=0, offset="0x035000", length=64, status="OK", mismatches=64),
            P_delta("framDriver.ReadsCompleted", 1),
        ],
    },
    "L7-1": {
        "complete": STD(),
        "all_of": ABS() + [
            P_count(WD, 4, status="OK"),
            P_count("framTester.DumpData", 32),
            ("for every k: event framTester.DumpData(offset=0x3FF00+8k, length=8, data0, data1) bytes == xorshift32 pattern(seed 0xF1F1, offset 0x3FF00+8k to +7) — computed by the auditor from the plan's normative pattern generator quoted on the page, not from any harness output",
             lambda W: dump_image_ok(W["W"])),  # text = plan.json L7-1 verbatim (bench-1 finding 1)
            P_delta("framDriver.SpiErrors", 0),
            P_delta("framDriver.RequestsRejected", 0),
            P_count(RJ, 0),
        ],
    },
}


# ----------------------------------------------------------------------------- driver
def judge(test: dict, W: dict[str, Window], resets: dict[str, bool]) -> dict:
    complete = list(test["complete"]) + [RESET(name, flag) for name, flag in resets.items()]
    failed_complete = [t for t, f in complete if not _safe(f, W)]
    if failed_complete:                                   # R3: nothing else is evaluated
        return {"verdict": "UNVERIFIED", "failed": failed_complete}
    failed, unverified = [], []
    for t, f in test["all_of"] + test.get("metrics", []):
        r = _safe(f, W)
        if r == UNVERIFIED:
            unverified.append(t)
        elif not r:
            failed.append(t)
    if unverified:                                        # R2: missing `… sent` timestamp
        return {"verdict": "UNVERIFIED", "failed": unverified}
    return {"verdict": "FAIL" if failed else "PASS", "failed": failed}


def _safe(f, W):
    try:
        r = f(W)
        return r if r == UNVERIFIED else bool(r)
    except Exception:  # a predicate that cannot be evaluated on this record fails
        return False


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    record_dir, plan_path, out_path = Path(argv[1]), Path(argv[2]), Path(argv[3])
    plan = json.loads(plan_path.read_text())
    markers = plan.get("markers", {})
    rec: Record = load_record(record_dir, plan.get("event_patterns"),
                              marker_event=markers.get("event", "Marker"),
                              marker_command=markers.get("command"))
    global REC
    REC = rec
    out: dict = {"checker": "independent_fram_checks", "version": 2, "tests": {}}
    for t in plan["tests"]:
        tid = t["id"]
        if tid not in TESTS:
            out["tests"][tid] = {"verdict": "UNVERIFIED", "failed": [f"no independent transcription of {tid}"]}
            continue
        W = {w["name"]: rec.window(int(w["start_id"]), int(w["end_id"]), label=w["name"]) for w in t["windows"]}
        resets = {w["name"]: bool(w.get("reset", False)) for w in t["windows"]}
        v = judge(TESTS[tid], W, resets)
        if tid == "L2-5" and "W" in W:
            el = sweep_elapsed(W["W"])
            v["notes"] = [f"L2-5 elapsed={el:.1f} s" if isinstance(el, float) else f"L2-5 elapsed={el}"]
        out["tests"][tid] = v
    out_path.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v["verdict"] for k, v in out["tests"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
