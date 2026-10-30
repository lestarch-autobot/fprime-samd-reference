// ======================================================================
// \title  FramTester.cpp
// \brief  Test harness for Fram.FramDriver on CuriosityReference (system test plan QUAL-FRAM §0)
// ======================================================================

#include "CuriosityReference/FramTester/FramTester.hpp"

#include <cstring>

#include "Fw/Types/Assert.hpp"

namespace CuriosityReference {

constexpr FwIndexType FramTester::CLIENTS;
constexpr FwSizeType FramTester::MAX_TRANSFER;
constexpr FwSizeType FramTester::MAX_RAW;
constexpr FwSizeType FramTester::MAX_DUMP;
constexpr U32 FramTester::FRAM_SIZE;
constexpr U16 FramTester::SWEEP_CHUNKS;

FramTester ::FramTester(const char* const compName)
    : FramTesterComponentBase(compName),
      m_activity(),
      m_buffer(),
      m_seed(),
      m_data(),
      m_raw(),
      m_sweepKind(FramTester_SweepKind::WRITE),
      m_sweepChunk(0),
      m_sweepChunks(SWEEP_CHUNKS),
      m_sweepVerifySeed(0),
      m_burstSecond(),
      m_sweepDone(0),
      m_sweepErrors(0),
      m_sweepSeed(0),
      m_bytesWritten(0),
      m_bytesVerified(0),
      m_mismatches(0),
      m_rejectionsSeen(0) {
    for (FwIndexType i = 0; i < CLIENTS; i++) {
        this->m_activity[i] = FramTester_Activity::NONE;
    }
}

FramTester ::~FramTester() {}

U8 FramTester ::patternByte(U32 seed, U32 offset, U32 i) {
    U32 v = seed ^ (offset + i);
    v ^= v << 13;
    v ^= v >> 17;
    v ^= v << 5;
    return static_cast<U8>(v & 0xFFu);
}

// ----------------------------------------------------------------------
// Completions
// ----------------------------------------------------------------------

bool FramTester ::checkCompletion(FwIndexType portNum, Fw::Buffer& buffer, const Fram::Status& status) {
    FW_ASSERT((portNum >= 0) && (portNum < CLIENTS), static_cast<FwAssertArgType>(portNum));
    if (this->m_activity[portNum] == FramTester_Activity::NONE) {
        this->log_WARNING_HI_UnexpectedCompletion(static_cast<U8>(portNum), status);
        return false;
    }
    if ((buffer.getData() != this->m_buffer[portNum].getData()) ||
        (buffer.getSize() != this->m_buffer[portNum].getSize())) {
        this->log_WARNING_HI_BufferMismatch(static_cast<U8>(portNum));
    }
    return true;
}

void FramTester ::readComplete_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) {
    if (!this->checkCompletion(portNum, buffer, status)) {
        return;
    }
    const FramTester_Activity activity = this->m_activity[portNum];
    const U8 client = static_cast<U8>(portNum);
    const U8 length = static_cast<U8>(buffer.getSize());
    this->m_activity[portNum] = FramTester_Activity::NONE;  // idle before any re-entrant request

    switch (activity.e) {
        case FramTester_Activity::READ_VERIFY:
        case FramTester_Activity::CHAIN_READ: {
            U8 mismatches = 0;
            if (status == Fram::Status::OK) {
                mismatches = this->countMismatches(portNum, offset, buffer.getSize());
            }
            this->m_bytesVerified += buffer.getSize();
            this->m_mismatches += mismatches;
            this->tlmWrite_BytesVerified(this->m_bytesVerified);
            this->tlmWrite_Mismatches(this->m_mismatches);
            this->log_ACTIVITY_LO_VerifyDone(client, offset, length, status, mismatches);
            if (this->m_burstSecond[portNum]) {
                // SEQUENTIAL BURST: second read of the same block, issued after the first completed
                this->m_burstSecond[portNum] = false;
                (void)this->issueRead(client, offset, length, this->m_seed[portNum], FramTester_Activity::READ_VERIFY);
            }
            break;
        }
        case FramTester_Activity::RAW:
            this->log_ACTIVITY_LO_RawDone(Fram::Op::READ, client, offset, length, status);
            break;
        case FramTester_Activity::DUMP: {
            U32 words[2] = {0, 0};
            for (FwSizeType i = 0; (i < buffer.getSize()) && (i < MAX_DUMP); i++) {
                words[i / 4] |= static_cast<U32>(this->m_data[0][i]) << (8 * (3 - (i % 4)));
            }
            if (status == Fram::Status::OK) {
                this->log_ACTIVITY_LO_DumpData(offset, length, words[0], words[1]);
            } else {
                this->log_ACTIVITY_LO_RawDone(Fram::Op::READ, client, offset, length, status);
            }
            break;
        }
        case FramTester_Activity::SWEEP_VERIFY: {
            U8 mismatches = 0;
            if (status == Fram::Status::OK) {
                mismatches = this->countMismatches(0, offset, buffer.getSize());
            }
            this->m_bytesVerified += buffer.getSize();
            this->m_mismatches += mismatches;
            if ((status != Fram::Status::OK) || (mismatches != 0)) {
                this->m_sweepErrors++;
            }
            this->m_sweepDone++;
            this->sweepNext();
            break;
        }
        default:
            this->log_WARNING_HI_UnexpectedCompletion(client, status);
            break;
    }
}

