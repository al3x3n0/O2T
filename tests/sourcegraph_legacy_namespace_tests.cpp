#include "o2t/SourceProgramGraph.h"

#include <cassert>
#include <set>
#include <string>
#include <vector>

int main() {
  std::vector<compilerverif::sourcegraph::SourceFunctionSummary> functions;
  compilerverif::sourcegraph::SourceFunctionSummary summary;
  summary.File = "compat.cpp";
  summary.Name = "fold";
  summary.StartLine = 1;
  summary.EndLine = 1;
  functions.push_back(summary);

  const std::set<std::string> reachable = {"fold"};
  const llvm::json::Object graph =
      compilerverif::sourcegraph::buildSourceProgramGraph(functions, reachable);
  assert(graph.getString("model").value_or("") ==
         "llvm-pass-source-program-graph-v1");
  return 0;
}
