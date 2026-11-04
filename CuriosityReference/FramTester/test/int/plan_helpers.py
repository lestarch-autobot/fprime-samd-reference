"""Pilot plan helpers for QUAL-FRAM: helper predicates and planted faults.

Loaded by ``fth`` through ``plan.json: helpers_module``. Helpers receive a
``HelperContext`` (``ctx.window``, ``ctx.windows``, ``ctx.segment(name, boot)``) and return
``(holds, seen_text)`` or ``(holds, seen_text, refs, unverified)``. Planted faults receive
``(test_id, command_name, args)`` and return rewritten ``args`` or a list of
``(command_name, args)`` to send instead. ``fails`` lists come from the sealed fault menu
(``aux/manifest-d994623e.json``) and are per test.
"""
from fth.registry import helper, planted_fault


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


@helper("last_in_boot_equals_last_in_window")
def last_in_boot_equals_last_in_window(ctx, channel, window, boot, other):
    seg = ctx.segment(window, boot)
    a, b = _last(seg, channel), _last(ctx.windows[other], channel)
    if a is None or b is None:
        return False, f"no sample of {channel} in {seg.label if a is None else other}", [], True
    return a.value == b.value, f"{window}-boot{boot + 1} last={a.value} ({a.line}); {other} last={b.value} ({b.line})", [
        f"channels.jsonl:{a.idx}-{a.idx}", f"channels.jsonl:{b.idx}-{b.idx}"]


@helper("last_in_boot_equals")
def last_in_boot_equals(ctx, channel, window, boot, value):
    seg = ctx.segment(window, boot)
    a = _last(seg, channel)
    if a is None:
        return False, f"no sample of {channel} in {seg.label}", [], True
    return a.value == value, f"{window}-boot{boot + 1} last={a.value} ({a.line})", [f"channels.jsonl:{a.idx}-{a.idx}"]


@helper("last_less_than_other_window")
def last_less_than_other_window(ctx, channel, other):
    a, b = _last(ctx.window, channel), _last(ctx.windows[other], channel)
    if a is None or b is None:
        return False, f"no sample of {channel} in {ctx.window.label if a is None else other}", [], True
    return a.value < b.value, f"{ctx.window.label} last={a.value} ({a.line}) vs {other} last={b.value} ({b.line})", [
        f"channels.jsonl:{a.idx}-{a.idx}", f"channels.jsonl:{b.idx}-{b.idx}"]


@helper("dump_image_matches_xorshift32")
def dump_image_matches_xorshift32(ctx, seed, base, chunk, count):
    """Every DumpData(offset=base+chunk*k) carries the auditor-regenerated pattern bytes."""
    rows = ctx.window.events("framTester.DumpData")
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


# ---- planted faults (menu is generated from these: ``fth planted-faults-menu plan.json``) ----

# ---------------------------------------------------------------------------------------
# Planted faults. ``command_name`` is the spelling the stimulus passes to ``send()`` (the
# ``framTester.X`` short form; the deployment prefix is added later by the sender). Each planted fault
# rewrites only the named command of the named test and returns ``args`` untouched otherwise.
# ---------------------------------------------------------------------------------------
def _is(command_name, short):
    return command_name == short or command_name.endswith("." + short)


@planted_fault("verify_wrong_seed", applies_to=["L2-1", "L2-5"], cls="harness-expectation",
      mechanism="harness verifies (READ_VERIFY / SWEEP verify pass) against seed+1 while the device holds the planned seed; BytesVerified counts bytes compared, so its delta predicates still hold",
      fails={
        "L2-1": [
            "event framTester.VerifyDone(client=0, offset=0, length=64, status=OK, mismatches=0) count == 1"
        ],
        "L2-5": [
            "event framTester.SweepDone(kind=VERIFY, chunks=4096, errors=0) count == 1"
        ]
    })
def verify_wrong_seed(test_id, command_name, args):
    if test_id == "L2-1" and _is(command_name, "framTester.READ_VERIFY"):
        return args[:3] + [(args[3] + 1) & 0xFFFFFFFF]
    if test_id == "L2-5" and _is(command_name, "framTester.SWEEP"):
        return args[:2] + [(args[2] + 1) & 0xFFFFFFFF]
    return args


