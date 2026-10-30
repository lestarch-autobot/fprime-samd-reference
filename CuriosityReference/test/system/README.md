# FramDriver system-test harness (stage 2, Pi side)

Files (flat in `~/fram-test/fvp/` on the Pi; `CuriosityReference.elf` + `TopTopologyDictionary.json` beside them):
- `bench.sh` — bench control only (GDS start/stop, one recorder, command send, OpenOCD flash/reset).
- `runner.py` — drives plan §3 in manifest order; `--run <id>`, `--faults sealed/faults.json`, `--tests`; sends the run-start
  markers (plan §1), flashes the image at run start (`record/raw/flash.log`), aborts unless exactly one GDS and one recorder
  are alive, brackets every window with two Fram telemetry samples, emits END markers in `finally`; counts timeouts per test.
- `faults.py` — knob → parameter adjustment only (never printed; unknown knob = exit 2).
- `export.py <run-dir>` — `record/raw/` → `record/{events,channels,commands}.jsonl` + `export-counts.json` (oracle §2).
- `checker.py <run-dir>/record [verdicts.json] [--run id]` — builder's verdicts, keys = plan text verbatim (oracle §3).

Run layout: `<run>/record/raw/` (GDS logs, recorder `fprime-cli-*/`, `cmd/` sender logs, `flash.log`), `<run>/record/*.jsonl`,
`<run>/runner/stdout.txt`, `<run>/sealed/faults.json` (knob runs), `<run>/verdicts.json`.

One full-plan run ≈ 11 min (L2-5 sweep ≈ 460 s, budget 1200 s). After the L3-1 reset the GDS is restarted (uplink stalls
intermittently after an OpenOCD reset with the GDS attached); the exporter's reboot epochs keep the record ordered.

Dry runs: `runner.py --out dry/<name> --run dry-<name> [--faults dry/<name>/sealed/faults.json]`, then `export.py dry/<name>`,
then `checker.py dry/<name>/record`. Knob names appear only under `sealed/`.
