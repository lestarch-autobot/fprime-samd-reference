# Test harness for Fram.FramDriver (system test plan QUAL-FRAM). Depends only on the
# Fram port/type definitions from fprime-devices; nothing SAMD21-specific.
register_fprime_library(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/FramTester.fpp"
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/FramTester.cpp"
    DEPENDS
        fprime-devices_Fram_Ports
        fprime-devices_Fram_Types
)
