#pragma once

#include "o2t/GeneratorConfig.h"
#include "o2t/PassInstrumentation.h"
#include "o2t/PassProbes.h"

#include <string>
#include <vector>

namespace cv {

enum class ProbeBackendKind {
  Abstract,
  LLVM,
};

struct ProbeBackendResult {
  ProbeBackendKind kind = ProbeBackendKind::Abstract;
  PassProbeCoverage coverage{};
  std::vector<std::string> observedMarkers;
  bool available = true;
};

ProbeBackendResult runLLVMProbeBackend(const GeneratorConfig &config);

inline const char *probeCategoryForConfig(const GeneratorConfig &rawConfig) {
  const GeneratorConfig config = normalizeConfig(rawConfig);
  if (globalShape(config) != GlobalShape::None) {
    return "global";
  }
  if (vectorShape(config) != VectorShape::None) {
    return "vector";
  }
  if (memoryShape(config) != MemoryShape::None) {
    return "memory";
  }
  if (loopShape(config) != LoopShape::None) {
    return "loop";
  }
  if (shape(config) != Shape::StraightLine) {
    return "cfg";
  }
  return "scalar";
}

inline const char *probePipelineForConfig(const GeneratorConfig &config) {
  const char *category = probeCategoryForConfig(config);
  if (std::string(category) == "memory") {
    return "mem2reg,dse,instcombine";
  }
  if (std::string(category) == "loop") {
    return "loop-simplify,licm,indvars,simplifycfg,instcombine";
  }
  if (std::string(category) == "cfg") {
    return "simplifycfg,instcombine";
  }
  return "instcombine";
}

inline ProbeBackendResult runAbstractProbeBackend(const GeneratorConfig &config) {
  clearPassProbeEvents();
  ProbeBackendResult result;
  result.kind = ProbeBackendKind::Abstract;
  result.coverage = scanOptimizationProbes(buildAbstractFunction(config));
  result.observedMarkers = passProbeEvents();
  result.available = true;
  return result;
}

inline ProbeBackendResult runProbeBackend(const GeneratorConfig &config) {
#if (defined(O2T_USE_LLVM_BACKEND) || defined(COMPILERVERIF_USE_LLVM_BACKEND))
  return runLLVMProbeBackend(config);
#else
  return runAbstractProbeBackend(config);
#endif
}

} // namespace cv
