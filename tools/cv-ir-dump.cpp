//===- cv-ir-dump.cpp - structured LLVM IR dump for O2T's validators ------===//
//
// Parse a .ll module with LLVM's OWN parser and emit it as JSON.
//
// WHY THIS EXISTS. Track B used to read LLVM IR with regexes -- a signature
// pattern here, an instruction pattern there, spread across ~20 modules. That
// produced a recurring bug CLASS, not isolated bugs: signature readers that
// matched a forward-reference call site instead of the `define` (5 modules at
// once), a whole-.cpp selector that guessed among overloads, and a signature
// capture that stopped at the first `)` so every parameter after an attribute
// containing parentheses -- `i32 range(i32 0, 8) %y` -- was silently dropped.
// Each failed toward a decline rather than a wrong answer, but each was found
// by accident, and "mostly declines" is not a soundness argument.
//
// Emitting the module through `llvm::parseAssembly` moves the syntax layer of
// the trusted base from O2T's regexes to LLVM's own parser, which is the same
// parser `opt` used to produce the IR being validated. It cannot disagree with
// itself about what the text means, and version drift is impossible because the
// tool links the same LLVM the pipeline runs.
//
// SCOPE. This is a SYNTAX tool. It carries no semantics and makes no
// soundness judgements: it reports what the module says -- types, parameter
// attributes, poison flags, predicates, phi incomings, gep source types -- and
// leaves every interpretation to the validators. An instruction it does not
// specifically model is still emitted, with its opcode name and operands, so
// the Python side declines on an unknown opcode instead of silently missing it.
//
// Usage:  cv-ir-dump <module.ll>        (JSON on stdout, non-zero on a parse error)
//
//===----------------------------------------------------------------------===//

#include "llvm/ADT/SmallString.h"
#include "llvm/AsmParser/Parser.h"
#include "llvm/Analysis/ConstantFolding.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/DataLayout.h"
#include "llvm/IR/DerivedTypes.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Module.h"
#include "llvm/IR/Operator.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/SourceMgr.h"
#include "llvm/Support/raw_ostream.h"

#include <string>
#include <vector>

using namespace llvm;

