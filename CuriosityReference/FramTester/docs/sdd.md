# CuriosityReference::FramTester

Test harness for `Fram.FramDriver` (system test plan QUAL-FRAM §0).

## Introduction

`FramTester` is the flight-side half of the FramDriver system-test harness. It
drives `framDriver.read` / `framDriver.write` on two client indices from ground
commands and reports every return value and every completion as an event, so
the GDS archive carries what the unit under test (UUT) did. It owns the request
buffers, checks each completion against the buffer it was issued with, and
compares read-back bytes with the plan's normative xorshift32 pattern. It models
nothing and verifies nothing else: the verdicts come from the generic tooling
(`fth evaluate` / `fth audit`) replaying the record against `test/int/plan.json`.

Like every component in this deployment it is passive, holds no dynamic memory
and runs entirely in main context (command handlers, completion ports called
from `spiDriver.activeIn`, and one rate-group tick).

## Design

### Ports

| Port | Kind | Type | Purpose |
|---|---|---|---|
| `read[2]`, `write[2]` | output | `Fram.Request` | Requests to the UUT, index = client. Reach `framDriver.read/write[k]` through the pass-through `framClient<k>` instances (FPP `match` rule). |
| `readComplete[2]`, `writeComplete[2]` | sync input | `Fram.Complete` | Completions, index = client the request was issued on. |
| `schedIn` | sync input | `Svc.Sched` | Rate-group tick (`rg1Hz`, index 0). Rewrites `McuSerial0..3`. |
| `timeCaller` | time get | — | Timestamps. |

`Fw.Command`, `Fw.Event` and `Fw.Channel` are imported.

### Commands

| Opcode | Command | Behavior |
|---|---|---|
| 0 | `MARK(id)` | Echoes `Marker(id)`; brackets a test window in the archive. |
| 1 | `WRITE_PATTERN(client, offset, length, seed)` | Writes `length` (1..64) pattern bytes; completion → `WriteDone`. |
| 2 | `READ_VERIFY(client, offset, length, seed)` | Reads and counts mismatching bytes; completion → `VerifyDone`. |
| 3 | `REQUEST_RAW(op, client, offset, length)` | Any buffer size (incl. 0 and 65) straight to the driver; completion → `RawDone`. |
| 4 | `BURST(client, offset, length, seed, mode)` | Two reads: `BACK_TO_BACK` issues the second before any completion (driver expected to answer BUSY), `SEQUENTIAL` from inside the first completion. |
| 5 | `CHAIN(client, offset, length, seed)` | Write whose verifying read is issued from inside the write completion. |
| 6 | `SWEEP(seed, chunks, verifySeed)` | Write then verify `chunks` × 64 bytes on client 0, each chunk from the previous completion; `SweepDone` per pass. |
| 7 | `DUMP(offset, length)` | Reads 1..8 bytes on client 0 into `DumpData` (auditor's final-state check). |

A command is rejected (`CommandRejected`, `VALIDATION_ERROR` / `EXECUTION_ERROR`)
when an argument is out of range or a request is already outstanding on that
client. The command response is sent when the request call has returned; the
completion is always reported by an event.

### Events

| Id | Event | Severity | Meaning |
|---|---|---|---|
| 0 | `Marker(id)` | activity high | `MARK` echo. |
| 1 | `RequestReturned(op, client, offset, length, status)` | activity low | Return value of a request call (not emitted for SWEEP chunks). |
| 2 | `WriteDone(client, offset, length, status)` | activity low | WRITE_PATTERN / CHAIN write completion. |
| 3 | `VerifyDone(client, offset, length, status, mismatches)` | activity low | READ_VERIFY / BURST / CHAIN read completion with the pattern comparison. |
| 4 | `RawDone(op, client, offset, length, status)` | activity low | REQUEST_RAW completion. |
| 5 | `UnexpectedCompletion(client, status, cause)` | warning high | Completion that cannot be matched to the request outstanding on that index: `cause` = NO_REQUEST (nothing outstanding) or WRONG_PORT (a completion of the other kind — the UUT completed on the wrong port). |
| 6 | `BufferMismatch(client)` | warning high | Completion buffer pointer/size differs from the request's. |
| 7 | `SweepDone(kind, chunks, errors)` | activity high | A SWEEP pass finished or stopped on a rejection. |
| 8 | `DumpData(offset, length, data0, data1)` | activity low | DUMP bytes, MSB-first packed, zero-padded. |
| 9 | `CommandRejected(opcode)` | warning low | Argument out of range or client busy. |

### Telemetry

| Id | Channel | Type | Meaning |
|---|---|---|---|
| 0 | `BytesWritten` | `U32` | Bytes of OK write completions. |
| 1 | `BytesVerified` | `U32` | Bytes compared by READ_VERIFY / BURST / CHAIN / SWEEP-verify (any outcome). |
| 2 | `Mismatches` | `U32` | Cumulative mismatching bytes. |
| 3 | `RejectionsSeen` | `U32` | Request calls that returned other than ACCEPTED. |
| 4..7 | `McuSerial0..3` | `U32` | The SAMD21 128-bit unique serial number, words 0..3, read from `0x0080A00C`, `0x0080A040`, `0x0080A044`, `0x0080A048` (data sheet §10.3.3) through a `volatile const U32*` on every `schedIn` tick. Constant per board; they name the board a Fram packet came from. |

`McuSerial0..3` are the *harness's* provenance. The F-RAM's own identity is the
UUT's to report and is already in-band: `framDriver.DeviceIdMatch` (channel, in
the Fram packet) and `framDriver.DeviceIdMismatch(deviceId)` (event, the 9 RDID
bytes in wire order). The harness does not read RDID itself.

