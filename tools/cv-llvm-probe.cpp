#include "o2t/GeneratorConfig.h"
#include "o2t/ProbeBackend.h"
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
  bool requireObserved = false;
  bool allowExtraObserved = false;
};

void usage(std::ostream &out) {
  out << "usage: cv-llvm-probe --config FILE [--require-observed] "
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
    if (arg == "--require-observed") {
      options.requireObserved = true;
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

  const cv::ProbeBackendResult result = cv::runLLVMProbeBackend(config);
  if (!result.available) {
    std::cerr << "LLVM backend unavailable or rejected config: "
              << *options->configPath << "\n";
    return 1;
  }

  std::cout << "status=ok\n";
  std::cout << "category=" << cv::probeCategoryForConfig(config) << "\n";
  std::cout << "passes=" << cv::probePipelineForConfig(config) << "\n";
  const std::vector<std::string> expectedMarkers =
      cv::markerStringsFor(result.coverage);
  const cv::ProbeOracleResult oracle = cv::evaluateProbeOracle(
      expectedMarkers, result.observedMarkers, options->allowExtraObserved);
  std::cout << "oracle_status=" << cv::toString(oracle.status) << "\n";
  std::cout << "expected_markers=";
  printMarkers(oracle.expectedMarkers);
  std::cout << "\n";
  std::cout << "observed_markers=";
  printMarkers(oracle.observedMarkers);
  std::cout << "\n";
  std::cout << "missing_markers=";
  printMarkers(oracle.missingMarkers);
  std::cout << "\n";
  std::cout << "unexpected_markers=";
  printMarkers(oracle.unexpectedMarkers);
  std::cout << "\n";

  if (options->requireObserved &&
      oracle.status != cv::ProbeOracleStatus::Matched) {
    return 2;
  }
  return oracle.status == cv::ProbeOracleStatus::Mismatch ? 3 : 0;
}
