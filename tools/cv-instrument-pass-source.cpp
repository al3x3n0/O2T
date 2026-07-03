#include "o2t/GeneratedSourceMarkerPatterns.h"

#include "clang/AST/ASTContext.h"
#include "clang/ASTMatchers/ASTMatchFinder.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendActions.h"
#include "clang/Rewrite/Core/Rewriter.h"
#include "clang/Tooling/CommonOptionsParser.h"
#include "clang/Tooling/Tooling.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

using namespace clang;
using namespace clang::ast_matchers;
using namespace clang::tooling;

namespace {

llvm::cl::OptionCategory Category("O2T instrumentation options");
llvm::cl::opt<std::string> MarkerFilter(
    "markers", llvm::cl::desc("Comma-separated probe markers to instrument"),
    llvm::cl::init(""), llvm::cl::cat(Category));
llvm::cl::opt<std::string> CandidateFile(
    "candidate-file",
    llvm::cl::desc("JSON findings file with file/line/marker candidates"),
    llvm::cl::init(""), llvm::cl::cat(Category));

struct Candidate {
  unsigned Line = 0;
  std::string Marker;
  std::string Predicate;
};

std::set<std::string> splitMarkers(StringRef Text) {
  std::set<std::string> Markers;
  std::stringstream Input(Text.str());
  std::string Marker;
  while (std::getline(Input, Marker, ',')) {
    if (!Marker.empty()) {
      Markers.insert(Marker);
    }
  }
  return Markers;
}

std::string sourceText(const SourceManager &SM, const LangOptions &LangOpts,
                       SourceRange Range) {
  CharSourceRange CharRange = CharSourceRange::getTokenRange(Range);
  return Lexer::getSourceText(CharRange, SM, LangOpts).str();
}

std::vector<std::string> contextLines(const SourceManager &SM, FileID File,
                                      unsigned Line, unsigned Radius) {
  std::vector<std::string> Result;
  bool Invalid = false;
  StringRef Buffer = SM.getBufferData(File, &Invalid);
  if (Invalid) {
    return Result;
  }
  llvm::SmallVector<StringRef, 128> Lines;
  Buffer.split(Lines, '\n');
  const unsigned Start = Line > Radius ? Line - Radius : 1;
  const unsigned End = std::min<unsigned>(Lines.size(), Line + Radius);
  for (unsigned Index = Start; Index <= End; ++Index) {
    Result.push_back(Lines[Index - 1].str());
  }
  return Result;
}

std::string stringField(const llvm::json::Object &Object, StringRef Name) {
  auto Value = Object.getString(Name);
  if (Value) {
    return std::string(*Value);
  }
  return "";
}

std::string markerForConditionText(StringRef Text) {
  return cv::markerForGeneratedSourceText(Text);
}

std::vector<Candidate> loadCandidates(StringRef Path) {
  std::vector<Candidate> Candidates;
  if (Path.empty()) {
    return Candidates;
  }

  auto Buffer = llvm::MemoryBuffer::getFile(Path);
  if (!Buffer) {
    llvm::errs() << "failed to read candidate file: " << Path << "\n";
    return Candidates;
  }
  llvm::Expected<llvm::json::Value> Parsed =
      llvm::json::parse((*Buffer)->getBuffer());
  if (!Parsed) {
    llvm::errs() << "failed to parse candidate file: " << Path << "\n";
    llvm::consumeError(Parsed.takeError());
    return Candidates;
  }
  const auto *Array = Parsed->getAsArray();
  if (!Array) {
    llvm::errs() << "candidate file must contain a JSON array: " << Path << "\n";
    return Candidates;
  }

  for (const llvm::json::Value &Value : *Array) {
    const auto *Object = Value.getAsObject();
    if (!Object) {
      continue;
    }
    auto Line = Object->getInteger("line");
    std::string Marker = stringField(*Object, "marker");
    if (!Line || *Line <= 0 || Marker.empty()) {
      continue;
    }
    std::string Predicate = stringField(*Object, "predicate_source");
    if (Predicate.empty()) {
      Predicate = stringField(*Object, "matched_pattern");
    }
    if (Predicate.empty()) {
      Predicate = stringField(*Object, "source");
    }
    Candidates.push_back(
        Candidate{static_cast<unsigned>(*Line), Marker, Predicate});
  }
  return Candidates;
}

class InstrumentationCallback : public MatchFinder::MatchCallback {
public:
  InstrumentationCallback(Rewriter &Rewrite, std::set<std::string> AllowedMarkers,
                          std::vector<Candidate> Candidates, bool CandidateMode)
      : Rewrite(Rewrite), AllowedMarkers(std::move(AllowedMarkers)),
        Candidates(std::move(Candidates)), CandidateMode(CandidateMode) {}

