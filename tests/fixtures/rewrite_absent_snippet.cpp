namespace llvm {
struct Value {};
bool match(Value *, int);
int m_Zero();
} // namespace llvm

using namespace llvm;

Value *nonRewriteBody(Value *Op1) {
  if (match(Op1, m_Zero())) {
    int Local = 0;
    (void)Local;
  }
  return Op1;
}
