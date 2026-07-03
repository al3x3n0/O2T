#pragma once

#include "o2t/GeneratorConfig.h"

#include <string>

namespace cv {

struct GeneratedIR {
  std::string moduleText;
  PatternCoverage coverage;
};

GeneratedIR generateIR(const GeneratorConfig &rawConfig);

} // namespace cv
