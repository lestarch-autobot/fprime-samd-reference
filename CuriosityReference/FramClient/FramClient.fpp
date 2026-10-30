module CuriosityReference {
    @ Pass-through F-RAM client, one per client index. FramDriver.fpp matches each completion
    @ port with its request port and FPP requires every index of a matched array to connect to
    @ a separate instance, so the single FramTester reaches framDriver.read/write[k] through
    @ one of these per k. Pure forwarding; no state, no dictionary items.
    passive component FramClient {
        sync input port readIn: Fram.Request
        sync input port writeIn: Fram.Request
        output port readOut: Fram.Request
        output port writeOut: Fram.Request
        sync input port readCompleteIn: Fram.Complete
        sync input port writeCompleteIn: Fram.Complete
        output port readCompleteOut: Fram.Complete
        output port writeCompleteOut: Fram.Complete
    }
}
