#!/bin/bash
# Harness pass 2 dry runs: clean + five knob runs, back to back (Pi side). Output under dry2/<run>/.
cd /home/devin/fram-test/fvp
rm -rf dry2; mkdir -p dry2
n=0
# run ids are neutral (dry-<n>): a run id, runner output or verdict file must never name a knob (fprime-system-test-harness §4)
for k in clean verify_wrong_seed corrupt_block final_wrong_image short_sweep busy_skip; do
  n=$((n+1)); mkdir -p dry2/$k/runner
  F=""
  if [ "$k" != clean ]; then mkdir -p dry2/$k/sealed; printf '{"knobs": ["%s"]}\n' "$k" > dry2/$k/sealed/faults.json; F="--faults dry2/$k/sealed/faults.json"; fi
  ./runner.py --out dry2/$k --run dry-$n $F > dry2/$k/runner/stdout.txt 2>&1
  python3 export.py dry2/$k > dry2/$k/export.out 2>&1
  python3 checker.py dry2/$k/record dry2/$k/verdicts.json --run dry-$n > dry2/$k/checker.out 2>&1
  echo "$k done $(date +%H:%M:%S)" >> dry2/progress.txt
done
echo ALL_DONE >> dry2/progress.txt
