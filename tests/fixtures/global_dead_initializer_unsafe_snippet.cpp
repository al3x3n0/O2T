namespace llvm {
struct Type {};
struct Constant {
  static Constant *getNullValue(Type *);
};
struct GlobalVariable {
  Type *getValueType();
  void setInitializer(Constant *);
};

bool isGlobalInitializerDead(GlobalVariable *);
} // namespace llvm

using namespace llvm;

bool removeUnsafeGlobalInitializer(GlobalVariable *GV) {
  if (isGlobalInitializerDead(GV)) {
    GV->setInitializer(Constant::getNullValue(GV->getValueType()));
    return true;
  }
  return false;
}
