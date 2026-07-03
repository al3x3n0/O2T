namespace llvm {
struct Value {};
struct Instruction {};

namespace PatternMatch {
struct Pattern {};
Pattern m_Zero();
Pattern m_One();
template <typename T> Pattern m_SplatOrPoison(T);
bool match(Value *, Pattern);
} // namespace PatternMatch

bool VectorXorSelf(Value *, Value *);
bool isIdentityMask(Instruction &);
bool isSplatMask(Instruction &);
bool sameLaneExtractInsert(Instruction &);
bool ReductionAddZero(Instruction &);
bool VectorSubZero(Value *);
bool VectorOrZero(Value *);
bool VectorAndAllOnes(Value *);
bool VectorSMin(Value *, Value *);
bool VectorSMax(Value *, Value *);
bool VectorUMin(Value *, Value *);
bool VectorUMax(Value *, Value *);
bool VectorAbs(Value *);
bool insertExtractIdentity(Instruction &);
bool ReductionAddSingleLane(Instruction &);
bool ScalableVectorAddZero(Value *);
bool ScalableVectorMulOne(Value *);
bool ScalableVectorXorSelf(Value *);
bool ScalableVectorSubZero(Value *);
bool ScalableVectorOrZero(Value *);
bool ScalableVectorAndAllOnes(Value *);
bool ScalableReductionAddZero(Instruction &);
} // namespace llvm

using namespace llvm;
using namespace PatternMatch;

void vectorLike(Value *Vec0, Value *Vec1, Instruction &I) {
  if (match(Vec1, m_SplatOrPoison(m_Zero()))) {
    return;
  }
  if (match(Vec1, m_SplatOrPoison(m_One()))) {
    return;
  }
  if (VectorXorSelf(Vec0, Vec1)) {
    return;
  }
  if (isIdentityMask(I)) {
    return;
  }
  if (isSplatMask(I)) {
    return;
  }
  if (sameLaneExtractInsert(I)) { // extract insert same lane
    return;
  }
  if (ReductionAddZero(I)) {
    return;
  }
  if (VectorSubZero(Vec0)) {
    return;
  }
  if (VectorOrZero(Vec0)) {
    return;
  }
  if (VectorAndAllOnes(Vec0)) {
    return;
  }
  if (VectorSMin(Vec0, Vec1)) {
    return;
  }
  if (VectorSMax(Vec0, Vec1)) {
    return;
  }
  if (VectorUMin(Vec0, Vec1)) {
    return;
  }
  if (VectorUMax(Vec0, Vec1)) {
    return;
  }
  if (VectorAbs(Vec0)) {
    return;
  }
  if (insertExtractIdentity(I)) { // insert extract identity
    return;
  }
  if (ReductionAddSingleLane(I)) {
    return;
  }
  if (ScalableVectorAddZero(Vec0)) {
    return;
  }
  if (ScalableVectorMulOne(Vec0)) {
    return;
  }
  if (ScalableVectorXorSelf(Vec0)) {
    return;
  }
  if (ScalableVectorSubZero(Vec0)) {
    return;
  }
  if (ScalableVectorOrZero(Vec0)) {
    return;
  }
  if (ScalableVectorAndAllOnes(Vec0)) {
    return;
  }
  if (ScalableReductionAddZero(I)) {
    return;
  }
}
