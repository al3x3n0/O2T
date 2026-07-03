namespace vendor_global {
namespace llvm {
struct Type {};
struct Constant {
  static Constant *getNullValue(Type *);
};
struct GlobalVariable {
  bool hasLocalLinkage();
  bool use_empty();
  Type *getValueType();
  void setInitializer(Constant *);
};

bool isGlobalInitializerDead(GlobalVariable *);
} // namespace llvm

using namespace llvm;

bool stripDormantInitializer(GlobalVariable *SubjectGlobal) {
  if (isGlobalInitializerDead(SubjectGlobal) &&
      SubjectGlobal->hasLocalLinkage() &&
      SubjectGlobal->use_empty()) {
    SubjectGlobal->setInitializer(
        Constant::getNullValue(SubjectGlobal->getValueType()));
    return true;
  }
  return false;
}
} // namespace vendor_global
