---
name: Wrong verdict (false proof / false refutation)
about: O2T reported proved/refuted incorrectly — the highest-priority bug class
title: "[wrong-verdict] "
labels: ["wrong-verdict", "soundness"]
---

<!-- A wrong verdict is the most serious bug in a verifier. Thank you for reporting it. -->

## Which way is it wrong?
- [ ] **False proof** — O2T said `proved` but the transform is actually unsound (most severe)
- [ ] **False refutation** — O2T said `refuted` but the transform is actually correct

## Inputs
The exact IR and/or pass source (paste, or attach the smallest reproducer you can):

```llvm
; before / pass source here
```

## Command and verdict
```
$ o2t ...           # the exact command
# the verdict O2T printed
```

## Why it's wrong
Point to ground truth — a concrete counterexample input, the real `opt` behavior, or the relevant
LLVM LangRef clause (e.g. the poison/UB semantics of a flag).

## Environment
Output of `o2t doctor` (tool versions matter — especially the Z3 and LLVM 18 versions):

```
$ o2t doctor
```
