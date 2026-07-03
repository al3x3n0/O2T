#include "o2t/GeneratorConfig.h"
#include "o2t/ProbeMarkers.h"
#include "o2t/ProbeOracle.h"

#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

namespace {

struct Options {
  std::optional<std::string> configPath;
  std::optional<std::string> observedPath;
  bool allowExtraObserved = false;
};

void usage(std::ostream &out) {
  out << "usage: cv-probe-oracle --config FILE [--observed FILE] "
         "[--allow-extra-observed]\n";
}

std::optional<Options> parseArgs(int argc, char **argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string arg = argv[index];
    if (arg == "--help" || arg == "-h") {
      usage(std::cout);
      return std::nullopt;
    }
    if (arg == "--config" && index + 1 < argc) {
      options.configPath = argv[++index];
      continue;
    }
    if (arg == "--observed" && index + 1 < argc) {
      options.observedPath = argv[++index];
      continue;
    }
    if (arg == "--allow-extra-observed") {
      options.allowExtraObserved = true;
      continue;
    }
    std::cerr << "unknown or incomplete argument: " << arg << "\n";
    usage(std::cerr);
    return std::nullopt;
  }

  if (!options.configPath.has_value()) {
    std::cerr << "--config is required\n";
    usage(std::cerr);
    return std::nullopt;
  }

  return options;
}

bool loadConfig(const std::string &path, cv::GeneratorConfig &config) {
  std::ifstream input(path);
  if (!input) {
    std::cerr << "failed to open config: " << path << "\n";
    return false;
  }

  std::string error;
  if (!cv::parseConfig(input, config, error)) {
    std::cerr << "failed to parse config: " << error << "\n";
    return false;
  }
  config = cv::normalizeConfig(config);
  return true;
}

std::vector<std::string> loadObservedMarkers(
    const std::optional<std::string> &path) {
  std::vector<std::string> markers;
  if (!path.has_value()) {
    return markers;
  }

  std::ifstream input(*path);
  if (!input) {
    return markers;
  }

  std::string line;
  while (std::getline(input, line)) {
    if (!line.empty()) {
      markers.push_back(line);
    }
  }
  return markers;
}

void printMarkers(const std::vector<std::string> &markers) {
  for (std::size_t index = 0; index < markers.size(); ++index) {
    if (index != 0) {
      std::cout << ',';
    }
    std::cout << markers[index];
  }
}

} // namespace

int main(int argc, char **argv) {
  const auto options = parseArgs(argc, argv);
  if (!options.has_value()) {
    return argc == 2 && std::string(argv[1]) == "--help" ? 0 : 1;
  }

  cv::GeneratorConfig config{};
  if (!loadConfig(*options->configPath, config)) {
    return 1;
  }

  const std::vector<std::string> expectedMarkers =
      cv::markerStringsForConfig(config);
  const std::vector<std::string> observedMarkers =
      loadObservedMarkers(options->observedPath);
  const cv::ProbeOracleResult oracle = cv::evaluateProbeOracle(
      expectedMarkers, observedMarkers, options->allowExtraObserved);

  std::cout << "expected_markers=";
  printMarkers(oracle.expectedMarkers);
  std::cout << "\nobserved_markers=";
  printMarkers(oracle.observedMarkers);
  std::cout << "\noracle_status=" << cv::toString(oracle.status);
  std::cout << "\nmissing_markers=";
  printMarkers(oracle.missingMarkers);
  std::cout << "\nunexpected_markers=";
  printMarkers(oracle.unexpectedMarkers);
  std::cout << "\n";

  return 0;
}
