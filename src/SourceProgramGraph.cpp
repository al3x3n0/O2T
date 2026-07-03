#include "o2t/SourceProgramGraph.h"

#include <algorithm>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <tuple>
#include <utility>

namespace o2t::sourcegraph {
namespace {

std::string trim(const std::string &Text) {
  const auto Begin = Text.find_first_not_of(" \t\r\n");
  if (Begin == std::string::npos) {
    return "";
  }
  const auto End = Text.find_last_not_of(" \t\r\n");
  return Text.substr(Begin, End - Begin + 1);
}

llvm::json::Array stringArray(const std::vector<std::string> &Values) {
  llvm::json::Array Result;
  for (const std::string &Value : Values) {
    Result.push_back(Value);
  }
  return Result;
}

llvm::json::Array callArgumentsArray(const std::vector<SourceCallArgument> &Arguments) {
  llvm::json::Array Result;
  for (const SourceCallArgument &Argument : Arguments) {
    Result.push_back(llvm::json::Object{
        {"symbol", Argument.Symbol},
        {"source", Argument.Source},
        {"line", static_cast<int>(Argument.Line)},
        {"column", static_cast<int>(Argument.Column)}});
  }
  return Result;
}

llvm::json::Object sourceRangeObject(const SourceFunctionSummary &Summary) {
  return llvm::json::Object{{"file", Summary.File},
                            {"begin_line", static_cast<int>(Summary.StartLine)},
                            {"end_line", static_cast<int>(Summary.EndLine)}};
}

const SourceFunctionSummary *
summaryByName(const std::vector<SourceFunctionSummary> &Functions,
              const std::string &Name) {
  for (const SourceFunctionSummary &Function : Functions) {
    if (Function.Name == Name) {
      return &Function;
    }
  }
  return nullptr;
}

bool isKeyword(const std::string &Name) {
  static const std::set<std::string> Keywords{
      "auto", "bool", "const", "else", "false", "for", "if", "int",
      "namespace", "return", "static", "struct", "true", "unsigned", "using",
      "void", "while"};
  return Keywords.count(Name) != 0;
}

std::string statementKind(const std::string &Line) {
  const std::string Text = trim(Line);
  if (Text.rfind("if", 0) == 0 || Text.find(" if ") != std::string::npos) {
    return "branch";
  }
  if (Text.rfind("return", 0) == 0 ||
      Text.find(" return ") != std::string::npos ||
      Text.find("{return ") != std::string::npos ||
      Text.find("{ return ") != std::string::npos) {
    return "return";
  }
  if (Text.find('=') != std::string::npos) {
    return "assignment";
  }
  if (Text.find('(') != std::string::npos && Text.find(')') != std::string::npos) {
    return "call";
  }
  return "statement";
}

std::vector<std::string> defsForLine(const std::string &Line) {
  std::vector<std::string> Defs;
  std::regex DefinitionPattern(
      R"cv((?:^|[;{]\s*)(?:(?:auto|bool|int|unsigned|Value\s*\*|[A-Za-z_:<>]+\s*\*?)\s+)?([A-Za-z_]\w*)\s*=)cv");
  for (std::sregex_iterator It(Line.begin(), Line.end(), DefinitionPattern),
       End;
       It != End; ++It) {
    Defs.push_back((*It)[1].str());
  }
  return Defs;
}

std::vector<std::string>
usesForLine(const std::vector<SourceFunctionSummary> &Functions,
            const std::string &Line, const std::set<std::string> &Defs) {
  std::vector<std::string> Uses;
  std::set<std::string> Seen;
  std::regex IdentifierPattern(R"cv(\b[A-Za-z_]\w*\b)cv");
  for (std::sregex_iterator It(Line.begin(), Line.end(), IdentifierPattern),
       End;
       It != End; ++It) {
    const std::string Name = It->str();
    if (Defs.count(Name) != 0 || isKeyword(Name) ||
        summaryByName(Functions, Name)) {
      continue;
    }
    if (Seen.insert(Name).second) {
      Uses.push_back(Name);
    }
  }
  return Uses;
}

std::vector<std::string>
astDefsForLine(const SourceFunctionSummary &Summary, unsigned Line) {
  std::vector<std::string> Defs;
  std::set<std::string> Seen;
  for (const SourceDataflowDef &Def : Summary.DataflowDefs) {
    if (Def.Line != Line || Def.Symbol.empty()) {
      continue;
    }
    if (Seen.insert(Def.Symbol).second) {
      Defs.push_back(Def.Symbol);
    }
  }
  return Defs;
}

std::vector<std::string>
astUsesForLine(const SourceFunctionSummary &Summary, unsigned Line) {
  std::vector<std::string> Uses;
  std::set<std::string> Seen;
  for (const SourceDataflowUse &Use : Summary.DataflowUses) {
    if (Use.Line != Line || Use.Symbol.empty()) {
      continue;
    }
    if (Seen.insert(Use.Symbol).second) {
      Uses.push_back(Use.Symbol);
    }
  }
  return Uses;
}

std::string baseSymbolForAccessPath(const std::string &Symbol) {
  size_t Pos = Symbol.size();
  const auto Consider = [&](size_t Candidate) {
    if (Candidate != std::string::npos) {
      Pos = std::min(Pos, Candidate);
    }
  };
  Consider(Symbol.find("->"));
  Consider(Symbol.find('.'));
  Consider(Symbol.find('['));
  if (Pos == Symbol.size()) {
    return "";
  }
  return Symbol.substr(0, Pos);
}

struct LastDefinitionLookup {
  std::map<std::string, std::string>::const_iterator Definition;
  std::string MatchKind;
  std::string BaseSymbol;
};

llvm::json::Array accessPathSegments(const std::string &Symbol) {
  llvm::json::Array Segments;
  size_t Pos = baseSymbolForAccessPath(Symbol).size();
  if (Pos == 0 || Pos >= Symbol.size()) {
    return Segments;
  }
  while (Pos < Symbol.size()) {
    if (Symbol.compare(Pos, 2, "->") == 0 || Symbol[Pos] == '.') {
      const bool IsArrow = Symbol.compare(Pos, 2, "->") == 0;
      Pos += IsArrow ? 2 : 1;
      const size_t Begin = Pos;
      while (Pos < Symbol.size() && Symbol[Pos] != '.' &&
             Symbol[Pos] != '[' &&
             Symbol.compare(Pos, 2, "->") != 0) {
        ++Pos;
      }
      if (Begin == Pos) {
        break;
      }
      Segments.push_back(llvm::json::Object{
          {"kind", "member"},
          {"name", Symbol.substr(Begin, Pos - Begin)},
          {"separator", IsArrow ? "->" : "."}});
      continue;
    }
    if (Symbol[Pos] == '[') {
      const size_t Begin = Pos + 1;
      ++Pos;
      unsigned Depth = 1;
      while (Pos < Symbol.size() && Depth != 0) {
        if (Symbol[Pos] == '[') {
          ++Depth;
        } else if (Symbol[Pos] == ']') {
          --Depth;
          if (Depth == 0) {
            break;
          }
        }
        ++Pos;
      }
      if (Depth != 0) {
        break;
      }
      Segments.push_back(llvm::json::Object{
          {"kind", "index"},
          {"source", Symbol.substr(Begin, Pos - Begin)}});
      ++Pos;
      continue;
    }
    break;
  }
  return Segments;
}

bool addAccessPathFields(llvm::json::Object &Object,
                         const std::string &Symbol) {
  const std::string Base = baseSymbolForAccessPath(Symbol);
  if (Base.empty()) {
    return false;
  }
  llvm::json::Array Segments = accessPathSegments(Symbol);
  if (Segments.empty()) {
    return false;
  }
  Object["symbol"] = Symbol;
  Object["base"] = Base;
  Object["segments"] = std::move(Segments);
  return true;
}

void addAccessPathEdgeProvenance(llvm::json::Object &Edge,
                                 const std::string &Symbol,
                                 const std::string &DefinitionMatch,
                                 const std::string &MatchedBase) {
  llvm::json::Object AccessPath;
  if (!addAccessPathFields(AccessPath, Symbol)) {
    return;
  }
  if (!DefinitionMatch.empty()) {
    AccessPath["definition_match"] = DefinitionMatch;
  }
  if (!MatchedBase.empty()) {
    AccessPath["matched_base"] = MatchedBase;
  }
  Edge["access_path"] = std::move(AccessPath);
}

void appendAccessPathFact(llvm::json::Array &Facts,
                          const std::string &FunctionName,
                          const std::string &NodeId, llvm::StringRef Role,
                          const std::string &Symbol, unsigned Line,
                          unsigned Column, const std::string &Source) {
  llvm::json::Object Fact{{"function", FunctionName},
                          {"node", NodeId},
                          {"role", Role.str()},
                          {"line", static_cast<int>(Line)},
                          {"column", static_cast<int>(Column)},
                          {"source", Source}};
  if (addAccessPathFields(Fact, Symbol)) {
    Facts.push_back(std::move(Fact));
  }
}

void appendAccessPathFactsForLine(llvm::json::Array &Facts,
                                  const SourceFunctionSummary &Summary,
                                  unsigned Line,
                                  const std::string &NodeId) {
  std::set<std::tuple<std::string, std::string, unsigned, unsigned>> Seen;
  for (const SourceDataflowDef &Def : Summary.DataflowDefs) {
    if (Def.Line != Line || Def.Symbol.empty()) {
      continue;
    }
    auto Key = std::make_tuple(std::string("def"), Def.Symbol, Def.Line,
                               Def.Column);
    if (Seen.insert(Key).second) {
      appendAccessPathFact(Facts, Summary.Name, NodeId, "def", Def.Symbol,
                           Def.Line, Def.Column, Def.Source);
    }
  }
  for (const SourceDataflowUse &Use : Summary.DataflowUses) {
    if (Use.Line != Line || Use.Symbol.empty()) {
      continue;
    }
    auto Key = std::make_tuple(std::string("use"), Use.Symbol, Use.Line,
                               Use.Column);
    if (Seen.insert(Key).second) {
      appendAccessPathFact(Facts, Summary.Name, NodeId, "use", Use.Symbol,
                           Use.Line, Use.Column, Use.Source);
    }
  }
}

LastDefinitionLookup findLastDefinition(
    const std::map<std::string, std::string> &LastDefinitionBySymbol,
    const std::string &FunctionName, const std::string &Symbol) {
  auto FoundDefinition =
      LastDefinitionBySymbol.find(FunctionName + "::" + Symbol);
  if (FoundDefinition != LastDefinitionBySymbol.end()) {
    return {FoundDefinition, "exact", ""};
  }
  const std::string BaseSymbol = baseSymbolForAccessPath(Symbol);
  if (BaseSymbol.empty()) {
    return {FoundDefinition, "", ""};
  }
  return {LastDefinitionBySymbol.find(FunctionName + "::" + BaseSymbol),
          "base-fallback", BaseSymbol};
}

} // namespace

llvm::json::Object
buildSourceProgramGraph(const std::vector<SourceFunctionSummary> &Functions,
                        const std::set<std::string> &Reachable) {
  llvm::json::Array FunctionObjects;
  llvm::json::Array Nodes;
  llvm::json::Array CfgBlocks;
  llvm::json::Array CfgEdges;
  llvm::json::Array DfgEdges;
  llvm::json::Array CallEdges;
  llvm::json::Array AccessPathFacts;
  bool HasClangCfg = false;
  bool HasAstDfg = false;
  bool HasInterproceduralDfg = false;
  std::map<std::pair<std::string, unsigned>, std::string> StatementIdByLine;
  std::map<std::string, std::string> LastDefinitionBySymbol;
  std::map<std::string, std::vector<std::string>> ReturnStatementIdsByFunction;
  std::set<std::tuple<std::string, std::string, std::string>> SeenDfgEdges;
  std::set<std::tuple<std::string, std::string, std::string, std::string>>
      SeenInterprocDfgEdges;

  for (const SourceFunctionSummary &Summary : Functions) {
    if (Reachable.count(Summary.Name) == 0) {
      continue;
    }
    const std::string EntryId = Summary.Name + ":entry";
    const std::string ExitId = Summary.Name + ":exit";
    HasClangCfg = HasClangCfg || !Summary.CfgBlocks.empty();
    const bool UseAstDfg =
        !Summary.DataflowDefs.empty() || !Summary.DataflowUses.empty();
    HasAstDfg = HasAstDfg || UseAstDfg;
    FunctionObjects.push_back(llvm::json::Object{
        {"name", Summary.Name},
        {"source_range", sourceRangeObject(Summary)},
        {"entry", EntryId},
        {"exit", ExitId},
        {"roles", stringArray(Summary.Roles)},
        {"parameters", stringArray(Summary.Parameters)}});
    for (const SourceCfgBlock &Block : Summary.CfgBlocks) {
      const std::string BlockId = Summary.Name + ":bb" + std::to_string(Block.Id);
      llvm::json::Array StatementLines;
      for (unsigned Line : Block.StatementLines) {
        StatementLines.push_back(static_cast<int>(Line));
      }
      llvm::json::Array Successors;
      for (unsigned Successor : Block.Successors) {
        const std::string SuccessorId =
            Summary.Name + ":bb" + std::to_string(Successor);
        Successors.push_back(SuccessorId);
        CfgEdges.push_back(llvm::json::Object{{"from", BlockId},
                                             {"to", SuccessorId},
                                             {"kind", "clang-cfg-successor"}});
      }
      CfgBlocks.push_back(llvm::json::Object{
          {"id", BlockId},
          {"function", Summary.Name},
          {"block_id", static_cast<int>(Block.Id)},
          {"begin_line", static_cast<int>(Block.BeginLine)},
          {"end_line", static_cast<int>(Block.EndLine)},
          {"statement_lines", std::move(StatementLines)},
          {"successors", std::move(Successors)}});
    }
    llvm::json::Array EntryDefArray;
    if (UseAstDfg) {
      for (const std::string &Def : astDefsForLine(Summary, Summary.StartLine)) {
        EntryDefArray.push_back(Def);
        LastDefinitionBySymbol[Summary.Name + "::" + Def] = EntryId;
      }
    }
    Nodes.push_back(llvm::json::Object{{"id", EntryId},
                                       {"function", Summary.Name},
                                       {"kind", "entry"},
                                       {"line", static_cast<int>(Summary.StartLine)},
                                       {"defs", std::move(EntryDefArray)}});
    std::map<unsigned, std::vector<const SourceCall *>> CallsByLine;
    for (const SourceCall &Call : Summary.Calls) {
      CallsByLine[Call.Line].push_back(&Call);
    }
    std::vector<std::string> FunctionStatementIds;
    for (size_t Index = 0; Index < Summary.Lines.size(); ++Index) {
      const std::string Text = trim(Summary.Lines[Index]);
      if (Text.empty() || Text == "{" || Text == "}" || Text == "};" ||
          (Index == 0 && Text == Summary.Signature)) {
        continue;
      }
      const unsigned Line = Summary.StartLine + static_cast<unsigned>(Index);
      const std::string Id =
          Summary.Name + ":s" + std::to_string(FunctionStatementIds.size());
      std::vector<std::string> Defs =
          UseAstDfg ? astDefsForLine(Summary, Line) : defsForLine(Text);
      std::set<std::string> DefSet(Defs.begin(), Defs.end());
      std::vector<std::string> Uses =
          UseAstDfg ? astUsesForLine(Summary, Line)
                    : usesForLine(Functions, Text, DefSet);
      StatementIdByLine[{Summary.Name, Line}] = Id;
      FunctionStatementIds.push_back(Id);

      llvm::json::Array DefArray;
      for (const std::string &Def : Defs) {
        DefArray.push_back(Def);
      }
      llvm::json::Array UseArray;
      for (const std::string &Use : Uses) {
        UseArray.push_back(Use);
        LastDefinitionLookup FoundDefinition =
            findLastDefinition(LastDefinitionBySymbol, Summary.Name, Use);
        if (FoundDefinition.Definition == LastDefinitionBySymbol.cend()) {
          continue;
        }
        auto Key = std::make_tuple(FoundDefinition.Definition->second, Id, Use);
        if (SeenDfgEdges.insert(Key).second) {
          llvm::json::Object Edge{{"from", FoundDefinition.Definition->second},
                                  {"to", Id},
                                  {"symbol", Use},
                                  {"kind",
                                   UseAstDfg ? "clang-ast-decl-use"
                                             : "last-definition"}};
          addAccessPathEdgeProvenance(Edge, Use, FoundDefinition.MatchKind,
                                      FoundDefinition.BaseSymbol);
          DfgEdges.push_back(std::move(Edge));
        }
      }
      auto CallsIt = CallsByLine.find(Line);
      if (CallsIt != CallsByLine.end()) {
        for (const SourceCall *Call : CallsIt->second) {
          if (!Call || Reachable.count(Call->Callee) == 0) {
            continue;
          }
          const SourceFunctionSummary *CalleeSummary =
              summaryByName(Functions, Call->Callee);
          if (!CalleeSummary) {
            continue;
          }
          for (size_t ArgIndex = 0; ArgIndex < Call->Arguments.size();
               ++ArgIndex) {
            const SourceCallArgument &Argument = Call->Arguments[ArgIndex];
            if (Argument.Symbol.empty()) {
              continue;
            }
            LastDefinitionLookup FoundDefinition =
                findLastDefinition(LastDefinitionBySymbol, Summary.Name,
                                   Argument.Symbol);
            if (FoundDefinition.Definition == LastDefinitionBySymbol.cend()) {
              continue;
            }
            const std::string Parameter =
                ArgIndex < CalleeSummary->Parameters.size()
                    ? CalleeSummary->Parameters[ArgIndex]
                    : "";
            const std::string To = Call->Callee + ":entry";
            auto Key = std::make_tuple(FoundDefinition.Definition->second, To,
                                       Argument.Symbol, Parameter);
            if (SeenInterprocDfgEdges.insert(Key).second) {
              llvm::json::Object Edge{{"from", FoundDefinition.Definition->second},
                                      {"to", To},
                                      {"caller", Summary.Name},
                                      {"callee", Call->Callee},
                                      {"symbol", Argument.Symbol},
                                      {"kind", "interproc-argument"}};
              if (!Parameter.empty()) {
                Edge["parameter"] = Parameter;
              }
              addAccessPathEdgeProvenance(Edge, Argument.Symbol,
                                          FoundDefinition.MatchKind,
                                          FoundDefinition.BaseSymbol);
              DfgEdges.push_back(std::move(Edge));
              HasInterproceduralDfg = true;
            }
          }
        }
      }
      for (const std::string &Def : Defs) {
        LastDefinitionBySymbol[Summary.Name + "::" + Def] = Id;
      }
      const std::string Kind = statementKind(Text);
      if (Kind == "return") {
        ReturnStatementIdsByFunction[Summary.Name].push_back(Id);
      }
      appendAccessPathFactsForLine(AccessPathFacts, Summary, Line, Id);
      Nodes.push_back(llvm::json::Object{{"id", Id},
                                         {"function", Summary.Name},
                                         {"kind", Kind},
                                         {"line", static_cast<int>(Line)},
                                         {"source", Text},
                                         {"defs", std::move(DefArray)},
                                         {"uses", std::move(UseArray)}});
    }
    Nodes.push_back(llvm::json::Object{{"id", ExitId},
                                       {"function", Summary.Name},
                                       {"kind", "exit"},
                                       {"line", static_cast<int>(Summary.EndLine)}});
    std::string Previous = EntryId;
    for (const std::string &Id : FunctionStatementIds) {
      CfgEdges.push_back(llvm::json::Object{{"from", Previous},
                                           {"to", Id},
                                           {"kind", "sequential"}});
      Previous = Id;
    }
    CfgEdges.push_back(llvm::json::Object{{"from", Previous},
                                         {"to", ExitId},
                                         {"kind", "sequential"}});
  }

  for (const SourceFunctionSummary &Summary : Functions) {
    if (Reachable.count(Summary.Name) == 0) {
      continue;
    }
    for (const SourceCall &Call : Summary.Calls) {
      if (Reachable.count(Call.Callee) == 0) {
        continue;
      }
      auto StatementIt = StatementIdByLine.find({Summary.Name, Call.Line});
      if (StatementIt == StatementIdByLine.end()) {
        continue;
      }
      llvm::json::Object CallEdge{{"from", StatementIt->second},
                                  {"to", Call.Callee + ":entry"},
                                  {"caller", Summary.Name},
                                  {"callee", Call.Callee},
                                  {"kind", "call"},
                                  {"arguments", callArgumentsArray(Call.Arguments)}};
      if (!Call.AssignedSymbol.empty()) {
        CallEdge["assigned_symbol"] = Call.AssignedSymbol;
      }
      CallEdges.push_back(std::move(CallEdge));
      CallEdges.push_back(llvm::json::Object{{"from", Call.Callee + ":exit"},
                                            {"to", StatementIt->second},
                                            {"caller", Summary.Name},
                                            {"callee", Call.Callee},
                                            {"kind", "return"}});
      if (!Call.AssignedSymbol.empty()) {
        auto ReturnIt = ReturnStatementIdsByFunction.find(Call.Callee);
        std::vector<std::string> ReturnIds;
        if (ReturnIt != ReturnStatementIdsByFunction.end()) {
          ReturnIds = ReturnIt->second;
        }
        if (ReturnIds.empty()) {
          ReturnIds.push_back(Call.Callee + ":exit");
        }
        for (const std::string &ReturnId : ReturnIds) {
          auto Key = std::make_tuple(ReturnId, StatementIt->second,
                                     Call.AssignedSymbol, Call.Callee);
          if (!SeenInterprocDfgEdges.insert(Key).second) {
            continue;
          }
          DfgEdges.push_back(llvm::json::Object{
              {"from", ReturnId},
              {"to", StatementIt->second},
              {"caller", Summary.Name},
              {"callee", Call.Callee},
              {"symbol", Call.AssignedSymbol},
              {"kind", "interproc-return"}});
          HasInterproceduralDfg = true;
        }
      }
    }
  }

  llvm::json::Array Limitations;
  if (HasClangCfg) {
    Limitations.push_back(
        "Clang CFG block edges model source-level control flow, not lowered IR semantics");
  } else {
    Limitations.push_back(
        "source-line CFG approximates structured branches as sequential edges");
  }
  Limitations.push_back(
      HasAstDfg
          ? "Clang AST DFG tracks local declaration/use flow, not alias or memory semantics"
          : "DFG tracks local named definitions and uses, not full alias/value semantics");
  if (HasInterproceduralDfg) {
    Limitations.push_back(
        "Interprocedural DFG binds direct helper calls only; non-symbol arguments are not linked");
  }

  return llvm::json::Object{
      {"model", "llvm-pass-source-program-graph-v1"},
      {"scope", "reachable-transaction-slice"},
      {"cfg_precision",
       HasClangCfg ? "clang-cfg-block-v1" : "source-line-sequential-v1"},
      {"dfg_precision",
       HasAstDfg ? "clang-ast-decl-use-v1" : "last-local-definition-v1"},
      {"interprocedural_dfg", HasInterproceduralDfg},
      {"functions", std::move(FunctionObjects)},
      {"nodes", std::move(Nodes)},
      {"cfg_blocks", std::move(CfgBlocks)},
      {"cfg_edges", std::move(CfgEdges)},
      {"dfg_edges", std::move(DfgEdges)},
      {"call_edges", std::move(CallEdges)},
      {"access_path_facts", std::move(AccessPathFacts)},
      {"limitations", std::move(Limitations)}};
}

} // namespace o2t::sourcegraph
