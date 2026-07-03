#include "o2t/GeneratorConfig.h"
#include "o2t/IRTextGenerator.h"

#include <cassert>
#include <string>

int main() {
  const cv::GeneratedIR generated = cv::generateIR(cv::defaultConfig());
  assert(generated.moduleText.find("o2t-generated") != std::string::npos);
  assert(generated.coverage.hasAddZero);
  return 0;
}
