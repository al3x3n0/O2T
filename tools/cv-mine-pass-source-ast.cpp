#include "o2t/GeneratedAstBindMarkerMap.h"
#include "o2t/GeneratedAstMatcherSpecs.h"
#include "o2t/GeneratedLlvmIdioms.h"
#include "o2t/GeneratedSourceMarkerPatterns.h"
#include "o2t/GeneratedVectorIntentParts.h"
#include "o2t/SourceProgramGraph.h"

#include "clang/AST/ASTContext.h"
#include "clang/AST/ASTTypeTraits.h"
#include "clang/AST/Expr.h"
#include "clang/Analysis/CFG.h"
#include "clang/ASTMatchers/ASTMatchFinder.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendActions.h"
#include "clang/Lex/Lexer.h"
#include "clang/Tooling/CommonOptionsParser.h"
#include "clang/Tooling/Tooling.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/raw_ostream.h"
#include "llvm/ADT/SmallVector.h"

#include <algorithm>
#include <cctype>
#include <functional>
#include <initializer_list>
#include <iterator>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

using namespace clang;
using namespace clang::ast_matchers;
using namespace clang::tooling;

namespace {

llvm::cl::OptionCategory Category("O2T AST mining options");
llvm::cl::opt<std::string> Format(
    "format", llvm::cl::desc("Output format: json or jsonl"),
    llvm::cl::init("json"), llvm::cl::cat(Category));
llvm::cl::list<std::string> RequiredMarkers(
    "require-marker", llvm::cl::desc("Require a marker to be mined"),
    llvm::cl::cat(Category));
llvm::cl::opt<std::string> RegistryPath(
    "registry", llvm::cl::desc("Constraint registry JSON path"),
    llvm::cl::init("constraints/pass_constraints.json"), llvm::cl::cat(Category));
llvm::cl::opt<std::string> SemanticRegistryPath(
    "semantic-registry", llvm::cl::desc("Semantic facts registry JSON path"),
    llvm::cl::init("constraints/semantic_facts.json"), llvm::cl::cat(Category));
llvm::cl::opt<std::string> GuardSemanticsPath(
    "guard-semantics", llvm::cl::desc("Guard semantics registry JSON path"),
    llvm::cl::init("constraints/guard_semantics.json"), llvm::cl::cat(Category));
llvm::cl::opt<std::string> LlvmIdiomsPath(
    "llvm-idioms", llvm::cl::desc("LLVM idiom registry JSON path"),
    llvm::cl::init("constraints/llvm_idioms.json"), llvm::cl::cat(Category));

struct RegistryEntry {
  std::string Marker;
  std::string Pass;
  std::string PredicateKind;
  llvm::json::Value Constraints = llvm::json::Object{};
};

struct GuardCatalogEntry {
  std::string Kind;
  std::string Role;
  std::string ProofEffect;
  std::string FormalEffect;
  std::string AuditCategory;
};

struct OperationIdiom {
  std::string Operation;
  std::set<std::string> Matchers;
  std::set<std::string> Builders;
  bool Commutative = false;
};

struct Finding {
  std::string File;
  unsigned Line = 0;
  unsigned Column = 0;
  std::string Marker;
  std::string Pass;
  std::string PredicateKind;
  std::string PredicateSource;
  std::string RewriteSource;
  std::string RewriteStatus;
  std::string RewriteAbsentReason;
  std::string RewriteSearchScope = "then-block-v1";
  std::string Function;
  int BranchIndex = -1;
  std::string Opcode;
  unsigned RewriteLine = 0;
  unsigned EndLine = 0;
  unsigned EndColumn = 0;
  llvm::json::Value Constraints = llvm::json::Object{};
  std::optional<llvm::json::Object> SourceIntent;
  std::optional<llvm::json::Object> SourceIntentGraph;
  std::optional<llvm::json::Object> OptimizationTransaction;
};

std::map<std::string, GuardCatalogEntry> GuardCatalog;
std::set<std::string> MissingGuardKinds;
std::map<std::string, OperationIdiom> OperationIdioms;
std::map<std::string, std::string> MatcherOperation;
std::map<std::string, std::string> BuilderOperation;
std::map<std::string, std::string> ReductionOperation;
std::map<std::string, std::string> VectorBuilderOperation;
std::map<std::string, int64_t> ConstantMatcherValue;
std::set<std::string> SymbolMatcherNames;
std::vector<std::string> RewriteTokens;
std::vector<std::string> ReductionTokens;
std::vector<std::string> VectorEmissionTokens;

std::string stringField(const llvm::json::Object &Object, llvm::StringRef Name) {
  if (auto Value = Object.getString(Name)) {
    return std::string(*Value);
  }
  return "";
}

int64_t intField(const llvm::json::Object &Object, llvm::StringRef Name,
                 int64_t Default = 0) {
  if (auto Value = Object.getInteger(Name)) {
    return *Value;
  }
  return Default;
}

llvm::json::Value cloneJson(const llvm::json::Value &Value) {
  std::string Text;
  llvm::raw_string_ostream OS(Text);
  OS << Value;
  OS.flush();
  llvm::Expected<llvm::json::Value> Parsed = llvm::json::parse(Text);
  if (!Parsed) {
    llvm::consumeError(Parsed.takeError());
    return llvm::json::Object{};
  }
  return std::move(*Parsed);
}

llvm::json::Value cloneJsonObject(const llvm::json::Object &Object) {
  llvm::json::Object Result;
  for (const auto &Item : Object) {
    Result[Item.first] = cloneJson(Item.second);
  }
  return Result;
}

void rebuildIdiomIndexes() {
  MatcherOperation.clear();
  BuilderOperation.clear();
  for (const auto &Item : OperationIdioms) {
    const OperationIdiom &Idiom = Item.second;
    for (const std::string &Matcher : Idiom.Matchers) {
      MatcherOperation[Matcher] = Idiom.Operation;
    }
    for (const std::string &Builder : Idiom.Builders) {
      BuilderOperation[Builder] = Idiom.Operation;
    }
  }
  VectorEmissionTokens.clear();
  for (const auto &Item : ReductionOperation) {
    VectorEmissionTokens.push_back(Item.first);
  }
  for (const auto &Item : BuilderOperation) {
    VectorEmissionTokens.push_back(Item.first);
  }
  for (const auto &Item : VectorBuilderOperation) {
    VectorEmissionTokens.push_back(Item.first);
  }
}

void installDefaultLlvmIdioms() {
  OperationIdioms.clear();
  auto AddOperation = [](std::string Operation,
                         std::initializer_list<const char *> Matchers,
                         std::initializer_list<const char *> Builders,
                         bool Commutative) {
    OperationIdiom Idiom;
    Idiom.Operation = std::move(Operation);
    Idiom.Commutative = Commutative;
    for (const char *Matcher : Matchers) {
      Idiom.Matchers.insert(Matcher);
    }
    for (const char *Builder : Builders) {
      Idiom.Builders.insert(Builder);
    }
    OperationIdioms[Idiom.Operation] = std::move(Idiom);
  };
  // Operation idioms are generated from constraints/llvm_idioms.json by
  // tools/cv-generate-idiom-header.py -> GeneratedLlvmIdioms.h, so this built-in
  // fallback can never drift from the registry the runtime loader uses.
  CV_FOR_EACH_GENERATED_OPERATION_IDIOM(AddOperation)

  ConstantMatcherValue = {{"m_Zero", 0},
                          {"m_One", 1},
                          {"m_AllOnes", 0xFFFFFFFFLL}};
  SymbolMatcherNames = {"m_Value", "m_Specific", "m_Deferred"};
  ReductionOperation = {
      {"CreateFAddReduce", "fadd"}, {"vector_reduce_fadd", "fadd"},
      {"CreateFMulReduce", "fmul"}, {"vector_reduce_fmul", "fmul"},
      {"CreateAddReduce", "add"},   {"vector_reduce_add", "add"},
      {"CreateMulReduce", "mul"},   {"vector_reduce_mul", "mul"},
      {"CreateAndReduce", "and"},   {"vector_reduce_and", "and"},
      {"CreateOrReduce", "or"},     {"vector_reduce_or", "or"},
      {"CreateXorReduce", "xor"},   {"vector_reduce_xor", "xor"},
      {"CreateSMinReduce", "smin"}, {"vector_reduce_smin", "smin"},
      {"CreateSMaxReduce", "smax"}, {"vector_reduce_smax", "smax"},
      {"CreateUMinReduce", "umin"}, {"vector_reduce_umin", "umin"},
      {"CreateUMaxReduce", "umax"}, {"vector_reduce_umax", "umax"}};
  VectorBuilderOperation = {
      {"CreateSMin", "smin"}, {"ICMP_SLT", "smin"},
      {"CreateSMax", "smax"}, {"ICMP_SGT", "smax"},
      {"CreateUMin", "umin"}, {"ICMP_ULT", "umin"},
      {"CreateUMax", "umax"}, {"ICMP_UGT", "umax"},
      {"CreateBinOp", "binop"}, {"CreateSelect", "select"}};
  ReductionTokens.clear();
  for (const auto &Item : ReductionOperation) {
    ReductionTokens.push_back(Item.first);
  }
  RewriteTokens = {"replaceInstUsesWith", "ReplaceInstWithValue",
                   "ReplaceInstWithInst", "eraseFromParent",
                   "setInitializer",      "return"};
  for (const auto &Item : OperationIdioms) {
    for (const std::string &Builder : Item.second.Builders) {
      RewriteTokens.push_back(Builder);
    }
  }
  RewriteTokens.push_back("CreateSelect");
  rebuildIdiomIndexes();
}

std::vector<std::string> stringArrayField(const llvm::json::Object &Object,
                                          llvm::StringRef Name) {
  std::vector<std::string> Result;
  const llvm::json::Array *Array = Object.getArray(Name);
  if (!Array) {
    return Result;
  }
  for (const llvm::json::Value &Value : *Array) {
    if (auto Text = Value.getAsString()) {
      if (!Text->empty()) {
        Result.push_back(Text->str());
      }
    }
  }
  return Result;
}

bool readableFile(llvm::StringRef Path) {
  return static_cast<bool>(llvm::MemoryBuffer::getFile(Path));
}

std::string pathDirname(llvm::StringRef Path) {
  const size_t Slash = Path.str().find_last_of("/\\");
  if (Slash == std::string::npos) {
    return "";
  }
  return Path.str().substr(0, Slash);
}

std::string pathBasename(llvm::StringRef Path) {
  const size_t Slash = Path.str().find_last_of("/\\");
  if (Slash == std::string::npos) {
    return Path.str();
  }
  return Path.str().substr(Slash + 1);
}

bool isAbsolutePath(llvm::StringRef Path) {
  return Path.starts_with("/") || (Path.size() > 2 && std::isalpha(Path[0]) &&
                                  Path[1] == ':');
}

std::string joinPath(llvm::StringRef Left, llvm::StringRef Right) {
  if (Left.empty()) {
    return Right.str();
  }
  if (Left.ends_with("/") || Left.ends_with("\\")) {
    return (Left + Right).str();
  }
  return (Left + "/" + Right).str();
}

std::string resolveDataPath(llvm::StringRef Path,
                            llvm::StringRef RegistryPath) {
  if (readableFile(Path)) {
    return Path.str();
  }
  if (isAbsolutePath(Path)) {
    return Path.str();
  }
  const std::string RegistryDir = pathDirname(RegistryPath);
  if (RegistryDir.empty()) {
    return Path.str();
  }
  const std::string RegistrySibling = joinPath(RegistryDir, pathBasename(Path));
  if (readableFile(RegistrySibling)) {
    return RegistrySibling;
  }
  const std::string RepoDir = pathDirname(RegistryDir);
  if (!RepoDir.empty()) {
    const std::string RepoRelative = joinPath(RepoDir, Path);
    if (readableFile(RepoRelative)) {
      return RepoRelative;
    }
  }
  return Path.str();
}

bool loadLlvmIdioms(llvm::StringRef Path) {
  installDefaultLlvmIdioms();
  auto Buffer = llvm::MemoryBuffer::getFile(Path);
  if (!Buffer) {
    llvm::errs() << "failed to read LLVM idioms: " << Path
                 << "; using built-in defaults\n";
    return false;
  }
  llvm::Expected<llvm::json::Value> Parsed =
      llvm::json::parse((*Buffer)->getBuffer());
  if (!Parsed) {
    llvm::errs() << "failed to parse LLVM idioms: " << Path
                 << "; using built-in defaults\n";
    llvm::consumeError(Parsed.takeError());
    return false;
  }
  const llvm::json::Object *RootObject = Parsed->getAsObject();
  if (!RootObject) {
    llvm::errs() << "LLVM idioms must contain a JSON object: " << Path
                 << "; using built-in defaults\n";
    return false;
  }
  const llvm::json::Array *Operations = RootObject->getArray("operations");
  const llvm::json::Array *Constants = RootObject->getArray("constants");
  const llvm::json::Array *Rewrites = RootObject->getArray("rewrites");
  const llvm::json::Array *Reductions = RootObject->getArray("reductions");
  const llvm::json::Array *VectorBuilders =
      RootObject->getArray("vector_builders");
  if (!Operations || !Constants || !Rewrites || !Reductions ||
      !VectorBuilders) {
    llvm::errs() << "LLVM idioms missing required arrays: " << Path
                 << "; using built-in defaults\n";
    return false;
  }

  std::map<std::string, OperationIdiom> LoadedOperations;
  for (const llvm::json::Value &Value : *Operations) {
    const llvm::json::Object *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    OperationIdiom Idiom;
    Idiom.Operation = stringField(*Object, "operation");
    if (Idiom.Operation.empty()) {
      continue;
    }
    for (std::string Matcher : stringArrayField(*Object, "matchers")) {
      Idiom.Matchers.insert(std::move(Matcher));
    }
    for (std::string Builder : stringArrayField(*Object, "builders")) {
      Idiom.Builders.insert(std::move(Builder));
    }
    if (auto Commutative = Object->getBoolean("commutative")) {
      Idiom.Commutative = *Commutative;
    }
    LoadedOperations[Idiom.Operation] = std::move(Idiom);
  }
  if (LoadedOperations.empty()) {
    llvm::errs() << "LLVM idioms contain no operations: " << Path
                 << "; using built-in defaults\n";
    return false;
  }

  std::map<std::string, int64_t> LoadedConstants;
  std::set<std::string> LoadedSymbolMatchers;
  for (const llvm::json::Value &Value : *Constants) {
    const llvm::json::Object *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    std::vector<std::string> Matchers = stringArrayField(*Object, "matchers");
    if (Matchers.empty()) {
      continue;
    }
    if (auto FormalValue = Object->getInteger("formal_value")) {
      for (std::string Matcher : Matchers) {
        LoadedConstants[std::move(Matcher)] = *FormalValue;
      }
    } else {
      for (std::string Matcher : Matchers) {
        LoadedSymbolMatchers.insert(std::move(Matcher));
      }
    }
  }

  std::map<std::string, std::string> LoadedReductions;
  std::vector<std::string> LoadedReductionTokens;
  for (const llvm::json::Value &Value : *Reductions) {
    const llvm::json::Object *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    const std::string Operation = stringField(*Object, "operation");
    if (Operation.empty()) {
      continue;
    }
    for (std::string Token : stringArrayField(*Object, "tokens")) {
      LoadedReductionTokens.push_back(Token);
      LoadedReductions[std::move(Token)] = Operation;
    }
  }

  std::map<std::string, std::string> LoadedVectorBuilders;
  for (const llvm::json::Value &Value : *VectorBuilders) {
    const llvm::json::Object *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    const std::string Operation = stringField(*Object, "operation");
    if (Operation.empty()) {
      continue;
    }
    for (std::string Token : stringArrayField(*Object, "tokens")) {
      LoadedVectorBuilders[std::move(Token)] = Operation;
    }
  }

  std::vector<std::string> LoadedRewriteTokens;
  for (const llvm::json::Value &Value : *Rewrites) {
    const llvm::json::Object *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    for (std::string Api : stringArrayField(*Object, "apis")) {
      LoadedRewriteTokens.push_back(std::move(Api));
    }
  }
  for (const auto &Item : LoadedOperations) {
    for (const std::string &Builder : Item.second.Builders) {
      LoadedRewriteTokens.push_back(Builder);
    }
  }
  LoadedRewriteTokens.push_back("CreateSelect");
  LoadedRewriteTokens.push_back("return");
  if (LoadedConstants.empty() || LoadedRewriteTokens.empty() ||
      LoadedReductions.empty() || LoadedVectorBuilders.empty()) {
    llvm::errs() << "LLVM idioms missing constants, rewrites, reductions, or vector builders: " << Path
                 << "; using built-in defaults\n";
    return false;
  }

  OperationIdioms = std::move(LoadedOperations);
  ConstantMatcherValue = std::move(LoadedConstants);
  SymbolMatcherNames = std::move(LoadedSymbolMatchers);
  ReductionOperation = std::move(LoadedReductions);
  ReductionTokens = std::move(LoadedReductionTokens);
  VectorBuilderOperation = std::move(LoadedVectorBuilders);
  RewriteTokens = std::move(LoadedRewriteTokens);
  rebuildIdiomIndexes();
  return true;
}

llvm::json::Object cloneObject(const llvm::json::Object &Object) {
  llvm::json::Value Value = cloneJsonObject(Object);
  if (auto *Clone = Value.getAsObject()) {
    return std::move(*Clone);
  }
  return {};
}

llvm::json::Array cloneArray(const llvm::json::Array &Array) {
  llvm::json::Array Result;
  for (const llvm::json::Value &Value : Array) {
    Result.push_back(cloneJson(Value));
  }
  return Result;
}

std::map<std::string, RegistryEntry> loadRegistry(llvm::StringRef Path) {
  std::map<std::string, RegistryEntry> Result;
  auto Buffer = llvm::MemoryBuffer::getFile(Path);
  if (!Buffer) {
    llvm::errs() << "failed to read registry: " << Path << "\n";
    return Result;
  }
  llvm::Expected<llvm::json::Value> Parsed =
      llvm::json::parse((*Buffer)->getBuffer());
  if (!Parsed) {
    llvm::errs() << "failed to parse registry: " << Path << "\n";
    llvm::consumeError(Parsed.takeError());
    return Result;
  }
  const auto *Array = Parsed->getAsArray();
  if (!Array) {
    llvm::errs() << "registry must contain a JSON array: " << Path << "\n";
    return Result;
  }
  for (const llvm::json::Value &Value : *Array) {
    const auto *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    RegistryEntry Entry;
    Entry.Marker = stringField(*Object, "marker");
    Entry.Pass = stringField(*Object, "pass");
    Entry.PredicateKind = stringField(*Object, "predicate_kind");
    if (const llvm::json::Value *Constraints = Object->get("constraints")) {
      Entry.Constraints = cloneJson(*Constraints);
    }
    if (!Entry.Marker.empty()) {
      Result[Entry.Marker] = std::move(Entry);
    }
  }
  return Result;
}

std::map<std::string, GuardCatalogEntry> loadGuardCatalog(llvm::StringRef Path,
                                                          bool &Ok) {
  Ok = false;
  std::map<std::string, GuardCatalogEntry> Result;
  auto Buffer = llvm::MemoryBuffer::getFile(Path);
  if (!Buffer) {
    llvm::errs() << "failed to read guard semantics: " << Path << "\n";
    return Result;
  }
  llvm::Expected<llvm::json::Value> Parsed =
      llvm::json::parse((*Buffer)->getBuffer());
  if (!Parsed) {
    llvm::errs() << "failed to parse guard semantics: " << Path << "\n";
    llvm::consumeError(Parsed.takeError());
    return Result;
  }
  const auto *Array = Parsed->getAsArray();
  if (!Array) {
    llvm::errs() << "guard semantics must contain a JSON array: " << Path << "\n";
    return Result;
  }
  for (const llvm::json::Value &Value : *Array) {
    const auto *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    GuardCatalogEntry Entry;
    Entry.Kind = stringField(*Object, "kind");
    Entry.Role = stringField(*Object, "role");
    Entry.ProofEffect = stringField(*Object, "proof_effect");
    Entry.FormalEffect = stringField(*Object, "formal_effect");
    Entry.AuditCategory = stringField(*Object, "audit_category");
    if (!Entry.Kind.empty()) {
      Result[Entry.Kind] = std::move(Entry);
    }
  }
  Ok = true;
  return Result;
}

std::map<std::string, llvm::json::Value> loadSemanticRegistry(llvm::StringRef Path) {
  std::map<std::string, llvm::json::Value> Result;
  auto Buffer = llvm::MemoryBuffer::getFile(Path);
  if (!Buffer) {
    llvm::errs() << "failed to read semantic registry: " << Path << "\n";
    return Result;
  }
  llvm::Expected<llvm::json::Value> Parsed =
      llvm::json::parse((*Buffer)->getBuffer());
  if (!Parsed) {
    llvm::errs() << "failed to parse semantic registry: " << Path << "\n";
    llvm::consumeError(Parsed.takeError());
    return Result;
  }
  const auto *Array = Parsed->getAsArray();
  if (!Array) {
    llvm::errs() << "semantic registry must contain a JSON array: " << Path
                 << "\n";
    return Result;
  }
  for (const llvm::json::Value &Value : *Array) {
    const auto *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    std::string Marker = stringField(*Object, "marker");
    if (Marker.empty()) {
      continue;
    }
    if (const llvm::json::Value *Facts = Object->get("semantic_facts")) {
      if (Facts->getAsObject()) {
        Result.try_emplace(std::move(Marker), cloneJson(*Facts));
      }
    }
  }
  return Result;
}

std::string effectiveSemanticRegistryPath() {
  const std::string Explicit = SemanticRegistryPath;
  if (Explicit != "constraints/semantic_facts.json") {
    return Explicit;
  }
  const std::string Registry = RegistryPath;
  const std::string BaseName = "pass_constraints.json";
  if (Registry.size() >= BaseName.size() &&
      Registry.compare(Registry.size() - BaseName.size(), BaseName.size(),
                       BaseName) == 0) {
    return Registry.substr(0, Registry.size() - BaseName.size()) +
           "semantic_facts.json";
  }
  return Explicit;
}

std::string effectiveGuardSemanticsPath() {
  const std::string Explicit = GuardSemanticsPath;
  if (Explicit != "constraints/guard_semantics.json") {
    return Explicit;
  }
  const std::string Registry = RegistryPath;
  const std::string BaseName = "pass_constraints.json";
  if (Registry.size() >= BaseName.size() &&
      Registry.compare(Registry.size() - BaseName.size(), BaseName.size(),
                       BaseName) == 0) {
    return Registry.substr(0, Registry.size() - BaseName.size()) +
           "guard_semantics.json";
  }
  return Explicit;
}

std::string sourceText(const SourceManager &SM, const LangOptions &LangOpts,
                       SourceRange Range) {
  CharSourceRange CharRange = CharSourceRange::getTokenRange(Range);
  return Lexer::getSourceText(CharRange, SM, LangOpts).str();
}

llvm::json::Object sourceRangeJson(const SourceManager &SM,
                                   SourceLocation Begin,
                                   SourceLocation End) {
  return llvm::json::Object{
      {"begin_line", static_cast<int64_t>(SM.getSpellingLineNumber(Begin))},
      {"begin_column", static_cast<int64_t>(SM.getSpellingColumnNumber(Begin))},
      {"end_line", static_cast<int64_t>(SM.getSpellingLineNumber(End))},
      {"end_column", static_cast<int64_t>(SM.getSpellingColumnNumber(End))},
  };
}

std::string markerForBoundNode(const MatchFinder::MatchResult &Result) {
  for (const cv::AstBindMarkerMetadata &Entry : cv::kAstBindMarkerMetadata) {
    if (Result.Nodes.getNodeAs<Stmt>(Entry.bindName)) {
      return Entry.marker;
    }
  }
  return "";
}

std::string markerForConditionText(llvm::StringRef Text) {
  return cv::markerForGeneratedSourceText(Text);
}

bool containsRewriteToken(llvm::StringRef Text) {
  for (const std::string &Token : RewriteTokens) {
    if (Text.contains(Token)) {
      return true;
    }
  }
  return false;
}

const Stmt *firstRewriteStmt(const Stmt *StmtNode, const SourceManager &SM,
                             const LangOptions &LangOpts) {
  if (!StmtNode) {
    return nullptr;
  }
  if (const auto *Compound = dyn_cast<CompoundStmt>(StmtNode)) {
    if (std::distance(Compound->body_begin(), Compound->body_end()) > 1) {
      const std::string Text =
          sourceText(SM, LangOpts, Compound->getSourceRange());
      if ((llvm::StringRef(Text).contains("replaceInstUsesWith") ||
           llvm::StringRef(Text).contains("ReplaceInstWithValue")) &&
          containsRewriteToken(Text)) {
        return Compound;
      }
    }
    for (const Stmt *Child : Compound->body()) {
      if (const Stmt *Result = firstRewriteStmt(Child, SM, LangOpts)) {
        return Result;
      }
    }
    return nullptr;
  }
  const std::string Text = sourceText(SM, LangOpts, StmtNode->getSourceRange());
  if (containsRewriteToken(Text)) {
    return StmtNode;
  }
  for (const Stmt *Child : StmtNode->children()) {
    if (const Stmt *Result = firstRewriteStmt(Child, SM, LangOpts)) {
      return Result;
    }
  }
  return nullptr;
}

std::string rewriteAbsentReason(const Stmt *StmtNode) {
  if (!StmtNode) {
    return "unsupported-control-flow";
  }
  if (const auto *Compound = dyn_cast<CompoundStmt>(StmtNode)) {
    if (Compound->body_empty()) {
      return "empty-then-block";
    }
    return "no-known-rewrite-call";
  }
  return "no-known-rewrite-call";
}

std::string trim(std::string Text) {
  const auto Begin = Text.find_first_not_of(" \t\n\r");
  if (Begin == std::string::npos) {
    return "";
  }
  const auto End = Text.find_last_not_of(" \t\n\r");
  return Text.substr(Begin, End - Begin + 1);
}

bool isSafeSymbolicMaskIndexText(std::string Text) {
  Text = trim(std::move(Text));
  if (Text.empty()) {
    return false;
  }
  if (std::regex_match(Text, std::regex(R"cv([A-Za-z_]\w*|\d+)cv"))) {
    return true;
  }
  if (Text.find(',') != std::string::npos ||
      Text.find("++") != std::string::npos ||
      Text.find("--") != std::string::npos ||
      Text.find('[') != std::string::npos ||
      Text.find(']') != std::string::npos ||
      Text.find('.') != std::string::npos ||
      Text.find('=') != std::string::npos ||
      Text.find('!') != std::string::npos ||
      Text.find('/') != std::string::npos ||
      Text.find('%') != std::string::npos ||
      Text.find('?') != std::string::npos ||
      Text.find(':') != std::string::npos) {
    return false;
  }
  if (std::regex_search(Text, std::regex(R"cv(\b[A-Za-z_]\w*\s*\()cv"))) {
    return false;
  }
  if (!std::regex_match(
          Text,
          std::regex(R"cv(([A-Za-z_]\w*|\d+|[()+\-*&|^~<>\s]+)+)cv"))) {
    return false;
  }
  int Depth = 0;
  bool HasIdentifierOrDigit = false;
  for (char C : Text) {
    if (std::isalnum(static_cast<unsigned char>(C)) || C == '_') {
      HasIdentifierOrDigit = true;
    }
    if (C == '(') {
      ++Depth;
    } else if (C == ')') {
      --Depth;
      if (Depth < 0) {
        return false;
      }
    }
  }
  return HasIdentifierOrDigit && Depth == 0;
}

std::optional<long long> parseStaticIntegerLiteral(std::string Text) {
  Text = trim(std::move(Text));
  if (Text.empty()) {
    return std::nullopt;
  }
  while (!Text.empty() &&
         (Text.back() == 'u' || Text.back() == 'U' || Text.back() == 'l' ||
          Text.back() == 'L')) {
    Text.pop_back();
  }
  if (Text.empty()) {
    return std::nullopt;
  }
  try {
    size_t Parsed = 0;
    long long Value = std::stoll(Text, &Parsed, 0);
    if (Parsed != Text.size()) {
      return std::nullopt;
    }
    return Value;
  } catch (...) {
    return std::nullopt;
  }
}

class StaticIntExprParser {
public:
  StaticIntExprParser(std::string Text,
                      const std::map<std::string, long long> &Constants)
      : Text(std::move(Text)), Constants(Constants) {}

  std::optional<long long> parse() {
    Position = 0;
    std::optional<long long> Value = parseBitOr();
    skipSpace();
    if (!Value || Position != Text.size()) {
      return std::nullopt;
    }
    return Value;
  }

private:
  std::string Text;
  const std::map<std::string, long long> &Constants;
  size_t Position = 0;

  void skipSpace() {
    while (Position < Text.size() &&
           std::isspace(static_cast<unsigned char>(Text[Position]))) {
      ++Position;
    }
  }

  bool consume(char C) {
    skipSpace();
    if (Position < Text.size() && Text[Position] == C) {
      ++Position;
      return true;
    }
    return false;
  }

  bool consumeText(llvm::StringRef Token) {
    skipSpace();
    if (Position + Token.size() <= Text.size() &&
        Text.compare(Position, Token.size(), Token.str()) == 0) {
      Position += Token.size();
      return true;
    }
    return false;
  }

  std::optional<long long> parseBitOr() {
    std::optional<long long> Value = parseBitXor();
    while (Value && consume('|')) {
      std::optional<long long> RHS = parseBitXor();
      if (!RHS) {
        return std::nullopt;
      }
      *Value = *Value | *RHS;
    }
    return Value;
  }

  std::optional<long long> parseBitXor() {
    std::optional<long long> Value = parseBitAnd();
    while (Value && consume('^')) {
      std::optional<long long> RHS = parseBitAnd();
      if (!RHS) {
        return std::nullopt;
      }
      *Value = *Value ^ *RHS;
    }
    return Value;
  }

  std::optional<long long> parseBitAnd() {
    std::optional<long long> Value = parseShift();
    while (Value && consume('&')) {
      std::optional<long long> RHS = parseShift();
      if (!RHS) {
        return std::nullopt;
      }
      *Value = *Value & *RHS;
    }
    return Value;
  }

  std::optional<long long> parseShift() {
    std::optional<long long> Value = parseAdditive();
    while (Value) {
      if (consumeText("<<")) {
        std::optional<long long> RHS = parseAdditive();
        if (!RHS || *RHS < 0 || *RHS >= 63) {
          return std::nullopt;
        }
        *Value = *Value << *RHS;
      } else if (consumeText(">>")) {
        std::optional<long long> RHS = parseAdditive();
        if (!RHS || *RHS < 0 || *RHS >= 63) {
          return std::nullopt;
        }
        *Value = *Value >> *RHS;
      } else {
        break;
      }
    }
    return Value;
  }

  std::optional<long long> parseAdditive() {
    std::optional<long long> Value = parseMultiplicative();
    while (Value) {
      if (consume('+')) {
        std::optional<long long> RHS = parseMultiplicative();
        if (!RHS) {
          return std::nullopt;
        }
        *Value += *RHS;
      } else if (consume('-')) {
        std::optional<long long> RHS = parseMultiplicative();
        if (!RHS) {
          return std::nullopt;
        }
        *Value -= *RHS;
      } else {
        break;
      }
    }
    return Value;
  }

  std::optional<long long> parseMultiplicative() {
    std::optional<long long> Value = parseUnary();
    while (Value) {
      if (consume('*')) {
        std::optional<long long> RHS = parseUnary();
        if (!RHS) {
          return std::nullopt;
        }
        *Value *= *RHS;
      } else if (consume('/')) {
        std::optional<long long> RHS = parseUnary();
        if (!RHS || *RHS == 0) {
          return std::nullopt;
        }
        *Value /= *RHS;
      } else if (consume('%')) {
        std::optional<long long> RHS = parseUnary();
        if (!RHS || *RHS == 0) {
          return std::nullopt;
        }
        *Value %= *RHS;
      } else {
        break;
      }
    }
    return Value;
  }

  std::optional<long long> parseUnary() {
    if (consume('+')) {
      return parseUnary();
    }
    if (consume('-')) {
      std::optional<long long> Value = parseUnary();
      if (!Value) {
        return std::nullopt;
      }
      return -*Value;
    }
    return parsePrimary();
  }

  std::optional<long long> parsePrimary() {
    skipSpace();
    if (consume('(')) {
      std::optional<long long> Value = parseBitOr();
      if (!Value || !consume(')')) {
        return std::nullopt;
      }
      return Value;
    }
    if (Position < Text.size() &&
        (std::isdigit(static_cast<unsigned char>(Text[Position])) ||
         Text[Position] == '-')) {
      const size_t Start = Position;
      while (Position < Text.size() &&
             (std::isalnum(static_cast<unsigned char>(Text[Position])) ||
              Text[Position] == 'x' || Text[Position] == 'X')) {
        ++Position;
      }
      return parseStaticIntegerLiteral(Text.substr(Start, Position - Start));
    }
    if (Position < Text.size() &&
        (std::isalpha(static_cast<unsigned char>(Text[Position])) ||
         Text[Position] == '_')) {
      const size_t Start = Position;
      ++Position;
      while (Position < Text.size() &&
             (std::isalnum(static_cast<unsigned char>(Text[Position])) ||
              Text[Position] == '_')) {
        ++Position;
      }
      const std::string Name = Text.substr(Start, Position - Start);
      const auto Found = Constants.find(Name);
      if (Found != Constants.end()) {
        return Found->second;
      }
    }
    return std::nullopt;
  }
};

std::optional<long long>
evalStaticIntExpr(const std::string &Text,
                  const std::map<std::string, long long> &Constants) {
  if (std::optional<long long> Literal = parseStaticIntegerLiteral(Text)) {
    return Literal;
  }
  return StaticIntExprParser(Text, Constants).parse();
}

std::vector<std::string> splitTopLevelCommaText(const std::string &Text) {
  std::vector<std::string> Items;
  int Depth = 0;
  size_t Start = 0;
  for (size_t Index = 0; Index < Text.size(); ++Index) {
    const char C = Text[Index];
    if (C == '(' || C == '[' || C == '{') {
      ++Depth;
    } else if (C == ')' || C == ']' || C == '}') {
      if (Depth > 0) {
        --Depth;
      }
    } else if (C == ',' && Depth == 0) {
      Items.push_back(trim(Text.substr(Start, Index - Start)));
      Start = Index + 1;
    }
  }
  Items.push_back(trim(Text.substr(Start)));
  return Items;
}

std::map<std::string, long long> parseStaticIntConstants(const std::string &Text) {
  std::map<std::string, long long> Constants;
  bool Progress = true;
  while (Progress) {
    Progress = false;
    std::regex ConstPattern(
        R"cv((?:static\s+)?(?:(?:constexpr|const)\s+)?(?:(?:unsigned\s+)?(?:int|long|long\s+long)|unsigned)\s+([A-Za-z_]\w*)\s*=\s*([^;]+)\s*;)cv");
    for (std::sregex_iterator It(Text.begin(), Text.end(), ConstPattern), End;
         It != End; ++It) {
      const std::string Name = (*It)[1].str();
      if (Constants.count(Name) != 0) {
        continue;
      }
      if (std::optional<long long> Value =
              evalStaticIntExpr((*It)[2].str(), Constants)) {
        Constants[Name] = *Value;
        Progress = true;
      }
    }
    std::regex EnumPattern(R"cv(\benum\s+(?:[A-Za-z_]\w*\s*)?\{([^}]*)\})cv");
    for (std::sregex_iterator It(Text.begin(), Text.end(), EnumPattern), End;
         It != End; ++It) {
      long long NextValue = 0;
      for (const std::string &Item : splitTopLevelCommaText((*It)[1].str())) {
        if (Item.empty()) {
          continue;
        }
        std::smatch Match;
        std::regex EnumItemPattern(R"cv(^\s*([A-Za-z_]\w*)\s*(?:=\s*(.+))?$)cv");
        if (!std::regex_match(Item, Match, EnumItemPattern)) {
          continue;
        }
        const std::string Name = Match[1].str();
        std::optional<long long> Value = NextValue;
        if (Match[2].matched) {
          Value = evalStaticIntExpr(Match[2].str(), Constants);
        }
        if (!Value) {
          continue;
        }
        NextValue = *Value + 1;
        if (Constants.count(Name) == 0) {
          Constants[Name] = *Value;
          Progress = true;
        }
      }
    }
  }
  return Constants;
}

std::optional<int>
evalLaneIndexExpr(const std::string &Text,
                  const std::map<std::string, long long> &Constants) {
  std::optional<long long> Value = evalStaticIntExpr(Text, Constants);
  if (!Value || *Value < 0 ||
      *Value > static_cast<long long>(std::numeric_limits<int>::max())) {
    return std::nullopt;
  }
  return static_cast<int>(*Value);
}

bool isSafeSymbolicMaskIndexText(
    std::string Text, const std::map<std::string, long long> &Constants) {
  Text = trim(std::move(Text));
  if (!isSafeSymbolicMaskIndexText(Text)) {
    return false;
  }
  if (evalLaneIndexExpr(Text, Constants)) {
    return true;
  }
  bool SawLane = false;
  std::regex IdentifierPattern(R"cv(\b[A-Za-z_]\w*\b)cv");
  for (std::sregex_iterator It(Text.begin(), Text.end(), IdentifierPattern), End;
       It != End; ++It) {
    const std::string Name = It->str();
    if (Name == "Lane") {
      SawLane = true;
      continue;
    }
    if (Constants.count(Name) != 0) {
      continue;
    }
    return false;
  }
  return SawLane;
}

bool textContainsAny(llvm::StringRef Text, std::initializer_list<llvm::StringRef> Tokens) {
  for (llvm::StringRef Token : Tokens) {
    if (Text.contains(Token)) {
      return true;
    }
  }
  return false;
}

bool textContainsAny(llvm::StringRef Text,
                     const std::vector<std::string> &Tokens) {
  for (const std::string &Token : Tokens) {
    if (Text.contains(Token)) {
      return true;
    }
  }
  return false;
}

bool hasString(const std::vector<std::string> &Values, llvm::StringRef Value) {
  return std::find(Values.begin(), Values.end(), Value.str()) != Values.end();
}

std::vector<std::string> splitLines(const std::string &Text) {
  std::vector<std::string> Result;
  std::stringstream Stream(Text);
  std::string Line;
  while (std::getline(Stream, Line)) {
    Result.push_back(Line);
  }
  if (Result.empty()) {
    Result.push_back("");
  }
  return Result;
}

std::string slpOpcodeForText(llvm::StringRef Text) {
  for (const auto &Item : ReductionOperation) {
    if (Text.contains(Item.first)) {
      return Item.second;
    }
  }
  for (const auto &Item : VectorBuilderOperation) {
    if (Text.contains(Item.first) && Item.second != "binop" &&
        Item.second != "select") {
      return Item.second;
    }
  }
  for (const auto &Item : BuilderOperation) {
    if (Text.contains(Item.first)) {
      return Item.second;
    }
  }
  static const std::map<std::string, std::string> InstructionOpcodes{
      {"Instruction::Add", "add"}, {"Instruction::Sub", "sub"},
      {"Instruction::Mul", "mul"}, {"Instruction::Xor", "xor"},
      {"Instruction::Or", "or"},   {"Instruction::And", "and"}};
  for (const auto &Item : InstructionOpcodes) {
    if (Text.contains(Item.first)) {
      return Item.second;
    }
  }
  return "";
}

bool slpReductionText(llvm::StringRef Text) {
  return textContainsAny(Text, ReductionTokens);
}

llvm::json::Object slpReductionWidthInfo(llvm::StringRef Text);

std::vector<std::string> slpReductionUnsupportedReasons(llvm::StringRef Text) {
  std::vector<std::string> Reasons;
  const std::string Lower = Text.lower();
  llvm::json::Object WidthInfo = slpReductionWidthInfo(Text);
  const std::string WidthStatus = stringField(WidthInfo, "status");
  if ((Lower.find("createzext") != std::string::npos ||
       Lower.find("createsext") != std::string::npos ||
       Lower.find("createzextortrunc") != std::string::npos ||
       Lower.find("zext") != std::string::npos ||
       Lower.find("sext") != std::string::npos) &&
      WidthStatus != "complete") {
    Reasons.push_back(stringField(WidthInfo, "unsupported_reason").empty()
                          ? "unsupported-reduction-ambiguous-width"
                          : stringField(WidthInfo, "unsupported_reason"));
  }
  if ((Lower.find("createtrunc") != std::string::npos ||
       Lower.find("trunc") != std::string::npos) &&
      WidthStatus != "complete") {
    Reasons.push_back(stringField(WidthInfo, "unsupported_reason").empty()
                          ? "unsupported-reduction-ambiguous-width"
                          : stringField(WidthInfo, "unsupported_reason"));
  }
  return Reasons;
}

llvm::json::Object slpReductionWidthInfo(llvm::StringRef Text) {
  const std::string Lower = Text.lower();
  if (Lower.find("createzext") == std::string::npos &&
      Lower.find("createsext") == std::string::npos &&
      Lower.find("zext") == std::string::npos &&
      Lower.find("sext") == std::string::npos) {
    return {};
  }
  std::map<std::string, int> SymbolBits;
  std::map<std::string, std::set<int>> RoleWidths;
  std::set<std::string> SeenWidthRecords;
  std::set<int> Widths;
  llvm::json::Array Provenance;
  std::vector<std::string> Lines = splitLines(Text.str());
  std::regex WidthPattern(
      R"((Type::getInt|IntegerType::get\s*\([^,]+,\s*|i|input_bits\s*=|accumulator_bits\s*=|result_bits\s*=|bits\s*=|bitwidth\s*=)(8|16|32|64)(Ty)?\b)",
      std::regex::icase);
  std::regex ConstantPattern(
      R"(\b(const\s+)?(unsigned|int|size_t|auto)\s+([A-Za-z_]\w*)\s*=\s*(8|16|32|64)\b)");
  std::regex AssignmentPattern(R"(\b([A-Za-z_]\w*)\s*=\s*(8|16|32|64)\b)");
  std::regex TypeAliasPattern(
      R"(\b(Type|IntegerType|auto)\s*\*?\s*([A-Za-z_]\w*)\s*=\s*(Type::getInt|IntegerType::get\s*\([^,]+,\s*|i)(8|16|32|64)(Ty)?\b)",
      std::regex::icase);
  std::regex TypeAliasSymbolPattern(
      R"(\b(Type|IntegerType|auto)\s*\*?\s*([A-Za-z_]\w*)\s*=\s*IntegerType::get\s*\([^,]+,\s*([A-Za-z_]\w*)\s*\))",
      std::regex::icase);
  auto RoleForName = [](llvm::StringRef Name) {
    std::string LowerName = Name.lower();
    if (LowerName.find("input") != std::string::npos ||
        LowerName.find("narrow") != std::string::npos ||
        LowerName.find("scalar") != std::string::npos ||
        LowerName.find("lane") != std::string::npos ||
        LowerName.find("orig") != std::string::npos) {
      return std::string("input");
    }
    if (LowerName.find("wide") != std::string::npos ||
        LowerName.find("accum") != std::string::npos ||
        LowerName.find("zext") != std::string::npos ||
        LowerName.find("sext") != std::string::npos ||
        LowerName.find("extended") != std::string::npos) {
      return std::string("accumulator");
    }
    if (LowerName.find("result") != std::string::npos ||
        LowerName.find("trunc") != std::string::npos) {
      return std::string("result");
    }
    return std::string("unknown");
  };
  auto AddRecord = [&](size_t Index, const std::string &Line,
                       const std::string &Kind, const std::string &Role,
                       int Bits, const std::string &Symbol = "") {
    std::string Key = std::to_string(Index + 1) + "|" + Kind + "|" + Role +
                      "|" + std::to_string(Bits) + "|" + Symbol;
    if (!SeenWidthRecords.insert(Key).second) {
      return;
    }
    Widths.insert(Bits);
    if (Role != "unknown") {
      RoleWidths[Role].insert(Bits);
    }
    llvm::json::Object Record{
        {"line", static_cast<int>(Index + 1)},
        {"source", trim(Line)},
        {"kind", Kind},
        {"role", Role},
        {"bits", Bits},
    };
    if (!Symbol.empty()) {
      Record["symbol"] = Symbol;
    }
    Provenance.push_back(std::move(Record));
  };
  for (size_t Index = 0; Index < Lines.size(); ++Index) {
    std::string Line = Lines[Index];
    std::string LowerLine = llvm::StringRef(Line).lower();
    std::string Role = "unknown";
    if (LowerLine.find("input") != std::string::npos ||
        LowerLine.find("narrow") != std::string::npos ||
        LowerLine.find("scalar") != std::string::npos) {
      Role = "input";
    }
    if (LowerLine.find("wide") != std::string::npos ||
        LowerLine.find("accum") != std::string::npos ||
        LowerLine.find("zext") != std::string::npos ||
        LowerLine.find("sext") != std::string::npos ||
        LowerLine.find("extended") != std::string::npos) {
      Role = "accumulator";
    }
    if (LowerLine.find("result") != std::string::npos ||
        LowerLine.find("trunc") != std::string::npos) {
      Role = "result";
    }
    for (std::sregex_iterator It(Line.begin(), Line.end(), ConstantPattern),
         End;
         It != End; ++It) {
      std::string Symbol = (*It)[3].str();
      int Bits = std::stoi((*It)[4].str());
      SymbolBits[Symbol] = Bits;
      AddRecord(Index, Line, "width-constant", RoleForName(Symbol), Bits,
                Symbol);
    }
    for (std::sregex_iterator It(Line.begin(), Line.end(), AssignmentPattern),
         End;
         It != End; ++It) {
      std::string Symbol = (*It)[1].str();
      int Bits = std::stoi((*It)[2].str());
      SymbolBits[Symbol] = Bits;
      AddRecord(Index, Line, "width-constant", RoleForName(Symbol), Bits,
                Symbol);
    }
    for (std::sregex_iterator It(Line.begin(), Line.end(), TypeAliasPattern),
         End;
         It != End; ++It) {
      std::string Symbol = (*It)[2].str();
      int Bits = std::stoi((*It)[4].str());
      SymbolBits[Symbol] = Bits;
      AddRecord(Index, Line, "type-alias-width", RoleForName(Symbol), Bits,
                Symbol);
    }
    for (std::sregex_iterator It(Line.begin(), Line.end(), TypeAliasSymbolPattern),
         End;
         It != End; ++It) {
      std::string Symbol = (*It)[2].str();
      std::string SourceSymbol = (*It)[3].str();
      auto Found = SymbolBits.find(SourceSymbol);
      if (Found != SymbolBits.end()) {
        SymbolBits[Symbol] = Found->second;
        AddRecord(Index, Line, "type-alias-width", RoleForName(Symbol),
                  Found->second, Symbol);
      }
    }
    for (std::sregex_iterator It(Line.begin(), Line.end(), WidthPattern), End;
         It != End; ++It) {
      int Bits = std::stoi((*It)[2].str());
      AddRecord(Index, Line, "width-expression", Role, Bits);
    }
    std::smatch Match;
    std::regex ExtPattern(R"(Create(ZExt|SExt|ZExtOrTrunc)\s*\(.*,\s*([A-Za-z_]\w*)\s*\))");
    if (std::regex_search(Line, Match, ExtPattern)) {
      auto Found = SymbolBits.find(Match[2].str());
      if (Found != SymbolBits.end()) {
        AddRecord(Index, Line, "extension-target-width", "accumulator",
                  Found->second, Match[2].str());
      }
    }
    std::regex TruncPattern(R"(CreateTrunc\s*\(.*,\s*([A-Za-z_]\w*)\s*\))");
    if (std::regex_search(Line, Match, TruncPattern)) {
      auto Found = SymbolBits.find(Match[1].str());
      if (Found != SymbolBits.end()) {
        AddRecord(Index, Line, "trunc-target-width", "result", Found->second,
                  Match[1].str());
      }
    }
  }
  for (const std::string &Role : {"input", "accumulator", "result"}) {
    if (RoleWidths[Role].size() > 1) {
      return llvm::json::Object{
          {"status", "conflicting"},
          {"unsupported_reason", "unsupported-reduction-conflicting-width"},
          {"width_provenance", std::move(Provenance)},
      };
    }
  }
  int InputBits = RoleWidths["input"].empty() ? 0 : *RoleWidths["input"].begin();
  int AccumulatorBits = RoleWidths["accumulator"].empty()
                            ? 0
                            : *RoleWidths["accumulator"].begin();
  int ResultBits =
      RoleWidths["result"].empty() ? 0 : *RoleWidths["result"].begin();
  if ((InputBits == 0 || AccumulatorBits == 0) && Widths.size() == 2) {
    InputBits = InputBits == 0 ? *Widths.begin() : InputBits;
    AccumulatorBits =
        AccumulatorBits == 0 ? *std::next(Widths.begin()) : AccumulatorBits;
  }
  if (InputBits == 0 || AccumulatorBits == 0) {
    return llvm::json::Object{
        {"status", "ambiguous"},
        {"unsupported_reason", "unsupported-reduction-ambiguous-width"},
        {"width_provenance", std::move(Provenance)},
    };
  }
  if (AccumulatorBits < InputBits ||
      (ResultBits != 0 && ResultBits > AccumulatorBits)) {
    return llvm::json::Object{
        {"status", "conflicting"},
        {"unsupported_reason", "unsupported-reduction-conflicting-width"},
        {"width_provenance", std::move(Provenance)},
    };
  }
  const bool HasTrunc = Lower.find("createtrunc") != std::string::npos ||
                        Lower.find("trunc") != std::string::npos;
  if (ResultBits == 0) {
    ResultBits = HasTrunc ? InputBits : AccumulatorBits;
  }
  return llvm::json::Object{
      {"status", "complete"},
      {"input_bits", InputBits},
      {"accumulator_bits", AccumulatorBits},
      {"result_bits", ResultBits},
      {"extend_kind", Lower.find("createsext") != std::string::npos ||
                              Lower.find("sext") != std::string::npos
                          ? "sext"
                          : "zext"},
      {"width_provenance", std::move(Provenance)},
  };
}

std::string slpTransactionKindForOpcode(llvm::StringRef Opcode,
                                        llvm::StringRef Text = "") {
  if ((Opcode == "add" || Opcode == "mul" || Opcode == "and" ||
       Opcode == "or" || Opcode == "xor" || Opcode == "smin" ||
       Opcode == "smax" || Opcode == "umin" || Opcode == "umax" ||
       Opcode == "fadd" || Opcode == "fmul") &&
      slpReductionText(Text)) {
    return "slp-vectorize-reduction";
  }
  if (Opcode == "smin" || Opcode == "smax" || Opcode == "umin" ||
      Opcode == "umax") {
    return "slp-vectorize-minmax";
  }
  return "slp-vectorize-binop";
}

std::string minmaxPredicateForOpcode(llvm::StringRef Opcode) {
  if (Opcode == "smin") {
    return "slt";
  }
  if (Opcode == "smax") {
    return "sgt";
  }
  if (Opcode == "umin") {
    return "ult";
  }
  if (Opcode == "umax") {
    return "ugt";
  }
  return "";
}

llvm::json::Array intArray(const std::vector<int> &Values) {
  llvm::json::Array Result;
  for (int Value : Values) {
    Result.push_back(Value);
  }
  return Result;
}

std::vector<int> identityMap(int Lanes) {
  std::vector<int> Result;
  for (int Lane = 0; Lane < Lanes; ++Lane) {
    Result.push_back(Lane);
  }
  return Result;
}

std::vector<int> inversePermutation(const std::vector<int> &Map) {
  std::vector<int> Result(Map.size(), 0);
  for (size_t Index = 0; Index < Map.size(); ++Index) {
    if (Map[Index] >= 0 && static_cast<size_t>(Map[Index]) < Map.size()) {
      Result[Map[Index]] = static_cast<int>(Index);
    }
  }
  return Result;
}

bool validPermutation(const std::vector<int> &Map, int Lanes) {
  if (static_cast<int>(Map.size()) != Lanes) {
    return false;
  }
  std::vector<int> Sorted = Map;
  std::sort(Sorted.begin(), Sorted.end());
  for (int Index = 0; Index < Lanes; ++Index) {
    if (Sorted[Index] != Index) {
      return false;
    }
  }
  return true;
}

std::string validateLaneMapping(const llvm::json::Object &Mapping, int Lanes) {
  const auto *MapValue = Mapping.getArray("map");
  if (!MapValue) {
    return "missing-lane-map";
  }
  std::vector<int> Map;
  for (const llvm::json::Value &Value : *MapValue) {
    if (auto Number = Value.getAsInteger()) {
      Map.push_back(static_cast<int>(*Number));
    } else {
      return "invalid-lane-map";
    }
  }
  if (static_cast<int>(Map.size()) != Lanes) {
    return "lane-map-size-mismatch";
  }
  if (!validPermutation(Map, Lanes)) {
    return "unsupported-lane-map-kind";
  }
  return "";
}

llvm::json::Object makeLaneMapping(const std::string &Kind, int Lanes,
                                   const std::vector<int> &Map, unsigned Line,
                                   const std::string &Source,
                                   const std::string &SourceKind) {
  llvm::json::Object Mapping{
      {"kind", Kind},
      {"lanes", Lanes},
      {"map", intArray(Map)},
      {"inverse_map", intArray(inversePermutation(Map))},
      {"source", llvm::json::Object{{"line", static_cast<int>(Line)},
                                     {"source", Source},
                                     {"kind", SourceKind}}},
  };
  return Mapping;
}

std::optional<std::vector<int>> explicitLaneMapFromSource(
    const std::string &Source, std::initializer_list<std::string> Names) {
  for (const std::string &Name : Names) {
    std::regex Pattern("\\b" + Name + R"(\s*\[\s*\d+\s*\]\s*=\s*\{([^}]*)\})");
    std::smatch Match;
    if (!std::regex_search(Source, Match, Pattern)) {
      continue;
    }
    std::vector<int> Values;
    std::stringstream Stream(Match[1].str());
    std::string Item;
    while (std::getline(Stream, Item, ',')) {
      Values.push_back(std::stoi(trim(Item)));
    }
    return Values;
  }
  return std::nullopt;
}

unsigned lineForToken(unsigned StartLine, const std::vector<std::string> &Lines,
                      std::initializer_list<llvm::StringRef> Tokens) {
  for (size_t Index = 0; Index < Lines.size(); ++Index) {
    for (llvm::StringRef Token : Tokens) {
      if (llvm::StringRef(Lines[Index]).contains(Token)) {
        return StartLine + static_cast<unsigned>(Index);
      }
    }
  }
  return StartLine;
}

unsigned lineForToken(unsigned StartLine, const std::vector<std::string> &Lines,
                      const std::vector<std::string> &Tokens) {
  for (size_t Index = 0; Index < Lines.size(); ++Index) {
    for (const std::string &Token : Tokens) {
      if (llvm::StringRef(Lines[Index]).contains(Token)) {
        return StartLine + static_cast<unsigned>(Index);
      }
    }
  }
  return StartLine;
}

std::string sourceLineForToken(const std::vector<std::string> &Lines,
                               std::initializer_list<llvm::StringRef> Tokens) {
  for (const std::string &Line : Lines) {
    for (llvm::StringRef Token : Tokens) {
      if (llvm::StringRef(Line).contains(Token)) {
        return trim(Line);
      }
    }
  }
  return Lines.empty() ? "" : trim(Lines.front());
}

std::string sourceLineForToken(const std::vector<std::string> &Lines,
                               const std::vector<std::string> &Tokens) {
  for (const std::string &Line : Lines) {
    for (const std::string &Token : Tokens) {
      if (llvm::StringRef(Line).contains(Token)) {
        return trim(Line);
      }
    }
  }
  return Lines.empty() ? "" : trim(Lines.front());
}

std::string replacementValue(llvm::StringRef RewriteSource) {
  if (RewriteSource.contains("Constant::getNullValue") ||
      RewriteSource.contains("ConstantInt::get")) {
    return "0";
  }
  const std::string Text = RewriteSource.str();
  const std::string Call = "replaceInstUsesWith";
  const size_t CallPos = Text.find(Call);
  if (CallPos != std::string::npos) {
    const size_t Open = Text.find('(', CallPos + Call.size());
    if (Open != std::string::npos) {
      int Depth = 0;
      size_t Comma = std::string::npos;
      size_t Close = std::string::npos;
      for (size_t Index = Open; Index < Text.size(); ++Index) {
        const char C = Text[Index];
        if (C == '(') {
          ++Depth;
        } else if (C == ')') {
          --Depth;
          if (Depth == 0) {
            Close = Index;
            break;
          }
        } else if (C == ',' && Depth == 1) {
          Comma = Index;
        }
      }
      if (Comma != std::string::npos && Close != std::string::npos &&
          Comma + 1 < Close) {
        return trim(Text.substr(Comma + 1, Close - Comma - 1));
      }
    }
  }
  if (RewriteSource.contains("Op0")) {
    return "Op0";
  }
  if (RewriteSource.contains("Op1")) {
    return "Op1";
  }
  return "";
}

llvm::json::Value operandJson(llvm::StringRef Operand) {
  if (Operand == "0") {
    return llvm::json::Object{{"constant", 0}};
  }
  if (Operand == "1") {
    return llvm::json::Object{{"constant", 1}};
  }
  return Operand.str();
}

llvm::json::Object guardObject(llvm::StringRef Kind, llvm::StringRef Role,
                               llvm::StringRef Source,
                               bool RequireCatalog = false);

llvm::json::Array guardJson(llvm::StringRef PredicateSource) {
  llvm::json::Array Guards;
  const std::string Predicate = PredicateSource.str();
  auto addGuard = [&](llvm::StringRef Kind, llvm::StringRef Role,
                      bool RequireCatalog = false) {
    Guards.push_back(
        guardObject(Kind, Role, Predicate, RequireCatalog));
  };
  if (PredicateSource.contains("match(")) {
    addGuard("matcher", "semantic");
  }
  if (PredicateSource.contains("Op0 == Op1") ||
      PredicateSource.contains("LHS == RHS")) {
    addGuard("equality", "semantic");
  }
  if (PredicateSource.contains("isInstructionTriviallyDead")) {
    addGuard("dead-instruction", "semantic");
  }
  const bool HasGlobalInitializerEvidence =
      PredicateSource.contains("isGlobalInitializerDead") ||
      PredicateSource.contains("hasLocalLinkage");
  if (HasGlobalInitializerEvidence) {
    addGuard("dead-global-initializer", "semantic");
  }
  if (!HasGlobalInitializerEvidence &&
      (PredicateSource.contains("use_empty") ||
       PredicateSource.contains("user_empty") ||
       PredicateSource.contains("hasNUses") ||
       PredicateSource.contains("users().empty") ||
       PredicateSource.contains("hasNUsesOrMore"))) {
    addGuard("unused-alloca", "semantic");
  }
  if (PredicateSource.contains("VectorXorSelf") ||
      PredicateSource.contains("isIdentityMask") ||
      PredicateSource.contains("isIdentityWithExtract") ||
      PredicateSource.contains("isSplatMask") ||
      PredicateSource.contains("sameLaneExtractInsert") ||
      PredicateSource.contains("ReductionAddZero") ||
      PredicateSource.contains("VectorSubZero") ||
      PredicateSource.contains("VectorOrZero") ||
      PredicateSource.contains("VectorAndAllOnes") ||
      PredicateSource.contains("VectorSMin") ||
      PredicateSource.contains("VectorSMax") ||
      PredicateSource.contains("VectorUMin") ||
      PredicateSource.contains("VectorUMax") ||
      PredicateSource.contains("VectorAbs") ||
      PredicateSource.contains("insertExtractIdentity") ||
      PredicateSource.contains("ReductionAddSingleLane") ||
      PredicateSource.contains("ScalableVectorAddZero") ||
      PredicateSource.contains("ScalableVectorMulOne") ||
      PredicateSource.contains("ScalableVectorXorSelf") ||
      PredicateSource.contains("ScalableVectorSubZero") ||
      PredicateSource.contains("ScalableVectorOrZero") ||
      PredicateSource.contains("ScalableVectorAndAllOnes") ||
      PredicateSource.contains("ScalableReductionAddZero")) {
    addGuard("vector-helper", "semantic");
  }
  if (PredicateSource.contains("!hasPoisonGeneratingFlags") ||
      PredicateSource.contains("hasNoPoisonGeneratingFlags")) {
    Guards.push_back(guardObject("no-poison-generating-flags",
                                 "modeled-side-condition", Predicate, true));
  }
  if (PredicateSource.contains("isGuaranteedNotToBePoison")) {
    addGuard("guaranteed-not-poison", "unmodeled-side-condition", true);
  }
  if (PredicateSource.contains("isKnownNonZero")) {
    addGuard("known-nonzero", "unmodeled-side-condition", true);
  }
  if (PredicateSource.contains("isKnownPositive")) {
    addGuard("known-positive", "unmodeled-side-condition", true);
  }
  if (PredicateSource.contains("isKnownNonNegative")) {
    addGuard("known-nonnegative", "unmodeled-side-condition", true);
  }
  if (PredicateSource.contains("MaskedValueIsZero")) {
    addGuard("masked-value-is-zero", "unmodeled-side-condition", true);
  }
  if (PredicateSource.contains("isKnownPowerOf2") ||
      PredicateSource.contains("isKnownToBeAPowerOfTwo")) {
    addGuard("known-power-of-two", "unmodeled-side-condition", true);
  }
  if (PredicateSource.contains("hasOneUse")) {
    addGuard("one-use", "unmodeled-side-condition", true);
  }
  if (PredicateSource.contains("shouldOptimizeForSize") ||
      PredicateSource.contains("TargetTransformInfo") ||
      PredicateSource.contains("TTI") ||
      PredicateSource.contains("getInstructionCost") ||
      PredicateSource.contains("isProfitable")) {
    addGuard("profitability", "profitability", true);
  }
  if (Guards.empty()) {
    addGuard("unknown", "unmodeled-side-condition");
  }
  return Guards;
}

llvm::json::Object guardObject(llvm::StringRef Kind, llvm::StringRef Role,
                               llvm::StringRef Source, bool RequireCatalog) {
  llvm::json::Object Guard{{"kind", Kind.str()}, {"source", Source.str()}};
  auto It = GuardCatalog.find(Kind.str());
  if (It == GuardCatalog.end()) {
    if (RequireCatalog) {
      MissingGuardKinds.insert(Kind.str());
    }
    Guard["role"] = Role.str();
    return Guard;
  }
  const GuardCatalogEntry &Entry = It->second;
  Guard["role"] = Entry.Role.empty() ? Role.str() : Entry.Role;
  if (!Entry.ProofEffect.empty()) {
    Guard["proof_effect"] = Entry.ProofEffect;
  }
  if (!Entry.FormalEffect.empty()) {
    Guard["formal_effect"] = Entry.FormalEffect;
  }
  if (!Entry.AuditCategory.empty()) {
    Guard["audit_category"] = Entry.AuditCategory;
  }
  return Guard;
}

std::string exprText(const Expr *Expression, const SourceManager &SM,
                     const LangOptions &LangOpts) {
  if (!Expression) {
    return "";
  }
  return sourceText(SM, LangOpts, Expression->getSourceRange());
}

std::string calleeName(const CallExpr *Call) {
  if (!Call) {
    return "";
  }
  if (const auto *MemberCall = dyn_cast<CXXMemberCallExpr>(Call)) {
    if (const CXXMethodDecl *Method = MemberCall->getMethodDecl()) {
      return Method->getNameAsString();
    }
  }
  if (const FunctionDecl *Direct = Call->getDirectCallee()) {
    return Direct->getNameAsString();
  }
  return "";
}

std::string directVariableSymbol(const Expr *Expression) {
  if (!Expression) {
    return "";
  }
  Expression = Expression->IgnoreParenImpCasts();
  if (const auto *Reference = dyn_cast<DeclRefExpr>(Expression)) {
    if (const auto *Variable = dyn_cast<VarDecl>(Reference->getDecl())) {
      return Variable->getNameAsString();
    }
  }
  return "";
}

std::string accessPathSymbol(const Expr *Expression, const SourceManager &SM,
                             const LangOptions &LangOpts) {
  if (!Expression) {
    return "";
  }
  Expression = Expression->IgnoreParenImpCasts();
  if (const auto *Reference = dyn_cast<DeclRefExpr>(Expression)) {
    if (const auto *Variable = dyn_cast<VarDecl>(Reference->getDecl())) {
      return Variable->getNameAsString();
    }
    return "";
  }
  if (const auto *Member = dyn_cast<MemberExpr>(Expression)) {
    const std::string Base =
        accessPathSymbol(Member->getBase(), SM, LangOpts);
    if (Base.empty() || !Member->getMemberDecl()) {
      return "";
    }
    return Base + (Member->isArrow() ? "->" : ".") +
           Member->getMemberDecl()->getNameAsString();
  }
  if (const auto *Subscript = dyn_cast<ArraySubscriptExpr>(Expression)) {
    const std::string Base =
        accessPathSymbol(Subscript->getBase(), SM, LangOpts);
    const std::string Index = trim(exprText(Subscript->getIdx(), SM, LangOpts));
    if (Base.empty() || Index.empty()) {
      return "";
    }
    return Base + "[" + Index + "]";
  }
  if (const auto *Unary = dyn_cast<UnaryOperator>(Expression)) {
    return accessPathSymbol(Unary->getSubExpr(), SM, LangOpts);
  }
  return "";
}

llvm::json::Array callArgumentsJson(const CallExpr *Call,
                                    const SourceManager &SM,
                                    const LangOptions &LangOpts) {
  llvm::json::Array Arguments;
  if (!Call) {
    return Arguments;
  }
  for (const Expr *Argument : Call->arguments()) {
    if (!Argument || Argument->getBeginLoc().isInvalid() ||
        !SM.isWrittenInMainFile(Argument->getBeginLoc())) {
      continue;
    }
    llvm::json::Object Entry{
        {"source", exprText(Argument, SM, LangOpts)},
        {"line", static_cast<int64_t>(SM.getSpellingLineNumber(Argument->getBeginLoc()))},
        {"column", static_cast<int64_t>(SM.getSpellingColumnNumber(Argument->getBeginLoc()))},
    };
    const std::string Symbol = accessPathSymbol(Argument, SM, LangOpts);
    if (!Symbol.empty()) {
      Entry["symbol"] = Symbol;
    }
    Arguments.push_back(std::move(Entry));
  }
  return Arguments;
}

void appendSlpCall(const CallExpr *Call, const SourceManager &SM,
                   const LangOptions &LangOpts, llvm::StringRef AssignedSymbol,
                   llvm::json::Array &Calls,
                   std::vector<std::string> &CalledFunctions) {
  if (!Call) {
    return;
  }
  const std::string Name = calleeName(Call);
  llvm::json::Object Entry{
      {"kind", "call"},
      {"callee", Name},
      {"source", sourceText(SM, LangOpts, Call->getSourceRange())},
      {"line", static_cast<int64_t>(SM.getSpellingLineNumber(Call->getBeginLoc()))},
      {"column", static_cast<int64_t>(SM.getSpellingColumnNumber(Call->getBeginLoc()))},
      {"arguments", callArgumentsJson(Call, SM, LangOpts)},
  };
  if (!AssignedSymbol.empty()) {
    Entry["assigned_symbol"] = AssignedSymbol.str();
  }
  Calls.push_back(std::move(Entry));
  if (!Name.empty() && !hasString(CalledFunctions, Name)) {
    CalledFunctions.push_back(Name);
  }
}

llvm::json::Array guardJsonForCondition(const Expr *Condition,
                                        const SourceManager &SM,
                                        const LangOptions &LangOpts);

void collectCallNodes(const Stmt *Node, const SourceManager &SM,
                      const LangOptions &LangOpts,
                      llvm::json::Array &Nodes) {
  if (!Node) {
    return;
  }
  if (const auto *Call = dyn_cast<CallExpr>(Node)) {
    llvm::json::Object Entry{
        {"kind", "call"},
        {"callee", calleeName(Call)},
        {"source", sourceText(SM, LangOpts, Call->getSourceRange())},
        {"line", static_cast<int64_t>(SM.getSpellingLineNumber(Call->getBeginLoc()))},
        {"column", static_cast<int64_t>(SM.getSpellingColumnNumber(Call->getBeginLoc()))},
    };
    Nodes.push_back(std::move(Entry));
  }
  for (const Stmt *Child : Node->children()) {
    collectCallNodes(Child, SM, LangOpts, Nodes);
  }
}

void collectSlpAstBackbone(const Stmt *Node, const SourceManager &SM,
                           const LangOptions &LangOpts,
                           llvm::json::Array &Calls,
                           llvm::json::Array &Conditions,
                           std::vector<std::string> &CalledFunctions,
                           llvm::StringRef AssignedSymbol = "") {
  if (!Node) {
    return;
  }
  if (const auto *Declaration = dyn_cast<DeclStmt>(Node)) {
    for (const Decl *Declared : Declaration->decls()) {
      const auto *Variable = dyn_cast<VarDecl>(Declared);
      if (!Variable || !Variable->hasInit()) {
        continue;
      }
      const Expr *Init = Variable->getInit()->IgnoreParenImpCasts();
      const llvm::StringRef Assigned =
          isa<CallExpr>(Init) ? llvm::StringRef(Variable->getName()) : "";
      collectSlpAstBackbone(Init, SM, LangOpts, Calls, Conditions,
                            CalledFunctions, Assigned);
    }
    return;
  }
  if (const auto *Binary = dyn_cast<BinaryOperator>(Node)) {
    if (Binary->isAssignmentOp()) {
      const Expr *RHS = Binary->getRHS()->IgnoreParenImpCasts();
      const std::string Assigned =
          isa<CallExpr>(RHS) ? directVariableSymbol(Binary->getLHS()) : "";
      collectSlpAstBackbone(RHS, SM, LangOpts, Calls, Conditions,
                            CalledFunctions, Assigned);
      return;
    }
  }
  if (const auto *Call = dyn_cast<CallExpr>(Node)) {
    appendSlpCall(Call, SM, LangOpts, AssignedSymbol, Calls, CalledFunctions);
    for (const Expr *Argument : Call->arguments()) {
      collectSlpAstBackbone(Argument, SM, LangOpts, Calls, Conditions,
                            CalledFunctions);
    }
    return;
  }
  if (const auto *If = dyn_cast<IfStmt>(Node)) {
    if (const Expr *Condition = If->getCond()) {
      Conditions.push_back(llvm::json::Object{
          {"kind", "if-condition"},
          {"source", exprText(Condition, SM, LangOpts)},
          {"line", static_cast<int64_t>(SM.getSpellingLineNumber(Condition->getBeginLoc()))},
          {"column", static_cast<int64_t>(SM.getSpellingColumnNumber(Condition->getBeginLoc()))},
          {"guards", guardJsonForCondition(Condition, SM, LangOpts)},
      });
    }
  }
  for (const Stmt *Child : Node->children()) {
    collectSlpAstBackbone(Child, SM, LangOpts, Calls, Conditions,
                          CalledFunctions);
  }
}

const CallExpr *firstCallNamed(const Stmt *Node, llvm::StringRef Name) {
  if (!Node) {
    return nullptr;
  }
  if (const auto *Call = dyn_cast<CallExpr>(Node)) {
    if (calleeName(Call) == Name) {
      return Call;
    }
  }
  for (const Stmt *Child : Node->children()) {
    if (const CallExpr *Found = firstCallNamed(Child, Name)) {
      return Found;
    }
  }
  return nullptr;
}

const CallExpr *firstCallWithAnyName(const Stmt *Node,
                                     std::initializer_list<llvm::StringRef> Names) {
  if (!Node) {
    return nullptr;
  }
  if (const auto *Call = dyn_cast<CallExpr>(Node)) {
    const std::string Name = calleeName(Call);
    for (llvm::StringRef Candidate : Names) {
      if (Name == Candidate) {
        return Call;
      }
    }
  }
  for (const Stmt *Child : Node->children()) {
    if (const CallExpr *Found = firstCallWithAnyName(Child, Names)) {
      return Found;
    }
  }
  return nullptr;
}

bool hasMemberCallNamed(const Stmt *Node, llvm::StringRef Name) {
  if (!Node) {
    return false;
  }
  if (const auto *Call = dyn_cast<CXXMemberCallExpr>(Node)) {
    if (calleeName(Call) == Name) {
      return true;
    }
  }
  for (const Stmt *Child : Node->children()) {
    if (hasMemberCallNamed(Child, Name)) {
      return true;
    }
  }
  return false;
}

const CXXMemberCallExpr *firstMemberCallNamed(const Stmt *Node,
                                              llvm::StringRef Name) {
  if (!Node) {
    return nullptr;
  }
  if (const auto *Call = dyn_cast<CXXMemberCallExpr>(Node)) {
    if (calleeName(Call) == Name) {
      return Call;
    }
  }
  for (const Stmt *Child : Node->children()) {
    if (const CXXMemberCallExpr *Found = firstMemberCallNamed(Child, Name)) {
      return Found;
    }
  }
  return nullptr;
}

std::string globalInitializerValueTypeExpr(const Expr *Replacement,
                                           const SourceManager &SM,
                                           const LangOptions &LangOpts) {
  if (!Replacement) {
    return "";
  }
  if (const auto *Call = dyn_cast<CallExpr>(Replacement->IgnoreParenImpCasts())) {
    if (Call->getNumArgs() >= 1) {
      return exprText(Call->getArg(0), SM, LangOpts);
    }
  }
  return "";
}

bool isDefaultNullGlobalInitializerReplacement(llvm::StringRef Text) {
  return Text.contains("Constant::getNullValue") ||
         Text.contains("ConstantAggregateZero") ||
         Text.contains("zeroinitializer") || Text == "nullptr" ||
         Text == "0";
}

struct PatternTerm {
  enum class Kind { Invalid, Symbol, Constant, Operation };
  Kind TermKind = Kind::Invalid;
  std::string Symbol;
  int64_t Constant = 0;
  std::string Operation;
  bool Commutative = false;
  std::vector<PatternTerm> Operands;
};

std::string symbolForExpr(const Expr *Expression, const SourceManager &SM,
                          const LangOptions &LangOpts) {
  if (!Expression) {
    return "";
  }
  Expression = Expression->IgnoreParenImpCasts();
  if (const auto *Ref = dyn_cast<DeclRefExpr>(Expression)) {
    return Ref->getDecl()->getNameAsString();
  }
  if (const auto *Unary = dyn_cast<UnaryOperator>(Expression)) {
    return symbolForExpr(Unary->getSubExpr(), SM, LangOpts);
  }
  return exprText(Expression, SM, LangOpts);
}

std::optional<uint64_t> unsignedIntegerForExpr(const Expr *Expression) {
  if (!Expression) {
    return std::nullopt;
  }
  Expression = Expression->IgnoreParenImpCasts();
  if (const auto *Literal = dyn_cast<IntegerLiteral>(Expression)) {
    return Literal->getValue().getLimitedValue();
  }
  return std::nullopt;
}

PatternTerm symbolTerm(std::string Symbol) {
  PatternTerm Term;
  Term.TermKind = PatternTerm::Kind::Symbol;
  Term.Symbol = std::move(Symbol);
  return Term;
}

PatternTerm constantTerm(int64_t Value) {
  PatternTerm Term;
  Term.TermKind = PatternTerm::Kind::Constant;
  Term.Constant = Value;
  return Term;
}

PatternTerm operationTerm(std::string Operation, PatternTerm LHS,
                          PatternTerm RHS, bool Commutative) {
  PatternTerm Term;
  Term.TermKind = PatternTerm::Kind::Operation;
  Term.Operation = std::move(Operation);
  Term.Commutative = Commutative;
  Term.Operands.push_back(std::move(LHS));
  Term.Operands.push_back(std::move(RHS));
  return Term;
}

PatternTerm interpretPattern(const Expr *Expression, const SourceManager &SM,
                             const LangOptions &LangOpts) {
  if (!Expression) {
    return {};
  }
  Expression = Expression->IgnoreParenImpCasts();
  const auto *Call = dyn_cast<CallExpr>(Expression);
  if (!Call) {
    return {};
  }
  const std::string Name = calleeName(Call);
  auto ConstantIt = ConstantMatcherValue.find(Name);
  if (ConstantIt != ConstantMatcherValue.end()) {
    return constantTerm(ConstantIt->second);
  }
  if (SymbolMatcherNames.count(Name) != 0 && Call->getNumArgs() >= 1) {
    return symbolTerm(symbolForExpr(Call->getArg(0), SM, LangOpts));
  }
  auto MatcherIt = MatcherOperation.find(Name);
  if (MatcherIt != MatcherOperation.end() && Call->getNumArgs() >= 2) {
    const bool Commutative = llvm::StringRef(Name).starts_with("m_c_");
    return operationTerm(MatcherIt->second,
                         interpretPattern(Call->getArg(0), SM, LangOpts),
                         interpretPattern(Call->getArg(1), SM, LangOpts),
                         Commutative);
  }
  return {};
}

llvm::json::Value termValueJson(const PatternTerm &Term) {
  if (Term.TermKind == PatternTerm::Kind::Constant) {
    return llvm::json::Object{{"constant", Term.Constant}};
  }
  if (Term.TermKind == PatternTerm::Kind::Symbol) {
    return llvm::json::Object{{"symbol", Term.Symbol}};
  }
  return llvm::json::Object{{"unknown", true}};
}

const Expr *singleReturnExpression(const FunctionDecl *Function,
                                   unsigned &ReturnCount) {
  ReturnCount = 0;
  const Expr *Result = nullptr;
  if (!Function || !Function->hasBody()) {
    return nullptr;
  }
  std::function<void(const Stmt *)> Visit = [&](const Stmt *Node) {
    if (!Node) {
      return;
    }
    if (const auto *Return = dyn_cast<ReturnStmt>(Node)) {
      ++ReturnCount;
      Result = Return->getRetValue();
      return;
    }
    for (const Stmt *Child : Node->children()) {
      Visit(Child);
    }
  };
  Visit(Function->getBody());
  return ReturnCount == 1 ? Result : nullptr;
}

llvm::json::Object helperSummaryForCall(
    const CallExpr *Call, const SourceManager &SM, const LangOptions &LangOpts,
    const std::map<std::string, llvm::json::Value> &Bindings,
    unsigned HelperDepth);

std::optional<std::string> builderOperationForCallName(llvm::StringRef Name) {
  auto It = BuilderOperation.find(Name.str());
  if (It == BuilderOperation.end()) {
    return std::nullopt;
  }
  return It->second;
}

llvm::json::Value exprValueJsonWithBindings(
    const Expr *Expression, const SourceManager &SM, const LangOptions &LangOpts,
    const std::map<std::string, llvm::json::Value> &Bindings,
    unsigned HelperDepth) {
  if (!Expression) {
    return llvm::json::Object{{"unknown", true}};
  }
  Expression = Expression->IgnoreParenImpCasts();
  if (const auto *Reference = dyn_cast<DeclRefExpr>(Expression)) {
    if (const auto *Variable = dyn_cast<VarDecl>(Reference->getDecl())) {
      auto Found = Bindings.find(Variable->getNameAsString());
      if (Found != Bindings.end()) {
        return cloneJson(Found->second);
      }
    }
  }
  if (const auto *Call = dyn_cast<CallExpr>(Expression)) {
    const std::string Name = calleeName(Call);
    if (Name == "getNullValue" || Name == "get") {
      return llvm::json::Object{{"constant", 0}};
    }
    llvm::StringRef NameRef(Name);
    if (std::optional<std::string> Operation =
            builderOperationForCallName(NameRef);
        Operation && Call->getNumArgs() >= 2) {
      llvm::json::Array Operands;
      Operands.push_back(exprValueJsonWithBindings(Call->getArg(0), SM, LangOpts,
                                                   Bindings, HelperDepth));
      Operands.push_back(exprValueJsonWithBindings(Call->getArg(1), SM, LangOpts,
                                                   Bindings, HelperDepth));
      return llvm::json::Object{{"operation", *Operation},
                                {"operands", std::move(Operands)}};
    }
    if (HelperDepth == 0) {
      llvm::json::Object Summary =
          helperSummaryForCall(Call, SM, LangOpts, Bindings, HelperDepth + 1);
      if (auto Status = Summary.getString("status");
          Status && *Status == "complete") {
        if (const llvm::json::Value *Value = Summary.get("value")) {
          return cloneJson(*Value);
        }
      }
    }
  }
  return termValueJson(symbolTerm(symbolForExpr(Expression, SM, LangOpts)));
}

llvm::json::Value exprValueJson(const Expr *Expression, const SourceManager &SM,
                                const LangOptions &LangOpts) {
  return exprValueJsonWithBindings(Expression, SM, LangOpts, {}, 0);
}

llvm::json::Object helperSummaryForCall(
    const CallExpr *Call, const SourceManager &SM, const LangOptions &LangOpts,
    const std::map<std::string, llvm::json::Value> &Bindings,
    unsigned HelperDepth) {
  llvm::json::Object Summary{{"status", "unsupported"}};
  if (!Call) {
    Summary["reason"] = "missing-helper-call";
    return Summary;
  }
  const FunctionDecl *Direct = Call->getDirectCallee();
  if (!Direct || !Direct->hasBody()) {
    Summary["name"] = calleeName(Call);
    Summary["reason"] = "missing-helper-body";
    return Summary;
  }
  Summary["name"] = Direct->getNameAsString();
  unsigned ReturnCount = 0;
  const Expr *Return = singleReturnExpression(Direct, ReturnCount);
  if (!Return) {
    Summary["reason"] = ReturnCount > 1 ? "multiple-helper-returns"
                                        : "missing-helper-return";
    Summary["return_count"] = static_cast<int64_t>(ReturnCount);
    return Summary;
  }
  if (Call->getNumArgs() < Direct->getNumParams()) {
    Summary["reason"] = "incomplete-helper-arguments";
    return Summary;
  }
  std::map<std::string, llvm::json::Value> HelperBindings;
  for (unsigned Index = 0; Index < Direct->getNumParams(); ++Index) {
    const ParmVarDecl *Parameter = Direct->getParamDecl(Index);
    if (!Parameter || Parameter->getName().empty()) {
      continue;
    }
    HelperBindings.insert_or_assign(
        Parameter->getNameAsString(),
        exprValueJsonWithBindings(Call->getArg(Index), SM, LangOpts, Bindings,
                                  HelperDepth));
  }
  llvm::json::Value Value =
      exprValueJsonWithBindings(Return, SM, LangOpts, HelperBindings, HelperDepth);
  Summary["status"] = "complete";
  Summary["return_source"] = exprText(Return, SM, LangOpts);
  Summary["value"] = cloneJson(Value);
  return Summary;
}

void collectLocalValueDefinitions(const Stmt *Node, const SourceManager &SM,
                                  const LangOptions &LangOpts,
                                  llvm::json::Array &Definitions) {
  if (!Node) {
    return;
  }
  if (const auto *Declaration = dyn_cast<DeclStmt>(Node)) {
    for (const Decl *Declared : Declaration->decls()) {
      const auto *Variable = dyn_cast<VarDecl>(Declared);
      if (!Variable || !Variable->hasInit() || Variable->getName().empty()) {
        continue;
      }
      const Expr *Init = Variable->getInit();
      llvm::json::Object Definition{
          {"name", Variable->getNameAsString()},
          {"source", exprText(Init, SM, LangOpts)},
          {"value", exprValueJson(Init, SM, LangOpts)},
      };
      if (const auto *Call = dyn_cast<CallExpr>(Init->IgnoreParenImpCasts())) {
        llvm::json::Object Summary = helperSummaryForCall(Call, SM, LangOpts, {}, 0);
        if (Summary.getString("name")) {
          Definition["helper_summary"] = std::move(Summary);
        }
      }
      Definitions.push_back(std::move(Definition));
    }
  }
  for (const Stmt *Child : Node->children()) {
    collectLocalValueDefinitions(Child, SM, LangOpts, Definitions);
  }
}

std::optional<llvm::json::Value>
localValueDefinitionForName(const Stmt *Node, llvm::StringRef Name,
                            const SourceManager &SM,
                            const LangOptions &LangOpts) {
  if (!Node || Name.empty()) {
    return std::nullopt;
  }
  if (const auto *Declaration = dyn_cast<DeclStmt>(Node)) {
    for (const Decl *Declared : Declaration->decls()) {
      const auto *Variable = dyn_cast<VarDecl>(Declared);
      if (!Variable || !Variable->hasInit() || Variable->getName() != Name) {
        continue;
      }
      return exprValueJson(Variable->getInit(), SM, LangOpts);
    }
  }
  for (const Stmt *Child : Node->children()) {
    if (auto Value = localValueDefinitionForName(Child, Name, SM, LangOpts)) {
      return Value;
    }
  }
  return std::nullopt;
}

llvm::json::Array termOperandsJson(const PatternTerm &Term) {
  llvm::json::Array Operands;
  for (const PatternTerm &Operand : Term.Operands) {
    Operands.push_back(termValueJson(Operand));
  }
  return Operands;
}

const llvm::json::Object *semanticFactsForMarker(
    llvm::StringRef Marker,
    const std::map<std::string, llvm::json::Value> &SemanticRegistry) {
  auto It = SemanticRegistry.find(Marker.str());
  if (It == SemanticRegistry.end()) {
    return nullptr;
  }
  return It->second.getAsObject();
}

std::string sourceIntentOperationForMarker(
    llvm::StringRef Marker,
    const std::map<std::string, llvm::json::Value> &SemanticRegistry) {
  if (const llvm::json::Object *Facts =
          semanticFactsForMarker(Marker, SemanticRegistry)) {
    const std::optional<llvm::StringRef> Shape = Facts->getString("shape");
    const std::optional<llvm::StringRef> Operation =
        Facts->getString("operation");
    if (Shape && Operation && *Shape == "scalar" && !Operation->empty()) {
      if (*Operation == "erase" && Marker == "probe.dce.dead-instruction") {
        return "dead-instruction";
      }
      return Operation->str();
    }
    if (Shape && Operation && *Shape == "global" &&
        *Operation == "erase" &&
        Marker == "probe.globalopt.dead-initializer") {
      return "global-initializer";
    }
  }
  if (Marker == "probe.instcombine.add-zero") {
    return "add";
  }
  if (Marker == "probe.instcombine.sub-zero") {
    return "sub";
  }
  if (Marker == "probe.instcombine.mul-one") {
    return "mul";
  }
  if (Marker == "probe.instcombine.or-zero") {
    return "or";
  }
  if (Marker == "probe.instcombine.and-allones" ||
      Marker == "probe.instcombine.and-self") {
    return "and";
  }
  if (Marker == "probe.instcombine.xor-self") {
    return "xor";
  }
  if (Marker == "probe.dce.dead-instruction") {
    return "dead-instruction";
  }
  if (Marker == "probe.globalopt.dead-initializer") {
    return "global-initializer";
  }
  return "";
}

llvm::json::Array guardJsonForCondition(const Expr *Condition,
                                        const SourceManager &SM,
                                        const LangOptions &LangOpts);

llvm::json::Array globalInitializerRequiredSafetyFacts() {
  return llvm::json::Array{"initializer-dead", "local-linkage", "no-uses"};
}

std::string globalInitializerSafetyFactForCall(const CallExpr *Call) {
  const std::string Name = calleeName(Call);
  if (Name == "isGlobalInitializerDead") {
    return "initializer-dead";
  }
  if (Name == "hasLocalLinkage") {
    return "local-linkage";
  }
  if (Name == "use_empty") {
    return "no-uses";
  }
  return "";
}

void collectGlobalInitializerSafetyFacts(const Stmt *Node,
                                         const SourceManager &SM,
                                         const LangOptions &LangOpts,
                                         llvm::json::Array &Facts,
                                         std::set<std::string> &Observed) {
  if (!Node) {
    return;
  }
  if (const auto *Call = dyn_cast<CallExpr>(Node)) {
    const std::string Fact = globalInitializerSafetyFactForCall(Call);
    if (!Fact.empty() && Observed.count(Fact) == 0) {
      Observed.insert(Fact);
      llvm::json::Object Entry{
          {"fact", Fact},
          {"status", "observed"},
          {"predicate_family", calleeName(Call)},
          {"source", sourceText(SM, LangOpts, Call->getSourceRange())},
          {"source_range", sourceRangeJson(SM, Call->getBeginLoc(), Call->getEndLoc())},
      };
      if (const auto *MemberCall = dyn_cast<CXXMemberCallExpr>(Call)) {
        Entry["subject"] = exprText(MemberCall->getImplicitObjectArgument(), SM,
                                    LangOpts);
      } else if (Call->getNumArgs() >= 1) {
        Entry["subject"] = exprText(Call->getArg(0), SM, LangOpts);
      }
      Facts.push_back(std::move(Entry));
    }
  }
  for (const Stmt *Child : Node->children()) {
    collectGlobalInitializerSafetyFacts(Child, SM, LangOpts, Facts, Observed);
  }
}

llvm::json::Array safetyArrayFromSet(const std::set<std::string> &Facts) {
  llvm::json::Array Values;
  for (const std::string &Fact : Facts) {
    Values.push_back(Fact);
  }
  return Values;
}

std::set<std::string> globalInitializerMissingSafetySet(
    const std::set<std::string> &Observed) {
  std::set<std::string> Missing;
  for (llvm::StringRef Fact :
       {"initializer-dead", "local-linkage", "no-uses"}) {
    if (Observed.count(Fact.str()) == 0) {
      Missing.insert(Fact.str());
    }
  }
  return Missing;
}

llvm::json::Array globalInitializerSafetyProvenance(
    const Expr *Condition, const SourceManager &SM, const LangOptions &LangOpts,
    std::set<std::string> &Observed) {
  llvm::json::Array Facts;
  collectGlobalInitializerSafetyFacts(Condition, SM, LangOpts, Facts, Observed);
  for (llvm::StringRef Fact :
       {"initializer-dead", "local-linkage", "no-uses"}) {
    if (Observed.count(Fact.str()) == 0) {
      Facts.push_back(llvm::json::Object{
          {"fact", Fact.str()},
          {"status", "missing"},
          {"predicate_family", ""},
          {"source", ""},
          {"source_range", llvm::json::Object{}},
      });
    }
  }
  return Facts;
}

llvm::json::Array globalInitializerGuardsFromSafetyProvenance(
    const llvm::json::Array &SafetyProvenance) {
  llvm::json::Array Guards;
  for (const llvm::json::Value &Value : SafetyProvenance) {
    const auto *Record = Value.getAsObject();
    const std::optional<llvm::StringRef> Status =
        Record ? Record->getString("status") : std::nullopt;
    if (!Record || !Status || *Status != "observed") {
      continue;
    }
    Guards.push_back(llvm::json::Object{
        {"kind", "dead-global-initializer"},
        {"role", "semantic"},
        {"source", stringField(*Record, "source")},
    });
  }
  return Guards;
}

llvm::json::Array bindingArrayFromSourceIntent(const llvm::json::Object *SourceIntent) {
  llvm::json::Array Bindings;
  if (!SourceIntent) {
    return Bindings;
  }
  const auto *Before = SourceIntent->getObject("before");
  if (!Before) {
    return Bindings;
  }
  const auto *Operands = Before->getArray("operands");
  std::set<std::string> Seen;
  if (Operands) {
    for (const llvm::json::Value &Operand : *Operands) {
      if (auto SymbolText = Operand.getAsString()) {
        if (!SymbolText->empty() && Seen.count(SymbolText->str()) == 0 &&
            !SymbolText->str().empty() &&
            !std::all_of(SymbolText->begin(), SymbolText->end(), ::isdigit)) {
          Seen.insert(SymbolText->str());
          Bindings.push_back(llvm::json::Object{
              {"source_symbol", SymbolText->str()},
              {"role", "operand"},
          });
        }
        continue;
      }
      const auto *Object = Operand.getAsObject();
      if (!Object) {
        continue;
      }
      auto Symbol = Object->getString("symbol");
      if (!Symbol || Symbol->empty() || Seen.count(Symbol->str()) != 0) {
        continue;
      }
      Seen.insert(Symbol->str());
      Bindings.push_back(llvm::json::Object{
          {"source_symbol", Symbol->str()},
          {"role", "operand"},
      });
    }
  }
  const auto *After = SourceIntent->getObject("after");
  if (After) {
    const llvm::json::Value *Result = After->get("result");
    const auto *ResultObject = Result ? Result->getAsObject() : nullptr;
    llvm::StringRef Symbol;
    if (ResultObject) {
      if (auto MaybeSymbol = ResultObject->getString("symbol")) {
        Symbol = *MaybeSymbol;
      }
    }
    if (!Symbol.empty() && Seen.count(Symbol.str()) == 0) {
      Bindings.push_back(llvm::json::Object{
          {"source_symbol", Symbol.str()},
          {"role", "result"},
      });
    }
  }
  if (const auto *Parameters = Before->getObject("parameters")) {
    for (const auto &Item : *Parameters) {
      Bindings.push_back(llvm::json::Object{
          {"source_symbol", Item.first.str()},
          {"role", "parameter"},
          {"value", cloneJson(Item.second)},
      });
    }
  }
  return Bindings;
}

llvm::json::Object sourceIntentGraphFromAst(
    const Finding &Finding, const Expr *Condition, const Stmt *RewriteStmt,
    const SourceManager &SM, const LangOptions &LangOpts,
    const std::optional<llvm::json::Object> &SourceIntent) {
  llvm::json::Array PredicateNodes;
  PredicateNodes.push_back(llvm::json::Object{
      {"kind", "condition"},
      {"marker", Finding.Marker},
      {"source", Finding.PredicateSource},
      {"line", static_cast<int64_t>(Finding.Line)},
      {"column", static_cast<int64_t>(Finding.Column)},
  });
  collectCallNodes(Condition, SM, LangOpts, PredicateNodes);

  llvm::json::Array RewriteNodes;
  llvm::json::Array Reasons;
  if (RewriteStmt) {
    llvm::json::Object Rewrite{
        {"kind", "rewrite"},
        {"source", Finding.RewriteSource},
        {"line", static_cast<int64_t>(Finding.RewriteLine)},
    };
    if (const CallExpr *Replace = firstCallWithAnyName(
            RewriteStmt, {"replaceInstUsesWith", "ReplaceInstWithValue"})) {
      Rewrite["action"] = "replace-result";
      Rewrite["callee"] = calleeName(Replace);
      if (Replace->getNumArgs() >= 1) {
        Rewrite["replacement"] =
            exprText(Replace->getArg(Replace->getNumArgs() - 1), SM, LangOpts);
      }
      llvm::json::Array LocalDefinitions;
      collectLocalValueDefinitions(RewriteStmt, SM, LangOpts, LocalDefinitions);
      if (!LocalDefinitions.empty()) {
        Rewrite["local_definitions"] = std::move(LocalDefinitions);
      }
    } else if (hasMemberCallNamed(RewriteStmt, "eraseFromParent")) {
      Rewrite["action"] =
          Finding.Marker == "probe.cleanup.unused-alloca"
              ? "remove-unused-alloca"
              : "erase-instruction";
    } else if (const CXXMemberCallExpr *SetInitializer =
                   firstMemberCallNamed(RewriteStmt, "setInitializer")) {
      Rewrite["action"] = "remove-global-initializer-if-dead-v1";
      Rewrite["callee"] = "setInitializer";
      Rewrite["subject"] = exprText(SetInitializer->getImplicitObjectArgument(),
                                    SM, LangOpts);
      if (SetInitializer->getNumArgs() >= 1) {
        const Expr *Replacement = SetInitializer->getArg(0);
        const std::string ReplacementExpr = exprText(Replacement, SM, LangOpts);
        Rewrite["replacement_expr"] = ReplacementExpr;
        Rewrite["value_type_expr"] =
            globalInitializerValueTypeExpr(Replacement, SM, LangOpts);
        if (isDefaultNullGlobalInitializerReplacement(ReplacementExpr)) {
          Rewrite["replacement_kind"] = "default-null-initializer";
        } else {
          Rewrite["replacement_kind"] = "unknown";
          Reasons.push_back("unsupported-global-initializer-replacement");
        }
      } else {
        Rewrite["replacement_kind"] = "unknown";
        Reasons.push_back("missing-global-initializer-replacement");
      }
    } else {
      const auto *RewriteIntent = SourceIntent ? SourceIntent->getObject("rewrite") : nullptr;
      if (RewriteIntent && RewriteIntent->getString("action")) {
        Rewrite["action"] = RewriteIntent->getString("action")->str();
        if (auto Api = RewriteIntent->getString("api")) {
          Rewrite["api"] = Api->str();
        }
      } else {
        Rewrite["action"] = "unknown";
        Reasons.push_back("unsupported-rewrite");
      }
    }
    RewriteNodes.push_back(std::move(Rewrite));
  } else {
    Reasons.push_back("missing-rewrite");
  }
  if (!SourceIntent) {
    Reasons.push_back("missing-source-intent");
  }

  llvm::json::Object Graph{
      {"model", "source-intent-graph-v1"},
      {"status", Reasons.empty() ? "complete" : "incomplete"},
      {"predicate_nodes", std::move(PredicateNodes)},
      {"rewrite_nodes", std::move(RewriteNodes)},
      {"bindings", bindingArrayFromSourceIntent(SourceIntent ? &*SourceIntent : nullptr)},
      {"guards", guardJsonForCondition(Condition, SM, LangOpts)},
  };
  if (Finding.Marker == "probe.globalopt.dead-initializer") {
    Graph["global_symbol"] = "GV";
    Graph["observability_model"] = "local-unobservable-initializer-v1";
    if (const CXXMemberCallExpr *SetInitializer =
            firstMemberCallNamed(RewriteStmt, "setInitializer")) {
      Graph["rewrite_callee"] = "setInitializer";
      Graph["subject"] = exprText(SetInitializer->getImplicitObjectArgument(),
                                  SM, LangOpts);
      if (SetInitializer->getNumArgs() >= 1) {
        const Expr *Replacement = SetInitializer->getArg(0);
        const std::string ReplacementExpr = exprText(Replacement, SM, LangOpts);
        Graph["replacement_expr"] = ReplacementExpr;
        Graph["value_type_expr"] =
            globalInitializerValueTypeExpr(Replacement, SM, LangOpts);
      }
    }
    std::set<std::string> Observed;
    llvm::json::Array SafetyProvenance =
        globalInitializerSafetyProvenance(Condition, SM, LangOpts, Observed);
    llvm::json::Array GlobalGuards =
        globalInitializerGuardsFromSafetyProvenance(SafetyProvenance);
    const std::set<std::string> Missing =
        globalInitializerMissingSafetySet(Observed);
    Graph["required_safety_facts"] = globalInitializerRequiredSafetyFacts();
    Graph["observed_safety_facts"] = safetyArrayFromSet(Observed);
    Graph["missing_safety_facts"] = safetyArrayFromSet(Missing);
    Graph["guards"] = std::move(GlobalGuards);
    Graph["safety_provenance"] = std::move(SafetyProvenance);
    Graph["safety_provenance_status"] =
        Missing.empty() ? "complete" : "incomplete";
    Graph["safety_status"] = Missing.empty() ? "complete" : "incomplete";
  }
  if (!Reasons.empty()) {
    Graph["unsupported_reasons"] = std::move(Reasons);
  }
  return Graph;
}

bool isVectorMarker(llvm::StringRef Marker) {
  return Marker.starts_with("probe.vector.");
}

std::optional<llvm::json::Object>
vectorIntentPartsForMarker(llvm::StringRef Marker) {
#define EMIT(MK, SHAPE, OP, ID, RW, RES)                                        \
  if (Marker == (MK)) {                                                         \
    return llvm::json::Object{{"shape", SHAPE},    {"operation", OP},           \
                             {"identity", ID},     {"rewrite", RW},             \
                             {"result", RES}};                                  \
  }
  // Marker -> source-intent parts is generated from constraints/semantic_facts.json
  // by tools/cv-generate-vector-intent-header.py (GeneratedVectorIntentParts.h),
  // so the registry is the single source of truth -- no hardcoded suffix ladder.
  CV_FOR_EACH_VECTOR_INTENT_MARKER(EMIT)
#undef EMIT
  return std::nullopt;
}

llvm::json::Object vectorParametersFromConstraints(const llvm::json::Value &Constraints) {
  llvm::json::Object Parameters;
  const auto *Object = Constraints.getAsObject();
  if (!Object) {
    return Parameters;
  }
  for (llvm::StringRef Key :
       {"vector.shuffle.mask", "vector.shuffle.splat_lane",
        "vector.extract_insert.lane", "vector.insert_extract.lane",
        "vector.reduction.lane"}) {
    if (const llvm::json::Value *Value = Object->get(Key)) {
      Parameters[Key.str()] = cloneJson(*Value);
    }
  }
  return Parameters;
}

std::optional<llvm::json::Object>
vectorSourceIntentFromAst(const Finding &Finding, const Expr *Condition,
                          const SourceManager &SM,
                          const LangOptions &LangOpts) {
  if (!isVectorMarker(Finding.Marker)) {
    return std::nullopt;
  }
  std::optional<llvm::json::Object> Parts =
      vectorIntentPartsForMarker(Finding.Marker);
  if (!Parts) {
    return std::nullopt;
  }
  const std::string Shape = std::string(*Parts->getString("shape"));
  const std::string Operation = std::string(*Parts->getString("operation"));
  const std::string Identity = std::string(*Parts->getString("identity"));
  const std::string Rewrite = std::string(*Parts->getString("rewrite"));
  const std::string Result = std::string(*Parts->getString("result"));
  llvm::json::Object Before{
      {"shape", Shape},
      {"operation", Operation},
      {"identity", Identity},
  };
  llvm::json::Object Parameters = vectorParametersFromConstraints(Finding.Constraints);
  if (!Parameters.empty()) {
    Before["parameters"] = std::move(Parameters);
  }
  return llvm::json::Object{
      {"model", "source-intent-v1"},
      {"subject", "vector"},
      {"before", std::move(Before)},
      {"after",
       llvm::json::Object{{"rewrite", Rewrite},
                          {"result", Result}}},
      {"rewrite",
       llvm::json::Object{{"api", "source-vector-helper"},
                          {"action", Rewrite}}},
      {"guards", guardJsonForCondition(Condition, SM, LangOpts)},
  };
}

llvm::json::Array guardJsonForCondition(const Expr *Condition,
                                        const SourceManager &SM,
                                        const LangOptions &LangOpts) {
  if (!Condition) {
    return {};
  }
  Condition = Condition->IgnoreParenImpCasts();
  if (const auto *Binary = dyn_cast<BinaryOperator>(Condition)) {
    if (Binary->getOpcode() == BO_LAnd) {
      llvm::json::Array Guards = guardJsonForCondition(Binary->getLHS(), SM, LangOpts);
      llvm::json::Array RHS = guardJsonForCondition(Binary->getRHS(), SM, LangOpts);
      for (llvm::json::Value &Guard : RHS) {
        Guards.push_back(std::move(Guard));
      }
      return Guards;
    }
  }
  if (const auto *Call = dyn_cast<CallExpr>(Condition)) {
    const std::string Name = calleeName(Call);
    const std::string Source = exprText(Condition, SM, LangOpts);
    if (Name == "isGuaranteedNotToBePoison" && Call->getNumArgs() >= 1) {
      llvm::json::Object Guard =
          guardObject("guaranteed-not-poison", "modeled-side-condition", Source,
                      true);
      Guard["subject"] = symbolForExpr(Call->getArg(0), SM, LangOpts);
      return llvm::json::Array{std::move(Guard)};
    }
    if (Name == "isKnownNonZero" && Call->getNumArgs() >= 1) {
      llvm::json::Object Guard =
          guardObject("known-nonzero", "modeled-side-condition", Source, true);
      Guard["subject"] = symbolForExpr(Call->getArg(0), SM, LangOpts);
      return llvm::json::Array{std::move(Guard)};
    }
    if (Name == "isKnownPositive" && Call->getNumArgs() >= 1) {
      llvm::json::Object Guard =
          guardObject("known-positive", "modeled-side-condition", Source, true);
      Guard["subject"] = symbolForExpr(Call->getArg(0), SM, LangOpts);
      return llvm::json::Array{std::move(Guard)};
    }
    if (Name == "isKnownNonNegative" && Call->getNumArgs() >= 1) {
      llvm::json::Object Guard =
          guardObject("known-nonnegative", "modeled-side-condition", Source,
                      true);
      Guard["subject"] = symbolForExpr(Call->getArg(0), SM, LangOpts);
      return llvm::json::Array{std::move(Guard)};
    }
    if (Name == "MaskedValueIsZero" && Call->getNumArgs() >= 2) {
      llvm::json::Object Guard =
          guardObject("masked-value-is-zero", "modeled-side-condition", Source,
                      true);
      Guard["subject"] = symbolForExpr(Call->getArg(0), SM, LangOpts);
      if (std::optional<uint64_t> Mask =
              unsignedIntegerForExpr(Call->getArg(1))) {
        Guard["zero_mask"] = static_cast<int64_t>(*Mask);
      }
      return llvm::json::Array{std::move(Guard)};
    }
    if ((Name == "isKnownPowerOf2" || Name == "isKnownToBeAPowerOfTwo") &&
        Call->getNumArgs() >= 1) {
      llvm::json::Object Guard =
          guardObject("known-power-of-two", "modeled-side-condition", Source,
                      true);
      Guard["subject"] = symbolForExpr(Call->getArg(0), SM, LangOpts);
      return llvm::json::Array{std::move(Guard)};
    }
  }
  if (const auto *Call = dyn_cast<CXXMemberCallExpr>(Condition)) {
    const std::string Name = calleeName(Call);
    if (Name == "hasOneUse") {
      const std::string Source = exprText(Condition, SM, LangOpts);
      llvm::json::Object Guard =
          guardObject("one-use", "modeled-side-condition", Source, true);
      Guard["subject"] = symbolForExpr(Call->getImplicitObjectArgument(), SM, LangOpts);
      return llvm::json::Array{std::move(Guard)};
    }
  }
  return guardJson(exprText(Condition, SM, LangOpts));
}

std::optional<llvm::json::Object>
sourceIntentFromAst(const Finding &Finding, const Expr *Condition,
                    const Stmt *RewriteStmt, const SourceManager &SM,
                    const LangOptions &LangOpts,
                    const std::map<std::string, llvm::json::Value>
                        &SemanticRegistry) {
  if (!Condition || !RewriteStmt) {
    return std::nullopt;
  }
  if (std::optional<llvm::json::Object> VectorIntent =
          vectorSourceIntentFromAst(Finding, Condition, SM, LangOpts)) {
    return VectorIntent;
  }
  if (Finding.Marker == "probe.globalopt.dead-initializer") {
    const CXXMemberCallExpr *SetInitializer =
        firstMemberCallNamed(RewriteStmt, "setInitializer");
    if (!SetInitializer) {
      return std::nullopt;
    }
    std::string Subject = exprText(SetInitializer->getImplicitObjectArgument(),
                                   SM, LangOpts);
    std::string ReplacementExpr;
    std::string ReplacementKind = "unknown";
    std::string ValueTypeExpr;
    if (SetInitializer->getNumArgs() >= 1) {
      const Expr *Replacement = SetInitializer->getArg(0);
      ReplacementExpr = exprText(Replacement, SM, LangOpts);
      ValueTypeExpr = globalInitializerValueTypeExpr(Replacement, SM, LangOpts);
      if (isDefaultNullGlobalInitializerReplacement(ReplacementExpr)) {
        ReplacementKind = "default-null-initializer";
      }
    }
    std::set<std::string> Observed;
    llvm::json::Array SafetyProvenance =
        globalInitializerSafetyProvenance(Condition, SM, LangOpts, Observed);
    llvm::json::Array GlobalGuards =
        globalInitializerGuardsFromSafetyProvenance(SafetyProvenance);
    const std::set<std::string> Missing =
        globalInitializerMissingSafetySet(Observed);
    llvm::json::Object Before{
        {"shape", "global"},
        {"operation", "erase"},
        {"identity", "dead"},
        {"target", "initializer"},
        {"safety_facts", safetyArrayFromSet(Observed)}};
    llvm::json::Object After{
        {"effect", "initializer-defaulted"}};
    return llvm::json::Object{
        {"model", "source-intent-v1"},
        {"subject", "GV"},
        {"global_symbol", "GV"},
        {"observability_model", "local-unobservable-initializer-v1"},
        {"required_safety_facts", globalInitializerRequiredSafetyFacts()},
        {"observed_safety_facts", safetyArrayFromSet(Observed)},
        {"missing_safety_facts", safetyArrayFromSet(Missing)},
        {"safety_provenance", std::move(SafetyProvenance)},
        {"safety_provenance_status", Missing.empty() ? "complete" : "incomplete"},
        {"safety_status", Missing.empty() ? "complete" : "incomplete"},
        {"before", std::move(Before)},
        {"after", std::move(After)},
        {"rewrite",
         llvm::json::Object{{"api", "setInitializer"},
                            {"action", "remove-global-initializer-if-dead-v1"},
                            {"subject", Subject.empty() ? "GV" : Subject},
                            {"replacement", ReplacementKind},
                            {"replacement_kind", ReplacementKind},
                            {"replacement_expr", ReplacementExpr},
                            {"value_type_expr", ValueTypeExpr}}},
        {"guards", std::move(GlobalGuards)},
    };
  }
  if (Finding.Marker == "probe.cleanup.unused-alloca") {
    if (!hasMemberCallNamed(RewriteStmt, "eraseFromParent")) {
      return std::nullopt;
    }
    std::string Subject = "AI";
    if (const CXXMemberCallExpr *Erase =
            firstMemberCallNamed(RewriteStmt, "eraseFromParent")) {
      Subject = exprText(Erase->getImplicitObjectArgument(), SM, LangOpts);
    }
    llvm::json::Object Before{
        {"shape", "memory"},
        {"operation", "remove-alloca"},
        {"identity", "unused-alloca"},
        {"target", "alloca"},
        {"observable", false}};
    llvm::json::Object After{
        {"effect", "alloca-removed"},
        {"result", "observable-result-preserved"}};
    return llvm::json::Object{
        {"model", "source-intent-v1"},
        {"subject", Subject.empty() ? "AI" : Subject},
        {"before", std::move(Before)},
        {"after", std::move(After)},
        {"rewrite",
         llvm::json::Object{{"api", "eraseFromParent"},
                            {"action", "remove-unused-alloca"},
                            {"subject", Subject.empty() ? "AI" : Subject},
                            {"replacement", "erased"}}},
        {"guards", guardJsonForCondition(Condition, SM, LangOpts)},
    };
  }
  const CallExpr *MatchCall = firstCallNamed(Condition, "match");
  PatternTerm BeforeTerm;
  if (MatchCall && MatchCall->getNumArgs() >= 2) {
    BeforeTerm = interpretPattern(MatchCall->getArg(1), SM, LangOpts);
  }
  if (BeforeTerm.TermKind != PatternTerm::Kind::Operation) {
    const std::string Operation =
        sourceIntentOperationForMarker(Finding.Marker, SemanticRegistry);
    if (Operation.empty()) {
      return std::nullopt;
    }
    if (Operation == "dead-instruction") {
      BeforeTerm = operationTerm(Operation, symbolTerm("I"), {}, false);
      BeforeTerm.Operands.pop_back();
    } else {
      return std::nullopt;
    }
  }

  std::string Api;
  std::string Action;
  llvm::json::Value Replacement = llvm::json::Object{{"unknown", true}};
  if (const CallExpr *Replace = firstCallWithAnyName(
          RewriteStmt, {"replaceInstUsesWith", "ReplaceInstWithValue"})) {
    Api = calleeName(Replace);
    Action = "replace-result";
    if (Replace->getNumArgs() >= 1) {
      Replacement = exprValueJson(Replace->getArg(Replace->getNumArgs() - 1), SM, LangOpts);
      const auto *ReplacementObject = Replacement.getAsObject();
      if (ReplacementObject) {
        if (auto Symbol = ReplacementObject->getString("symbol")) {
          if (auto DefinedValue =
                  localValueDefinitionForName(RewriteStmt, *Symbol, SM, LangOpts)) {
            Replacement = std::move(*DefinedValue);
          }
        }
      }
    }
  } else if (hasMemberCallNamed(RewriteStmt, "eraseFromParent")) {
    Api = "eraseFromParent";
    Action = "erase-instruction";
    Replacement = "";
  } else if (hasMemberCallNamed(RewriteStmt, "setInitializer")) {
    Api = "setInitializer";
    Action = "remove-global-initializer-if-dead-v1";
    Replacement = "default-null-initializer";
  } else {
    return std::nullopt;
  }

  llvm::json::Object After{{"result", cloneJson(Replacement)}};
  if (Action == "erase-instruction") {
    After = llvm::json::Object{{"effect", "erased"}};
  }

  llvm::json::Object Before{{"shape", "scalar"},
                            {"operation", BeforeTerm.Operation},
                            {"operands", termOperandsJson(BeforeTerm)}};
  if (BeforeTerm.Commutative) {
    Before["commutative"] = true;
  }

  return llvm::json::Object{
      {"model", "source-intent-v1"},
      {"subject", "I"},
      {"before", std::move(Before)},
      {"after", std::move(After)},
      {"rewrite",
       llvm::json::Object{{"api", Api},
                          {"action", Action},
                          {"replacement", std::move(Replacement)}}},
      {"guards", guardJsonForCondition(Condition, SM, LangOpts)},
  };
}

// identity -> {value, matcher} is generated from constraints/llvm_idioms.json
// (GeneratedLlvmIdioms.h), so these scalar source-intent helpers stay in sync
// with the registry the lifter trusts.
std::string identityValueForSourceIntent(llvm::StringRef Identity) {
#define EMIT(ID, VAL, MATCHER) \
  if (Identity == (ID))        \
    return VAL;
  CV_FOR_EACH_GENERATED_IDENTITY_CONSTANT(EMIT)
#undef EMIT
  return "";
}

std::string identityMatcherForSourceIntent(llvm::StringRef Identity) {
#define EMIT(ID, VAL, MATCHER) \
  if (Identity == (ID))        \
    return MATCHER;
  CV_FOR_EACH_GENERATED_IDENTITY_CONSTANT(EMIT)
#undef EMIT
  return "";
}

std::optional<llvm::json::Object> sourceIntentFor(
    const Finding &Finding,
    const std::map<std::string, llvm::json::Value> &SemanticRegistry) {
  llvm::StringRef Marker(Finding.Marker);
  llvm::StringRef Predicate(Finding.PredicateSource);
  llvm::StringRef Rewrite(Finding.RewriteSource);
  if (Rewrite.empty()) {
    return std::nullopt;
  }

  std::string Operation;
  llvm::json::Array Operands;
  if (const llvm::json::Object *Facts =
          semanticFactsForMarker(Marker, SemanticRegistry)) {
    const std::optional<llvm::StringRef> Shape = Facts->getString("shape");
    const std::optional<llvm::StringRef> FactOperation =
        Facts->getString("operation");
    const std::optional<llvm::StringRef> Identity =
        Facts->getString("identity");
    const std::optional<llvm::StringRef> FactRewrite =
        Facts->getString("rewrite");
    if (Shape && FactOperation && FactRewrite && *Shape == "scalar" &&
        *FactOperation != "erase") {
      Operation = FactOperation->str();
      if (*FactRewrite == "replace-with-lhs" && Identity) {
        if (*Identity == "same-value") {
          Operands.push_back(operandJson("Op0"));
          Operands.push_back(operandJson("Op0"));
        } else {
          const std::string IdentityValue =
              identityValueForSourceIntent(*Identity);
          const std::string Matcher = identityMatcherForSourceIntent(*Identity);
          if (IdentityValue.empty() || Matcher.empty()) {
            return std::nullopt;
          }
          if (Predicate.contains("match(Op0") && Predicate.contains(Matcher)) {
            Operands.push_back(operandJson(IdentityValue));
            Operands.push_back(operandJson("Op1"));
          } else {
            Operands.push_back(operandJson("Op0"));
            Operands.push_back(operandJson(IdentityValue));
          }
        }
      } else if (*FactRewrite == "replace-with-zero") {
        Operands.push_back(operandJson("Op0"));
        Operands.push_back(operandJson("Op0"));
      } else {
        return std::nullopt;
      }
    } else if (Shape && FactOperation && *Shape == "scalar" &&
               *FactOperation == "erase") {
      Operation = "dead-instruction";
      Operands.push_back(operandJson("I"));
    } else if (Shape && FactOperation && *Shape == "global" &&
               *FactOperation == "erase") {
      Operation = "global-initializer";
      Operands.push_back(operandJson("GV"));
    }
  }
  if (Operation.empty()) {
    if (Marker == "probe.dce.dead-instruction") {
      Operation = "dead-instruction";
      Operands.push_back(operandJson("I"));
    } else if (Marker == "probe.globalopt.dead-initializer") {
      Operation = "global-initializer";
      Operands.push_back(operandJson("GV"));
    } else {
      return std::nullopt;
    }
  }

  std::string Api;
  std::string Action;
  std::string Replacement = replacementValue(Rewrite);
  if (Rewrite.contains("replaceInstUsesWith") ||
      Rewrite.contains("ReplaceInstWithValue")) {
    Api = "replaceInstUsesWith";
    Action = "replace-result";
  } else if (Rewrite.contains("eraseFromParent")) {
    Api = "eraseFromParent";
    Action = "erase-instruction";
  } else if (Rewrite.contains("setInitializer")) {
    Api = "setInitializer";
    Action = "remove-global-initializer-if-dead-v1";
  } else {
    return std::nullopt;
  }

  llvm::json::Object After{{"result", operandJson(Replacement)}};
  if (Action == "erase-instruction") {
    After = llvm::json::Object{{"effect", "erased"}};
  } else if (Action == "remove-global-initializer-if-dead-v1") {
    After = llvm::json::Object{{"effect", "initializer-defaulted"}};
  }

  return llvm::json::Object{
      {"model", "source-intent-v1"},
      {"subject", Marker == "probe.globalopt.dead-initializer" ? "GV" : "I"},
      {"before",
       llvm::json::Object{{"shape", "scalar"},
                          {"operation", Operation},
                          {"operands", std::move(Operands)}}},
      {"after", std::move(After)},
      {"rewrite",
       llvm::json::Object{{"api", Api},
                          {"action", Action},
                          {"replacement", operandJson(Replacement)}}},
      {"guards", guardJson(Predicate)},
  };
}

std::vector<std::string> contextLines(const SourceManager &SM, FileID File,
                                      unsigned Line, unsigned Radius) {
  std::vector<std::string> Result;
  bool Invalid = false;
  llvm::StringRef Buffer = SM.getBufferData(File, &Invalid);
  if (Invalid) {
    return Result;
  }
  llvm::SmallVector<llvm::StringRef, 128> Lines;
  Buffer.split(Lines, '\n');
  const unsigned Start = Line > Radius ? Line - Radius : 1;
  const unsigned End = std::min<unsigned>(Lines.size(), Line + Radius);
  for (unsigned Index = Start; Index <= End; ++Index) {
    Result.push_back(Lines[Index - 1].str());
  }
  return Result;
}

bool lineHasAny(llvm::StringRef Line,
                std::initializer_list<llvm::StringRef> Tokens) {
  for (llvm::StringRef Token : Tokens) {
    if (Line.contains(Token)) {
      return true;
    }
  }
  return false;
}

llvm::json::Object analysisFact(llvm::StringRef Kind, llvm::StringRef Role,
                                llvm::StringRef Status,
                                std::initializer_list<llvm::StringRef> Subjects,
                                unsigned Line, llvm::StringRef Source,
                                llvm::StringRef Provenance,
                                llvm::StringRef ByteMask = "",
                                unsigned ByteBound = 0) {
  llvm::json::Array SubjectValues;
  for (llvm::StringRef Subject : Subjects) {
    if (!Subject.empty()) {
      SubjectValues.push_back(Subject.str());
    }
  }
  llvm::json::Object Fact{{"kind", Kind.str()},
                          {"role", Role.str()},
                          {"status", Status.str()},
                          {"subjects", std::move(SubjectValues)},
                          {"line", static_cast<int64_t>(Line)},
                          {"source", trim(Source.str())}};
  if (!Provenance.empty()) {
    Fact["provenance"] = Provenance.str();
  }
  if (!ByteMask.empty()) {
    Fact["byte_mask"] = ByteMask.str();
  }
  if (ByteBound) {
    Fact["byte_bound"] = static_cast<int64_t>(ByteBound);
  }
  return Fact;
}

unsigned dseSymbolicSizeByteBound(llvm::StringRef Line) {
  if (Line.contains("knownSizeWithinFourBytes")) {
    return 4;
  }
  if (Line.contains("knownSizeWithinEightBytes")) {
    return 8;
  }
  const std::string Text = Line.str();
  std::smatch Match;
  const std::regex LeBound(R"((?:getValue\s*\(\)|Size|size)[^<>=!]*<=\s*([0-9]+))");
  if (std::regex_search(Text, Match, LeBound) && Match.size() == 2) {
    return static_cast<unsigned>(std::stoul(Match[1].str()));
  }
  const std::regex LtBound(R"((?:getValue\s*\(\)|Size|size)[^<>=!]*<\s*([0-9]+))");
  if (std::regex_search(Text, Match, LtBound) && Match.size() == 2) {
    const unsigned Limit = static_cast<unsigned>(std::stoul(Match[1].str()));
    return Limit > 0 ? Limit - 1 : 0;
  }
  return 0;
}

std::string dsePartialOverwriteByteMask(llvm::StringRef Line) {
  const std::string Text = Line.str();
  auto MaskName = [](unsigned Bits, unsigned Width) -> std::string {
    if (Width < 1 || Width > 8) {
      return "";
    }
    Bits &= (1u << Width) - 1u;
    if (Bits == 0) {
      return "";
    }
    std::string Result = "lanes";
    for (unsigned Lane = 0; Lane < Width; ++Lane) {
      if ((Bits & (1u << Lane)) == 0) {
        continue;
      }
      Result += "-";
      Result += std::to_string(Lane);
    }
    Result += "-of-";
    Result += std::to_string(Width);
    return Result;
  };

  std::smatch Match;
  const std::regex ExplicitMask(R"(lanes-([0-7](?:-[0-7])*)-of-([1-8]))");
  if (std::regex_search(Text, Match, ExplicitMask) && Match.size() == 3) {
    const unsigned Width = static_cast<unsigned>(std::stoi(Match[2].str()));
    unsigned Bits = 0;
    std::set<unsigned> Seen;
    std::string Body = Match[1].str();
    std::regex LaneRegex(R"([0-7])");
    for (auto It = std::sregex_iterator(Body.begin(), Body.end(), LaneRegex);
         It != std::sregex_iterator(); ++It) {
      const unsigned Lane = static_cast<unsigned>(std::stoi((*It)[0].str()));
      if (Lane >= Width) {
        return "";
      }
      if (!Seen.insert(Lane).second) {
        return "";
      }
      Bits |= 1u << Lane;
    }
    return MaskName(Bits, Width);
  }

  const std::regex RangeCall(
      R"((?:knownPartialOverwriteByteMask|partialOverwriteByteMask)\s*\([^,]*,\s*[^,]*,\s*([0-7])\s*,\s*([1-8])(?:\s*,\s*([1-8]))?\s*\))");
  if (std::regex_search(Text, Match, RangeCall) && Match.size() >= 3) {
    const int Start = std::stoi(Match[1].str());
    const int Length = std::stoi(Match[2].str());
    const int Width = Match.size() >= 4 && Match[3].matched ? std::stoi(Match[3].str()) : 4;
    if (Start + Length <= Width) {
      unsigned Bits = 0;
      for (int Lane = Start; Lane < Start + Length; ++Lane) {
        Bits |= 1u << static_cast<unsigned>(Lane);
      }
      return MaskName(Bits, static_cast<unsigned>(Width));
    }
  }

  const std::regex SparseCall(
      R"((?:knownPartialOverwriteByteMask|partialOverwriteByteMask)\s*\([^,]*,\s*[^,]*,\s*0x([0-9a-f]+)(?:\s*,\s*([1-8]))?\s*\))",
      std::regex::icase);
  if (std::regex_search(Text, Match, SparseCall) && Match.size() >= 2) {
    const unsigned Width =
        Match.size() >= 3 && Match[2].matched
            ? static_cast<unsigned>(std::stoi(Match[2].str()))
            : 4u;
    const unsigned Bits =
        static_cast<unsigned>(std::stoul(Match[1].str(), nullptr, 16));
    return MaskName(Bits, Width);
  }

  if (Line.contains("fixedPartialOverwrite")) {
    return "lanes-0-1-of-4";
  }
  return "";
}

llvm::json::Array analysisFactsForFinding(const Finding &Finding,
                                          const SourceManager &SM,
                                          FileID File) {
  llvm::json::Array Facts;
  if (!llvm::StringRef(Finding.Marker).starts_with("probe.dse.")) {
    return Facts;
  }
  bool Invalid = false;
  llvm::StringRef Buffer = SM.getBufferData(File, &Invalid);
  if (Invalid) {
    return Facts;
  }
  llvm::SmallVector<llvm::StringRef, 256> Lines;
  Buffer.split(Lines, '\n');
  const size_t BeginLine = std::max<size_t>(1, static_cast<size_t>(Finding.Line));
  const size_t EndLine = std::min<size_t>(
      Lines.size(), static_cast<size_t>(std::max(Finding.EndLine, Finding.Line) + 3));
  auto AddFirstLine = [&](llvm::StringRef Kind, llvm::StringRef Role,
                          llvm::StringRef Status,
                          std::initializer_list<llvm::StringRef> Subjects,
                          std::initializer_list<llvm::StringRef> Tokens,
                          llvm::StringRef Provenance,
                          std::function<bool(llvm::StringRef)> ExtraCheck =
                              [](llvm::StringRef) { return true; }) {
    for (size_t LineNumber = BeginLine; LineNumber <= EndLine; ++LineNumber) {
      const size_t Index = LineNumber - 1;
      llvm::StringRef Line = Lines[Index];
      if (!lineHasAny(Line, Tokens) || !ExtraCheck(Line)) {
        continue;
      }
      Facts.push_back(analysisFact(Kind, Role, Status, Subjects,
                                   static_cast<unsigned>(Index + 1), Line,
                                   Provenance));
      return;
    }
  };

  const bool IsDeadStore = Finding.Marker == "probe.dse.dead-store";
  const bool IsOverwrite = Finding.Marker == "probe.dse.overwritten-store";
  AddFirstLine("alias.noalias", "memory-safety", "complete",
               {"store", "memory-location"},
               {"isNoAlias", "isKnownNoAlias", "noAlias", "NoAlias",
                "AA.isNoAlias", "AA->isNoAlias"},
               "source-alias-query");
  AddFirstLine("alias.noalias", "memory-safety", "complete",
               {"store", "memory-location"}, {"mayAlias"},
               "source-negated-mayalias",
               [](llvm::StringRef Line) {
                 return Line.contains("!mayAlias") ||
                        Line.contains("== NoAlias") ||
                        Line.contains("!= MayAlias");
               });
  AddFirstLine("alias.unknown", "memory-safety", "unknown",
               {"store", "memory-location"}, {"mayAlias"},
               "source-mayalias-query",
               [](llvm::StringRef Line) {
                 return !Line.contains("!mayAlias") &&
                        !Line.contains("== NoAlias") &&
                        !Line.contains("!= MayAlias");
               });
  if (IsDeadStore) {
    AddFirstLine("memoryssa.dead-store", "deadness", "complete", {"store"},
                 {"isRemovable", "MemorySSA", "MSSA", "getMemoryAccess",
                  "isLiveOnEntryDef"},
                 "source-dse-deadness");
  }
  if (IsOverwrite) {
    AddFirstLine("memoryssa.clobber", "overwrite-safety", "complete",
                 {"store", "clobber"},
                 {"getClobberingMemoryAccess", "MemorySSA", "MSSA",
                  "getMemoryAccess"},
                 "source-dse-clobber");
    AddFirstLine("memory.no-intervening-store", "overwrite-safety", "complete",
                 {"store", "clobber"},
                 {"noIntervening", "getLocForWrite", "getDomMemoryDef"},
                 "source-overwrite-window");
    AddFirstLine("memory.no-intervening-read", "overwrite-safety",
                 "complete", {"store", "clobber"},
                 {"noInterveningRead", "noReadBetween", "readClobber",
                  "mayReadFromMemory"},
                 "source-no-intervening-read",
                 [](llvm::StringRef Line) {
                   return Line.contains("noInterveningRead") ||
                          Line.contains("noReadBetween") ||
                          Line.contains("!mayReadFromMemory") ||
                          Line.contains("!mayReadOrWriteMemory");
                 });
    AddFirstLine("memory.no-intervening-memory-effect", "overwrite-safety",
                 "complete", {"store", "clobber"},
                 {"noInterveningMemoryAccess", "noUnknownMemoryEffect",
                  "mayReadOrWriteMemory", "mayHaveSideEffects"},
                 "source-no-intervening-memory-effect",
                 [](llvm::StringRef Line) {
                   return Line.contains("noInterveningMemoryAccess") ||
                          Line.contains("noUnknownMemoryEffect") ||
                          Line.contains("!mayReadOrWriteMemory") ||
                          Line.contains("!mayHaveSideEffects");
                 });
    AddFirstLine("memory.unknown-intervening-effect", "safety-blocker",
                 "unsupported", {"store", "clobber"},
                 {"mayReadOrWriteMemory", "mayReadFromMemory",
                  "mayHaveSideEffects", "unknownMemoryEffect"},
                 "source-unknown-intervening-memory-effect",
                 [](llvm::StringRef Line) {
                   return !Line.contains("!mayReadOrWriteMemory") &&
                          !Line.contains("!mayReadFromMemory") &&
                          !Line.contains("!mayHaveSideEffects") &&
                          !Line.contains("noUnknownMemoryEffect");
                 });
    AddFirstLine("memory.overwrite.full", "overwrite-range", "complete",
                 {"store", "clobber", "memory-location"},
                 {"isCompleteOverwrite", "isOverwriteComplete", "covers",
                  "fullyOverwrites", "CompleteOverwrite"},
                 "source-full-overwrite-range");
    AddFirstLine("memory.overwrite.size.known", "overwrite-range",
                 "complete", {"store", "clobber", "memory-location"},
                 {"hasKnownSize", "hasValue", "getValue", "getSizeInBytes",
                  "LocationSize::precise", "knownSizeWithinFourBytes",
                  "knownSizeWithinEightBytes"},
                 "source-known-overwrite-size",
                 [](llvm::StringRef Line) {
                   return Line.contains("knownSizeWithinFourBytes") ||
                          Line.contains("knownSizeWithinEightBytes") ||
                          Line.contains("LocationSize::precise") ||
                          Line.contains("hasValue") ||
                          Line.contains("getValue") ||
                          Line.contains("getSizeInBytes") ||
                          (Line.contains("hasKnownSize") &&
                           !Line.contains("!hasKnownSize"));
                 });
    AddFirstLine("memory.overwrite.size.bounded-four-lane",
                 "overwrite-range", "complete",
                 {"store", "clobber", "memory-location"},
                 {"knownSizeWithinFourBytes", "getSizeInBytes",
                  "LocationSize::precise", "getValue"},
                 "source-bounded-four-lane-overwrite-size",
                 [](llvm::StringRef Line) {
                   return Line.contains("knownSizeWithinFourBytes") ||
                          Line.contains("LocationSize::precise(4") ||
                          Line.contains("getValue() <= 4") ||
                          Line.contains("getValue()<=4") ||
                          Line.contains("getValue() < 5") ||
                          Line.contains("getValue()<5") ||
                          Line.contains("<= 4") ||
                          Line.contains("<=4");
                 });
    AddFirstLine("memory.overwrite.size.bounded-eight-lane",
                 "overwrite-range", "complete",
                 {"store", "clobber", "memory-location"},
                 {"knownSizeWithinEightBytes", "getSizeInBytes",
                  "LocationSize::precise", "getValue"},
                 "source-bounded-eight-lane-overwrite-size",
                 [](llvm::StringRef Line) {
                   return Line.contains("knownSizeWithinEightBytes") ||
                          Line.contains("LocationSize::precise(8") ||
                          Line.contains("getValue() <= 8") ||
                          Line.contains("getValue()<=8") ||
                          Line.contains("getValue() < 9") ||
                          Line.contains("getValue()<9") ||
                          Line.contains("<= 8") ||
                          Line.contains("<=8");
                 });
    bool HasUnknownSize = false;
    bool HasSymbolicEqualSize = false;
    unsigned SymbolicUpperBound = 0;
    unsigned SymbolicLine = 0;
    llvm::StringRef SymbolicSource;
    unsigned EqualSizeLine = 0;
    llvm::StringRef EqualSizeSource;
    unsigned UpperBoundLine = 0;
    llvm::StringRef UpperBoundSource;
    auto IsSizeRelationLine = [](llvm::StringRef Line) {
      return Line.contains("Size") || Line.contains("size") ||
             Line.contains("getValue") || Line.contains("LocationSize");
    };
    auto IsSymbolicSizeEquality = [&](llvm::StringRef Line) {
      return Line.contains("sameSize") || Line.contains("equalSize") ||
             Line.contains("sameUnknownSize") ||
             (IsSizeRelationLine(Line) && Line.contains("==") &&
              !Line.contains("AtomicOrdering::"));
    };
    auto IsSymbolicSizeUpperBound = [&](llvm::StringRef Line) {
      if (Line.contains("LocationSize::precise")) {
        return false;
      }
      if (Line.contains("knownSizeWithinFourBytes") ||
          Line.contains("knownSizeWithinEightBytes")) {
        return true;
      }
      if (!IsSizeRelationLine(Line)) {
        return false;
      }
      return dseSymbolicSizeByteBound(Line) != 0;
    };
    for (size_t LineNumber = BeginLine; LineNumber <= EndLine; ++LineNumber) {
      const size_t Index = LineNumber - 1;
      llvm::StringRef Line = Lines[Index];
      const bool Unknown = Line.contains("unknownSize") ||
                           Line.contains("LocationSize::unknown") ||
                           Line.contains("!hasKnownSize");
      const bool EqualSize = IsSymbolicSizeEquality(Line);
      const bool UpperBound = IsSymbolicSizeUpperBound(Line);
      const unsigned Bound = UpperBound ? dseSymbolicSizeByteBound(Line) : 0;
      HasUnknownSize |= Unknown;
      HasSymbolicEqualSize |= EqualSize;
      if (Bound && (!SymbolicUpperBound || Bound < SymbolicUpperBound)) {
        SymbolicUpperBound = Bound;
      }
      if (!EqualSizeLine && EqualSize) {
        EqualSizeLine = static_cast<unsigned>(Index + 1);
        EqualSizeSource = Line;
      }
      if (!UpperBoundLine && UpperBound) {
        UpperBoundLine = static_cast<unsigned>(Index + 1);
        UpperBoundSource = Line;
      }
      if (!SymbolicLine && (Unknown || EqualSize || UpperBound)) {
        SymbolicLine = static_cast<unsigned>(Index + 1);
        SymbolicSource = Line;
      }
    }
    if (HasUnknownSize && EqualSizeLine) {
      Facts.push_back(analysisFact(
          "memory.overwrite.size.symbolic-equal", "overwrite-range",
          "complete", {"store", "clobber", "memory-location"}, EqualSizeLine,
          EqualSizeSource, "source-symbolic-equal-overwrite-size"));
    }
    if (HasUnknownSize && UpperBoundLine) {
      Facts.push_back(analysisFact(
          "memory.overwrite.size.symbolic-upper-bound", "overwrite-range",
          "complete", {"store", "clobber", "memory-location"},
          UpperBoundLine, UpperBoundSource,
          "source-symbolic-upper-bound-overwrite-size", "",
          SymbolicUpperBound));
    }
    if (HasUnknownSize && HasSymbolicEqualSize && SymbolicUpperBound &&
        SymbolicUpperBound <= 4 && SymbolicLine) {
      Facts.push_back(analysisFact(
          "memory.overwrite.size.symbolic-bounded-four-lane",
          "overwrite-range", "complete",
          {"store", "clobber", "memory-location"}, SymbolicLine,
          SymbolicSource, "source-symbolic-bounded-four-lane-overwrite-size",
          "", SymbolicUpperBound));
    } else if (HasUnknownSize && HasSymbolicEqualSize && SymbolicUpperBound &&
               SymbolicUpperBound <= 8 && SymbolicLine) {
      Facts.push_back(analysisFact(
          "memory.overwrite.size.symbolic-bounded-eight-lane",
          "overwrite-range", "complete",
          {"store", "clobber", "memory-location"}, SymbolicLine,
          SymbolicSource, "source-symbolic-bounded-eight-lane-overwrite-size",
          "", SymbolicUpperBound));
    }
    for (size_t LineNumber = BeginLine; LineNumber <= EndLine; ++LineNumber) {
      const size_t Index = LineNumber - 1;
      llvm::StringRef Line = Lines[Index];
      if (!lineHasAny(Line, {"fixedPartialOverwrite",
                             "knownPartialOverwriteByteMask",
                             "partialOverwriteByteMask",
                             "FixedPartialOverwrite"})) {
        continue;
      }
      const std::string ByteMask = dsePartialOverwriteByteMask(Line);
      if (ByteMask.empty()) {
        continue;
      }
      Facts.push_back(analysisFact(
          "memory.overwrite.partial.fixed-byte-mask", "overwrite-range",
          "complete", {"store", "clobber", "memory-location"},
          static_cast<unsigned>(Index + 1), Line,
          "source-fixed-partial-overwrite-byte-mask", ByteMask));
      break;
    }
    AddFirstLine("memory.overwrite.partial", "overwrite-range", "unsupported",
                 {"store", "clobber", "memory-location"},
                 {"isPartialOverwrite", "partialOverlap",
                  "mayPartiallyOverwrite", "PartialOverwrite"},
                 "source-partial-overwrite-range",
                 [](llvm::StringRef Line) {
                   return !Line.contains("fixedPartialOverwrite") &&
                          !Line.contains("knownPartialOverwriteByteMask") &&
                          !Line.contains("partialOverwriteByteMask") &&
                          !Line.contains("FixedPartialOverwrite");
                 });
    AddFirstLine("memory.overwrite.nonoverlap", "overwrite-range",
                 "unsupported", {"store", "clobber", "memory-location"},
                 {"NoOverlap", "nonOverlapping", "non-overlap"},
                 "source-nonoverlap-overwrite-range");
    AddFirstLine("memory.overwrite.nonoverlap", "overwrite-range",
                 "unsupported", {"store", "clobber", "memory-location"},
                 {"overlap"}, "source-negated-overlap-range",
                 [](llvm::StringRef Line) {
                   return Line.contains("!overlap") ||
                          Line.contains("!mayOverlap") ||
                          Line.contains("== NoOverlap");
                 });
    AddFirstLine("memory.overwrite.unknown-size", "overwrite-range",
                 "unsupported", {"store", "clobber", "memory-location"},
                 {"unknownSize", "hasKnownSize", "LocationSize::unknown"},
                 "source-unknown-size-overwrite-range",
                 [](llvm::StringRef Line) {
                   return Line.contains("unknownSize") ||
                          Line.contains("LocationSize::unknown") ||
                          Line.contains("!hasKnownSize");
                 });
  }
  AddFirstLine("memory.volatile-atomic-blocker", "safety-blocker",
               "unsupported", {"store"},
               {"isVolatile", "isAtomic", " volatile", " atomic",
                "CreateVolatile", "CreateAtomic"},
               "source-memory-side-effect-blocker");
  AddFirstLine("memory.volatile-blocker", "safety-blocker", "unsupported",
               {"store"}, {"isVolatile", " volatile", "CreateVolatile"},
               "source-volatile-memory-blocker");
  AddFirstLine("memory.atomic-unordered-blocker", "safety-blocker",
               "unsupported", {"store"},
               {"isAtomic", "getOrdering", "AtomicOrdering::Unordered"},
               "source-atomic-unordered-memory-blocker",
               [](llvm::StringRef Line) {
                 return Line.contains("AtomicOrdering::Unordered");
               });
  AddFirstLine("memory.atomic-ordered-blocker", "safety-blocker",
               "unsupported", {"store"},
               {"isAtomic", "getOrdering", "AtomicOrdering::Monotonic",
                "AtomicOrdering::Acquire", "AtomicOrdering::Release",
                "AtomicOrdering::AcquireRelease",
                "AtomicOrdering::SequentiallyConsistent"},
               "source-atomic-ordered-memory-blocker",
               [](llvm::StringRef Line) {
                 return Line.contains("AtomicOrdering::Monotonic") ||
                        Line.contains("AtomicOrdering::Acquire") ||
                        Line.contains("AtomicOrdering::Release") ||
                        Line.contains("AtomicOrdering::AcquireRelease") ||
                        Line.contains("AtomicOrdering::SequentiallyConsistent");
               });
  AddFirstLine("memory.atomic-ordering-unknown-blocker", "safety-blocker",
               "unsupported", {"store"},
               {"isAtomic", "getOrdering", "unknownAtomicOrdering"},
               "source-atomic-unknown-ordering-memory-blocker",
               [](llvm::StringRef Line) {
                 return Line.contains("unknownAtomicOrdering") ||
                        (Line.contains("isAtomic") &&
                        !Line.contains("&&") &&
                        !Line.contains("AtomicOrdering::Unordered") &&
                        !Line.contains("AtomicOrdering::Monotonic") &&
                        !Line.contains("AtomicOrdering::Acquire") &&
                        !Line.contains("AtomicOrdering::Release") &&
                        !Line.contains("AtomicOrdering::AcquireRelease") &&
                        !Line.contains("AtomicOrdering::SequentiallyConsistent"));
               });
  return Facts;
}

llvm::json::Value findingJson(
    const Finding &Finding, const SourceManager &SM, FileID File,
    const std::map<std::string, llvm::json::Value> &SemanticRegistry) {
  llvm::json::Array Context;
  for (const std::string &Line : contextLines(SM, File, Finding.Line, 2)) {
    Context.push_back(Line);
  }
  llvm::json::Object SourceRange{
      {"predicate_begin_line", Finding.Line},
      {"predicate_begin_column", Finding.Column},
      {"predicate_end_line", Finding.EndLine},
      {"predicate_end_column", Finding.EndColumn},
  };
  if (Finding.RewriteLine != 0) {
    SourceRange["rewrite_line"] = Finding.RewriteLine;
  }
  llvm::json::Object Result{
      {"file", Finding.File},
      {"line", Finding.Line},
      {"function", Finding.Function},
      {"branch_index", Finding.BranchIndex},
      {"opcode", Finding.Opcode},
      {"marker", Finding.Marker},
      {"pass", Finding.Pass},
      {"predicate_kind", Finding.PredicateKind},
      {"matched_pattern", Finding.PredicateSource},
      {"source", Finding.PredicateSource},
      {"predicate_source", Finding.PredicateSource},
      {"rewrite_source", Finding.RewriteSource},
      {"rewrite_status",
       Finding.RewriteStatus.empty()
           ? (Finding.RewriteLine != 0 ? "found" : "absent")
           : Finding.RewriteStatus},
      {"rewrite_absent_reason", Finding.RewriteAbsentReason},
      {"rewrite_search_scope", Finding.RewriteSearchScope},
      {"rewrite_line", Finding.RewriteLine},
      {"constraints", cloneJson(Finding.Constraints)},
      {"suggestion",
       "Wrap predicate with CV_PASS_PROBE_IF(\"" + Finding.Marker +
           "\", <predicate>)"},
      {"context", std::move(Context)},
      {"finding_source", "ast"},
      {"source_range", std::move(SourceRange)},
  };
  auto SemanticIt = SemanticRegistry.find(Finding.Marker);
  if (SemanticIt != SemanticRegistry.end()) {
    Result["semantic_facts"] = cloneJson(SemanticIt->second);
  }
  if (Finding.SourceIntent) {
    Result["source_intent"] = cloneJsonObject(*Finding.SourceIntent);
  } else if (std::optional<llvm::json::Object> SourceIntent =
                 sourceIntentFor(Finding, SemanticRegistry)) {
    Result["source_intent"] = std::move(*SourceIntent);
  }
  if (Finding.SourceIntentGraph) {
    Result["source_intent_graph"] = cloneJsonObject(*Finding.SourceIntentGraph);
  }
  llvm::json::Array AnalysisFacts = analysisFactsForFinding(Finding, SM, File);
  if (!AnalysisFacts.empty()) {
    Result["analysis_facts"] = cloneArray(AnalysisFacts);
    if (auto *Graph = Result["source_intent_graph"].getAsObject()) {
      (*Graph)["analysis_facts"] = cloneArray(AnalysisFacts);
    }
  }
  if (Finding.OptimizationTransaction) {
    Result["optimization_transaction"] =
        cloneJsonObject(*Finding.OptimizationTransaction);
  }
  return Result;
}

StatementMatcher ifWithCondition(StatementMatcher ConditionMatcher,
                                 llvm::StringRef BindName) {
  return ifStmt(hasCondition(expr(ConditionMatcher).bind("condition")),
                hasCondition(expr(ConditionMatcher).bind(BindName)))
      .bind("if");
}

void registerGeneratedAstMatcherSpecs(MatchFinder &Finder,
                                       MatchFinder::MatchCallback *Callback) {
  auto AllocaInstDecl = cxxRecordDecl(anyOf(hasName("AllocaInst"),
                                            hasName("llvm::AllocaInst"),
                                            hasName("::llvm::AllocaInst")));
  auto AllocaInstType = qualType(hasDeclaration(AllocaInstDecl));
  StatementMatcher AllocaInstObject =
      expr(anyOf(hasType(AllocaInstType), hasType(pointsTo(AllocaInstType)),
                 hasType(references(AllocaInstType))));
  for (const cv::AstMatcherSpec &Spec : cv::kAstMatcherSpecs) {
    switch (Spec.kind) {
    case cv::AstMatcherKind::FunctionCall: {
      StatementMatcher CallMatcher =
          callExpr(callee(functionDecl(hasName(Spec.name))));
      Finder.addMatcher(
          ifWithCondition(anyOf(CallMatcher, hasDescendant(CallMatcher)),
                          Spec.bindName),
          Callback);
      break;
    }
    case cv::AstMatcherKind::MemberCall: {
      StatementMatcher CallMatcher =
          cxxMemberCallExpr(callee(cxxMethodDecl(hasName(Spec.name))));
      if (llvm::StringRef(Spec.bindName) == "unused-alloca") {
        CallMatcher = cxxMemberCallExpr(
            callee(cxxMethodDecl(hasName(Spec.name))), on(AllocaInstObject));
      }
      Finder.addMatcher(
          ifWithCondition(anyOf(CallMatcher, hasDescendant(CallMatcher)),
                          Spec.bindName),
          Callback);
      break;
    }
    case cv::AstMatcherKind::MemberCallUIntArg: {
      unsigned ArgValue = 0;
      if (llvm::StringRef(Spec.nestedName).getAsInteger(10, ArgValue)) {
        break;
      }
      StatementMatcher CallMatcher = cxxMemberCallExpr(
          callee(cxxMethodDecl(hasName(Spec.name))), on(AllocaInstObject),
          hasArgument(0, ignoringParenImpCasts(integerLiteral(equals(ArgValue)))));
      Finder.addMatcher(
          ifWithCondition(anyOf(CallMatcher, hasDescendant(CallMatcher)),
                          Spec.bindName),
          Callback);
      break;
    }
    case cv::AstMatcherKind::MemberRangeEmpty: {
      StatementMatcher RangeCall =
          cxxMemberCallExpr(callee(cxxMethodDecl(hasName(Spec.name))),
                            on(AllocaInstObject));
      StatementMatcher EmptyCall = cxxMemberCallExpr(
          callee(cxxMethodDecl(hasName(Spec.nestedName))),
          callee(memberExpr(hasObjectExpression(
              expr(anyOf(RangeCall, hasDescendant(RangeCall)))))));
      Finder.addMatcher(
          ifWithCondition(anyOf(EmptyCall, hasDescendant(EmptyCall)),
                          Spec.bindName),
          Callback);
      break;
    }
    case cv::AstMatcherKind::NegatedMemberCallUIntArg: {
      unsigned ArgValue = 0;
      if (llvm::StringRef(Spec.nestedName).getAsInteger(10, ArgValue)) {
        break;
      }
      StatementMatcher CallMatcher = cxxMemberCallExpr(
          callee(cxxMethodDecl(hasName(Spec.name))), on(AllocaInstObject),
          hasArgument(0, ignoringParenImpCasts(integerLiteral(equals(ArgValue)))));
      StatementMatcher NegatedCall = unaryOperator(
          hasOperatorName("!"),
          hasUnaryOperand(ignoringParenImpCasts(CallMatcher)));
      Finder.addMatcher(
          ifWithCondition(anyOf(NegatedCall, hasDescendant(NegatedCall)),
                          Spec.bindName),
          Callback);
      break;
    }
    case cv::AstMatcherKind::TypeName:
      Finder.addMatcher(
          ifWithCondition(hasDescendant(typeLoc(loc(qualType(hasDeclaration(
                              namedDecl(hasName(Spec.name))))))),
                          Spec.bindName),
          Callback);
      break;
    case cv::AstMatcherKind::BinaryEquality:
      Finder.addMatcher(
          ifWithCondition(binaryOperator(hasOperatorName("==")), Spec.bindName),
          Callback);
      break;
    case cv::AstMatcherKind::NestedFunctionCall:
      Finder.addMatcher(
          ifWithCondition(
              hasDescendant(callExpr(
                  callee(functionDecl(hasName(Spec.name))),
                  hasDescendant(callExpr(callee(functionDecl(
                      hasName(Spec.nestedName))))))),
              Spec.bindName),
          Callback);
      break;
    }
  }
}

struct SlpFunctionSummary {
  std::string File;
  std::string Name;
  unsigned StartLine = 0;
  unsigned EndLine = 0;
  std::string Signature;
  std::string Body;
  std::vector<std::string> Lines;
  std::vector<std::string> Roles;
  std::vector<std::string> Parameters;
  llvm::json::Array Evidence;
  std::string Opcode;
  llvm::json::Array Calls;
  llvm::json::Array Conditions;
  std::vector<std::string> CalledFunctions;
  std::vector<o2t::sourcegraph::SourceCfgBlock> CfgBlocks;
  std::vector<o2t::sourcegraph::SourceDataflowDef> DataflowDefs;
  std::vector<o2t::sourcegraph::SourceDataflowUse> DataflowUses;
};

// Walk AST parents from a node up to the enclosing FunctionDecl, so each finding
// carries the fold function it belongs to (used to assemble per-function pass
// models / branch sequences -- cv-extract-pass-model). Empty if not inside one.
std::string enclosingFunctionName(clang::ASTContext &Ctx, const clang::Stmt *S) {
  if (!S) {
    return "";
  }
  clang::DynTypedNode Node = clang::DynTypedNode::create(*S);
  for (int Depth = 0; Depth < 100; ++Depth) {
    if (const auto *FD = Node.get<clang::FunctionDecl>()) {
      return FD->getNameAsString();
    }
    clang::DynTypedNodeList Parents = Ctx.getParents(Node);
    if (Parents.empty()) {
      break;
    }
    Node = Parents[0];
  }
  return "";
}

// Ordinal position of a branch's IfStmt among the top-level if-statements of its
// enclosing function -- the cascade order code-lift B relies on. -1 if the if is
// nested (not a direct function-body child) or has no enclosing function.
int branchIndexInFunction(clang::ASTContext &Ctx, const clang::IfStmt *If) {
  if (!If) {
    return -1;
  }
  const clang::FunctionDecl *FD = nullptr;
  clang::DynTypedNode Node = clang::DynTypedNode::create(*If);
  for (int Depth = 0; Depth < 100; ++Depth) {
    if (const auto *F = Node.get<clang::FunctionDecl>()) {
      FD = F;
      break;
    }
    clang::DynTypedNodeList Parents = Ctx.getParents(Node);
    if (Parents.empty()) {
      break;
    }
    Node = Parents[0];
  }
  if (!FD || !FD->hasBody()) {
    return -1;
  }
  const auto *Body = llvm::dyn_cast<clang::CompoundStmt>(FD->getBody());
  if (!Body) {
    return -1;
  }
  int Index = 0;
  for (const clang::Stmt *S : Body->body()) {
    if (const auto *Candidate = llvm::dyn_cast<clang::IfStmt>(S)) {
      if (Candidate == If) {
        return Index;
      }
      ++Index;
    }
  }
  return -1;
}

// The instruction opcode a fold function handles, mined from the SOURCE rather
// than the (hand-maintained, possibly-mislabeled) marker registry. LLVM names a
// fold/visitor after the opcode it folds -- visitAdd, foldAddMulti, foldXorOfX --
// so the opcode is the first known-opcode CamelCase word in the function name.
// This keeps a multi-branch fold's opcode stable even when an individual branch
// carries a pattern marker borrowed from a different operation (e.g. an add fold
// with an `xor-self` shaped branch). Returns the lowercase operation name (the
// key cv_optimization_registry.BV_OP_FOR_OPERATION expects), or "" if none.
std::string opcodeFromFunctionName(llvm::StringRef Name) {
  // LLVM opcode token -> operation name. Longer tokens first so LShr/AShr win
  // over any prefix collision.
  static const std::pair<llvm::StringRef, llvm::StringRef> Opcodes[] = {
      {"LShr", "lshr"}, {"AShr", "ashr"}, {"Add", "add"}, {"Sub", "sub"},
      {"Mul", "mul"},   {"Shl", "shl"},   {"Xor", "xor"}, {"And", "and"},
      {"Or", "or"},
  };
  for (const auto &[Token, Operation] : Opcodes) {
    size_t Pos = 0;
    while ((Pos = Name.find(Token, Pos)) != llvm::StringRef::npos) {
      // CamelCase word boundary before: start, or a lowercase->upper transition.
      const bool BoundaryBefore =
          Pos == 0 || (std::islower(static_cast<unsigned char>(Name[Pos - 1])) != 0);
      // Word boundary after: end, next char uppercase (next word), or non-alpha.
      const size_t After = Pos + Token.size();
      const bool BoundaryAfter =
          After >= Name.size() ||
          std::isupper(static_cast<unsigned char>(Name[After])) != 0 ||
          std::isalpha(static_cast<unsigned char>(Name[After])) == 0;
      if (BoundaryBefore && BoundaryAfter) {
        return Operation.str();
      }
      ++Pos;
    }
  }
  return "";
}

class MiningCallback : public MatchFinder::MatchCallback {
public:
  explicit MiningCallback(
      std::map<std::string, RegistryEntry> Registry,
      std::map<std::string, llvm::json::Value> SemanticRegistry)
      : Registry(std::move(Registry)),
        SemanticRegistry(std::move(SemanticRegistry)) {}

  void run(const MatchFinder::MatchResult &Result) override {
    if (const auto *Function =
            Result.Nodes.getNodeAs<FunctionDecl>("slp-function")) {
      collectSlpFunction(Function, Result);
      return;
    }
    const auto *If = Result.Nodes.getNodeAs<IfStmt>("if");
    const auto *Condition = Result.Nodes.getNodeAs<Expr>("condition");
    if (!If || !Condition || !Result.SourceManager || !Result.Context) {
      return;
    }
    SourceManager &SM = *Result.SourceManager;
    if (!SM.isWrittenInMainFile(Condition->getBeginLoc())) {
      return;
    }
    const SourceLocation Begin = Condition->getBeginLoc();
    const SourceLocation End = Lexer::getLocForEndOfToken(
        Condition->getEndLoc(), 0, SM, Result.Context->getLangOpts());
    if (Begin.isInvalid() || End.isInvalid()) {
      return;
    }
    const std::string PredicateSource =
        sourceText(SM, Result.Context->getLangOpts(), Condition->getSourceRange());
    std::string Marker = markerForBoundNode(Result);
    if ((Marker == "probe.instcombine.add-zero" ||
         Marker == "probe.instcombine.mul-one") &&
        llvm::StringRef(PredicateSource).contains("m_SplatOrPoison")) {
      Marker = markerForConditionText(PredicateSource);
    }
    if (Marker.empty()) {
      Marker = markerForConditionText(PredicateSource);
    }
    if (Marker.empty()) {
      return;
    }
    FileID File = SM.getFileID(Begin);
    const unsigned Line = SM.getSpellingLineNumber(Begin);
    const auto Key = std::make_pair(Line, Marker);
    if (Seen.count(Key) != 0) {
      return;
    }
    Seen.insert(Key);

    std::string Pass;
    std::string PredicateKind;
    llvm::json::Value Constraints = llvm::json::Object{};
    auto It = Registry.find(Marker);
    if (It != Registry.end()) {
      Pass = It->second.Pass;
      PredicateKind = It->second.PredicateKind;
      Constraints = cloneJson(It->second.Constraints);
    }

    Finding Finding;
    Finding.File = SM.getFilename(Begin).str();
    Finding.Line = Line;
    Finding.Function = enclosingFunctionName(*Result.Context, Condition);
    Finding.BranchIndex = branchIndexInFunction(*Result.Context, If);
    Finding.Opcode = opcodeFromFunctionName(Finding.Function);
    Finding.Column = SM.getSpellingColumnNumber(Begin);
    Finding.EndLine = SM.getSpellingLineNumber(End);
    Finding.EndColumn = SM.getSpellingColumnNumber(End);
    Finding.Marker = Marker;
    Finding.Pass = Pass;
    Finding.PredicateKind = PredicateKind;
    Finding.Constraints = cloneJson(Constraints);
    Finding.PredicateSource = PredicateSource;
    Finding.RewriteSearchScope = "then-block-v1";

    if (llvm::StringRef(Marker).starts_with("probe.instcombine.")) {
      std::string TextMarker = markerForConditionText(PredicateSource);
      if (llvm::StringRef(TextMarker).starts_with("probe.instcombine.") &&
          TextMarker != Marker) {
        Marker = TextMarker;
        Finding.Marker = Marker;
        auto TextIt = Registry.find(Marker);
        if (TextIt != Registry.end()) {
          Finding.Pass = TextIt->second.Pass;
          Finding.PredicateKind = TextIt->second.PredicateKind;
          Finding.Constraints = cloneJson(TextIt->second.Constraints);
        }
      }
    }
    const auto FinalKey = std::make_pair(Line, Marker);
    if (FinalKey != Key) {
      if (Seen.count(FinalKey) != 0) {
        return;
      }
      Seen.insert(FinalKey);
    }

    if (const Stmt *Rewrite =
            firstRewriteStmt(If->getThen(), SM, Result.Context->getLangOpts())) {
      Finding.RewriteSource =
          sourceText(SM, Result.Context->getLangOpts(), Rewrite->getSourceRange());
      Finding.RewriteStatus = "found";
      Finding.RewriteLine = SM.getSpellingLineNumber(Rewrite->getBeginLoc());
      Finding.SourceIntent =
          sourceIntentFromAst(Finding, Condition, Rewrite, SM,
                              Result.Context->getLangOpts(), SemanticRegistry);
      if (!Finding.SourceIntent) {
        Finding.SourceIntent = sourceIntentFor(Finding, SemanticRegistry);
      }
      Finding.SourceIntentGraph = sourceIntentGraphFromAst(
          Finding, Condition, Rewrite, SM, Result.Context->getLangOpts(),
          Finding.SourceIntent);
    } else {
      Finding.RewriteStatus = "absent";
      Finding.RewriteAbsentReason = rewriteAbsentReason(If->getThen());
      Finding.SourceIntentGraph = sourceIntentGraphFromAst(
          Finding, Condition, nullptr, SM, Result.Context->getLangOpts(),
          Finding.SourceIntent);
    }

    Output.push_back(findingJson(Finding, SM, File, SemanticRegistry));
  }

  void finalizeSlpTransactions() {
    if (SlpFinalized) {
      return;
    }
    SlpFinalized = true;
    std::optional<llvm::json::Value> Finding = slpTransactionFinding();
    if (Finding) {
      Output.push_back(std::move(*Finding));
    }
  }

  const std::vector<llvm::json::Value> &findings() const { return Output; }

private:
  bool hasRole(const SlpFunctionSummary &Summary, llvm::StringRef Role) const {
    return std::find(Summary.Roles.begin(), Summary.Roles.end(), Role) !=
           Summary.Roles.end();
  }

  const SlpFunctionSummary *firstSummaryWithRole(llvm::StringRef Role) const {
    const SlpFunctionSummary *Fallback = nullptr;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      if (!hasRole(Summary, Role)) {
        continue;
      }
      if (!Fallback) {
        Fallback = &Summary;
      }
      const std::string LowerName = llvm::StringRef(Summary.Name).lower();
      if (Role == "candidate-tree" &&
          (LowerName.find("discover") != std::string::npos ||
           LowerName.find("candidate") != std::string::npos ||
           LowerName.find("buildtree") != std::string::npos ||
           LowerName.find("vectorize") != std::string::npos)) {
        return &Summary;
      }
      if (Role == "legality" &&
          (LowerName.find("legal") != std::string::npos ||
           LowerName.find("valid") != std::string::npos ||
           LowerName.find("canvectorize") != std::string::npos)) {
        return &Summary;
      }
      if (Role == "profitability" &&
          (LowerName.find("profit") != std::string::npos ||
           LowerName.find("cost") != std::string::npos)) {
        return &Summary;
      }
      if (Role == "vector-emission" &&
          (LowerName.find("emit") != std::string::npos ||
           LowerName.find("materialize") != std::string::npos ||
           LowerName.find("vectorize") != std::string::npos ||
           LowerName.find("create") != std::string::npos)) {
        return &Summary;
      }
      if (Role == "scalar-replacement" &&
          (LowerName.find("replace") != std::string::npos ||
           LowerName.find("commit") != std::string::npos)) {
        return &Summary;
      }
    }
    return Fallback;
  }

  llvm::json::Object roleEvidence(const SlpFunctionSummary &Summary,
                                  const std::string &Role,
                                  std::initializer_list<llvm::StringRef> Tokens,
                                  const std::string &Opcode = "") const {
    const unsigned Line = lineForToken(Summary.StartLine, Summary.Lines, Tokens);
    llvm::json::Object Evidence{{"role", Role},
                                {"function", Summary.Name},
                                {"line", static_cast<int>(Line)},
                                {"source", sourceLineForToken(Summary.Lines, Tokens)}};
    if (!Opcode.empty()) {
      Evidence["opcode"] = Opcode;
    }
    return Evidence;
  }

  llvm::json::Object roleEvidence(const SlpFunctionSummary &Summary,
                                  const std::string &Role,
                                  const std::vector<std::string> &Tokens,
                                  const std::string &Opcode = "") const {
    const unsigned Line = lineForToken(Summary.StartLine, Summary.Lines, Tokens);
    llvm::json::Object Evidence{{"role", Role},
                                {"function", Summary.Name},
                                {"line", static_cast<int>(Line)},
                                {"source", sourceLineForToken(Summary.Lines, Tokens)}};
    if (!Opcode.empty()) {
      Evidence["opcode"] = Opcode;
    }
    return Evidence;
  }

  std::vector<o2t::sourcegraph::SourceCfgBlock>
  collectCfgBlocks(const FunctionDecl *Function, ASTContext &Context,
                   const SourceManager &SM) const {
    std::vector<o2t::sourcegraph::SourceCfgBlock> Blocks;
    if (!Function || !Function->hasBody()) {
      return Blocks;
    }
    CFG::BuildOptions Options;
    Options.AddImplicitDtors = true;
    std::unique_ptr<CFG> Graph =
        CFG::buildCFG(Function, Function->getBody(), &Context, Options);
    if (!Graph) {
      return Blocks;
    }
    for (const CFGBlock *Block : *Graph) {
      if (!Block) {
        continue;
      }
      o2t::sourcegraph::SourceCfgBlock Out;
      Out.Id = Block->getBlockID();
      unsigned BeginLine = 0;
      unsigned EndLine = 0;
      for (const CFGElement &Element : *Block) {
        std::optional<CFGStmt> Statement = Element.getAs<CFGStmt>();
        if (!Statement) {
          continue;
        }
        const Stmt *S = Statement->getStmt();
        if (!S || S->getBeginLoc().isInvalid() ||
            !SM.isWrittenInMainFile(S->getBeginLoc())) {
          continue;
        }
        const unsigned Line = SM.getSpellingLineNumber(S->getBeginLoc());
        Out.StatementLines.push_back(Line);
        BeginLine = BeginLine == 0 ? Line : std::min(BeginLine, Line);
        EndLine = std::max(EndLine, Line);
      }
      if (const Stmt *Terminator = Block->getTerminatorStmt()) {
        if (Terminator->getBeginLoc().isValid() &&
            SM.isWrittenInMainFile(Terminator->getBeginLoc())) {
          const unsigned Line =
              SM.getSpellingLineNumber(Terminator->getBeginLoc());
          Out.StatementLines.push_back(Line);
          BeginLine = BeginLine == 0 ? Line : std::min(BeginLine, Line);
          EndLine = std::max(EndLine, Line);
        }
      }
      Out.BeginLine = BeginLine;
      Out.EndLine = EndLine;
      for (const CFGBlock::AdjacentBlock &Successor : Block->succs()) {
        if (const CFGBlock *Reachable = Successor.getReachableBlock()) {
          Out.Successors.push_back(Reachable->getBlockID());
        }
      }
      std::sort(Out.StatementLines.begin(), Out.StatementLines.end());
      Out.StatementLines.erase(
          std::unique(Out.StatementLines.begin(), Out.StatementLines.end()),
          Out.StatementLines.end());
      std::sort(Out.Successors.begin(), Out.Successors.end());
      Out.Successors.erase(
          std::unique(Out.Successors.begin(), Out.Successors.end()),
          Out.Successors.end());
      Blocks.push_back(std::move(Out));
    }
    return Blocks;
  }

  bool isMainFileLocation(SourceLocation Location,
                          const SourceManager &SM) const {
    return Location.isValid() && SM.isWrittenInMainFile(Location);
  }

  void addDataflowDef(
      std::vector<o2t::sourcegraph::SourceDataflowDef> &Defs,
      llvm::StringRef Symbol, SourceLocation Location,
      llvm::StringRef Source, llvm::StringRef Kind,
      const SourceManager &SM) const {
    if (Symbol.empty() || !isMainFileLocation(Location, SM)) {
      return;
    }
    Defs.push_back(o2t::sourcegraph::SourceDataflowDef{
        Symbol.str(),
        SM.getSpellingLineNumber(Location),
        SM.getSpellingColumnNumber(Location),
        Source.str(),
        Kind.str()});
  }

  void addDataflowUse(
      std::vector<o2t::sourcegraph::SourceDataflowUse> &Uses,
      llvm::StringRef Symbol, SourceLocation Location, llvm::StringRef Source,
      const SourceManager &SM) const {
    if (Symbol.empty() || !isMainFileLocation(Location, SM)) {
      return;
    }
    Uses.push_back(o2t::sourcegraph::SourceDataflowUse{
        Symbol.str(),
        SM.getSpellingLineNumber(Location),
        SM.getSpellingColumnNumber(Location),
        Source.str()});
  }

  void addDataflowUse(
      std::vector<o2t::sourcegraph::SourceDataflowUse> &Uses,
      const DeclRefExpr *Reference, const SourceManager &SM,
      const LangOptions &LangOpts) const {
    if (!Reference) {
      return;
    }
    if (const auto *Variable = dyn_cast<VarDecl>(Reference->getDecl())) {
      addDataflowUse(Uses, Variable->getNameAsString(),
                     Reference->getBeginLoc(), exprText(Reference, SM, LangOpts),
                     SM);
    }
  }

  void collectLValueDef(
      const Expr *Expression,
      std::vector<o2t::sourcegraph::SourceDataflowDef> &Defs,
      const SourceManager &SM, const LangOptions &LangOpts,
      llvm::StringRef Kind) const {
    if (!Expression) {
      return;
    }
    Expression = Expression->IgnoreParenImpCasts();
    const std::string Symbol = accessPathSymbol(Expression, SM, LangOpts);
    if (!Symbol.empty()) {
      addDataflowDef(Defs, Symbol, Expression->getBeginLoc(),
                     exprText(Expression, SM, LangOpts), Kind, SM);
    }
  }

  void collectAstDataflow(
      const Stmt *Node,
      std::vector<o2t::sourcegraph::SourceDataflowDef> &Defs,
      std::vector<o2t::sourcegraph::SourceDataflowUse> &Uses,
      const SourceManager &SM, const LangOptions &LangOpts) const {
    if (!Node) {
      return;
    }
    if (const auto *Declaration = dyn_cast<DeclStmt>(Node)) {
      for (const Decl *Declared : Declaration->decls()) {
        const auto *Variable = dyn_cast<VarDecl>(Declared);
        if (!Variable || !Variable->hasInit()) {
          continue;
        }
        collectAstDataflow(Variable->getInit(), Defs, Uses, SM, LangOpts);
        addDataflowDef(Defs, Variable->getNameAsString(),
                       Variable->getLocation(), Variable->getName(),
                       "local-init", SM);
      }
      return;
    }
    if (const auto *Unary = dyn_cast<UnaryOperator>(Node)) {
      if (Unary->isIncrementDecrementOp()) {
        collectAstDataflow(Unary->getSubExpr(), Defs, Uses, SM, LangOpts);
        collectLValueDef(Unary->getSubExpr(), Defs, SM, LangOpts,
                         "unary-update");
        return;
      }
    }
    if (const auto *Binary = dyn_cast<BinaryOperator>(Node)) {
      if (Binary->isAssignmentOp()) {
        if (isa<CompoundAssignOperator>(Binary)) {
          collectAstDataflow(Binary->getLHS(), Defs, Uses, SM, LangOpts);
        }
        collectAstDataflow(Binary->getRHS(), Defs, Uses, SM, LangOpts);
        collectLValueDef(Binary->getLHS(), Defs, SM, LangOpts, "assignment");
        return;
      }
    }
    if (const auto *Subscript = dyn_cast<ArraySubscriptExpr>(Node)) {
      const std::string Symbol = accessPathSymbol(Subscript, SM, LangOpts);
      if (!Symbol.empty()) {
        addDataflowUse(Uses, Symbol, Subscript->getBeginLoc(),
                       exprText(Subscript, SM, LangOpts), SM);
      }
      for (const Stmt *Child : Node->children()) {
        collectAstDataflow(Child, Defs, Uses, SM, LangOpts);
      }
      return;
    }
    if (const auto *Member = dyn_cast<MemberExpr>(Node)) {
      const std::string Symbol = accessPathSymbol(Member, SM, LangOpts);
      if (!Symbol.empty()) {
        addDataflowUse(Uses, Symbol, Member->getBeginLoc(),
                       exprText(Member, SM, LangOpts), SM);
      }
      for (const Stmt *Child : Node->children()) {
        collectAstDataflow(Child, Defs, Uses, SM, LangOpts);
      }
      return;
    }
    if (const auto *Reference = dyn_cast<DeclRefExpr>(Node)) {
      addDataflowUse(Uses, Reference, SM, LangOpts);
      return;
    }
    for (const Stmt *Child : Node->children()) {
      collectAstDataflow(Child, Defs, Uses, SM, LangOpts);
    }
  }

  void collectAstDataflowFacts(
      const FunctionDecl *Function, const SourceManager &SM,
      const LangOptions &LangOpts,
      std::vector<o2t::sourcegraph::SourceDataflowDef> &Defs,
      std::vector<o2t::sourcegraph::SourceDataflowUse> &Uses) const {
    if (!Function || !Function->hasBody()) {
      return;
    }
    for (const ParmVarDecl *Parameter : Function->parameters()) {
      addDataflowDef(Defs, Parameter->getNameAsString(),
                     Function->getBeginLoc(), Parameter->getName(), "parameter",
                     SM);
    }
    collectAstDataflow(Function->getBody(), Defs, Uses, SM, LangOpts);

    auto DefKey = [](const o2t::sourcegraph::SourceDataflowDef &Def) {
      return std::make_tuple(Def.Symbol, Def.Line, Def.Column, Def.Kind);
    };
    std::sort(Defs.begin(), Defs.end(),
              [&](const auto &LHS, const auto &RHS) {
                return DefKey(LHS) < DefKey(RHS);
              });
    Defs.erase(std::unique(Defs.begin(), Defs.end(),
                           [&](const auto &LHS, const auto &RHS) {
                             return DefKey(LHS) == DefKey(RHS);
                           }),
               Defs.end());

    auto UseKey = [](const o2t::sourcegraph::SourceDataflowUse &Use) {
      return std::make_tuple(Use.Symbol, Use.Line, Use.Column);
    };
    std::sort(Uses.begin(), Uses.end(),
              [&](const auto &LHS, const auto &RHS) {
                return UseKey(LHS) < UseKey(RHS);
              });
    Uses.erase(std::unique(Uses.begin(), Uses.end(),
                           [&](const auto &LHS, const auto &RHS) {
                             return UseKey(LHS) == UseKey(RHS);
                           }),
               Uses.end());
  }

  void collectSlpFunction(const FunctionDecl *Function,
                          const MatchFinder::MatchResult &Result) {
    if (!Function || !Function->hasBody() || !Result.SourceManager ||
        !Result.Context) {
      return;
    }
    SourceManager &SM = *Result.SourceManager;
    const LangOptions &LangOpts = Result.Context->getLangOpts();
    SourceLocation Begin = Function->getBeginLoc();
    if (Begin.isInvalid() || !SM.isWrittenInMainFile(Begin)) {
      return;
    }
    FileID File = SM.getFileID(Begin);
    if (MainSource.empty()) {
      bool Invalid = false;
      MainSource = SM.getBufferData(File, &Invalid).str();
      if (!Invalid) {
        MainLines = splitLines(MainSource);
      }
      MainFile = SM.getFilename(Begin).str();
      MainFileID = File;
    }
    SlpFunctionSummary Summary;
    Summary.File = SM.getFilename(Begin).str();
    Summary.Name = Function->getNameAsString();
    Summary.StartLine = SM.getSpellingLineNumber(Begin);
    Summary.EndLine =
        SM.getSpellingLineNumber(Function->getBody()->getEndLoc());
    Summary.Body = sourceText(SM, LangOpts, Function->getSourceRange());
    Summary.Lines = splitLines(Summary.Body);
    Summary.Signature = Summary.Lines.empty() ? "" : trim(Summary.Lines.front());
    for (const ParmVarDecl *Parameter : Function->parameters()) {
      Summary.Parameters.push_back(Parameter->getNameAsString());
    }
    collectSlpAstBackbone(Function->getBody(), SM, LangOpts, Summary.Calls,
                          Summary.Conditions, Summary.CalledFunctions);
    Summary.CfgBlocks = collectCfgBlocks(Function, *Result.Context, SM);
    collectAstDataflowFacts(Function, SM, LangOpts, Summary.DataflowDefs,
                            Summary.DataflowUses);
    llvm::StringRef BodyRef(Summary.Body);
    if (BodyRef.contains("TreeEntry") &&
        textContainsAny(BodyRef, {"Scalars", "VectorizableTree", "packOperand",
                                  "ExternalUses", "buildTree"})) {
      Summary.Roles.push_back("candidate-tree");
      Summary.Evidence.push_back(roleEvidence(
          Summary, "candidate-tree",
          {"Scalars", "VectorizableTree", "TreeEntry", "buildTree"}));
    }
    if (textContainsAny(BodyRef, {"allSameOpcode", "sameOpcode",
                                  "isValidElementType", "canVectorize"})) {
      Summary.Roles.push_back("legality");
      const std::string Opcode = slpOpcodeForText(sourceLineForToken(
          Summary.Lines,
          {"allSameOpcode", "sameOpcode", "isValidElementType",
           "canVectorize"}));
      Summary.Evidence.push_back(roleEvidence(
          Summary, "legality",
          {"allSameOpcode", "sameOpcode", "isValidElementType",
           "canVectorize"},
          Opcode));
    }
    if (textContainsAny(BodyRef, {"getEntryCost", "TTI", "isProfitable"})) {
      Summary.Roles.push_back("profitability");
      Summary.Evidence.push_back(roleEvidence(
          Summary, "profitability", {"getEntryCost", "TTI", "isProfitable"}));
    }
    const std::string Opcode = slpOpcodeForText(BodyRef);
    if (!Opcode.empty() && textContainsAny(BodyRef, VectorEmissionTokens)) {
      Summary.Opcode = Opcode;
      Summary.Roles.push_back("vector-emission");
      Summary.Evidence.push_back(roleEvidence(
          Summary, "vector-emission", VectorEmissionTokens, Opcode));
    }
    if (textContainsAny(BodyRef, {"replaceScalarUses", "replaceExternalUses",
                                  "replaceAllUsesWith", "ExternalUses"})) {
      Summary.Roles.push_back("scalar-replacement");
      Summary.Evidence.push_back(roleEvidence(
          Summary, "scalar-replacement",
          {"replaceScalarUses", "replaceExternalUses", "replaceAllUsesWith",
           "ExternalUses"}));
    }
    auto HasAccessPathFact = [](const auto &Facts) {
      for (const auto &Fact : Facts) {
        if (Fact.Symbol.find('.') != std::string::npos ||
            Fact.Symbol.find("->") != std::string::npos ||
            Fact.Symbol.find('[') != std::string::npos) {
          return true;
        }
      }
      return false;
    };
    if (!Summary.Roles.empty() || BodyRef.contains("TreeEntry") ||
        HasAccessPathFact(Summary.DataflowDefs) ||
        HasAccessPathFact(Summary.DataflowUses)) {
      SlpSummaries.push_back(std::move(Summary));
    }
  }

  std::vector<int> mapFromObject(const llvm::json::Object &Object) const {
    std::vector<int> Result;
    if (const auto *Array = Object.getArray("map")) {
      for (const llvm::json::Value &Value : *Array) {
        if (auto Number = Value.getAsInteger()) {
          Result.push_back(static_cast<int>(*Number));
        }
      }
    }
    return Result;
  }

  llvm::json::Object defaultLaneMapping(int Lanes, unsigned Line,
                                        const std::string &Source) const {
    return makeLaneMapping("identity", Lanes, identityMap(Lanes), Line, Source,
                           "default-identity");
  }

  int discoveredLaneCount(const SlpFunctionSummary &Candidate) const {
    std::regex DeclPattern(R"(Scalars\s*\[\s*(\d+)\s*\])");
    std::smatch Match;
    if (std::regex_search(MainSource, Match, DeclPattern)) {
      return std::stoi(Match[1].str());
    }
    int MaxIndex = -1;
    for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                 DeclPattern),
         End;
         It != End; ++It) {
      MaxIndex = std::max(MaxIndex, std::stoi((*It)[1].str()));
    }
    if (MaxIndex >= 0) {
      return MaxIndex + 1;
    }
    (void)Candidate;
    return 4;
  }

  llvm::json::Object scalableInfo(int DefaultBaseLanes) const {
    llvm::StringRef Source(MainSource);
    if (!Source.contains("getScalable") && !Source.contains("isScalable") &&
        !Source.contains("scalable")) {
      return llvm::json::Object{{"scalable", false}};
    }
    int BaseLanes = DefaultBaseLanes > 0 ? DefaultBaseLanes : 4;
    llvm::json::Array Provenance;
    std::vector<std::regex> Patterns = {
        std::regex(R"(ElementCount::getScalable\s*\(\s*(\d+)\s*\))"),
        std::regex(R"(base_lanes\s*=\s*(\d+))", std::regex::icase),
        std::regex(R"(scalable_base_lanes\s*=\s*(\d+))", std::regex::icase),
        std::regex(R"(VectorType::get\s*\([^,]+,\s*(\d+)\s*,\s*true\s*\))"),
    };
    for (size_t Index = 0; Index < MainLines.size(); ++Index) {
      for (const std::regex &Pattern : Patterns) {
        std::smatch Match;
        if (std::regex_search(MainLines[Index], Match, Pattern)) {
          BaseLanes = std::stoi(Match[1].str());
          Provenance.push_back(llvm::json::Object{
              {"line", static_cast<int>(Index + 1)},
              {"source", trim(MainLines[Index])},
              {"kind", "scalable-base-lanes"},
              {"base_lanes", BaseLanes},
          });
        }
      }
    }
    return llvm::json::Object{
        {"scalable", true},
        {"base_lanes", BaseLanes},
        {"vscale_values", llvm::json::Array{1, 2, 4}},
        {"scalable_provenance", std::move(Provenance)},
    };
  }

  llvm::json::Object laneSourceObject(const SlpFunctionSummary &Candidate,
                                      int Lanes) const {
    std::regex DeclPattern(R"(Scalars\s*\[\s*(\d+)\s*\])");
    for (size_t Index = 0; Index < MainLines.size(); ++Index) {
      std::smatch Match;
      if (std::regex_search(MainLines[Index], Match, DeclPattern)) {
        return llvm::json::Object{
            {"lanes", Lanes},
            {"line", static_cast<int>(Index + 1)},
            {"source", trim(MainLines[Index])},
            {"kind", "scalar-array-declaration"}};
      }
    }
    return llvm::json::Object{{"lanes", Lanes},
                              {"line", static_cast<int>(Candidate.StartLine)},
                              {"source", Candidate.Signature},
                              {"kind", "tree-entry-scalars"}};
  }

  llvm::json::Object globalLaneMapping(int Lanes) const {
    if (std::optional<std::vector<int>> Map = explicitLaneMapFromSource(
            MainSource, {"LaneMap", "ReorderMask", "ShuffleMask"})) {
      const std::vector<int> Identity = identityMap(Lanes);
      return makeLaneMapping((*Map == Identity)
                                 ? "identity"
                                 : "permutation",
                             Lanes, *Map, 1, "global lane map",
                             "explicit-lane-map");
    }
    return makeLaneMapping("identity", Lanes, identityMap(Lanes), 1,
                           "default identity lane map", "default-identity");
  }

  llvm::json::Object operandMapping(const SlpFunctionSummary &Emitter,
                                    llvm::StringRef Role,
                                    const llvm::json::Object &Fallback,
                                    int Lanes) const {
    const bool IsLHS = Role == "lhs";
    std::optional<std::vector<int>> Explicit =
        IsLHS ? explicitLaneMapFromSource(MainSource,
                                          {"LHSLaneMap", "LHSReorderMask",
                                           "LHSShuffleMask", "LeftLaneMap",
                                           "LeftReorderMask", "LeftShuffleMask"})
              : explicitLaneMapFromSource(MainSource,
                                          {"RHSLaneMap", "RHSReorderMask",
                                           "RHSShuffleMask", "RightLaneMap",
                                           "RightReorderMask",
                                           "RightShuffleMask"});
    llvm::json::Object Mapping = Explicit
                                     ? makeLaneMapping(
                                           validPermutation(*Explicit, Lanes)
                                               ? ((*Explicit ==
                                                   identityMap(Lanes))
                                                      ? "identity"
                                                      : "permutation")
                                               : "unsupported",
                                           Lanes, *Explicit, Emitter.StartLine,
                                           Emitter.Signature,
                                           (IsLHS ? "explicit-lhs-lane-map"
                                                  : "explicit-rhs-lane-map"))
                                     : cloneObject(Fallback);
    const int OperandIndex = IsLHS ? 0 : 1;
    std::smatch HelperMatch;
    std::regex HelperPattern((IsLHS ? R"(\bLHS\s*=\s*([A-Za-z_]\w*)\s*\()"
                                    : R"(\bRHS\s*=\s*([A-Za-z_]\w*)\s*\()"));
    if (std::regex_search(Emitter.Body, HelperMatch, HelperPattern) &&
        HelperMatch[1].str() != "packOperand") {
      Mapping["pack_source"] =
          llvm::json::Object{{"line", static_cast<int>(Emitter.StartLine)},
                             {"source", Emitter.Signature},
                             {"kind", "pack-helper-call"},
                             {"function", HelperMatch[1].str()},
                             {"operand_index", OperandIndex}};
      Mapping["pack_builder"] =
          llvm::json::Object{{"function", HelperMatch[1].str()},
                             {"line", static_cast<int>(Emitter.StartLine)},
                             {"source", Emitter.Signature},
                             {"kind", "helper-explicit-map"},
                             {"status", "complete"}};
    } else {
      Mapping["pack_source"] =
          llvm::json::Object{{"line", static_cast<int>(Emitter.StartLine)},
                             {"source", sourceLineForToken(Emitter.Lines,
                                                          {"packOperand"})},
                             {"kind", "direct-pack-operand"},
                             {"operand_index", OperandIndex}};
    }
    return Mapping;
  }

  llvm::json::Object resultMapping(const SlpFunctionSummary &Replacement,
                                   const llvm::json::Object &Fallback,
                                   int Lanes) const {
    if (std::optional<std::vector<int>> Explicit = explicitLaneMapFromSource(
            MainSource, {"ResultLaneMap", "ResultReorderMask",
                         "ReplacementLaneMap", "UseLaneMap"})) {
      llvm::json::Object Mapping = makeLaneMapping(
          validPermutation(*Explicit, Lanes)
              ? ((*Explicit == identityMap(Lanes)) ? "identity" : "permutation")
              : "unsupported",
          Lanes, *Explicit, Replacement.StartLine, Replacement.Signature,
          "result-explicit-lane-map");
      Mapping["replacement_source"] =
          llvm::json::Object{{"function", Replacement.Name},
                             {"line", static_cast<int>(Replacement.StartLine)},
                             {"source", Replacement.Signature},
                             {"kind", "result-helper-map"},
                             {"status", validateLaneMapping(Mapping, Lanes).empty()
                                            ? "complete"
                                            : "incomplete"}};
      return Mapping;
    }
    std::set<int> Replaced;
    std::regex ScalarsPattern(R"(Scalars\s*\[\s*(\d+)\s*\])");
    for (std::sregex_iterator It(Replacement.Body.begin(),
                                 Replacement.Body.end(), ScalarsPattern),
         End;
         It != End; ++It) {
      Replaced.insert(std::stoi((*It)[1].str()));
    }
    std::set<int> IdentitySet;
    for (int Lane = 0; Lane < Lanes; ++Lane) {
      IdentitySet.insert(Lane);
    }
    if (Replaced == IdentitySet) {
      llvm::json::Object Mapping =
          makeLaneMapping("identity", Lanes, identityMap(Lanes), Replacement.StartLine,
                          Replacement.Signature,
                          "direct-result-scalar-indexes");
      Mapping["replacement_source"] =
          llvm::json::Object{{"function", Replacement.Name},
                             {"line", static_cast<int>(Replacement.StartLine)},
                             {"source", Replacement.Signature},
                             {"kind", "direct-result-scalar-indexes"},
                             {"status", "complete"}};
      return Mapping;
    }
    const std::string ReplacementNameLower =
        llvm::StringRef(Replacement.Name).lower();
    if (llvm::StringRef(Replacement.Body).contains("replaceScalarUses") ||
        llvm::StringRef(Replacement.Body).contains("replaceExternalUses") ||
        Replacement.Name == "replaceScalarUses" ||
        Replacement.Name == "replaceExternalUses" ||
        ReplacementNameLower.find("commit") != std::string::npos ||
        ReplacementNameLower.find("externaluses") != std::string::npos) {
      llvm::json::Object Mapping = cloneObject(Fallback);
      if (!validateLaneMapping(Mapping, Lanes).empty()) {
        std::vector<int> FallbackMap = mapFromObject(Fallback);
        if (validPermutation(FallbackMap, Lanes)) {
          std::string Kind = stringField(Fallback, "kind");
          if (Kind.empty()) {
            Kind = FallbackMap == identityMap(Lanes) ? "identity" : "permutation";
          }
          Mapping = makeLaneMapping(Kind, Lanes, FallbackMap,
                                    Replacement.StartLine,
                                    Replacement.Signature,
                                    "default-result-lane-mapping");
        }
      }
      Mapping["source"] =
          llvm::json::Object{{"line", static_cast<int>(Replacement.StartLine)},
                             {"source", Replacement.Signature},
                             {"kind", "default-result-lane-mapping"}};
      Mapping["replacement_source"] =
          llvm::json::Object{{"function", Replacement.Name},
                             {"line", static_cast<int>(Replacement.StartLine)},
                             {"source", Replacement.Signature},
                             {"kind", "coarse-replacement-helper"},
                             {"status", "complete"}};
      return Mapping;
    }
    std::vector<int> Partial(Replaced.begin(), Replaced.end());
    llvm::json::Object Mapping = makeLaneMapping(
        "incomplete", Lanes, Partial, Replacement.StartLine, Replacement.Signature,
        "partial-result-scalar-indexes");
    Mapping["replacement_source"] =
        llvm::json::Object{{"function", Replacement.Name},
                           {"line", static_cast<int>(Replacement.StartLine)},
                           {"source", Replacement.Signature},
                           {"kind", "partial-result-scalar-indexes"},
                           {"status", "incomplete"}};
    return Mapping;
  }

  std::string graphOperandName(unsigned PackIndex) const {
    static const char *Names[] = {"a", "b", "c", "d", "e", "f"};
    if (PackIndex < (sizeof(Names) / sizeof(Names[0]))) {
      return Names[PackIndex];
    }
    return "p" + std::to_string(PackIndex);
  }

  unsigned lineForBodyText(const SlpFunctionSummary &Summary,
                           const std::string &Needle) const {
    for (size_t Index = 0; Index < Summary.Lines.size(); ++Index) {
      if (Summary.Lines[Index].find(Needle) != std::string::npos) {
        return Summary.StartLine + static_cast<unsigned>(Index);
      }
    }
    return Summary.StartLine;
  }

  llvm::json::Object graphPackMapping(const SlpFunctionSummary &Emitter,
                                      const llvm::json::Object &Fallback,
                                      unsigned PackIndex) const {
    llvm::json::Object Mapping = cloneObject(Fallback);
    Mapping["pack_source"] =
        llvm::json::Object{{"line", static_cast<int>(Emitter.StartLine)},
                           {"source", sourceLineForToken(Emitter.Lines,
                                                        {"packOperand"})},
                           {"kind", "direct-pack-operand"},
                           {"operand_index", static_cast<int>(PackIndex)}};
    return Mapping;
  }

  llvm::json::Array sourceAccessPathFacts(
      const llvm::json::Array &Facts, llvm::StringRef Function,
      llvm::StringRef Role, llvm::StringRef Base = "",
      llvm::StringRef SymbolContains = "",
      llvm::StringRef SourceContains = "") const {
    llvm::json::Array Result;
    std::set<std::string> Seen;
    for (const llvm::json::Value &Value : Facts) {
      const llvm::json::Object *Fact = Value.getAsObject();
      if (!Fact) {
        continue;
      }
      const std::string FactFunction = stringField(*Fact, "function");
      const std::string FactRole = stringField(*Fact, "role");
      const std::string FactBase = stringField(*Fact, "base");
      const std::string FactSymbol = stringField(*Fact, "symbol");
      const std::string FactSource = stringField(*Fact, "source");
      if (!Function.empty() && FactFunction != Function) {
        continue;
      }
      if (!Role.empty() && FactRole != Role) {
        continue;
      }
      if (!Base.empty() && FactBase != Base) {
        continue;
      }
      if (!SymbolContains.empty() &&
          FactSymbol.find(SymbolContains.str()) == std::string::npos) {
        continue;
      }
      if (!SourceContains.empty() &&
          FactSource.find(SourceContains.str()) == std::string::npos) {
        continue;
      }
      const std::string Key = FactFunction + ":" + FactRole + ":" +
                              FactSymbol + ":" +
                              std::to_string(intField(*Fact, "line"));
      if (Seen.insert(Key).second) {
        Result.push_back(cloneJson(Value));
      }
    }
    return Result;
  }

  void attachSourceAccessPaths(llvm::json::Object &Object,
                               llvm::json::Array Paths) const {
    if (!Paths.empty()) {
      Object["source_access_paths"] = std::move(Paths);
    }
  }

  void enrichOperandMappingsWithAccessPaths(
      llvm::json::Object &OperandMappings,
      const llvm::json::Array &AccessPathFacts) const {
    for (llvm::StringRef Name : {"lhs", "rhs"}) {
      llvm::json::Object *Mapping = OperandMappings.getObject(Name);
      if (!Mapping) {
        continue;
      }
      std::string Function;
      if (const llvm::json::Object *PackSource =
              Mapping->getObject("pack_source")) {
        Function = stringField(*PackSource, "function");
      }
      if (Function.empty()) {
        if (const llvm::json::Object *PackBuilder =
                Mapping->getObject("pack_builder")) {
          Function = stringField(*PackBuilder, "function");
        }
      }
      if (Function.empty()) {
        continue;
      }
      llvm::json::Array Paths =
          sourceAccessPathFacts(AccessPathFacts, Function, "use", "Entry",
                                "Scalars");
      if (Paths.empty()) {
        Paths = sourceAccessPathFacts(AccessPathFacts, Function, "use");
      }
      attachSourceAccessPaths(*Mapping, std::move(Paths));
    }
  }

  void enrichResultMappingWithAccessPaths(
      llvm::json::Object &ResultMapping, llvm::StringRef ReplacementFunction,
      const llvm::json::Array &AccessPathFacts) const {
    if (ReplacementFunction.empty()) {
      return;
    }
    llvm::json::Array Paths =
        sourceAccessPathFacts(AccessPathFacts, ReplacementFunction, "def");
    llvm::json::Array Uses =
        sourceAccessPathFacts(AccessPathFacts, ReplacementFunction, "use");
    for (llvm::json::Value &Use : Uses) {
      Paths.push_back(std::move(Use));
    }
    attachSourceAccessPaths(ResultMapping, std::move(Paths));
  }

  void enrichTransactionGraphWithAccessPaths(
      llvm::json::Object &TransactionGraph,
      const llvm::json::Array &AccessPathFacts) const {
    if (llvm::json::Array *Operands = TransactionGraph.getArray("operands")) {
      for (llvm::json::Value &Value : *Operands) {
        llvm::json::Object *Operand = Value.getAsObject();
        if (!Operand || stringField(*Operand, "kind") != "memory-pack") {
          continue;
        }
        const std::string Base = stringField(*Operand, "base");
        if (Base.empty()) {
          continue;
        }
        llvm::json::Array Paths =
            sourceAccessPathFacts(AccessPathFacts, "", "use", Base);
        if (Paths.empty()) {
          if (const llvm::json::Object *Mapping =
                  Operand->getObject("mapping")) {
            if (const llvm::json::Object *PackBuilder =
                    Mapping->getObject("pack_builder")) {
              const std::string Function = stringField(*PackBuilder, "function");
              if (!Function.empty()) {
                Paths = sourceAccessPathFacts(AccessPathFacts, Function, "use",
                                              "", "[");
              }
            }
          }
        }
        attachSourceAccessPaths(*Operand, std::move(Paths));
      }
    }
    if (llvm::json::Array *StoreSinks =
            TransactionGraph.getArray("store_sinks")) {
      for (llvm::json::Value &Value : *StoreSinks) {
        llvm::json::Object *Sink = Value.getAsObject();
        if (!Sink) {
          continue;
        }
        const std::string Base = stringField(*Sink, "base");
        if (Base.empty()) {
          continue;
        }
        llvm::json::Array Paths =
            sourceAccessPathFacts(AccessPathFacts, "", "def", Base);
        llvm::json::Array Uses =
            sourceAccessPathFacts(AccessPathFacts, "", "use", Base);
        for (llvm::json::Value &Use : Uses) {
          Paths.push_back(std::move(Use));
        }
        attachSourceAccessPaths(*Sink, std::move(Paths));
      }
    }
  }

  llvm::json::Object transactionGraph(const SlpFunctionSummary &Emitter,
                                      const llvm::json::Object &LaneMapping,
                                      const llvm::json::Object &ResultMapping,
                                      int Lanes) const {
    struct PackBinding {
      std::string Name;
      unsigned Index = 0;
      bool IsMemory = false;
      std::string Base;
      std::vector<int> AddressOrder;
      llvm::json::Array AddressTerms;
      unsigned ElementBits = 32;
      std::string MemoryContract;
      bool IsMasked = false;
      std::string MaskOperand;
      std::vector<int> MaskOrder;
      llvm::json::Array MaskConditions;
      std::string PassthruOperand;
      std::vector<int> PassthruOrder;
      std::string PassthruKind;
      std::vector<std::string> PassthruSymbols;
      std::string MaskedLanePolicy;
      bool HasAddressStride = false;
      int AddressStride = 0;
      std::string MemorySafetyStatus = "complete";
      std::string MemorySafetyReason;
      std::string MaskFailureDetail;
      std::string MaskFailureSource;
      std::string MaskFailureTemp;
      std::string MaskFailureRole;
      std::string Source;
    };
    struct MemoryPackInfo {
      std::vector<int> Offsets;
      llvm::json::Array AddressTerms;
      bool IsMasked = false;
      std::string MaskOperand;
      std::vector<int> MaskOrder;
      llvm::json::Array MaskConditions;
      std::string PassthruOperand;
      std::vector<int> PassthruOrder;
      std::string PassthruKind;
      std::vector<std::string> PassthruSymbols;
      std::string MaskedLanePolicy;
      std::string Status = "complete";
      std::string Reason;
      std::string MaskFailureDetail;
      std::string MaskFailureSource;
      std::string MaskFailureTemp;
      std::string MaskFailureRole = "memory-pack";
      std::string EffectWindow = "helper-local-load-pack";
    };
    struct StoreSinkBinding {
      std::string Base;
      std::vector<int> AddressOrder;
      llvm::json::Array StoreAddressTerms;
      std::string StoreContract;
      bool IsMasked = false;
      std::string MaskOperand;
      std::vector<int> MaskOrder;
      llvm::json::Array MaskConditions;
      std::string MaskedLanePolicy;
      bool HasAddressStride = false;
      int AddressStride = 0;
      std::string SafetyStatus = "complete";
      std::string SafetyReason;
      std::string MaskFailureDetail;
      std::string MaskFailureSource;
      std::string MaskFailureTemp;
      std::string MaskFailureRole = "memory-store";
      std::string Source;
    };
    struct ConstBinding {
      unsigned long long Value = 0;
      unsigned Bits = 0;
      std::string Source;
    };
    struct NodeBinding {
      std::string Id;
      std::string Temp;
      std::string Opcode;
      std::string Arg0;
      std::string Arg1;
      std::string Arg2;
      std::string Kind = "binop";
      std::string Predicate;
      std::vector<int> Mask;
      unsigned Bits = 0;
      int Index = -1;
      std::string Source;
    };
    struct CmpBinding {
      std::string Opcode;
      std::string Predicate;
      std::string Arg0;
      std::string Arg1;
      std::string Source;
    };
    std::string HelperSliceAbsentReason;
    llvm::json::Array HelperSliceAbsentDiagnostics;
    std::string TransactionGraphAbsentReason;
    llvm::json::Array TransactionGraphAbsentDiagnostics;
    auto HelperStackArray = [](const std::vector<std::string> &Stack) {
      llvm::json::Array Result;
      for (const std::string &Name : Stack) {
        Result.push_back(Name);
      }
      return Result;
    };
    auto AddHelperSliceDiagnostic =
        [&](const std::string &Reason, const std::string &Helper,
            const std::string &Role, const std::string &Source,
            const std::vector<std::string> &Stack, unsigned Depth) {
          if (Reason.empty()) {
            return;
          }
          llvm::json::Object Diagnostic{
              {"reason", Reason},
              {"helper", Helper},
              {"role", Role},
              {"source", trim(Source)},
              {"expansion_stack", HelperStackArray(Stack)},
              {"depth", static_cast<int64_t>(Depth)}};
          HelperSliceAbsentDiagnostics.push_back(std::move(Diagnostic));
        };
    auto RecordHelperSliceFailure =
        [&](const std::string &Role, const std::string &Source,
            const auto &Failure) {
          if (HelperSliceAbsentReason.empty()) {
            HelperSliceAbsentReason = Failure.Reason;
          }
          AddHelperSliceDiagnostic(Failure.Reason, Failure.Helper, Role,
                                   Source.empty() ? Failure.Source : Source,
                                   Failure.Stack, Failure.Depth);
        };
    auto EmptyGraph = [&]() {
      if (!HelperSliceAbsentReason.empty()) {
        llvm::json::Object Result{
            {"__helper_slice_absent_reason", HelperSliceAbsentReason}};
        if (!HelperSliceAbsentDiagnostics.empty()) {
          llvm::json::Array Diagnostics;
          for (const llvm::json::Value &Diagnostic :
               HelperSliceAbsentDiagnostics) {
            Diagnostics.push_back(cloneJson(Diagnostic));
          }
          Result["__helper_slice_absent_diagnostics"] =
              std::move(Diagnostics);
        }
        return Result;
      }
      if (!TransactionGraphAbsentReason.empty() ||
          !TransactionGraphAbsentDiagnostics.empty()) {
        llvm::json::Object Result;
        if (!TransactionGraphAbsentReason.empty()) {
          Result["__transaction_graph_absent_reason"] =
              TransactionGraphAbsentReason;
        }
        if (!TransactionGraphAbsentDiagnostics.empty()) {
          llvm::json::Array Diagnostics;
          for (const llvm::json::Value &Diagnostic :
               TransactionGraphAbsentDiagnostics) {
            Diagnostics.push_back(cloneJson(Diagnostic));
          }
          Result["__transaction_graph_absent_diagnostics"] =
              std::move(Diagnostics);
        }
        return Result;
      }
      return llvm::json::Object{};
    };
    auto RecordTransactionGraphAbsentDiagnostic =
        [&](const std::string &Reason, const std::string &Role,
            const std::string &Source, const std::string &Detail,
            const std::string &Temp) {
          if (Reason.empty()) {
            return;
          }
          if (TransactionGraphAbsentReason.empty()) {
            TransactionGraphAbsentReason = Reason;
          }
          llvm::json::Object Diagnostic{
              {"reason", Reason},
              {"role", Role.empty() ? "memory-pack" : Role},
              {"source", trim(Source)}};
          if (!Detail.empty()) {
            Diagnostic["detail"] = Detail;
          }
          if (!Temp.empty()) {
            Diagnostic["temp"] = Temp;
          }
          TransactionGraphAbsentDiagnostics.push_back(std::move(Diagnostic));
        };
    auto RecordMemoryPackMaskFailure = [&](const MemoryPackInfo &Info,
                                           const std::string &FallbackSource) {
      if (Info.Reason.empty()) {
        return;
      }
      RecordTransactionGraphAbsentDiagnostic(
          Info.Reason,
          Info.MaskFailureRole.empty() ? "memory-pack" : Info.MaskFailureRole,
          Info.MaskFailureSource.empty() ? FallbackSource
                                         : Info.MaskFailureSource,
          Info.MaskFailureDetail, Info.MaskFailureTemp);
    };
    auto RecordStoreMaskFailure = [&](const StoreSinkBinding &Sink,
                                      const std::string &FallbackSource) {
      if (Sink.SafetyReason.empty()) {
        return;
      }
      RecordTransactionGraphAbsentDiagnostic(
          Sink.SafetyReason,
          Sink.MaskFailureRole.empty() ? "memory-store" : Sink.MaskFailureRole,
          Sink.MaskFailureSource.empty() ? FallbackSource
                                         : Sink.MaskFailureSource,
          Sink.MaskFailureDetail, Sink.MaskFailureTemp);
    };

    auto MinMaxOpcodeForPredicate =
        [](const std::string &Predicate) -> std::string {
      if (Predicate == "SLT") {
        return "smin";
      }
      if (Predicate == "SGT") {
        return "smax";
      }
      if (Predicate == "ULT") {
        return "umin";
      }
      if (Predicate == "UGT") {
        return "umax";
      }
      return "";
    };

    auto IcmpPredicateName = [](const std::string &Predicate) -> std::string {
      std::string Result = llvm::StringRef(Predicate).lower();
      if (llvm::StringRef(Result).starts_with("icmp_")) {
        Result = Result.substr(5);
      }
      return Result;
    };

    auto OpcodeName = [](const std::string &CreateSuffix) -> std::string {
      return llvm::StringRef(CreateSuffix).lower();
    };

    std::map<std::string, unsigned> TypeBitsByName;
    auto AddTypeAliasBits = [&](const std::string &Text) {
      std::smatch Match;
      std::regex IntTyAlias(
          R"cv((?:Type|IntegerType|auto)\s*\*?\s*([A-Za-z_]\w*)\s*=\s*Type::getInt(\d+)Ty\s*\()cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), IntTyAlias), End;
           It != End; ++It) {
        TypeBitsByName[(*It)[1].str()] =
            static_cast<unsigned>(std::stoul((*It)[2].str()));
      }
      std::regex IntegerTypeAlias(
          R"cv((?:Type|IntegerType|auto)\s*\*?\s*([A-Za-z_]\w*)\s*=\s*IntegerType::get\s*\([^,]+,\s*(\d+)\s*\))cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), IntegerTypeAlias),
           End;
           It != End; ++It) {
        TypeBitsByName[(*It)[1].str()] =
            static_cast<unsigned>(std::stoul((*It)[2].str()));
      }
    };
    AddTypeAliasBits(Emitter.Body);
    for (const std::string &Line : Emitter.Lines) {
      AddTypeAliasBits(Line);
    }

    auto CastBitsForTarget =
        [&](const std::string &TargetText) -> std::optional<unsigned> {
      std::smatch Match;
      std::regex InlineIntTy(R"cv(Type::getInt(\d+)Ty\s*\()cv");
      if (std::regex_search(TargetText, Match, InlineIntTy)) {
        return static_cast<unsigned>(std::stoul(Match[1].str()));
      }
      std::regex InlineIntegerType(
          R"cv(IntegerType::get\s*\([^,]+,\s*(\d+)\s*\))cv");
      if (std::regex_search(TargetText, Match, InlineIntegerType)) {
        return static_cast<unsigned>(std::stoul(Match[1].str()));
      }
      std::regex BareIRType(R"cv(\bi(\d+)\b)cv");
      if (std::regex_search(TargetText, Match, BareIRType)) {
        return static_cast<unsigned>(std::stoul(Match[1].str()));
      }
      const std::string Name = trim(TargetText);
      const auto It = TypeBitsByName.find(Name);
      if (It != TypeBitsByName.end()) {
        return It->second;
      }
      return std::nullopt;
    };

    auto SplitTopLevelArgs = [](const std::string &Text)
        -> std::vector<std::string> {
      std::vector<std::string> Args;
      int Depth = 0;
      size_t Start = 0;
      for (size_t Index = 0; Index < Text.size(); ++Index) {
        const char C = Text[Index];
        if (C == '(' || C == '<' || C == '[' || C == '{') {
          ++Depth;
        } else if (C == ')' || C == '>' || C == ']' || C == '}') {
          if (Depth > 0) {
            --Depth;
          }
        } else if (C == ',' && Depth == 0) {
          Args.push_back(trim(Text.substr(Start, Index - Start)));
          Start = Index + 1;
        }
      }
      Args.push_back(trim(Text.substr(Start)));
      return Args;
    };

    auto ParseUnsignedLiteral =
        [](const std::string &Text) -> std::optional<unsigned long long> {
      std::string Value = trim(Text);
      if (Value.empty() || Value[0] == '-') {
        return std::nullopt;
      }
      while (!Value.empty() &&
             (Value.back() == 'u' || Value.back() == 'U' ||
              Value.back() == 'l' || Value.back() == 'L')) {
        Value.pop_back();
      }
      if (Value.empty()) {
        return std::nullopt;
      }
      try {
        size_t Parsed = 0;
        unsigned long long Result = std::stoull(Value, &Parsed, 0);
        if (Parsed != Value.size()) {
          return std::nullopt;
        }
        return Result;
      } catch (...) {
        return std::nullopt;
      }
    };

    auto ConstForText =
        [&](const std::string &Text) -> std::optional<ConstBinding> {
      std::smatch Match;
      std::regex ConstantInt(
          R"cv(ConstantInt::get\s*\(\s*(.+)\s*,\s*([^,\)]+)(?:,\s*(?:true|false))?\s*\))cv");
      if (std::regex_search(Text, Match, ConstantInt)) {
        std::optional<unsigned> Bits = CastBitsForTarget(Match[1].str());
        std::optional<unsigned long long> Value =
            ParseUnsignedLiteral(Match[2].str());
        if (!Bits || !Value || *Bits >= 63 || *Value >= (1ULL << *Bits)) {
          return std::nullopt;
        }
        return ConstBinding{*Value, *Bits, trim(Text)};
      }
      std::regex NullValue(
          R"cv(Constant::getNullValue\s*\(\s*(.+)\s*\))cv");
      if (std::regex_search(Text, Match, NullValue)) {
        if (std::optional<unsigned> Bits = CastBitsForTarget(Match[1].str())) {
          return ConstBinding{0, *Bits, trim(Text)};
        }
      }
      std::regex AllOnes(
          R"cv(Constant::getAllOnesValue\s*\(\s*(.+)\s*\))cv");
      if (std::regex_search(Text, Match, AllOnes)) {
        if (std::optional<unsigned> Bits = CastBitsForTarget(Match[1].str())) {
          if (*Bits >= 63) {
            return std::nullopt;
          }
          return ConstBinding{(1ULL << *Bits) - 1, *Bits, trim(Text)};
        }
      }
      return std::nullopt;
    };

    std::map<std::string, long long> IntConstantsByName =
        parseStaticIntConstants(MainSource);
    for (const auto &Item : parseStaticIntConstants(Emitter.Body)) {
      IntConstantsByName[Item.first] = Item.second;
    }
    auto PackIndexForText = [&](const std::string &Text,
                                const std::map<std::string, long long> &Constants)
        -> std::optional<unsigned> {
      if (std::optional<int> Index = evalLaneIndexExpr(Text, Constants)) {
        return static_cast<unsigned>(*Index);
      }
      return std::nullopt;
    };
    auto HelperPackIndex = [&](const std::string &FunctionName)
        -> std::optional<unsigned> {
      const SlpFunctionSummary *Summary = summaryByName(FunctionName);
      if (!Summary) {
        return std::nullopt;
      }
      std::map<std::string, long long> HelperConstants = IntConstantsByName;
      for (const auto &Item : parseStaticIntConstants(Summary->Body)) {
        HelperConstants[Item.first] = Item.second;
      }
      std::regex DirectPack(
          R"((?:buildPack|packOperand)\s*\(([^;]+?)\))");
      for (std::sregex_iterator It(Summary->Body.begin(), Summary->Body.end(),
                                   DirectPack),
           End;
           It != End; ++It) {
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[1].str());
        if (Args.size() >= 2 && trim(Args[0]) == "Entry") {
          if (std::optional<unsigned> Index =
                  PackIndexForText(Args[1], HelperConstants)) {
            return Index;
          }
        }
      }
      std::regex CollectCall(
          R"(collectOperand\s*\(([^;]+?)\))");
      for (std::sregex_iterator It(Summary->Body.begin(), Summary->Body.end(),
                                   CollectCall),
           End;
           It != End; ++It) {
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[1].str());
        if (Args.size() >= 2 && trim(Args[0]) == "Entry") {
          if (std::optional<unsigned> Index =
                  PackIndexForText(Args[1], HelperConstants)) {
            return Index;
          }
        }
      }
      return std::nullopt;
    };

    std::map<std::string, PackBinding> PacksByTemp;
    std::map<std::string, ConstBinding> ConstsByTemp;
    std::map<std::string, std::vector<int>> MasksByName;
    std::vector<std::string> PackOrder;
    auto AddPackBinding = [&](const std::string &Temp, unsigned PackIndex) {
      PackBinding Binding;
      Binding.Name = graphOperandName(PackIndex);
      Binding.Index = PackIndex;
      PacksByTemp[Temp] = std::move(Binding);
      PackOrder.push_back(Temp);
    };
    auto AddMemoryPackBinding = [&](const std::string &Temp,
                                    const std::string &Base,
                                    const MemoryPackInfo &MemoryInfo,
                                    const std::string &Source) {
      std::set<std::string> UsedNames;
      for (const auto &Item : PacksByTemp) {
        UsedNames.insert(Item.second.Name);
      }
      unsigned PackIndex = 0;
      while (UsedNames.count(graphOperandName(PackIndex)) != 0) {
        ++PackIndex;
      }
      PackBinding Binding;
      Binding.Name = graphOperandName(PackIndex);
      Binding.Index = PackIndex;
      Binding.IsMemory = true;
      Binding.Base = trim(Base);
      Binding.AddressOrder = MemoryInfo.Offsets;
      for (const llvm::json::Value &Term : MemoryInfo.AddressTerms) {
        Binding.AddressTerms.push_back(cloneJson(Term));
      }
      Binding.IsMasked = MemoryInfo.IsMasked;
      Binding.MaskOperand = MemoryInfo.MaskOperand;
      Binding.MaskOrder = MemoryInfo.MaskOrder;
      for (const llvm::json::Value &Condition : MemoryInfo.MaskConditions) {
        Binding.MaskConditions.push_back(cloneJson(Condition));
      }
      Binding.PassthruOperand = MemoryInfo.PassthruOperand;
      Binding.PassthruOrder = MemoryInfo.PassthruOrder;
      Binding.PassthruKind = MemoryInfo.PassthruKind;
      Binding.PassthruSymbols = MemoryInfo.PassthruSymbols;
      Binding.MaskedLanePolicy = MemoryInfo.MaskedLanePolicy;
      Binding.MemorySafetyStatus = MemoryInfo.Status;
      Binding.MemorySafetyReason = MemoryInfo.Reason;
      Binding.MaskFailureDetail = MemoryInfo.MaskFailureDetail;
      Binding.MaskFailureSource = MemoryInfo.MaskFailureSource;
      Binding.MaskFailureTemp = MemoryInfo.MaskFailureTemp;
      Binding.MaskFailureRole = MemoryInfo.MaskFailureRole;
      Binding.MemoryContract = "contiguous-load-pack-v1";
      if (static_cast<int>(Binding.AddressOrder.size()) == Lanes) {
        bool HasSymbolicAddress = false;
        for (int Offset : Binding.AddressOrder) {
          if (Offset < 0) {
            HasSymbolicAddress = true;
            break;
          }
        }
        bool IsContiguous = true;
        for (int Lane = 0; Lane < Lanes; ++Lane) {
          if (Binding.AddressOrder[Lane] != Lane) {
            IsContiguous = false;
            break;
          }
        }
        if (HasSymbolicAddress) {
          Binding.MemoryContract =
              Binding.IsMasked ? "masked-symbolic-gather-pack-v1"
                               : "symbolic-gather-pack-v1";
        } else if (Binding.IsMasked) {
          Binding.MemoryContract =
              IsContiguous ? "masked-contiguous-load-pack-v1"
                           : "masked-static-gather-pack-v1";
        } else {
          Binding.MemoryContract =
              IsContiguous ? "contiguous-load-pack-v1" : "static-gather-pack-v1";
        }
        if (!HasSymbolicAddress && Binding.AddressOrder.size() >= 2) {
          const int Stride = Binding.AddressOrder[1] - Binding.AddressOrder[0];
          bool HasStride = true;
          for (size_t Index = 2; Index < Binding.AddressOrder.size(); ++Index) {
            if (Binding.AddressOrder[Index] - Binding.AddressOrder[Index - 1] != Stride) {
              HasStride = false;
              break;
            }
          }
          Binding.HasAddressStride = HasStride;
          Binding.AddressStride = Stride;
        }
      }
      Binding.Source = Source;
      PacksByTemp[Temp] = std::move(Binding);
      PackOrder.push_back(Temp);
    };
    std::regex PackPattern(
        R"(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*(?:packOperand|buildPack)\s*\(([^;]+)\)\s*;)");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 PackPattern),
         End;
         It != End; ++It) {
      const std::string Temp = (*It)[1].str();
      std::vector<std::string> Args = SplitTopLevelArgs((*It)[2].str());
      if (Args.size() >= 2 && trim(Args[0]) == "Entry") {
        if (std::optional<unsigned> PackIndex =
                PackIndexForText(Args[1], IntConstantsByName)) {
          AddPackBinding(Temp, *PackIndex);
        }
      }
    }
    std::regex HelperPackPattern(
        R"(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(\s*Entry\s*\)\s*;)");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 HelperPackPattern),
         End;
         It != End; ++It) {
      const std::string Temp = (*It)[1].str();
      const std::string Helper = (*It)[2].str();
      if (PacksByTemp.count(Temp) != 0) {
        continue;
      }
      if (std::optional<unsigned> PackIndex = HelperPackIndex(Helper)) {
        AddPackBinding(Temp, *PackIndex);
      }
    }
    auto SkipWhitespaceAndComments = [&](size_t Index) {
      while (Index < MainSource.size()) {
        if (std::isspace(static_cast<unsigned char>(MainSource[Index]))) {
          ++Index;
          continue;
        }
        if (Index + 1 < MainSource.size() && MainSource[Index] == '/' &&
            MainSource[Index + 1] == '/') {
          Index += 2;
          while (Index < MainSource.size() && MainSource[Index] != '\n') {
            ++Index;
          }
          continue;
        }
        if (Index + 1 < MainSource.size() && MainSource[Index] == '/' &&
            MainSource[Index + 1] == '*') {
          Index += 2;
          while (Index + 1 < MainSource.size() &&
                 !(MainSource[Index] == '*' && MainSource[Index + 1] == '/')) {
            ++Index;
          }
          if (Index + 1 < MainSource.size()) {
            Index += 2;
          }
          continue;
        }
        break;
      }
      return Index;
    };
    auto MatchingDelimiter = [&](size_t Open, char Left, char Right)
        -> std::optional<size_t> {
      if (Open >= MainSource.size() || MainSource[Open] != Left) {
        return std::nullopt;
      }
      int Depth = 0;
      bool InLineComment = false;
      bool InBlockComment = false;
      bool InString = false;
      bool InChar = false;
      bool Escaped = false;
      for (size_t Index = Open; Index < MainSource.size(); ++Index) {
        const char C = MainSource[Index];
        const char Next =
            Index + 1 < MainSource.size() ? MainSource[Index + 1] : '\0';
        if (InLineComment) {
          if (C == '\n') {
            InLineComment = false;
          }
          continue;
        }
        if (InBlockComment) {
          if (C == '*' && Next == '/') {
            InBlockComment = false;
            ++Index;
          }
          continue;
        }
        if (InString || InChar) {
          if (Escaped) {
            Escaped = false;
            continue;
          }
          if (C == '\\') {
            Escaped = true;
            continue;
          }
          if (InString && C == '"') {
            InString = false;
          } else if (InChar && C == '\'') {
            InChar = false;
          }
          continue;
        }
        if (C == '/' && Next == '/') {
          InLineComment = true;
          ++Index;
          continue;
        }
        if (C == '/' && Next == '*') {
          InBlockComment = true;
          ++Index;
          continue;
        }
        if (C == '"') {
          InString = true;
          continue;
        }
        if (C == '\'') {
          InChar = true;
          continue;
        }
        if (C == Left) {
          ++Depth;
        } else if (C == Right) {
          --Depth;
          if (Depth == 0) {
            return Index;
          }
        }
      }
      return std::nullopt;
    };
    auto IsFunctionNameChar = [](char C) {
      return std::isalnum(static_cast<unsigned char>(C)) || C == '_' || C == ':';
    };
    auto UnqualifiedFunctionName = [](const std::string &Name) {
      const size_t Pos = Name.rfind("::");
      return Pos == std::string::npos ? Name : Name.substr(Pos + 2);
    };
    struct IndexedHelperFunction {
      std::string QualifiedName;
      std::string UnqualifiedName;
      std::string Signature;
      std::string Body;
    };
    std::vector<IndexedHelperFunction> HelperFunctionIndex;
    std::map<std::string, std::vector<size_t>> HelperFunctionsByQualifiedName;
    std::map<std::string, std::vector<size_t>> HelperFunctionsByUnqualifiedName;
    std::regex PotentialFunctionOpen(R"cv(\()cv");
    for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                 PotentialFunctionOpen),
         End;
         It != End; ++It) {
      const size_t OpenParen = static_cast<size_t>(It->position());
      size_t NameEnd = OpenParen;
      while (NameEnd > 0 &&
             std::isspace(static_cast<unsigned char>(MainSource[NameEnd - 1]))) {
        --NameEnd;
      }
      size_t NameBegin = NameEnd;
      while (NameBegin > 0 && IsFunctionNameChar(MainSource[NameBegin - 1])) {
        --NameBegin;
      }
      if (NameBegin == NameEnd) {
        continue;
      }
      std::string QualifiedName =
          trim(MainSource.substr(NameBegin, NameEnd - NameBegin));
      std::string UnqualifiedName = UnqualifiedFunctionName(QualifiedName);
      if (UnqualifiedName == "if" || UnqualifiedName == "for" ||
          UnqualifiedName == "while" || UnqualifiedName == "switch" ||
          UnqualifiedName == "return" || UnqualifiedName == "sizeof") {
        continue;
      }
      std::optional<size_t> CloseParen =
          MatchingDelimiter(OpenParen, '(', ')');
      if (!CloseParen) {
        continue;
      }
      const size_t OpenBrace = SkipWhitespaceAndComments(*CloseParen + 1);
      if (OpenBrace >= MainSource.size() || MainSource[OpenBrace] != '{') {
        continue;
      }
      std::optional<size_t> CloseBrace =
          MatchingDelimiter(OpenBrace, '{', '}');
      if (!CloseBrace || *CloseBrace <= OpenBrace) {
        continue;
      }
      size_t SignatureBegin = NameBegin;
      while (SignatureBegin > 0 && MainSource[SignatureBegin - 1] != '\n' &&
             MainSource[SignatureBegin - 1] != ';' &&
             MainSource[SignatureBegin - 1] != '{' &&
             MainSource[SignatureBegin - 1] != '}') {
        --SignatureBegin;
      }
      IndexedHelperFunction Function{
          QualifiedName,
          UnqualifiedName,
          trim(MainSource.substr(SignatureBegin,
                                 *CloseParen - SignatureBegin + 1)),
          MainSource.substr(OpenBrace + 1, *CloseBrace - OpenBrace - 1)};
      const size_t Index = HelperFunctionIndex.size();
      HelperFunctionIndex.push_back(std::move(Function));
      HelperFunctionsByQualifiedName[QualifiedName].push_back(Index);
      HelperFunctionsByUnqualifiedName[UnqualifiedName].push_back(Index);
    }
    auto HelperFunctionForName =
        [&](const std::string &FunctionName) -> const IndexedHelperFunction * {
      auto Qualified = HelperFunctionsByQualifiedName.find(FunctionName);
      if (Qualified != HelperFunctionsByQualifiedName.end() &&
          Qualified->second.size() == 1) {
        return &HelperFunctionIndex[Qualified->second.front()];
      }
      auto Unqualified =
          HelperFunctionsByUnqualifiedName.find(UnqualifiedFunctionName(FunctionName));
      if (Unqualified != HelperFunctionsByUnqualifiedName.end() &&
          Unqualified->second.size() == 1) {
        return &HelperFunctionIndex[Unqualified->second.front()];
      }
      return nullptr;
    };
    auto HelperNameIsAmbiguous = [&](const std::string &FunctionName) {
      auto Qualified = HelperFunctionsByQualifiedName.find(FunctionName);
      if (Qualified != HelperFunctionsByQualifiedName.end() &&
          Qualified->second.size() > 1) {
        return true;
      }
      auto Unqualified =
          HelperFunctionsByUnqualifiedName.find(UnqualifiedFunctionName(FunctionName));
      return Unqualified != HelperFunctionsByUnqualifiedName.end() &&
             Unqualified->second.size() > 1;
    };
    auto HelperBodyForName = [&](const std::string &FunctionName)
        -> std::optional<std::string> {
      if (const IndexedHelperFunction *Function =
              HelperFunctionForName(FunctionName)) {
        return Function->Body;
      }
      if (HelperNameIsAmbiguous(FunctionName)) {
        return std::nullopt;
      }
      if (const SlpFunctionSummary *Summary = summaryByName(FunctionName)) {
        return Summary->Body;
      }
      return std::nullopt;
    };
    auto IsContiguousMemoryOffsets = [&](const std::vector<int> &Offsets) {
      if (static_cast<int>(Offsets.size()) != Lanes) {
        return false;
      }
      for (int Lane = 0; Lane < Lanes; ++Lane) {
        if (Offsets[Lane] != Lane) {
          return false;
        }
      }
      return true;
    };
    auto IsUniqueStaticMemoryOffsets = [&](const std::vector<int> &Offsets) {
      if (static_cast<int>(Offsets.size()) != Lanes) {
        return false;
      }
      std::set<int> Seen;
      for (int Offset : Offsets) {
        if (Offset < 0 || !Seen.insert(Offset).second) {
          return false;
        }
      }
      return true;
    };
    auto HasCompleteSymbolicAddressTerms = [&](const MemoryPackInfo &Info) {
      if (static_cast<int>(Info.Offsets.size()) != Lanes ||
          static_cast<int>(Info.AddressTerms.size()) != Lanes) {
        return false;
      }
      bool HasSymbolic = false;
      std::set<int> SeenLanes;
      for (const llvm::json::Value &Value : Info.AddressTerms) {
        const auto *Term = Value.getAsObject();
        if (!Term) {
          return false;
        }
        const std::optional<int64_t> Lane = Term->getInteger("lane");
        if (!Lane || *Lane < 0 || *Lane >= Lanes ||
            !SeenLanes.insert(static_cast<int>(*Lane)).second) {
          return false;
        }
        const std::string Kind = stringField(*Term, "kind");
        if (Kind == "symbolic") {
          HasSymbolic = true;
        } else if (Kind != "static") {
          return false;
        }
      }
      return HasSymbolic;
    };
    struct HelperParameter {
      std::string Name;
      std::optional<std::string> DefaultExpr;
    };
    auto HelperParameters = [&](const std::string &FunctionName) {
      std::vector<HelperParameter> Params;
      std::string Signature;
      if (const IndexedHelperFunction *Function =
              HelperFunctionForName(FunctionName)) {
        Signature = Function->Signature;
      } else if (HelperNameIsAmbiguous(FunctionName)) {
        return Params;
      } else if (const SlpFunctionSummary *Summary = summaryByName(FunctionName)) {
        Signature = Summary->Signature;
      }
      if (Signature.find('(') == std::string::npos ||
          Signature.rfind(')') == std::string::npos) {
        size_t SearchFrom = 0;
        while (SearchFrom < MainSource.size()) {
          const size_t NamePos = MainSource.find(FunctionName, SearchFrom);
          if (NamePos == std::string::npos) {
            break;
          }
          SearchFrom = NamePos + FunctionName.size();
          const bool HasLeftIdent =
              NamePos > 0 &&
              (std::isalnum(static_cast<unsigned char>(MainSource[NamePos - 1])) ||
               MainSource[NamePos - 1] == '_');
          const bool HasRightIdent =
              SearchFrom < MainSource.size() &&
              (std::isalnum(static_cast<unsigned char>(MainSource[SearchFrom])) ||
               MainSource[SearchFrom] == '_');
          if (HasLeftIdent || HasRightIdent) {
            continue;
          }
          size_t Open = SearchFrom;
          while (Open < MainSource.size() &&
                 std::isspace(static_cast<unsigned char>(MainSource[Open]))) {
            ++Open;
          }
          if (Open >= MainSource.size() || MainSource[Open] != '(') {
            continue;
          }
          int Depth = 0;
          for (size_t Index = Open; Index < MainSource.size(); ++Index) {
            if (MainSource[Index] == '(') {
              ++Depth;
            } else if (MainSource[Index] == ')') {
              --Depth;
              if (Depth == 0) {
                Signature = MainSource.substr(NamePos, Index - NamePos + 1);
                break;
              }
            }
          }
          if (!Signature.empty()) {
            break;
          }
        }
      }
      const size_t Open = Signature.find('(');
      const size_t Close = Signature.rfind(')');
      if (Open == std::string::npos || Close == std::string::npos ||
          Close <= Open) {
        return Params;
      }
      for (std::string Param :
           SplitTopLevelArgs(Signature.substr(Open + 1, Close - Open - 1))) {
        Param = trim(std::move(Param));
        if (Param.empty()) {
          continue;
        }
        std::optional<std::string> DefaultExpr;
        size_t DefaultPos = std::string::npos;
        int Depth = 0;
        for (size_t Index = 0; Index < Param.size(); ++Index) {
          const char C = Param[Index];
          if (C == '(' || C == '<' || C == '[' || C == '{') {
            ++Depth;
          } else if (C == ')' || C == '>' || C == ']' || C == '}') {
            if (Depth > 0) {
              --Depth;
            }
          } else if (C == '=' && Depth == 0) {
            DefaultPos = Index;
            break;
          }
        }
        std::string ParamDecl = Param;
        if (DefaultPos != std::string::npos) {
          ParamDecl = trim(Param.substr(0, DefaultPos));
          DefaultExpr = trim(Param.substr(DefaultPos + 1));
        }
        std::smatch Match;
        std::regex NamePattern(R"cv(([A-Za-z_]\w*)\s*$)cv");
        if (std::regex_search(ParamDecl, Match, NamePattern)) {
          Params.push_back(HelperParameter{Match[1].str(), DefaultExpr});
        }
      }
      return Params;
    };
    auto SubstituteHelperArgs = [&](std::string Text,
                                    const std::vector<std::string> &Formals,
                                    const std::vector<std::string> &Actuals) {
      for (size_t Index = 0; Index < Formals.size() && Index < Actuals.size();
           ++Index) {
        const std::string Pattern =
            "\\b" + Formals[Index] + "\\b";
        Text = std::regex_replace(Text, std::regex(Pattern), Actuals[Index]);
      }
      return Text;
    };
    struct HelperExpansionResult {
      std::optional<std::string> Body;
      std::string Reason;
      std::string Helper;
      std::string Source;
      std::vector<std::string> Stack;
      unsigned Depth = 0;
    };
    struct ReturnExprInfo {
      std::optional<std::string> Expr;
      std::optional<std::string> Condition;
      std::optional<std::string> TrueExpr;
      std::optional<std::string> FalseExpr;
      unsigned Count = 0;
    };
    auto AnalyzeReturnExpr = [&](const std::string &Text) -> ReturnExprInfo {
      std::smatch Conditional;
      std::regex SimpleConditionalReturn(
          R"cv(^\s*if\s*\(([^;{}]+)\)\s*return\s+([^;]+)\s*;\s*return\s+([^;]+)\s*;\s*$)cv");
      if (std::regex_match(Text, Conditional, SimpleConditionalReturn)) {
        return ReturnExprInfo{std::nullopt, trim(Conditional[1].str()),
                              trim(Conditional[2].str()),
                              trim(Conditional[3].str()), 2};
      }
      std::vector<std::string> Returns;
      std::regex ReturnPattern(R"cv(return\s+([^;]+)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), ReturnPattern),
           End;
           It != End; ++It) {
        Returns.push_back(trim((*It)[1].str()));
      }
      if (Returns.size() != 1) {
        return ReturnExprInfo{std::nullopt, std::nullopt, std::nullopt,
                              std::nullopt,
                              static_cast<unsigned>(Returns.size())};
      }
      return ReturnExprInfo{Returns.front(), std::nullopt, std::nullopt,
                            std::nullopt, 1};
    };
    auto MaterializeReturnAsAssignment =
        [&](const ReturnExprInfo &ReturnExpr, const std::string &Temp,
            const std::string &Body) -> std::optional<std::string> {
      if (ReturnExpr.Expr) {
        return std::regex_replace(
            Body, std::regex(R"cv(return\s+([^;]+)\s*;)cv"),
            "Value *" + Temp + " = " + *ReturnExpr.Expr + ";");
      }
      if (!ReturnExpr.Condition || !ReturnExpr.TrueExpr ||
          !ReturnExpr.FalseExpr) {
        return std::nullopt;
      }
      std::string SelectCondition = *ReturnExpr.Condition;
      std::string ConditionAssignment;
      if (std::regex_match(SelectCondition, std::regex(R"cv([A-Za-z_]\w*)cv"))) {
        SelectCondition = trim(SelectCondition);
      } else if (std::regex_match(
                     SelectCondition,
                     std::regex(
                         R"cv([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*\s*\([^;{}]*\))cv"))) {
        SelectCondition = Temp + "_cond";
        ConditionAssignment = "Value *" + SelectCondition + " = " +
                              *ReturnExpr.Condition + ";\n";
      } else {
        return std::nullopt;
      }
      std::smatch BuilderMatch;
      std::regex BuilderPattern(R"cv(\b([A-Za-z_]\w*)\s*(\.|->)\s*Create)cv");
      std::string Builder = "Builder";
      std::string Access = ".";
      if (std::regex_search(*ReturnExpr.TrueExpr, BuilderMatch,
                            BuilderPattern) ||
          std::regex_search(*ReturnExpr.FalseExpr, BuilderMatch,
                            BuilderPattern)) {
        Builder = BuilderMatch[1].str();
        Access = BuilderMatch[2].str();
      }
      const std::string TrueTemp = Temp + "_true";
      const std::string FalseTemp = Temp + "_false";
      return ConditionAssignment +
             "Value *" + TrueTemp + " = " + *ReturnExpr.TrueExpr + ";\n" +
             "Value *" + FalseTemp + " = " + *ReturnExpr.FalseExpr + ";\n" +
             "Value *" + Temp + " = " + Builder + Access +
             "CreateSelect(" + SelectCondition + ", " + TrueTemp +
             ", " + FalseTemp + ");";
    };
    auto IsBuiltinHelperCall = [&](const std::string &Name) {
      const std::string Unqualified = UnqualifiedFunctionName(Name);
      return Name == "ConstantInt" ||
             llvm::StringRef(Name).starts_with("ConstantInt::") ||
             llvm::StringRef(Name).starts_with("Constant::") ||
             llvm::StringRef(Name).starts_with("Type::") ||
             llvm::StringRef(Name).starts_with("IntegerType::") ||
             Unqualified == "CreateICmp" || Unqualified == "CreateAnd" ||
             Unqualified == "CreateOr" || Unqualified == "CreateNot" ||
             Unqualified == "CreateSelect" || Unqualified == "buildPack" ||
             Unqualified == "packOperand";
    };
    auto HasStaticLaneIndexedArg = [&](const std::vector<std::string> &Args) {
      std::regex LaneArgPattern(R"cv(\b[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\])cv");
      for (const std::string &Arg : Args) {
        std::smatch Match;
        if (std::regex_search(Arg, Match, LaneArgPattern) &&
            evalLaneIndexExpr(trim(Match[1].str()), IntConstantsByName)) {
          return true;
        }
      }
      return false;
    };
    std::function<HelperExpansionResult(
        const std::string &, const std::vector<std::string> &, unsigned,
        std::vector<std::string>)>
        ExpandHelperBody;
    const unsigned MaxHelperExpansionDepth = 6;
    auto HelperFailure =
        [](const std::string &Reason, const std::string &Helper,
           const std::string &Source, const std::vector<std::string> &Stack,
           unsigned Depth) {
          HelperExpansionResult Result;
          Result.Reason = Reason;
          Result.Helper = Helper;
          Result.Source = trim(Source);
          Result.Stack = Stack;
          Result.Depth = Depth;
          return Result;
        };
    auto HelperExpansionCacheKey =
        [](const std::string &FunctionName,
           const std::vector<std::string> &Actuals) {
          std::string Key = FunctionName;
          for (const std::string &Actual : Actuals) {
            Key.append("\n");
            Key.append(trim(Actual));
          }
          return Key;
        };
    std::map<std::string, HelperExpansionResult> HelperExpansionCache;
    ExpandHelperBody =
        [&](const std::string &FunctionName,
            const std::vector<std::string> &Actuals, unsigned Depth,
            std::vector<std::string> Stack) -> HelperExpansionResult {
      if (std::find(Stack.begin(), Stack.end(), FunctionName) != Stack.end()) {
        std::vector<std::string> FailureStack = Stack;
        FailureStack.push_back(FunctionName);
        return HelperFailure("unsupported-recursive-helper-slice",
                             FunctionName, FunctionName + "(...)", FailureStack,
                             Depth);
      }
      const std::string CacheKey = HelperExpansionCacheKey(FunctionName, Actuals);
      const auto Cached = HelperExpansionCache.find(CacheKey);
      if (Cached != HelperExpansionCache.end()) {
        return Cached->second;
      }
      std::optional<std::string> MaybeBody = HelperBodyForName(FunctionName);
      if (!MaybeBody) {
        return HelperFailure("unsupported-unresolved-helper-slice",
                             FunctionName, FunctionName + "(...)", Stack,
                             Depth);
      }
      std::vector<HelperParameter> Params = HelperParameters(FunctionName);
      if (Params.empty() || Actuals.size() > Params.size()) {
        return HelperFailure("unsupported-incomplete-helper-arguments",
                             FunctionName, FunctionName + "(...)", Stack,
                             Depth);
      }
      std::vector<std::string> Formals;
      std::vector<std::string> BoundActuals = Actuals;
      for (size_t Index = Actuals.size(); Index < Params.size(); ++Index) {
        if (!Params[Index].DefaultExpr) {
          return HelperFailure("unsupported-incomplete-helper-arguments",
                               FunctionName, FunctionName + "(...)", Stack,
                               Depth);
        }
        BoundActuals.push_back(*Params[Index].DefaultExpr);
      }
      for (const HelperParameter &Param : Params) {
        Formals.push_back(Param.Name);
      }
      Stack.push_back(FunctionName);
      std::string Body = SubstituteHelperArgs(*MaybeBody, Formals, BoundActuals);
      if (Depth >= MaxHelperExpansionDepth) {
        ReturnExprInfo ReturnExpr = AnalyzeReturnExpr(Body);
        if (!ReturnExpr.Expr) {
          return HelperFailure("unsupported-helper-expansion-depth",
                               FunctionName, FunctionName + "(...)", Stack,
                               Depth);
        }
        std::smatch ReturnCall;
        std::regex ReturnHelperCall(
            R"cv(^\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\((.*)\)\s*$)cv");
        if (std::regex_match(*ReturnExpr.Expr, ReturnCall,
                             ReturnHelperCall) &&
            !IsBuiltinHelperCall(ReturnCall[1].str())) {
          const std::string Helper = ReturnCall[1].str();
          std::vector<std::string> Args =
              SplitTopLevelArgs(ReturnCall[2].str());
          std::optional<std::string> NestedBody = HelperBodyForName(Helper);
          std::vector<HelperParameter> NestedParams = HelperParameters(Helper);
          if (NestedBody && !NestedParams.empty() &&
              Args.size() <= NestedParams.size()) {
            std::vector<std::string> NestedFormals;
            std::vector<std::string> NestedActuals = Args;
            bool CompleteArgs = true;
            for (size_t Index = Args.size(); Index < NestedParams.size();
                 ++Index) {
              if (!NestedParams[Index].DefaultExpr) {
                CompleteArgs = false;
                break;
              }
              NestedActuals.push_back(*NestedParams[Index].DefaultExpr);
            }
            for (const HelperParameter &Param : NestedParams) {
              NestedFormals.push_back(Param.Name);
            }
            if (CompleteArgs) {
              std::string Summarized =
                  SubstituteHelperArgs(*NestedBody, NestedFormals,
                                       NestedActuals);
              ReturnExprInfo NestedReturn = AnalyzeReturnExpr(Summarized);
              std::smatch NestedReturnCall;
              if (NestedReturn.Expr &&
                  (!std::regex_match(*NestedReturn.Expr, NestedReturnCall,
                                     ReturnHelperCall) ||
                   IsBuiltinHelperCall(NestedReturnCall[1].str()))) {
                HelperExpansionResult Result;
                Result.Body = Summarized;
                HelperExpansionCache[CacheKey] = Result;
                return Result;
              }
            }
          }
          return HelperFailure("unsupported-helper-expansion-depth",
                               FunctionName, FunctionName + "(...)", Stack,
                               Depth);
        }
        HelperExpansionResult Result;
        Result.Body = Body;
        HelperExpansionCache[CacheKey] = Result;
        return Result;
      }
      std::regex HelperAssignment(
          R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(([^;]+)\)\s*;)cv");
      std::string Expanded;
      size_t Last = 0;
      for (std::sregex_iterator It(Body.begin(), Body.end(), HelperAssignment),
           End;
           It != End; ++It) {
        const std::string Temp = (*It)[1].str();
        const std::string Helper = (*It)[2].str();
        if (IsBuiltinHelperCall(Helper)) {
          continue;
        }
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
        HelperExpansionResult Nested =
            ExpandHelperBody(Helper, Args, Depth + 1, Stack);
        if (!Nested.Body) {
          if (Nested.Reason == "unsupported-unresolved-helper-slice" &&
              !HelperNameIsAmbiguous(Helper) &&
              HasStaticLaneIndexedArg(Args)) {
            Expanded.append(Body.substr(Last, It->position() - Last));
            Expanded.append(It->str());
            Last = It->position() + It->length();
            continue;
          }
          return Nested;
        }
        ReturnExprInfo ReturnExpr = AnalyzeReturnExpr(*Nested.Body);
        if (!ReturnExpr.Expr) {
          std::optional<std::string> Materialized =
              MaterializeReturnAsAssignment(ReturnExpr, Temp, *Nested.Body);
          if (!Materialized) {
            return HelperFailure(
                ReturnExpr.Count > 1 ? "unsupported-multiple-return-helper-slice"
                                     : "unsupported-unresolved-helper-slice",
                Helper, It->str(), Stack, Depth + 1);
          }
          Expanded.append(Body.substr(Last, It->position() - Last));
          Expanded.append(*Materialized);
          Last = It->position() + It->length();
          continue;
        }
        Expanded.append(Body.substr(Last, It->position() - Last));
        Expanded.append(*MaterializeReturnAsAssignment(ReturnExpr, Temp,
                                                       *Nested.Body));
        Last = It->position() + It->length();
      }
      Expanded.append(Body.substr(Last));
      Body = std::move(Expanded);
      ReturnExprInfo ReturnExpr = AnalyzeReturnExpr(Body);
      if (ReturnExpr.Expr) {
        std::smatch ReturnCall;
        std::regex ReturnHelperCall(
            R"cv(^\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\((.*)\)\s*$)cv");
        if (std::regex_match(*ReturnExpr.Expr, ReturnCall, ReturnHelperCall)) {
          std::string Helper = ReturnCall[1].str();
          if (!IsBuiltinHelperCall(Helper)) {
            std::vector<std::string> Args =
                SplitTopLevelArgs(ReturnCall[2].str());
            HelperExpansionResult Nested =
                ExpandHelperBody(Helper, Args, Depth + 1, Stack);
            if (!Nested.Body) {
              return Nested;
            }
            return Nested;
          }
        }
      } else if (ReturnExpr.Count > 1 && !ReturnExpr.Condition) {
        return HelperFailure("unsupported-multiple-return-helper-slice",
                             FunctionName, FunctionName + "(...)", Stack,
                             Depth);
      }
      HelperExpansionResult Result;
      Result.Body = Body;
      HelperExpansionCache[CacheKey] = Result;
      return Result;
    };
    auto ExpandHelperReturnAsAssignment =
        [&](const std::string &Temp, const std::string &Helper,
            const std::vector<std::string> &Args, unsigned Depth)
        -> HelperExpansionResult {
      HelperExpansionResult Body = ExpandHelperBody(Helper, Args, Depth, {});
      if (!Body.Body) {
        return Body;
      }
      ReturnExprInfo ReturnExpr = AnalyzeReturnExpr(*Body.Body);
      std::optional<std::string> Materialized =
          MaterializeReturnAsAssignment(ReturnExpr, Temp, *Body.Body);
      if (!Materialized) {
        return HelperFailure(
            ReturnExpr.Count > 1 ? "unsupported-multiple-return-helper-slice"
                                 : "unsupported-unresolved-helper-slice",
            Helper, Helper + "(...)", {}, Depth);
      }
      HelperExpansionResult Result;
      Result.Body = *Materialized;
      return Result;
    };
    std::function<std::map<std::string, llvm::json::Object>(
        const std::string &, unsigned)>
        ParseMaskConditionsImpl;
    ParseMaskConditionsImpl = [&](const std::string &Text, unsigned Depth) {
      std::map<std::string, llvm::json::Object> Conditions;
      const std::string MaskDecl =
          R"cv((?:(?:Value|auto)\s*\*\s*(?:const\s*)?)([A-Za-z_]\w*))cv";
      const std::string MaskAssign =
          R"cv((?:(?:(?:Value|auto)\s*\*\s*(?:const\s*)?)?)([A-Za-z_]\w*))cv";
      const std::string BuilderAccess =
          R"cv([A-Za-z_]\w*\s*(?:\.|->)\s*)cv";
      std::set<std::string> InvalidTemps;
      auto RecordCondition = [&](const std::string &Temp,
                                 llvm::json::Object Condition) {
        if (InvalidTemps.count(Temp) != 0) {
          return;
        }
        if (Conditions.count(Temp) != 0) {
          return;
        }
        Condition["temp"] = Temp;
        Conditions[Temp] = std::move(Condition);
      };
      std::regex IcmpPattern(
          MaskAssign + R"cv(\s*=\s*)cv" + BuilderAccess +
          R"cv(CreateICmp\s*\(\s*(?:(?:Instruction|CmpInst)::)?ICMP_(EQ|NE|SLT|SLE|SGT|SGE|ULT|ULE|UGT|UGE)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), IcmpPattern), End;
           It != End; ++It) {
        const std::string Temp = (*It)[1].str();
        const std::string Predicate = IcmpPredicateName((*It)[2].str());
        if (Predicate.empty()) {
          continue;
        }
        RecordCondition(Temp, llvm::json::Object{
                                  {"predicate", Predicate},
                                  {"lhs", trim((*It)[3].str())},
                                  {"rhs", trim((*It)[4].str())},
                                  {"source", trim(It->str())}});
      }
      auto AddConstantMask = [&](const std::string &Temp, bool Value,
                                 const std::string &Source) {
        RecordCondition(Temp, llvm::json::Object{
                                  {"op", "const"},
                                  {"value", Value},
                                  {"source", trim(Source)}});
      };
      std::regex BuilderConstantPattern(
          MaskAssign + R"cv(\s*=\s*)cv" + BuilderAccess +
          R"cv(get(True|False)\s*\(\s*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(),
                                   BuilderConstantPattern),
           End;
           It != End; ++It) {
        AddConstantMask((*It)[1].str(), (*It)[2].str() == "True", It->str());
      }
      std::regex ConstantIntNamedPattern(
          MaskAssign +
          R"cv(\s*=\s*ConstantInt::get(True|False)\s*\([^;]*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(),
                                   ConstantIntNamedPattern),
           End;
           It != End; ++It) {
        AddConstantMask((*It)[1].str(), (*It)[2].str() == "True", It->str());
      }
      std::regex ConstantIntBoolPattern(
          MaskAssign +
          R"cv(\s*=\s*ConstantInt::get\s*\([^,]+,\s*(true|false)\s*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(),
                                   ConstantIntBoolPattern),
           End;
           It != End; ++It) {
          AddConstantMask((*It)[1].str(), (*It)[2].str() == "true", It->str());
      }
      std::vector<std::smatch> AliasMatches;
      std::regex AliasPattern(MaskAssign +
                              R"cv(\s*=\s*([A-Za-z_]\w*)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), AliasPattern),
           End;
           It != End; ++It) {
        AliasMatches.push_back(*It);
      }
      std::vector<std::smatch> BooleanMatches;
      std::regex BooleanPattern(
          MaskAssign + R"cv(\s*=\s*)cv" + BuilderAccess +
          R"cv(Create(And|Or)\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), BooleanPattern),
           End;
           It != End; ++It) {
        BooleanMatches.push_back(*It);
      }
      std::vector<std::smatch> NotMatches;
      std::regex NotPattern(
          MaskAssign + R"cv(\s*=\s*)cv" + BuilderAccess +
          R"cv(CreateNot\s*\(\s*([A-Za-z_]\w*)\s*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), NotPattern),
           End;
           It != End; ++It) {
        NotMatches.push_back(*It);
      }
      std::vector<std::smatch> XorMatches;
      std::regex XorPattern(
          MaskAssign + R"cv(\s*=\s*)cv" + BuilderAccess +
          R"cv(CreateXor\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), XorPattern),
           End;
           It != End; ++It) {
        XorMatches.push_back(*It);
      }
      std::vector<std::smatch> SelectMatches;
      std::regex SelectPattern(
          MaskAssign + R"cv(\s*=\s*)cv" + BuilderAccess +
          R"cv(CreateSelect\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\)\s*;)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), SelectPattern),
           End;
           It != End; ++It) {
        SelectMatches.push_back(*It);
      }
      std::vector<std::smatch> BranchAssignmentMatches;
      std::set<std::string> BranchAssignedTemps;
      std::regex BranchAssignmentPattern(
          R"cv(if\s*\(\s*([^;){}]+?)\s*\)\s*(?:\{\s*)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;\s*(?:\}\s*)?else\s*(?:\{\s*)?\2\s*=\s*([A-Za-z_]\w*)\s*;\s*(?:\}\s*)?)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(),
                                   BranchAssignmentPattern),
           End;
           It != End; ++It) {
        BranchAssignmentMatches.push_back(*It);
        BranchAssignedTemps.insert((*It)[2].str());
      }
      struct BranchMaskAssignment {
        std::string Temp;
        std::string Source;
        std::string Cond;
        std::string LeafExpr;
        std::unique_ptr<BranchMaskAssignment> TrueValue;
        std::unique_ptr<BranchMaskAssignment> FalseValue;
      };
      auto SkipSpaces = [](const std::string &Input, size_t Pos) {
        while (Pos < Input.size() &&
               std::isspace(static_cast<unsigned char>(Input[Pos]))) {
          ++Pos;
        }
        return Pos;
      };
      auto MatchingDelimiter =
          [](const std::string &Input, size_t OpenPos, char Open,
             char Close) -> std::optional<size_t> {
        if (OpenPos >= Input.size() || Input[OpenPos] != Open) {
          return std::nullopt;
        }
        int Depth = 0;
        for (size_t Pos = OpenPos; Pos < Input.size(); ++Pos) {
          if (Input[Pos] == Open) {
            ++Depth;
          } else if (Input[Pos] == Close) {
            --Depth;
            if (Depth == 0) {
              return Pos;
            }
          }
        }
        return std::nullopt;
      };
      std::function<std::optional<BranchMaskAssignment>(const std::string &)>
          ParseBranchMaskAssignmentBlock;
      ParseBranchMaskAssignmentBlock =
          [&](const std::string &Input) -> std::optional<BranchMaskAssignment> {
        std::string Block = trim(Input);
        if (!Block.empty() && Block.front() == '{') {
          if (std::optional<size_t> End =
                  MatchingDelimiter(Block, 0, '{', '}')) {
            if (SkipSpaces(Block, *End + 1) == Block.size()) {
              Block = trim(Block.substr(1, *End - 1));
            }
          }
        }
        std::smatch AssignMatch;
        std::regex DirectAssignPattern(
            R"cv(^([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*(?:\s*\[[^\]]+\])?)\s*;\s*$)cv");
        if (std::regex_match(Block, AssignMatch, DirectAssignPattern)) {
          BranchMaskAssignment Assignment;
          Assignment.Temp = AssignMatch[1].str();
          Assignment.LeafExpr = trim(AssignMatch[2].str());
          Assignment.Source = Block;
          return Assignment;
        }
        size_t Pos = SkipSpaces(Block, 0);
        if (Block.compare(Pos, 2, "if") != 0) {
          return std::nullopt;
        }
        Pos = SkipSpaces(Block, Pos + 2);
        if (Pos >= Block.size() || Block[Pos] != '(') {
          return std::nullopt;
        }
        std::optional<size_t> CondEnd =
            MatchingDelimiter(Block, Pos, '(', ')');
        if (!CondEnd) {
          return std::nullopt;
        }
        std::string Cond = trim(Block.substr(Pos + 1, *CondEnd - Pos - 1));
        Pos = SkipSpaces(Block, *CondEnd + 1);
        auto ReadStatementOrBlock =
            [&](size_t Start) -> std::optional<std::pair<std::string, size_t>> {
          Start = SkipSpaces(Block, Start);
          if (Start >= Block.size()) {
            return std::nullopt;
          }
          if (Block[Start] == '{') {
            std::optional<size_t> End =
                MatchingDelimiter(Block, Start, '{', '}');
            if (!End) {
              return std::nullopt;
            }
            return std::make_pair(Block.substr(Start + 1, *End - Start - 1),
                                  *End + 1);
          }
          size_t Semi = Block.find(';', Start);
          if (Semi == std::string::npos) {
            return std::nullopt;
          }
          return std::make_pair(Block.substr(Start, Semi - Start + 1),
                                Semi + 1);
        };
        std::optional<std::pair<std::string, size_t>> TrueBlock =
            ReadStatementOrBlock(Pos);
        if (!TrueBlock) {
          return std::nullopt;
        }
        Pos = SkipSpaces(Block, TrueBlock->second);
        if (Block.compare(Pos, 4, "else") != 0) {
          return std::nullopt;
        }
        std::optional<std::pair<std::string, size_t>> FalseBlock =
            ReadStatementOrBlock(Pos + 4);
        if (!FalseBlock) {
          return std::nullopt;
        }
        size_t EndPos = SkipSpaces(Block, FalseBlock->second);
        if (EndPos != Block.size()) {
          return std::nullopt;
        }
        std::optional<BranchMaskAssignment> TrueAssignment =
            ParseBranchMaskAssignmentBlock(TrueBlock->first);
        std::optional<BranchMaskAssignment> FalseAssignment =
            ParseBranchMaskAssignmentBlock(FalseBlock->first);
        if (!TrueAssignment || !FalseAssignment ||
            TrueAssignment->Temp != FalseAssignment->Temp) {
          return std::nullopt;
        }
        BranchMaskAssignment Assignment;
        Assignment.Temp = TrueAssignment->Temp;
        Assignment.Source = Block;
        Assignment.Cond = Cond;
        Assignment.TrueValue =
            std::make_unique<BranchMaskAssignment>(std::move(*TrueAssignment));
        Assignment.FalseValue =
            std::make_unique<BranchMaskAssignment>(std::move(*FalseAssignment));
        return Assignment;
      };
      std::vector<BranchMaskAssignment> BranchAssignmentTrees;
      std::set<std::string> BranchAssignmentTreeSources;
      for (size_t Search = 0; Search < Text.size();) {
        size_t IfPos = Text.find("if", Search);
        if (IfPos == std::string::npos) {
          break;
        }
        const bool HasLeftBoundary =
            IfPos == 0 ||
            !std::isalnum(static_cast<unsigned char>(Text[IfPos - 1]));
        const bool HasRightBoundary =
            IfPos + 2 >= Text.size() ||
            !std::isalnum(static_cast<unsigned char>(Text[IfPos + 2]));
        if (!HasLeftBoundary || !HasRightBoundary) {
          Search = IfPos + 2;
          continue;
        }
        size_t Pos = SkipSpaces(Text, IfPos + 2);
        if (Pos >= Text.size() || Text[Pos] != '(') {
          Search = IfPos + 2;
          continue;
        }
        std::optional<size_t> CondEnd =
            MatchingDelimiter(Text, Pos, '(', ')');
        if (!CondEnd) {
          Search = IfPos + 2;
          continue;
        }
        auto ReadTopLevelStatementOrBlock =
            [&](size_t Start) -> std::optional<std::pair<std::string, size_t>> {
          Start = SkipSpaces(Text, Start);
          if (Start >= Text.size()) {
            return std::nullopt;
          }
          if (Text[Start] == '{') {
            std::optional<size_t> End =
                MatchingDelimiter(Text, Start, '{', '}');
            if (!End) {
              return std::nullopt;
            }
            return std::make_pair(Text.substr(Start, *End - Start + 1),
                                  *End + 1);
          }
          size_t Semi = Text.find(';', Start);
          if (Semi == std::string::npos) {
            return std::nullopt;
          }
          return std::make_pair(Text.substr(Start, Semi - Start + 1),
                                Semi + 1);
        };
        std::optional<std::pair<std::string, size_t>> TrueBlock =
            ReadTopLevelStatementOrBlock(*CondEnd + 1);
        if (!TrueBlock) {
          Search = IfPos + 2;
          continue;
        }
        size_t ElsePos = SkipSpaces(Text, TrueBlock->second);
        if (Text.compare(ElsePos, 4, "else") != 0) {
          Search = IfPos + 2;
          continue;
        }
        std::optional<std::pair<std::string, size_t>> FalseBlock =
            ReadTopLevelStatementOrBlock(ElsePos + 4);
        if (!FalseBlock) {
          Search = IfPos + 2;
          continue;
        }
        std::string Source = Text.substr(IfPos, FalseBlock->second - IfPos);
        if (std::optional<BranchMaskAssignment> Assignment =
                ParseBranchMaskAssignmentBlock(Source)) {
          if (BranchAssignmentTreeSources.insert(Assignment->Source).second) {
            BranchAssignedTemps.insert(Assignment->Temp);
            BranchAssignmentTrees.push_back(std::move(*Assignment));
          }
        }
        Search = IfPos + 2;
      }
      std::regex AnyIfAssignmentPattern(
          R"cv(if\s*\(\s*[^;){}]+?\s*\)\s*(?:\{\s*)?([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*;\s*(?:\}\s*)?)cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(),
                                   AnyIfAssignmentPattern),
           End;
           It != End; ++It) {
        const std::string Temp = (*It)[1].str();
        if (BranchAssignedTemps.count(Temp) == 0) {
          InvalidTemps.insert(Temp);
          Conditions.erase(Temp);
        }
      }
      auto LaneIndexForOpaqueMaskArgs =
          [&](const std::vector<std::string> &Args) -> std::optional<int> {
        std::optional<int> FoundLane;
        std::regex LaneArgPattern(R"cv(\b[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\])cv");
        for (const std::string &Arg : Args) {
          std::smatch Match;
          if (!std::regex_search(Arg, Match, LaneArgPattern)) {
            continue;
          }
          std::optional<int> Lane =
              evalLaneIndexExpr(trim(Match[1].str()), IntConstantsByName);
          if (!Lane) {
            return std::nullopt;
          }
          if (FoundLane && *FoundLane != *Lane) {
            return std::nullopt;
          }
          FoundLane = *Lane;
        }
        return FoundLane;
      };
      if (Depth < 2) {
        std::regex HelperCallPattern(
            MaskAssign +
            R"cv(\s*=\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(([^;]+)\)\s*;)cv");
        for (std::sregex_iterator It(Text.begin(), Text.end(),
                                     HelperCallPattern),
             End;
             It != End; ++It) {
          const std::string Temp = (*It)[1].str();
          const std::string Helper = (*It)[2].str();
          if (IsBuiltinHelperCall(Helper)) {
            continue;
          }
          std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
          HelperExpansionResult Body =
              ExpandHelperReturnAsAssignment(Temp, Helper, Args, Depth);
          if (!Body.Body) {
            if (Body.Reason == "unsupported-unresolved-helper-slice" &&
                !HelperNameIsAmbiguous(Helper)) {
              if (std::optional<int> Lane = LaneIndexForOpaqueMaskArgs(Args)) {
                RecordCondition(Temp, llvm::json::Object{
                                          {"op", "opaque-mask"},
                                          {"name", Temp},
                                          {"inferred_lane", *Lane},
                                          {"source", trim(It->str())}});
              }
              continue;
            }
            RecordHelperSliceFailure("mask-condition", It->str(), Body);
            continue;
          }
          std::map<std::string, llvm::json::Object> HelperConditions =
              ParseMaskConditionsImpl(*Body.Body, Depth + 1);
          const auto Found = HelperConditions.find(Temp);
          if (Found != HelperConditions.end()) {
            llvm::json::Object Condition = cloneObject(Found->second);
            Condition["source"] = trim(It->str());
            RecordCondition(Temp, std::move(Condition));
          }
        }
      }
      bool Progress = true;
      while (Progress) {
        Progress = false;
        auto ConditionForMaskExpr =
            [&](const std::string &Expr) -> std::optional<llvm::json::Object> {
          const std::string Text = trim(Expr);
          const auto Found = Conditions.find(Text);
          if (Found != Conditions.end()) {
            return cloneObject(Found->second);
          }
          std::smatch Indexed;
          std::regex IndexedMaskExpr(
              R"cv(^([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]$)cv");
          if (std::regex_match(Text, Indexed, IndexedMaskExpr)) {
            const std::string IndexText = trim(Indexed[2].str());
            if (!isSafeSymbolicMaskIndexText(IndexText, IntConstantsByName)) {
              return std::nullopt;
            }
            return llvm::json::Object{{"op", "indexed-mask"},
                                      {"name", Indexed[1].str()},
                                      {"index", IndexText},
                                      {"source", Text}};
          }
          return std::nullopt;
        };
        std::function<std::optional<llvm::json::Object>(
            const BranchMaskAssignment &)>
            ConditionForBranchAssignment =
                [&](const BranchMaskAssignment &Assignment)
            -> std::optional<llvm::json::Object> {
          if (!Assignment.LeafExpr.empty()) {
            return ConditionForMaskExpr(Assignment.LeafExpr);
          }
          if (!Assignment.TrueValue || !Assignment.FalseValue) {
            return std::nullopt;
          }
          std::optional<llvm::json::Object> Cond =
              ConditionForMaskExpr(Assignment.Cond);
          std::optional<llvm::json::Object> TrueValue =
              ConditionForBranchAssignment(*Assignment.TrueValue);
          std::optional<llvm::json::Object> FalseValue =
              ConditionForBranchAssignment(*Assignment.FalseValue);
          if (!Cond || !TrueValue || !FalseValue) {
            return std::nullopt;
          }
          return llvm::json::Object{
              {"op", "select"},
              {"args", llvm::json::Array{cloneJsonObject(*Cond),
                                          cloneJsonObject(*TrueValue),
                                          cloneJsonObject(*FalseValue)}},
              {"source", Assignment.Source}};
        };
        for (const std::smatch &Match : AliasMatches) {
          const std::string Temp = Match[1].str();
          if (Conditions.count(Temp) != 0 || InvalidTemps.count(Temp) != 0) {
            continue;
          }
          if (BranchAssignedTemps.count(Temp) != 0) {
            continue;
          }
          const std::string SourceTemp = trim(Match[2].str());
          if (InvalidTemps.count(SourceTemp) != 0) {
            InvalidTemps.insert(Temp);
            continue;
          }
          const auto Source = Conditions.find(SourceTemp);
          if (Source == Conditions.end()) {
            continue;
          }
          llvm::json::Object Condition = cloneObject(Source->second);
          Condition["source"] = trim(Match.str());
          RecordCondition(Temp, std::move(Condition));
          Progress = true;
        }
        for (const std::smatch &Match : BooleanMatches) {
          const std::string Temp = Match[1].str();
          if (Conditions.count(Temp) != 0 || InvalidTemps.count(Temp) != 0) {
            continue;
          }
          const auto Left = Conditions.find(trim(Match[3].str()));
          const auto Right = Conditions.find(trim(Match[4].str()));
          if (Left == Conditions.end() || Right == Conditions.end()) {
            continue;
          }
          std::string Op = llvm::StringRef(Match[2].str()).lower();
          RecordCondition(Temp, llvm::json::Object{
                                     {"op", Op},
                                     {"args",
                                      llvm::json::Array{
                                          cloneJsonObject(Left->second),
                                          cloneJsonObject(Right->second)}},
                                     {"source", trim(Match.str())}});
          Progress = true;
        }
        for (const std::smatch &Match : NotMatches) {
          const std::string Temp = Match[1].str();
          if (Conditions.count(Temp) != 0 || InvalidTemps.count(Temp) != 0) {
            continue;
          }
          const auto Arg = Conditions.find(trim(Match[2].str()));
          if (Arg == Conditions.end()) {
            continue;
          }
          RecordCondition(Temp, llvm::json::Object{
                                     {"op", "not"},
                                     {"args", llvm::json::Array{
                                                  cloneJsonObject(Arg->second)}},
                                     {"source", trim(Match.str())}});
          Progress = true;
        }
        for (const std::smatch &Match : XorMatches) {
          const std::string Temp = Match[1].str();
          if (Conditions.count(Temp) != 0 || InvalidTemps.count(Temp) != 0) {
            continue;
          }
          const auto Left = Conditions.find(trim(Match[2].str()));
          const auto Right = Conditions.find(trim(Match[3].str()));
          if (Left == Conditions.end() || Right == Conditions.end()) {
            continue;
          }
          const auto LeftConst = Left->second.getBoolean("value");
          const auto RightConst = Right->second.getBoolean("value");
          if (LeftConst && stringField(Left->second, "op") == "const") {
            llvm::json::Object Condition = cloneObject(Right->second);
            if (*LeftConst) {
              Condition = llvm::json::Object{
                  {"op", "not"},
                  {"args", llvm::json::Array{cloneJsonObject(Right->second)}},
                  {"source", trim(Match.str())}};
            } else {
              Condition["source"] = trim(Match.str());
            }
            RecordCondition(Temp, std::move(Condition));
            Progress = true;
            continue;
          }
          if (RightConst && stringField(Right->second, "op") == "const") {
            llvm::json::Object Condition = cloneObject(Left->second);
            if (*RightConst) {
              Condition = llvm::json::Object{
                  {"op", "not"},
                  {"args", llvm::json::Array{cloneJsonObject(Left->second)}},
                  {"source", trim(Match.str())}};
            } else {
              Condition["source"] = trim(Match.str());
            }
            RecordCondition(Temp, std::move(Condition));
            Progress = true;
          }
        }
        for (const std::smatch &Match : SelectMatches) {
          const std::string Temp = Match[1].str();
          if (Conditions.count(Temp) != 0 || InvalidTemps.count(Temp) != 0) {
            continue;
          }
          const auto Cond = Conditions.find(trim(Match[2].str()));
          const auto TrueValue = Conditions.find(trim(Match[3].str()));
          const auto FalseValue = Conditions.find(trim(Match[4].str()));
          if (Cond == Conditions.end() || TrueValue == Conditions.end() ||
              FalseValue == Conditions.end()) {
            continue;
          }
          RecordCondition(Temp, llvm::json::Object{
                                     {"op", "select"},
                                     {"args",
                                      llvm::json::Array{
                                          cloneJsonObject(Cond->second),
                                          cloneJsonObject(TrueValue->second),
                                          cloneJsonObject(FalseValue->second)}},
                                     {"source", trim(Match.str())}});
          Progress = true;
        }
        for (const BranchMaskAssignment &Assignment : BranchAssignmentTrees) {
          const std::string Temp = Assignment.Temp;
          if (Conditions.count(Temp) != 0 || InvalidTemps.count(Temp) != 0) {
            continue;
          }
          std::optional<llvm::json::Object> Condition =
              ConditionForBranchAssignment(Assignment);
          if (!Condition) {
            continue;
          }
          RecordCondition(Temp, std::move(*Condition));
          Progress = true;
        }
        for (const std::smatch &Match : BranchAssignmentMatches) {
          const std::string Temp = Match[2].str();
          if (Conditions.count(Temp) != 0 || InvalidTemps.count(Temp) != 0) {
            continue;
          }
          std::optional<llvm::json::Object> Cond =
              ConditionForMaskExpr(Match[1].str());
          std::optional<llvm::json::Object> TrueValue =
              ConditionForMaskExpr(Match[3].str());
          std::optional<llvm::json::Object> FalseValue =
              ConditionForMaskExpr(Match[4].str());
          if (!Cond || !TrueValue || !FalseValue) {
            continue;
          }
          RecordCondition(Temp, llvm::json::Object{
                                     {"op", "select"},
                                     {"args",
                                      llvm::json::Array{
                                          cloneJsonObject(*Cond),
                                          cloneJsonObject(*TrueValue),
                                          cloneJsonObject(*FalseValue)}},
                                     {"source", trim(Match.str())}});
          Progress = true;
        }
      }
      return Conditions;
    };
    auto ParseMaskConditions = [&](const std::string &Text) {
      return ParseMaskConditionsImpl(Text, 0);
    };
    auto AppendMaskCondition =
        [&](llvm::json::Array &Conditions,
            const std::map<std::string, llvm::json::Object> &ByTemp,
            const std::string &Temp, int Lane) {
          const auto Found = ByTemp.find(trim(Temp));
          if (Found == ByTemp.end()) {
            return false;
          }
          llvm::json::Object Condition;
          for (const auto &Item : Found->second) {
            Condition[Item.first] = cloneJson(Item.second);
          }
          Condition["lane"] = Lane;
          Conditions.push_back(std::move(Condition));
          return true;
        };
    auto NormalizeSymbolicMaskIndex = [&](const std::string &Index) {
      std::string Result = trim(Index);
      std::regex IdentifierPattern(R"cv(\b[A-Za-z_]\w*\b)cv");
      std::string Out;
      size_t Last = 0;
      for (std::sregex_iterator It(Result.begin(), Result.end(),
                                   IdentifierPattern),
           End;
           It != End; ++It) {
        const size_t Pos = static_cast<size_t>(It->position());
        Out.append(Result.substr(Last, Pos - Last));
        const std::string Name = It->str();
        const auto Found = IntConstantsByName.find(Name);
        if (Name != "Lane" && Found != IntConstantsByName.end()) {
          Out.append(std::to_string(Found->second));
        } else {
          Out.append(Name);
        }
        Last = Pos + It->length();
      }
      Out.append(Result.substr(Last));
      return trim(Out);
    };
    auto AppendIndexedMaskCondition =
        [&](llvm::json::Array &Conditions, const std::string &Name,
            const std::string &Index, int Lane, const std::string &Source) {
      if (!isSafeSymbolicMaskIndexText(Index, IntConstantsByName)) {
        return false;
      }
      const std::string NormalizedIndex = NormalizeSymbolicMaskIndex(Index);
      Conditions.push_back(llvm::json::Object{
          {"op", "indexed-mask"},
          {"name", Name},
          {"index", NormalizedIndex},
          {"lane", Lane},
          {"source", Source}});
      return true;
        };
    auto LaneIndexForText = [&](const std::string &Text) -> std::optional<int> {
      return evalLaneIndexExpr(Text, IntConstantsByName);
    };
    auto AppendSymbolicPassthru =
        [&](MemoryPackInfo &Info, const std::string &PackName, int Lane) {
          Info.PassthruKind = "symbolic-undef";
          Info.PassthruSymbols.push_back(PackName + "_undef" +
                                         std::to_string(Lane));
        };
    auto MemoryPackInfoForBody = [&](const std::string &Body)
        -> std::optional<MemoryPackInfo> {
      struct PassthruBinding {
        std::string Kind;
        std::string Operand;
        int Lane = -1;
        std::string Source;
      };
      MemoryPackInfo Info;
      std::map<std::string, llvm::json::Object> LocalMaskConditions =
          ParseMaskConditions(Body);
      std::set<std::string> Bases;
      bool SawMaskedLoad = false;
      std::set<std::string> MaskOperands;
      std::set<std::string> PassthruOperands;
      auto SetMaskFailure = [&](const std::string &Reason,
                                const std::string &Detail,
                                const std::string &Source,
                                const std::string &Temp) {
        if (Info.Reason.empty()) {
          Info.Status = "failed";
          Info.Reason = Reason;
        }
        if (Info.MaskFailureDetail.empty()) {
          Info.MaskFailureDetail = Detail;
          Info.MaskFailureSource = trim(Source);
          Info.MaskFailureTemp = Temp;
          Info.MaskFailureRole = "memory-pack";
        }
      };
      auto SetGatherIndexFailure = [&](const std::string &Source,
                                       const std::string &Base) {
        if (Info.Reason.empty()) {
          Info.Status = "failed";
          Info.Reason = "unsupported-variable-gather-index";
        }
        if (Info.MaskFailureDetail.empty()) {
          Info.MaskFailureDetail = "unsafe-gather-index";
          Info.MaskFailureSource = trim(Source);
          Info.MaskFailureTemp = trim(Base);
          Info.MaskFailureRole = "memory-pack";
        }
      };
      auto AppendGatherAddress = [&](const std::string &IndexText, int Lane,
                                     const std::string &Base,
                                     const std::string &Source) {
        const std::string TrimmedIndex = trim(IndexText);
        if (std::optional<int> Index = LaneIndexForText(TrimmedIndex)) {
          Info.Offsets.push_back(*Index);
          Info.AddressTerms.push_back(llvm::json::Object{
              {"kind", "static"},
              {"base", trim(Base)},
              {"index", *Index},
              {"lane", Lane},
              {"source", trim(Source)}});
          return true;
        }
        if (isSafeSymbolicMaskIndexText(TrimmedIndex, IntConstantsByName)) {
          const std::string NormalizedIndex =
              NormalizeSymbolicMaskIndex(TrimmedIndex);
          Info.Offsets.push_back(-1);
          Info.AddressTerms.push_back(llvm::json::Object{
              {"kind", "symbolic"},
              {"base", trim(Base)},
              {"index", NormalizedIndex},
              {"lane", Lane},
              {"source", trim(Source)}});
          return true;
        }
        SetGatherIndexFailure(Source, Base);
        return false;
      };
      auto ResolveMaskAliasTemp = [&](std::string Temp) {
        Temp = trim(std::move(Temp));
        std::map<std::string, std::string> AliasByTemp;
        std::regex PlainAlias(
            R"cv((?:(?:Value|auto)\s*\*\s*(?:const\s*)?)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;)cv");
        for (std::sregex_iterator It(Body.begin(), Body.end(), PlainAlias),
             End;
             It != End; ++It) {
          AliasByTemp.emplace((*It)[1].str(), (*It)[2].str());
        }
        std::set<std::string> Seen;
        while (!Temp.empty() && Seen.insert(Temp).second) {
          const auto Found = AliasByTemp.find(Temp);
          if (Found == AliasByTemp.end()) {
            break;
          }
          Temp = trim(Found->second);
        }
        return Temp;
      };
      auto IncompleteBranchDetailForTemp =
          [&](const std::string &Temp) -> std::string {
        const std::string DirectTemp = trim(Temp);
        if (DirectTemp.empty()) {
          return "unknown-mask-expression";
        }
        auto HasIncompleteBranchAssignment = [&](const std::string &Name) {
          std::regex BranchAssignment(
              R"cv(if\s*\(\s*[^;){}]+?\s*\)\s*(?:\{\s*)?)cv" + Name +
              R"cv(\s*=\s*[A-Za-z_]\w*\s*;\s*(?:\}\s*)?)cv");
          for (std::sregex_iterator It(Body.begin(), Body.end(),
                                       BranchAssignment),
               End;
               It != End; ++It) {
            std::string After = Body.substr(
                static_cast<size_t>(It->position() + It->length()));
            After = trim(After);
            if (!llvm::StringRef(After).starts_with("else")) {
              return true;
            }
          }
          return false;
        };
        if (HasIncompleteBranchAssignment(DirectTemp)) {
          return "incomplete-branch-assignment";
        }
        const std::string ResolvedTemp = ResolveMaskAliasTemp(DirectTemp);
        if (ResolvedTemp != DirectTemp &&
            HasIncompleteBranchAssignment(ResolvedTemp)) {
          return "incomplete-branch-assignment";
        }
        return "unknown-mask-expression";
      };
      std::map<std::string, PassthruBinding> LocalPassthruValues;
      auto RecordPassthruBinding = [&](const std::string &Temp,
                                       PassthruBinding Binding) {
        const std::string Key = trim(Temp);
        if (Key.empty() || LocalPassthruValues.count(Key) != 0) {
          return false;
        }
        Binding.Source = trim(Binding.Source);
        LocalPassthruValues[Key] = std::move(Binding);
        return true;
      };
      const std::string PtrDecl =
          R"cv((?:(?:Value|auto)\s*\*\s*(?:const\s*)?)?)cv";
      std::string PassthruBindingText = Body;
      if (Body.find("CreateMaskedLoad") != std::string::npos) {
        std::regex PassthruHelperCallPattern(
            PtrDecl +
            R"cv(([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(([^;]+)\)\s*;)cv");
        for (std::sregex_iterator It(Body.begin(), Body.end(),
                                     PassthruHelperCallPattern),
             End;
             It != End; ++It) {
          const std::string Temp = (*It)[1].str();
          const std::string Helper = (*It)[2].str();
          if (IsBuiltinHelperCall(Helper)) {
            continue;
          }
          std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
          HelperExpansionResult Expanded =
              ExpandHelperReturnAsAssignment(Temp, Helper, Args, 0);
          if (!Expanded.Body) {
            continue;
          }
          PassthruBindingText.append("\n");
          PassthruBindingText.append(*Expanded.Body);
        }
      }
      std::regex PassthruArrayAliasPattern(
          PtrDecl +
          R"cv(([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*;)cv");
      for (std::sregex_iterator It(PassthruBindingText.begin(),
                                   PassthruBindingText.end(),
                                   PassthruArrayAliasPattern),
           End;
           It != End; ++It) {
        if (std::optional<int> Lane = LaneIndexForText((*It)[3].str())) {
          RecordPassthruBinding(
              (*It)[1].str(),
              PassthruBinding{"array", (*It)[2].str(), *Lane, It->str()});
        }
      }
      std::regex PassthruUndefAliasPattern(
          PtrDecl +
          R"cv(([A-Za-z_]\w*)\s*=\s*((?:(?:UndefValue|PoisonValue)::get\s*\([^)]*\))|nullptr)\s*;)cv");
      for (std::sregex_iterator It(PassthruBindingText.begin(),
                                   PassthruBindingText.end(),
                                   PassthruUndefAliasPattern),
           End;
           It != End; ++It) {
        RecordPassthruBinding(
            (*It)[1].str(),
            PassthruBinding{"symbolic-undef", "", -1, It->str()});
      }
      std::regex PassthruPlainAliasPattern(
          PtrDecl + R"cv(([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;)cv");
      bool PassthruAliasProgress = true;
      while (PassthruAliasProgress) {
        PassthruAliasProgress = false;
        for (std::sregex_iterator It(PassthruBindingText.begin(),
                                     PassthruBindingText.end(),
                                     PassthruPlainAliasPattern),
             End;
             It != End; ++It) {
          const std::string Temp = (*It)[1].str();
          const std::string SourceTemp = (*It)[2].str();
          const auto Source = LocalPassthruValues.find(SourceTemp);
          if (Source == LocalPassthruValues.end()) {
            continue;
          }
          PassthruBinding Binding = Source->second;
          Binding.Source = It->str();
          PassthruAliasProgress |= RecordPassthruBinding(Temp, std::move(Binding));
        }
      }
      auto AppendPassthruTemp = [&](const std::string &Temp, int Lane,
                                    const std::string &Source) {
        const auto Found = LocalPassthruValues.find(trim(Temp));
        if (Found == LocalPassthruValues.end()) {
          if (Info.Reason.empty()) {
            SetMaskFailure("unsupported-missing-masked-load-passthru",
                           "missing-passthru", Source, trim(Temp));
          }
          return false;
        }
        if (Found->second.Kind == "array") {
          PassthruOperands.insert(Found->second.Operand);
          Info.PassthruOrder.push_back(Found->second.Lane);
          return true;
        }
        if (Found->second.Kind == "symbolic-undef") {
          AppendSymbolicPassthru(Info, "a", Lane);
          return true;
        }
        if (Info.Reason.empty()) {
          SetMaskFailure("unsupported-missing-masked-load-passthru",
                         "missing-passthru", Source, trim(Temp));
        }
        return false;
      };
      auto HasConflictingConcreteMaskAssignment = [&]() {
        std::set<std::string> UsedMaskTemps;
        std::regex TempMaskedLoadUse(
            R"cv(CreateMaskedLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*([A-Za-z_]\w*)\s*,)cv");
        for (std::sregex_iterator It(Body.begin(), Body.end(),
                                     TempMaskedLoadUse),
             End;
             It != End; ++It) {
          UsedMaskTemps.insert((*It)[1].str());
        }
        std::regex TempMaskedStoreUse(
            R"cv(CreateMaskedStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*([A-Za-z_]\w*)\s*\))cv");
        for (std::sregex_iterator It(Body.begin(), Body.end(),
                                     TempMaskedStoreUse),
             End;
             It != End; ++It) {
          UsedMaskTemps.insert((*It)[1].str());
        }
        std::map<std::string, int> AssignmentCounts;
        std::regex ConcreteMaskAssign(
            R"cv((?:(?:Value|auto)\s*\*\s*(?:const\s*)?)?([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*(?:\.|->)\s*Create(?:ICmp|And|Or|Xor|Not|Select)\s*\()cv");
        for (std::sregex_iterator It(Body.begin(), Body.end(),
                                     ConcreteMaskAssign),
             End;
             It != End; ++It) {
          const std::string Temp = (*It)[1].str();
          if (UsedMaskTemps.count(Temp) != 0 &&
              ++AssignmentCounts[Temp] > 1) {
            return true;
          }
        }
        return false;
      };
      if (Body.find("CreateStore") != std::string::npos) {
        Info.Status = "failed";
        Info.Reason = "unsupported-intervening-store";
      } else if (Body.find("CreateVolatileLoad") != std::string::npos ||
                 Body.find("CreateAtomicLoad") != std::string::npos ||
                 Body.find("isVolatile") != std::string::npos ||
                 Body.find("isAtomic") != std::string::npos) {
        Info.Status = "failed";
        Info.Reason = "unsupported-volatile-or-atomic-memory";
      } else if (std::regex_search(
                     Body,
                     std::regex(
                         R"cv((?:\+\+\s*[A-Za-z_]\w*)|(?:[A-Za-z_]\w*\s*\+\+)|(?:[A-Za-z_]\w*\s*\+=))cv")) ||
                 Body.find("Base =") != std::string::npos) {
        Info.Status = "failed";
        Info.Reason = "unsupported-pointer-mutation";
      } else if (Body.find("touchMemory") != std::string::npos ||
                 Body.find("unknownMemoryEffect") != std::string::npos ||
                 Body.find("mayWriteMemory") != std::string::npos) {
        Info.Status = "failed";
        Info.Reason = "unsupported-memory-effect-call";
      } else if (HasConflictingConcreteMaskAssignment()) {
        SetMaskFailure("unsupported-unresolved-memory-mask",
                       "conflicting-assignment", Body, "");
      }
      std::regex MaskedLoadPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(), MaskedLoadPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        MaskOperands.insert((*It)[3].str());
        PassthruOperands.insert((*It)[5].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskName = trim((*It)[3].str());
        const std::string MaskIndexText = trim((*It)[4].str());
        const std::string PassthruIndexText = trim((*It)[6].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (std::optional<int> MaskIndex = LaneIndexForText(MaskIndexText)) {
          Info.MaskOrder.push_back(*MaskIndex);
        } else {
          Info.MaskOrder.push_back(-1);
          if (!AppendIndexedMaskCondition(Info.MaskConditions, MaskName,
                                          MaskIndexText, Lane,
                                          MaskName + "[" + MaskIndexText + "]") &&
              Info.Reason.empty()) {
            SetMaskFailure("unsupported-variable-mask-index",
                           "unsafe-mask-index",
                           MaskName + "[" + MaskIndexText + "]", MaskName);
          }
        }
        if (std::optional<int> PassthruIndex =
                LaneIndexForText(PassthruIndexText)) {
          Info.PassthruOrder.push_back(*PassthruIndex);
        } else {
          if (Info.Reason.empty()) {
            SetMaskFailure("unsupported-missing-masked-load-passthru",
                           "missing-passthru", It->str(), "");
          }
        }
      }
      std::regex MaskedLoadUndefPassthruPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*((?:(?:UndefValue|PoisonValue)::get\s*\([^)]*\))|nullptr)\s*\))cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(),
                                   MaskedLoadUndefPassthruPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        MaskOperands.insert((*It)[3].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskName = trim((*It)[3].str());
        const std::string MaskIndexText = trim((*It)[4].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (std::optional<int> MaskIndex = LaneIndexForText(MaskIndexText)) {
          Info.MaskOrder.push_back(*MaskIndex);
        } else {
          Info.MaskOrder.push_back(-1);
          if (!AppendIndexedMaskCondition(Info.MaskConditions, MaskName,
                                          MaskIndexText, Lane,
                                          MaskName + "[" + MaskIndexText + "]") &&
              Info.Reason.empty()) {
            SetMaskFailure("unsupported-variable-mask-index",
                           "unsafe-mask-index",
                           MaskName + "[" + MaskIndexText + "]", MaskName);
          }
        }
        AppendSymbolicPassthru(Info, "a", Lane);
      }
      std::regex MaskedLoadImplicitUndefPassthruPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(),
                                   MaskedLoadImplicitUndefPassthruPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        MaskOperands.insert((*It)[3].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskName = trim((*It)[3].str());
        const std::string MaskIndexText = trim((*It)[4].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (std::optional<int> MaskIndex = LaneIndexForText(MaskIndexText)) {
          Info.MaskOrder.push_back(*MaskIndex);
        } else {
          Info.MaskOrder.push_back(-1);
          if (!AppendIndexedMaskCondition(Info.MaskConditions, MaskName,
                                          MaskIndexText, Lane,
                                          MaskName + "[" + MaskIndexText + "]") &&
              Info.Reason.empty()) {
            SetMaskFailure("unsupported-variable-mask-index",
                           "unsafe-mask-index",
                           MaskName + "[" + MaskIndexText + "]", MaskName);
          }
        }
        AppendSymbolicPassthru(Info, "a", Lane);
      }
      std::regex MaskedLoadTempPassthruPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\))cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(),
                                   MaskedLoadTempPassthruPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        MaskOperands.insert((*It)[3].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskName = trim((*It)[3].str());
        const std::string MaskIndexText = trim((*It)[4].str());
        const std::string PassthruTemp = trim((*It)[5].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (std::optional<int> MaskIndex = LaneIndexForText(MaskIndexText)) {
          Info.MaskOrder.push_back(*MaskIndex);
        } else {
          Info.MaskOrder.push_back(-1);
          if (!AppendIndexedMaskCondition(Info.MaskConditions, MaskName,
                                          MaskIndexText, Lane,
                                          MaskName + "[" + MaskIndexText + "]") &&
              Info.Reason.empty()) {
            SetMaskFailure("unsupported-variable-mask-index",
                           "unsafe-mask-index",
                           MaskName + "[" + MaskIndexText + "]", MaskName);
          }
        }
        AppendPassthruTemp(PassthruTemp, Lane, It->str());
      }
      std::regex MaskedLoadTempMaskPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(), MaskedLoadTempMaskPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        PassthruOperands.insert((*It)[4].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskTemp = trim((*It)[3].str());
        const std::string PassthruIndexText = trim((*It)[5].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (!AppendMaskCondition(Info.MaskConditions, LocalMaskConditions,
                                 MaskTemp, Lane) &&
            Info.Reason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         IncompleteBranchDetailForTemp(MaskTemp), It->str(),
                         MaskTemp);
        }
        if (std::optional<int> PassthruIndex =
                LaneIndexForText(PassthruIndexText)) {
          Info.PassthruOrder.push_back(*PassthruIndex);
        } else {
          if (Info.Reason.empty()) {
            SetMaskFailure("unsupported-missing-masked-load-passthru",
                           "missing-passthru", It->str(), "");
          }
        }
      }
      std::regex MaskedLoadTempMaskTempPassthruPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\))cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(),
                                   MaskedLoadTempMaskTempPassthruPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskTemp = trim((*It)[3].str());
        const std::string PassthruTemp = trim((*It)[4].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (!AppendMaskCondition(Info.MaskConditions, LocalMaskConditions,
                                 MaskTemp, Lane) &&
            Info.Reason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         IncompleteBranchDetailForTemp(MaskTemp), It->str(),
                         MaskTemp);
        }
        AppendPassthruTemp(PassthruTemp, Lane, It->str());
      }
      std::regex MaskedLoadTempMaskUndefPassthruPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*,\s*((?:(?:UndefValue|PoisonValue)::get\s*\([^)]*\))|nullptr)\s*\))cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(),
                                   MaskedLoadTempMaskUndefPassthruPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskTemp = trim((*It)[3].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (!AppendMaskCondition(Info.MaskConditions, LocalMaskConditions,
                                 MaskTemp, Lane) &&
            Info.Reason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         IncompleteBranchDetailForTemp(MaskTemp), It->str(),
                         MaskTemp);
        }
        AppendSymbolicPassthru(Info, "a", Lane);
      }
      std::regex MaskedLoadTempMaskImplicitUndefPassthruPattern(
          R"cv(CreateMaskedLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\))cv");
      for (std::sregex_iterator It(
               Body.begin(), Body.end(),
               MaskedLoadTempMaskImplicitUndefPassthruPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        Bases.insert((*It)[1].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string IndexText = trim((*It)[2].str());
        const std::string MaskTemp = trim((*It)[3].str());
        AppendGatherAddress(IndexText, Lane, (*It)[1].str(), It->str());
        if (!AppendMaskCondition(Info.MaskConditions, LocalMaskConditions,
                                 MaskTemp, Lane) &&
            Info.Reason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         IncompleteBranchDetailForTemp(MaskTemp), It->str(),
                         MaskTemp);
        }
        AppendSymbolicPassthru(Info, "a", Lane);
      }
      std::regex GuardedLoadPattern(
          R"cv((?:Value\s*\*\s*)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*;\s*if\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\)\s*\{?\s*\1\s*=\s*(?:[A-Za-z_]\w*\s*\.\s*)?CreateLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\)\s*;\s*\}?)cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(), GuardedLoadPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        PassthruOperands.insert((*It)[2].str());
        MaskOperands.insert((*It)[4].str());
        Bases.insert((*It)[6].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string PassthruIndexText = trim((*It)[3].str());
        const std::string MaskName = trim((*It)[4].str());
        const std::string MaskIndexText = trim((*It)[5].str());
        const std::string IndexText = trim((*It)[7].str());
        AppendGatherAddress(IndexText, Lane, (*It)[6].str(), It->str());
        if (std::optional<int> MaskIndex = LaneIndexForText(MaskIndexText)) {
          Info.MaskOrder.push_back(*MaskIndex);
        } else {
          Info.MaskOrder.push_back(-1);
          if (!AppendIndexedMaskCondition(Info.MaskConditions, MaskName,
                                          MaskIndexText, Lane,
                                          MaskName + "[" + MaskIndexText + "]") &&
              Info.Reason.empty()) {
            SetMaskFailure("unsupported-variable-mask-index",
                           "unsafe-mask-index",
                           MaskName + "[" + MaskIndexText + "]", MaskName);
          }
        }
        if (std::optional<int> PassthruIndex =
                LaneIndexForText(PassthruIndexText)) {
          Info.PassthruOrder.push_back(*PassthruIndex);
        } else {
          if (Info.Reason.empty()) {
            SetMaskFailure("unsupported-missing-masked-load-passthru",
                           "missing-passthru", It->str(), "");
          }
        }
      }
      std::regex GuardedLoadTempMaskPattern(
          R"cv((?:Value\s*\*\s*)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*;\s*if\s*\(\s*([A-Za-z_]\w*)\s*\)\s*\{?\s*\1\s*=\s*(?:[A-Za-z_]\w*\s*\.\s*)?CreateLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\)\s*;\s*\}?)cv");
      for (std::sregex_iterator It(Body.begin(), Body.end(),
                                   GuardedLoadTempMaskPattern),
           End;
           It != End; ++It) {
        SawMaskedLoad = true;
        PassthruOperands.insert((*It)[2].str());
        Bases.insert((*It)[5].str());
        const int Lane = static_cast<int>(Info.Offsets.size());
        const std::string PassthruIndexText = trim((*It)[3].str());
        const std::string MaskTemp = trim((*It)[4].str());
        const std::string IndexText = trim((*It)[6].str());
        AppendGatherAddress(IndexText, Lane, (*It)[5].str(), It->str());
        if (!AppendMaskCondition(Info.MaskConditions, LocalMaskConditions,
                                 MaskTemp, Lane) &&
            Info.Reason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         IncompleteBranchDetailForTemp(MaskTemp), It->str(),
                         MaskTemp);
        }
        if (std::optional<int> PassthruIndex =
                LaneIndexForText(PassthruIndexText)) {
          Info.PassthruOrder.push_back(*PassthruIndex);
        } else {
          if (Info.Reason.empty()) {
            SetMaskFailure("unsupported-missing-masked-load-passthru",
                           "missing-passthru", It->str(), "");
          }
        }
      }
      std::regex LoadPattern(
          R"cv(CreateLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      if (!SawMaskedLoad) {
        for (std::sregex_iterator It(Body.begin(), Body.end(), LoadPattern),
             End;
             It != End; ++It) {
          Bases.insert((*It)[1].str());
          const std::string IndexText = trim((*It)[2].str());
          AppendGatherAddress(IndexText, static_cast<int>(Info.Offsets.size()),
                              (*It)[1].str(), It->str());
        }
      }
      if (SawMaskedLoad) {
        Info.IsMasked = true;
        Info.MaskedLanePolicy = "passthru";
        const bool HasConditionMasks =
            Info.MaskConditions.size() == Info.Offsets.size();
        if (HasConditionMasks) {
          Info.MaskOperand = "";
        } else if (MaskOperands.size() == 1) {
          Info.MaskOperand = *MaskOperands.begin();
        } else if (Info.Reason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         "unknown-mask-expression", Body, "");
        }
        const bool HasSymbolicPassthru =
            Info.PassthruKind == "symbolic-undef" &&
            Info.PassthruSymbols.size() == Info.Offsets.size();
        if (HasSymbolicPassthru) {
          Info.PassthruOperand = "";
        } else if (PassthruOperands.size() == 1) {
          Info.PassthruOperand = *PassthruOperands.begin();
        } else if (Info.Reason.empty()) {
          SetMaskFailure("unsupported-missing-masked-load-passthru",
                         "missing-passthru", Body, "");
        }
        if (((!HasConditionMasks &&
              Info.MaskOrder.size() != Info.Offsets.size()) ||
             (!HasSymbolicPassthru &&
              Info.PassthruOrder.size() != Info.Offsets.size())) &&
            Info.Reason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         "unknown-mask-expression", Body, "");
        }
      }
      if (Bases.size() > 1 && Info.Reason.empty()) {
        Info.Status = "failed";
        Info.Reason = "unsupported-ambiguous-memory-base";
      }
      if (Info.Offsets.empty() && Info.Reason.empty()) {
        return std::nullopt;
      }
      if (Info.Reason.empty()) {
        std::set<int> Seen;
        for (int Offset : Info.Offsets) {
          if (Offset < 0) {
            continue;
          }
          if (!Seen.insert(Offset).second) {
            Info.Status = "failed";
            Info.Reason = "unsupported-duplicate-gather-lane";
            break;
          }
        }
      }
      return Info;
    };
    auto HelperMemoryPackInfo =
        [&](const std::string &FunctionName,
            const std::vector<std::string> &Args) -> std::optional<MemoryPackInfo> {
      HelperExpansionResult MaybeBody =
          ExpandHelperBody(FunctionName, Args, 0, {});
      if (!MaybeBody.Body) {
        RecordHelperSliceFailure("memory-pack", FunctionName + "(...)", MaybeBody);
        return std::nullopt;
      }
      return MemoryPackInfoForBody(*MaybeBody.Body);
    };
    std::regex HelperMemoryPackPattern(
        R"(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*\(([^;]+)\)\s*;)");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 HelperMemoryPackPattern),
         End;
         It != End; ++It) {
      const std::string Temp = (*It)[1].str();
      const std::string Helper = (*It)[2].str();
      if (PacksByTemp.count(Temp) != 0) {
        continue;
      }
      std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
      if (Args.empty()) {
        continue;
      }
      if (std::optional<MemoryPackInfo> MemoryInfo =
              HelperMemoryPackInfo(Helper, Args)) {
        if (MemoryInfo->Status == "complete" &&
            (IsContiguousMemoryOffsets(MemoryInfo->Offsets) ||
             IsUniqueStaticMemoryOffsets(MemoryInfo->Offsets) ||
             HasCompleteSymbolicAddressTerms(*MemoryInfo))) {
          AddMemoryPackBinding(Temp, Args[0], *MemoryInfo, It->str());
        } else if (MemoryInfo->Status != "complete") {
          RecordMemoryPackMaskFailure(*MemoryInfo, It->str());
        }
      }
    }
    std::regex NamedConstPattern(
        R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*(Constant(?:Int)?::get(?:NullValue|AllOnesValue)?\s*\([^;]+?\))\s*;)cv");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 NamedConstPattern),
         End;
         It != End; ++It) {
      if (std::optional<ConstBinding> Binding = ConstForText((*It)[2].str())) {
        ConstsByTemp[(*It)[1].str()] = *Binding;
      }
    }
    auto ParseMaskValues =
        [](const std::string &Text) -> std::optional<std::vector<int>> {
      std::vector<int> Values;
      std::stringstream Stream(Text);
      std::string Item;
      while (std::getline(Stream, Item, ',')) {
        Item = trim(Item);
        if (Item.empty()) {
          continue;
        }
        try {
          size_t Parsed = 0;
          int Value = std::stoi(Item, &Parsed, 0);
          if (Parsed != Item.size()) {
            return std::nullopt;
          }
          Values.push_back(Value);
        } catch (...) {
          return std::nullopt;
        }
      }
      if (Values.empty()) {
        return std::nullopt;
      }
      return Values;
    };
    auto AddMaskBindings = [&](const std::string &Text) {
      std::regex MaskDecl(
          R"cv((?:static\s+)?(?:const\s+)?int\s+([A-Za-z_]\w*)\s*\[\s*\d+\s*\]\s*=\s*\{([^}]*)\})cv");
      for (std::sregex_iterator It(Text.begin(), Text.end(), MaskDecl), End;
           It != End; ++It) {
        if (std::optional<std::vector<int>> Mask =
                ParseMaskValues((*It)[2].str())) {
          MasksByName[(*It)[1].str()] = *Mask;
        }
      }
    };
    AddMaskBindings(Emitter.Body);
    AddMaskBindings(MainSource);
    for (const std::string &Line : Emitter.Lines) {
      AddMaskBindings(Line);
    }
    auto MaskForText =
        [&](const std::string &Text) -> std::optional<std::vector<int>> {
      const std::string Key = trim(Text);
      if (const auto Found = MasksByName.find(Key); Found != MasksByName.end()) {
        return Found->second;
      }
      std::smatch Match;
      std::regex Braced(R"cv(\{([^}]*)\})cv");
      if (std::regex_search(Key, Match, Braced)) {
        return ParseMaskValues(Match[1].str());
      }
      return std::nullopt;
    };
    std::map<std::string, std::string> NodeIdByTemp;
    std::vector<NodeBinding> NodeBindings;

    auto OperandObject =
        [&](const std::string &Temp) -> std::optional<llvm::json::Object> {
      const std::string Key = trim(Temp);
      if (const auto Pack = PacksByTemp.find(Key); Pack != PacksByTemp.end()) {
        if (Pack->second.IsMemory) {
          return llvm::json::Object{{"kind", "memory-pack"},
                                    {"name", Pack->second.Name}};
        }
        return llvm::json::Object{{"kind", "pack"}, {"name", Pack->second.Name}};
      }
      if (const auto Node = NodeIdByTemp.find(Key); Node != NodeIdByTemp.end()) {
        return llvm::json::Object{{"kind", "node"}, {"id", Node->second}};
      }
      if (const auto Const = ConstsByTemp.find(Key); Const != ConstsByTemp.end()) {
        return llvm::json::Object{
            {"kind", "const"},
            {"value", static_cast<int64_t>(Const->second.Value)},
            {"bits", static_cast<int>(Const->second.Bits)},
            {"source", Const->second.Source}};
      }
      if (std::optional<ConstBinding> Const = ConstForText(Key)) {
        return llvm::json::Object{
            {"kind", "const"},
            {"value", static_cast<int64_t>(Const->Value)},
            {"bits", static_cast<int>(Const->Bits)},
            {"source", Const->Source}};
      }
      return std::nullopt;
    };

    std::regex NodePattern(
        R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\.\s*Create(Add|Sub|Mul|Xor|Or|And|Shl|LShr|AShr|SMin|SMax|UMin|UMax)\s*\(([^;]+)\)\s*;)cv");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 NodePattern),
         End;
         It != End; ++It) {
      std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
      if (Args.size() != 2 || !OperandObject(Args[0]) || !OperandObject(Args[1])) {
        continue;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = (*It)[1].str();
      Node.Opcode = OpcodeName((*It)[2].str());
      Node.Arg0 = Args[0];
      Node.Arg1 = Args[1];
      Node.Source = It->str();
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    }

    auto AddNodeBinding = [&](const std::string &Temp, const std::string &Opcode,
                              const std::string &Arg0, const std::string &Arg1,
                              const std::string &Source) {
      if (NodeIdByTemp.count(Temp) != 0) {
        return;
      }
      if (!OperandObject(Arg0) || !OperandObject(Arg1)) {
        return;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = Temp;
      Node.Opcode = Opcode;
      Node.Arg0 = Arg0;
      Node.Arg1 = Arg1;
      Node.Source = Source;
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    };

    auto AddCastBinding = [&](const std::string &Temp, const std::string &Opcode,
                              const std::string &Arg0, unsigned Bits,
                              const std::string &Source) {
      if (NodeIdByTemp.count(Temp) != 0 || Bits == 0) {
        return;
      }
      if (!OperandObject(Arg0)) {
        return;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = Temp;
      Node.Opcode = Opcode;
      Node.Arg0 = Arg0;
      Node.Kind = "cast";
      Node.Bits = Bits;
      Node.Source = Source;
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    };

    auto AddIcmpBinding = [&](const std::string &Temp,
                              const std::string &Predicate,
                              const std::string &Arg0, const std::string &Arg1,
                              const std::string &Source) {
      if (NodeIdByTemp.count(Temp) != 0) {
        return;
      }
      if (!OperandObject(Arg0) || !OperandObject(Arg1)) {
        return;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = Temp;
      Node.Opcode = "icmp";
      Node.Predicate = Predicate;
      Node.Arg0 = Arg0;
      Node.Arg1 = Arg1;
      Node.Kind = "icmp";
      Node.Source = Source;
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    };

    auto AddSelectBinding = [&](const std::string &Temp,
                                const std::string &Condition,
                                const std::string &TrueValue,
                                const std::string &FalseValue,
                                const std::string &Source) {
      if (NodeIdByTemp.count(Temp) != 0) {
        return;
      }
      if (!OperandObject(Condition) || !OperandObject(TrueValue) ||
          !OperandObject(FalseValue)) {
        return;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = Temp;
      Node.Opcode = "select";
      Node.Arg0 = Condition;
      Node.Arg1 = TrueValue;
      Node.Arg2 = FalseValue;
      Node.Kind = "select";
      Node.Source = Source;
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    };

    auto AddShuffleBinding = [&](const std::string &Temp,
                                 const std::string &Arg0,
                                 const std::string &Arg1,
                                 const std::vector<int> &Mask,
                                 const std::string &Source) {
      if (NodeIdByTemp.count(Temp) != 0 || Mask.empty()) {
        return;
      }
      if (!OperandObject(Arg0) || (!Arg1.empty() && !OperandObject(Arg1))) {
        return;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = Temp;
      Node.Opcode = "shuffle";
      Node.Arg0 = Arg0;
      Node.Arg1 = Arg1;
      Node.Kind = "shuffle";
      Node.Mask = Mask;
      Node.Source = Source;
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    };

    auto AddExtractBinding = [&](const std::string &Temp,
                                 const std::string &Arg0, int Index,
                                 const std::string &Source) {
      if (NodeIdByTemp.count(Temp) != 0 || Index < 0) {
        return;
      }
      if (!OperandObject(Arg0)) {
        return;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = Temp;
      Node.Opcode = "extract";
      Node.Arg0 = Arg0;
      Node.Kind = "extract";
      Node.Index = Index;
      Node.Source = Source;
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    };

    auto AddInsertBinding = [&](const std::string &Temp,
                                const std::string &Arg0,
                                const std::string &Arg1, int Index,
                                const std::string &Source) {
      if (NodeIdByTemp.count(Temp) != 0 || Index < 0) {
        return;
      }
      if (!OperandObject(Arg0) || !OperandObject(Arg1)) {
        return;
      }
      NodeBinding Node;
      Node.Id = "n" + std::to_string(NodeBindings.size());
      Node.Temp = Temp;
      Node.Opcode = "insert";
      Node.Arg0 = Arg0;
      Node.Arg1 = Arg1;
      Node.Kind = "insert";
      Node.Index = Index;
      Node.Source = Source;
      NodeIdByTemp[Node.Temp] = Node.Id;
      NodeBindings.push_back(std::move(Node));
    };

    std::regex CastPattern(
        R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\.\s*Create(ZExt|SExt|Trunc)\s*\(\s*([A-Za-z_]\w*)\s*,\s*([^;]+?)\s*\)\s*;)cv");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 CastPattern),
         End;
         It != End; ++It) {
      if (std::optional<unsigned> Bits = CastBitsForTarget((*It)[4].str())) {
        AddCastBinding((*It)[1].str(), OpcodeName((*It)[2].str()),
                       (*It)[3].str(), *Bits, It->str());
      }
    }
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 NodePattern),
         End;
         It != End; ++It) {
      std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
      if (Args.size() == 2) {
        AddNodeBinding((*It)[1].str(), OpcodeName((*It)[2].str()), Args[0],
                       Args[1], It->str());
      }
    }
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 CastPattern),
         End;
         It != End; ++It) {
      if (std::optional<unsigned> Bits = CastBitsForTarget((*It)[4].str())) {
        AddCastBinding((*It)[1].str(), OpcodeName((*It)[2].str()),
                       (*It)[3].str(), *Bits, It->str());
      }
    }

    auto DiscoverShuffleNodes = [&]() {
      std::regex ShufflePattern(
          R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\.\s*CreateShuffleVector\s*\(([^;]+)\)\s*;)cv");
      for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                   ShufflePattern),
           End;
           It != End; ++It) {
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[2].str());
        if (Args.size() == 2) {
          if (std::optional<std::vector<int>> Mask = MaskForText(Args[1])) {
            AddShuffleBinding((*It)[1].str(), Args[0], "", *Mask, It->str());
          }
        } else if (Args.size() == 3) {
          if (std::optional<std::vector<int>> Mask = MaskForText(Args[2])) {
            AddShuffleBinding((*It)[1].str(), Args[0], Args[1], *Mask,
                              It->str());
          }
        }
      }
    };
    DiscoverShuffleNodes();

    auto DiscoverExtractInsertNodes = [&]() {
      std::regex ExtractPattern(
          R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\.\s*CreateExtractElement\s*\(([^;]+)\)\s*;)cv");
      for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                   ExtractPattern),
           End;
           It != End; ++It) {
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[2].str());
        if (Args.size() == 2) {
          if (std::optional<int> Index = LaneIndexForText(Args[1])) {
            AddExtractBinding((*It)[1].str(), Args[0], *Index, It->str());
          }
        }
      }
      std::regex InsertPattern(
          R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\.\s*CreateInsertElement\s*\(([^;]+)\)\s*;)cv");
      for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                   InsertPattern),
           End;
           It != End; ++It) {
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[2].str());
        if (Args.size() == 3) {
          if (std::optional<int> Index = LaneIndexForText(Args[2])) {
            AddInsertBinding((*It)[1].str(), Args[0], Args[1], *Index,
                             It->str());
          }
        }
      }
    };
    DiscoverExtractInsertNodes();

    std::map<std::string, CmpBinding> CmpByTemp;
    std::regex CmpPattern(
        R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\.\s*CreateICmp\s*\(\s*(?:(?:Instruction|CmpInst)::)?ICMP_(EQ|NE|SLT|SLE|SGT|SGE|ULT|ULE|UGT|UGE)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*;)cv");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 CmpPattern),
         End;
         It != End; ++It) {
      const std::string Opcode = MinMaxOpcodeForPredicate((*It)[2].str());
      const std::string Predicate = IcmpPredicateName((*It)[2].str());
      if (Predicate.empty()) {
        continue;
      }
      CmpByTemp[(*It)[1].str()] =
          CmpBinding{Opcode, Predicate, trim((*It)[3].str()),
                     trim((*It)[4].str()), It->str()};
    }
    std::regex SelectPattern(
        R"cv(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*\.\s*CreateSelect\s*\(\s*([A-Za-z_]\w*)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*;)cv");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 SelectPattern),
         End;
         It != End; ++It) {
      const auto Cmp = CmpByTemp.find((*It)[2].str());
      if (Cmp == CmpByTemp.end()) {
        continue;
      }
      const std::string TrueValue = trim((*It)[3].str());
      const std::string FalseValue = trim((*It)[4].str());
      if (!Cmp->second.Opcode.empty() && TrueValue == Cmp->second.Arg0 &&
          FalseValue == Cmp->second.Arg1) {
        bool HasEquivalentNode = false;
        for (const NodeBinding &Node : NodeBindings) {
          if (Node.Opcode == Cmp->second.Opcode &&
              Node.Arg0 == Cmp->second.Arg0 &&
              Node.Arg1 == Cmp->second.Arg1) {
            HasEquivalentNode = true;
            break;
          }
        }
        if (!HasEquivalentNode) {
          AddNodeBinding((*It)[1].str(), Cmp->second.Opcode, Cmp->second.Arg0,
                         Cmp->second.Arg1,
                         Cmp->second.Source + " " + It->str());
        }
        continue;
      }
      AddIcmpBinding((*It)[2].str(), Cmp->second.Predicate, Cmp->second.Arg0,
                     Cmp->second.Arg1, Cmp->second.Source);
      AddSelectBinding((*It)[1].str(), (*It)[2].str(), TrueValue, FalseValue,
                       Cmp->second.Source + " " + It->str());
    }
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 NodePattern),
         End;
         It != End; ++It) {
      std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
      if (Args.size() == 2) {
        AddNodeBinding((*It)[1].str(), OpcodeName((*It)[2].str()), Args[0],
                       Args[1], It->str());
      }
    }
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 CastPattern),
         End;
         It != End; ++It) {
      if (std::optional<unsigned> Bits = CastBitsForTarget((*It)[4].str())) {
        AddCastBinding((*It)[1].str(), OpcodeName((*It)[2].str()),
                       (*It)[3].str(), *Bits, It->str());
      }
    }
    DiscoverShuffleNodes();
    DiscoverExtractInsertNodes();

    auto DiscoverNodesInText = [&](const std::string &Text) {
      std::regex LocalPackPattern(
          R"(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*(?:packOperand|buildPack)\s*\(([^;]+)\)\s*;)");
      for (std::sregex_iterator It(Text.begin(), Text.end(), LocalPackPattern),
           End;
           It != End; ++It) {
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[2].str());
        if (Args.size() >= 2 && trim(Args[0]) == "Entry") {
          if (std::optional<unsigned> PackIndex =
                  PackIndexForText(Args[1], IntConstantsByName)) {
            AddPackBinding((*It)[1].str(), *PackIndex);
          }
        }
      }
      for (std::sregex_iterator It(Text.begin(), Text.end(), NodePattern), End;
           It != End; ++It) {
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
        if (Args.size() == 2) {
          AddNodeBinding((*It)[1].str(), OpcodeName((*It)[2].str()), Args[0],
                         Args[1], It->str());
        }
      }
      for (std::sregex_iterator It(Text.begin(), Text.end(), CastPattern), End;
           It != End; ++It) {
        if (std::optional<unsigned> Bits = CastBitsForTarget((*It)[4].str())) {
          AddCastBinding((*It)[1].str(), OpcodeName((*It)[2].str()),
                         (*It)[3].str(), *Bits, It->str());
        }
      }
    };
    std::regex HelperNodePattern(
        R"(Value\s*\*\s*([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(([^;]+)\)\s*;)");
    for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                 HelperNodePattern),
         End;
         It != End; ++It) {
      const std::string Temp = (*It)[1].str();
      const std::string Helper = (*It)[2].str();
      if (PacksByTemp.count(Temp) != 0 || NodeIdByTemp.count(Temp) != 0) {
        continue;
      }
      std::vector<std::string> Args = SplitTopLevelArgs((*It)[3].str());
      HelperExpansionResult Body =
          ExpandHelperReturnAsAssignment(Temp, Helper, Args, 0);
      if (Body.Body) {
        DiscoverNodesInText(*Body.Body);
      } else {
        RecordHelperSliceFailure("node-expansion", It->str(), Body);
      }
    }

    if (PacksByTemp.size() < 2) {
      return EmptyGraph();
    }
    if (NodeBindings.size() < 2) {
      return EmptyGraph();
    }

    std::vector<NodeBinding> OrderedNodes;
    std::set<std::string> PlacedTemps;
    while (OrderedNodes.size() < NodeBindings.size()) {
      bool Progress = false;
      for (const NodeBinding &Node : NodeBindings) {
        if (PlacedTemps.count(Node.Temp) != 0) {
          continue;
        }
        auto OperandReady = [&](const std::string &Temp) -> bool {
          const std::string Key = trim(Temp);
          return PacksByTemp.count(Key) != 0 || ConstsByTemp.count(Key) != 0 ||
                 PlacedTemps.count(Key) != 0 || ConstForText(Key).has_value();
        };
        if (!OperandReady(Node.Arg0)) {
          continue;
        }
        if (Node.Kind == "binop" && !OperandReady(Node.Arg1)) {
          continue;
        }
        if (Node.Kind == "icmp" && !OperandReady(Node.Arg1)) {
          continue;
        }
        if (Node.Kind == "select" &&
            (!OperandReady(Node.Arg1) || !OperandReady(Node.Arg2))) {
          continue;
        }
        if (Node.Kind == "shuffle" && !Node.Arg1.empty() &&
            !OperandReady(Node.Arg1)) {
          continue;
        }
        if (Node.Kind == "insert" && !OperandReady(Node.Arg1)) {
          continue;
        }
        OrderedNodes.push_back(Node);
        PlacedTemps.insert(Node.Temp);
        Progress = true;
      }
      if (!Progress) {
        return EmptyGraph();
      }
    }
    NodeBindings = std::move(OrderedNodes);
    NodeIdByTemp.clear();
    for (size_t Index = 0; Index < NodeBindings.size(); ++Index) {
      NodeBindings[Index].Id = "n" + std::to_string(Index);
      NodeIdByTemp[NodeBindings[Index].Temp] = NodeBindings[Index].Id;
    }

    std::string RootTemp;
    std::regex ReplacementPattern(
        R"((?:replaceScalarUses|replaceExternalUses)\s*\([^,]+,\s*([A-Za-z_]\w*)\s*\))");
    std::smatch ReplacementMatch;
    if (std::regex_search(Emitter.Body, ReplacementMatch, ReplacementPattern)) {
      RootTemp = ReplacementMatch[1].str();
    } else {
      RootTemp = NodeBindings.back().Temp;
    }
    if (NodeIdByTemp.count(RootTemp) == 0) {
      return EmptyGraph();
    }

    auto StoreContractForOffsets =
        [&](const std::vector<int> &Offsets, bool IsMasked) -> std::string {
      if (static_cast<int>(Offsets.size()) != Lanes) {
        return "";
      }
      std::set<int> Seen;
      bool IsContiguous = true;
      for (int Lane = 0; Lane < Lanes; ++Lane) {
        if (Offsets[Lane] < 0 || !Seen.insert(Offsets[Lane]).second) {
          return "";
        }
        if (Offsets[Lane] != Lane) {
          IsContiguous = false;
        }
      }
      if (IsMasked) {
        return IsContiguous ? "masked-contiguous-store-pack-v1"
                            : "masked-static-scatter-store-pack-v1";
      }
      return IsContiguous ? "contiguous-store-pack-v1"
                          : "static-scatter-store-pack-v1";
    };
    auto AddStrideInfo = [](StoreSinkBinding &Sink) {
      if (Sink.AddressOrder.size() < 2) {
        return;
      }
      for (int Offset : Sink.AddressOrder) {
        if (Offset < 0) {
          return;
        }
      }
      const int Stride = Sink.AddressOrder[1] - Sink.AddressOrder[0];
      bool HasStride = true;
      for (size_t Index = 2; Index < Sink.AddressOrder.size(); ++Index) {
        if (Sink.AddressOrder[Index] - Sink.AddressOrder[Index - 1] != Stride) {
          HasStride = false;
          break;
        }
      }
      Sink.HasAddressStride = HasStride;
      Sink.AddressStride = Stride;
    };
    auto StoreSinkForRootInText =
        [&](const std::string &StoreText) -> std::optional<StoreSinkBinding> {
      StoreSinkBinding Sink;
      std::set<std::string> Bases;
      std::set<std::string> MaskOperands;
      std::map<std::string, llvm::json::Object> LocalMaskConditions =
          ParseMaskConditions(StoreText);
      auto SetMaskFailure = [&](const std::string &Reason,
                                const std::string &Detail,
                                const std::string &Source,
                                const std::string &Temp) {
        if (Sink.SafetyReason.empty()) {
          Sink.SafetyStatus = "failed";
          Sink.SafetyReason = Reason;
        }
        if (Sink.MaskFailureDetail.empty()) {
          Sink.MaskFailureDetail = Detail;
          Sink.MaskFailureSource = trim(Source);
          Sink.MaskFailureTemp = Temp;
          Sink.MaskFailureRole = "memory-store";
        }
      };
      auto SetStoreIndexFailure = [&](const std::string &Source,
                                      const std::string &Base) {
        if (Sink.SafetyReason.empty()) {
          Sink.SafetyStatus = "failed";
          Sink.SafetyReason = "unsupported-variable-store-index";
        }
        if (Sink.MaskFailureDetail.empty()) {
          Sink.MaskFailureDetail = "unsafe-store-index";
          Sink.MaskFailureSource = trim(Source);
          Sink.MaskFailureTemp = trim(Base);
          Sink.MaskFailureRole = "memory-store";
        }
      };
      auto HasCompleteSymbolicStoreAddressTerms = [&]() {
        if (static_cast<int>(Sink.AddressOrder.size()) != Lanes ||
            static_cast<int>(Sink.StoreAddressTerms.size()) != Lanes) {
          return false;
        }
        bool HasSymbolic = false;
        std::set<int> SeenLanes;
        for (const llvm::json::Value &Value : Sink.StoreAddressTerms) {
          const auto *Term = Value.getAsObject();
          if (!Term) {
            return false;
          }
          const std::optional<int64_t> Lane = Term->getInteger("lane");
          if (!Lane || *Lane < 0 || *Lane >= Lanes ||
              !SeenLanes.insert(static_cast<int>(*Lane)).second) {
            return false;
          }
          const std::string Kind = stringField(*Term, "kind");
          if (Kind == "symbolic") {
            HasSymbolic = true;
          } else if (Kind != "static") {
            return false;
          }
        }
        return HasSymbolic;
      };
      auto AppendStoreAddress = [&](const std::string &IndexText, int Lane,
                                    const std::string &Base,
                                    const std::string &Source) {
        const std::string TrimmedIndex = trim(IndexText);
        if (std::optional<int> Index = LaneIndexForText(TrimmedIndex)) {
          Sink.AddressOrder.push_back(*Index);
          Sink.StoreAddressTerms.push_back(llvm::json::Object{
              {"kind", "static"},
              {"base", trim(Base)},
              {"index", *Index},
              {"lane", Lane},
              {"source", trim(Source)}});
          return true;
        }
        if (isSafeSymbolicMaskIndexText(TrimmedIndex, IntConstantsByName)) {
          const std::string NormalizedIndex =
              NormalizeSymbolicMaskIndex(TrimmedIndex);
          Sink.AddressOrder.push_back(-1);
          Sink.StoreAddressTerms.push_back(llvm::json::Object{
              {"kind", "symbolic"},
              {"base", trim(Base)},
              {"index", NormalizedIndex},
              {"lane", Lane},
              {"source", trim(Source)}});
          return true;
        }
        SetStoreIndexFailure(Source, Base);
        return false;
      };
      auto ResolveMaskAliasTemp = [&](std::string Temp) {
        Temp = trim(std::move(Temp));
        std::map<std::string, std::string> AliasByTemp;
        std::regex PlainAlias(
            R"cv((?:(?:Value|auto)\s*\*\s*(?:const\s*)?)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;)cv");
        for (std::sregex_iterator It(StoreText.begin(), StoreText.end(),
                                     PlainAlias),
             End;
             It != End; ++It) {
          AliasByTemp.emplace((*It)[1].str(), (*It)[2].str());
        }
        std::set<std::string> Seen;
        while (!Temp.empty() && Seen.insert(Temp).second) {
          const auto Found = AliasByTemp.find(Temp);
          if (Found == AliasByTemp.end()) {
            break;
          }
          Temp = trim(Found->second);
        }
        return Temp;
      };
      auto IncompleteBranchDetailForTemp =
          [&](const std::string &Temp) -> std::string {
        const std::string DirectTemp = trim(Temp);
        if (DirectTemp.empty()) {
          return "unknown-mask-expression";
        }
        auto HasIncompleteBranchAssignment = [&](const std::string &Name) {
          std::regex BranchAssignment(
              R"cv(if\s*\(\s*[^;){}]+?\s*\)\s*(?:\{\s*)?)cv" + Name +
              R"cv(\s*=\s*[A-Za-z_]\w*\s*;\s*(?:\}\s*)?)cv");
          for (std::sregex_iterator It(StoreText.begin(), StoreText.end(),
                                       BranchAssignment),
               End;
               It != End; ++It) {
            std::string After = StoreText.substr(
                static_cast<size_t>(It->position() + It->length()));
            After = trim(After);
            if (!llvm::StringRef(After).starts_with("else")) {
              return true;
            }
          }
          return false;
        };
        if (HasIncompleteBranchAssignment(DirectTemp)) {
          return "incomplete-branch-assignment";
        }
        const std::string ResolvedTemp = ResolveMaskAliasTemp(DirectTemp);
        if (ResolvedTemp != DirectTemp &&
            HasIncompleteBranchAssignment(ResolvedTemp)) {
          return "incomplete-branch-assignment";
        }
        return "unknown-mask-expression";
      };
      std::regex MaskedStorePattern(
          R"cv(CreateMaskedStore\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(StoreText.begin(), StoreText.end(),
                                   MaskedStorePattern),
           End;
           It != End; ++It) {
        const std::string Value = trim((*It)[1].str());
        if (Value != RootTemp) {
          continue;
        }
        Sink.IsMasked = true;
        Sink.MaskedLanePolicy = "preserve-old-memory";
        Bases.insert((*It)[2].str());
        MaskOperands.insert((*It)[4].str());
        const int Lane = static_cast<int>(Sink.AddressOrder.size());
        const std::string IndexText = trim((*It)[3].str());
        const std::string MaskName = trim((*It)[4].str());
        const std::string MaskIndexText = trim((*It)[5].str());
        AppendStoreAddress(IndexText, Lane, (*It)[2].str(), It->str());
        if (std::optional<int> MaskIndex = LaneIndexForText(MaskIndexText)) {
          Sink.MaskOrder.push_back(*MaskIndex);
        } else {
          Sink.MaskOrder.push_back(-1);
          if (!AppendIndexedMaskCondition(Sink.MaskConditions, MaskName,
                                          MaskIndexText, Lane,
                                          MaskName + "[" + MaskIndexText + "]") &&
              Sink.SafetyReason.empty()) {
            SetMaskFailure("unsupported-variable-mask-index",
                           "unsafe-mask-index",
                           MaskName + "[" + MaskIndexText + "]", MaskName);
          }
        }
        Sink.Source += It->str();
        Sink.Source += "\n";
      }
      std::regex MaskedStoreTempMaskPattern(
          R"cv(CreateMaskedStore\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*([A-Za-z_]\w*)\s*\))cv");
      for (std::sregex_iterator It(StoreText.begin(), StoreText.end(),
                                   MaskedStoreTempMaskPattern),
           End;
           It != End; ++It) {
        const std::string Value = trim((*It)[1].str());
        if (Value != RootTemp) {
          continue;
        }
        Sink.IsMasked = true;
        Sink.MaskedLanePolicy = "preserve-old-memory";
        Bases.insert((*It)[2].str());
        const int Lane = static_cast<int>(Sink.AddressOrder.size());
        const std::string IndexText = trim((*It)[3].str());
        const std::string MaskTemp = trim((*It)[4].str());
        AppendStoreAddress(IndexText, Lane, (*It)[2].str(), It->str());
        if (!AppendMaskCondition(Sink.MaskConditions, LocalMaskConditions,
                                 MaskTemp, Lane) &&
            Sink.SafetyReason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         IncompleteBranchDetailForTemp(MaskTemp), It->str(),
                         MaskTemp);
        }
        Sink.Source += It->str();
        Sink.Source += "\n";
      }
      std::regex GuardedStorePattern(
          R"cv(if\s*\(\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\)\s*\{?\s*(?:[A-Za-z_]\w*\s*\.\s*)?CreateStore\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\)\s*;\s*\}?)cv");
      for (std::sregex_iterator It(StoreText.begin(), StoreText.end(),
                                   GuardedStorePattern),
           End;
           It != End; ++It) {
        const std::string Value = trim((*It)[3].str());
        if (Value != RootTemp) {
          continue;
        }
        Sink.IsMasked = true;
        Sink.MaskedLanePolicy = "preserve-old-memory";
        const std::string MaskName = trim((*It)[1].str());
        MaskOperands.insert(MaskName);
        Bases.insert((*It)[4].str());
        const int Lane = static_cast<int>(Sink.AddressOrder.size());
        const std::string MaskIndexText = trim((*It)[2].str());
        const std::string IndexText = trim((*It)[5].str());
        AppendStoreAddress(IndexText, Lane, (*It)[4].str(), It->str());
        if (std::optional<int> MaskIndex = LaneIndexForText(MaskIndexText)) {
          Sink.MaskOrder.push_back(*MaskIndex);
        } else {
          Sink.MaskOrder.push_back(-1);
          if (!AppendIndexedMaskCondition(Sink.MaskConditions, MaskName,
                                          MaskIndexText, Lane,
                                          MaskName + "[" + MaskIndexText + "]") &&
              Sink.SafetyReason.empty()) {
            SetMaskFailure("unsupported-variable-mask-index",
                           "unsafe-mask-index",
                           MaskName + "[" + MaskIndexText + "]", MaskName);
          }
        }
        Sink.Source += It->str();
        Sink.Source += "\n";
      }
      std::regex GuardedStoreTempMaskPattern(
          R"cv(if\s*\(\s*([A-Za-z_]\w*)\s*\)\s*\{?\s*(?:[A-Za-z_]\w*\s*\.\s*)?CreateStore\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\)\s*;\s*\}?)cv");
      for (std::sregex_iterator It(StoreText.begin(), StoreText.end(),
                                   GuardedStoreTempMaskPattern),
           End;
           It != End; ++It) {
        const std::string Value = trim((*It)[2].str());
        if (Value != RootTemp) {
          continue;
        }
        Sink.IsMasked = true;
        Sink.MaskedLanePolicy = "preserve-old-memory";
        Bases.insert((*It)[3].str());
        const int Lane = static_cast<int>(Sink.AddressOrder.size());
        const std::string MaskTemp = trim((*It)[1].str());
        const std::string IndexText = trim((*It)[4].str());
        AppendStoreAddress(IndexText, Lane, (*It)[3].str(), It->str());
        if (!AppendMaskCondition(Sink.MaskConditions, LocalMaskConditions,
                                 MaskTemp, Lane) &&
            Sink.SafetyReason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         IncompleteBranchDetailForTemp(MaskTemp), It->str(),
                         MaskTemp);
        }
        Sink.Source += It->str();
        Sink.Source += "\n";
      }
      std::regex StorePattern(
          R"cv(CreateStore\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      if (!Sink.IsMasked) {
        for (std::sregex_iterator It(StoreText.begin(), StoreText.end(),
                                     StorePattern),
             End;
             It != End; ++It) {
          const std::string Value = trim((*It)[1].str());
          if (Value != RootTemp) {
            continue;
          }
          Bases.insert((*It)[2].str());
          const std::string IndexText = trim((*It)[3].str());
          const int Lane = static_cast<int>(Sink.AddressOrder.size());
          AppendStoreAddress(IndexText, Lane, (*It)[2].str(), It->str());
          Sink.Source += It->str();
          Sink.Source += "\n";
        }
      }
      if (Sink.AddressOrder.empty() && Sink.SafetyReason.empty()) {
        return std::nullopt;
      }
      if (Bases.size() > 1 && Sink.SafetyReason.empty()) {
        Sink.SafetyStatus = "failed";
        Sink.SafetyReason = "unsupported-ambiguous-store-base";
      }
      if (!Bases.empty()) {
        Sink.Base = *Bases.begin();
      }
      if (Sink.IsMasked) {
        const bool HasConditionMasks =
            Sink.MaskConditions.size() == Sink.AddressOrder.size();
        if (HasConditionMasks) {
          Sink.MaskOperand = "";
        } else if (MaskOperands.size() == 1) {
          Sink.MaskOperand = *MaskOperands.begin();
        } else if (Sink.SafetyReason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         "unknown-mask-expression", StoreText, "");
        }
        if (!HasConditionMasks &&
            Sink.MaskOrder.size() != Sink.AddressOrder.size() &&
            Sink.SafetyReason.empty()) {
          SetMaskFailure("unsupported-unresolved-memory-mask",
                         "unknown-mask-expression", StoreText, "");
        }
      }
      if (Sink.SafetyReason.empty()) {
        if (HasCompleteSymbolicStoreAddressTerms()) {
          Sink.StoreContract = Sink.IsMasked ? "masked-symbolic-store-pack-v1"
                                             : "symbolic-store-pack-v1";
        } else {
          Sink.StoreContract =
              StoreContractForOffsets(Sink.AddressOrder, Sink.IsMasked);
        }
        if (Sink.StoreContract.empty()) {
          Sink.SafetyStatus = "failed";
          Sink.SafetyReason = "unsupported-duplicate-scatter-lane";
        }
      }
      AddStrideInfo(Sink);
      return Sink;
    };
    std::optional<StoreSinkBinding> StoreSink =
        StoreSinkForRootInText(Emitter.Body);
    if (!StoreSink) {
      std::regex HelperStorePattern(
          R"cv(\b([A-Za-z_]\w*)\s*\(([^;]*\b[A-Za-z_]\w*\b[^;]*)\)\s*;)cv");
      for (std::sregex_iterator It(Emitter.Body.begin(), Emitter.Body.end(),
                                   HelperStorePattern),
           End;
           It != End; ++It) {
        const std::string Helper = (*It)[1].str();
        if (Helper == "if" || Helper == "for" || Helper == "while" ||
            Helper == "replaceScalarUses" || Helper == "replaceExternalUses" ||
            Helper == "CreateStore" || Helper == "CreateMaskedStore") {
          continue;
        }
        std::vector<std::string> Args = SplitTopLevelArgs((*It)[2].str());
        if (Args.empty() || std::find(Args.begin(), Args.end(), RootTemp) ==
                                Args.end()) {
          continue;
        }
        HelperExpansionResult Body = ExpandHelperBody(Helper, Args, 0, {});
        if (Body.Body) {
          StoreSink = StoreSinkForRootInText(*Body.Body);
          if (StoreSink) {
            StoreSink->Source = trim(It->str()) + "\n" + StoreSink->Source;
            break;
          }
        } else {
          RecordHelperSliceFailure("store-sink", It->str(), Body);
        }
      }
    }
    if (StoreSink && StoreSink->SafetyStatus != "complete") {
      RecordStoreMaskFailure(*StoreSink, Emitter.Body);
      return EmptyGraph();
    }
    auto HasNoAliasEvidence = [&](const std::string &Left,
                                  const std::string &Right) {
      if (Left == Right) {
        return true;
      }
      auto DirectCallEvidence = [&](const std::string &Name,
                                    const std::string &First,
                                    const std::string &Second) {
        const std::string Pattern =
            R"(\b)" + Name + R"(\s*\(\s*)" + First + R"(\s*,\s*)" +
            Second + R"(\s*\))";
        return std::regex_search(MainSource, std::regex(Pattern));
      };
      auto MethodCallEvidence = [&](const std::string &Name,
                                    const std::string &First,
                                    const std::string &Second) {
        const std::string Pattern =
            R"(\b[A-Za-z_]\w*\s*(?:\.|->)\s*)" + Name +
            R"(\s*\(\s*)" + First + R"(\s*,\s*)" + Second + R"(\s*\))";
        return std::regex_search(MainSource, std::regex(Pattern));
      };
      for (const std::string &Name :
           {"noAlias", "NoAlias", "isNoAlias", "areNoAlias",
            "isKnownNoAlias"}) {
        if (DirectCallEvidence(Name, Left, Right) ||
            DirectCallEvidence(Name, Right, Left) ||
            MethodCallEvidence(Name, Left, Right) ||
            MethodCallEvidence(Name, Right, Left)) {
          return true;
        }
      }
      const std::string MayAliasA =
          R"(!\s*mayAlias\s*\(\s*)" + Left + R"(\s*,\s*)" + Right +
          R"(\s*\))";
      const std::string MayAliasB =
          R"(!\s*mayAlias\s*\(\s*)" + Right + R"(\s*,\s*)" + Left +
          R"(\s*\))";
      return std::regex_search(MainSource, std::regex(MayAliasA)) ||
             std::regex_search(MainSource, std::regex(MayAliasB));
    };

    llvm::json::Array Operands;
    std::set<std::string> AddedOperands;
    for (const std::string &Temp : PackOrder) {
      const auto Found = PacksByTemp.find(Temp);
      if (Found == PacksByTemp.end() ||
          AddedOperands.count(Found->second.Name) != 0) {
        continue;
      }
      AddedOperands.insert(Found->second.Name);
      Operands.push_back(llvm::json::Object{
          {"name", Found->second.Name},
          {"mapping", graphPackMapping(Emitter, LaneMapping, Found->second.Index)}});
      if (Found->second.IsMemory) {
        llvm::json::Object &Operand = *Operands.back().getAsObject();
        Operand["kind"] = "memory-pack";
        Operand["base"] = Found->second.Base;
        Operand["element_bits"] = static_cast<int>(Found->second.ElementBits);
        Operand["address_order"] = intArray(Found->second.AddressOrder);
        if (!Found->second.AddressTerms.empty()) {
          llvm::json::Array Terms;
          for (const llvm::json::Value &Term : Found->second.AddressTerms) {
            Terms.push_back(cloneJson(Term));
          }
          Operand["address_terms"] = std::move(Terms);
        }
        if (Found->second.HasAddressStride) {
          Operand["address_stride"] = Found->second.AddressStride;
        }
        Operand["memory_contract"] = Found->second.MemoryContract;
        if (Found->second.MemoryContract == "symbolic-gather-pack-v1" ||
            Found->second.MemoryContract == "masked-symbolic-gather-pack-v1") {
          Operand["memory_address_model"] = "lane-index-expression-v1";
        }
        if (Found->second.IsMasked) {
          Operand["masked"] = true;
          Operand["mask_operand"] = Found->second.MaskOperand;
          Operand["mask_order"] = intArray(Found->second.MaskOrder);
          if (!Found->second.MaskConditions.empty()) {
            llvm::json::Array Conditions;
            for (const llvm::json::Value &Condition :
                 Found->second.MaskConditions) {
              Conditions.push_back(cloneJson(Condition));
            }
            Operand["mask_conditions"] = std::move(Conditions);
          }
          Operand["passthru_operand"] = Found->second.PassthruOperand;
          Operand["passthru_order"] = intArray(Found->second.PassthruOrder);
          if (!Found->second.PassthruKind.empty()) {
            Operand["passthru_kind"] = Found->second.PassthruKind;
            llvm::json::Array Symbols;
            for (const std::string &Symbol : Found->second.PassthruSymbols) {
              Symbols.push_back(Symbol);
            }
            Operand["passthru_symbols"] = std::move(Symbols);
          }
          Operand["masked_lane_policy"] = Found->second.MaskedLanePolicy;
        }
        Operand["memory_safety_status"] = Found->second.MemorySafetyStatus;
        Operand["memory_effect_window"] = "helper-local-load-pack";
        Operand["no_intervening_store"] = Found->second.MemorySafetyStatus == "complete";
        Operand["alias_scope"] = "single-base";
        Operand["load_order"] = intArray(Found->second.AddressOrder);
        Operand["memory_side_conditions"] = llvm::json::Object{
            {"no_intervening_store", Found->second.MemorySafetyStatus == "complete"},
            {"stable_base", true},
            {"no_unknown_memory_effects", true},
            {"non_volatile", true},
            {"non_atomic", true}};
        if (!Found->second.MemorySafetyReason.empty()) {
          Operand["memory_safety_reason"] = Found->second.MemorySafetyReason;
        }
        Operand["source"] = trim(Found->second.Source);
      }
    }

    llvm::json::Array Nodes;
    llvm::json::Array Edges;
    for (const NodeBinding &Binding : NodeBindings) {
      std::optional<llvm::json::Object> Arg0 = OperandObject(Binding.Arg0);
      if (!Arg0) {
        return EmptyGraph();
      }
      if (stringField(*Arg0, "kind") == "node") {
        Edges.push_back(llvm::json::Object{{"from", stringField(*Arg0, "id")},
                                           {"to", Binding.Id},
                                           {"operand", 0}});
      }
      llvm::json::Array NodeOperands;
      NodeOperands.push_back(std::move(*Arg0));
      if (Binding.Kind == "binop" || Binding.Kind == "icmp" ||
          Binding.Kind == "select" || Binding.Kind == "insert" ||
          (Binding.Kind == "shuffle" && !Binding.Arg1.empty())) {
        std::optional<llvm::json::Object> Arg1 = OperandObject(Binding.Arg1);
        if (!Arg1) {
          return EmptyGraph();
        }
        if (stringField(*Arg1, "kind") == "node") {
          Edges.push_back(llvm::json::Object{{"from", stringField(*Arg1, "id")},
                                             {"to", Binding.Id},
                                             {"operand", 1}});
        }
        NodeOperands.push_back(std::move(*Arg1));
      }
      if (Binding.Kind == "select") {
        std::optional<llvm::json::Object> Arg2 = OperandObject(Binding.Arg2);
        if (!Arg2) {
          return EmptyGraph();
        }
        if (stringField(*Arg2, "kind") == "node") {
          Edges.push_back(llvm::json::Object{{"from", stringField(*Arg2, "id")},
                                             {"to", Binding.Id},
                                             {"operand", 2}});
        }
        NodeOperands.push_back(std::move(*Arg2));
      }
      llvm::json::Object NodeObject{
          {"id", Binding.Id},
          {"kind", Binding.Kind},
          {"opcode", Binding.Opcode},
          {"operands", std::move(NodeOperands)},
          {"source", trim(Binding.Source)},
          {"line", static_cast<int>(lineForBodyText(Emitter, Binding.Source))}};
      if (Binding.Kind == "cast") {
        NodeObject["bits"] = static_cast<int>(Binding.Bits);
      }
      if (Binding.Kind == "icmp") {
        NodeObject["predicate"] = Binding.Predicate;
      }
      if (Binding.Kind == "shuffle") {
        if (static_cast<int>(Binding.Mask.size()) != Lanes) {
          return EmptyGraph();
        }
        int SourceLanes = Lanes * (Binding.Arg1.empty() ? 1 : 2);
        for (int Index : Binding.Mask) {
          if (Index < 0 || Index >= SourceLanes) {
            return EmptyGraph();
          }
        }
        NodeObject["mask"] = intArray(Binding.Mask);
        NodeObject["base_mask"] = intArray(Binding.Mask);
      }
      if (Binding.Kind == "extract" || Binding.Kind == "insert") {
        if (Binding.Index < 0 || Binding.Index >= Lanes) {
          return EmptyGraph();
        }
        NodeObject["index"] = Binding.Index;
      }
      Nodes.push_back(std::move(NodeObject));
    }
    if (Edges.empty()) {
      return EmptyGraph();
    }

    llvm::json::Array GraphScalarPairs;
    for (int Lane = 0; Lane < Lanes; ++Lane) {
      llvm::json::Object Pair{{"lane", Lane},
                              {"result", std::string("r") + std::to_string(Lane)}};
      for (const llvm::json::Value &OperandValue : Operands) {
        if (const auto *Operand = OperandValue.getAsObject()) {
          const std::string Name = stringField(*Operand, "name");
          Pair[Name] = Name + std::to_string(Lane);
        }
      }
      GraphScalarPairs.push_back(std::move(Pair));
    }

    llvm::json::Array StoreSinks;
    llvm::json::Array MemoryAliasConditions;
    if (StoreSink) {
      llvm::json::Object Sink{
          {"kind", "memory-store"},
          {"node", NodeIdByTemp[RootTemp]},
          {"base", StoreSink->Base},
          {"element_bits", 32},
          {"address_order", intArray(StoreSink->AddressOrder)},
          {"store_contract", StoreSink->StoreContract},
          {"store_safety_status", StoreSink->SafetyStatus},
          {"store_effect_window", "helper-local-store-pack"},
          {"no_intervening_store", true},
          {"alias_scope", "single-base"},
          {"store_order", intArray(StoreSink->AddressOrder)},
          {"store_side_conditions",
           llvm::json::Object{{"no_intervening_store", true},
                              {"stable_base", true},
                              {"no_unknown_memory_effects", true},
                              {"non_volatile", true},
                              {"non_atomic", true}}},
          {"source", trim(StoreSink->Source)}};
      if (StoreSink->IsMasked) {
        Sink["masked"] = true;
        Sink["mask_operand"] = StoreSink->MaskOperand;
        Sink["mask_order"] = intArray(StoreSink->MaskOrder);
        if (!StoreSink->MaskConditions.empty()) {
          llvm::json::Array Conditions;
          for (const llvm::json::Value &Condition :
               StoreSink->MaskConditions) {
            Conditions.push_back(cloneJson(Condition));
          }
          Sink["mask_conditions"] = std::move(Conditions);
        }
        Sink["masked_lane_policy"] = StoreSink->MaskedLanePolicy;
      }
      if (!StoreSink->StoreAddressTerms.empty()) {
        llvm::json::Array Terms;
        for (const llvm::json::Value &Term : StoreSink->StoreAddressTerms) {
          Terms.push_back(cloneJson(Term));
        }
        Sink["store_address_terms"] = std::move(Terms);
      }
      if (StoreSink->StoreContract == "symbolic-store-pack-v1" ||
          StoreSink->StoreContract == "masked-symbolic-store-pack-v1") {
        Sink["store_address_model"] = "lane-index-expression-v1";
      }
      if (StoreSink->HasAddressStride) {
        Sink["address_stride"] = StoreSink->AddressStride;
      }
      StoreSinks.push_back(std::move(Sink));
      for (const auto &Item : PacksByTemp) {
        const PackBinding &Binding = Item.second;
        if (!Binding.IsMemory) {
          continue;
        }
        const bool SameBase = Binding.Base == StoreSink->Base;
        const bool NoAlias = HasNoAliasEvidence(Binding.Base, StoreSink->Base);
        MemoryAliasConditions.push_back(llvm::json::Object{
            {"left_base", Binding.Base},
            {"right_base", StoreSink->Base},
            {"relation", SameBase ? "same-base" : (NoAlias ? "noalias" : "unknown")},
            {"status", SameBase || NoAlias ? "complete" : "unknown"}});
      }
    }

    llvm::json::Object Graph{
        {"model", "optimization-transaction-graph-v1"},
        {"kind", "slp-binop-chain"},
        {"lanes", Lanes},
        {"lane_mapping", cloneObject(LaneMapping)},
        {"operands", std::move(Operands)},
        {"nodes", std::move(Nodes)},
        {"edges", std::move(Edges)},
        {"outputs",
         llvm::json::Array{llvm::json::Object{
             {"node", NodeIdByTemp[RootTemp]},
             {"result_lane_mapping", cloneObject(ResultMapping)}}}},
        {"scalar_lane_pairs", std::move(GraphScalarPairs)},
        {"consistency", "ok"}};
    if (!StoreSinks.empty()) {
      Graph["store_sinks"] = std::move(StoreSinks);
    }
    if (!MemoryAliasConditions.empty()) {
      Graph["memory_alias_conditions"] = std::move(MemoryAliasConditions);
    }
    return Graph;
  }

  llvm::json::Array scalarLanePairs(const llvm::json::Object &LHS,
                                    const llvm::json::Object &RHS,
                                    const llvm::json::Object &Result,
                                    int Lanes) const {
    llvm::json::Array Pairs;
    std::vector<int> LHSMap = mapFromObject(LHS);
    std::vector<int> RHSMap = mapFromObject(RHS);
    std::vector<int> ResultMap = mapFromObject(Result);
    if (static_cast<int>(LHSMap.size()) != Lanes ||
        static_cast<int>(RHSMap.size()) != Lanes ||
        static_cast<int>(ResultMap.size()) != Lanes) {
      return Pairs;
    }
    for (int Lane = 0; Lane < Lanes; ++Lane) {
      Pairs.push_back(llvm::json::Object{{"vector_lane", Lane},
                                         {"result_lane", ResultMap[Lane]},
                                         {"lhs_lane", LHSMap[Lane]},
                                         {"rhs_lane", RHSMap[Lane]}});
    }
    return Pairs;
  }

  llvm::json::Array sourceRecordsForTokens(
      std::initializer_list<llvm::StringRef> Tokens) const {
    llvm::json::Array Records;
    for (size_t Index = 0; Index < MainLines.size(); ++Index) {
      llvm::StringRef Line(MainLines[Index]);
      if (!textContainsAny(Line, Tokens)) {
        continue;
      }
      Records.push_back(llvm::json::Object{
          {"line", static_cast<int>(Index + 1)}, {"source", trim(MainLines[Index])}});
    }
    return Records;
  }

  llvm::json::Array sourceRecordsForTokens(
      const std::vector<std::string> &Tokens) const {
    llvm::json::Array Records;
    for (size_t Index = 0; Index < MainLines.size(); ++Index) {
      llvm::StringRef Line(MainLines[Index]);
      if (!textContainsAny(Line, Tokens)) {
        continue;
      }
      Records.push_back(llvm::json::Object{
          {"line", static_cast<int>(Index + 1)}, {"source", trim(MainLines[Index])}});
    }
    return Records;
  }

  const SlpFunctionSummary *summaryByName(llvm::StringRef Name) const {
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      if (Summary.Name == Name) {
        return &Summary;
      }
    }
    return nullptr;
  }

  llvm::json::Array clonedArray(const llvm::json::Array &Array) const {
    llvm::json::Array Copy;
    for (const llvm::json::Value &Value : Array) {
      Copy.push_back(cloneJson(Value));
    }
    return Copy;
  }

  llvm::json::Array stringArray(const std::vector<std::string> &Values) const {
    llvm::json::Array Result;
    for (const std::string &Value : Values) {
      Result.push_back(Value);
    }
    return Result;
  }

  llvm::json::Object sourceRangeObject(const SlpFunctionSummary &Summary) const {
    return llvm::json::Object{{"file", Summary.File},
                              {"begin_line", static_cast<int>(Summary.StartLine)},
                              {"end_line", static_cast<int>(Summary.EndLine)}};
  }

  llvm::json::Object functionSemanticSummary(
      const SlpFunctionSummary &Summary) const {
    llvm::json::Array Roles;
    for (const std::string &Role : Summary.Roles) {
      Roles.push_back(Role);
    }
    llvm::json::Object Result{
        {"function", Summary.Name},
        {"signature", Summary.Signature},
        {"source_range", sourceRangeObject(Summary)},
        {"roles", std::move(Roles)},
        {"parameters", stringArray(Summary.Parameters)},
        {"calls", clonedArray(Summary.Calls)},
        {"conditions", clonedArray(Summary.Conditions)},
        {"called_functions", stringArray(Summary.CalledFunctions)},
    };
    if (!Summary.Opcode.empty()) {
      Result["opcode"] = Summary.Opcode;
    }
    return Result;
  }

  llvm::json::Array helperSummaries() const {
    llvm::json::Array Summaries;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      Summaries.push_back(functionSemanticSummary(Summary));
    }
    return Summaries;
  }

  llvm::json::Array callGraphEdges() const {
    llvm::json::Array Edges;
    std::set<std::pair<std::string, std::string>> SeenEdges;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      for (const std::string &Callee : Summary.CalledFunctions) {
        if (!summaryByName(Callee)) {
          continue;
        }
        auto Key = std::make_pair(Summary.Name, Callee);
        if (!SeenEdges.insert(Key).second) {
          continue;
        }
        Edges.push_back(llvm::json::Object{{"caller", Summary.Name},
                                           {"callee", Callee}});
      }
    }
    return Edges;
  }

  std::set<std::string>
  reachableFunctionNames(const std::vector<std::string> &Roots) const {
    std::set<std::string> Reachable;
    std::vector<std::string> Worklist;
    for (const std::string &Root : Roots) {
      if (!Root.empty() && summaryByName(Root) &&
          Reachable.insert(Root).second) {
        Worklist.push_back(Root);
      }
    }
    while (!Worklist.empty()) {
      std::string Current = Worklist.back();
      Worklist.pop_back();
      const SlpFunctionSummary *Summary = summaryByName(Current);
      if (!Summary) {
        continue;
      }
      for (const std::string &Callee : Summary->CalledFunctions) {
        if (!summaryByName(Callee)) {
          continue;
        }
        if (Reachable.insert(Callee).second) {
          Worklist.push_back(Callee);
        }
      }
    }
    return Reachable;
  }

  llvm::json::Array reachableHelperSummaries(
      const std::set<std::string> &Reachable) const {
    llvm::json::Array Summaries;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      if (Reachable.count(Summary.Name) == 0) {
        continue;
      }
      Summaries.push_back(functionSemanticSummary(Summary));
    }
    return Summaries;
  }

  llvm::json::Array reachableCallGraphEdges(
      const std::set<std::string> &Reachable) const {
    llvm::json::Array Edges;
    std::set<std::pair<std::string, std::string>> SeenEdges;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      if (Reachable.count(Summary.Name) == 0) {
        continue;
      }
      for (const std::string &Callee : Summary.CalledFunctions) {
        if (Reachable.count(Callee) == 0) {
          continue;
        }
        auto Key = std::make_pair(Summary.Name, Callee);
        if (!SeenEdges.insert(Key).second) {
          continue;
        }
        Edges.push_back(llvm::json::Object{{"caller", Summary.Name},
                                           {"callee", Callee}});
      }
    }
    return Edges;
  }

  llvm::json::Object sourceProgramGraph(
      const std::set<std::string> &Reachable) const {
    std::vector<o2t::sourcegraph::SourceFunctionSummary> Functions;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      o2t::sourcegraph::SourceFunctionSummary Function;
      Function.File = Summary.File;
      Function.Name = Summary.Name;
      Function.StartLine = Summary.StartLine;
      Function.EndLine = Summary.EndLine;
      Function.Signature = Summary.Signature;
      Function.Lines = Summary.Lines;
      Function.Roles = Summary.Roles;
      Function.Parameters = Summary.Parameters;
      Function.CalledFunctions = Summary.CalledFunctions;
      Function.CfgBlocks = Summary.CfgBlocks;
      Function.DataflowDefs = Summary.DataflowDefs;
      Function.DataflowUses = Summary.DataflowUses;
      for (const llvm::json::Value &CallValue : Summary.Calls) {
        const llvm::json::Object *Call = CallValue.getAsObject();
        if (!Call) {
          continue;
        }
        o2t::sourcegraph::SourceCall SourceCall;
        SourceCall.Callee = stringField(*Call, "callee");
        SourceCall.Line =
            static_cast<unsigned>(intField(*Call, "line", Summary.StartLine));
        SourceCall.AssignedSymbol = stringField(*Call, "assigned_symbol");
        if (const llvm::json::Array *Arguments = Call->getArray("arguments")) {
          for (const llvm::json::Value &ArgumentValue : *Arguments) {
            const llvm::json::Object *Argument = ArgumentValue.getAsObject();
            if (!Argument) {
              continue;
            }
            SourceCall.Arguments.push_back(
                o2t::sourcegraph::SourceCallArgument{
                    stringField(*Argument, "symbol"),
                    stringField(*Argument, "source"),
                    static_cast<unsigned>(
                        intField(*Argument, "line", SourceCall.Line)),
                    static_cast<unsigned>(intField(*Argument, "column", 0))});
          }
        }
        Function.Calls.push_back(std::move(SourceCall));
      }
      Functions.push_back(std::move(Function));
    }
    return o2t::sourcegraph::buildSourceProgramGraph(Functions,
                                                               Reachable);
  }

  llvm::json::Array reachableFunctionArray(
      const std::set<std::string> &Reachable) const {
    llvm::json::Array Functions;
    for (const std::string &Name : Reachable) {
      Functions.push_back(Name);
    }
    return Functions;
  }

  std::vector<std::string> callPath(llvm::StringRef Root,
                                    llvm::StringRef Target) const {
    if (Root.empty() || Target.empty() || !summaryByName(Root) ||
        !summaryByName(Target)) {
      return {};
    }
    if (Root == Target) {
      return {Root.str()};
    }
    std::vector<std::string> Worklist{Root.str()};
    std::map<std::string, std::string> Parent;
    std::set<std::string> Seen{Root.str()};
    for (size_t Index = 0; Index < Worklist.size(); ++Index) {
      const std::string Current = Worklist[Index];
      const SlpFunctionSummary *Summary = summaryByName(Current);
      if (!Summary) {
        continue;
      }
      for (const std::string &Callee : Summary->CalledFunctions) {
        if (!summaryByName(Callee) || !Seen.insert(Callee).second) {
          continue;
        }
        Parent[Callee] = Current;
        if (Callee == Target) {
          std::vector<std::string> Path{Target.str()};
          std::string Cursor = Target.str();
          while (Cursor != Root) {
            Cursor = Parent[Cursor];
            Path.push_back(Cursor);
          }
          std::reverse(Path.begin(), Path.end());
          return Path;
        }
        Worklist.push_back(Callee);
      }
    }
    return {};
  }

  llvm::json::Object sourceSliceContract(
      const SlpFunctionSummary *ControlRoot,
      const SlpFunctionSummary *Candidate,
      const SlpFunctionSummary *Legality,
      const SlpFunctionSummary *Profitability,
      const SlpFunctionSummary &Emitter,
      const SlpFunctionSummary &Replacement,
      bool HasExpandedLegality,
      bool HasLaneMapping,
      const llvm::json::Object &SourceProgramGraph) const {
    const std::vector<std::string> RequiredRoles{
        "candidate-tree", "legality", "profitability", "vector-emission",
        "scalar-replacement", "lane-mapping"};
    llvm::json::Array Required;
    llvm::json::Array ReachableRoles;
    llvm::json::Array MissingRoles;
    llvm::json::Array RolePaths;
    llvm::json::Array Checks;
    bool HasFailedCheck = false;
    const std::string Root =
        ControlRoot ? ControlRoot->Name : Emitter.Name;
    auto PathArray = [](const std::vector<std::string> &Path) {
      llvm::json::Array JsonPath;
      for (const std::string &Name : Path) {
        JsonPath.push_back(Name);
      }
      return JsonPath;
    };
    auto AddCheck = [&](llvm::StringRef Id, llvm::StringRef Kind,
                        llvm::StringRef Status, llvm::StringRef Role,
                        llvm::json::Object Detail) {
      llvm::json::Object Check{{"id", Id.str()},
                               {"kind", Kind.str()},
                               {"status", Status.str()}};
      if (!Role.empty()) {
        Check["role"] = Role.str();
      }
      if (Status == "passed") {
        Check["witness"] = std::move(Detail);
      } else {
        HasFailedCheck = true;
        Check["counterexample"] = std::move(Detail);
      }
      Checks.push_back(std::move(Check));
    };
    auto AddRole = [&](llvm::StringRef Role,
                       const SlpFunctionSummary *Target,
                       bool Available) {
      Required.push_back(Role.str());
      if (!Available || !Target) {
        MissingRoles.push_back(Role.str());
        AddCheck("role-reachability:" + Role.str(), "role-reachability",
                 "failed", Role,
                 llvm::json::Object{{"reason", "missing-role-evidence"},
                                    {"control_root_function", Root}});
        return;
      }
      std::vector<std::string> Path =
          ControlRoot ? callPath(Root, Target->Name)
                      : std::vector<std::string>{Target->Name};
      if (Path.empty()) {
        MissingRoles.push_back(Role.str());
        AddCheck("role-reachability:" + Role.str(), "role-reachability",
                 "failed", Role,
                 llvm::json::Object{{"reason", "role-not-reachable"},
                                    {"control_root_function", Root},
                                    {"function", Target->Name}});
        return;
      }
      ReachableRoles.push_back(Role.str());
      llvm::json::Array JsonPath = PathArray(Path);
      RolePaths.push_back(llvm::json::Object{
          {"role", Role.str()},
          {"function", Target->Name},
          {"path", PathArray(Path)},
      });
      AddCheck("role-reachability:" + Role.str(), "role-reachability",
               "passed", Role,
               llvm::json::Object{{"function", Target->Name},
                                  {"path", std::move(JsonPath)}});
    };
    AddRole("candidate-tree", Candidate, Candidate != nullptr);
    AddRole("legality", Legality, Legality != nullptr && HasExpandedLegality);
    AddRole("profitability", Profitability, Profitability != nullptr);
    AddRole("vector-emission", &Emitter, true);
    AddRole("scalar-replacement", &Replacement, true);
    AddRole("lane-mapping", &Emitter, HasLaneMapping);
    AddCheck("predicate-expands-legality", "predicate-expansion",
             HasExpandedLegality ? "passed" : "failed", "legality",
             HasExpandedLegality
                 ? llvm::json::Object{{"control_root_function", Root}}
                 : llvm::json::Object{{"reason", "missing-expanded-legality"},
                                      {"control_root_function", Root}});
    std::vector<std::string> EmitterPath =
        ControlRoot ? callPath(Root, Emitter.Name)
                    : std::vector<std::string>{Emitter.Name};
    AddCheck("emission-reachable-from-control-root", "control-flow",
             EmitterPath.empty() ? "failed" : "passed", "vector-emission",
             EmitterPath.empty()
                 ? llvm::json::Object{{"reason", "emitter-not-reachable"},
                                      {"control_root_function", Root},
                                      {"function", Emitter.Name}}
                 : llvm::json::Object{{"function", Emitter.Name},
                                      {"path", PathArray(EmitterPath)}});
    std::vector<std::string> ReplacementPath =
        ControlRoot ? callPath(Root, Replacement.Name)
                    : std::vector<std::string>{Replacement.Name};
    AddCheck("replacement-reachable-from-control-root", "control-flow",
             ReplacementPath.empty() ? "failed" : "passed",
             "scalar-replacement",
             ReplacementPath.empty()
                 ? llvm::json::Object{{"reason", "replacement-not-reachable"},
                                      {"control_root_function", Root},
                                      {"function", Replacement.Name}}
                 : llvm::json::Object{{"function", Replacement.Name},
                                      {"path", PathArray(ReplacementPath)}});
    AddCheck("lane-map-bound-to-emitter", "lane-map-binding",
             HasLaneMapping ? "passed" : "failed", "lane-mapping",
             HasLaneMapping
                 ? llvm::json::Object{{"function", Emitter.Name}}
                 : llvm::json::Object{{"reason", "invalid-lane-mapping"},
                                      {"function", Emitter.Name}});
    auto JsonArraySize = [](const llvm::json::Object &Object,
                            llvm::StringRef Name) -> size_t {
      if (const llvm::json::Array *Array = Object.getArray(Name)) {
        return Array->size();
      }
      return 0;
    };
    auto EdgeKinds = [](const llvm::json::Object &Object,
                        llvm::StringRef Name) {
      std::set<std::string> Kinds;
      if (const llvm::json::Array *Array = Object.getArray(Name)) {
        for (const llvm::json::Value &Value : *Array) {
          if (const llvm::json::Object *Edge = Value.getAsObject()) {
            const std::string Kind = stringField(*Edge, "kind");
            if (!Kind.empty()) {
              Kinds.insert(Kind);
            }
          }
        }
      }
      return Kinds;
    };
    auto GraphIds = [](const llvm::json::Object &Object) {
      std::set<std::string> Ids;
      if (const llvm::json::Array *Nodes = Object.getArray("nodes")) {
        for (const llvm::json::Value &Value : *Nodes) {
          if (const llvm::json::Object *Node = Value.getAsObject()) {
            const std::string Id = stringField(*Node, "id");
            if (!Id.empty()) {
              Ids.insert(Id);
            }
          }
        }
      }
      if (const llvm::json::Array *Blocks = Object.getArray("cfg_blocks")) {
        for (const llvm::json::Value &Value : *Blocks) {
          if (const llvm::json::Object *Block = Value.getAsObject()) {
            const std::string Id = stringField(*Block, "id");
            if (!Id.empty()) {
              Ids.insert(Id);
            }
          }
        }
      }
      if (const llvm::json::Array *Functions = Object.getArray("functions")) {
        for (const llvm::json::Value &Value : *Functions) {
          if (const llvm::json::Object *Function = Value.getAsObject()) {
            const std::string Entry = stringField(*Function, "entry");
            const std::string Exit = stringField(*Function, "exit");
            if (!Entry.empty()) {
              Ids.insert(Entry);
            }
            if (!Exit.empty()) {
              Ids.insert(Exit);
            }
          }
        }
      }
      return Ids;
    };
    auto EndpointFailures = [&](const llvm::json::Object &Object) {
      llvm::json::Array Failures;
      const std::set<std::string> Ids = GraphIds(Object);
      for (llvm::StringRef EdgeArrayName :
           {"cfg_edges", "dfg_edges", "call_edges"}) {
        const llvm::json::Array *Edges = Object.getArray(EdgeArrayName);
        if (!Edges) {
          continue;
        }
        for (const llvm::json::Value &Value : *Edges) {
          const llvm::json::Object *Edge = Value.getAsObject();
          if (!Edge) {
            continue;
          }
          for (llvm::StringRef Field : {"from", "to"}) {
            const std::string Endpoint = stringField(*Edge, Field);
            if (!Endpoint.empty() && Ids.count(Endpoint) != 0) {
              continue;
            }
            Failures.push_back(llvm::json::Object{
                {"edge_array", EdgeArrayName.str()},
                {"field", Field.str()},
                {"endpoint", Endpoint},
                {"kind", stringField(*Edge, "kind")}});
          }
        }
      }
      return Failures;
    };
    auto AccessPathFailures = [&](const llvm::json::Object &Object) {
      llvm::json::Array Failures;
      const std::set<std::string> Ids = GraphIds(Object);
      if (const llvm::json::Array *Facts =
              Object.getArray("access_path_facts")) {
        for (const llvm::json::Value &Value : *Facts) {
          const llvm::json::Object *Fact = Value.getAsObject();
          if (!Fact) {
            continue;
          }
          const std::string Node = stringField(*Fact, "node");
          if (Node.empty() || Ids.count(Node) == 0) {
            Failures.push_back(llvm::json::Object{
                {"kind", "dangling-access-path-fact-node"},
                {"node", Node},
                {"symbol", stringField(*Fact, "symbol")}});
          }
          if (stringField(*Fact, "symbol").empty() ||
              stringField(*Fact, "base").empty() ||
              JsonArraySize(*Fact, "segments") == 0) {
            Failures.push_back(llvm::json::Object{
                {"kind", "malformed-access-path-fact"},
                {"node", Node},
                {"symbol", stringField(*Fact, "symbol")}});
          }
        }
      }
      if (const llvm::json::Array *Edges = Object.getArray("dfg_edges")) {
        for (const llvm::json::Value &Value : *Edges) {
          const llvm::json::Object *Edge = Value.getAsObject();
          if (!Edge) {
            continue;
          }
          const llvm::json::Object *AccessPath =
              Edge->getObject("access_path");
          if (!AccessPath) {
            continue;
          }
          const std::string DefinitionMatch =
              stringField(*AccessPath, "definition_match");
          const bool Malformed =
              stringField(*AccessPath, "symbol") !=
                  stringField(*Edge, "symbol") ||
              stringField(*AccessPath, "base").empty() ||
              JsonArraySize(*AccessPath, "segments") == 0 ||
              (DefinitionMatch == "base-fallback" &&
               stringField(*AccessPath, "matched_base").empty());
          if (Malformed) {
            Failures.push_back(llvm::json::Object{
                {"kind", "malformed-access-path-edge"},
                {"from", stringField(*Edge, "from")},
                {"to", stringField(*Edge, "to")},
                {"symbol", stringField(*Edge, "symbol")}});
          }
        }
      }
      return Failures;
    };
    const bool GraphModelOk =
        stringField(SourceProgramGraph, "model") ==
        "llvm-pass-source-program-graph-v1";
    AddCheck("source-graph:present", "source-graph",
             GraphModelOk ? "passed" : "failed", "",
             GraphModelOk
                 ? llvm::json::Object{{"model", stringField(SourceProgramGraph,
                                                            "model")}}
                 : llvm::json::Object{{"reason",
                                        "unexpected-source-graph-model"},
                                      {"model", stringField(SourceProgramGraph,
                                                           "model")}});
    const size_t CfgBlockCount = JsonArraySize(SourceProgramGraph, "cfg_blocks");
    const bool CfgPrecisionOk =
        stringField(SourceProgramGraph, "cfg_precision") ==
            "clang-cfg-block-v1" &&
        CfgBlockCount > 0;
    AddCheck("source-graph:cfg-precision", "source-graph",
             CfgPrecisionOk ? "passed" : "failed", "",
             CfgPrecisionOk
                 ? llvm::json::Object{{"cfg_precision",
                                        stringField(SourceProgramGraph,
                                                    "cfg_precision")},
                                      {"cfg_blocks",
                                       static_cast<int>(CfgBlockCount)}}
                 : llvm::json::Object{{"reason", "missing-clang-cfg-blocks"},
                                      {"cfg_precision",
                                       stringField(SourceProgramGraph,
                                                   "cfg_precision")},
                                      {"cfg_blocks",
                                       static_cast<int>(CfgBlockCount)}});
    const size_t DfgEdgeCount = JsonArraySize(SourceProgramGraph, "dfg_edges");
    const bool DfgPrecisionOk =
        stringField(SourceProgramGraph, "dfg_precision") ==
            "clang-ast-decl-use-v1" &&
        DfgEdgeCount > 0;
    AddCheck("source-graph:dfg-precision", "source-graph",
             DfgPrecisionOk ? "passed" : "failed", "",
             DfgPrecisionOk
                 ? llvm::json::Object{{"dfg_precision",
                                        stringField(SourceProgramGraph,
                                                    "dfg_precision")},
                                      {"dfg_edges",
                                       static_cast<int>(DfgEdgeCount)}}
                 : llvm::json::Object{{"reason", "missing-clang-ast-dfg"},
                                      {"dfg_precision",
                                       stringField(SourceProgramGraph,
                                                   "dfg_precision")},
                                      {"dfg_edges",
                                       static_cast<int>(DfgEdgeCount)}});
    const std::set<std::string> DfgKinds =
        EdgeKinds(SourceProgramGraph, "dfg_edges");
    auto DfgKindArray = [&]() {
      llvm::json::Array Array;
      for (const std::string &Kind : DfgKinds) {
        Array.push_back(Kind);
      }
      return Array;
    };
    const bool InterproceduralDfgOk =
        SourceProgramGraph.getBoolean("interprocedural_dfg").value_or(false) &&
        DfgKinds.count("interproc-argument") != 0 &&
        DfgKinds.count("interproc-return") != 0;
    AddCheck("source-graph:interprocedural-dfg", "source-graph",
             InterproceduralDfgOk ? "passed" : "failed", "",
             InterproceduralDfgOk
                 ? llvm::json::Object{{"edge_kinds", DfgKindArray()}}
                 : llvm::json::Object{
                       {"reason", "missing-interprocedural-dfg-edges"},
                       {"interprocedural_dfg",
                        SourceProgramGraph.getBoolean("interprocedural_dfg")
                            .value_or(false)},
                       {"edge_kinds", DfgKindArray()}});
    llvm::json::Array Failures = EndpointFailures(SourceProgramGraph);
    const bool EndpointIntegrityOk = Failures.empty();
    AddCheck("source-graph:node-edge-integrity", "source-graph",
             EndpointIntegrityOk ? "passed" : "failed", "",
             EndpointIntegrityOk
                 ? llvm::json::Object{{"checked_edge_arrays",
                                        llvm::json::Array{
                                            "cfg_edges", "dfg_edges",
                                            "call_edges"}}}
                 : llvm::json::Object{{"reason", "dangling-edge-endpoints"},
                                      {"failures", std::move(Failures)}});
    llvm::json::Array AccessFailures = AccessPathFailures(SourceProgramGraph);
    const bool AccessPathOk = AccessFailures.empty();
    AddCheck("source-graph:access-path-provenance", "source-graph",
             AccessPathOk ? "passed" : "failed", "",
             AccessPathOk
                 ? llvm::json::Object{
                       {"access_path_facts",
                        static_cast<int>(
                            JsonArraySize(SourceProgramGraph,
                                          "access_path_facts"))}}
                 : llvm::json::Object{{"reason",
                                        "invalid-access-path-provenance"},
                                      {"failures",
                                       std::move(AccessFailures)}});
    return llvm::json::Object{
        {"model", "ast-source-slice-contract-v1"},
        {"status", HasFailedCheck ? "failed" : "complete"},
        {"control_root_function", Root},
        {"required_roles", std::move(Required)},
        {"reachable_roles", std::move(ReachableRoles)},
        {"missing_roles", std::move(MissingRoles)},
        {"role_paths", std::move(RolePaths)},
        {"checks", std::move(Checks)},
    };
  }

  llvm::json::Object slpPredicateFact(llvm::StringRef Fact,
                                      llvm::StringRef PredicateFamily,
                                      llvm::StringRef Source,
                                      unsigned Line,
                                      llvm::StringRef Subject = "") const {
    llvm::json::Object Entry{
        {"fact", Fact.str()},
        {"status", "observed"},
        {"predicate_family", PredicateFamily.str()},
        {"source", (PredicateFamily + " " + Source).str()},
        {"source_range",
         llvm::json::Object{{"begin_line", static_cast<int>(Line)},
                            {"begin_column", 1},
                            {"end_line", static_cast<int>(Line)},
                            {"end_column", 1}}},
    };
    if (!Subject.empty()) {
      Entry["subject"] = Subject.str();
    }
    return Entry;
  }

  llvm::json::Array slpPredicateProvenance(
      const llvm::json::Array &RoleEvidence,
      const llvm::json::Object &Contract,
      const llvm::json::Array &ReductionSources,
      const llvm::json::Object &ReductionResult,
      const SlpFunctionSummary &Emitter,
      llvm::StringRef TransactionKind) const {
    llvm::json::Array Facts;
    std::set<std::string> Seen;
    auto Add = [&](llvm::json::Object Fact) {
      const std::string Name = stringField(Fact, "fact");
      if (!Name.empty() && Seen.insert(Name).second) {
        Facts.push_back(std::move(Fact));
      }
    };
    for (const llvm::json::Value &Value : RoleEvidence) {
      const auto *Object = Value.getAsObject();
      if (!Object) {
        continue;
      }
      const std::string Role = stringField(*Object, "role");
      if (Role == "candidate-tree" || Role == "legality" ||
          Role == "vector-emission" || Role == "scalar-replacement") {
        Add(slpPredicateFact(Role, std::string("slp-role:") + Role,
                             stringField(*Object, "source"),
                             static_cast<unsigned>(
                                 Object->getInteger("line").value_or(Emitter.StartLine)),
                             stringField(*Object, "function")));
      }
    }
    if (const auto *Checks = Contract.getArray("checks")) {
      for (const llvm::json::Value &Value : *Checks) {
        const auto *Check = Value.getAsObject();
        if (!Check || stringField(*Check, "id") != "lane-map-bound-to-emitter" ||
            stringField(*Check, "status") != "passed") {
          continue;
        }
        Add(slpPredicateFact("lane-mapping",
                             "slp-contract:lane-map-bound-to-emitter",
                             "lane-map-bound-to-emitter",
                             Emitter.StartLine, Emitter.Name));
      }
    }
    if (TransactionKind == "slp-vectorize-reduction") {
      if (!ReductionSources.empty()) {
        const auto *First = ReductionSources[0].getAsObject();
        const std::optional<int64_t> Line =
            First ? First->getInteger("line") : std::nullopt;
        Add(slpPredicateFact(
            "reduction-source", "slp-reduction:source",
            First ? stringField(*First, "source") : "reduction source",
            Line ? static_cast<unsigned>(*Line) : Emitter.StartLine,
            Emitter.Name));
      }
      if (!ReductionResult.empty()) {
        Add(slpPredicateFact("reduction-result", "slp-reduction:result",
                             stringField(ReductionResult, "source"),
                             Emitter.StartLine, Emitter.Name));
      }
    }
    return Facts;
  }

  std::string expansionRoleForSummary(const SlpFunctionSummary &Summary) const {
    if (hasRole(Summary, "legality")) {
      return "legality";
    }
    if (hasRole(Summary, "profitability")) {
      return "profitability";
    }
    if (hasRole(Summary, "candidate-tree")) {
      const std::string LowerName = llvm::StringRef(Summary.Name).lower();
      if (LowerName.find("buildtree") == std::string::npos &&
          LowerName.find("discover") == std::string::npos &&
          LowerName.find("candidate") == std::string::npos &&
          LowerName.find("vectorize") == std::string::npos) {
        return "unknown-helper";
      }
      return "candidate-tree";
    }
    if (hasRole(Summary, "vector-emission")) {
      return "vector-emission";
    }
    if (hasRole(Summary, "scalar-replacement")) {
      return "scalar-replacement";
    }
    return "unknown-helper";
  }

  const SlpFunctionSummary *
  controlRootForTransaction(const SlpFunctionSummary &Emitter,
                            const SlpFunctionSummary &Replacement) const {
    const SlpFunctionSummary *Fallback = nullptr;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      const bool CallsEmitter = hasString(Summary.CalledFunctions, Emitter.Name);
      const bool CallsReplacement =
          hasString(Summary.CalledFunctions, Replacement.Name);
      if (CallsEmitter && CallsReplacement && !Summary.Conditions.empty()) {
        return &Summary;
      }
      if (CallsEmitter && CallsReplacement && !Fallback) {
        Fallback = &Summary;
      }
    }
    return Fallback;
  }

  llvm::json::Array predicateExpansion(
      const SlpFunctionSummary *ControlRoot) const {
    llvm::json::Array Expansion;
    if (!ControlRoot) {
      return Expansion;
    }
    std::set<std::string> Seen;
    for (const std::string &Callee : ControlRoot->CalledFunctions) {
      const SlpFunctionSummary *Summary = summaryByName(Callee);
      if (!Summary || !Seen.insert(Callee).second) {
        continue;
      }
      bool InPredicate = false;
      for (const llvm::json::Value &ConditionValue : ControlRoot->Conditions) {
        const auto *Condition = ConditionValue.getAsObject();
        if (!Condition) {
          continue;
        }
        const std::string Source = stringField(*Condition, "source");
        if (llvm::StringRef(Source).contains(Callee + "(")) {
          InPredicate = true;
          break;
        }
      }
      if (!InPredicate) {
        continue;
      }
      const std::string Role = expansionRoleForSummary(*Summary);
      if (Role == "vector-emission" || Role == "scalar-replacement") {
        continue;
      }
      llvm::json::Object Entry{
          {"function", Summary->Name},
          {"role", Role},
          {"signature", Summary->Signature},
          {"source_range", sourceRangeObject(*Summary)},
          {"calls", clonedArray(Summary->Calls)},
      };
      if (!Summary->Conditions.empty()) {
        Entry["conditions"] = clonedArray(Summary->Conditions);
      }
      Expansion.push_back(std::move(Entry));
    }
    return Expansion;
  }

  bool expansionHasRole(const llvm::json::Array &Expansion,
                        llvm::StringRef Role) const {
    for (const llvm::json::Value &Value : Expansion) {
      const auto *Object = Value.getAsObject();
      if (Object && stringField(*Object, "role") == Role) {
        return true;
      }
    }
    return false;
  }

  llvm::json::Array roleSliceEntries(const llvm::json::Array &RoleEvidence,
                                     bool Forward) const {
    llvm::json::Array Entries;
    for (const llvm::json::Value &Value : RoleEvidence) {
      const auto *Object = Value.getAsObject();
      if (!Object) {
        continue;
      }
      const std::string Role = stringField(*Object, "role");
      const bool IsForward = Role == "scalar-replacement";
      if (IsForward != Forward) {
        continue;
      }
      Entries.push_back(llvm::json::Object{
          {"role", Role},
          {"function", stringField(*Object, "function")},
          {"line", static_cast<int>(*Object->getInteger("line"))},
          {"source", stringField(*Object, "source")},
      });
    }
    return Entries;
  }

  llvm::json::Object rewriteSlice(
      const SlpFunctionSummary &Emitter, const SlpFunctionSummary &Replacement,
      const llvm::json::Array &RoleEvidence,
      const std::set<std::string> &Reachable,
      const SlpFunctionSummary *ControlRoot,
      const llvm::json::Array &PredicateExpansion,
      const llvm::json::Object &Completeness,
      const llvm::json::Object &Contract) const {
    llvm::json::Array OperandPacking;
    for (const llvm::json::Value &Value : Emitter.Calls) {
      const auto *Object = Value.getAsObject();
      if (!Object) {
        continue;
      }
      const std::string Callee = stringField(*Object, "callee");
      if (Callee != "packOperand" && Callee != "buildPack" &&
          Callee.find("build") != 0) {
        continue;
      }
      OperandPacking.push_back(llvm::json::Object{
          {"kind", "operand-pack"},
          {"callee", Callee},
          {"line", static_cast<int>(*Object->getInteger("line"))},
          {"source", stringField(*Object, "source")},
      });
    }
    llvm::json::Object Slice{
        {"model", "ast-rewrite-slice-v1"},
        {"status", "complete"},
        {"root_function", Emitter.Name},
        {"control_root_function",
         ControlRoot ? ControlRoot->Name : Emitter.Name},
        {"predicate_expansion", clonedArray(PredicateExpansion)},
        {"completeness", cloneJsonObject(Completeness)},
        {"contract", cloneJsonObject(Contract)},
        {"rewrite_site",
         llvm::json::Object{{"function", Emitter.Name},
                            {"line", static_cast<int>(Emitter.StartLine)},
                            {"source", sourceLineForToken(Emitter.Lines,
                                                         VectorEmissionTokens)}}},
        {"backward_slice", roleSliceEntries(RoleEvidence, false)},
        {"operand_packing", std::move(OperandPacking)},
        {"forward_slice", roleSliceEntries(RoleEvidence, true)},
        {"reachable_functions", reachableFunctionArray(Reachable)},
        {"reachable_helper_summaries", reachableHelperSummaries(Reachable)},
        {"replacement_site",
         llvm::json::Object{{"function", Replacement.Name},
                            {"line", static_cast<int>(Replacement.StartLine)},
                            {"source", sourceLineForToken(
                                           Replacement.Lines,
                                           {"replaceScalarUses",
                                            "replaceExternalUses",
                                            "replaceAllUsesWith",
                                            "ExternalUses"})}}},
        {"call_graph", reachableCallGraphEdges(Reachable)},
    };
    return Slice;
  }

  llvm::json::Object fpReductionPolicy(
      llvm::StringRef Opcode, const llvm::json::Object &LaneMapping) const {
    if (Opcode != "fadd" && Opcode != "fmul") {
      return llvm::json::Object{};
    }
    llvm::json::Array Evidence = sourceRecordsForTokens(
        {"AllowReassoc", "hasAllowReassoc", "setAllowReassoc",
         "FastMathFlags", "setFastMathFlags", "reassoc", "fast", "setFast",
         "unordered", "Unordered", "isOrdered", "IsOrdered"});
    if (Evidence.empty()) {
      return llvm::json::Object{};
    }
    std::string EvidenceText;
    for (const llvm::json::Value &Value : Evidence) {
      if (const auto *Object = Value.getAsObject()) {
        EvidenceText += stringField(*Object, "source");
        EvidenceText += "\n";
      }
    }
    EvidenceText = llvm::StringRef(EvidenceText).lower();
    std::string Semantics = "fast-math-fp-reduction";
    if (EvidenceText.find("reassoc") != std::string::npos) {
      Semantics = "relaxed-reassoc";
    } else if (EvidenceText.find("unordered") != std::string::npos ||
               EvidenceText.find("isordered") != std::string::npos) {
      Semantics = "unordered-fp-reduction";
    }
    return llvm::json::Object{{"kind", "fp-reduction-policy"},
                              {"semantics", Semantics},
                              {"operation", Opcode.str()},
                              {"element_type", "fp32"},
                              {"lane_mapping", cloneObject(LaneMapping)},
                              {"evidence", std::move(Evidence)}};
  }

  std::optional<llvm::json::Value> slpTransactionFinding() const {
    const SlpFunctionSummary *Candidate = firstSummaryWithRole("candidate-tree");
    const SlpFunctionSummary *Legality = firstSummaryWithRole("legality");
    const SlpFunctionSummary *Emitter = firstSummaryWithRole("vector-emission");
    const SlpFunctionSummary *Replacement =
        firstSummaryWithRole("scalar-replacement");
    if (!Candidate || !Emitter || !Replacement || Emitter->Opcode.empty()) {
      return std::nullopt;
    }
    const std::string TransactionKind =
        slpTransactionKindForOpcode(Emitter->Opcode, Emitter->Body);
    llvm::json::Array RoleEvidence;
    std::set<std::string> SeenRoles;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      for (const llvm::json::Value &Value : Summary.Evidence) {
        const auto *Object = Value.getAsObject();
        if (!Object) {
          continue;
        }
        std::string Role = stringField(*Object, "role");
        if (Role.empty() || SeenRoles.count(Role) != 0) {
          continue;
        }
        SeenRoles.insert(Role);
        RoleEvidence.push_back(cloneJson(Value));
      }
    }
    int Lanes = discoveredLaneCount(*Candidate);
    llvm::json::Object ScalableInfo = scalableInfo(Lanes);
    const bool IsScalable =
        ScalableInfo.getBoolean("scalable").value_or(false);
    if (IsScalable) {
      Lanes = static_cast<int>(*ScalableInfo.getInteger("base_lanes"));
    }
    llvm::json::Object LaneMapping = globalLaneMapping(Lanes);
    llvm::json::Object LHS = operandMapping(*Emitter, "lhs", LaneMapping, Lanes);
    llvm::json::Object RHS =
        TransactionKind == "slp-vectorize-reduction"
            ? llvm::json::Object{}
            : operandMapping(*Emitter, "rhs", LaneMapping, Lanes);
    if (TransactionKind == "slp-vectorize-reduction") {
      LaneMapping = cloneObject(LHS);
      LaneMapping.erase("pack_source");
    } else if (mapFromObject(LHS) == mapFromObject(RHS)) {
      LaneMapping = cloneObject(LHS);
      LaneMapping.erase("pack_source");
    }
    llvm::json::Object ResultMapping =
        TransactionKind == "slp-vectorize-reduction"
            ? llvm::json::Object{}
            : resultMapping(*Replacement, LaneMapping, Lanes);

    llvm::json::Array OpcodeSources;
    std::vector<std::string> ConsistencyErrors;
    llvm::json::Array ReductionSources =
        TransactionKind == "slp-vectorize-reduction"
            ? sourceRecordsForTokens(ReductionTokens)
            : llvm::json::Array{};
    llvm::json::Object ReductionResult =
        TransactionKind == "slp-vectorize-reduction"
            ? llvm::json::Object{{"kind", "scalar-reduction-result"},
                                 {"source", Emitter->Signature}}
            : llvm::json::Object{};
    for (const llvm::json::Value &Value : RoleEvidence) {
      const auto *Object = Value.getAsObject();
      if (!Object || !Object->getString("opcode")) {
        continue;
      }
      llvm::json::Object Source{{"role", stringField(*Object, "role")},
                                {"function", stringField(*Object, "function")},
                                {"line", static_cast<int>(*Object->getInteger("line"))},
                                {"opcode", stringField(*Object, "opcode")},
                                {"source", stringField(*Object, "source")}};
      if (stringField(Source, "role") != "vector-emission" &&
          stringField(Source, "opcode") != Emitter->Opcode) {
        ConsistencyErrors.push_back("opcode-mismatch:" +
                                    stringField(Source, "role") + ":" +
                                    stringField(Source, "opcode") +
                                    "!=vector-emission:" + Emitter->Opcode);
      }
      OpcodeSources.push_back(std::move(Source));
    }
    if (IsScalable) {
      if (TransactionKind != "slp-vectorize-reduction" &&
          TransactionKind != "slp-vectorize-binop" &&
          TransactionKind != "slp-vectorize-minmax") {
        ConsistencyErrors.push_back("unsupported-scalable-transaction");
      }
      if (Lanes <= 0) {
        ConsistencyErrors.push_back("unsupported-scalable-base-lanes");
      }
    } else if (Lanes != 2 && Lanes != 4 && Lanes != 8 && Lanes != 16 &&
               Lanes != 32 && Lanes != 64) {
      ConsistencyErrors.push_back("unsupported-lane-count:" +
                                  std::to_string(Lanes));
    }
    llvm::json::Object FPPolicy =
        fpReductionPolicy(Emitter->Opcode, LaneMapping);
    if (TransactionKind == "slp-vectorize-reduction") {
      for (const std::string &Reason :
           slpReductionUnsupportedReasons(Emitter->Body)) {
        ConsistencyErrors.push_back(Reason);
      }
      if (Emitter->Opcode == "fadd" || Emitter->Opcode == "fmul") {
        std::vector<int> Identity;
        for (int I = 0; I < Lanes; ++I)
          Identity.push_back(I);
        if (mapFromObject(LaneMapping) != Identity && FPPolicy.empty())
          ConsistencyErrors.push_back("unsupported-reduction-fp-permutation");
      }
      std::string EmitterBodyLower = llvm::StringRef(Emitter->Body).lower();
      if (IsScalable &&
          (EmitterBodyLower.find("createzext") != std::string::npos ||
           EmitterBodyLower.find("createsext") != std::string::npos ||
           EmitterBodyLower.find("createzextortrunc") != std::string::npos ||
           EmitterBodyLower.find("createtrunc") != std::string::npos ||
           EmitterBodyLower.find("zext") != std::string::npos ||
           EmitterBodyLower.find("sext") != std::string::npos ||
           EmitterBodyLower.find("trunc") != std::string::npos) &&
          stringField(slpReductionWidthInfo(Emitter->Body), "status") !=
              "complete") {
        ConsistencyErrors.push_back("unsupported-scalable-widening-reduction");
      }
      if (ReductionSources.empty()) {
        ConsistencyErrors.push_back("missing-reduction-source");
      }
      if (ReductionResult.empty()) {
        ConsistencyErrors.push_back("missing-reduction-result");
      }
    }
    auto AddMapError = [&](const std::string &Prefix,
                           const llvm::json::Object &Mapping) {
      std::string Error = validateLaneMapping(Mapping, Lanes);
      if (!Error.empty()) {
        ConsistencyErrors.push_back(Prefix.empty() ? Error : Prefix + Error);
      }
    };
    AddMapError("", LaneMapping);
    AddMapError("lhs-", LHS);
    if (TransactionKind != "slp-vectorize-reduction") {
      AddMapError("rhs-", RHS);
    }
    if (TransactionKind != "slp-vectorize-reduction" &&
        mapFromObject(LHS) != mapFromObject(RHS)) {
      ConsistencyErrors.push_back("operand-lane-map-mismatch");
    }
    if (const auto *Builder = LHS.getObject("pack_builder")) {
      if (stringField(*Builder, "status") != "complete") {
        ConsistencyErrors.push_back("incomplete-pack-builder");
      }
    }
    if (const auto *Builder = RHS.getObject("pack_builder")) {
      if (stringField(*Builder, "status") != "complete") {
        ConsistencyErrors.push_back("incomplete-pack-builder");
      }
    }
    if (const auto *Source = ResultMapping.getObject("replacement_source")) {
      if (stringField(*Source, "status") != "complete") {
        ConsistencyErrors.push_back("incomplete-result-lane-mapping");
      }
    }
    std::string ResultError;
    if (TransactionKind != "slp-vectorize-reduction") {
      ResultError = validateLaneMapping(ResultMapping, Lanes);
      if (!ResultError.empty()) {
        ConsistencyErrors.push_back("result-" + ResultError);
      }
      if (ResultError.empty() && mapFromObject(LHS) == mapFromObject(RHS) &&
          mapFromObject(ResultMapping) != mapFromObject(LHS)) {
        ConsistencyErrors.push_back("unsupported-lane-pairing");
      }
    }

    llvm::json::Array Functions;
    std::set<std::string> FunctionNames;
    for (const SlpFunctionSummary &Summary : SlpSummaries) {
      if (FunctionNames.insert(Summary.Name).second) {
        Functions.push_back(Summary.Name);
      }
    }
    llvm::json::Array ScalarPairs =
        TransactionKind == "slp-vectorize-reduction"
            ? llvm::json::Array{}
            : scalarLanePairs(LHS, RHS, ResultMapping, Lanes);
    llvm::json::Object TransactionGraph;
    if ((TransactionKind == "slp-vectorize-binop" ||
         TransactionKind == "slp-vectorize-minmax") &&
        ConsistencyErrors.empty() &&
        validateLaneMapping(LaneMapping, Lanes).empty() &&
        validateLaneMapping(ResultMapping, Lanes).empty() &&
        mapFromObject(ResultMapping) == mapFromObject(LaneMapping)) {
      TransactionGraph = transactionGraph(Replacement ? *Replacement : *Emitter,
                                          LaneMapping, ResultMapping, Lanes);
    }
    llvm::json::Array TransactionGraphAbsentReasons;
    llvm::json::Array TransactionGraphAbsentDiagnostics;
    auto AddTransactionGraphAbsentDiagnostic =
        [&](const std::string &Reason, const std::string &Role,
            const std::string &Source, const std::string &Detail,
            const std::string &Temp = "") {
          if (Reason.empty()) {
            return;
          }
          llvm::json::Object Diagnostic{
              {"reason", Reason},
              {"role", Role},
              {"source", trim(Source)}};
          if (!Detail.empty()) {
            Diagnostic["detail"] = Detail;
          }
          if (!Temp.empty()) {
            Diagnostic["temp"] = Temp;
          }
          TransactionGraphAbsentDiagnostics.push_back(std::move(Diagnostic));
        };
    if (const std::string Reason =
            stringField(TransactionGraph, "__helper_slice_absent_reason");
        !Reason.empty()) {
      if (const auto *Diagnostics =
              TransactionGraph.getArray("__helper_slice_absent_diagnostics")) {
        for (const llvm::json::Value &Diagnostic : *Diagnostics) {
          TransactionGraphAbsentDiagnostics.push_back(cloneJson(Diagnostic));
        }
      }
      TransactionGraph = llvm::json::Object{};
      TransactionGraphAbsentReasons.push_back(Reason);
    }
    if (const std::string Reason =
            stringField(TransactionGraph, "__transaction_graph_absent_reason");
        !Reason.empty()) {
      if (const auto *Diagnostics =
              TransactionGraph.getArray("__transaction_graph_absent_diagnostics")) {
        for (const llvm::json::Value &Diagnostic : *Diagnostics) {
          TransactionGraphAbsentDiagnostics.push_back(cloneJson(Diagnostic));
        }
      }
      TransactionGraph = llvm::json::Object{};
      TransactionGraphAbsentReasons.push_back(Reason);
    }
    auto GraphHasMemoryPack = [](const llvm::json::Object &Graph) {
      if (const auto *Operands = Graph.getArray("operands")) {
        for (const llvm::json::Value &Value : *Operands) {
          const auto *Operand = Value.getAsObject();
          if (Operand && stringField(*Operand, "kind") == "memory-pack") {
            return true;
          }
        }
      }
      return false;
    };
    auto GraphHasIncompleteMemoryPack = [](const llvm::json::Object &Graph) {
      if (const auto *Operands = Graph.getArray("operands")) {
        for (const llvm::json::Value &Value : *Operands) {
          const auto *Operand = Value.getAsObject();
          if (Operand && stringField(*Operand, "kind") == "memory-pack" &&
              stringField(*Operand, "memory_safety_status") != "complete") {
            return true;
          }
        }
      }
      return false;
    };
    auto GraphHasStoreSink = [](const llvm::json::Object &Graph) {
      if (const auto *Sinks = Graph.getArray("store_sinks")) {
        return !Sinks->empty();
      }
      return false;
    };
    auto GraphHasUnresolvedMemoryAlias = [](const llvm::json::Object &Graph) {
      if (const auto *Conditions = Graph.getArray("memory_alias_conditions")) {
        for (const llvm::json::Value &Value : *Conditions) {
          const auto *Condition = Value.getAsObject();
          if (Condition && stringField(*Condition, "status") != "complete") {
            return true;
          }
        }
      }
      return false;
    };
    auto SourceHasVolatileOrAtomicMemory = [&]() {
      llvm::StringRef Source(MainSource);
      return Source.contains("CreateVolatileLoad") ||
             Source.contains("CreateAtomicLoad") ||
             Source.contains("isVolatile") || Source.contains("isAtomic") ||
             Source.contains(" volatile") || Source.contains(" atomic");
    };
    const std::map<std::string, long long> SourceIntConstants =
        parseStaticIntConstants(MainSource);
    auto SourceLaneIndexForText =
        [&](const std::string &Text) -> std::optional<int> {
      return evalLaneIndexExpr(Text, SourceIntConstants);
    };
    auto SourceHasNonContiguousLoadOffsets = [&](int LaneCount) {
      std::vector<int> Offsets;
      std::regex LoadPattern(
          R"cv(CreateLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   LoadPattern),
           End;
           It != End; ++It) {
        if (std::optional<int> Index = SourceLaneIndexForText((*It)[1].str())) {
          Offsets.push_back(*Index);
        }
      }
      if (static_cast<int>(Offsets.size()) < LaneCount) {
        return false;
      }
      for (int Lane = 0; Lane < LaneCount; ++Lane) {
        if (Offsets[Lane] != Lane) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasDuplicateLoadOffsets = [&](int LaneCount) {
      std::vector<int> Offsets;
      std::regex LoadPattern(
          R"cv(CreateLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   LoadPattern),
           End;
           It != End; ++It) {
        if (std::optional<int> Index = SourceLaneIndexForText((*It)[1].str())) {
          Offsets.push_back(*Index);
        }
      }
      if (static_cast<int>(Offsets.size()) < LaneCount) {
        return false;
      }
      std::set<int> Seen;
      for (int Lane = 0; Lane < LaneCount; ++Lane) {
        if (!Seen.insert(Offsets[Lane]).second) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasVariableLoadIndex = [&]() {
      std::regex LoadWithIndex(
          R"cv(CreateLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   LoadWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[1].str());
        if (!SourceLaneIndexForText(IndexText)) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasVariableMaskIndex = [&]() {
      std::regex MaskedLoadWithIndex(
          R"cv(CreateMaskedLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   MaskedLoadWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[1].str());
        if (!SourceLaneIndexForText(IndexText) &&
            !isSafeSymbolicMaskIndexText(IndexText, SourceIntConstants)) {
          return true;
        }
      }
      std::regex MaskedStoreWithIndex(
          R"cv(CreateMaskedStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   MaskedStoreWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[1].str());
        if (!SourceLaneIndexForText(IndexText) &&
            !isSafeSymbolicMaskIndexText(IndexText, SourceIntConstants)) {
          return true;
        }
      }
      std::regex GuardedLoadWithIndex(
          R"cv(if\s*\(\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\)\s*\{?\s*[A-Za-z_]\w*\s*=\s*(?:[A-Za-z_]\w*\s*\.\s*)?CreateLoad\s*\()cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   GuardedLoadWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[1].str());
        if (!SourceLaneIndexForText(IndexText) &&
            !isSafeSymbolicMaskIndexText(IndexText, SourceIntConstants)) {
          return true;
        }
      }
      std::regex GuardedStoreWithIndex(
          R"cv(if\s*\(\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\)\s*\{?\s*(?:[A-Za-z_]\w*\s*\.\s*)?CreateStore\s*\()cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   GuardedStoreWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[1].str());
        if (!SourceLaneIndexForText(IndexText)) {
          return true;
        }
      }
      return false;
    };
    auto FirstVariableMaskIndexDiagnostic =
        [&]() -> std::tuple<std::string, std::string, std::string> {
      std::regex MaskedLoadWithIndex(
          R"cv(CreateMaskedLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   MaskedLoadWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[2].str());
        if (!SourceLaneIndexForText(IndexText) &&
            !isSafeSymbolicMaskIndexText(IndexText, SourceIntConstants)) {
          return {It->str(), (*It)[1].str(), "memory-pack"};
        }
      }
      std::regex MaskedStoreWithIndex(
          R"cv(CreateMaskedStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   MaskedStoreWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[2].str());
        if (!SourceLaneIndexForText(IndexText) &&
            !isSafeSymbolicMaskIndexText(IndexText, SourceIntConstants)) {
          return {It->str(), (*It)[1].str(), "memory-store"};
        }
      }
      return {MainSource, "", "memory-pack"};
    };
    auto SourceHasStoreTempMemoryMask = [&]() {
      std::regex TempMaskedStore(
          R"cv(CreateMaskedStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*\))cv");
      std::regex GuardedStoreTemp(
          R"cv(if\s*\(\s*[A-Za-z_]\w*\s*\)\s*\{?\s*(?:[A-Za-z_]\w*\s*\.\s*)?CreateStore\s*\()cv");
      return std::regex_search(MainSource, TempMaskedStore) ||
             std::regex_search(MainSource, GuardedStoreTemp);
    };
    auto SourceHasMissingMaskedLoadPassthru = [&]() {
      if (MainSource.find("CreateMaskedLoad") == std::string::npos) {
        return false;
      }
      std::regex FullMaskedLoad(
          R"cv(CreateMaskedLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*\))cv");
      std::regex TempMaskLoad(
          R"cv(CreateMaskedLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*\))cv");
      return !std::regex_search(MainSource, FullMaskedLoad) &&
             !std::regex_search(MainSource, TempMaskLoad);
    };
    auto FirstMissingMaskedLoadPassthruDiagnostic =
        [&]() -> std::pair<std::string, std::string> {
      std::regex MaskedLoad(
          R"cv(CreateMaskedLoad\s*\(\s*[^,]+,\s*[^,]+,\s*([^)]+)\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   MaskedLoad),
           End;
           It != End; ++It) {
        std::string Temp = trim((*It)[1].str());
        std::smatch Name;
        if (!std::regex_match(Temp, Name, std::regex(R"cv([A-Za-z_]\w*)cv"))) {
          Temp.clear();
        }
        return {It->str(), Temp};
      }
      return {MainSource, ""};
    };
    auto SourceHasTempMemoryMask = [&]() {
      std::regex TempMaskedLoad(
          R"cv(CreateMaskedLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*\))cv");
      std::regex TempMaskedStore(
          R"cv(CreateMaskedStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*[A-Za-z_]\w*\s*\))cv");
      return std::regex_search(MainSource, TempMaskedLoad) ||
             std::regex_search(MainSource, TempMaskedStore);
    };
    auto TempMaskUses = [&]() {
      std::set<std::string> Temps;
      std::regex TempMaskedLoad(
          R"cv(CreateMaskedLoad\s*\(\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*([A-Za-z_]\w*)\s*,)cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   TempMaskedLoad),
           End;
           It != End; ++It) {
        Temps.insert((*It)[1].str());
      }
      std::regex TempMaskedStore(
          R"cv(CreateMaskedStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*[^\]]+\s*\]\s*,\s*([A-Za-z_]\w*)\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   TempMaskedStore),
           End;
           It != End; ++It) {
        Temps.insert((*It)[1].str());
      }
      return Temps;
    };
    auto SourceHasConflictingMaskAssignment = [&]() {
      const std::set<std::string> UsedTemps = TempMaskUses();
      std::map<std::string, int> AssignmentCounts;
      std::regex ConcreteMaskAssign(
          R"cv((?:(?:Value|auto)\s*\*\s*(?:const\s*)?)?([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*(?:\.|->)\s*Create(?:ICmp|And|Or|Xor|Not|Select)\s*\()cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   ConcreteMaskAssign),
           End;
           It != End; ++It) {
        const std::string Temp = (*It)[1].str();
        if (UsedTemps.count(Temp) != 0 && ++AssignmentCounts[Temp] > 1) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasIncompleteBranchMaskAssignment = [&]() {
      const std::set<std::string> UsedTemps = TempMaskUses();
      std::regex BranchAssignment(
          R"cv(if\s*\(\s*[^;){}]+?\s*\)\s*(?:\{\s*)?([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\s*;\s*(?:\}\s*)?)cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   BranchAssignment),
           End;
           It != End; ++It) {
        if (UsedTemps.count((*It)[1].str()) == 0) {
          continue;
        }
        std::string After = MainSource.substr(static_cast<size_t>(It->position() + It->length()));
        After = trim(After);
        if (!llvm::StringRef(After).starts_with("else")) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasUnresolvedMaskHelperCall = [&]() {
      const std::set<std::string> UsedTemps = TempMaskUses();
      std::regex HelperMaskAssign(
          R"cv((?:(?:Value|auto)\s*\*\s*(?:const\s*)?)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\([^;]*\)\s*;)cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   HelperMaskAssign),
           End;
           It != End; ++It) {
        const std::string Temp = (*It)[1].str();
        const std::string Helper = (*It)[2].str();
        if (UsedTemps.count(Temp) == 0 || Helper == "CreateICmp" ||
            Helper == "CreateAnd" || Helper == "CreateOr" ||
            Helper == "CreateXor" || Helper == "CreateNot" ||
            Helper == "CreateSelect") {
          continue;
        }
        return true;
      }
      return false;
    };
    auto SourceMaskBlockerDetail = [&]() -> std::string {
      if (SourceHasConflictingMaskAssignment()) {
        return "conflicting-assignment";
      }
      if (SourceHasIncompleteBranchMaskAssignment()) {
        return "incomplete-branch-assignment";
      }
      if (SourceHasUnresolvedMaskHelperCall()) {
        return "unresolved-helper-call";
      }
      return "unknown-mask-expression";
    };
    auto SourceHasInterveningStore = [&]() {
      return MainSource.find("CreateStore") != std::string::npos;
    };
    auto SourceHasStore = [&]() {
      return MainSource.find("CreateStore") != std::string::npos ||
             MainSource.find("CreateMaskedStore") != std::string::npos ||
             MainSource.find("CreateVolatileStore") != std::string::npos ||
             MainSource.find("CreateAtomicStore") != std::string::npos;
    };
    auto SourceHasVolatileOrAtomicStore = [&]() {
      llvm::StringRef Source(MainSource);
      return Source.contains("CreateVolatileStore") ||
             Source.contains("CreateAtomicStore") ||
             Source.contains("isVolatile") || Source.contains("isAtomic") ||
             Source.contains(" volatile") || Source.contains(" atomic");
    };
    auto SourceHasVariableStoreIndex = [&]() {
      std::regex StoreWithIndex(
          R"cv(CreateStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   StoreWithIndex),
           End;
           It != End; ++It) {
        const std::string IndexText = trim((*It)[1].str());
        if (!SourceLaneIndexForText(IndexText)) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasDuplicateStoreOffsets = [&](int LaneCount) {
      std::vector<int> Offsets;
      std::regex StorePattern(
          R"cv(CreateStore\s*\(\s*[A-Za-z_]\w*\s*,\s*[A-Za-z_]\w*\s*\[\s*([^\]]+)\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   StorePattern),
           End;
           It != End; ++It) {
        if (std::optional<int> Index = SourceLaneIndexForText((*It)[1].str())) {
          Offsets.push_back(*Index);
        }
      }
      if (static_cast<int>(Offsets.size()) < LaneCount) {
        return false;
      }
      std::set<int> Seen;
      for (int Lane = 0; Lane < LaneCount; ++Lane) {
        if (!Seen.insert(Offsets[Lane]).second) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasAmbiguousStoreBase = [&](int LaneCount) {
      std::vector<std::string> Bases;
      std::regex StorePattern(
          R"cv(CreateStore\s*\(\s*[A-Za-z_]\w*\s*,\s*([A-Za-z_]\w*)\s*\[\s*[^\]]+\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   StorePattern),
           End;
           It != End; ++It) {
        Bases.push_back((*It)[1].str());
      }
      if (static_cast<int>(Bases.size()) < LaneCount) {
        return false;
      }
      for (int Lane = 1; Lane < LaneCount; ++Lane) {
        if (Bases[Lane] != Bases[0]) {
          return true;
        }
      }
      return false;
    };
    auto SourceHasUnknownMemoryEffectCall = [&]() {
      return MainSource.find("touchMemory") != std::string::npos ||
             MainSource.find("unknownMemoryEffect") != std::string::npos ||
             MainSource.find("mayWriteMemory") != std::string::npos;
    };
    auto SourceHasPointerMutation = [&]() {
      return MainSource.find("++Base") != std::string::npos ||
             MainSource.find("Base++") != std::string::npos ||
             MainSource.find("Base =") != std::string::npos ||
             MainSource.find("Base +=") != std::string::npos;
    };
    auto SourceHasAmbiguousMemoryBase = [&](int LaneCount) {
      std::vector<std::string> Bases;
      std::regex LoadPattern(
          R"cv(CreateLoad\s*\(\s*([A-Za-z_]\w*)\s*\[\s*[^\]]+\s*\]\s*\))cv");
      for (std::sregex_iterator It(MainSource.begin(), MainSource.end(),
                                   LoadPattern),
           End;
           It != End; ++It) {
        Bases.push_back((*It)[1].str());
      }
      if (static_cast<int>(Bases.size()) < LaneCount) {
        return false;
      }
      for (int Lane = 1; Lane < LaneCount; ++Lane) {
        if (Bases[Lane] != Bases[0]) {
          return true;
        }
      }
      return false;
    };
    if (TransactionGraphAbsentReasons.empty()) {
    if (IsScalable && GraphHasIncompleteMemoryPack(TransactionGraph)) {
      TransactionGraph = llvm::json::Object{};
      TransactionGraphAbsentReasons.push_back("unsupported-scalable-memory-graph");
    } else if (GraphHasMemoryPack(TransactionGraph) &&
               SourceHasVolatileOrAtomicMemory()) {
      TransactionGraph = llvm::json::Object{};
      TransactionGraphAbsentReasons.push_back("unsupported-volatile-or-atomic-memory");
    } else if (GraphHasMemoryPack(TransactionGraph) &&
               GraphHasStoreSink(TransactionGraph) &&
               GraphHasUnresolvedMemoryAlias(TransactionGraph)) {
      TransactionGraph = llvm::json::Object{};
      TransactionGraphAbsentReasons.push_back("unsupported-unresolved-memory-alias");
    } else if (TransactionGraph.empty() &&
               Emitter->Body.find("CreateShuffleVector") != std::string::npos) {
      TransactionGraphAbsentReasons.push_back("unresolved-shuffle-mask");
    } else if (TransactionGraph.empty() &&
               (Emitter->Body.find("CreateExtractElement") != std::string::npos ||
                Emitter->Body.find("CreateInsertElement") != std::string::npos)) {
      TransactionGraphAbsentReasons.push_back("unresolved-extract-insert-index");
    } else if (TransactionGraph.empty() &&
               (MainSource.find("CreateLoad") != std::string::npos ||
                MainSource.find("CreateMaskedLoad") != std::string::npos ||
                SourceHasVolatileOrAtomicMemory())) {
      if (SourceHasVolatileOrAtomicMemory()) {
        TransactionGraphAbsentReasons.push_back("unsupported-volatile-or-atomic-memory");
      } else if (SourceHasMissingMaskedLoadPassthru()) {
        TransactionGraphAbsentReasons.push_back("unsupported-missing-masked-load-passthru");
        auto [Source, Temp] = FirstMissingMaskedLoadPassthruDiagnostic();
        AddTransactionGraphAbsentDiagnostic(
            "unsupported-missing-masked-load-passthru", "memory-pack", Source,
            "missing-passthru", Temp);
      } else if (SourceHasTempMemoryMask()) {
        TransactionGraphAbsentReasons.push_back("unsupported-unresolved-memory-mask");
        AddTransactionGraphAbsentDiagnostic(
            "unsupported-unresolved-memory-mask", "memory-pack", MainSource,
            SourceMaskBlockerDetail());
      } else if (SourceHasVariableMaskIndex()) {
        TransactionGraphAbsentReasons.push_back("unsupported-variable-mask-index");
        auto [Source, Temp, Role] = FirstVariableMaskIndexDiagnostic();
        AddTransactionGraphAbsentDiagnostic(
            "unsupported-variable-mask-index", Role, Source,
            "unsafe-mask-index", Temp);
      } else if (IsScalable) {
        const bool HasMaskedLoad =
            MainSource.find("CreateMaskedLoad") != std::string::npos;
        TransactionGraphAbsentReasons.push_back(
            HasMaskedLoad ? "unsupported-scalable-masked-memory"
                          : "unsupported-scalable-memory-graph");
        if (HasMaskedLoad) {
          AddTransactionGraphAbsentDiagnostic(
              "unsupported-scalable-masked-memory", "memory-pack", MainSource,
              "scalable-mask-syntax");
        }
      } else if (SourceHasInterveningStore()) {
        TransactionGraphAbsentReasons.push_back("unsupported-intervening-store");
      } else if (SourceHasUnknownMemoryEffectCall()) {
        TransactionGraphAbsentReasons.push_back("unsupported-memory-effect-call");
      } else if (SourceHasPointerMutation()) {
        TransactionGraphAbsentReasons.push_back("unsupported-pointer-mutation");
      } else if (SourceHasAmbiguousMemoryBase(Lanes)) {
        TransactionGraphAbsentReasons.push_back("unsupported-ambiguous-memory-base");
      } else if (SourceHasVariableLoadIndex()) {
        TransactionGraphAbsentReasons.push_back("unsupported-variable-gather-index");
      } else if (SourceHasDuplicateLoadOffsets(Lanes)) {
        TransactionGraphAbsentReasons.push_back("unsupported-duplicate-gather-lane");
      } else {
        TransactionGraphAbsentReasons.push_back(
            SourceHasNonContiguousLoadOffsets(Lanes)
                ? "unresolved-gather-lane-address"
                : "unresolved-memory-lane-address");
      }
    } else if (TransactionGraph.empty() && SourceHasStore()) {
      if (SourceHasVolatileOrAtomicStore()) {
        TransactionGraphAbsentReasons.push_back("unsupported-volatile-or-atomic-store");
      } else if (SourceHasVariableMaskIndex()) {
        TransactionGraphAbsentReasons.push_back("unsupported-variable-mask-index");
        auto [Source, Temp, Role] = FirstVariableMaskIndexDiagnostic();
        AddTransactionGraphAbsentDiagnostic(
            "unsupported-variable-mask-index", Role, Source,
            "unsafe-mask-index", Temp);
      } else if (SourceHasStoreTempMemoryMask()) {
        TransactionGraphAbsentReasons.push_back("unsupported-unresolved-memory-mask");
        AddTransactionGraphAbsentDiagnostic(
            "unsupported-unresolved-memory-mask", "memory-store", MainSource,
            SourceMaskBlockerDetail());
      } else if (IsScalable) {
        const bool HasMaskedStore =
            MainSource.find("CreateMaskedStore") != std::string::npos;
        TransactionGraphAbsentReasons.push_back(
            HasMaskedStore ? "unsupported-scalable-masked-memory"
                           : "unsupported-scalable-store-graph");
        if (HasMaskedStore) {
          AddTransactionGraphAbsentDiagnostic(
              "unsupported-scalable-masked-memory", "memory-store", MainSource,
              "scalable-mask-syntax");
        }
      } else if (SourceHasUnknownMemoryEffectCall()) {
        TransactionGraphAbsentReasons.push_back("unsupported-store-memory-effect-call");
      } else if (SourceHasPointerMutation()) {
        TransactionGraphAbsentReasons.push_back("unsupported-store-pointer-mutation");
      } else if (SourceHasAmbiguousStoreBase(Lanes)) {
        TransactionGraphAbsentReasons.push_back("unsupported-ambiguous-store-base");
      } else if (SourceHasVariableStoreIndex()) {
        TransactionGraphAbsentReasons.push_back("unsupported-variable-store-index");
      } else if (SourceHasDuplicateStoreOffsets(Lanes)) {
        TransactionGraphAbsentReasons.push_back("unsupported-duplicate-scatter-lane");
      } else {
        TransactionGraphAbsentReasons.push_back("unresolved-store-lane-address");
      }
    }
    }
    llvm::json::Object OperandMappings{{"lhs", std::move(LHS)}};
    if (TransactionKind != "slp-vectorize-reduction") {
      OperandMappings["rhs"] = std::move(RHS);
    }
    const std::string EmitAction =
        TransactionKind == "slp-vectorize-reduction"
            ? "emit-vector-reduction"
            : (TransactionKind == "slp-vectorize-minmax" ? "emit-vector-minmax"
                                                          : "emit-vector-binop");
    const SlpFunctionSummary *Profitability =
        firstSummaryWithRole("profitability");
    const SlpFunctionSummary *ControlRoot =
        controlRootForTransaction(*Emitter, *Replacement);
    llvm::json::Array PredicateExpansion = predicateExpansion(ControlRoot);
    const bool HasExpandedLegality =
        !ControlRoot || expansionHasRole(PredicateExpansion, "legality");
    const bool HasExpandedReplacement = Replacement != nullptr;
    const bool HasLaneMapping = validateLaneMapping(LaneMapping, Lanes).empty();
    llvm::json::Object Completeness{
        {"has_candidate", Candidate != nullptr},
        {"has_legality", Legality != nullptr && HasExpandedLegality},
        {"has_profitability", Profitability != nullptr},
        {"has_emitter", Emitter != nullptr},
        {"has_replacement", HasExpandedReplacement},
        {"has_lane_mapping", HasLaneMapping},
    };
    std::set<std::string> Reachable = reachableFunctionNames(
        {Emitter->Name, Replacement->Name, Candidate->Name,
         Legality ? Legality->Name : "",
         Profitability ? Profitability->Name : "",
         ControlRoot ? ControlRoot->Name : ""});
    llvm::json::Object SourceProgramGraph = sourceProgramGraph(Reachable);
    llvm::json::Object Contract = sourceSliceContract(
        ControlRoot, Candidate, Legality, Profitability, *Emitter,
        *Replacement, HasExpandedLegality, HasLaneMapping, SourceProgramGraph);
    llvm::json::Array SourceAccessPathProvenance;
    if (const llvm::json::Array *Facts =
            SourceProgramGraph.getArray("access_path_facts")) {
      for (const llvm::json::Value &Fact : *Facts) {
        SourceAccessPathProvenance.push_back(cloneJson(Fact));
      }
    }
    enrichOperandMappingsWithAccessPaths(OperandMappings,
                                         SourceAccessPathProvenance);
    enrichResultMappingWithAccessPaths(ResultMapping, Replacement->Name,
                                       SourceAccessPathProvenance);
    enrichTransactionGraphWithAccessPaths(TransactionGraph,
                                         SourceAccessPathProvenance);
    if (ControlRoot && !HasExpandedLegality) {
      ConsistencyErrors.push_back("missing-expanded-legality");
    }
    if (!HasExpandedReplacement) {
      ConsistencyErrors.push_back("missing-expanded-replacement");
    }
    if (const auto *MissingRoles = Contract.getArray("missing_roles")) {
      for (const llvm::json::Value &RoleValue : *MissingRoles) {
        if (auto Role = RoleValue.getAsString()) {
          ConsistencyErrors.push_back("missing-contract-role:" + Role->str());
        }
      }
    }
    llvm::json::Array Errors;
    for (const std::string &Error : ConsistencyErrors) {
      Errors.push_back(Error);
    }
    llvm::json::Object SourceSlice =
        rewriteSlice(*Emitter, *Replacement, RoleEvidence, Reachable,
                     ControlRoot, PredicateExpansion, Completeness, Contract);
    llvm::json::Array HelperSummaries = helperSummaries();
    llvm::json::Array CallGraph = callGraphEdges();
    llvm::json::Array ReachableHelperSummaries =
        reachableHelperSummaries(Reachable);
    llvm::json::Array ReachableCallGraph = reachableCallGraphEdges(Reachable);
    llvm::json::Array PredicateProvenance =
        slpPredicateProvenance(RoleEvidence, Contract, ReductionSources,
                               ReductionResult, *Emitter, TransactionKind);
    llvm::json::Object Transaction{
        {"model", "optimization-transaction-v1"},
        {"kind", TransactionKind},
        {"opcode", Emitter->Opcode},
        {"lanes", Lanes},
        {"root", Emitter->Signature},
        {"functions", std::move(Functions)},
        {"role_provenance", std::move(RoleEvidence)},
        {"predicate_provenance", std::move(PredicateProvenance)},
        {"source_slice", std::move(SourceSlice)},
        {"source_program_graph", std::move(SourceProgramGraph)},
        {"source_access_path_provenance",
         std::move(SourceAccessPathProvenance)},
        {"helper_summaries", std::move(HelperSummaries)},
        {"slice_helper_summaries", std::move(ReachableHelperSummaries)},
        {"call_graph", std::move(CallGraph)},
        {"slice_call_graph", std::move(ReachableCallGraph)},
        {"opcode_sources", std::move(OpcodeSources)},
        {"lane_source", laneSourceObject(*Candidate, Lanes)},
        {"lane_mapping", std::move(LaneMapping)},
        {"operand_lane_mappings", std::move(OperandMappings)},
        {"result_lane_mapping", cloneJsonObject(ResultMapping)},
        {"scalar_lane_pairs", std::move(ScalarPairs)},
        {"consistency", ConsistencyErrors.empty() ? "ok" : "failed"},
        {"consistency_errors", std::move(Errors)},
        {"legality",
         llvm::json::Object{{"same_opcode", true}, {"valid_element_type", true}}},
        {"profitability",
         llvm::json::Object{{"cost_model",
                             firstSummaryWithRole("profitability") != nullptr}}},
        {"actions",
         llvm::json::Array{
             llvm::json::Object{{"kind", "pack-scalars"},
                                {"source", "TreeEntry.Scalars"}},
             llvm::json::Object{{"kind", EmitAction},
                                {"opcode", Emitter->Opcode}},
             llvm::json::Object{{"kind", "replace-scalar-uses"}}}},
        {"preserves", TransactionKind == "slp-vectorize-reduction"
                          ? "scalar reduction result"
                          : "lane-wise scalar result"}};
    if (!TransactionGraph.empty()) {
      Transaction["transaction_graph"] = std::move(TransactionGraph);
    } else if (!TransactionGraphAbsentReasons.empty()) {
      Transaction["transaction_graph_absent_reasons"] =
          std::move(TransactionGraphAbsentReasons);
      if (!TransactionGraphAbsentDiagnostics.empty()) {
        Transaction["transaction_graph_absent_diagnostics"] =
            std::move(TransactionGraphAbsentDiagnostics);
      }
    }
    if (TransactionKind == "slp-vectorize-minmax") {
      Transaction["predicate"] = minmaxPredicateForOpcode(Emitter->Opcode);
      Transaction["select_order"] = "canonical";
      Transaction["compare_sources"] =
          sourceRecordsForTokens({"ICMP_", "CreateICmp", "CmpInst::"});
      Transaction["select_sources"] =
          sourceRecordsForTokens({"CreateSelect", "SelectInst", "select"});
    }
    if (IsScalable) {
      Transaction["scalable"] = true;
      Transaction["base_lanes"] = Lanes;
      Transaction["vscale_values"] = cloneJson(*ScalableInfo.get("vscale_values"));
      Transaction["scalable_provenance"] =
          cloneJson(*ScalableInfo.get("scalable_provenance"));
    }
    if (TransactionKind == "slp-vectorize-reduction") {
      Transaction["reduction_opcode"] = Emitter->Opcode;
      Transaction["reduction_lanes"] = Lanes;
      Transaction["reduction_sources"] = std::move(ReductionSources);
      Transaction["reduction_result"] = std::move(ReductionResult);
      llvm::json::Object WidthInfo = slpReductionWidthInfo(Emitter->Body);
      if (!WidthInfo.empty()) {
        Transaction["reduction_width_status"] = stringField(WidthInfo, "status");
        if (const auto *Provenance = WidthInfo.getArray("width_provenance")) {
          llvm::json::Array ProvenanceCopy;
          for (const llvm::json::Value &Value : *Provenance) {
            ProvenanceCopy.push_back(cloneJson(Value));
          }
          Transaction["reduction_width_provenance"] = std::move(ProvenanceCopy);
        }
        if (stringField(WidthInfo, "status") == "complete") {
          Transaction["reduction_input_bits"] =
              static_cast<int>(*WidthInfo.getInteger("input_bits"));
          Transaction["reduction_accumulator_bits"] =
              static_cast<int>(*WidthInfo.getInteger("accumulator_bits"));
          Transaction["reduction_result_bits"] =
              static_cast<int>(*WidthInfo.getInteger("result_bits"));
          Transaction["reduction_extend_kind"] =
              std::string(*WidthInfo.getString("extend_kind"));
        }
      }
      if (!FPPolicy.empty()) {
        Transaction["fp_policy"] = std::move(FPPolicy);
      }
      llvm::json::Array UnsupportedReasons;
      for (const std::string &Error : ConsistencyErrors) {
        if (llvm::StringRef(Error).starts_with("unsupported-reduction-") ||
            llvm::StringRef(Error).starts_with("unsupported-scalable-")) {
          UnsupportedReasons.push_back(Error);
        }
      }
      Transaction["unsupported_reduction_reasons"] =
          std::move(UnsupportedReasons);
    }
    llvm::json::Object Constraints{{"transaction.kind", TransactionKind},
                                   {"transaction.opcode", Emitter->Opcode},
                                   {"transaction.lanes", Lanes}};
    if (Legality) {
      Constraints["transaction.legality_guard"] = "valid-element-type";
    }
    if (firstSummaryWithRole("profitability")) {
      Constraints["transaction.profitability_guard"] = true;
    }
    llvm::json::Array Context;
    const SlpFunctionSummary *PredicateSummary =
        Legality ? Legality : (ControlRoot ? ControlRoot : Emitter);
    const unsigned GuardLine = PredicateSummary->StartLine;
    const unsigned Start = GuardLine > 2 ? GuardLine - 2 : 1;
    const unsigned End =
        std::min<unsigned>(MainLines.size(), GuardLine + 2);
    for (unsigned Line = Start; Line <= End; ++Line) {
      Context.push_back(MainLines[Line - 1]);
    }
    llvm::json::Object Result{
        {"file", MainFile},
        {"line", static_cast<int>(GuardLine)},
        {"marker", TransactionKind == "slp-vectorize-reduction"
                       ? "probe.slp.vectorize-reduction"
                       : "probe.slp.vectorize-binop"},
        {"pass", "slp-vectorizer"},
        {"predicate_kind", "transaction"},
        {"matched_pattern", TransactionKind + "-transaction"},
        {"source", sourceLineForToken(PredicateSummary->Lines,
                                      {"allSameOpcode", "sameOpcode",
                                       "isValidElementType", "canVectorize",
                                       "buildTree_rec", "opaqueVectorLegality",
                                       "isTreeLegal", "isReductionLegal"})},
        {"predicate_source", sourceLineForToken(PredicateSummary->Lines,
                                                {"allSameOpcode", "sameOpcode",
                                                 "isValidElementType",
                                                 "canVectorize",
                                                 "buildTree_rec",
                                                 "opaqueVectorLegality",
                                                 "isTreeLegal",
                                                 "isReductionLegal"})},
        {"rewrite_source",
         TransactionKind == "slp-vectorize-reduction"
             ? "emit vector " + Emitter->Opcode +
                   " reduction and replace scalar result"
             : "emit vector " + Emitter->Opcode + " and replace scalar uses"},
        {"rewrite_line", static_cast<int>(Emitter->StartLine)},
        {"constraints", std::move(Constraints)},
        {"suggestion",
         TransactionKind == "slp-vectorize-reduction"
             ? "Wrap transaction root with CV_PASS_PROBE_IF(\"probe.slp.vectorize-reduction\", <legality>)"
             : "Wrap transaction root with CV_PASS_PROBE_IF(\"probe.slp.vectorize-binop\", <legality>)"},
        {"context", std::move(Context)},
        {"finding_source", "ast"},
        {"source_range",
         llvm::json::Object{{"predicate_begin_line", static_cast<int>(GuardLine)},
                            {"predicate_begin_column", 1},
                            {"predicate_end_line", static_cast<int>(PredicateSummary->EndLine)},
                            {"predicate_end_column", 1},
                            {"rewrite_line", static_cast<int>(Emitter->StartLine)}}},
        {"optimization_transaction", std::move(Transaction)}};
    return llvm::json::Value(std::move(Result));
  }

  std::map<std::string, RegistryEntry> Registry;
  std::map<std::string, llvm::json::Value> SemanticRegistry;
  std::vector<llvm::json::Value> Output;
  std::set<std::pair<unsigned, std::string>> Seen;
  std::vector<SlpFunctionSummary> SlpSummaries;
  std::string MainSource;
  std::vector<std::string> MainLines;
  std::string MainFile;
  FileID MainFileID;
  bool SlpFinalized = false;
};

class MiningActionFactory : public FrontendActionFactory {
public:
  explicit MiningActionFactory(MiningCallback &Callback) : Callback(Callback) {}

  std::unique_ptr<FrontendAction> create() override {
    class Action : public ASTFrontendAction {
    public:
      explicit Action(MiningCallback &Callback) : Callback(Callback) {}

      std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &,
                                                     llvm::StringRef) override {
        Finder.addMatcher(functionDecl(isDefinition()).bind("slp-function"),
                          &Callback);
        Finder.addMatcher(ifStmt(hasCondition(expr().bind("condition"))).bind("if"),
                          &Callback);
        registerGeneratedAstMatcherSpecs(Finder, &Callback);
        return Finder.newASTConsumer();
      }

    private:
      MiningCallback &Callback;
      MatchFinder Finder;
    };
    return std::make_unique<Action>(Callback);
  }

private:
  MiningCallback &Callback;
};

} // namespace

int main(int argc, const char **argv) {
  auto ExpectedParser = CommonOptionsParser::create(argc, argv, Category);
  if (!ExpectedParser) {
    llvm::errs() << ExpectedParser.takeError();
    return 1;
  }
  if (Format != "json" && Format != "jsonl") {
    llvm::errs() << "--format must be json or jsonl\n";
    return 1;
  }
  loadLlvmIdioms(resolveDataPath(LlvmIdiomsPath, RegistryPath));

  std::map<std::string, RegistryEntry> Registry = loadRegistry(RegistryPath);
  bool GuardCatalogOk = false;
  GuardCatalog = loadGuardCatalog(effectiveGuardSemanticsPath(), GuardCatalogOk);
  if (!GuardCatalogOk) {
    return 1;
  }
  std::map<std::string, llvm::json::Value> SemanticRegistry =
      loadSemanticRegistry(effectiveSemanticRegistryPath());
  CommonOptionsParser &OptionsParser = ExpectedParser.get();
  MiningCallback Callback(std::move(Registry), std::move(SemanticRegistry));
  MiningActionFactory Factory(Callback);
  ClangTool Tool(OptionsParser.getCompilations(),
                 OptionsParser.getSourcePathList());
  const int ToolStatus = Tool.run(&Factory);
  if (ToolStatus != 0) {
    return ToolStatus;
  }
  Callback.finalizeSlpTransactions();
  if (!MissingGuardKinds.empty()) {
    llvm::errs() << "guard semantics missing kind";
    if (MissingGuardKinds.size() != 1) {
      llvm::errs() << "s";
    }
    llvm::errs() << ": ";
    size_t Index = 0;
    for (const std::string &Kind : MissingGuardKinds) {
      if (Index++ != 0) {
        llvm::errs() << ", ";
      }
      llvm::errs() << Kind;
    }
    llvm::errs() << "\n";
    return 1;
  }

  std::set<std::string> Found;
  for (const llvm::json::Value &Value : Callback.findings()) {
    if (const auto *Object = Value.getAsObject()) {
      if (auto Marker = Object->getString("marker")) {
        Found.insert(std::string(*Marker));
      }
    }
  }
  std::vector<std::string> Missing;
  for (const std::string &Marker : RequiredMarkers) {
    if (Found.count(Marker) == 0) {
      Missing.push_back(Marker);
    }
  }
  if (Format == "jsonl") {
    for (const llvm::json::Value &Value : Callback.findings()) {
      llvm::outs() << Value << "\n";
    }
  } else {
    llvm::json::Array Array;
    for (const llvm::json::Value &Value : Callback.findings()) {
      Array.push_back(cloneJson(Value));
    }
    llvm::outs() << llvm::json::Value(std::move(Array)) << "\n";
  }
  if (!Missing.empty()) {
    llvm::errs() << "missing required markers: ";
    for (size_t Index = 0; Index < Missing.size(); ++Index) {
      if (Index != 0) {
        llvm::errs() << ", ";
      }
      llvm::errs() << Missing[Index];
    }
    llvm::errs() << "\n";
    return 1;
  }
  return 0;
}
