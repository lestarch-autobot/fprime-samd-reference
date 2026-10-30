module CuriosityReference {
    @ Harness for the Fram.FramDriver system tests (plan QUAL-FRAM §0). Harness-owned
    @ observables only: it drives framDriver.read/write on two client indices from ground
    @ commands and reports every return value and completion as an event, so the archive
    @ carries what the UUT did. It models nothing and verifies nothing itself beyond
    @ comparing read-back bytes with the normative xorshift32 pattern of the plan.
    passive component FramTester {
        # ------------------------------------------------------------------
        # UUT client ports (two client indices)
        # ------------------------------------------------------------------

        @ Read request to the F-RAM driver, index = client
        output port read: [2] Fram.Request

        @ Write request to the F-RAM driver, index = client
        output port write: [2] Fram.Request

        @ Read completion, index = client the request was issued on
        sync input port readComplete: [2] Fram.Complete

        @ Write completion, index = client the request was issued on
        sync input port writeComplete: [2] Fram.Complete

        # ------------------------------------------------------------------
        # Commands (one outstanding at a time; the response is sent when the
        # request call has returned -- the completion is reported by an event)
        # ------------------------------------------------------------------

        @ Echo a marker into the event stream (window brackets)
        sync command MARK($id: U32) opcode 0

        @ Write the xorshift32 pattern (seed, offset) of `length` bytes at `offset` on `client`
        sync command WRITE_PATTERN(
            client: U8 @< Client index 0..1
            offset: U32 @< F-RAM offset
            length: U8 @< Bytes, 1..64
            seed: U32 @< Pattern seed
        ) opcode 1

        @ Read `length` bytes at `offset` on `client` and count bytes differing from the pattern (seed, offset)
        sync command READ_VERIFY(
            client: U8 @< Client index 0..1
            offset: U32 @< F-RAM offset
            length: U8 @< Bytes, 1..64
            seed: U32 @< Pattern seed
        ) opcode 2

        @ Issue a raw request with a buffer of `length` bytes (any value, incl. 0 and 65) and report the return value
        sync command REQUEST_RAW(
            op: Fram.Op @< READ or WRITE
            client: U8 @< Client index 0..1
            offset: U32 @< F-RAM offset
            length: U8 @< Buffer size handed to the driver
        ) opcode 3

        @ Two READ_VERIFY reads: BACK_TO_BACK issues the second right after the first returned, before any
        @ completion; SEQUENTIAL issues it from inside the first read's completion
        sync command BURST(
            client: U8 @< Client index 0..1
            offset: U32 @< F-RAM offset
            length: U8 @< Bytes, 1..64
            seed: U32 @< Pattern seed
            mode: BurstMode @< When the second read is issued
        ) opcode 4

        @ WRITE_PATTERN whose verifying read is issued from inside the write completion callback
        sync command CHAIN(
            client: U8 @< Client index 0..1
            offset: U32 @< F-RAM offset
            length: U8 @< Bytes, 1..64
            seed: U32 @< Pattern seed
        ) opcode 5

        @ Write then verify the whole device in 64-byte chunks on client 0, each chunk issued from the previous completion
        sync command SWEEP(
            seed: U32 @< Pattern seed
            chunks: U16 @< 64-byte chunks per pass, 1..4096 (4096 = whole device)
            verifySeed: U32 @< Seed the verify pass compares against (= seed for a nominal sweep)
        ) opcode 6

        @ Read `length` (1..8) bytes at `offset` on client 0 and report them in DumpData (auditor's final-state check)
        sync command DUMP(
            offset: U32 @< F-RAM offset
            length: U8 @< Bytes, 1..8
        ) opcode 7

        # ------------------------------------------------------------------
        # Types
        # ------------------------------------------------------------------

        @ What a client index is doing
        enum Activity: U8 {
            NONE @< No request outstanding
            WRITE_PATTERN @< WRITE_PATTERN write
            READ_VERIFY @< READ_VERIFY read (also BURST reads)
            RAW @< REQUEST_RAW request
            CHAIN_WRITE @< CHAIN write; the read is issued from its completion
            CHAIN_READ @< CHAIN's chained read
            SWEEP_WRITE @< SWEEP write pass
            SWEEP_VERIFY @< SWEEP verify pass
            DUMP @< DUMP read
        } default NONE

        @ Sweep pass
        enum BurstMode: U8 {
            BACK_TO_BACK
            SEQUENTIAL
        } default BACK_TO_BACK

        enum SweepKind: U8 {
            WRITE
            VERIFY
        }

        # ------------------------------------------------------------------
        # Events
        # ------------------------------------------------------------------

        @ Marker echoed from MARK
        event Marker($id: U32) severity activity high id 0 format "FramTester marker {}"

        @ Return value of a framDriver.read/write call made by a command handler (or CHAIN's chained read)
        event RequestReturned(
            op: Fram.Op @< READ or WRITE
            client: U8 @< Client index the call was made on
            offset: U32 @< Offset passed
            length: U8 @< Buffer size passed
            $status: Fram.Status @< The call's return value
        ) severity activity low id 1 format "FramTester {} client {} offset {} length {} returned {}"

        @ A WRITE_PATTERN / CHAIN write completion (client, offset, length from the completion)
        event WriteDone(
            client: U8 @< Completion port index
            offset: U32 @< Offset from the completion
            length: U8 @< Completion buffer size
            $status: Fram.Status @< Completion status
        ) severity activity low id 2 format "FramTester write done client {} offset {} length {}: {}"

        @ A READ_VERIFY / BURST / CHAIN read completion with the pattern comparison
        event VerifyDone(
            client: U8 @< Completion port index
            offset: U32 @< Offset from the completion
            length: U8 @< Completion buffer size
            $status: Fram.Status @< Completion status
            mismatches: U8 @< Bytes differing from the pattern (0 when status != OK)
        ) severity activity low id 3 format "FramTester verify done client {} offset {} length {}: {} mismatches {}"

        @ A REQUEST_RAW completion
        event RawDone(
            op: Fram.Op @< READ or WRITE
            client: U8 @< Completion port index
            offset: U32 @< Offset from the completion
            length: U8 @< Completion buffer size
            $status: Fram.Status @< Completion status
        ) severity activity low id 4 format "FramTester raw {} done client {} offset {} length {}: {}"

        @ A completion arrived on a client index with no request outstanding
        event UnexpectedCompletion(
            client: U8 @< Completion port index
            $status: Fram.Status @< Completion status
        ) severity warning high id 5 format "FramTester unexpected completion on client {}: {}"

        @ The completion's buffer pointer or size differs from the one passed in the request
        event BufferMismatch(client: U8) severity warning high id 6 format "FramTester buffer mismatch on client {}"

        @ A SWEEP pass finished (or stopped on a non-ACCEPTED return)
        event SweepDone(
            kind: SweepKind @< WRITE or VERIFY pass
            chunks: U16 @< Chunks completed
            errors: U16 @< Non-OK completions (WRITE) or chunks with mismatches (VERIFY), plus a stopping rejection
        ) severity activity high id 7 format "FramTester sweep {} done: {} chunks, {} errors"

        @ Bytes read by DUMP, MSB-first packed, zero-padded past length
        event DumpData(
            offset: U32 @< Offset read
            length: U8 @< Bytes read
            data0: U32 @< Bytes 0-3
            data1: U32 @< Bytes 4-7
        ) severity activity low id 8 format "FramTester dump offset {} length {}: {x} {x}"

        @ Command argument out of range (client > 1, length out of bounds) or a request outstanding on that client
        event CommandRejected($opcode: U8) severity warning low id 9 format "FramTester command {} rejected"

        # ------------------------------------------------------------------
        # Telemetry
        # ------------------------------------------------------------------

        @ Bytes of OK write completions
        telemetry BytesWritten: U32 id 0

        @ Bytes compared by READ_VERIFY / BURST / CHAIN / SWEEP-verify (any outcome)
        telemetry BytesVerified: U32 id 1

        @ Cumulative mismatching bytes
        telemetry Mismatches: U32 id 2

        @ Request calls that returned other than ACCEPTED
        telemetry RejectionsSeen: U32 id 3

        ###############################################################################
        # Standard AC Ports
        ###############################################################################
        time get port timeCaller
        import Fw.Command
        import Fw.Event
        import Fw.Channel
    }
}