void FramTester ::writeComplete_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) {
    if (!this->checkCompletion(portNum, buffer, status)) {
        return;
    }
    const FramTester_Activity activity = this->m_activity[portNum];
    const U8 client = static_cast<U8>(portNum);
    const U8 length = static_cast<U8>(buffer.getSize());
    this->m_activity[portNum] = FramTester_Activity::NONE;
    if (status == Fram::Status::OK) {
        this->m_bytesWritten += buffer.getSize();
        this->tlmWrite_BytesWritten(this->m_bytesWritten);
    }

    switch (activity.e) {
        case FramTester_Activity::WRITE_PATTERN:
            this->log_ACTIVITY_LO_WriteDone(client, offset, length, status);
            break;
        case FramTester_Activity::CHAIN_WRITE:
            this->log_ACTIVITY_LO_WriteDone(client, offset, length, status);
            // The verifying read is issued from inside this completion (REQ-010 path)
            (void)this->issueRead(portNum, offset, buffer.getSize(), this->m_seed[portNum],
                                  FramTester_Activity::CHAIN_READ);
            break;
        case FramTester_Activity::RAW:
            this->log_ACTIVITY_LO_RawDone(Fram::Op::WRITE, client, offset, length, status);
            break;
        case FramTester_Activity::SWEEP_WRITE:
            if (status != Fram::Status::OK) {
                this->m_sweepErrors++;
            }
            this->m_sweepDone++;
            this->sweepNext();
            break;
        default:
            this->log_WARNING_HI_UnexpectedCompletion(client, status);
            break;
    }
}

// ----------------------------------------------------------------------
// Commands
// ----------------------------------------------------------------------

void FramTester ::MARK_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 id) {
    this->log_ACTIVITY_HI_Marker(id);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

bool FramTester ::acceptPatternCommand(FwOpcodeType opCode, U32 cmdSeq, U8 client, U8 length) {
    if ((client >= CLIENTS) || (length == 0) || (length > MAX_TRANSFER)) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return false;
    }
    if (this->m_activity[client] != FramTester_Activity::NONE) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
        return false;
    }
    return true;
}

