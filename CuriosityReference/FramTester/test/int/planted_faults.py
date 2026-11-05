"""Planted faults for QUAL-FRAM (the former helper predicates now live in ``test_fram_plan.py`` as
check functions beside the checks that use them).

Loaded by ``fth`` through ``plan.json: helpers_module``. Planted faults receive
``(test_id, command_name, args)`` and return rewritten ``args`` or a list of
``(command_name, args)`` to send instead. Each ``fails`` list names, per test and verbatim, the
checks of ``test_fram_plan.py`` the fault must break; ``faults-menu.json`` is generated FROM these
decorators (``fth planted-faults-menu plan.json``) and ``fth verify-plan --tests`` / the ``fth run``
pre-flight refuse a name no test declares.
"""
from fth.registry import planted_fault


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
