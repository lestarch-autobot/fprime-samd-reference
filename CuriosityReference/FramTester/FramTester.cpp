// ======================================================================
// \title  FramTester.cpp
// \brief  Test harness for Fram.FramDriver on CuriosityReference (system test plan QUAL-FRAM §0)
// ======================================================================

#include "CuriosityReference/FramTester/FramTester.hpp"

#include <cstring>

#include "Fw/Types/Assert.hpp"

#ifdef FRAM_COVERAGE_TRIAL
// GCC >= 11 freestanding gcov: __gcov_info_to_gcda streams one object's .gcda through callbacks
extern "C" {
#include <gcov.h>  // declares no C linkage itself
extern const struct gcov_info* const __gcov_info_start[];  // linker script .gcov_info
extern const struct gcov_info* const __gcov_info_end[];
extern U32 __gcov_counters_start[];  // linker script .gcov_counters (NOLOAD: survives a warm reset)
extern U32 __gcov_counters_end[];
}
#endif

namespace CuriosityReference {

constexpr FwIndexType FramTester::CLIENTS;
constexpr FwSizeType FramTester::MAX_TRANSFER;
constexpr FwSizeType FramTester::MAX_RAW;
constexpr FwSizeType FramTester::MAX_DUMP;
constexpr U32 FramTester::FRAM_SIZE;
constexpr U16 FramTester::SWEEP_CHUNKS;
constexpr FwSizeType FramTester::SERIAL_WORDS;
constexpr U32 FramTester::SERIAL_WORD_ADDRESS[FramTester::SERIAL_WORDS];
constexpr FwSizeType FramTester::COVERAGE_CHUNK;
constexpr U8 FramTester::COVERAGE_MAX_CHUNKS;

#ifdef FRAM_COVERAGE_TRIAL
namespace {
// Which image the preserved counters belong to; lives in the counter region, so it is not zeroed at startup
struct CoverageId {
    U32 magic;
    U32 identity;
};
__attribute__((section(".gcov_counters.id"), used)) CoverageId s_coverageId;
constexpr U32 COVERAGE_MAGIC = 0xC0FEC0DEu;
}  // namespace
#endif

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
      m_rejectionsSeen(0),
      m_cov() {
    for (FwIndexType i = 0; i < CLIENTS; i++) {
        this->m_activity[i] = FramTester_Activity::NONE;
    }
    this->coverageInit();
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
        this->log_WARNING_HI_UnexpectedCompletion(static_cast<U8>(portNum), status, FramTester_CompletionFault::NO_REQUEST);
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
            this->log_WARNING_HI_UnexpectedCompletion(client, status, FramTester_CompletionFault::WRONG_PORT);
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
            this->log_WARNING_HI_UnexpectedCompletion(client, status, FramTester_CompletionFault::WRONG_PORT);
            break;
    }
}

// ----------------------------------------------------------------------
// Scheduler
// ----------------------------------------------------------------------

void FramTester ::schedIn_handler(FwIndexType portNum, U32 context) {
    U32 words[SERIAL_WORDS];
    for (FwSizeType i = 0; i < SERIAL_WORDS; i++) {
        // Hardware read of a fixed NVM address (CPP-10: the only way to reach a memory-mapped word)
        words[i] = *reinterpret_cast<volatile const U32*>(SERIAL_WORD_ADDRESS[i]);
    }
    this->tlmWrite_McuSerial0(words[0]);
    this->tlmWrite_McuSerial1(words[1]);
    this->tlmWrite_McuSerial2(words[2]);
    this->tlmWrite_McuSerial3(words[3]);
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

void FramTester ::DUMP_COVERAGE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U16 start, U8 count) {
#ifdef FRAM_COVERAGE_TRIAL
    if (count > COVERAGE_MAX_CHUNKS) {
        this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::VALIDATION_ERROR);
        return;
    }
    if (count == 0) {
        this->coverageZero();
        this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
        return;
    }
    // Every command walks the whole stream (chunk indices are only known by walking) and emits its window
    this->m_cov = CoverageWalk();
    this->m_cov.windowStart = start;
    this->m_cov.windowEnd = static_cast<U32>(start) + count;
    this->m_cov.crc = 0xFFFFFFFFu;
    for (const struct gcov_info* const* info = __gcov_info_start; info < __gcov_info_end; info++) {
        this->m_cov.objectBytes = 0;
        this->m_cov.lastInWindow = false;
        this->m_cov.nameCrc = 0;
        __gcov_info_to_gcda(*info, coverageFilename, coverageDump, coverageAllocate, this);
        this->coverageFlushChunk(true);
        this->m_cov.object++;
    }
    if (this->m_cov.windowEnd >= this->m_cov.chunk) {
        this->log_ACTIVITY_HI_CoverageDumpDone(this->m_cov.object, this->m_cov.totalBytes, this->m_cov.crc ^ 0xFFFFFFFFu);
    }
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::OK);
#else
    (void)start;
    (void)count;
    this->log_WARNING_LO_CommandRejected(static_cast<U8>(opCode & 0xFF));
    this->cmdResponse_out(opCode, cmdSeq, Fw::CmdResponse::EXECUTION_ERROR);
