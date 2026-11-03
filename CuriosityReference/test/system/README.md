# FramDriver system test — bench helpers and the independent checker

The stage-2 runner (`runner.py`, `faults.py`, `runall2.sh`) has been retired. Stimulus is now
`CuriosityReference/FramTester/test/int/test_fram_plan.py` (pytest, stimulus only) driven by
`fth run` from [`LeStarch/fprime-testing-harness`](https://github.com/LeStarch/fprime-testing-harness);
the plan, its pass rules as predicate data, the fault knobs and the generated fault menu live in that
repo under `pilot/fram/`. See `CuriosityReference/FramTester/test/int/README.md` for how to run live,
dry and replay-only.

What remains here:
- `bench.sh` — bench control only (GDS start/stop, one recorder, command send, OpenOCD flash/reset) for
  hand use on the Pi; `fth run` uses the equivalent lines from `pilot/fram/bench.json`.
- `export.py <run-dir>` — `record/raw/` → `record/{events,channels,commands}.jsonl` + `export-counts.json`
  (oracle §2); `fth export` is the maintained equivalent.
- `checker.py <run-dir>/record [verdicts.json] [--run id]` — the builder's independent checker of the
  12 approved tests (keys = plan text verbatim). The auditor runs it as `fth audit --independent` and
  diffs it against the engine; it does not know the redundancy tests L3-2 / L5-4 added in the port.

Run layout (unchanged): `<run>/record/raw/` (GDS logs, recorder `fprime-cli-*/`, `cmd/` sender logs,
`flash.log`), `<run>/record/*.jsonl`, `<run>/runner/`, `<run>/sealed/faults.json` (knob runs),
`<run>/auditor/`.