@planted_fault("corrupt_block", applies_to=["L2-1", "L2-2", "L3-1"], cls="uut-state",
      mechanism="harness overwrites the test's 64-byte block with seed+7 (WRITE_PATTERN from client 1) between the planned write and the verify — inside the window for L2-1/L2-2 (hence the WritesCompleted delta also fails); for L3-1 issued right after MARK(71), before the reset, i.e. in window R (outside W1/W2; no ripple to L7-1, whose region 0x3FF00.. is rewritten by L7-1 itself)",
      fails={
        "L2-1": [
            "event framTester.VerifyDone(client=0, offset=0, length=64, status=OK, mismatches=0) count == 1",
            "delta(framDriver.WritesCompleted) == 1"
        ],
        "L2-2": [
            "event framTester.VerifyDone(client=0, offset=0x3FFC0, length=64, status=OK, mismatches=0) count == 1",
            "delta(framDriver.WritesCompleted) == 1"
        ],
        "L3-1": [
            "event framTester.VerifyDone(client=0, offset=0x020000, length=64, status=OK, mismatches=0) count == 1"
        ]
    }, kind="insert")
def corrupt_block(test_id, command_name, args):
    if test_id in ("L2-1", "L2-2") and _is(command_name, "framTester.WRITE_PATTERN"):
        client, offset, length, seed = args
        return [(command_name, args), (command_name, [1, offset, length, (seed + 7) & 0xFFFFFFFF])]
    if test_id == "L3-1" and _is(command_name, "framTester.WRITE_PATTERN"):
        # inside W1, before the reset (the marker command is never handed to a planted fault — iter-4 ADV-E #1)
        client, offset, length, seed = args
        return [(command_name, args), (command_name, [1, offset, length, (seed + 7) & 0xFFFFFFFF])]
    return args


@planted_fault("final_wrong_image", applies_to=["L7-1"], cls="uut-state",
      mechanism="harness writes the final region with seed 0xF1F2 while the plan's dump expectation stays 0xF1F1 (device content disturbed; final-state row expected FAIL)",
      fails=[
        "for every k: event framTester.DumpData(offset=0x3FF00+8k, length=8, data0, data1) bytes == xorshift32 pattern(seed 0xF1F1, offset 0x3FF00+8k to +7) — computed by the auditor from the plan's normative pattern generator quoted on the page, not from any harness output"
    ])
def final_wrong_image(test_id, command_name, args):
    if test_id == "L7-1" and _is(command_name, "framTester.WRITE_PATTERN") and args[3] == 0xF1F1:
        return args[:3] + [0xF1F2]
    return args


@planted_fault("short_sweep", applies_to=["L2-5"], cls="stimulus",
      mechanism="harness configures SWEEP over 4095 chunks and still expects the full device",
      fails=[
        "event framTester.SweepDone(kind=WRITE, chunks=4096, errors=0) count == 1",
        "event framTester.SweepDone(kind=VERIFY, chunks=4096, errors=0) count == 1",
        "delta(framDriver.WritesCompleted) == 4096",
        "delta(framDriver.ReadsCompleted) == 4096",
        "delta(framTester.BytesVerified) == 262144"
    ])
def short_sweep(test_id, command_name, args):
    if test_id == "L2-5" and _is(command_name, "framTester.SWEEP"):
        return [args[0], 4095, args[2]]
    return args


@planted_fault("busy_skip", applies_to=["L5-1"], cls="stimulus",
      mechanism="harness BURST mode SEQUENTIAL issues the second read from inside the first read's completion instead of back-to-back (FPP enum FramTester.BurstMode: BACK_TO_BACK | SEQUENTIAL)",
      fails=[
        "event framTester.RequestReturned(op=READ, client=1, offset=0x030000, length=64, status=BUSY) count == 1",
        "event framDriver.RequestRejected(op=READ, client=1, offset=0x030000, length=64, status=BUSY) count == 1",
        "delta(framDriver.RequestsRejected) == 1",
        "event framTester.VerifyDone(client=1, offset=0x030000, length=64, status=OK) count == 1",
        "delta(framDriver.ReadsCompleted) == 1"
    ])
def busy_skip(test_id, command_name, args):
    if test_id == "L5-1" and _is(command_name, "framTester.BURST"):
        return args[:4] + ["SEQUENTIAL"]
    return args