void FramTester ::WRITE_PATTERN_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed) {
    if (!this->acceptPatternCommand(opCode, cmdSeq, client, length)) {
        return;
    }
    (void)this->issueWrite(client, offset, length, seed, FramTester_Activity::WRITE_PATTERN);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void FramTester ::READ_VERIFY_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed) {
    if (!this->acceptPatternCommand(opCode, cmdSeq, client, length)) {
        return;
    }
    (void)this->issueRead(client, offset, length, seed, FramTester_Activity::READ_VERIFY);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void FramTester ::REQUEST_RAW_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fram::Op& op, U8 client, U32 offset, U8 length) {
    if ((client >= CLIENTS) || (length > MAX_RAW)) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    if (this->m_activity[client] != FramTester_Activity::NONE) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
        return;
    }
    (void)::memset(this->m_raw, 0, sizeof(this->m_raw));
    Fw::Buffer raw(this->m_raw, length);
    (void)this->issue(op, client, offset, raw, FramTester_Activity::RAW);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void FramTester ::BURST_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed, const FramTester_BurstMode& mode) {
    if (!this->acceptPatternCommand(opCode, cmdSeq, client, length)) {
        return;
    }
    if (mode == FramTester_BurstMode::SEQUENTIAL) {
        // Second read issued from inside the first read's completion (see readComplete_handler)
        this->m_burstSecond[client] = true;
        (void)this->issueRead(client, offset, length, seed, FramTester_Activity::READ_VERIFY);
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
        return;
    }
    const Fram::Status first = this->issueRead(client, offset, length, seed, FramTester_Activity::READ_VERIFY);
    // Second read on the same client right after the first returned, before any completion: the driver is
    // expected to answer BUSY. Issued directly (not through issueRead) so the first request's record is kept.
    Fw::Buffer same(this->m_data[client], length);
    const Fram::Status second = this->read_out(client, offset, same);
    this->log_ACTIVITY_LO_RequestReturned(Fram::Op::READ, client, offset, length, second);
    if (second != Fram::Status::ACCEPTED) {
        this->m_rejectionsSeen++;
        this->tlmWrite_RejectionsSeen(this->m_rejectionsSeen);
    }
    (void)first;
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void FramTester ::CHAIN_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed) {
    if (!this->acceptPatternCommand(opCode, cmdSeq, client, length)) {
        return;
    }
    (void)this->issueWrite(client, offset, length, seed, FramTester_Activity::CHAIN_WRITE);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

void FramTester ::SWEEP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 seed, U16 chunks, U32 verifySeed) {
    if ((this->m_activity[0] != FramTester_Activity::NONE) || (chunks == 0) || (chunks > SWEEP_CHUNKS)) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
        return;
    }
    this->m_sweepSeed = seed;
    this->m_sweepChunks = chunks;
    this->m_sweepVerifySeed = verifySeed;
    this->m_sweepKind = FramTester_SweepKind::WRITE;
    this->m_sweepChunk = 0;
    this->m_sweepDone = 0;
    this->m_sweepErrors = 0;
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
    this->sweepNext();
}

void FramTester ::DUMP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset, U8 length) {
    if ((length == 0) || (length > MAX_DUMP)) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    if (this->m_activity[0] != FramTester_Activity::NONE) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
        return;
    }
    (void)this->issueRead(0, offset, length, 0, FramTester_Activity::DUMP);
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

Fram::Status FramTester ::issue(Fram::Op op,
                                FwIndexType client,
                                U32 offset,
                                Fw::Buffer& buffer,
                                FramTester_Activity activity) {
    FW_ASSERT((client >= 0) && (client < CLIENTS), static_cast<FwAssertArgType>(client));
    // Commit the record before the call: the completion may arrive inside it
    this->m_activity[client] = activity;
    this->m_buffer[client] = buffer;
    const U8 length = static_cast<U8>(buffer.getSize());
    Fram::Status status = (op == Fram::Op::READ) ? this->read_out(client, offset, buffer)
                                                 : this->write_out(client, offset, buffer);
    if (activity != FramTester_Activity::SWEEP_WRITE && activity != FramTester_Activity::SWEEP_VERIFY) {
        this->log_ACTIVITY_LO_RequestReturned(op, static_cast<U8>(client), offset, length, status);
    }
    if (status != Fram::Status::ACCEPTED) {
        this->m_rejectionsSeen++;
        this->tlmWrite_RejectionsSeen(this->m_rejectionsSeen);
        // No completion follows a rejection; unless the completion already arrived inside the call, clear the record
        if (this->m_activity[client] == activity) {
            this->m_activity[client] = FramTester_Activity::NONE;
        }
    }
    return status;
}

