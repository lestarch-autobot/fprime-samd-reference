L0: desk-checked; bench confirmation pending
# System test plan — Fram.FramDriver on CuriosityReference (run family QUAL-FRAM)

## 0 Scope
- UUT: `Fram.FramDriver` (fprime-community/fprime-devices, `fprime-devices/Fram`), SHA recorded in the manifest at stage 2.
- Deployment: `fprime-samd-reference/CuriosityReference` **system deployment** with the human's stated reduction (I2C driver + I2cTester removed: bench I2C lines PA12/PA13 unterminated → cross-talk), plus `spiDriver: Samd21.SpiDriver` (SERCOM1, 4 MHz, mode 0, software CS), `framDriver: Fram.FramDriver` on SPI slot 0, and the harness component `framTester: CuriosityReference.FramTester` (two client port pairs → `framDriver.read/write[0..1]`).
- Bench: SAMD21 Curiosity Nano on the human's Raspberry Pi (ssh key-only, port 4112), board `/dev/samd21-curiosity`, nEDBG `MCHP3313053000002254`, flash + MCU reset via OpenOCD (`cmsis-dap`), no power cycle (shared hub must not be cycled). Device: CY15B102QN answering RDID `00 2A C2 7F×6` (wire order) = CY15B102QN-50SXI per Infineon 002-26764 Rev. *D (the request named -50SXE; ruling at the human gate).
- Ground system: fprime-gds on the Pi, UART 115200, `--gui none`, one `--logs` directory per run (the archive); exporter = the GDS log/ZMQ record → `record/{events,channels,commands}.jsonl` per `fprime-test-oracle` §2. Dictionary regenerated from the exact image.
- Harness observables (FramTester, harness-owned, not the UUT; definitions below are normative for both checkers): commands `MARK(id: U32)`, `WRITE_PATTERN(client: U8, offset: U32, length: U8, seed: U32)`, `READ_VERIFY(client, offset, length, seed)`, `REQUEST_RAW(op: Fram.Op, client, offset, length)` (buffer of `length` bytes, no pattern), `BURST(client, offset, length, seed, mode: BurstMode)` (BACK_TO_BACK: second READ issued inside the first's request call return path, i.e. before completion; SEQUENTIAL: issued from the first read's completion), `CHAIN(client, offset, length, seed)` (READ issued from inside the write completion), `SWEEP(seed, chunks: U16, verifySeed: U32)` (write `chunks` 64-byte chunks from offset 0 with `seed`, then verify them against `verifySeed`, one request outstanding; the plan uses 4096 and verifySeed = seed), `DUMP(offset: U32, length: U8 ≤ 8)`; events `Marker(id)` ACTIVITY_HI, `RequestReturned(op, client, offset, length, status)` (the call's return value, emitted synchronously for every `framDriver.read/write` call made directly by a command handler — WRITE_PATTERN, READ_VERIFY, REQUEST_RAW, DUMP, both BURST calls — and for CHAIN's chained read; SWEEP emits none for an ACCEPTED return: a non-ACCEPTED return inside SWEEP counts in `errors` and `RejectionsSeen` and ends the pass with `SweepDone(kind, chunks so far, errors)`), `WriteDone(client, offset, length, status: Fram.Status)`, `VerifyDone(client, offset, length, status, mismatches: U8)`, `RawDone(op, client, offset, length, status)` (a completion for a REQUEST_RAW), `UnexpectedCompletion(client, status)` WARNING_HI (completion on a client index with no request outstanding), `BufferMismatch(client)` WARNING_HI (completion buffer pointer or size differs from the one passed), `SweepDone(kind: WRITE|VERIFY, chunks: U16, errors: U16)`, `DumpData(offset, length, data0: U32, data1: U32)`; channels `BytesWritten` (bytes of OK write completions), `BytesVerified` (bytes compared by READ_VERIFY / SWEEP-verify regardless of outcome), `Mismatches` (cumulative mismatching bytes), `RejectionsSeen` (request calls that returned other than ACCEPTED) — all U32, boot-zero, written on change. Harness-side command refusals (busy client, bad length — VALIDATION/EXECUTION_ERROR) are reported as `framTester.CommandRejected` and are in ABS (#14).
  - In `WriteDone` / `VerifyDone` / `RawDone`, `client` is the index of the `readComplete` / `writeComplete` port the completion arrived on; `offset` and `length` come from the completion arguments (buffer size), never from the command.
  - `SWEEP(seed)`: FramTester is passive with no rate-group slot; chunk k+1 is issued from inside chunk k's completion callback (the REQ-010 path, 4095 times per pass); the WRITE pass ends when the chunk at `Size − MaxTransfer` completes, then the VERIFY pass starts the same way; `errors` = non-OK completions (WRITE) or chunks with ≥ 1 mismatching byte (VERIFY). `BURST`: the second READ is issued from the BURST command handler immediately after the first call returned ACCEPTED (before any completion). `CHAIN`: the READ is issued from inside the write completion.
  - Pattern generator (normative): `xorshift32(v)`: `v ^= v << 13; v ^= v >> 17; v ^= v << 5` on U32 with wrap, one iteration; pattern byte i of (seed, offset) = `xorshift32(seed ^ (offset + i)) & 0xFF`. Test vectors (computed offline, in §8 #5): (seed 0x1001, offset 0) bytes 0–3 = 21 00 63 42; (0xF1F1, 0x3FF00) bytes 0–3 = FF DE BD 9C; (0xB00B vs 0xB00C, 0x035000, 64 bytes) → 64 mismatches.
  - Event predicates match on the listed arguments only; unlisted arguments are wildcards. `A precedes B` = the first A row is before the first B row; if either is absent the predicate fails. Times are archive `recv`; `t(X sent)` is the `commands.jsonl` `sent` field; rows with equal `recv` are ordered by archive sequence.
- Deployment delta vs `fprime-samd-reference` main (test deployment; the I2C removal is the human's, the additions are the designer's — human gate confirms, §8 #1): removed `i2cDriver`, `i2cTester` (+ their `DmaChannel` entries and `Error`-packet channels); added `spiDriver: Samd21.SpiDriver` (SERCOM1, DmaChannel SERCOM1_SPI_TX/RX), `framDriver: Fram.FramDriver` (slot 0, `startup()` in startTasks), `framTester: CuriosityReference.FramTester`; connections `cycler.cycleOut -> spiDriver.activeIn`, `framTester.read/write[0..1] -> framDriver.read/write[0..1]` + matched completions; config `FramerConfig::FRAMER_TX_BUFFER_SIZE = 256`; telemetry scheduling: `tlm.pktSendIn` is unconnected in CuriosityReference and packets go out only on `tlm.SEND_PKT(id)`, so the test build connects `rg1Hz.RateGroupMemberOut[1] -> tlm.pktSendIn[Fram]` directly (the slot freed by i2cTester; both ports are Svc.Sched) — one Fram packet per 1 Hz tick; packet ids pinned: `Error 0`, `Health 1`, `Fram id 2 group 1` and `Samd21.StaticTlmPacketizerConfig.NUM_TLM_PACKETS = 3`; packet set delta: `packet Fram group 1 { framDriver.ReadsCompleted WritesCompleted RequestsRejected SpiErrors StartupState DeviceIdMatch StatusRegister framTester.BytesWritten BytesVerified Mismatches RejectionsSeen framer.DroppedPackets }` (id 2). All Fram/FramTester channels are therefore sampled once per second; `framer.DroppedPackets` is a boot-zero counter sampled at 1 Hz, baseline = first sample in the window.
- Out of scope on this bench: SPI fault injection (REQ-009, REQ-014, REQ-021's non-BUSY cases), block-protect (REQ-022) and device-ID mismatch (REQ-013) — no second device, bus fault or protected part; REQ-021's SPI_BUSY_ERR case is plantable in principle (W2, §8 #6) but not attempted in this plan; verified by unit test; see §7.

## 1 Entry gates
- Stage-1 close: `fprime-util check` on `Fram/Components/FramDriver` passes with every row of the SDD unit-test list implemented (REQ-018/019 by inspection: no thread, no queue, no fprime-samd dependency in the library build).
- Image: `CuriosityReference` built from the manifest SHAs (fprime-devices, fprime-samd ≥ 056d4d3, fprime-samd-reference, harness), flashed by OpenOCD with read-back verify (`raw/flash.log`); dictionary from that build copied to the Pi.
- Marker round-trip: `framTester.MARK(0xA5A5)` echoed as `Marker(id=0xA5A5)` once (L0-2) — replaces the contract's `NO_OP_STRING` marker because `Samd21.StaticCmdDispatcher` has no `CMD_NO_OP_STRING` and `FW_LOG_STRING_MAX_SIZE = 2` on this deployment (decision log #2).
- Run-start markers (contract §4 `RFR <run> M/S <sha8>`, string path closed): the runner sends `MARK(0xFFFF0001); MARK(<manifest sha8 as U32>); MARK(0xFFFF0002); MARK(<seal-id sha8 as U32>); MARK(0xFFFF0003); MARK(crc32(<run id>))` before L0-1; ids ≥ 0xFFFF0000 are reserved and never used as test markers. Dry runs carry sha8 = 00000000. The sha8 / crc32 values are arbitrary U32s that share the `Marker(id)` space with the test markers (25 values in 2³²): a collision would place a stray `Marker(id=START/END)` before L0-1, which the §3 window rule tolerates (an END with no START before it is skipped; the START used is the last one before its END) — this reasoning is why the window rule must keep that form. `seal.py unseal` must read this `Marker(id=…)` encoding (stage-3 tooling item, #14).
- Downlink: see the framer burst budget below (the events/s figure is not the binding limit).
- Execution order = the order of `ID:` lines in §3 (manifest order): L0-1, L0-2, L2-1, L2-2, L2-3, L2-5, L3-1, L5-1, L5-2, L5-3, L6-1, L7-1. `RequestRejected` throttle (5 per boot): 1 spent before the L3-1 reset (L2-2), 3 of 5 after it (L5-1, L5-2); any other rejection in the run is a FAIL of the test it lands in.
- Pacing: one harness command outstanding at a time — the next command is sent only after the previous command's `cmdDisp.OpCodeCompleted` and its completion event (WriteDone / VerifyDone / RawDone / DumpData / SweepDone / Marker) are in the archive (≥ 50 ms apart). `CMD_DISPATCHER_SEQUENCER_TABLE_SIZE = 4`.
- Framer burst budget (Samd21.Framer: a packet is dropped when the active buffer is TRANSMITTING or overflows it while the other buffer transmits; after each 1 Hz flush one buffer transmits ≈ 6–11 ms at 11.5 B/ms, so the usable burst during a flush is one buffer): the test build sets `FramerConfig::FRAMER_TX_BUFFER_SIZE = 256` (+256 B RAM; §8 #8) so every command's synchronous events + completion fit one buffer: ≤ 256 B framed per command. Per command (framed B): MARK 103; WRITE_PATTERN 70 + 43 + 39 = 152; READ_VERIFY 70 + 43 + 40 = 153; REQUEST_RAW 70 + 43 + 43 (RequestRejected) = 156; BURST 70 + 43 + 43 + 43 + 40 = 239; CHAIN 113 in the handler, then 39 + 43 at the write completion, then 40 ≈ 235; DUMP 70 + 43 + 42 = 155; SWEEP 70 + 2 × 39 (SweepDone only, seconds apart). A drop still reads UNVERIFIED (STD: DroppedPackets, fram samples), never FAIL.
- Bench recipe file (`bench.yaml`: devices, links, ports, OpenOCD command lines, GDS start line) present and hashed into the manifest; Pi bench exclusive on the CNANO USB port during a run.

## 2 Test levels
L0 provenance/feasibility · L2 functional · L3 persistence/reset · L4 endurance/performance · L5 fault paths · L6 negative controls · L7 final state for independent verification.

## 3 Test entries

Window rows (bench fact #10: inside one command the archive holds the handler's event first, then that command's `cmdDisp.OpCodeCompleted` and `OpCodeDispatched` rows): from the **last** `framTester.Marker(id=START)` row that precedes the first `framTester.Marker(id=END)` row following it (a START re-sent after a ground-system restart counts from its last sending), through that `Marker(id=END)` row plus the END MARK's own cmdDisp rows (at most two), inclusive. Channel rows are those with ordering time `t` (exporter: `t = epoch·1e6 + fsw`, epoch incremented at each reboot) between the first and last window event; `precedes` compares `t`, archive index on ties. `commands_sent` = rows of `commands.jsonl` from the matching `MARK(START)` sending through the `MARK(END)` sending, inclusive (`sent` is the ground wall clock and is never compared with `fsw`). Standard `complete` block (referenced as "complete: STD" below): both markers present; commands_sent == count(event cmdDisp.OpCodeDispatched); event cmdDisp.OpCodeError count == 0; event cmdDisp.TooManyCommands count == 0; delta(framer.DroppedPackets) == 0; fram samples >= 2. Telemetry bracket (every window): after MARK(START) wait until two Fram packet samples have arrived (≥ 2 s) before the first stimulus; after the last stimulus command's final event (its completion event if it has one, else its `OpCodeCompleted`) wait until two further Fram samples have arrived before MARK(END). `delta(ch)` = `last(ch)` − `first(ch)` over the window rows and requires ≥ 2 samples of `ch` in the window, otherwise the predicate fails; `first/last` = earliest/latest sample inside the window. Standard absence block ("ABS"): event framTester.UnexpectedCompletion count == 0; event framTester.BufferMismatch count == 0; event framTester.RawDone count == 0 (unless the test lists one); event framTester.CommandRejected count == 0.

ID:        L0-1
Title:     Device identity and startup state
Covers:    REQ-FramDriver-012, REQ-FramDriver-015, REQ-FramDriver-017
Setup:     Image flashed, GDS attached, ≥ 2 s after reset (startup reads complete).
Procedure: MARK(10); bracket (2 samples); bracket; MARK(11).
Window:    Marker(id=10) … Marker(id=11)
Pass rule: complete: STD. all_of: ABS; last(framDriver.StartupState) == COMPLETE; last(framDriver.DeviceIdMatch) == true; last(framDriver.StatusRegister) & 0x0C == 0 (BP1/BP0 clear; datasheet 002-26764 Rev.*D Table 5 — bench value to confirm at stage 2); last(framDriver.StatusRegister) & 0xBD == 0x00 (WEL bit 1 may be set; bit 6 is non-writable and always reads ‘1’ per the status-register table of 002-26764 Rev.*D — bench value 0x40 confirmed at stage 2).
Evidence:  Channel strip StartupState / DeviceIdMatch / StatusRegister (boot events are emitted before the GDS attaches and are not in the archive — the standing channels are the evidence).
Fault planted faults: none

ID:        L0-2
Title:     Marker round-trip and command path
Covers:    — (entry gate; harness completeness)
Setup:     As L0-1.
Procedure: MARK(20); bracket (2 samples); MARK(0xA5A5); bracket; MARK(21).
Window:    Marker(id=20) … Marker(id=21)
Pass rule: complete: STD; commands_sent == 3. all_of: event framTester.Marker(id=0xA5A5) count == 1; event framTester.Marker(id=20) precedes event framTester.Marker(id=0xA5A5); event framTester.Marker(id=0xA5A5) precedes event framTester.Marker(id=21).
Evidence:  Three Marker events in order.
Fault planted faults: none

ID:        L2-1
Title:     Write then read back, 64 bytes at offset 0
Covers:    REQ-FramDriver-001, REQ-FramDriver-002, REQ-FramDriver-003, REQ-FramDriver-004, REQ-FramDriver-005, REQ-FramDriver-011
Setup:     L0-1 passed.
Procedure: MARK(30); bracket (2 samples); WRITE_PATTERN(0, 0x000000, 64, 0x1001); READ_VERIFY(0, 0x000000, 64, 0x1001); bracket (2 samples); MARK(31).
Window:    Marker(id=30) … Marker(id=31)
Pass rule: complete: STD. all_of: ABS; event framTester.RequestReturned(op=WRITE, client=0, offset=0, length=64, status=ACCEPTED) count == 1; event framTester.RequestReturned(op=WRITE, client=0, offset=0, length=64, status=ACCEPTED) precedes event framTester.WriteDone(client=0, offset=0, length=64, status=OK); event framTester.WriteDone(client=0, offset=0, length=64, status=OK) count == 1; event framTester.VerifyDone(client=0, offset=0, length=64, status=OK, mismatches=0) count == 1; event framTester.WriteDone(client=0, offset=0, length=64) precedes event framTester.VerifyDone(client=0, offset=0, length=64); delta(framDriver.WritesCompleted) == 1; delta(framDriver.ReadsCompleted) == 1; delta(framDriver.RequestsRejected) == 0; delta(framDriver.SpiErrors) == 0; event framDriver.RequestRejected count == 0; event framDriver.SpiError count == 0.
Evidence:  WriteDone(OK) → VerifyDone(OK, 0 mismatches); counters +1/+1.
Fault planted faults: verify_wrong_seed, corrupt_block

ID:        L2-2
Title:     Last 64 bytes of the array and the one-past boundary
Covers:    REQ-FramDriver-001, REQ-FramDriver-002, REQ-FramDriver-006, REQ-FramDriver-008
Setup:     L2-1 passed.
Procedure: MARK(40); bracket (2 samples); WRITE_PATTERN(0, 0x3FFC0, 64, 0x2002); READ_VERIFY(0, 0x3FFC0, 64, 0x2002); REQUEST_RAW(READ, 0, 0x3FFC1, 64); bracket; MARK(41).
Window:    Marker(id=40) … Marker(id=41)
Pass rule: complete: STD. all_of: ABS; event framTester.WriteDone(client=0, offset=0x3FFC0, length=64, status=OK) count == 1; event framTester.VerifyDone(client=0, offset=0x3FFC0, length=64, status=OK, mismatches=0) count == 1; event framTester.RequestReturned(op=READ, client=0, offset=0x3FFC1, length=64, status=OUT_OF_RANGE) count == 1; event framDriver.RequestRejected(op=READ, client=0, offset=0x3FFC1, length=64, status=OUT_OF_RANGE) count == 1; delta(framDriver.ReadsCompleted) == 1; delta(framDriver.WritesCompleted) == 1; delta(framDriver.RequestsRejected) == 1; event framTester.VerifyDone count == 1.
Evidence:  Last-byte write/verify OK; one OUT_OF_RANGE pair (tester return + UUT event); ReadsCompleted +1 only.
Fault planted faults: corrupt_block

ID:        L2-3
Title:     Completion returns on the requesting port index
Covers:    REQ-FramDriver-004, REQ-FramDriver-011
Setup:     L2-1 passed.
Procedure: MARK(50); bracket (2 samples); WRITE_PATTERN(1, 0x010000, 32, 0x3005); READ_VERIFY(1, 0x010000, 32, 0x3005); READ_VERIFY(0, 0x010000, 32, 0x3005); bracket; MARK(51).
Window:    Marker(id=50) … Marker(id=51)
Pass rule: complete: STD. all_of: ABS; event framTester.WriteDone(client=1, offset=0x010000, length=32, status=OK) count == 1; event framTester.VerifyDone(client=1, offset=0x010000, length=32, status=OK, mismatches=0) count == 1; event framTester.VerifyDone(client=0, offset=0x010000, length=32, status=OK, mismatches=0) count == 1; event framTester.WriteDone(client=0) count == 0; delta(framDriver.ReadsCompleted) == 2; delta(framDriver.WritesCompleted) == 1.
Evidence:  Completions carry client=1 for the client-1 requests and client=0 for the client-0 read of the same data.
Fault planted faults: none

ID:        L2-5
Title:     Full-device write/verify sweep
Covers:    REQ-FramDriver-001, REQ-FramDriver-002, REQ-FramDriver-010
Setup:     L2-1 passed.
Procedure: MARK(60); bracket (2 samples); SWEEP(0x5005, 4096, 0x5005) (chunks chained from inside completions, §0); wait for SweepDone(VERIFY) (≤ 1200 s); bracket; MARK(61).
Window:    Marker(id=60) … Marker(id=61)
Pass rule: complete: STD. all_of: ABS; event framTester.SweepDone(kind=WRITE, chunks=4096, errors=0) count == 1; event framTester.SweepDone(kind=VERIFY, chunks=4096, errors=0) count == 1; event framTester.SweepDone(kind=WRITE) precedes event framTester.SweepDone(kind=VERIFY); delta(framDriver.WritesCompleted) == 4096; delta(framDriver.ReadsCompleted) == 4096; delta(framDriver.SpiErrors) == 0; delta(framDriver.RequestsRejected) == 0. metrics: delta(framTester.BytesVerified) == 262144; t(SweepDone VERIFY) − t(SWEEP sent) ≤ 1200 s, both on the FSW clock: t(SWEEP sent) is the SWEEP's own `cmdDisp.OpCodeDispatched` row (the ground sending precedes it by the uplink latency, < 1 s); the checker records the elapsed value in its notes.
Evidence:  Two SweepDone events with chunks=4096 errors=0; counters +4096/+4096.
Fault planted faults: verify_wrong_seed, short_sweep

ID:        L3-1
Title:     Retention across MCU reset
Covers:    REQ-FramDriver-023 (non-volatility — one-off, verification method Inspection; datasheet 002-26764 Rev.*D §Features), REQ-FramDriver-012, REQ-FramDriver-015, REQ-FramDriver-016 (startup events captured in R — the GDS is attached at this reset)
Setup:     L2-1 passed. GDS events listener attached before the reset.
Procedure: MARK(70); bracket (2 samples); WRITE_PATTERN(0, 0x020000, 64, 0x7007); tlm.SEND_PKT(Health); bracket; MARK(71); wait until Marker(id=71) and its OpCodeCompleted are in the archive, then 1 s more (framer flushes at the 1 Hz tick); OpenOCD `reset run` (no power cycle: bench constraint); wait for last(framDriver.StartupState) == COMPLETE (≤ 10 s); MARK(72); bracket (2 samples); tlm.SEND_PKT(Health); READ_VERIFY(0, 0x020000, 64, 0x7007); bracket; MARK(73).
Window:    W1 = Marker(id=70) … Marker(id=71); R = Marker(id=71) … Marker(id=72) (reset window, no commands_sent check — the OpenOCD action is not a command); W2 = Marker(id=72) … Marker(id=73)
Pass rule: complete (W1, W2): STD; (R): both markers present; R is split at the reboot — the exporter's epoch boundary (FSW time falls back by > 1 s), not at any UUT event; boot-1 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot1 == last(framer.DroppedPackets) in W1 (no drop after W1 closed); boot-2 segment (≥ 1 sample): last(framer.DroppedPackets) in R-boot2 == 0 (boot-zero counter; a dropped startup event would count here). all_of: W1: ABS; event framTester.WriteDone(client=0, offset=0x020000, length=64, status=OK) count == 1. R: event framDriver.StartupComplete count == 1; event framDriver.DeviceIdMismatch count == 0; event framDriver.BlockProtectSet count == 0; event framDriver.StartupReadFailed count == 0; last(framDriver.StartupState) == COMPLETE. W2: ABS; event framTester.VerifyDone(client=0, offset=0x020000, length=64, status=OK, mismatches=0) count == 1; first(framDriver.WritesCompleted) == 0 (boot-zero counter → the MCU rebooted); last(rg8Hz.CycleCount) in W2 < last(rg8Hz.CycleCount) in W1 (Health packet id 1 polled by the two `tlm.SEND_PKT(Health)` commands in the procedure; UUT-independent reboot evidence).
Evidence:  WriteDone before the reset; WritesCompleted back to 0 then VerifyDone(mismatches=0) after it.
Fault planted faults: corrupt_block
Risk:      bench note — after an OpenOCD reset with the GDS attached, command dispatch has been seen to stall (uplink artifact); if reproduced in the dry run the harness restarts the GDS between MARK(71) and MARK(72); the run's `raw/` then holds two GDS log directories, the exporter merges them in `recv` order, `export-counts.json` reports both, and the restart time is recorded in §8.

ID:        L5-1
Title:     Second request while a transfer is in progress is rejected BUSY
Covers:    REQ-FramDriver-007, REQ-FramDriver-008
Setup:     L2-1 passed.
Procedure: MARK(80); bracket (2 samples); BURST(1, 0x030000, 64, 0x8008, BACK_TO_BACK); bracket; MARK(81).
Window:    Marker(id=80) … Marker(id=81)
Pass rule: complete: STD. all_of: ABS; event framTester.RequestReturned(op=READ, client=1, offset=0x030000, length=64, status=BUSY) count == 1; event framDriver.RequestRejected(op=READ, client=1, offset=0x030000, length=64, status=BUSY) count == 1; event framTester.VerifyDone(client=1, offset=0x030000, length=64, status=OK) count == 1 (`mismatches` unconstrained: the block was never written with seed 0x8008); delta(framDriver.ReadsCompleted) == 1; delta(framDriver.RequestsRejected) == 1; delta(framDriver.WritesCompleted) == 0.
Evidence:  One BUSY return + one UUT BUSY event; exactly one completion (the first read).
Fault planted faults: busy_skip

ID:        L5-2
Title:     Zero-length and over-size requests are rejected without completion
Covers:    REQ-FramDriver-006, REQ-FramDriver-008, REQ-FramDriver-016
Setup:     L2-1 passed.
Procedure: MARK(90); bracket (2 samples); REQUEST_RAW(READ, 0, 0x000100, 0); REQUEST_RAW(WRITE, 0, 0x000100, 65); bracket; MARK(91).
Window:    Marker(id=90) … Marker(id=91)
Pass rule: complete: STD. all_of: ABS; event framTester.RequestReturned(op=READ, client=0, offset=0x100, length=0, status=OUT_OF_RANGE) count == 1; event framTester.RequestReturned(op=WRITE, client=0, offset=0x100, length=65, status=OUT_OF_RANGE) count == 1; event framDriver.RequestRejected(status=OUT_OF_RANGE) count == 2; delta(framDriver.RequestsRejected) == 2; delta(framDriver.ReadsCompleted) == 0; delta(framDriver.WritesCompleted) == 0; event framTester.WriteDone count == 0; event framTester.VerifyDone count == 0.
Evidence:  Two OUT_OF_RANGE pairs, no RawDone; no completion; counters +2 rejected, +0 completed.
Fault planted faults: none

ID:        L5-3
Title:     Request issued from inside a completion is accepted
Covers:    REQ-FramDriver-010
Setup:     L2-1 passed.
Procedure: MARK(100); bracket (2 samples); CHAIN(0, 0x034000, 64, 0xA00A); bracket; MARK(101).
Window:    Marker(id=100) … Marker(id=101)
Pass rule: complete: STD. all_of: ABS; event framTester.WriteDone(client=0, offset=0x034000, length=64, status=OK) count == 1; event framTester.VerifyDone(client=0, offset=0x034000, length=64, status=OK, mismatches=0) count == 1; event framDriver.RequestRejected count == 0; delta(framDriver.RequestsRejected) == 0; delta(framDriver.WritesCompleted) == 1; delta(framDriver.ReadsCompleted) == 1.
Evidence:  Write completion immediately followed by the chained read's completion, no BUSY.
Fault planted faults: none

ID:        L6-1
Title:     Negative control — the verifier detects a wrong pattern
Covers:    — (checker validity)
Setup:     L2-5 passed (block 0x035000 holds seed 0x5005 from the sweep; L6-1 rewrites it).
Procedure: MARK(110); bracket (2 samples); WRITE_PATTERN(0, 0x035000, 64, 0xB00B); READ_VERIFY(0, 0x035000, 64, 0xB00C); bracket; MARK(111).
Window:    Marker(id=110) … Marker(id=111)
Pass rule: complete: STD. all_of: ABS; event framTester.VerifyDone(client=0, offset=0x035000, length=64, status=OK, mismatches=64) count == 1 (xorshift32 differs in every byte for adjacent seeds over 64 bytes — generator property, checked offline and recorded in §8); delta(framDriver.ReadsCompleted) == 1.
Evidence:  VerifyDone with mismatches=64.
Fault planted faults: none

ID:        L7-1
Title:     Final state for independent verification
Covers:    REQ-FramDriver-001, REQ-FramDriver-002 (independent read of what the run left in the device)
Setup:     All other tests done.
Procedure: MARK(120); bracket (2 samples); WRITE_PATTERN(0, 0x3FF00, 64, 0xF1F1); WRITE_PATTERN(0, 0x3FF40, 64, 0xF1F1); WRITE_PATTERN(0, 0x3FF80, 64, 0xF1F1); WRITE_PATTERN(0, 0x3FFC0, 64, 0xF1F1); DUMP(0x3FF00 + 8k, 8) for k = 0..31, DUMP(k+1) sent only after DumpData(offset=0x3FF00+8k) is in the archive (≥ 50 ms apart); bracket; MARK(121).
Window:    Marker(id=120) … Marker(id=121)
Pass rule: complete: STD. all_of: ABS; event framTester.WriteDone(status=OK) count == 4; event framTester.DumpData count == 32; for every k: event framTester.DumpData(offset=0x3FF00+8k, length=8, data0, data1) bytes == xorshift32 pattern(seed 0xF1F1, offset 0x3FF00+8k…+7) — computed by the auditor from the §0 generator, not from any harness output; delta(framDriver.SpiErrors) == 0; delta(framDriver.RequestsRejected) == 0; event framDriver.RequestRejected count == 0.
Evidence:  32 DumpData events whose 256 bytes equal the auditor's regenerated image.
Fault planted faults: final_wrong_image (uut-state)

## 4 Traceability
| REQ | Tests | Note |
|---|---|---|
| 001 | L2-1, L2-5 | |
| 002 | L2-1, L2-5 | |
| 003 | L2-1 (read-back equality implies the datasheet command sequence) + UT | |
| 004 | L2-1, L2-3 | |
| 005 | L2-1 (RequestReturned(ACCEPTED) precedes WriteDone) + UT | |
| 006 | L2-2, L5-2 | |
| 007 | L5-1 | |
| 008 | L2-2, L5-1, L5-2 | |
| 009 | UT only — no SPI fault injection on this bench | waiver W2 |
| 010 | L5-3, L2-5 | |
| 011 | L2-1, L2-3 | |
| 012 | L0-1, L3-1 (reset window R) | |
| 013 | UT only — bench device matches; mismatch path not plantable | waiver W2 |
| 014 | UT only | waiver W2 |
| 015 | L0-1, L3-1 (reset window R) | |
| 016 | L5-2 (RequestRejected), L3-1 R (StartupComplete; absence of the startup warnings); SpiError by UT | partial, waiver W2 |
| 017 | L0-1, L2-1 (all seven channels observed) | |
| 018 | Inspection (entry gate) | |
| 019 | Inspection (entry gate: library build without fprime-samd) | |
| 020 | L2-1, L2-2, L2-5, L3-1, L7-1 | |
| 021 | UT only | waiver W2 |
| 022 | UT only — block protect cannot be set (no WRSR path by design) | waiver W2 |

Waivers: W2 — SPI_CONFIG_ERR / other SPI-driver fault paths (009, 014, 021 non-BUSY case), device-ID mismatch (013) and block protect (022) need a bus fault, a second device or a protected part; none available on this bench; unit tests are the verification. 021's SPI_BUSY_ERR path could be planted by driving a second SPI slot from the harness (Samd21.SpiDriver refuses while another slot is in flight) — not attempted in this plan because the bench has no second chip-select target and the SpiDriver's tolerance of a target-less slot is unverified; offered to the human (§8 #6).

## 5 Metrics
- Sweep (L2-5): `BytesVerified` delta == 262144 bytes exactly; time ≤ 1200 s on the FSW clock (#11; the 120 s design estimate ignored the deployment's one-completion-per-`activeIn`-call cadence of ≈ 9 writes/s; measured ≈ 458 s).
- Framer burst budget: ≤ 250 B framed per command (§1 table); zero `framer.DroppedPackets` in every window.
- Startup: `StartupState == COMPLETE` within 10 s of reset (L3-1).

## 6 Fault menu
See `faults-menu.json` (same directory). max_count 2; planted faults: verify_wrong_seed (harness-expectation), corrupt_block (uut-state), final_wrong_image (uut-state), short_sweep (stimulus), busy_skip (stimulus); 6 tests covered.

## 7 Not covered
- SPI-driver faults (REQ-009/014, REQ-021 non-BUSY cases), device-ID mismatch (REQ-013), block protect (REQ-022): no plantable mechanism on this bench; unit tests only (W2). REQ-021 SPI_BUSY_ERR: plantable in principle, not attempted (§8 #6).
- Image identity via `Svc.Version` (`version.VERSION ALL`): the deployment has no `Svc.Version` instance → stage-2 finding; provenance comes from the manifest SHAs + OpenOCD flash read-back log.
- Boot-time events (DeviceIdMismatch, BlockProtectSet, StartupReadFailed, StartupComplete) are emitted before the GDS attaches on the **first** boot; L0-1 reads the standing channels instead (that is why they exist); the L3-1 reset window R captures them for the second boot.
- Power-cycle retention: no scripted power control (shared hub); L3-1 uses an MCU reset, the F-RAM stays powered.
- No evidence path independent of the UUT's SPI read exists on this bench: the F-RAM is reachable only through the SAMD21. L7-1's dump travels through `framDriver.read`; its independence rests on the generator/seed being the auditor's and on L6-1 proving the verifier can fail. Human to confirm (§8 #4) or choose a strengthening: (i) a raw 9-byte `DeviceId` channel compared against the datasheet constant (reopens design: Table 3), (ii) a Pi-side read of the device with the MCU in `reset halt`, if the SPI lines are exposed.
- Endurance (L4): none planned — the F-RAM has 10^14 cycles endurance (datasheet); a sweep is the performance test.

## 8 Decision log
1. 2026-10-29 designer — deployment = CuriosityReference minus I2C (human's stated reduction) plus the additions of §0's delta table (designer's; gate confirms, #7). It is a test deployment in the contract's sense.
2. 2026-10-29 designer — marker = `framTester.MARK(U32)` → `Marker(id)` event (no CMD_NO_OP_STRING, string max 2 chars); needs line (4) to the human.
3. 2026-10-29 designer — L3 reset via OpenOCD, not power cycle (needs line (2)).
4. 2026-10-29 designer — final-state check = PRNG image at 0x3FF00..0x3FFFF, auditor regenerates from the §0 generator + seed 0xF1F1 (needs line (6)); the dump travels through the UUT read path — **human to confirm** this acceptance at the gate (see §7).
5. 2026-10-29 designer — generator property checked offline for every (seed, offset) pair in the plan with the §0 definition: L6-1 seeds 0xB00B/0xB00C at 0x035000 differ in 64/64 bytes; test vectors in §0.
6. 2026-10-29 designer — REQ-021 SPI_BUSY_ERR bench path via a second SPI slot: not attempted (no second CS target; SpiDriver tolerance unverified) — human may order an L5-4.
8. 2026-10-29 designer — framer burst mitigation (a): test-build `FramerConfig::FRAMER_TX_BUFFER_SIZE = 256`; every command fits one buffer; drops still read UNVERIFIED via STD.
7. 2026-10-29 designer — the deployment additions (spiDriver, framDriver, framTester, Fram packet + 1 Hz pktSend, cycler connection, DmaChannel entries, NUM_TLM_PACKETS) are the designer's and go to the human gate for confirmation.
8. 2026-10-29 human gate (Michael Starch) — (a) no DMA wording: the requirement is the asynchronous SPI interface only; (b) EXPECTED_DEVICE_ID is a deployment configuration item (family-wide), default = bench -50SXI reply; (c) BP0/BP1 WARNING_HI with meaning text; (d) REQ-019 retired to a design constraint, REQ-020 retired to test design (this plan); (e) retention across reset is REQ-023, verification Inspection as a one-off (L3-1); (f) SDD Configure as snippets, Behavior shortened. Traceability updated accordingly; otherwise approved "good job".
9. 2026-10-30 builder (stage 2) — harness configuration for the `short_sweep` and `busy_skip` planted faults needed command arguments the harness did not expose: `SWEEP(seed, chunks: U16, verifySeed: U32)` (plan: 4096, verifySeed = seed) and `BURST(client, offset, length, seed, mode: BurstMode)` (plan: BACK_TO_BACK; SEQUENTIAL issues the second read from the first completion). FramTester is harness-owned; the UUT is untouched. Also found at stage 2: FPP `match` forbids one instance on two indices of a matched port array, so the deployment reaches `framDriver.read/write[k]` through two pass-through `CuriosityReference.FramClient` instances (`framClient0/1`); event names in the record are unchanged. Mode B reviewer to confirm both.
10. 2026-10-30 builder (stage 2) — L0-1 status-register mask: bench reads 0x40; datasheet 002-26764 Rev.*D status-register table: bit 6 non-writable, always ‘1’ → predicate mask 0xFD → 0xBD (the plan had flagged this value for stage-2 confirmation). Window slicing (bench fact): inside one command the archive holds the handler's event (Marker) first, then that command's `cmdDisp.OpCodeCompleted` and `OpCodeDispatched` rows with sub-millisecond later timestamps (reverse of logical order). Window rows therefore run from the `Marker(START)` row through the `Marker(END)` row plus the END MARK's own two cmdDisp rows; `precedes` compares timestamps, archive index on ties.
11. 2026-10-30 builder (stage 2) — L2-5 time budget: dry/clean measured `framDriver.WritesCompleted` advancing 9 per second during the sweep (one 64-byte write = WREN + WRITE frames, each completion delivered on a later `spiDriver.activeIn` call — deployment cadence, UUT-independent), so 2 × 4096 chunks need ≈ 15 min; budget 120 s → 1200 s, harness wait 1200 s. The 120 s figure was a design-time estimate; the sweep is otherwise unchanged (full device, one request outstanding).
12. 2026-10-30 builder (stage 2) — dry/clean found L5-3 (CHAIN at 0x040000) and L6-1 (0x050000) addressed past the array: 0x040000 = FRAM_SIZE, so the UUT rejected them OUT_OF_RANGE (correct behaviour; a plan defect). Offsets moved inside the array and away from every other test's block: L5-3 → 0x034000, L6-1 → 0x035000. Pass rules unchanged otherwise.
13. 2026-10-30 builder (stage 2) — dry runs 1–2 (bench facts): (a) `framDriver.RequestRejected` is throttled at 5 per boot, so a second full-plan run on the same boot loses the event (L2-2); every run therefore starts from an OpenOCD `reset run` before the ground system is started. (b) FSW time restarts at 0 on reboot: the exporter adds an ordering time `t = epoch·1e6 + recv` (epoch increments when recv falls back by > 1 s) and both checkers slice windows by `t`, never by raw `recv`. Post-reboot rows can be byte-identical to pre-reboot rows (same FSW time and text), so the exporter never de-duplicates rows globally; the archive uses a single recorder (`fprime-cli events`, which logs channels and events) so no row is recorded twice. (c) The uplink stall after an OpenOCD reset with the GDS attached reproduced in dry run 2: the harness now restarts the GDS unconditionally after `StartupComplete` is in the archive and before MARK(72); the archive then holds two recorder directories merged in archive order. (d) When a START marker had to be re-sent, `commands_sent` counts from its last sending. Dry run 2 verified L3-1 retention on the bench (VerifyDone mismatches 0 after reset, WritesCompleted boot-zero, rg8Hz.CycleCount lower) and L7-1 (dump image == xorshift32(0xF1F1)).
14. 2026-10-30 builder (stage 2, Mode B round 1 → harness pass 2) — reviewer findings applied: predicate keys in both checkers are this plan's text verbatim; L2-5 time metric evaluated (clock defined above); verdict files carry `run`, `record_sha256`, `evidence`; run-start markers via reserved `MARK` ids (§1); exporter de-duplicates the recorder's repeated rows exactly (set per boot segment per recorder file), reads commands only from the sender's own log directory (`record/raw/cmd/`), reports raw archive line totals, names the FSW clock `fsw`, pairs each command with its dispatcher response; runner asserts exactly one GDS and one recorder and flashes the image (read-back verified, `record/raw/flash.log`) at run start; window START occurrence, END rows and the L3-1 reboot split defined as above; `framTester.CommandRejected` added to §0/ABS; archive layout = `<run>/record/raw/`. Known record artifact: the recorder stopped at the L3-1 ground-system restart may have logged its last 1 Hz packet partially (rows are written one channel at a time), so one boot-2 Fram packet in R can carry fewer than its full channel set; R's predicates use `last()` values only and are unaffected. Plan changes since the gate (#11 budget, #12 offsets, #14) are listed for the human with the delivery.
15. 2026-10-30 builder (stage 2, Mode B round 2 → pass 2b) — run ids are neutral (`dry-1`…`dry-6`; the planted fault → directory mapping lives only in the orchestration script) after the builder's own grep found `dry-<planted fault>` ids in `runner/`, `verdicts.json` and, via `MARK(crc32(run id))`, invertibly in the record. Also applied: `precedes` tie-break by archive index only (builder checker), `delta` ≥ 2 samples wording (above), marker-collision note (§1), exporter lists torn packets (< 12 rows) in `export-counts.json`, runner stops the recorder right after a Fram packet completes before the L3-1 ground-system restart (takes effect in the next campaign; pass-2b records predate it). Flash step for stage 3 (manifest `.bin`, `dump_image` read-back + sha256 in `flash.log`) is owed before any run-for-record.
16. 2026-10-30 human gate (Michael Starch) — plan changes #11–#15 acknowledged and approved as one diff; REQ-023 verification method stays Inspection; test build + harness to be opened as a draft PR on fprime-community/fprime-samd-reference (not for merge).
17. 2026-11-02 runner (stage 3) — run `fram-r1` (manifest 6a91b929, drawn by the human) VOID at run start: `faults.py` rejected the draw's `sealed/faults.json` shape (`{"manifest_sha256", "faults": {test: planted fault}}`, written by `seal.py draw`) with a silent exit 2 — the dry runs had only ever fed it `{"planted faults": [...]}`; nothing was flashed or recorded and `sealed/` was never opened. Fix: `faults.py` reads `faults.values()` (legacy `planted faults` list kept for the orchestration script) and a malformed file fails loudly (stderr without planted fault names, exit 2). Proven by one unsealed smoke run with `{"faults": {}}` and a scratch local draw fed to the parser. Harness re-committed, manifest re-frozen (new sha8); the human re-draws into `fram-r2/`; `fram-r1/` is left in place, bound to the superseded manifest.
