// ======================================================================
// \title  FramTester.hpp
// \brief  Test harness for Fram.FramDriver on CuriosityReference (system test plan QUAL-FRAM §0)
//
// Harness-owned observables only: every framDriver.read/write return value and every completion is
// reported as an event, so the GDS archive carries what the UUT did. Buffers are component-owned and
// handed to the driver by reference; a completion is checked against the buffer it was issued with.
// ======================================================================

#ifndef CuriosityReference_FramTester_HPP
#define CuriosityReference_FramTester_HPP

#include "CuriosityReference/FramTester/FramTesterComponentAc.hpp"
#include "CuriosityReference/FramTester/FramTester_ActivityEnumAc.hpp"
#include "CuriosityReference/FramTester/FramTester_BurstModeEnumAc.hpp"
#include "Fw/Buffer/Buffer.hpp"

// Coverage trial: functions that run between two DUMP_COVERAGE windows (the dump path itself and the
// 1 Hz schedIn) must not move any arc counter, or the windows describe different gcda streams and the
// final CRC cannot match. GCC >= 8 attribute; empty in normal builds.
#ifdef FRAM_COVERAGE_TRIAL
#define FRAM_COVERAGE_NO_INSTRUMENT __attribute__((no_profile_instrument_function))
#else
#define FRAM_COVERAGE_NO_INSTRUMENT
#endif

namespace CuriosityReference {

class FramTester final : public FramTesterComponentBase {
  public:
    static constexpr FwIndexType CLIENTS = 2;
    static constexpr FwSizeType MAX_TRANSFER = 64;      //!< Fram.FramConfig.MaxTransfer
    static constexpr FwSizeType MAX_RAW = 72;           //!< REQUEST_RAW buffer capacity (covers the 65-byte case)
    static constexpr FwSizeType MAX_DUMP = 8;
    static constexpr U32 FRAM_SIZE = 262144;            //!< Fram.FramConfig.Size
    static constexpr U16 SWEEP_CHUNKS = static_cast<U16>(FRAM_SIZE / MAX_TRANSFER);
    static constexpr FwSizeType SERIAL_WORDS = 4;
    //! SAMD21 128-bit unique serial number: four non-contiguous words in the NVM (data sheet section 10.3.3)
    static constexpr U32 SERIAL_WORD_ADDRESS[SERIAL_WORDS] = {0x0080A00Cu, 0x0080A040u, 0x0080A044u, 0x0080A048u};
    static constexpr FwSizeType COVERAGE_CHUNK = 32;  //!< gcda bytes per CoverageChunk event
    static constexpr U8 COVERAGE_MAX_CHUNKS = 4;      //!< DUMP_COVERAGE count ceiling (framer: 2 x 128-byte buffers)

    // cppcheck-suppress noExplicitConstructor
    FramTester(const char* const compName);
    ~FramTester();

    //! Normative pattern generator (plan §0): byte i of (seed, offset) = xorshift32(seed ^ (offset + i)) & 0xFF
    static U8 patternByte(U32 seed, U32 offset, U32 i);