Fram::Status FramTester ::issueWrite(FwIndexType client,
                                     U32 offset,
                                     FwSizeType length,
                                     U32 seed,
                                     FramTester_Activity activity) {
    FW_ASSERT(length <= MAX_TRANSFER, static_cast<FwAssertArgType>(length));
    for (FwSizeType i = 0; i < length; i++) {
        this->m_data[client][i] = patternByte(seed, offset, static_cast<U32>(i));
    }
    this->m_seed[client] = seed;
    Fw::Buffer buffer(this->m_data[client], length);
    return this->issue(Fram::Op::WRITE, client, offset, buffer, activity);
}

Fram::Status FramTester ::issueRead(FwIndexType client,
                                    U32 offset,
                                    FwSizeType length,
                                    U32 seed,
                                    FramTester_Activity activity) {
    FW_ASSERT(length <= MAX_TRANSFER, static_cast<FwAssertArgType>(length));
    (void)::memset(this->m_data[client], 0, sizeof(this->m_data[client]));
    this->m_seed[client] = seed;
    Fw::Buffer buffer(this->m_data[client], length);
    return this->issue(Fram::Op::READ, client, offset, buffer, activity);
}

U8 FramTester ::countMismatches(FwIndexType client, U32 offset, FwSizeType length) const {
    U8 mismatches = 0;
    for (FwSizeType i = 0; (i < length) && (i < MAX_TRANSFER); i++) {
        if (this->m_data[client][i] != patternByte(this->m_seed[client], offset, static_cast<U32>(i))) {
            mismatches++;
        }
    }
    return mismatches;
}

void FramTester ::sweepNext() {
    if (this->m_sweepChunk >= this->m_sweepChunks) {
        // Pass complete
        this->tlmWrite_BytesVerified(this->m_bytesVerified);
        this->tlmWrite_Mismatches(this->m_mismatches);
        this->log_ACTIVITY_HI_SweepDone(this->m_sweepKind, this->m_sweepDone, this->m_sweepErrors);
        if (this->m_sweepKind == FramTester_SweepKind::WRITE) {
            this->m_sweepKind = FramTester_SweepKind::VERIFY;
            this->m_sweepChunk = 0;
            this->m_sweepDone = 0;
            this->m_sweepErrors = 0;
        } else {
            return;
        }
    }
    const U32 offset = static_cast<U32>(this->m_sweepChunk) * static_cast<U32>(MAX_TRANSFER);
    this->m_sweepChunk++;
    Fram::Status status;
    if (this->m_sweepKind == FramTester_SweepKind::WRITE) {
        status = this->issueWrite(0, offset, MAX_TRANSFER, this->m_sweepSeed, FramTester_Activity::SWEEP_WRITE);
    } else {
        status = this->issueRead(0, offset, MAX_TRANSFER, this->m_sweepVerifySeed, FramTester_Activity::SWEEP_VERIFY);
    }
    if (status != Fram::Status::ACCEPTED) {
        // A rejection ends the pass: counted as an error, reported, no further chunk
        this->m_sweepErrors++;
        this->log_ACTIVITY_HI_SweepDone(this->m_sweepKind, this->m_sweepDone, this->m_sweepErrors);
        this->m_sweepChunk = this->m_sweepChunks;
        this->m_sweepKind = FramTester_SweepKind::VERIFY;  // nothing more follows
    }
}

}  // namespace CuriosityReference