namespace {

// --- JSON emission (small and dependency-free; the schema is documented in
// o2t/validate/ir_model.py, which is its only consumer) --------------------

std::string quote(StringRef s) {
  std::string out = "\"";
  for (char c : s) {
    switch (c) {
    case '"': out += "\\\""; break;
    case '\\': out += "\\\\"; break;
    case '\n': out += "\\n"; break;
    case '\t': out += "\\t"; break;
    case '\r': out += "\\r"; break;
    default:
      if ((unsigned char)c < 0x20) {
        char buf[8];
        snprintf(buf, sizeof(buf), "\\u%04x", c);
        out += buf;
      } else {
        out += c;
      }
    }
  }
  return out + "\"";
}

// A value's textual name as the IR uses it (`%x`, `@g`). Unnamed values get
// their slot number from LLVM's own printer, so the Python side sees exactly
// the names the .ll text would show.
std::string valueName(const Value *V) {
  std::string s;
  raw_string_ostream os(s);
  V->printAsOperand(os, /*PrintType=*/false);
  return os.str();
}

// A basic block's LABEL, as the IR text writes it (`entry:`), not as an operand (`%entry`). Blocks
// are referenced both ways in LLVM's own printing; consumers match labels against branch successors
// and phi incoming blocks, so all three are reported in the same, prefix-free form.
std::string blockLabel(const BasicBlock *BB) {
  std::string s = valueName(BB);
  return s.empty() || s[0] != '%' ? s : s.substr(1);
}

const DataLayout *DL = nullptr;   // set in main; needed to fold constant expressions

std::string typeJson(Type *T) {
  if (T->isVoidTy())
    return "{\"kind\":\"void\"}";
  if (auto *IT = dyn_cast<IntegerType>(T))
    return "{\"kind\":\"int\",\"bits\":" + std::to_string(IT->getBitWidth()) + "}";
  if (T->isPointerTy())
    // The ADDRESS SPACE comes with it. `ptr` and `ptr addrspace(1)` are different types with
    // different rules -- most sharply, `null` is only guaranteed non-dereferenceable in address
    // space 0 -- and LLVM's own InstCombine tests turn on the distinction (or.ll/select.ll carry
    // `_as1` and `_neg` variants asserting a fold does NOT happen off address space 0). Emitting
    // them identically leaves a validator unable to decline what it cannot model.
    return "{\"kind\":\"ptr\",\"addrspace\":" +
           std::to_string(cast<PointerType>(T)->getAddressSpace()) + "}";
  if (auto *VT = dyn_cast<VectorType>(T)) {
    bool scalable = isa<ScalableVectorType>(VT);
    unsigned n = VT->getElementCount().getKnownMinValue();
    return "{\"kind\":\"vector\",\"n\":" + std::to_string(n) +
           ",\"scalable\":" + (scalable ? "true" : "false") +
           ",\"elem\":" + typeJson(VT->getElementType()) + "}";
  }
  if (auto *AT = dyn_cast<ArrayType>(T))
    return "{\"kind\":\"array\",\"n\":" + std::to_string(AT->getNumElements()) +
           ",\"elem\":" + typeJson(AT->getElementType()) + "}";
  if (auto *ST = dyn_cast<StructType>(T)) {
    std::string s = "{\"kind\":\"struct\",\"packed\":";
    s += ST->isPacked() ? "true" : "false";
    s += ",\"fields\":[";
    for (unsigned i = 0; i < ST->getNumElements(); ++i) {
      if (i) s += ",";
      s += typeJson(ST->getElementType(i));
    }
    return s + "]}";
  }
  if (T->isFloatingPointTy()) {
    std::string s;
    raw_string_ostream os(s);
    T->print(os);
    // The WIDTH comes with it, from LLVM's own accessor -- the same one `bitcast` legality is
    // decided by. A validator that carries a float only as opaque BITS (never applying an FP
    // operation to it) needs exactly this and nothing else; deriving it from the type NAME in
    // Python would be the second reading of LLVM that the parse migration removed.
    return "{\"kind\":\"float\",\"name\":" + quote(os.str()) +
           ",\"bits\":" + std::to_string(T->getPrimitiveSizeInBits()) + "}";
  }
  std::string s;
  raw_string_ostream os(s);
  T->print(os);
  // Deliberately not an error: an unmodeled type is reported faithfully so the
  // validator declines on it rather than the dump failing the whole module.
  return "{\"kind\":\"other\",\"text\":" + quote(os.str()) + "}";
}

std::string valueJson(const Value *V);

std::string constantJson(const Constant *C) {
  if (isa<UndefValue>(C) && !isa<PoisonValue>(C))
    return "{\"kind\":\"undef\",\"type\":" + typeJson(C->getType()) + "}";
  if (isa<PoisonValue>(C))
    return "{\"kind\":\"poison\",\"type\":" + typeJson(C->getType()) + "}";
  if (auto *CI = dyn_cast<ConstantInt>(C)) {
    SmallString<40> dec;
    // Signed decimal, arbitrary width: the Python side reads it as an integer,
    // so i128 constants survive rather than being truncated to 64 bits.
    CI->getValue().toString(dec, 10, /*Signed=*/true);
    return "{\"kind\":\"int\",\"value\":" + quote(dec) +
           ",\"type\":" + typeJson(C->getType()) + "}";
  }
  if (isa<ConstantPointerNull>(C))
    return "{\"kind\":\"null\",\"type\":" + typeJson(C->getType()) + "}";
  if (auto *CAZ = dyn_cast<ConstantAggregateZero>(C))
    return "{\"kind\":\"zeroinit\",\"type\":" + typeJson(CAZ->getType()) + "}";
  if (auto *CDV = dyn_cast<ConstantDataVector>(C)) {
    std::string s = "{\"kind\":\"vector\",\"type\":" + typeJson(CDV->getType()) + ",\"elems\":[";
    for (unsigned i = 0; i < CDV->getNumElements(); ++i) {
      if (i) s += ",";
      s += constantJson(CDV->getElementAsConstant(i));
    }
    return s + "]}";
  }
  if (auto *CV = dyn_cast<ConstantVector>(C)) {
    // `splat (i32 3)` parses to a ConstantVector whose splat value LLVM knows;
    // report it as a vector of elements so the lane model needs no special case.
    std::string s = "{\"kind\":\"vector\",\"type\":" + typeJson(CV->getType()) + ",\"elems\":[";
    for (unsigned i = 0; i < CV->getNumOperands(); ++i) {
      if (i) s += ",";
      s += constantJson(CV->getOperand(i));
    }
    return s + "]}";
  }
  // A SPLAT -- every lane the same value. Asked via LLVM's own `getSplatValue`, which answers for
  // scalable vectors too, where there is no element list to enumerate (`splat (i32 -1)` at
  // <vscale x 4 x i32> is not a ConstantVector). Reported after the enumerable cases above, so a
  // fixed vector still arrives as its elements and only the non-enumerable form needs this.
  if (C->getType()->isVectorTy()) {
    if (Constant *S = C->getSplatValue()) {
      bool scalable = isa<ScalableVectorType>(C->getType());
      return "{\"kind\":\"splat\",\"scalable\":" + std::string(scalable ? "true" : "false") +
             ",\"elem\":" + constantJson(S) + ",\"type\":" + typeJson(C->getType()) + "}";
    }
  }
  // A CONSTANT EXPRESSION. Two very different things arrive here. Some are COMPUTABLE --
  // `bitcast (<2 x i32> <i32 1, i32 -1> to i64)` is a fixed number LLVM can evaluate, and
  // InstCombine does evaluate it (icmp.ll test12 folds to `xor %A, true`). Those must be FOLDED,
  // not symbolised: a model that made test12's constant opaque could not prove the fold and would
  // REFUTE a sound transform. So fold first, and re-enter with whatever comes back.
  if (auto *CE = dyn_cast<ConstantExpr>(C)) {
    Constant *F = ConstantFoldConstant(CE, *DL);
    if (F && F != C)
      return constantJson(F);
    // What is left genuinely depends on an address no compiler knows -- `ptrtoint (ptr @g to i32)`.
    // It is a FIXED but UNKNOWN value, and a fold involving it must be valid for EVERY address it
    // could have, so an unconstrained symbol keyed by the expression's text is the right reading
    // rather than a weakening of it. (No alignment or non-null facts are asserted about it, which
    // over-approximates: it costs refutations, not proofs.)
    std::string t;
    raw_string_ostream ts(t);
    CE->printAsOperand(ts, false);
    return "{\"kind\":\"const_expr\",\"text\":" + quote(ts.str()) +
           ",\"type\":" + typeJson(C->getType()) + "}";
  }
  // A FLOATING-POINT CONSTANT, AS BITS. This model carries floats as opaque bitvectors, and a
  // constant reached it only as the printed text ("float 1.000000e+00") -- unusable, and the
  // reason `operand kind 'other_const'` is its own decline bucket. The IEEE bit pattern is what a
  // bit-level fold (copysign, a sign-bit test) actually operates on, and LLVM already holds it.
  // Emitted as a decimal string because a 64- or 128-bit pattern does not fit a JSON number.
  if (auto *CFP = dyn_cast<ConstantFP>(C)) {
    SmallString<40> Bits;
    CFP->getValueAPF().bitcastToAPInt().toStringUnsigned(Bits, 10);
    return "{\"kind\":\"float\",\"bits_value\":" + quote(std::string(Bits.c_str())) +
           ",\"type\":" + typeJson(C->getType()) + "}";
  }
  if (auto *GV = dyn_cast<GlobalValue>(C))
    return "{\"kind\":\"global\",\"name\":" + quote(valueName(GV)) +
           ",\"type\":" + typeJson(GV->getType()) + "}";
  std::string s;
  raw_string_ostream os(s);
  C->print(os);
  return "{\"kind\":\"other_const\",\"text\":" + quote(os.str()) +
         ",\"type\":" + typeJson(C->getType()) + "}";
}

std::string valueJson(const Value *V) {
  if (auto *C = dyn_cast<Constant>(V))
    return constantJson(C);
  return "{\"kind\":\"reg\",\"name\":" + quote(valueName(V)) +
         ",\"type\":" + typeJson(V->getType()) + "}";
}

// Poison-generating flags. These are the surface every false proof in the
// 2026-07 review lived on, so they are reported explicitly rather than being
// re-derived from the instruction text.
std::string flagsJson(const Instruction &I) {
  std::vector<std::string> flags;
  // Emitted in LLVM's OWN print order (`nuw` before `nsw`, as the AsmWriter prints them). The order
  // is not semantic, but it must be CANONICAL: consumers build SMT strings by iterating this list,
  // and an unordered container made the emitted formula vary between runs.
  if (const auto *OBO = dyn_cast<OverflowingBinaryOperator>(&I)) {
    if (OBO->hasNoUnsignedWrap()) flags.push_back("nuw");
    if (OBO->hasNoSignedWrap()) flags.push_back("nsw");
  }
  if (const auto *PE = dyn_cast<PossiblyExactOperator>(&I))
    if (PE->isExact()) flags.push_back("exact");
  if (const auto *PD = dyn_cast<PossiblyDisjointInst>(&I))
    if (PD->isDisjoint()) flags.push_back("disjoint");
  if (const auto *NNI = dyn_cast<PossiblyNonNegInst>(&I))
    if (NNI->hasNonNeg()) flags.push_back("nneg");
  if (isa<FPMathOperator>(&I)) {
    FastMathFlags FMF = I.getFastMathFlags();
    if (FMF.noNaNs()) flags.push_back("nnan");
    if (FMF.noInfs()) flags.push_back("ninf");
    if (FMF.noSignedZeros()) flags.push_back("nsz");
    if (FMF.allowReassoc()) flags.push_back("reassoc");
    if (FMF.allowContract()) flags.push_back("contract");
  }
  std::string s = "[";
  for (size_t i = 0; i < flags.size(); ++i) {
    if (i) s += ",";
    s += quote(flags[i]);
  }
  return s + "]";
}

std::string instJson(const Instruction &I) {
  std::string s = "{\"op\":" + quote(I.getOpcodeName());
  if (!I.getType()->isVoidTy() && I.hasName())
    s += ",\"result\":" + quote(valueName(&I));
  else if (!I.getType()->isVoidTy())
    s += ",\"result\":" + quote(valueName(&I));
  s += ",\"type\":" + typeJson(I.getType());
  s += ",\"flags\":" + flagsJson(I);

  if (const auto *CI = dyn_cast<CmpInst>(&I))
    s += ",\"pred\":" + quote(CmpInst::getPredicateName(CI->getPredicate()));

  if (const auto *PN = dyn_cast<PHINode>(&I)) {
    s += ",\"incoming\":[";
    for (unsigned i = 0; i < PN->getNumIncomingValues(); ++i) {
      if (i) s += ",";
      s += "{\"value\":" + valueJson(PN->getIncomingValue(i)) +
           ",\"block\":" + quote(blockLabel(PN->getIncomingBlock(i))) + "}";
    }
    s += "]";
  }

  if (const auto *GEP = dyn_cast<GetElementPtrInst>(&I)) {
    s += ",\"source_type\":" + typeJson(GEP->getSourceElementType());
    s += ",\"inbounds\":";
    s += GEP->isInBounds() ? "true" : "false";
  }

  if (const auto *LI = dyn_cast<LoadInst>(&I)) {
    s += ",\"align\":" + std::to_string(LI->getAlign().value());
    s += ",\"volatile\":";
    s += LI->isVolatile() ? "true" : "false";
    // `!noundef` is a PROMISE about the loaded value, and it is the only thing that makes some
    // `freeze` pairs decidable: a freeze whose operand cannot be poison has no nondeterministic
    // choice left to collapse, so a validator that cannot pick a side can still decide it. LLVM
    // spells it as metadata rather than a flag, so nothing reached the model until now.
    // Three metadata kinds carry the same promise for this purpose. `!noundef` states it
    // directly; `!dereferenceable` and `!dereferenceable_or_null` state that the loaded pointer
    // is dereferenceable (or null), and a POISON pointer is neither -- so violating them is UB
    // just the same. Alive2 agrees the freeze folds under all three, checked before modeling it.
    s += ",\"noundef\":";
    s += (LI->hasMetadata(LLVMContext::MD_noundef) ||
          LI->hasMetadata(LLVMContext::MD_dereferenceable) ||
          LI->hasMetadata(LLVMContext::MD_dereferenceable_or_null)) ? "true" : "false";
  }
  if (const auto *SI = dyn_cast<StoreInst>(&I)) {
    s += ",\"align\":" + std::to_string(SI->getAlign().value());
    s += ",\"volatile\":";
    s += SI->isVolatile() ? "true" : "false";
  }
  if (const auto *AI = dyn_cast<AllocaInst>(&I)) {
    s += ",\"alloc_type\":" + typeJson(AI->getAllocatedType());
    s += ",\"align\":" + std::to_string(AI->getAlign().value());
  }
  if (const auto *CB = dyn_cast<CallBase>(&I)) {
    const Function *Callee = CB->getCalledFunction();
    s += ",\"callee\":" + quote(Callee ? valueName(Callee) : "");
    s += ",\"indirect\":";
    s += Callee ? "false" : "true";
    s += ",\"args\":[";
    for (unsigned i = 0; i < CB->arg_size(); ++i) {
      if (i) s += ",";
      s += valueJson(CB->getArgOperand(i));
    }
    s += "]";
  }
  if (const auto *SVI = dyn_cast<ShuffleVectorInst>(&I)) {
    s += ",\"mask\":[";
    ArrayRef<int> M = SVI->getShuffleMask();
    for (size_t i = 0; i < M.size(); ++i) {
      if (i) s += ",";
      s += std::to_string(M[i]);          // -1 is an undef/poison mask lane
    }
    s += "]";
  }
  if (const auto *BI = dyn_cast<BranchInst>(&I)) {
    s += ",\"conditional\":";
    s += BI->isConditional() ? "true" : "false";
    s += ",\"successors\":[";
    for (unsigned i = 0; i < BI->getNumSuccessors(); ++i) {
      if (i) s += ",";
      s += quote(blockLabel(BI->getSuccessor(i)));
    }
    s += "]";
  }
  if (const auto *CastI = dyn_cast<CastInst>(&I))
    s += ",\"src_type\":" + typeJson(CastI->getSrcTy());

  s += ",\"operands\":[";
  // A call's operand list ends with the callee; `args` above is the useful
  // view, but the raw operands are kept so nothing is hidden from the consumer.
  for (unsigned i = 0; i < I.getNumOperands(); ++i) {
    if (i) s += ",";
    s += valueJson(I.getOperand(i));
  }
  return s + "]}";
}

std::string paramAttrsJson(const Function &F, unsigned idx) {
  AttributeSet AS = F.getAttributes().getParamAttrs(idx);
  std::string s = "[";
  bool first = true;
  for (const Attribute &A : AS) {
    if (!first) s += ",";
    first = false;
    s += quote(A.getAsString());
  }
  return s + "]";
}

std::string functionJson(const Function &F) {
  std::string s = "{\"name\":" + quote(valueName(&F));
  s += ",\"ret\":" + typeJson(F.getReturnType());
  s += ",\"declaration\":";
  s += F.isDeclaration() ? "true" : "false";
  s += ",\"linkage\":";
  s += F.hasInternalLinkage()   ? "\"internal\""
       : F.hasPrivateLinkage()  ? "\"private\""
                                : "\"external\"";
  s += ",\"varargs\":";
  s += F.isVarArg() ? "true" : "false";

  s += ",\"params\":[";
  unsigned i = 0;
  for (const Argument &A : F.args()) {
    if (i) s += ",";
    s += "{\"name\":" + quote(valueName(&A)) +
         ",\"type\":" + typeJson(A.getType()) +
         ",\"attrs\":" + paramAttrsJson(F, i) + "}";
    ++i;
  }
  s += "]";

  s += ",\"blocks\":[";
  bool firstBB = true;
  for (const BasicBlock &BB : F) {
    if (!firstBB) s += ",";
    firstBB = false;
    s += "{\"name\":" + quote(blockLabel(&BB)) + ",\"instrs\":[";
    bool firstI = true;
    for (const Instruction &I : BB) {
      if (!firstI) s += ",";
      firstI = false;
      s += instJson(I);
    }
    s += "]}";
  }
  return s + "]}";
}

} // namespace

