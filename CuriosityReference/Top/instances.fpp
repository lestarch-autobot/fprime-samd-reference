module CuriosityReference {
    # ----------------------------------------------------------------------
    # Queued component instances
    # ----------------------------------------------------------------------

    # None. This deployment is bare-metal with no RTOS, so there are no queued
    # and no active component instances -- every instance below is passive and
    # runs on the caller's stack. See Main.cpp for the single main loop.

    # ----------------------------------------------------------------------
    # Passive component instances
    # ----------------------------------------------------------------------

    enum FixedBaseIds {
        @ Common base id to use for components that have no dictionary items
        NO_DICTIONARY = 0xFFFF
    }

    instance dmaDriver: Samd21.DmaDriver base id 0x3000
    instance comDriver: Samd21.UsartDriver base id 0x3020 {
        phase Fpp.ToCpp.Phases.configComponents """
        // Configure GPIO pins for SERCOM5 (routed to the on-board nEDBG virtual COM port)
        Samd21::PinMux::configure(PINMUX_PA22D_SERCOM5_PAD0);  // PA22 -> SERCOM5 PAD[0] (TX -> CDC RX)
        Samd21::PinMux::configure(PINMUX_PB22D_SERCOM5_PAD2);  // PB22 -> SERCOM5 PAD[2] (RX <- CDC TX)

        comDriver.configure(
        Samd21::SercomKind::SERCOM_5,
        Samd21::UsartDriver::RxPinOut::PAD2,
        Samd21::UsartDriver::TxPinOut::PAD0,
        Samd21::UsartDriver::ClockMode::INTERNAL,
        Samd21::UsartDriver::CommunicationMode::ASYNC,
        Samd21::UsartDriver::BaudRate::BAUD_115200,
        Samd21::UsartDriver::DataOrder::LSB_FIRST,
        Samd21::UsartDriver::DataBits::BITS_8,
        Samd21::UsartDriver::StopBits::ONE,
        Samd21::UsartDriver::Parity::NONE
        );
        """
    }

    @ Chip select of the F-RAM (software CS, PA18), opened as an output before spiDriver.configure()
    instance csPA18: Samd21.GpioDriver base id FixedBaseIds.NO_DICTIONARY {
        phase Fpp.ToCpp.Phases.configComponents """
        csPA18.configureOutput(
            Samd21::GpioDriver::Group::PA,
            Samd21::GpioDriver::Pin::PIN_18
        );
        """
    }

    @ SPI host on SERCOM1 for the F-RAM: MOSI PA16/PAD0, SCK PA17/PAD1, MISO PA19/PAD3, 4 MHz, mode 0, software CS
    instance spiDriver: Samd21.SpiDriver base id 0x3030 {
        phase Fpp.ToCpp.Phases.configComponents """
        // Configure GPIO pins for SERCOM1
        Samd21::PinMux::configure(PINMUX_PA16C_SERCOM1_PAD0);  // PA16 -> SERCOM1 PAD[0] (MOSI)
        Samd21::PinMux::configure(PINMUX_PA17C_SERCOM1_PAD1);  // PA17 -> SERCOM1 PAD[1] (SCK)
        Samd21::PinMux::configure(PINMUX_PA19C_SERCOM1_PAD3);  // PA19 -> SERCOM1 PAD[3] (MISO)

        spiDriver.configure(
        Samd21::SercomKind::SERCOM_1,
        /* baud kHz */ 4000,
        Samd21::SpiDriver::DataOrder::MSB,
        Samd21::SpiDriver::ClockPolarity::IdleLow,
        Samd21::SpiDriver::ClockPhase::SampleOnLeadingSck,
        Samd21::SpiDriver::DataInPinout::PAD3,
        Samd21::SpiDriver::DataOutPinout::MOSI_0_SCK_1_CS_2,
        Samd21::SpiDriver::RunInStandby::ENABLED,
        Samd21::SpiDriver::HardwareChipSelect::DISABLED
        );
        """
    }

    instance comStub: Svc.ComStub base id FixedBaseIds.NO_DICTIONARY
    instance fatalFramer: Samd21.SyncFramer base id FixedBaseIds.NO_DICTIONARY
    instance downlink: Samd21.PassiveDownlink base id FixedBaseIds.NO_DICTIONARY
    instance cycler: Svc.PassiveCycler base id FixedBaseIds.NO_DICTIONARY

    @ Realtime clock driven rate driver, puts the CPU to sleep in-between cycles
    instance rateDriver: Samd21.RtcDriver base id 0x1000 {
        @ Cycle the clock @ 8Hz
        phase Fpp.ToCpp.Phases.configComponents """
        rateDriver.configure(
        Samd21::RtcDriver::ClockSource::UltraLowPowerOscillator,
        Samd21::RtcDriver::TickRate::TICK_8_HZ
        );
        """

        @ Enable the RTC peripheral and the unmask the NVIC
        phase Fpp.ToCpp.Phases.startTasks """
        rateDriver.enable();
        """
    }

    instance fatalHandler: Samd21.FatalHandler base id 0
    instance rg8Hz: Svc.PassiveRateGroup base id 0x1020  @< 8Hz frequency
    instance rg1Hz: Svc.PassiveRateGroup base id 0x1030  @< 1Hz frequency
    instance rg10s: Svc.PassiveRateGroup base id 0x1040  @< 10s period

    instance timeHandler: Samd21.ProcTime base id 0x2000
    instance rateGroupDriver: Samd21.PassiveRateGroupDriver base id 0x2010
    instance cmdDisp: Samd21.StaticCmdDispatcher base id 0x2020
    instance tlm: Samd21.StaticTlmPacketizer base id 0x2030

    # ----------------------------------------------------------------------
    # Uplink Components
    # ----------------------------------------------------------------------

    instance frameAccumulator: Svc.FrameAccumulator base id 0xAA00 {
        phase Fpp.ToCpp.Phases.configObjects """
        Svc::FrameDetectors::FprimeFrameDetector frameDetector;
        """

        phase Fpp.ToCpp.Phases.configComponents """
        frameAccumulator.configure(
            ConfigObjects::CuriosityReference_frameAccumulator::frameDetector,
            1,
            Allocation::frameAccumulatorAllocator,
            /* frame buffer size */ 128
        );
        """

        phase Fpp.ToCpp.Phases.tearDownComponents """
        frameAccumulator.cleanup();
        """
    }

    instance commsBufferManager: Svc.StaticMemory base id 0xAA10

    instance deframer: Svc.FprimeDeframer base id 0xAA20
    instance fprimeRouter: Samd21.FprimeRouter base id 0xAA30
    instance framer: Samd21.Framer base id 0xAA40
    instance fwHealth: CuriosityReference.FrameworkHealth base id 0xAA50

    # ----------------------------------------------------------------------
    # GPIO Components
    #
    # pinIn/inPA23 and pinOut/outPA25 are pairs: the Curiosity* component owns
    # the commands and events, the Samd21.GpioDriver instance owns the pin. The
    # base ids are deliberately tight (4 / 1 / 4 ids of headroom) -- adding a
    # command or event to GpioIn/GpioOut may require re-spacing them.
    # ----------------------------------------------------------------------

    instance pinIn: CuriosityReference.GpioIn base id 0xBB00

    instance inPA23: Samd21.GpioDriver base id 0xBB04 {
        phase Fpp.ToCpp.Phases.startTasks """
        inPA23.configureInput(
            Samd21::GpioDriver::Group::PA,
            Samd21::GpioDriver::Pin::PIN_23,
            Samd21::Gpio::InputPullMode::PULL_UP,
            Samd21::GpioDriver::ExternalInterruptMode::BOTH
        );
        """
    }

    instance pinOut: CuriosityReference.GpioOut base id 0xBB05

    instance outPA25: Samd21.GpioDriver base id 0xBB09 {
        phase Fpp.ToCpp.Phases.startTasks """
        outPA25.configureOutput(
            Samd21::GpioDriver::Group::PA,
            Samd21::GpioDriver::Pin::PIN_25
        );
        """
    }

    # ----------------------------------------------------------------------
    # F-RAM Components (system test build for Fram.FramDriver)
    # ----------------------------------------------------------------------

    @ UUT: generic F-RAM driver over the asynchronous SPI driver, slot 0
    instance framDriver: Fram.FramDriver base id 0xDD00 {
        @ startTasks runs after configComponents has configured spiDriver; RDID then RDSR, once
        phase Fpp.ToCpp.Phases.startTasks """
        framDriver.startup();
        """
    }

    @ Harness: drives framDriver.read/write on two client indices from ground commands
    instance framTester: CuriosityReference.FramTester base id 0xCC00

    @ Pass-through clients, one per index (FPP match rule: separate instance per index of a matched array)
    instance framClient0: CuriosityReference.FramClient base id FixedBaseIds.NO_DICTIONARY
    instance framClient1: CuriosityReference.FramClient base id FixedBaseIds.NO_DICTIONARY
}
