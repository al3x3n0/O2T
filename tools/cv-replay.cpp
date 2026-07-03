#include "o2t/GeneratorConfig.h"
#include "o2t/IRTextGenerator.h"

#include <cstdint>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>

namespace {

struct Options {
  std::optional<std::string> configPath;
  std::optional<std::string> outputPath;
  std::optional<std::string> normalizedConfigPath;
  std::optional<std::uint32_t> seed;
  bool dumpConfig = false;
};

void usage(std::ostream &out) {
  out << "usage: cv-replay [--seed N | --config FILE] [--out FILE] "
         "[--dump-config] [--write-config FILE]\n";
}

bool parseUint32(const std::string &text, std::uint32_t &value) {
  char *end = nullptr;
  const unsigned long parsed = std::strtoul(text.c_str(), &end, 0);
  if (end == text.c_str() || *end != '\0') {
    return false;
  }
  value = static_cast<std::uint32_t>(parsed);
  return true;
}

std::optional<Options> parseArgs(int argc, char **argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--help" || arg == "-h") {
      usage(std::cout);
      return std::nullopt;
    }
    if (arg == "--seed" && i + 1 < argc) {
      std::uint32_t seed = 0;
      if (!parseUint32(argv[++i], seed)) {
        std::cerr << "invalid --seed value\n";
        return std::nullopt;
      }
      options.seed = seed;
      continue;
    }
    if (arg == "--config" && i + 1 < argc) {
      options.configPath = argv[++i];
      continue;
    }
    if (arg == "--out" && i + 1 < argc) {
      options.outputPath = argv[++i];
      continue;
    }
    if (arg == "--write-config" && i + 1 < argc) {
      options.normalizedConfigPath = argv[++i];
      continue;
    }
    if (arg == "--dump-config") {
      options.dumpConfig = true;
      continue;
    }
    std::cerr << "unknown or incomplete argument: " << arg << "\n";
    usage(std::cerr);
    return std::nullopt;
  }

  if (options.seed.has_value() && options.configPath.has_value()) {
    std::cerr << "--seed and --config are mutually exclusive\n";
    return std::nullopt;
  }
  return options;
}

bool loadConfig(const Options &options, cv::GeneratorConfig &config) {
  if (options.configPath.has_value()) {
    std::ifstream input(*options.configPath);
    if (!input) {
      std::cerr << "failed to open config: " << *options.configPath << "\n";
      return false;
    }
    std::string error;
    if (!cv::parseConfig(input, config, error)) {
      std::cerr << "failed to parse config: " << error << "\n";
      return false;
    }
    return true;
  }

  config = options.seed.has_value() ? cv::configFromSeed(*options.seed)
                                    : cv::defaultConfig();
  return true;
}

} // namespace

int main(int argc, char **argv) {
  const auto options = parseArgs(argc, argv);
  if (!options.has_value()) {
    return argc == 2 && std::string(argv[1]) == "--help" ? 0 : 1;
  }

  cv::GeneratorConfig config{};
  if (!loadConfig(*options, config)) {
    return 1;
  }
  config = cv::normalizeConfig(config);

  if (options->dumpConfig) {
    cv::writeConfig(std::cerr, config);
  }

  if (options->normalizedConfigPath.has_value()) {
    std::ofstream output(*options->normalizedConfigPath);
    if (!output) {
      std::cerr << "failed to open normalized config output: "
                << *options->normalizedConfigPath << "\n";
      return 1;
    }
    cv::writeConfig(output, config);
  }

  const cv::GeneratedIR generated = cv::generateIR(config);

  if (options->outputPath.has_value()) {
    std::ofstream output(*options->outputPath);
    if (!output) {
      std::cerr << "failed to open output: " << *options->outputPath << "\n";
      return 1;
    }
    output << generated.moduleText;
  } else {
    std::cout << generated.moduleText;
  }

  return 0;
}
