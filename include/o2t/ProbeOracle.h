#pragma once

#include <algorithm>
#include <string>
#include <vector>

namespace cv {

enum class ProbeOracleStatus {
  NotInstrumented,
  Matched,
  Mismatch,
};

struct ProbeOracleResult {
  std::vector<std::string> expectedMarkers;
  std::vector<std::string> observedMarkers;
  std::vector<std::string> missingMarkers;
  std::vector<std::string> unexpectedMarkers;
  ProbeOracleStatus status = ProbeOracleStatus::NotInstrumented;
};

inline bool markerListContains(const std::vector<std::string> &markers,
                               const std::string &marker) {
  return std::find(markers.begin(), markers.end(), marker) != markers.end();
}

inline std::vector<std::string> missingMarkers(
    const std::vector<std::string> &expected,
    const std::vector<std::string> &observed) {
  std::vector<std::string> missing;
  for (const std::string &marker : expected) {
    if (!markerListContains(observed, marker) &&
        !markerListContains(missing, marker)) {
      missing.push_back(marker);
    }
  }
  return missing;
}

inline std::vector<std::string> unexpectedMarkers(
    const std::vector<std::string> &expected,
    const std::vector<std::string> &observed) {
  std::vector<std::string> unexpected;
  for (const std::string &marker : observed) {
    if (!markerListContains(expected, marker) &&
        !markerListContains(unexpected, marker)) {
      unexpected.push_back(marker);
    }
  }
  return unexpected;
}

inline bool containsAllMarkers(const std::vector<std::string> &observed,
                               const std::vector<std::string> &expected) {
  return missingMarkers(expected, observed).empty();
}

inline const char *toString(ProbeOracleStatus status) {
  switch (status) {
  case ProbeOracleStatus::NotInstrumented:
    return "not-instrumented";
  case ProbeOracleStatus::Matched:
    return "matched";
  case ProbeOracleStatus::Mismatch:
    return "mismatch";
  }
  return "mismatch";
}

inline ProbeOracleResult evaluateProbeOracle(
    std::vector<std::string> expected, std::vector<std::string> observed,
    bool allowExtraObserved = false) {
  ProbeOracleResult result;
  result.expectedMarkers = std::move(expected);
  result.observedMarkers = std::move(observed);

  if (result.observedMarkers.empty()) {
    result.status = ProbeOracleStatus::NotInstrumented;
    result.missingMarkers = result.expectedMarkers;
    return result;
  }

  result.missingMarkers =
      missingMarkers(result.expectedMarkers, result.observedMarkers);
  if (!allowExtraObserved) {
    result.unexpectedMarkers =
        unexpectedMarkers(result.expectedMarkers, result.observedMarkers);
  }

  result.status =
      result.missingMarkers.empty() && result.unexpectedMarkers.empty()
          ? ProbeOracleStatus::Matched
          : ProbeOracleStatus::Mismatch;
  return result;
}

} // namespace cv
