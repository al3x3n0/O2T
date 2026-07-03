#pragma once

#include "o2t/GeneratorConfig.h"
#include "o2t/ProbeMarkers.h"

#include <cstdint>
#include <vector>

namespace cv {

inline bool tryCandidate(GeneratorConfig &current, GeneratorConfig candidate,
                         const std::vector<std::string> &requiredMarkers) {
  candidate = normalizeConfig(candidate);
  if (preservesMarkers(candidate, requiredMarkers)) {
    current = candidate;
    return true;
  }
  return false;
}

inline void reduceByteField(GeneratorConfig &current, std::uint8_t GeneratorConfig::*field,
                            const std::vector<std::uint8_t> &candidates,
                            const std::vector<std::string> &requiredMarkers) {
  GeneratorConfig best = current;
  std::uint8_t bestValue = current.*field;
  for (std::uint8_t value : candidates) {
    GeneratorConfig candidate = current;
    candidate.*field = value;
    candidate = normalizeConfig(candidate);
    if (!preservesMarkers(candidate, requiredMarkers)) {
      continue;
    }
    if (candidate.*field < bestValue) {
      best = candidate;
      bestValue = candidate.*field;
    }
  }
  current = best;
}

inline void reduceIntField(GeneratorConfig &current, std::int32_t GeneratorConfig::*field,
                           const std::vector<std::int32_t> &candidates,
                           const std::vector<std::string> &requiredMarkers) {
  for (std::int32_t value : candidates) {
    if (current.*field == value) {
      return;
    }
    GeneratorConfig candidate = current;
    candidate.*field = value;
    candidate = normalizeConfig(candidate);
    if (!preservesMarkers(candidate, requiredMarkers)) {
      continue;
    }
    if (candidate.*field == value) {
      current = candidate;
      return;
    }
  }
}

inline GeneratorConfig reduceConfig(
    const GeneratorConfig &rawConfig,
    const std::vector<std::string> &requiredMarkers) {
  GeneratorConfig current = normalizeConfig(rawConfig);

  reduceByteField(current, &GeneratorConfig::featureBits, {0, 1, 2, 3},
                  requiredMarkers);
  reduceIntField(current, &GeneratorConfig::constA, {0, 1, -1},
                 requiredMarkers);
  reduceIntField(current, &GeneratorConfig::constB, {0, 1, -1},
                 requiredMarkers);
  reduceByteField(current, &GeneratorConfig::loadUseMode, {0, 1, 2},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::storeMode, {0, 1, 2},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::pointerMode, {0, 1, 2},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::memoryShape, {0, 1, 2, 3, 4, 5},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::loopUseMode, {0, 1, 2},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::inductionMode, {0, 1, 2},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::loopTripMode, {0, 1, 2},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::loopShape, {0, 1, 2, 3, 4},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::vectorShape,
                  {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
                   13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::globalShape, {0, 1, 2, 3},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::predicate, {0, 1, 2, 3},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::extraOpcode, {0, 1, 2, 3, 4, 5},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::rhsMode, {0, 1, 2, 3},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::arithOpcode, {0, 1, 2, 3, 4, 5},
                  requiredMarkers);
  reduceByteField(current, &GeneratorConfig::shape, {0, 1, 2, 3, 4},
                  requiredMarkers);

  return normalizeConfig(current);
}

} // namespace cv
