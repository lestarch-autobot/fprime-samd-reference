"""Integration tests for the FRAM qualification plan (QUAL-FRAM) — one pytest per plan test, in plan order.

Each test *is* its plan entry: the stimulus it sends inside ``with window(start, end):`` and the named
checks it declares there with ``check("<name>")``. When the window closes (END marker sent) every
check is judged over the closed window's history and the failures are raised together — no fail-fast,
every check name is reported. The names are the identity the planted-fault menu (``fails:`` lists),
the unseal and the VnV matrix refer to: keep them stable.

- ``check("<predicate>")`` uses the plan grammar (``event X(args) count == n``, ``A precedes B``,
  ``delta(channel) == n``, ``last(channel) == v``, ``first(channel) == v``); ``check("<name>", fn)``
  judges ``fn(h)`` over ``h.window`` / ``h.windows[name]`` / ``h.segment(name, boot)``.
- ``require("<predicate>")`` is an infrastructure precondition — markers, every command dispatched,
  drop counters, bracket samples. Not holding makes the test UNVERIFIED (Incomplete), never FAIL.
  The rule (Captain, 2026-11): *unverified only when the absence itself is not indicative of a UUT
  failure assuming a working system* — an expected UUT event that is missing is a FAIL.
- Waits (``bench.wait_*``) are pacing only: a timeout aborts the stimulus, the checks still judge.
- ``@pytest.mark.req(...)`` carries the requirements the plan test covers (VnV matrix).

``plan.json`` beside this file keeps the windows, metrics, requirement list and the planted-fault
menu (bodies in ``planted_faults.py``); ``send(name, args)`` lets the drawn planted fault rewrite or
insert commands for *this* test only.
"""
import pytest

FRAM_SAMPLE = "framDriver.StartupState"  # one row per 1 Hz Fram packet: the plan's "bracket (n samples)"
MARK = "framTester.MARK"
HEALTH_PACKET = 1  # tlm.SEND_PKT(Health): packet id 1 of `telemetry packets Main` (positional; Fram is id 2)

STD = "STD"  # plan.json std_complete: markers, every command dispatched, no dispatcher errors, no drops, >= 2 samples
ABS = "ABS"  # plan.json abs.ABS: nothing unexpected from the tester (UnexpectedCompletion, BufferMismatch, RawDone, CommandRejected)


def bracket(bench, n=2):
    bench.wait_samples(FRAM_SAMPLE, n)


def write(send, bench, client, offset, length, seed):
    send("framTester.WRITE_PATTERN", [client, offset, length, seed])
    bench.wait_event("framTester.WriteDone")


def verify(send, bench, client, offset, length, seed):
    send("framTester.READ_VERIFY", [client, offset, length, seed])
    bench.wait_event("framTester.VerifyDone")


def raw(send, bench, op, client, offset, length):
    send("framTester.REQUEST_RAW", [op, client, offset, length])
    bench.wait_event("framTester.RequestReturned")


# ---- check functions beyond the plan grammar (formerly plan_helpers.py @helper predicates) ----

def xorshift32_byte(seed, offset, i):
    """Normative pattern byte i of (seed, offset): one xorshift32 iteration of seed ^ (offset+i)."""
    v = (seed ^ ((offset + i) & 0xFFFFFFFF)) & 0xFFFFFFFF
    v ^= (v << 13) & 0xFFFFFFFF
    v ^= v >> 17
    v ^= (v << 5) & 0xFFFFFFFF
    return v & 0xFF


def _last(window, channel):
    rows = window.channel(channel)
    return rows[-1] if rows else None


def last_in_boot_equals_last_in_window(channel, window, boot, other):
    def fn(h):
        seg = h.segment(window, boot)
        a, b = _last(seg, channel), _last(h.windows[other], channel)
        if a is None or b is None:
            return False, f"no sample of {channel} in {seg.label if a is None else other}", [], True
        return a.value == b.value, f"{window}-boot{boot + 1} last={a.value} ({a.line}); {other} last={b.value} ({b.line})", [
            f"channels.jsonl:{a.idx}-{a.idx}", f"channels.jsonl:{b.idx}-{b.idx}"]
    return fn


def last_in_boot_equals(channel, window, boot, value):
    def fn(h):
        seg = h.segment(window, boot)
        a = _last(seg, channel)
        if a is None:
            return False, f"no sample of {channel} in {seg.label}", [], True
        return a.value == value, f"{window}-boot{boot + 1} last={a.value} ({a.line})", [f"channels.jsonl:{a.idx}-{a.idx}"]
    return fn