  void run(const MatchFinder::MatchResult &Result) override {
    const auto *If = Result.Nodes.getNodeAs<IfStmt>("if");
    const auto *Condition = Result.Nodes.getNodeAs<Expr>("condition");
    if (!If || !Condition || !Result.SourceManager) {
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

    const auto Key = std::make_pair(SM.getFileID(Begin), SM.getSpellingLineNumber(Begin));
    if (InstrumentedLines.count(Key) != 0) {
      return;
    }

    const std::string ConditionText =
        sourceText(SM, Result.Context->getLangOpts(), Condition->getSourceRange());
    std::string Marker = markerForCondition(Result, ConditionText, Key.second);
    if (Marker.empty()) {
      for (const std::string &Line : contextLines(SM, SM.getFileID(Begin), Key.second, 0)) {
        Marker = markerForConditionText(Line);
        if (!Marker.empty()) {
          break;
        }
      }
    }
    if (Marker.empty()) {
      return;
    }
    if (!AllowedMarkers.empty() && AllowedMarkers.count(Marker) == 0) {
      return;
    }

    InstrumentedLines.insert(Key);
    const std::string Replacement =
        "CV_PASS_PROBE_IF(\"" + Marker + "\", " + ConditionText + ")";
    Rewrite.ReplaceText(SourceRange(Begin, Condition->getEndLoc()), Replacement);
    IncludeNeeded.insert(SM.getFileID(Begin));
  }

  void insertIncludes() {
    for (FileID File : IncludeNeeded) {
      SourceManager &SM = Rewrite.getSourceMgr();
      const FileEntry *Entry = SM.getFileEntryForID(File);
      if (!Entry) {
        continue;
      }
      const RewriteBuffer *Buffer = Rewrite.getRewriteBufferFor(File);
      if (Buffer && Buffer->begin() != Buffer->end()) {
        std::string Text(Buffer->begin(), Buffer->end());
        if (Text.find("o2t/PassInstrumentation.h") != std::string::npos) {
          continue;
        }
      }

      SourceLocation Start = SM.getLocForStartOfFile(File);
      Rewrite.InsertTextBefore(Start,
                               "#include \"o2t/PassInstrumentation.h\"\n");
    }
  }

private:
  std::string markerForCondition(const MatchFinder::MatchResult &Result,
                                 const std::string &ConditionText,
                                 unsigned Line) const {
    (void)Result;
    if (!CandidateMode) {
      return markerForConditionText(ConditionText);
    }
    for (const Candidate &Candidate : Candidates) {
      if (Candidate.Line != Line) {
        continue;
      }
      if (!Candidate.Predicate.empty() &&
          ConditionText.find(Candidate.Predicate) == std::string::npos) {
        continue;
      }
      return Candidate.Marker;
    }
    return "";
  }

  Rewriter &Rewrite;
  std::set<std::string> AllowedMarkers;
  std::vector<Candidate> Candidates;
  bool CandidateMode;
  std::set<std::pair<FileID, unsigned>> InstrumentedLines;
  std::set<FileID> IncludeNeeded;
};

} // namespace

int main(int argc, const char **argv) {
  auto ExpectedParser = CommonOptionsParser::create(argc, argv, Category);
  if (!ExpectedParser) {
    llvm::errs() << ExpectedParser.takeError();
    return 1;
  }

  CommonOptionsParser &OptionsParser = ExpectedParser.get();
  ClangTool Tool(OptionsParser.getCompilations(),
                 OptionsParser.getSourcePathList());
  const std::set<std::string> AllowedMarkers = splitMarkers(MarkerFilter);
  const bool CandidateMode = !CandidateFile.empty();
  const std::vector<Candidate> Candidates = loadCandidates(CandidateFile);

  class ActionFactory : public FrontendActionFactory {
  public:
    ActionFactory(std::set<std::string> AllowedMarkers,
                  std::vector<Candidate> Candidates, bool CandidateMode)
        : AllowedMarkers(std::move(AllowedMarkers)),
          Candidates(std::move(Candidates)), CandidateMode(CandidateMode) {}

    std::unique_ptr<FrontendAction> create() override {
      class Action : public ASTFrontendAction {
      public:
        Action(std::set<std::string> AllowedMarkers,
               std::vector<Candidate> Candidates, bool CandidateMode)
            : AllowedMarkers(std::move(AllowedMarkers)),
              Candidates(std::move(Candidates)), CandidateMode(CandidateMode) {}

        std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &CI,
                                                       StringRef) override {
          Rewrite.setSourceMgr(CI.getSourceManager(), CI.getLangOpts());
          Callback = std::make_unique<InstrumentationCallback>(
              Rewrite, AllowedMarkers, Candidates, CandidateMode);

          if (CandidateMode) {
            Finder.addMatcher(ifStmt(hasCondition(expr().bind("condition")))
                                  .bind("if"),
                              Callback.get());
            return Finder.newASTConsumer();
          }

          Finder.addMatcher(ifStmt(hasCondition(expr().bind("condition")))
                                .bind("if"),
                            Callback.get());
          return Finder.newASTConsumer();
        }

        void EndSourceFileAction() override {
          if (Callback) {
            Callback->insertIncludes();
          }
          Rewrite.getEditBuffer(Rewrite.getSourceMgr().getMainFileID())
              .write(llvm::outs());
        }

      private:
        std::set<std::string> AllowedMarkers;
        std::vector<Candidate> Candidates;
        bool CandidateMode;
        Rewriter Rewrite;
        MatchFinder Finder;
        std::unique_ptr<InstrumentationCallback> Callback;
      };

      return std::make_unique<Action>(AllowedMarkers, Candidates, CandidateMode);
    }

  private:
    std::set<std::string> AllowedMarkers;
    std::vector<Candidate> Candidates;
    bool CandidateMode;
  };

  ActionFactory Factory(AllowedMarkers, Candidates, CandidateMode);
  return Tool.run(&Factory);
}
