<!-- Thanks for contributing! Keep the change to one logical thing. -->

## What & why
<!-- What does this change, and why? Link any issue it closes. -->

## Soundness (the one rule)
- [ ] This change **cannot** make an unsound transform report `proved`.
- [ ] Anything it cannot model is **declined** (`unsupported`), not silently approximated.
- [ ] Any LLM/proposed semantics are ratified by an independent oracle (Z3 / real `opt` / `lli`),
      not trusted directly. *(N/A if not applicable.)*

## Tests (fixture is not optional)
- [ ] A `tests/fixtures/*.py` fixture gates this change, registered in `CMakeLists.txt`.
- [ ] It has **two-sided teeth**: proves a valid case **and** refutes-with-a-witness or declines an
      invalid one (ideally a seeded-adversarial case).
- [ ] `ctest --test-dir build` is green locally (note any fixtures skipped for a missing tool).

Which fixture gates this change: `______`

## Notes
<!-- Docs updated (SOURCES.md / capabilities.md / CHANGELOG.md)? Anything reviewers should focus on? -->