### State

Per client: `m_activity` (what the index is doing), `m_buffer` (the buffer
handed to the driver), `m_seed`, `m_data[64]`; `m_raw[72]` for REQUEST_RAW; the
SWEEP cursor on client 0; the four counters behind channels 0..3. The serial
words are not stored — they are re-read from the NVM on each tick.

## Topology Integration

`CuriosityReference/Top/instances.fpp`:

```fpp
instance framTester: CuriosityReference.FramTester base id 0xCC00
```

`CuriosityReference/Top/topology.fpp` — the tick comes first on `rg1Hz`, the
Fram packet is sent on the next index, so every packet carries the serial words
from the same tick:

```fpp
rg1Hz.RateGroupMemberOut[0] -> framTester.schedIn
rg1Hz.RateGroupMemberOut[1] -> tlm.pktSendIn[2]
```

Packet `Fram` (id 2) lists the twelve UUT/harness entries first and
`framTester.McuSerial0..3` last, so the offsets of the original entries are
unchanged. Channel payload 55 bytes; serialized packet (descriptor 1 + id 2 +
time 11) 69 bytes; framed (F´ header 8 + CRC 4) 81 bytes — against
`FRAMER_TX_BUFFER_SIZE = 256`.

There are no native unit tests for this component yet (the MCU serial-number read
in `FramTester.cpp` is unguarded for a host build — review iter-4 C11/D4, future-work);
it is verified on hardware by the system-test plan it serves and by the plan's knob
vectors, which exercise its error paths.

## Build record

- **2026-11-02** — `fram-harness` build with `McuSerial0..3` added (this change).
  Submodule pins: `lib/fprime` 7166cc78a2704354379db0856922462298183067
  (`lestarch-autobot/fprime`, `samd-reference-kronos3-upstream-devel`), `lib/fprime-baremetal`
  f0ab6dec81eb0cf2dc85c5f3af2ca5bead2146b8, `lib/fprime-samd` 056d4d39a965c9612e83e3096a0cf5bd692b37f5,
  `lib/fprime-devices` d994623e6f677bcaf36770e451744f4abd12c623 (UUT, frozen). Toolchain: adafruit:samd 1.7.17
  (arm-none-eabi-gcc 9-2019q4, CMSIS 5.4.0, CMSIS-Atmel 1.2.2) / fprime-tools 4.4.0 / fprime-fpp 3.4.0 /
  cmake 3.26.0, python 3.12. Baseline (85f0fa4, same pins) reproduced byte-for-byte:
  `CuriosityReference.elf.bin` sha256 af54922105553e201d9ff9763322c4886ccb8c06cf9c1c79d8f1a62029e128df,
  text 52068 / data 172 / bss 4548. With McuSerial0..3:
  sha256 6736537d0bdd0d0c19268b49d15af70bc9068b114ee80fca0fd3884c3e6b8ba7, text 52544 / data 172 / bss 4564
  (+476 B flash, +16 B RAM); Fram packet payload 39 → 55 bytes.
