# FramTester system test — stimulus for the QUAL-FRAM plan

This directory is the *deployment side* of the FRAM qualification test. It holds only the
pytest stimulus (`test_fram_plan.py`) and a copy of the regression vector. Everything that
decides pass/fail lives in the tooling repo
[`LeStarch/fprime-testing-harness`](https://github.com/LeStarch/fprime-testing-harness)
under `pilot/fram/`:

| what | where (single source of truth) |
|---|---|
| plan: tests, windows, procedures, pass rules as predicate data, requirements, decision log | `pilot/fram/plan.json` (`plan.md` is rendered from it: `fth render-plan`) |
| registered helpers and fault knobs (`@helper`, `@knob`) | `pilot/fram/plan_helpers.py` |
| generated fault menu | `pilot/fram/faults-menu.json` (`fth knobs-menu`, never hand-edited) |
| bench command lines (OpenOCD, GDS, recorder, sender) | `pilot/fram/bench.json` |
| regression vector: sealed record `fram-r2` + expected verdicts | `vectors/fram-r2/` |

`vectors/fram-r2.json` here is a *copy* of the tooling repo's `vectors/fram-r2/expected-verdicts.json`
kept so this branch states the verdicts it was ported against; the tooling repo's file is the one the
`fth vectors` gate checks. If the two differ, the tooling repo wins.

## Stimulus only

Each `test_<ID>` sends exactly the commands the plan's procedure lists for test `<ID>`, in plan
order, between the plan's start/end markers. There is **no assertion** on any result in this file;
the only waits are pacing (the plan's `bracket (2 samples)` = two 1 Hz Fram packets, and
"wait for *event*" steps) so a command is not sent while the device is still busy with the last one.
Verdicts come from replaying the record against the plan (`fth evaluate` / `fth audit`), where every
pass rule is data and the auditor can re-run it without the bench.

Fault knobs are applied by the `send` fixture from the sealed draw (`<run>/sealed/faults.json`) to the
drawn test **only**; the test bodies do not know whether a knob is active.

## Running

Live (bench host, campaign root containing `uut/` and the run directories):

```bash
pip install -e <fprime-testing-harness>            # provides `fth` and the pytest plugin
fth manifest ...                                   # repos, image, bench.json, toolchain -> manifest.json
fth draw --manifest manifest.json --seed-file seed.txt --run-dir rfr/<run-id>   # sealed/faults.json (knob names stay sealed)
fth run --run-dir rfr/<run-id> --manifest manifest.json \
        --plan <fprime-testing-harness>/pilot/fram/plan.json \
        --bench <fprime-testing-harness>/pilot/fram/bench.json \
        --tests CuriosityReference/FramTester/test/int
fth audit --run-dir rfr/<run-id> --manifest manifest.json --plan .../pilot/fram/plan.json [--independent checker.py]
```

`fth run` verifies the manifest, flashes the manifest image (OpenOCD program + `dump_image` read-back,
both sha256 in `record/raw/flash.log`), starts the GDS and exactly one recorder, sends the run-start
markers, then runs this directory with
`pytest -p fth.pytest_plugin --fth-plan ... --fth-run-dir ... --fth-manifest ... --fth-bench ...`.

Dry run (no hardware; writes the same `record/raw/cmd/*/command.log` shape the real sender does):

```bash
pytest CuriosityReference/FramTester/test/int -p fth.pytest_plugin --fth-dry-run \
       --fth-plan .../pilot/fram/plan.json --fth-bench .../pilot/fram/bench.json \
       --fth-run-dir /tmp/dry --fth-manifest manifest.json
```

Replay only (judge an existing sealed record, e.g. the regression vector):

```bash
fth evaluate .../pilot/fram/plan.json .../vectors/fram-r2/record -o verdicts.json
fth vectors  .../pilot/fram/plan.json .../vectors            # gate: expected verdicts still reproduced
```

## Bench facts the plan depends on

- **Markers.** `framTester.MARK(id)` → event `framTester.Marker(id)`. Window edges per test:
  L0-1 10/11, L0-2 20/21, L2-1 30/31, L2-2 40/41, L2-3 50/51, L2-5 60/61,
  L3-1 70/71 (W1), 71/72 (R, reset inside), 72/73 (W2), L3-2 74/75,
  L5-1 80/81, L5-4 82/83, L5-2 90/91, L5-3 100/101, L6-1 110/111, L7-1 120/121.
  Ids ≥ `0xFFFF0000` are reserved for the runner's run-start markers (`0xFFFF0001` manifest sha8,
  `0xFFFF0002` seal-id sha8, `0xFFFF0003` crc32(run id)). L0-2 also sends `MARK(0xA5A5)` inside its window.
- **Fram packet.** `telemetry packets Main` in `Top/topology.fpp` is positional: `Error` id 0, `Health`
  id 1, `Fram` id 2. The Fram packet is pushed once per 1 Hz tick by `framTester.schedIn` (this
  deployment otherwise only sends packets on `tlm.SEND_PKT`); one `framDriver.StartupState` row per
  packet is the plan's sample clock (`bracket (2 samples)`). On this branch the packet carries 16
  channels (`McuSerial0..3` appended). The plan's `fram samples >= 2` rule counts the channels listed
  under `packets.fram` in `plan.json`, which are the ones common to every image the plan judges.
- **Health packet.** `tlm.SEND_PKT(Health)` in the plan text is `tlm.SEND_PKT(1)` on the wire
  (symbol `Health`, number `1`); L3-1 uses it to sample `rg8Hz.CycleCount` before and after the reset.
- **Reset.** `bench.reset()` is OpenOCD `reset run` (no power cycle). It is *not* a command: the record
  shows it as an FSW clock restart, which the exporter turns into a new boot epoch; the plan's
  `R` window of L3-1 is declared `reset: true` and L3-2's helper `window_in_later_boot` requires its
  window to lie in a later epoch than the record's first.
- **Recorder duplicates.** The recorder's `channel.log` rows are duplicated; the exporter de-duplicates
  exact duplicate raw lines, so the plan never uses `count` rules on channel rows.
- **Timing.** A full plan run is ≈ 11 min; L2-5's 4096-chunk sweep is ≈ 460 s (budget 1200 s).

## History

The pass-1/2 harness (`CuriosityReference/test/system/runner.py`, `faults.py`) drove the same
procedures with hand-coded knob hooks; it was retired in favour of this directory plus the tooling
repo (commit "harness: retire runner.py/faults.py in favour of fprime-testing-harness").
`test/system/` keeps `bench.sh`, `export.py` and `checker.py` (the frozen independent checker).
