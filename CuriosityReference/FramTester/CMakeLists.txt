# Test harness for Fram.FramDriver (system test plan QUAL-FRAM). Build dependencies are only
# the Fram port/type definitions from fprime-devices; the one SAMD21-specific piece is the
# MCU serial-number word read in FramTester.cpp (McuSerial0..3 telemetry), by absolute address.
register_fprime_library(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/FramTester.fpp"
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/FramTester.cpp"
    DEPENDS
        fprime-devices_Fram_Ports
        fprime-devices_Fram_Types
)
