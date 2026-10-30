# Pass-through F-RAM client for the FramDriver system-test build (see FramClient.fpp)
register_fprime_library(
    AUTOCODER_INPUTS
        "${CMAKE_CURRENT_LIST_DIR}/FramClient.fpp"
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/FramClient.cpp"
    DEPENDS
        fprime-devices_Fram_Ports
        fprime-devices_Fram_Types
)
