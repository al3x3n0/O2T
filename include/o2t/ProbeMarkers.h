#pragma once

#include "o2t/GeneratorConfig.h"
#include "o2t/GeneratedProbeMarkerMap.h"
#include "o2t/ProbeBackend.h"

#include <algorithm>
#include <string>
#include <vector>

namespace cv {

inline std::vector<std::string> markerStringsFor(
    const PassProbeCoverage &coverage) {
  std::vector<std::string> markers;
  for (const ProbeMarkerMetadata &metadata : kProbeMarkerMetadata) {
    if (coverage.*metadata.coverage) {
      markers.emplace_back(metadata.marker);
    }
  }
  return markers;
}

inline std::vector<std::string> markerStringsForConfig(
    const GeneratorConfig &rawConfig) {
  const GeneratorConfig config = normalizeConfig(rawConfig);
  return markerStringsFor(runAbstractProbeBackend(config).coverage);
}

inline bool containsMarker(const std::vector<std::string> &markers,
                           const std::string &marker) {
  return std::find(markers.begin(), markers.end(), marker) != markers.end();
}

inline bool preservesMarkers(const GeneratorConfig &config,
                             const std::vector<std::string> &requiredMarkers) {
  const std::vector<std::string> markers = markerStringsForConfig(config);
  for (const std::string &required : requiredMarkers) {
    if (!containsMarker(markers, required)) {
      return false;
    }
  }
  return true;
}

} // namespace cv
