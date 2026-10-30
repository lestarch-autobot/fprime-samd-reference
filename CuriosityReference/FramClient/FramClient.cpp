#include "CuriosityReference/FramClient/FramClient.hpp"

namespace CuriosityReference {

FramClient ::FramClient(const char* const compName) : FramClientComponentBase(compName) {}

FramClient ::~FramClient() {}

Fram::Status FramClient ::readIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer) {
    return this->readOut_out(0, offset, buffer);
}

Fram::Status FramClient ::writeIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer) {
    return this->writeOut_out(0, offset, buffer);
}

void FramClient ::readCompleteIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) {
    this->readCompleteOut_out(0, offset, buffer, status);
}

void FramClient ::writeCompleteIn_handler(FwIndexType portNum, U32 offset, Fw::Buffer& buffer, const Fram::Status& status) {
    this->writeCompleteOut_out(0, offset, buffer, status);
}

}  // namespace CuriosityReference
