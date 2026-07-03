#pragma once

#include "o2t/KleeCompat.h"

#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

namespace cv {

inline std::vector<std::string> &mutablePassProbeEvents() {
  static thread_local std::vector<std::string> events;
  return events;
}

inline void clearPassProbeEvents() { mutablePassProbeEvents().clear(); }

inline const std::vector<std::string> &passProbeEvents() {
  return mutablePassProbeEvents();
}

inline void recordPassProbeEvent(const char *marker) {
#if !(defined(O2T_WITH_KLEE) || defined(COMPILERVERIF_WITH_KLEE))
  mutablePassProbeEvents().emplace_back(marker);
  const char *logPath = std::getenv("O2T_PASS_PROBE_LOG");
  if (!logPath) {
    logPath = std::getenv("COMPILERVERIF_PASS_PROBE_LOG");
  }
  if (logPath) {
    std::ofstream output(logPath, std::ios::app);
    if (output) {
      output << marker << '\n';
    }
  }
#else
  (void)marker;
#endif
}

inline void passProbe(const char *marker) {
  cover(marker, true);
  recordPassProbeEvent(marker);
}

inline bool passProbeIf(const char *marker, bool condition) {
  cover(marker, condition);
  if (condition) {
    recordPassProbeEvent(marker);
  }
  return condition;
}

} // namespace cv

#define CV_PASS_PROBE(marker) ::cv::passProbe(marker)
#define CV_PASS_PROBE_IF(marker, condition)                                  \
  ::cv::passProbeIf(marker, static_cast<bool>(condition))
