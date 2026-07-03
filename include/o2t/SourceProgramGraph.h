#pragma once

#include "llvm/Support/JSON.h"

#include <set>
#include <string>
#include <vector>

namespace o2t::sourcegraph {

struct SourceCallArgument {
  std::string Symbol;
  std::string Source;
  unsigned Line = 0;
  unsigned Column = 0;
};

struct SourceCall {
  std::string Callee;
  unsigned Line = 0;
  std::vector<SourceCallArgument> Arguments;
  std::string AssignedSymbol;
};

struct SourceCfgBlock {
  unsigned Id = 0;
  unsigned BeginLine = 0;
  unsigned EndLine = 0;
  std::vector<unsigned> StatementLines;
  std::vector<unsigned> Successors;
};

struct SourceDataflowDef {
  std::string Symbol;
  unsigned Line = 0;
  unsigned Column = 0;
  std::string Source;
  std::string Kind;
};

struct SourceDataflowUse {
  std::string Symbol;
  unsigned Line = 0;
  unsigned Column = 0;
  std::string Source;
};

struct SourceFunctionSummary {
  std::string File;
  std::string Name;
  unsigned StartLine = 0;
  unsigned EndLine = 0;
  std::string Signature;
  std::vector<std::string> Lines;
  std::vector<std::string> Roles;
  std::vector<std::string> Parameters;
  std::vector<SourceCall> Calls;
  std::vector<std::string> CalledFunctions;
  std::vector<SourceCfgBlock> CfgBlocks;
  std::vector<SourceDataflowDef> DataflowDefs;
  std::vector<SourceDataflowUse> DataflowUses;
};

llvm::json::Object
buildSourceProgramGraph(const std::vector<SourceFunctionSummary> &Functions,
                        const std::set<std::string> &Reachable);

} // namespace o2t::sourcegraph

namespace compilerverif {
namespace sourcegraph = ::o2t::sourcegraph;
} // namespace compilerverif
