// Pass-through F-RAM client (see FramClient.fpp)
#ifndef CuriosityReference_FramClient_HPP
#define CuriosityReference_FramClient_HPP

#include "CuriosityReference/FramClient/FramClientComponentAc.hpp"

namespace CuriosityReference {

class FramClient final : public FramClientComponentBase {
  public:
    // cppcheck-suppress noExplicitConstructor
    FramClient(const char* const compName);
    ~FramClient();

  private:
    Fram::Status readIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer) override;
    Fram::Status writeIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer) override;
    void readCompleteIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) override;
    void writeCompleteIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) override;
};

}  // namespace CuriosityReference

#endif
