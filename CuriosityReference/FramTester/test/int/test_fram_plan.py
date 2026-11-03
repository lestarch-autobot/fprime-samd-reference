"""Stimulus for the FRAM qualification plan (QUAL-FRAM) — one pytest per plan test, in plan order.

The plan (ids, windows, commands, pass rules, knobs) is data in ``plan.json`` beside this file
(knobs and helper predicates in ``plan_helpers.py``); this module only *sends* what each test's
procedure lists. No
result is asserted here: ``fth evaluate`` / ``fth audit`` judge the sealed record afterwards.
The only waits are pacing (the plan's ``bracket (2 samples)`` and "wait for X" steps) so that
the next command is not sent before the device has finished the previous transfer.

Fixtures (``fth.pytest_plugin``): ``window(start, end)`` sends ``framTester.MARK(start)`` /
``MARK(end)``; ``send(name, args)`` sends one command and lets the drawn fault knob rewrite or
insert commands for *this* test only; ``bench`` is the non-command side (OpenOCD reset, waits).
"""

FRAM_SAMPLE = "framDriver.StartupState"  # one row per 1 Hz Fram packet: the plan's "bracket (n samples)"
MARK = "framTester.MARK"
HEALTH_PACKET = 1  # tlm.SEND_PKT(Health): packet id 1 of `telemetry packets Main` (positional; Fram is id 2)


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


# ---- L0: harness self-test -------------------------------------------------------------

def test_L0_1(window, send, bench):
    with window(10, 11):
        bracket(bench)
        bracket(bench)


def test_L0_2(window, send, bench):
    with window(20, 21):
        bracket(bench)
        send(MARK, [0xA5A5])
        bracket(bench)


# ---- L2: write / read-back -------------------------------------------------------------

def test_L2_1(window, send, bench):
    with window(30, 31):
        bracket(bench)
        write(send, bench, 0, 0x000000, 64, 0x1001)
        verify(send, bench, 0, 0x000000, 64, 0x1001)   # verify_wrong_seed / corrupt_block act here if L2-1 was drawn
        bracket(bench)


def test_L2_2(window, send, bench):
    with window(40, 41):
        bracket(bench)
        write(send, bench, 0, 0x3FFC0, 64, 0x2002)
        verify(send, bench, 0, 0x3FFC0, 64, 0x2002)
        raw(send, bench, "READ", 0, 0x3FFC1, 64)        # one past the last 64-byte-aligned block: REJECTED
        bracket(bench)


def test_L2_3(window, send, bench):
    with window(50, 51):
        bracket(bench)
        write(send, bench, 1, 0x010000, 32, 0x3003)
        verify(send, bench, 1, 0x010000, 32, 0x3003)
        verify(send, bench, 0, 0x010000, 32, 0x3003)    # the other client reads the same bytes
        bracket(bench)


def test_L2_5(window, send, bench):
    with window(60, 61):
        bracket(bench)
        send("framTester.SWEEP", [0x5005, 4096, 0x5005])
        bench.wait_event("framTester.SweepDone", timeout=1200.0, min_new=2)  # WRITE pass, then VERIFY pass
        bracket(bench)


# ---- L3: retention across an MCU reset --------------------------------------------------

def test_L3_1(window, send, bench):
    # Windows W1 = 70..71, R = 71..72 (reset inside), W2 = 72..73 share their edge markers, so
    # MARK(71) and MARK(72) are sent once each here rather than through nested window() calls.
    with window(70, 73):
        bracket(bench)
        write(send, bench, 0, 0x020000, 64, 0x7007)
        send("tlm.SEND_PKT", [HEALTH_PACKET])
        bracket(bench)
        send(MARK, [71])                   # corrupt_block (if L3-1 was drawn) inserts its WRITE_PATTERN right after this
        bench.wait(1.5)                    # framer flushes at the 1 Hz tick: let Marker(71) reach the archive
        bench.reset()                      # action, not a command: OpenOCD `reset run` (no power cycle)
        bench.wait_channel("framDriver.StartupState", "COMPLETE", timeout=10.0)
        bench.wait(3.0)                    # uplink settles after the reset (plan risk note)
        send(MARK, [72])
        bracket(bench)
        send("tlm.SEND_PKT", [HEALTH_PACKET])
        verify(send, bench, 0, 0x020000, 64, 0x7007)
        bracket(bench)


