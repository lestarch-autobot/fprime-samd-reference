"""Fault knobs as harness configuration (fprime-system-test-harness §4).
Reads sealed/faults.json ({"knobs": ["name", ...]} or {"knobs": {"name": {...}}}); exposes parameter
adjustments only. Nothing here is ever logged or printed; an unknown knob name aborts the run silently (exit 2)."""
import json, os, sys

MENU = ("verify_wrong_seed", "corrupt_block", "final_wrong_image", "short_sweep", "busy_skip")

class Faults:
    def __init__(self, path):
        self._on = set()
        if path and os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
            knobs = data.get("knobs", data) if isinstance(data, dict) else data
            names = set(knobs.keys() if isinstance(knobs, dict) else knobs)
            if names - set(MENU):
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
