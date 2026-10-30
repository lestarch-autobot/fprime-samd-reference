#####
# 'CuriosityReference' Deployment:
#
# This registers the 'CuriosityReference' deployment to the build system: the four
# deployment-local components, the topology, and the SAMD21 executable.
#
# Custom components that have not been added at the project-level should be added to
# the component list below.
#
#####

###
# Components
#
# Registered BEFORE restrict_platforms() below, deliberately. restrict_platforms() is a
# macro that does a bare return() from this file when the platform does not match, so
# anything after it is invisible to a native build -- including these components and
# therefore including their unit tests. None of the four depends on the SAMD21: they
# talk to framework port types (Svc.Sched, Drv.GpioRead, Drv.I2cWriteReadRequest), not
# to fprime-samd drivers, so they compile and can be unit tested on the host.
###
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/FrameworkHealth/")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/GpioOut/")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/GpioIn/")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/FramTester/")
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/FramClient/")

# Add custom components to this specific deployment here
# add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/MyComponent/")

# Everything below this line is only compatible with the SAMD21 platform: it assumes the
# fprime-samd toolchain, the bare-metal MCU library, the SAMD21 linker script, and
# finalize_samd21_executable(). The topology instantiates fprime-samd drivers, so it
# cannot be built for the host either.
restrict_platforms(Samd21)

###
# Topology
###
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Top/")

# CMake does not predefine the WHOLE_ARCHIVE link feature for this bare-metal toolchain, so
# teach it the GNU ld spelling. Must be set before the executable target is created below.
set(CMAKE_CXX_LINK_LIBRARY_USING_WHOLE_ARCHIVE
    "-Wl,--whole-archive" "<LINK_ITEM>" "-Wl,--no-whole-archive")
set(CMAKE_CXX_LINK_LIBRARY_USING_WHOLE_ARCHIVE_SUPPORTED TRUE)

register_fprime_deployment(
    SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/Main.cpp"
    DEPENDS
        ${FPRIME_CURRENT_MODULE}_Top
    CHOOSES_IMPLEMENTATIONS
        # No RTOS, and no active components, so nothing ever blocks on a semaphore.
        # The stub satisfies the Os_CountingSemaphore interface without pulling in an
        # implementation that would need a scheduler.
        Os_CountingSemaphore_Stub
)

target_link_libraries("${FPRIME_CURRENT_MODULE}" PRIVATE
    fprime_samd_mcu
)

# Force strong ISR handlers from driver libraries to override weak aliases in vector table
# The --whole-archive forces all objects in the driver libraries to be linked, ensuring
# the strong RTC_Handler, DMAC_Handler and EIC_Handler override the weak aliases.
#
# Without this, the linker sees no undefined reference to (say) DMAC_Handler -- the vector
# table already resolves it to the weak default -- so it never pulls the driver's object
# file in, and every DMA completion interrupt silently lands in the default handler.
target_link_libraries("${FPRIME_CURRENT_MODULE}" PRIVATE
    -Wl,--whole-archive
    fprime-samd_Drv_RtcDriver
    fprime-samd_Drv_DmaDriver
    fprime-samd_Drv_GpioDriver
    fprime-samd_Drv_SpiDriver
    fprime-samd_Drv_Types
    -Wl,--no-whole-archive
)

# The topology library is whole-archived too, for a related archive-ordering reason.
# libCuriosityReference_Top.a supplies definitions that fprime-samd libraries *later* in
# the link line depend on: Samd21::StaticTlmPacketizer::writePoint and
# Samd21::StaticCmdDispatcher::seqCmd_helper (emitted by the two build autocoders) and
# Samd21::sendFatalPacket / sendBailFrame (called by the assert hook). A normal archive
# is only searched for symbols that are already undefined when the linker reaches it, so
# without this those members would never be extracted.
set_property(TARGET "${FPRIME_CURRENT_MODULE}"
    PROPERTY "LINK_LIBRARY_OVERRIDE_${FPRIME_CURRENT_MODULE}_Top" WHOLE_ARCHIVE)

# Supplied by lib/fprime-samd/cmake/toolchain/samd21-bare-toolchain.cmake. Attaches the
# linker script, then post-processes the ELF into .bin/.hex and prints the flash/RAM
# size report. Note that it operates on ${FPRIME_CURRENT_MODULE} regardless of what is
# passed to it; the argument is documentation.
finalize_samd21_executable("${FPRIME_CURRENT_MODULE}")