def last_less_than_other_window(channel, other):
    def fn(h):
        a, b = _last(h.window, channel), _last(h.windows[other], channel)
        if a is None or b is None:
            return False, f"no sample of {channel} in {h.window.label if a is None else other}", [], True
        return a.value < b.value, f"{h.window.label} last={a.value} ({a.line}) vs {other} last={b.value} ({b.line})", [
            f"channels.jsonl:{a.idx}-{a.idx}", f"channels.jsonl:{b.idx}-{b.idx}"]
    return fn


def dump_image_matches_xorshift32(seed, base, chunk, count):
    """Every DumpData(offset=base+chunk*k) carries the regenerated pattern bytes (seed, offset)."""
    def fn(h):
        rows = h.window.events("framTester.DumpData")
        bad, seen = [], {}
        for e in rows:
            if e.args.get("offset") in seen:
                return False, f"duplicate DumpData row for offset {e.args.get('offset')} (L{e.line})", [], False
            seen[e.args.get("offset")] = e
        missing = [base + chunk * k for k in range(count) if (base + chunk * k) not in seen]
        for k in range(count):
            off = base + chunk * k
            e = seen.get(off)
            if e is None:
                continue
            got = b"".join(int(e.raw_args[w], 16).to_bytes(4, "big") for w in ("data0", "data1"))
            want = bytes(xorshift32_byte(seed, off, i) for i in range(chunk))
            if got != want:
                bad.append(f"{e.line} offset 0x{off:X}: got {got.hex()} want {want.hex()}")
        holds = not bad and not missing
        text = f"{count - len(missing)}/{count} dumps compared, {len(bad)} differ"
        if missing:
            text += "; missing offsets " + ", ".join(f"0x{m:X}" for m in missing[:4])
        if bad:
            text += "; " + "; ".join(bad[:3])
        refs = [f"events.jsonl:{rows[0].idx}-{rows[-1].idx}"] if rows else []
        return holds, text, refs
    return fn


# ---- L0: harness self-test -------------------------------------------------------------

@pytest.mark.req("REQ-FramDriver-012", "REQ-FramDriver-015", "REQ-FramDriver-017")
def test_L0_1(window, send, bench, check, require):
    with window(10, 11):
        require(STD)
        bracket(bench)
        bracket(bench)
        check(ABS)
        check("last(framDriver.StartupState) == COMPLETE")
        check("last(framDriver.DeviceIdMatch) == true")
        check("last(framDriver.StatusRegister) & 0x0C == 0")
        check("last(framDriver.StatusRegister) & 0xBD == 0x00")


def test_L0_2(window, send, bench, check, require):
    with window(20, 21):
        require(STD)
        require("commands_sent == 3")
        bracket(bench)
        send(MARK, [0xA5A5])
        bracket(bench)
        check("event framTester.Marker(id=0xA5A5) count == 1")
        check("event framTester.Marker(id=20) precedes event framTester.Marker(id=0xA5A5)")
        check("event framTester.Marker(id=0xA5A5) precedes event framTester.Marker(id=21)")


# ---- L2: write / read-back -------------------------------------------------------------

@pytest.mark.req("REQ-FramDriver-001", "REQ-FramDriver-002", "REQ-FramDriver-003", "REQ-FramDriver-004",
                 "REQ-FramDriver-005", "REQ-FramDriver-011")
def test_L2_1(window, send, bench, check, require):
    with window(30, 31):
        require(STD)
        bracket(bench)
        write(send, bench, 0, 0x000000, 64, 0x1001)
        verify(send, bench, 0, 0x000000, 64, 0x1001)   # verify_wrong_seed / corrupt_block act here if L2-1 was drawn
        bracket(bench)
        check(ABS)
        check("event framTester.RequestReturned(op=WRITE, client=0, offset=0, length=64, status=ACCEPTED) count == 1")
        check("event framTester.RequestReturned(op=WRITE, client=0, offset=0, length=64, status=ACCEPTED) precedes "
              "event framTester.WriteDone(client=0, offset=0, length=64, status=OK)")
        check("event framTester.WriteDone(client=0, offset=0, length=64, status=OK) count == 1")
        check("event framTester.VerifyDone(client=0, offset=0, length=64, status=OK, mismatches=0) count == 1")
        check("event framTester.WriteDone(client=0, offset=0, length=64) precedes event framTester.VerifyDone(client=0, offset=0, length=64)")
        check("delta(framDriver.WritesCompleted) == 1")
        check("delta(framDriver.ReadsCompleted) == 1")
        check("delta(framDriver.RequestsRejected) == 0")
        check("delta(framDriver.SpiErrors) == 0")
        check("event framDriver.RequestRejected count == 0")
        check("event framDriver.SpiError count == 0")


