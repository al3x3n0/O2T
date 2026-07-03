#include "o2t/ConfigReducer.h"
#include "o2t/GeneratorConfig.h"
#include "o2t/ProbeMarkers.h"

#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Options {
  std::optional<std::string> configPath;
  std::optional<std::string> outputPath;
  std::vector<std::string> preserveMarkers;
};

void usage(std::ostream &out) {
  out << "usage: cv-reduce-config --config FILE [--preserve A,B] [--out FILE]\n";
}

std::vector<std::string> splitMarkers(const std::string &text) {
  std::vector<std::string> markers;
  std::stringstream input(text);
  std::string marker;
  while (std::getline(input, marker, ',')) {
    if (!marker.empty()) {
      markers.push_back(marker);
    }
  }
  return markers;
}

std::optional<Options> parseArgs(int argc, char **argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--help" || arg == "-h") {
      usage(std::cout);
      return std::nullopt;
    }
    if (arg == "--config" && i + 1 < argc) {
      options.configPath = argv[++i];
      continue;
    }
    if (arg == "--preserve" && i + 1 < argc) {
      options.preserveMarkers = splitMarkers(argv[++i]);
      continue;
    }
    if (arg == "--out" && i + 1 < argc) {
      options.outputPath = argv[++i];
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
  return true;
}

void printMarkers(std::ostream &out, const std::vector<std::string> &markers) {
  for (std::size_t index = 0; index < markers.size(); ++index) {
    if (index != 0) {
      out << ',';
    }
    out << markers[index];
  }
}

bool validateMarkers(const std::vector<std::string> &originalMarkers,
                     const std::vector<std::string> &requiredMarkers) {
  for (const std::string &marker : requiredMarkers) {
    if (!cv::containsMarker(originalMarkers, marker)) {
      std::cerr << "cannot preserve marker not hit by original config: "
                << marker << "\n";
      return false;
    }
  }
  return true;
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
  config = cv::normalizeConfig(config);

  const std::vector<std::string> originalMarkers =
      cv::markerStringsForConfig(config);
  std::vector<std::string> requiredMarkers = options->preserveMarkers;
  if (requiredMarkers.empty()) {
    requiredMarkers = originalMarkers;
  }

  if (!validateMarkers(originalMarkers, requiredMarkers)) {
    return 1;
  }

  const cv::GeneratorConfig reduced =
      cv::reduceConfig(config, requiredMarkers);
  const std::vector<std::string> reducedMarkers =
      cv::markerStringsForConfig(reduced);

  if (!cv::preservesMarkers(reduced, requiredMarkers)) {
    std::cerr << "internal error: reduced config does not preserve markers\n";
    return 1;
  }

  std::ostream *output = &std::cout;
  std::ofstream fileOutput;
  if (options->outputPath.has_value()) {
    fileOutput.open(*options->outputPath);
    if (!fileOutput) {
      std::cerr << "failed to open output: " << *options->outputPath << "\n";
      return 1;
    }
    output = &fileOutput;
  }

  cv::writeConfig(*output, reduced);

  std::cerr << "original markers: ";
  printMarkers(std::cerr, originalMarkers);
  std::cerr << "\nrequired markers: ";
  printMarkers(std::cerr, requiredMarkers);
  std::cerr << "\nreduced markers: ";
  printMarkers(std::cerr, reducedMarkers);
  std::cerr << "\n";

  return 0;
}
