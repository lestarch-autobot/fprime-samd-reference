# COV-FRAM dump plan (development trial, not RfR)

Run after a QUAL-FRAM plan run on the `coverage-trial` image (`-DFRAM_COVERAGE_TRIAL=ON`):
`fth run --plan gcov-dump/plan.json --tests gcov-dump --bench ../bench.json --unsealed`, then
`fth coverage-system --record <run>/record --gcno-root <build cache> --out <dir>` and
`fth render-ql ... --system-coverage <dir>`.
