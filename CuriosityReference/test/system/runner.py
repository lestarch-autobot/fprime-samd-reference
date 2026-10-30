#!/usr/bin/env python3
"""Drive the FramDriver system test plan on the bench (Pi side). Drives and records; never decides.
usage: runner.py --out <run-dir> --run <run-id> [--elf <image.elf>] [--manifest-sha8 HEX8] [--seal-sha8 HEX8]
                 [--faults sealed/faults.json] [--tests L0-1,L2-1,...]
Layout: <run-dir>/record/raw/ is the archive (GDS logs, the recorder's fprime-cli-*/ logs, cmd/ for the command
sender's logs, flash.log); stdout of this script belongs in <run-dir>/runner/. Run-start markers (plan §1): the
MARK(U32) sequence 0xFFFF0001, <manifest sha8>, 0xFFFF0002, <seal-id sha8>, 0xFFFF0003, crc32(run id)."""
import argparse, glob, os, re, subprocess, sys, time, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from faults import Faults
from export import read_log

BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench.sh")
P = "CuriosityReference."
MARKS = {"L0-1": (10, 11), "L0-2": (20, 21), "L2-1": (30, 31), "L2-2": (40, 41), "L2-3": (50, 51), "L2-5": (60, 61),
         "L3-1": (70, 73), "L5-1": (80, 81), "L5-2": (90, 91), "L5-3": (100, 101), "L6-1": (110, 111), "L7-1": (120, 121)}
ORDER = ["L0-1", "L0-2", "L2-1", "L2-2", "L2-3", "L2-5", "L3-1", "L5-1", "L5-2", "L5-3", "L6-1", "L7-1"]
RUN_MARK = {"manifest": 0xFFFF0001, "seal": 0xFFFF0002, "run": 0xFFFF0003}
EV = re.compile(r"^TB_PROC_TIME: [\d.]+s, context=\d+,\((.*?)\),([^,]+),(\d+),EventSeverity\.(\w+),(.*)$")
CH = re.compile(r"^TB_PROC_TIME: [\d.]+s, context=\d+,\((.*?)\),([^,]+),(\d+),(.*)$")
TIMEOUTS = {}
CURRENT = ["setup"]

def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)

