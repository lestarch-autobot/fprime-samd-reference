"""Stimulus for the COV-FRAM dump plan (development trial): one pytest, run right after the QUAL-FRAM
plan on the same gcov-instrumented coverage-trial image. Sends DUMP_COVERAGE windows of two 32-byte
chunks each, paced so the two 128-byte framer buffers never both fill. STD (every command dispatched,
no dispatcher errors, no dropped packets) is a precondition; ABS and the dump bookends are the checks.
"""
FRAM_SAMPLE = "framDriver.StartupState"
STD = "STD"  # plan.json std_complete: markers, every command dispatched, no dispatcher errors, no drops, >= 2 samples
ABS = "ABS"  # plan.json abs.ABS: nothing unexpected from the tester
CHUNKS_PER_COMMAND = 2   # two CoverageChunk events (~55 B each) fit one 128-byte downlink frame
COMMANDS = 160           # 320 chunks = 10 KiB window, past the ~6 KiB the two objects produce


def test_COV_1(window, check, require, send, bench):
    with window(900, 901):
        require(STD)
        check(ABS)
        check("event framTester.CoverageObject(object=0) count == 1")
        check("event framTester.CoverageObject(object=1) count == 1")
        check("event framTester.CoverageObject(object=1) precedes event framTester.CoverageDumpDone")
        check("commands_sent == 162")
        bench.wait_samples(FRAM_SAMPLE, 2)
        for i in range(COMMANDS):
            send("framTester.DUMP_COVERAGE", [i * CHUNKS_PER_COMMAND, CHUNKS_PER_COMMAND])
            bench.wait(0.3)
        bench.wait_samples(FRAM_SAMPLE, 2)
