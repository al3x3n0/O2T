bool customLegalityCheck(int X);
bool otherCheck(int X);

void llmSelectedPredicate(int X) {
  if (customLegalityCheck(X)) {
    return;
  }
  if (otherCheck(X)) {
    return;
  }
}