int main(int argc, char **argv) {
  if (argc != 2) {
    errs() << "usage: cv-ir-dump <module.ll>\n";
    return 2;
  }
  LLVMContext Ctx;
  SMDiagnostic Err;
  auto Buf = MemoryBuffer::getFileOrSTDIN(argv[1]);
  if (!Buf) {
    errs() << "cv-ir-dump: cannot read " << argv[1] << ": "
           << Buf.getError().message() << "\n";
    return 2;
  }
  // LLVM's own parser: the same one `opt` used to read this text.
  std::unique_ptr<Module> M = parseAssembly(Buf.get()->getMemBufferRef(), Err, Ctx);
  if (!M) {
    std::string s;
    raw_string_ostream os(s);
    Err.print("cv-ir-dump", os);
    errs() << os.str();
    return 1;
  }

  // THE POINTER SIZE IS NOT ALWAYS 64. `or.ll` in LLVM's own InstCombine tests declares
  // `p:32:32:32`, and there `ptrtoint ptr to i32` is EXACT rather than a truncation -- which is
  // what makes InstCombine's fold to `icmp eq ptr %A, null` correct. A model that assumes 64-bit
  // addresses reads that fold as unsound and REFUTES it. Emitting the real width lets a validator
  // decline what it cannot represent instead of inventing a disagreement.
  DL = &M->getDataLayout();
  // MODULE-LEVEL GLOBALS, WITH THEIR SIZES. A validator can only state that two objects do not
  // overlap if it knows how big they are -- address inequality is not enough, since a 4-byte store
  // at `@G1` and one at `@G1 + 1` are distinct addresses that still collide.
  std::string globals = "[";
  bool firstG = true;
  for (const GlobalVariable &GV : M->globals()) {
    if (!firstG) globals += ",";
    firstG = false;
    uint64_t sz = GV.getValueType()->isSized()
                      ? DL->getTypeAllocSize(GV.getValueType()).getFixedValue() : 0;
    // `constant` matters: a MUTABLE global's contents at entry are arbitrary (a caller may have
    // written it), but a CONSTANT one's are fixed and LLVM folds using them. A model that gives
    // both arbitrary contents is right about the first and wrong about the second.
    globals += "{\"name\":" + quote(valueName(&GV)) +
               ",\"bytes\":" + std::to_string(sz) +
               ",\"constant\":" + (GV.isConstant() ? "true" : "false") + "}";
  }
  globals += "]";
  std::string out = "{\"globals\":" + globals + ",\"ptr_bits\":" +
                    std::to_string(M->getDataLayout().getPointerSizeInBits(0)) +
                    ",\"functions\":[";
  bool first = true;
  for (const Function &F : *M) {
    if (!first) out += ",";
    first = false;
    out += functionJson(F);
  }
  out += "]}";
  outs() << out << "\n";
  return 0;
}
