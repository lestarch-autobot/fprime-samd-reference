####
# config-samd-reference/CMakeLists.txt
####

# Native unit-test builds link the POSIX OSAL, whose Os handles are larger than the
# SAMD21-tuned FW_*_HANDLE_MAX_SIZE maxima in PlatformCfg.fpp. Fall back to the F Prime
# defaults under BUILD_TESTING and apply the trimmed handle sizes only to flight builds.
if(BUILD_TESTING)
    set(PLATFORM_CFG_OVERRIDE "")
else()
    set(PLATFORM_CFG_OVERRIDE "${CMAKE_CURRENT_LIST_DIR}/PlatformCfg.fpp")
endif()

# Same treatment, different reason: this override SHADOWS
# lib/fprime-samd/default/samd-config/FramerConfig.hpp rather than extending it, and this
# repository is the harness fprime-samd's own CI builds in. A constant omitted here is
# therefore undeclared for the library's unit tests too -- which is how adding
# MAX_TRACKED_APIDS upstream broke them. Under BUILD_TESTING the library's tests see the
# library's defaults, which is what they are written against. Flight builds keep the
# trimmed FRAMER_TX_BUFFER_SIZE, and must still declare everything the cross `all` target
# compiles, TmFramer included.
if(BUILD_TESTING)
    set(SAMD_FRAMER_CFG_OVERRIDE "")
else()
    set(SAMD_FRAMER_CFG_OVERRIDE "${CMAKE_CURRENT_LIST_DIR}/FramerConfig.hpp")
endif()

register_fprime_config(
        config-samd-reference
    CONFIGURATION_OVERRIDES
        # --- overrides lib/fprime/default/config ---
        "${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/AsyncSpiCfg.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/ComCfg.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/FpConfig.fpp"
        "${CMAKE_CURRENT_LIST_DIR}/FpConstants.fpp"
        ${PLATFORM_CFG_OVERRIDE}
        "${CMAKE_CURRENT_LIST_DIR}/FpConfig.h"
        "${CMAKE_CURRENT_LIST_DIR}/BufferManagerComponentImplCfg.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/PassiveRateGroupCfg.hpp"
        "${CMAKE_CURRENT_LIST_DIR}/StaticMemoryConfig.hpp"
        # --- overrides lib/fprime-samd/default/samd-config ---
        ${SAMD_FRAMER_CFG_OVERRIDE}
        "${CMAKE_CURRENT_LIST_DIR}/Samd21StaticTlmPacketizerConfig.fpp"
    INTERFACE
)
