# COV-FRAM dump plan (development trial, not RfR)

Run after a QUAL-FRAM plan run on the `coverage-trial` image (`-DFRAM_COVERAGE_TRIAL=ON`):
`fth run --plan test/gcov-dump/plan.json --tests test/gcov-dump --bench test/int/bench.json --unsealed` (kept outside `test/int/` so the main plan run does not collect its pytest), then
`fth coverage-system --record <run>/record --gcno-root <build cache> --out <dir>` and
`fth render-ql ... --system-coverage <dir>`.
