namespace llvm {
struct Value {};
struct Instruction {};
struct Constant { static Value *getNullValue(int); };
bool match(Value *, int);
int m_Zero();
int m_One();
Value *replaceInstUsesWith(Instruction &, Value *);
} // namespace llvm

using namespace llvm;

// A multi-branch fold for the ADD instruction. Branch 1 is a planted bug:
// (add x, x -> x) is unsound. Branch 2 is dead (duplicate of branch 0).
Value *foldAddMulti(Value *Op0, Value *Op1, Instruction &I) {
  if (match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  if (Op0 == Op1) {
    return replaceInstUsesWith(I, Op0);
  }
  if (match(Op1, m_Zero())) {
    return replaceInstUsesWith(I, Op0);
  }
  return Op0;
}