@pytest.mark.req("REQ-FramDriver-001", "REQ-FramDriver-002", "REQ-FramDriver-006", "REQ-FramDriver-008")
def test_L2_2(window, send, bench, check, require):
    with window(40, 41):
        require(STD)
        bracket(bench)
        write(send, bench, 0, 0x3FFC0, 64, 0x2002)
        verify(send, bench, 0, 0x3FFC0, 64, 0x2002)
        raw(send, bench, "READ", 0, 0x3FFC1, 64)        # one past the last 64-byte-aligned block: REJECTED
        bracket(bench)
        check(ABS)
        check("event framTester.WriteDone(client=0, offset=0x3FFC0, length=64, status=OK) count == 1")
        check("event framTester.VerifyDone(client=0, offset=0x3FFC0, length=64, status=OK, mismatches=0) count == 1")
        check("event framTester.RequestReturned(op=READ, client=0, offset=0x3FFC1, length=64, status=OUT_OF_RANGE) count == 1")
        check("event framDriver.RequestRejected(op=READ, client=0, offset=0x3FFC1, length=64, status=OUT_OF_RANGE) count == 1")
        check("delta(framDriver.ReadsCompleted) == 1")
        check("delta(framDriver.WritesCompleted) == 1")
        check("delta(framDriver.RequestsRejected) == 1")
        check("event framTester.VerifyDone count == 1")


@pytest.mark.req("REQ-FramDriver-001", "REQ-FramDriver-002", "REQ-FramDriver-004", "REQ-FramDriver-011",
                 "REQ-FramDriver-003", "REQ-FramDriver-005")
def test_L2_3(window, send, bench, check, require):
    with window(50, 51):
        require(STD)
        bracket(bench)
        write(send, bench, 1, 0x010000, 32, 0x3005)
        verify(send, bench, 1, 0x010000, 32, 0x3005)
        verify(send, bench, 0, 0x010000, 32, 0x3005)    # the other client reads the same bytes
        bracket(bench)
        check(ABS)
        check("event framTester.RequestReturned(op=WRITE, client=1, offset=0x010000, length=32, status=ACCEPTED) count == 1")
        check("event framTester.RequestReturned(op=WRITE, client=1, offset=0x010000, length=32, status=ACCEPTED) precedes "
              "event framTester.WriteDone(client=1, offset=0x010000, length=32, status=OK)")
        check("event framTester.WriteDone(client=1, offset=0x010000, length=32, status=OK) count == 1")
        check("event framTester.VerifyDone(client=1, offset=0x010000, length=32, status=OK, mismatches=0) count == 1")
        check("event framTester.VerifyDone(client=0, offset=0x010000, length=32, status=OK, mismatches=0) count == 1")
        check("event framTester.WriteDone(client=0) count == 0")
        check("delta(framDriver.ReadsCompleted) == 2")
        check("delta(framDriver.WritesCompleted) == 1")


@pytest.mark.req("REQ-FramDriver-001", "REQ-FramDriver-002", "REQ-FramDriver-010")
def test_L2_5(window, send, bench, check, require):
    with window(60, 61):
        require(STD)
        bracket(bench)
        send("framTester.SWEEP", [0x5005, 4096, 0x5005])    # verify_wrong_seed / short_sweep act here if L2-5 was drawn
        bench.wait_event("framTester.SweepDone", timeout=1200.0, min_new=2)  # WRITE pass, then VERIFY pass
        bracket(bench)
        check(ABS)
        check("event framTester.SweepDone(kind=WRITE, chunks=4096, errors=0) count == 1")
        check("event framTester.SweepDone(kind=VERIFY, chunks=4096, errors=0) count == 1")
        check("event framTester.SweepDone(kind=WRITE) precedes event framTester.SweepDone(kind=VERIFY)")
        check("delta(framDriver.WritesCompleted) == 4096")
        check("delta(framDriver.ReadsCompleted) == 4096")
        check("delta(framDriver.SpiErrors) == 0")
        check("delta(framDriver.RequestsRejected) == 0")


