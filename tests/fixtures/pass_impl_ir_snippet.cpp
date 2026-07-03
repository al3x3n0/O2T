extern int helper(int);

int passImplIrSnippet(int X, bool Cond) {
  int Local = X + 1;
  if (Cond) {
    return helper(Local);
  }
  return helper(Local - 1);
}
