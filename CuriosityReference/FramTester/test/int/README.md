# FramTester system test — the QUAL-FRAM plan and its stimulus

This directory is the **single source of truth** for the FRAM qualification system test. By the
Captain's layout rule (test-process skills, "Layout"): the plan, the planted faults, the
integration tests (stimulus + named checks) and the independent checker live in the deployment branch that
hosts the test-helper component (`CuriosityReference/FramTester`), beside it; the UUT repo
(`fprime-community/fprime-devices`, `Fram.FramDriver`) holds the component, its unit tests and
its SDD; the tooling repo [`LeStarch/fprime-testing-harness`](https://github.com/LeStarch/fprime-testing-harness)
holds nothing UUT-specific (generic `fth` engine/runner/auditor/renderer plus vendored *records*
as regression vectors, each with a labelled copy of this plan under `vectors/plans/qual-fram/`).

| what | file (here) |
|---|---|
| plan: tests, windows, procedures, metrics, requirements, planted-fault menu source, decision log — **no pass rules**: the named checks live in the test functions (phase 1) | `plan.json` (`plan.md` is rendered from it: `fth render-plan plan.json`, never hand-edited) |
| planted-fault bodies (`@planted_fault`; their `fails` lists name checks verbatim) | `plan_helpers.py` (named by `plan.json` `helpers_module`) |
| generated fault menu | `faults-menu.json` (`fth planted-faults-menu plan.json`, never hand-edited) |
| bench command lines (OpenOCD, GDS, recorder, sender) | `bench.json` |
| integration tests, one per plan id: stimulus inside `with window(...)`, named checks `check("<name>")`, infrastructure preconditions `require(...)`, `@pytest.mark.req(...)`; the former helper predicates are check functions here | `test_fram_plan.py` |
| the adversary's independent checker (legacy: written from the plan text alone for the plan-owned pass rules; optional since phase 2 — the auditor replays the test functions) | `independent_fram_checks.py` — `fth audit --independent` (legacy plans only) |
| plan import provenance (the approved `plan.md` the JSON was ported from) | `import/` |
| regression verdicts this branch was ported against | `vectors/fram-r2.json` (copy of the tooling repo's `vectors/fram-r2/expected-verdicts.json`) |

Run (from a checkout of this branch, `fth` installed at the manifest's pin):

```
fth verify-plan CuriosityReference/FramTester/test/int/plan.json --tests CuriosityReference/FramTester/test/int/test_fram_plan.py   # dry-runs the tests: every planted-fault `fails` predicate must be a check they declare
fth run --run-dir <run> --manifest <manifest.json> --plan CuriosityReference/FramTester/test/int/plan.json \
       --bench CuriosityReference/FramTester/test/int/bench.json --tests CuriosityReference/FramTester/test/int
fth audit --run-dir <run> --manifest <manifest.json> --plan CuriosityReference/FramTester/test/int/plan.json \
          --tests CuriosityReference/FramTester/test/int --bench CuriosityReference/FramTester/test/int/bench.json \
          --sdd lib/fprime-devices/fprime-devices/Fram/Components/FramDriver/docs/sdd.md \
          --dictionary build-artifacts/samd21g17/CuriosityReference/dict/TopTopologyDictionary.json --project-root .
```

## Stimulus only

Each `test_<ID>` sends exactly the commands the plan's procedure lists for test `<ID>`, in plan
order, inside `with window(start, end):` (the plan's start/end markers), and declares there the
**named checks** that judge the window — `check("<name>")` in the plan grammar (`event X(args) count == n`,
`A precedes B`, `delta(channel) == n`, `last(channel) == v`, …) or `check("<name>", fn)` for a check function
(the former `plan_helpers.py` helper predicates: boot-segment comparisons, the xorshift32 dump image).
When the window closes every check is judged over the record and all failures are raised together (no
fail-fast). `require(...)` names infrastructure preconditions (`STD`: markers, every command dispatched,
no dispatcher errors, no dropped packets, two bracket samples): not holding → `Incomplete` → UNVERIFIED.
An expected UUT event that is absent is a FAIL, never UNVERIFIED. The only waits are pacing (the plan's
`bracket (2 samples)` = two 1 Hz Fram packets, and "wait for *event*" steps) so a command is not sent while
the device is still busy with the last one. Verdicts are pytest's (`fth run` reads its junit: pass → PASS,
failed check → FAIL, Incomplete → UNVERIFIED); the auditor re-executes the same test functions over the
sealed record (`fth audit --tests …`: every `send()` must meet the recorded command in order, waits return at
once, the checks are judged over the record) and diffs them per test against the runner's junit — any
difference is a DIVERGENCE finding. A judged FAIL stands whatever else happens on the test; all failed checks
are raised together at the test's last window; a window without a `check(...)` can never PASS.

Planted faults are applied by the `send` fixture from the sealed draw (`<run>/sealed/faults.json`) to the
drawn test **only**; the test bodies do not know whether a planted fault is active.

## Running

Live (bench host, campaign root containing `uut/` and the run directories):

```bash
pip install -e <fprime-testing-harness>            # provides `fth` and the pytest plugin
fth manifest ...                                   # repos, image, bench.json, toolchain -> manifest.json
fth draw --manifest manifest.json --seed-file seed.txt --run-dir rfr/<run-id>   # sealed/faults.json (planted fault names stay sealed)
fth run --run-dir rfr/<run-id> --manifest manifest.json \
        --plan CuriosityReference/FramTester/test/int/plan.json \
        --bench CuriosityReference/FramTester/test/int/bench.json \
        --tests CuriosityReference/FramTester/test/int
fth audit --run-dir rfr/<run-id> --manifest manifest.json --plan CuriosityReference/FramTester/test/int/plan.json \
          --tests CuriosityReference/FramTester/test/int --bench CuriosityReference/FramTester/test/int/bench.json
```

`fth run` verifies the manifest, flashes the manifest image (OpenOCD program + `dump_image` read-back,
both sha256 in `record/raw/flash.log`), starts the GDS and exactly one recorder, sends the run-start
markers, then runs this directory with
`pytest -p fth.pytest_plugin --fth-plan ... --fth-run-dir ... --fth-manifest ... --fth-bench ...`.

Dry run (no hardware; writes the same `record/raw/cmd/*/command.log` shape the real sender does):

```bash
pytest CuriosityReference/FramTester/test/int -p fth.pytest_plugin --fth-dry-run \
       --fth-plan CuriosityReference/FramTester/test/int/plan.json --fth-bench CuriosityReference/FramTester/test/int/bench.json \
       --fth-run-dir /tmp/dry --fth-manifest manifest.json
```

Replay only (judge an existing sealed record with these tests, no device — development):

```bash
fth run --run-dir <scratch> --manifest <manifest.json> --plan CuriosityReference/FramTester/test/int/plan.json \
        --bench CuriosityReference/FramTester/test/int/bench.json --tests CuriosityReference/FramTester/test/int \
        --replay-record <fprime-testing-harness>/vectors/bench-clean4/record      # runner/verdicts.json from the junit
fth vectors  <fprime-testing-harness>/vectors               # gate: each vector judged against its vendored plan copy
```
(`fth evaluate` is the legacy engine and refuses this plan: its checks live in the tests.)

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
procedures with hand-coded planted fault hooks; it was retired in favour of this directory plus the tooling
repo (commit "harness: retire runner.py/faults.py in favour of fprime-testing-harness").
`test/system/` keeps `bench.sh`, `export.py` and `checker.py` (the frozen independent checker).