class Archive:
    """Reads the recorder's logs (raw/fprime-cli-*/; the command sender logs under raw/cmd/ are not read)."""
    def __init__(self, path):
        self.path = path
    def _lines(self, fname):
        out = []
        for f in sorted(glob.glob(os.path.join(self.path, "fprime-cli-*", fname))):
            out += read_log(f)[0]
        return out
    def events(self, name, text=None):
        n = 0
        for l in self._lines("event.log"):
            m = EV.match(l)
            if m and m.group(2).endswith(name) and (text is None or text in m.group(5)):
                n += 1
        return n
    def channel_samples(self, name):
        return len([l for l in self._lines("channel.log") if CH.match(l) and CH.match(l).group(2).endswith(name)])
    def wait(self, fn, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            if fn():
                return True
            time.sleep(0.2)
        log("TIMEOUT waiting for %s" % what)
        TIMEOUTS[CURRENT[0]] = TIMEOUTS.get(CURRENT[0], 0) + 1
        return False

class Bench:
    def __init__(self, archive):
        self.archive = archive
        self.cmdlog = os.path.join(archive, "cmd")
        self.ar = Archive(archive)
    def sh(self, *args, check=False):
        r = subprocess.run([BENCH, *args], capture_output=True, text=True)
        if check and r.returncode != 0:
            log("bench %s rc=%d %s" % (args[0], r.returncode, r.stderr.strip()[-200:]))
        return r
    def start(self):
        """Start GDS + one recorder; abort unless exactly one of each is alive (M5d)."""
        g = self.sh("gds-start", self.archive).stdout.strip().splitlines()
        r = self.sh("rec-start", self.archive).stdout.strip().splitlines()
        g, r = (g[-1] if g else "?"), (r[-1] if r else "?")
        log("processes: gds=%s recorder=%s" % (g, r))
        if g != "1" or r != "1":
            self.stop(); raise SystemExit("ground system not singular (gds=%s recorder=%s)" % (g, r))
    def stop(self):
        self.sh("rec-stop"); self.sh("gds-stop")
    def restart_gds(self):
        # stop right after a Fram packet has been logged so the recorder is not killed mid-packet (torn rows)
        self.bracket(1); time.sleep(0.3)
        log("GDS restart after reset"); self.stop(); time.sleep(2); self.start()
    def cmd(self, mnemonic, args, done=None, timeout=20):
        """Send one command and wait for its OpCodeCompleted plus `done` = (eventName, textSub) completion."""
        before = self.ar.events("cmdDisp.OpCodeCompleted")
        d_before = self.ar.events(done[0], done[1]) if done else 0
        a = [str(x) for x in args]
        self.sh("cmd", P + mnemonic, *(["--arguments", *a] if a else []), "-l", self.cmdlog)
        ok = self.ar.wait(lambda: self.ar.events("cmdDisp.OpCodeCompleted") > before, timeout, mnemonic + " completed")
        if ok and done:
            ok = self.ar.wait(lambda: self.ar.events(done[0], done[1]) > d_before, timeout, "%s %s" % done)
        return ok
    def mark(self, mid):
        return self.cmd("framTester.MARK", [mid], ("framTester.Marker", "marker %d" % mid))
    def bracket(self, n=2):
        before = self.ar.channel_samples("framDriver.StartupState")
        self.ar.wait(lambda: self.ar.channel_samples("framDriver.StartupState") >= before + n, 6 + n, "bracket")
    def write(self, client, offset, length, seed):
        return self.cmd("framTester.WRITE_PATTERN", [client, offset, length, seed], ("framTester.WriteDone", None))
    def verify(self, client, offset, length, seed):
        return self.cmd("framTester.READ_VERIFY", [client, offset, length, seed], ("framTester.VerifyDone", None))
    def raw(self, op, client, offset, length):
        return self.cmd("framTester.REQUEST_RAW", [op, client, offset, length], ("framTester.RequestReturned", None))
    def reset(self):
        self.sh("reset", check=True)
    def flash(self, elf):
        r = self.sh("flash", elf)
        with open(os.path.join(self.archive, "flash.log"), "w") as fh:
            fh.write(r.stdout + r.stderr)
        log("flash rc=%d verified=%s" % (r.returncode, "Verified OK" in r.stdout + r.stderr))
        return r.returncode == 0 and "Verified OK" in r.stdout + r.stderr

def run_start_markers(b, run, msha8, ssha8):
    for key, value in (("manifest", int(msha8, 16)), ("seal", int(ssha8, 16)), ("run", zlib.crc32(run.encode()) & 0xFFFFFFFF)):
        b.mark(RUN_MARK[key]); b.mark(value)

def run_test(tid, b, f):
    start, end = MARKS[tid]
    try:
        b.mark(start); b.bracket(2)
        if tid == "L0-1":
            b.bracket(2)
        elif tid == "L0-2":
            b.mark(0xA5A5); b.bracket()
        elif tid == "L2-1":
            b.write(0, 0x000000, 64, 0x1001)
            c = f.corrupt(tid)
            if c: b.write(c[0], 0x000000, 64, 0x1001 + c[1])
            b.verify(0, 0x000000, 64, f.verify_seed(tid, 0x1001)); b.bracket(2)
        elif tid == "L2-2":
            b.write(0, 0x3FFC0, 64, 0x2002)
            c = f.corrupt(tid)
            if c: b.write(c[0], 0x3FFC0, 64, 0x2002 + c[1])
            b.verify(0, 0x3FFC0, 64, 0x2002); b.raw("READ", 0, 0x3FFC1, 64); b.bracket()
        elif tid == "L2-3":
            b.write(1, 0x010000, 32, 0x3003); b.verify(1, 0x010000, 32, 0x3003); b.verify(0, 0x010000, 32, 0x3003); b.bracket()
        elif tid == "L2-5":
            b.cmd("framTester.SWEEP", [0x5005, f.sweep_chunks(4096), f.verify_seed(tid, 0x5005)],
                  ("framTester.SweepDone", "VERIFY"), timeout=1200); b.bracket()
        elif tid == "L3-1":
            b.write(0, 0x020000, 64, 0x7007); b.cmd("tlm.SEND_PKT", [1]); b.bracket()
            b.mark(71)
            c = f.corrupt(tid)
            if c: b.write(c[0], 0x020000, 64, 0x7007 + c[1])
            time.sleep(1.5)
            sc = b.ar.events("framDriver.StartupComplete")
            b.reset()
            b.ar.wait(lambda: b.ar.events("framDriver.StartupComplete") > sc, 10, "StartupComplete after reset")
            time.sleep(1.0)
            b.restart_gds()  # uplink stalls intermittently after an OpenOCD reset with the GDS attached (plan risk note)
            time.sleep(3)
            b.mark(72)
            b.bracket(2); b.cmd("tlm.SEND_PKT", [1]); b.verify(0, 0x020000, 64, 0x7007); b.bracket()
        elif tid == "L5-1":
            b.cmd("framTester.BURST", [1, 0x030000, 64, 0x8008, f.burst_mode()], ("framTester.VerifyDone", None)); b.bracket()
        elif tid == "L5-2":
            b.raw("READ", 0, 0x000100, 0); b.raw("WRITE", 0, 0x000100, 65); b.bracket()
        elif tid == "L5-3":
            b.cmd("framTester.CHAIN", [0, 0x034000, 64, 0xA00A], ("framTester.VerifyDone", None)); b.bracket()
        elif tid == "L6-1":
            b.write(0, 0x035000, 64, 0xB00B); b.verify(0, 0x035000, 64, 0xB00C); b.bracket()
        elif tid == "L7-1":
            seed = f.final_seed(0xF1F1)
            for k in range(4):
                b.write(0, 0x3FF00 + 64 * k, 64, seed)
            for k in range(32):
                b.cmd("framTester.DUMP", [0x3FF00 + 8 * k, 8], ("framTester.DumpData", None)); time.sleep(0.05)
            b.bracket()
    except Exception as e:  # harness defect: recorded in runner/, never in the record
        log("%s exception: %r" % (tid, e))
    finally:
        b.mark(end)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--run", required=True)
    ap.add_argument("--elf", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "CuriosityReference.elf"))
    ap.add_argument("--manifest-sha8", default="00000000"); ap.add_argument("--seal-sha8", default="00000000")
    ap.add_argument("--faults", default=None); ap.add_argument("--tests", default=None)
    a = ap.parse_args()
    out = os.path.abspath(a.out); archive = os.path.join(out, "record", "raw")
    os.makedirs(os.path.join(archive, "cmd"), exist_ok=True)
    f = Faults(a.faults)
    tests = a.tests.split(",") if a.tests else ORDER
    b = Bench(archive)
    log("run %s" % a.run)
    if not b.flash(a.elf):   # every run starts from the flashed image (read-back verified) and a fresh boot
        raise SystemExit("flash failed")
    time.sleep(1.5)
    b.start()
    try:
        b.ar.wait(lambda: b.ar.channel_samples("framDriver.StartupState") >= 2, 20, "telemetry")
        run_start_markers(b, a.run, a.manifest_sha8, a.seal_sha8)
        for tid in tests:
            CURRENT[0] = tid; log("test %s" % tid); run_test(tid, b, f)
    finally:
        CURRENT[0] = "teardown"; time.sleep(2); b.stop()
    log("timeouts per test: %s" % (TIMEOUTS or "none"))
    log("done")

if __name__ == "__main__":
    main()
