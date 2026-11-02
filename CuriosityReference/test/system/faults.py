"""Fault knobs as harness configuration (fprime-system-test-harness §4).
Reads sealed/faults.json ({"knobs": ["name", ...]} or {"knobs": {"name": {...}}}); exposes parameter
adjustments only. Nothing here is ever logged or printed; an unknown knob name or a malformed file aborts the run (stderr message without the name, exit 2)."""
import json, os, sys

MENU = ("verify_wrong_seed", "corrupt_block", "final_wrong_image", "short_sweep", "busy_skip")

class Faults:
    def __init__(self, path):
        self._on = set()
        if path:
            try:
                with open(path) as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and "faults" in data:      # seal.py draw: {"manifest_sha256", "faults": {test: knob}}
                    names = set(data["faults"].values())
                elif isinstance(data, dict) and "knobs" in data:     # dry-run tooling: {"knobs": [name, ...]}
                    names = set(data["knobs"])
                else:
                    raise ValueError("faults file has neither 'faults' nor 'knobs'")
                unknown = names - set(MENU)
                if unknown:
                    raise ValueError("%d unknown knob name(s)" % len(unknown))   # names are never printed
            except (OSError, ValueError, AttributeError, TypeError) as e:
                sys.stderr.write("faults: cannot use %s: %s\n" % (path, e))
                sys.exit(2)
            self._on = names

    def _is(self, name):
        return name in self._on

    def verify_seed(self, test, seed):
        """Seed the harness verifies against (READ_VERIFY / SWEEP verifySeed)."""
        if self._is("verify_wrong_seed") and test in ("L2-1", "L2-5"):
            return (seed + 1) & 0xFFFFFFFF
        return seed

    def corrupt(self, test):
        """Return (client, seed_delta) for an extra overwrite of the test block, or None."""
        if self._is("corrupt_block") and test in ("L2-1", "L2-2", "L3-1"):
            return (1, 7)
        return None

    def final_seed(self, seed):
        return 0xF1F2 if self._is("final_wrong_image") else seed

    def sweep_chunks(self, chunks):
        return 4095 if self._is("short_sweep") else chunks

    def burst_mode(self):
        return "SEQUENTIAL" if self._is("busy_skip") else "BACK_TO_BACK"