# ---- L3: retention across an MCU reset --------------------------------------------------

def _retention(window, send, bench, check, require, w1, offset, length, seed):
    """L3-x: W1 = write + Health packet; R = reset (shares its edge markers with W1/W2); W2 = verify.

    The three windows share edges (W1 end == R start, R end == W2 start): ``window()`` sends each
    shared marker once. The reset is an action (OpenOCD ``reset run``, no power cycle), not a command.
    """
    with window(w1, w1 + 1):
        require(STD)
        bracket(bench)
        write(send, bench, 0, offset, length, seed)     # corrupt_block (if L3-1 was drawn) inserts its WRITE_PATTERN here
        send("tlm.SEND_PKT", [HEALTH_PACKET])
        bracket(bench)
        check(ABS)
        check(f"event framTester.WriteDone(client=0, offset=0x{offset:06X}, length={length}, status=OK) count == 1")
    with window(w1 + 1, w1 + 2):
        require("both markers present")
        require("boot-1 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot1 == last(framer.DroppedPackets) in W1",
                last_in_boot_equals_last_in_window("framer.DroppedPackets", "R", 0, "W1"))
        require("boot-2 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot2 == 0",
                last_in_boot_equals("framer.DroppedPackets", "R", 1, 0))
        bench.wait(1.5)                    # framer flushes at the 1 Hz tick: let the START marker reach the archive
        bench.reset()
        bench.wait_channel("framDriver.StartupState", "COMPLETE", timeout=10.0)
        bench.wait(3.0)                    # uplink settles after the reset (plan risk note)
        check("event framDriver.StartupComplete count == 1")
        check("event framDriver.DeviceIdMismatch count == 0")
        check("event framDriver.BlockProtectSet count == 0")
        check("event framDriver.StartupReadFailed count == 0")
        check("last(framDriver.StartupState) == COMPLETE")
    with window(w1 + 2, w1 + 3):
        require(STD)
        bracket(bench)
        send("tlm.SEND_PKT", [HEALTH_PACKET])
        verify(send, bench, 0, offset, length, seed)
        bracket(bench)
        check(ABS)
        check(f"event framTester.VerifyDone(client=0, offset=0x{offset:06X}, length={length}, status=OK, mismatches=0) count == 1")
        check("first(framDriver.WritesCompleted) == 0")
        check("last(rg8Hz.CycleCount) in W2 < last(rg8Hz.CycleCount) in W1",
              last_less_than_other_window("rg8Hz.CycleCount", "W1"))


@pytest.mark.req("REQ-FramDriver-023", "REQ-FramDriver-012", "REQ-FramDriver-015", "REQ-FramDriver-016")
def test_L3_1(window, send, bench, check, require):
    _retention(window, send, bench, check, require, 70, 0x020000, 64, 0x7007)


@pytest.mark.req("REQ-FramDriver-023")
def test_L3_2(window, send, bench, check, require):
    # Self-contained retention (plan L3-2, bench-1 redesign): the block is written by this test right
    # before its own reset and verified after it — no dependence on another test's device content.
    _retention(window, send, bench, check, require, 74, 0x010000, 32, 0x3003)


# ---- L5: driver protocol ---------------------------------------------------------------

def _burst(window, send, bench, check, require, start, client, offset, seed):
    with window(start, start + 1):
        require(STD)
        bracket(bench)
        send("framTester.BURST", [client, offset, 64, seed, "BACK_TO_BACK"])  # busy_skip may rewrite the mode (L5-1)
        bench.wait_event("framTester.VerifyDone")
        bracket(bench)
        check(ABS)
        check(f"event framTester.RequestReturned(op=READ, client={client}, offset=0x{offset:06X}, length=64, status=BUSY) count == 1")
        check(f"event framDriver.RequestRejected(op=READ, client={client}, offset=0x{offset:06X}, length=64, status=BUSY) count == 1")
        check(f"event framTester.VerifyDone(client={client}, offset=0x{offset:06X}, length=64, status=OK) count == 1")
        check("delta(framDriver.ReadsCompleted) == 1")
        check("delta(framDriver.RequestsRejected) == 1")
        check("delta(framDriver.WritesCompleted) == 0")