def test_L3_2(window, send, bench):
    # Self-contained retention (plan L3-2, bench-1 redesign): the block is written by this test, right
    # before its own reset, and verified after it — no dependence on another test's device content
    # (L2-5's SWEEP rewrites the whole array). Windows W1 = 74..75, R = 75..76 (reset inside),
    # W2 = 76..77 share edges, so MARK(75)/MARK(76) are sent once each, as in test_L3_1.
    with window(74, 77):
        bracket(bench)
        write(send, bench, 0, 0x010000, 32, 0x3003)
        send("tlm.SEND_PKT", [HEALTH_PACKET])
        bracket(bench)
        send(MARK, [75])
        bench.wait(1.5)                    # framer flushes at the 1 Hz tick: let Marker(75) reach the archive
        bench.reset()                      # action, not a command: OpenOCD `reset run` (no power cycle)
        bench.wait_channel("framDriver.StartupState", "COMPLETE", timeout=10.0)
        bench.wait(3.0)                    # uplink settles after the reset (plan risk note)
        send(MARK, [76])
        bracket(bench)
        send("tlm.SEND_PKT", [HEALTH_PACKET])
        verify(send, bench, 0, 0x010000, 32, 0x3003)
        bracket(bench)


# ---- L5: driver protocol ---------------------------------------------------------------

def test_L5_1(window, send, bench):
    with window(80, 81):
        bracket(bench)
        send("framTester.BURST", [1, 0x030000, 64, 0x8008, "BACK_TO_BACK"])  # busy_skip may rewrite the mode
        bench.wait_event("framTester.VerifyDone")
        bracket(bench)


def test_L5_4(window, send, bench):
    with window(82, 83):
        bracket(bench)
        send("framTester.BURST", [0, 0x031000, 64, 0x8118, "BACK_TO_BACK"])
        bench.wait_event("framTester.VerifyDone")
        bracket(bench)


def test_L5_2(window, send, bench):
    with window(90, 91):
        bracket(bench)
        raw(send, bench, "READ", 0, 0x000100, 0)         # zero length: REJECTED
        raw(send, bench, "WRITE", 0, 0x000100, 65)       # one over the 64-byte maximum: REJECTED
        bracket(bench)


def test_L5_3(window, send, bench):
    with window(100, 101):
        bracket(bench)
        send("framTester.CHAIN", [0, 0x034000, 64, 0xA00A])
        bench.wait_event("framTester.VerifyDone")
        bracket(bench)


# ---- L6: mismatch detection ------------------------------------------------------------

def test_L6_1(window, send, bench):
    with window(110, 111):
        bracket(bench)
        write(send, bench, 0, 0x035000, 64, 0xB00B)
        verify(send, bench, 0, 0x035000, 64, 0xB00C)    # deliberately the wrong seed: mismatches must be reported
        bracket(bench)


# ---- L7: final image ---------------------------------------------------------------------

def test_L7_1(window, send, bench):
    with window(120, 121):
        bracket(bench)
        for k in range(4):
            write(send, bench, 0, 0x3FF00 + 64 * k, 64, 0xF1F1)  # final_wrong_image rewrites the seed if L7-1 was drawn
        for k in range(32):
            send("framTester.DUMP", [0x3FF00 + 8 * k, 8])
            bench.wait_event("framTester.DumpData")     # DUMP(k+1) only after DumpData(k) is in the archive
            bench.wait(0.05)
        bracket(bench)
