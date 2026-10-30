module CuriosityReference {
    # ----------------------------------------------------------------------
    # Symbolic constants for port numbers
    # ----------------------------------------------------------------------

    enum Ports_ComPacketQueue : U8 {
        EVENTS
        TELEMETRY
        FATAL
    }

    enum Ports_RateGroups {
        rateGroup1
        rateGroup2
        rateGroup3
    }

    @ Every DMA channel used by the deployment. The SAMD21 DMAC has 12 channels and
    @ Samd21.DmaDriver sizes its port arrays from this enum, so a channel that is not
    @ listed here cannot be used -- and two peripherals must never be given the same
    @ index.
    enum DmaChannel : U8 {
        USART0_UPL_DWN_TX
        USART0_UPL_DWN_RX
        SERCOM1_SPI_MOSI
        SERCOM1_SPI_MISO
    }

    deployment topology Top {
        # ----------------------------------------------------------------------
        # Instances used in the topology
        # ----------------------------------------------------------------------

        instance fatalHandler
        instance rateDriver
        instance rg8Hz
        instance rg1Hz
        instance rg10s
        instance cycler
        instance rateGroupDriver
        instance timeHandler
        instance cmdDisp
        instance fwHealth

        instance comDriver
        instance spiDriver
        instance csPA18

        instance downlink
        instance framer

        instance comStub
        instance deframer
        instance fprimeRouter
        instance commsBufferManager
        instance frameAccumulator
        instance dmaDriver
        instance fatalFramer
        instance framDriver
        instance framTester
        instance framClient0
        instance framClient1

        instance pinIn
        instance inPA23
        instance pinOut
        instance outPA25

        instance tlm

        telemetry packets Main {

            packet Error id 0 group 1 {
                framer.DroppedPackets
                framDriver.RequestsRejected
                framDriver.SpiErrors
            }

            packet Health id 1 group 0 {
                rateDriver.CycleOverrun
                fwHealth.Counter
                rg8Hz.CycleCount
                rateGroupDriver.MaxCycleTime
                rg8Hz.MaxCycleTime
                rg1Hz.MaxCycleTime
                rg10s.MaxCycleTime
                comDriver.rxOverflows
                comDriver.rxBytes
                comDriver.txBytes
            }

            @ Scheduled at 1 Hz from rg1Hz (pktSendIn[2]); the UUT's standing state and counters plus the harness counters
            packet Fram id 2 group 1 {
                framDriver.StartupState
                framDriver.DeviceIdMatch
                framDriver.StatusRegister
                framDriver.ReadsCompleted
                framDriver.WritesCompleted
                framDriver.RequestsRejected
                framDriver.SpiErrors
                framer.DroppedPackets
                framTester.BytesWritten
                framTester.BytesVerified
                framTester.Mismatches
                framTester.RejectionsSeen
            }

        } omit {
            # Instantaneous and per-port cycle times. The high-water marks
            # (MaxCycleTime) are downlinked in the Health packet instead: they are
            # what a 16 KiB, 128 KiB-flash board actually needs, and PortCycleTime /
            # PortCycleTimeHWM are arrays of PassiveRateGroupOutputPorts entries,
            # which is more than a 128-byte packet can carry.
            rg8Hz.CycleTime
            rg8Hz.PortCycleTime
            rg8Hz.PortCycleTimeHWM

            rg1Hz.CycleTime
            rg1Hz.PortCycleTime
            rg1Hz.PortCycleTimeHWM

            rg10s.CycleTime
            rg10s.PortCycleTime
            rg10s.PortCycleTimeHWM

            rateGroupDriver.CycleTime

            # rg8Hz.CycleCount is downlinked; the slower groups are derivable from
            # it (divisors 8 and 80) and would just consume packet space.
            rg1Hz.CycleCount
            rg10s.CycleCount
        }

        # ----------------------------------------------------------------------
        # Pattern graph specifiers
        # ----------------------------------------------------------------------

        command connections instance cmdDisp

        event connections instance downlink

        telemetry connections instance tlm

        time connections instance timeHandler

        # ----------------------------------------------------------------------
        # Direct graph specifiers
        # ----------------------------------------------------------------------

        @ Components that need to be serviced from the main context rather than from
        @ an ISR. Each one latches work in its interrupt handler and completes it
        @ here, on cycler.cycle() in Main.cpp. Dropping one of these connections does
        @ not fail the build -- it silently stops that driver from ever delivering a
        @ completion.
        connections ActiveRateGroups {
            cycler.cycleOut -> comDriver.activeIn
            cycler.cycleOut -> rateDriver.activeIn
            cycler.cycleOut -> spiDriver.activeIn
        }

        connections RateGroups {
            # Block driver
            rateDriver.CycleOut -> rateGroupDriver.CycleIn

            # A PassiveRateGroup calls its members in ascending port-index order,
            # synchronously, on the rate driver's stack. The indices below are
            # therefore an execution order, not just wiring, and every one of them is
            # written out explicitly -- an unindexed connection takes the next free
            # slot, which means inserting a member silently renumbers the ones after
            # it. Unconnected slots in between are skipped at no cost.

            # Rate group 1 -- 8 Hz
            rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup1] -> rg8Hz.CycleIn
            rg8Hz.RateGroupMemberOut[0]                           -> comDriver.schedIn

            # Rate group 2 -- 1 Hz
            rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup2] -> rg1Hz.CycleIn
            # One Fram packet per 1 Hz tick (CuriosityReference otherwise sends packets only on tlm.SEND_PKT)
            rg1Hz.RateGroupMemberOut[1]                           -> tlm.pktSendIn[2]
            # framer.schedIn sits in the LAST slot on purpose: it drains the com
            # packet queue, so it must run after every member that may have pushed
            # telemetry or an event into that queue this cycle.
            rg1Hz.RateGroupMemberOut[PassiveRateGroupOutputPorts - 1] -> framer.schedIn

            # Rate group 3 -- 10 s period
            rateGroupDriver.CycleOut[Ports_RateGroups.rateGroup3] -> rg10s.CycleIn
            rg10s.RateGroupMemberOut[0]                           -> fwHealth.schedIn
        }

        @ FATAL path. fatalFramer writes straight out of comDriver.sendSync, bypassing
        @ the queued downlink pipeline, because a FATAL report has to reach the ground
        @ from inside the assert hook -- with the rest of the system already declared
        @ untrustworthy.
        connections FaultProtection {
            downlink.FatalAnnounce -> fatalHandler.FatalReceive
            fatalFramer.drvSendOut -> comDriver.sendSync
        }

        connections Link {
            comDriver.ready -> framer.drvConnected

            # Telemetry downlink pipeline
            downlink.PktSend        -> framer.comPacketQueueIn
            tlm.pktSendOut          -> framer.comPacketQueueIn
            framer.drvSendOut       -> comDriver.$send
            comDriver.sendReturnOut -> framer.drvReturnIn

            # TX
            comDriver.dmaQueueOut[Samd21.UsartDriver.DmaChannel.TX]   -> dmaDriver.sendTransactionIn[DmaChannel.USART0_UPL_DWN_TX]
            dmaDriver.transactionIsrOut[DmaChannel.USART0_UPL_DWN_TX] -> comDriver.dmaReplyIn[Samd21.UsartDriver.DmaChannel.TX]

            # RX
            comDriver.dmaQueueOut[Samd21.UsartDriver.DmaChannel.RX]   -> dmaDriver.sendTransactionIn[DmaChannel.USART0_UPL_DWN_RX]
            dmaDriver.transactionIsrOut[DmaChannel.USART0_UPL_DWN_RX] -> comDriver.dmaReplyIn[Samd21.UsartDriver.DmaChannel.RX]
            comDriver.dmaRxCircular                                   -> dmaDriver.linkToFrontIn[DmaChannel.USART0_UPL_DWN_RX]
            comDriver.dmaRxRead                                       -> dmaDriver.readWritebackIn[DmaChannel.USART0_UPL_DWN_RX]
        }

        connections Uplink {
            # ComDriver <-> ComStub (Uplink)
            comDriver.$recv             -> comStub.drvReceiveIn
            comStub.drvReceiveReturnOut -> comDriver.recvReturnIn

            # ComStub <-> FrameAccumulator (Uplink)
            comStub.dataOut                -> frameAccumulator.dataIn
            frameAccumulator.dataReturnOut -> comStub.dataReturnIn

            # (Incoming) ComInterface <-> FrameAccumulator connections shall be established by the user
            # FrameAccumulator buffer allocations
            frameAccumulator.bufferDeallocate -> commsBufferManager.bufferDeallocate[0]
            frameAccumulator.bufferAllocate   -> commsBufferManager.bufferAllocate[0]
            # FrameAccumulator <-> Deframer
            frameAccumulator.dataOut -> deframer.dataIn
            deframer.dataReturnOut   -> frameAccumulator.dataReturnIn
            # Deframer <-> Router
            deframer.dataOut           -> fprimeRouter.dataIn
            fprimeRouter.dataReturnOut -> deframer.dataReturnIn

            # Router <-> CmdDispatcher
            fprimeRouter.commandOut -> cmdDisp.seqCmdBuff
            cmdDisp.seqCmdStatus    -> fprimeRouter.cmdResponseIn
        }

        # DMA Engine drives the SPI transfers
        connections Spi {
            spiDriver.dmaTransactionOut[Samd21.SpiDriver.DmaChannel.MOSI]      -> dmaDriver.sendTransactionIn[DmaChannel.SERCOM1_SPI_MOSI]
            spiDriver.dmaTransactionAbortOut[Samd21.SpiDriver.DmaChannel.MOSI] -> dmaDriver.abortTransactionIn[DmaChannel.SERCOM1_SPI_MOSI]
            dmaDriver.transactionIsrOut[DmaChannel.SERCOM1_SPI_MOSI]           -> spiDriver.dmaReplyIn[Samd21.SpiDriver.DmaChannel.MOSI]

            spiDriver.dmaTransactionOut[Samd21.SpiDriver.DmaChannel.MISO]      -> dmaDriver.sendTransactionIn[DmaChannel.SERCOM1_SPI_MISO]
            spiDriver.dmaTransactionAbortOut[Samd21.SpiDriver.DmaChannel.MISO] -> dmaDriver.abortTransactionIn[DmaChannel.SERCOM1_SPI_MISO]
            dmaDriver.transactionIsrOut[DmaChannel.SERCOM1_SPI_MISO]           -> spiDriver.dmaReplyIn[Samd21.SpiDriver.DmaChannel.MISO]

            # Software chip select of slot 0 (the F-RAM)
            spiDriver.chipSelectGpioOut[0] -> csPA18.gpioWrite
        }

        @ UUT on SPI slot 0; the harness on both client indices (FramDriver.fpp `match` keeps request and
        @ completion on the same index)
        connections Fram {
            framDriver.spiWriteRead          -> spiDriver.SpiWriteRead[0]
            spiDriver.SpiWriteReadComplete[0] -> framDriver.spiWriteReadComplete

            framTester.read[0]               -> framClient0.readIn
            framClient0.readOut              -> framDriver.read[0]
            framDriver.readComplete[0]       -> framClient0.readCompleteIn
            framClient0.readCompleteOut      -> framTester.readComplete[0]
            framTester.write[0]              -> framClient0.writeIn
            framClient0.writeOut             -> framDriver.write[0]
            framDriver.writeComplete[0]      -> framClient0.writeCompleteIn
            framClient0.writeCompleteOut     -> framTester.writeComplete[0]

            framTester.read[1]               -> framClient1.readIn
            framClient1.readOut              -> framDriver.read[1]
            framDriver.readComplete[1]       -> framClient1.readCompleteIn
            framClient1.readCompleteOut      -> framTester.readComplete[1]
            framTester.write[1]              -> framClient1.writeIn
            framClient1.writeOut             -> framDriver.write[1]
            framDriver.writeComplete[1]      -> framClient1.writeCompleteIn
            framClient1.writeCompleteOut     -> framTester.writeComplete[1]
        }

        connections Pins {
            # Input. gpioInterrupt is emitted from the EIC ISR -- see
            # CuriosityReference/GpioIn/docs/sdd.md.
            pinIn.readOut        -> inPA23.gpioRead
            inPA23.gpioInterrupt -> pinIn.transitionIn

            # Output
            pinOut.write -> outPA25.gpioWrite
        }
    }
}
