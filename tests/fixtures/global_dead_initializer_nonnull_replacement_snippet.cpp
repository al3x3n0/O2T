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

bool replaceDeadGlobalInitializerWithOtherValue(GlobalVariable *GV, Constant *SomeOtherValue) {
  if (isGlobalInitializerDead(GV) && GV->hasLocalLinkage() && GV->use_empty()) {
    GV->setInitializer(SomeOtherValue);
    return true;
  }
  return false;
}