  private:
    // Port handlers
    void readComplete_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) override;
    void writeComplete_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) override;
    //! Publishes McuSerial0..3 from SERIAL_WORD_ADDRESS
    FRAM_COVERAGE_NO_INSTRUMENT void schedIn_handler(FwIndexType portNum, U32 context) override;

    // Command handlers
    void MARK_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 id) override;
    void WRITE_PATTERN_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed) override;
    void READ_VERIFY_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed) override;
    void REQUEST_RAW_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, const Fram::Op& op, U8 client, U32 offset, U8 length) override;
    void BURST_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed, const FramTester_BurstMode& mode) override;
    void CHAIN_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U8 client, U32 offset, U8 length, U32 seed) override;
    void SWEEP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 seed, U16 chunks, U32 verifySeed) override;
    void DUMP_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U32 offset, U8 length) override;
    FRAM_COVERAGE_NO_INSTRUMENT void DUMP_COVERAGE_cmdHandler(FwOpcodeType opCode, U32 cmdSeq, U16 start, U8 count) override;

    // Helpers
    //! Common argument check for the pattern commands; sends the command response on failure
    bool acceptPatternCommand(FwOpcodeType opCode, U32 cmdSeq, U8 client, U8 length);
    //! Fill the client's buffer with the pattern and issue a write; records activity, emits RequestReturned
    Fram::Status issueWrite(FwIndexType client, U32 offset, FwSizeType length, U32 seed, FramTester_Activity activity);
    //! Issue a read into the client's buffer; records activity, emits RequestReturned
    Fram::Status issueRead(FwIndexType client, U32 offset, FwSizeType length, U32 seed, FramTester_Activity activity);
    //! Issue a request on the client with the given buffer; emits RequestReturned and counts rejections
    Fram::Status issue(Fram::Op op, FwIndexType client, U32 offset, Fw::Buffer& buffer, FramTester_Activity activity);
    //! Count bytes of the client's buffer differing from the pattern (seed, offset)
    U8 countMismatches(FwIndexType client, U32 offset, FwSizeType length) const;
    //! Completion common path: identity checks; returns false when the completion is not accounted for
    bool checkCompletion(FwIndexType portNum, Fw::Buffer& buffer, const Fram::Status& status);
    //! Issue the next sweep chunk or finish the pass
    void sweepNext();

    // Per-client state
    FramTester_Activity m_activity[CLIENTS];
    Fw::Buffer m_buffer[CLIENTS];   //!< The buffer handed to the driver for the outstanding request
    U32 m_seed[CLIENTS];
    U8 m_data[CLIENTS][MAX_TRANSFER];
    U8 m_raw[MAX_RAW];

    // Sweep state (client 0)
    FramTester_SweepKind m_sweepKind;
    U16 m_sweepChunk;   //!< Next chunk index to issue
    U16 m_sweepChunks;  //!< Chunks per sweep pass (SWEEP argument)
    U32 m_sweepVerifySeed;  //!< Seed the verify pass compares against (SWEEP argument)
    bool m_burstSecond[CLIENTS];  //!< SEQUENTIAL BURST: issue the second read from the first completion
    U16 m_sweepDone;    //!< Chunks completed in the current pass
    U16 m_sweepErrors;
    U32 m_sweepSeed;

    // Counters
    U32 m_bytesWritten;
    U32 m_bytesVerified;
    U32 m_mismatches;
    U32 m_rejectionsSeen;

    // Coverage trial: windowed walk of the gcda streams (state valid during one DUMP_COVERAGE only)
    struct CoverageWalk {
        U32 windowStart;  //!< First global chunk index to emit
        U32 windowEnd;    //!< One past the last chunk index to emit
        U32 chunk;        //!< Global chunk index of the chunk being filled
        U32 totalBytes;   //!< gcda bytes walked so far, all objects
        U32 objectBytes;  //!< gcda bytes walked so far, current object
        U32 crc;          //!< Running CRC-32 over all gcda bytes
        U32 nameCrc;      //!< CRC-32 of the current object's gcda file name
        U8 object;        //!< Current object index
        U8 chunkLength;   //!< Bytes in chunkData
        bool lastInWindow;  //!< The current object's most recent chunk was emitted
        U8 chunkData[COVERAGE_CHUNK];
    };
    FRAM_COVERAGE_NO_INSTRUMENT static U32 crc32Update(U32 crc, const U8* data, U32 length);
    FRAM_COVERAGE_NO_INSTRUMENT static void coverageFilename(const char* name, void* arg);
    FRAM_COVERAGE_NO_INSTRUMENT static void coverageDump(const void* data, unsigned length, void* arg);
    FRAM_COVERAGE_NO_INSTRUMENT static void* coverageAllocate(unsigned length, void* arg);
    FRAM_COVERAGE_NO_INSTRUMENT void coverageInit();
    FRAM_COVERAGE_NO_INSTRUMENT void coverageZero();
    FRAM_COVERAGE_NO_INSTRUMENT void coverageFlushChunk(bool objectEnd);
    CoverageWalk m_cov;
};

}  // namespace CuriosityReference

#endif
