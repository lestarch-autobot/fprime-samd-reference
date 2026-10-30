module Samd21 {
    module StaticTlmPacketizerConfig {
        @ Size of StaticTlmPacketizer.pktSendIn. The port index is the packet id, so this
        @ must be strictly greater than the largest packet id driven from a port.
        @ CuriosityReference FRAM test build: packet Fram (id 2) is driven from rg1Hz.
        constant NUM_TLM_PACKETS = 3

        @ Number of ports to multiplex the packet transmission over
        constant NUM_PKT_OUT = 1
    }
}