@pytest.mark.req("REQ-FramDriver-007", "REQ-FramDriver-008")
def test_L5_1(window, send, bench, check, require):
    _burst(window, send, bench, check, require, 80, 1, 0x030000, 0x8008)


@pytest.mark.req("REQ-FramDriver-007", "REQ-FramDriver-008")
def test_L5_4(window, send, bench, check, require):
    _burst(window, send, bench, check, require, 82, 0, 0x031000, 0x8118)


@pytest.mark.req("REQ-FramDriver-006", "REQ-FramDriver-008", "REQ-FramDriver-016")
def test_L5_2(window, send, bench, check, require):
    with window(90, 91):
        require(STD)
        bracket(bench)
        raw(send, bench, "READ", 0, 0x000100, 0)         # zero length: REJECTED
        raw(send, bench, "WRITE", 0, 0x000100, 65)       # one over the 64-byte maximum: REJECTED
        bracket(bench)
        check(ABS)
        check("event framTester.RequestReturned(op=READ, client=0, offset=0x100, length=0, status=OUT_OF_RANGE) count == 1")
        check("event framTester.RequestReturned(op=WRITE, client=0, offset=0x100, length=65, status=OUT_OF_RANGE) count == 1")
        check("event framDriver.RequestRejected(status=OUT_OF_RANGE) count == 2")
        check("delta(framDriver.RequestsRejected) == 2")
        check("delta(framDriver.ReadsCompleted) == 0")
        check("delta(framDriver.WritesCompleted) == 0")
        check("event framTester.WriteDone count == 0")
        check("event framTester.VerifyDone count == 0")


@pytest.mark.req("REQ-FramDriver-010")
def test_L5_3(window, send, bench, check, require):
    with window(100, 101):
        require(STD)
        bracket(bench)
        send("framTester.CHAIN", [0, 0x034000, 64, 0xA00A])
        bench.wait_event("framTester.VerifyDone")
        bracket(bench)
        check(ABS)
        check("event framTester.WriteDone(client=0, offset=0x034000, length=64, status=OK) count == 1")
        check("event framTester.VerifyDone(client=0, offset=0x034000, length=64, status=OK, mismatches=0) count == 1")
        check("event framDriver.RequestRejected count == 0")
        check("delta(framDriver.RequestsRejected) == 0")
        check("delta(framDriver.WritesCompleted) == 1")
        check("delta(framDriver.ReadsCompleted) == 1")


# ---- L6: mismatch detection ------------------------------------------------------------

def test_L6_1(window, send, bench, check, require):
    with window(110, 111):
        require(STD)
        bracket(bench)
        write(send, bench, 0, 0x035000, 64, 0xB00B)
        verify(send, bench, 0, 0x035000, 64, 0xB00C)    # deliberately the wrong seed: mismatches must be reported
        bracket(bench)
        check(ABS)
        check("event framTester.VerifyDone(client=0, offset=0x035000, length=64, status=OK, mismatches=64) count == 1")
        check("delta(framDriver.ReadsCompleted) == 1")


# ---- L7: final image ---------------------------------------------------------------------

@pytest.mark.req("REQ-FramDriver-001", "REQ-FramDriver-002")
def test_L7_1(window, send, bench, check, require):
    with window(120, 121):
        require(STD)
        bracket(bench)
        for k in range(4):
            write(send, bench, 0, 0x3FF00 + 64 * k, 64, 0xF1F1)  # final_wrong_image rewrites the seed if L7-1 was drawn
        for k in range(32):
            send("framTester.DUMP", [0x3FF00 + 8 * k, 8])
            bench.wait_event("framTester.DumpData")     # DUMP(k+1) only after DumpData(k) is in the archive
            bench.wait(0.05)
        bracket(bench)
        check(ABS)
        check("event framTester.WriteDone(status=OK) count == 4")
        check("event framTester.DumpData count == 32")
        check("for every k: event framTester.DumpData(offset=0x3FF00+8k, length=8, data0, data1) bytes == xorshift32 pattern"
              "(seed 0xF1F1, offset 0x3FF00+8k to +7) — computed by the auditor from the plan's normative pattern generator "
              "quoted on the page, not from any harness output",
              dump_image_matches_xorshift32(0xF1F1, 0x3FF00, 8, 32))
        check("delta(framDriver.SpiErrors) == 0")
        check("delta(framDriver.RequestsRejected) == 0")
        check("event framDriver.RequestRejected count == 0")
