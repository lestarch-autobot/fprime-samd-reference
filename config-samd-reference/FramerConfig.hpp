/*
 * FramerConfig.hpp:
 *
 * Configuration settings for the SAMD21 Framer component.
 */

#ifndef SAMD21_FRAMER_CFG_HPP_
#define SAMD21_FRAMER_CFG_HPP_

namespace Samd21 {
enum FramerConfig {
    //! Maximum size for a single transmit buffer (bytes)
    //! Buffer contains: frame header + multiple accumulated ComBuffers + frame trailer
    //! Larger buffers allow more packet accumulation before flush
    FRAMER_TX_BUFFER_SIZE = 256,

    //! Per-APID Space Packet sequence count slots for Samd21::TmFramer. Kept at
    //! fprime-samd's default; see the full rationale in
    //! lib/fprime-samd/default/samd-config/FramerConfig.hpp.
    //!
    //! This deployment does not link TmFramer at all -- downlink is Samd21::Framer over
    //! Svc::FprimeProtocol, not CCSDS -- but this whole file SHADOWS the library default
    //! rather than extending it, so a constant omitted here is simply undeclared for
    //! everything built in this repo, including fprime-samd's own TmFramer unit test.
    //! Omitting it is what used to break that test's build. When adding a FramerConfig
    //! constant upstream, add it here too.
    MAX_TRACKED_APIDS = 4,
};
}  // namespace Samd21

#endif