#endif
}

// ----------------------------------------------------------------------
// Coverage trial helpers (no-ops without FRAM_COVERAGE_TRIAL)
// ----------------------------------------------------------------------

U32 FramTester ::crc32Update(U32 crc, const U8* data, U32 length) {
    for (U32 i = 0; i < length; i++) {
        crc ^= data[i];
        for (U8 k = 0; k < 8; k++) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return crc;
}

void FramTester ::coverageInit() {
#ifdef FRAM_COVERAGE_TRIAL
    // Identity of the image: the .gcov_info pointer table and the counter region bounds move with any
    // change to the instrumented code. Mismatch (cold start, new image) -> start from zero.
    const U8* tableStart = reinterpret_cast<const U8*>(__gcov_info_start);
    const U8* tableEnd = reinterpret_cast<const U8*>(__gcov_info_end);
    U32 identity = crc32Update(0xFFFFFFFFu, tableStart, static_cast<U32>(tableEnd - tableStart));
    identity ^= reinterpret_cast<U32>(__gcov_counters_start) ^ reinterpret_cast<U32>(__gcov_counters_end);
    if ((s_coverageId.magic != COVERAGE_MAGIC) || (s_coverageId.identity != identity)) {
        for (U32* word = __gcov_counters_start; word < __gcov_counters_end; word++) {
            *word = 0;
        }
        s_coverageId.magic = COVERAGE_MAGIC;
        s_coverageId.identity = identity;
    }
#endif
}

void FramTester ::coverageZero() {
#ifdef FRAM_COVERAGE_TRIAL
    for (U32* word = __gcov_counters_start; word < __gcov_counters_end; word++) {
        *word = 0;
    }
    s_coverageId.magic = 0;  // force coverageInit to restamp the identity
    this->coverageInit();
#endif
}

void FramTester ::coverageFilename(const char* name, void* arg) {
    FramTester* self = static_cast<FramTester*>(arg);
    self->m_cov.nameCrc =
        crc32Update(0xFFFFFFFFu, reinterpret_cast<const U8*>(name), static_cast<U32>(strlen(name))) ^ 0xFFFFFFFFu;
}

void FramTester ::coverageDump(const void* data, unsigned length, void* arg) {
    FramTester* self = static_cast<FramTester*>(arg);
    const U8* bytes = static_cast<const U8*>(data);
    self->m_cov.crc = crc32Update(self->m_cov.crc, bytes, length);
    for (unsigned i = 0; i < length; i++) {
        self->m_cov.chunkData[self->m_cov.chunkLength++] = bytes[i];
        self->m_cov.objectBytes++;
        self->m_cov.totalBytes++;
        if (self->m_cov.chunkLength == COVERAGE_CHUNK) {
            self->coverageFlushChunk(false);
        }
    }
}

void* FramTester ::coverageAllocate(unsigned length, void* arg) {
    // Only TOPN value-profile counters allocate; -fprofile-arcs alone never calls this
    (void)length;
    (void)arg;
    FW_ASSERT(0);
    return nullptr;
}

void FramTester ::coverageFlushChunk(bool objectEnd) {
    CoverageWalk& w = this->m_cov;
    if (w.chunkLength > 0) {
        if ((w.chunk >= w.windowStart) && (w.chunk < w.windowEnd)) {
            U32 words[COVERAGE_CHUNK / 4] = {0};
            for (FwSizeType i = 0; i < w.chunkLength; i++) {
                words[i / 4] |= static_cast<U32>(w.chunkData[i]) << (24 - 8 * (i % 4));
            }
            this->log_ACTIVITY_LO_CoverageChunk(w.object, w.objectBytes - w.chunkLength, w.chunkLength, words[0],
                                                words[1], words[2], words[3], words[4], words[5], words[6],
                                                words[7]);
            w.lastInWindow = true;
        } else {
            w.lastInWindow = false;
        }
        w.chunk++;
        w.chunkLength = 0;
    }
    if (objectEnd && w.lastInWindow) {
        this->log_ACTIVITY_LO_CoverageObject(w.object, w.nameCrc, w.objectBytes);
    }
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
        // A rejected first BURST read must not leave the second read armed for a later command's completion
        this->m_burstSecond[client] = false;
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
