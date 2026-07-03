# Repository Guidelines

## Project Structure & Module Organization

O2T is a C++17 and Python toolchain for generating and checking LLVM IR test cases. Public C++ headers live in `include/o2t/`, with core implementations in `src/`. CLI tools and workflow helpers are in `tools/`; KLEE-oriented harnesses are in `harnesses/`. Tests are under `tests/`, with reusable fixtures in `tests/fixtures/`. Example generator configs live in `examples/`, design notes in `docs/`, and pass constraint data in `constraints/`.

Treat `build/`, `build-llvm/`, `build-clang-tools/`, `klee-out*`, `opt/`, object files, and generated `examples/*.ll` files as build artifacts.

The `o2t/symexec/` subsystem symbolically executes the **real compiled C++** of custom LLVM-API pass folds and discharges poison/UB-aware refinement per control-flow path (an under-guarded pass is refuted with a witness). To catch up on it, read `docs/symexec_real_pass.md`; it is gated by `symexec_real_pass_fixture` / `klee_symexec_fixture`.

## Build, Test, and Development Commands

- `cmake -S . -B build`: configure the default build without requiring LLVM or KLEE development packages.
- `cmake --build build`: compile the core library, harness, tools, and tests.
- `ctest --test-dir build --output-on-failure`: run the CTest suite with failure logs.
- `cmake -S . -B build-llvm -DO2T_WITH_LLVM=ON -DLLVM_DIR=/path/to/lib/cmake/llvm`: configure the optional LLVM-backed probe build.
- `build/cv-replay --config examples/add_zero.cfg --out /tmp/add_zero.ll`: replay a saved config into LLVM IR.

## Coding Style & Naming Conventions

C++ code uses C++17, `cv::` namespace types, two-space indentation, and compiler warnings `-Wall -Wextra -Wpedantic`. Prefer PascalCase for types and enums, camelCase for functions and fields, and descriptive enum values such as `LoopShape::CountedLoop`. Keep headers in `include/o2t/` paired with focused implementations in `src/`.

Python tools use `#!/usr/bin/env python3`, `argparse` CLIs, `pathlib.Path`, and JSON or JSONL artifacts. Keep tool names in the existing `cv-*` pattern.

## Testing Guidelines

CTest is the primary test runner. Add focused coverage to `tests/generator_tests.cpp` for core generator behavior, and add fixture-driven tests in CMake when validating Python tools, shell workflows, manifests, or probe outputs. Place reusable fake tools and sample inputs in `tests/fixtures/`. Run `ctest --test-dir build --output-on-failure` before submitting changes that affect behavior.

## Commit & Pull Request Guidelines

This checkout does not include Git history, so use a simple imperative convention: `area: concise change`, for example `tools: validate manifest status`. Keep commits scoped to one logical change.

Pull requests should explain the purpose, summarize behavior changes, list commands run, and mention any generated artifact locations relevant to review. Link issues when available, and include sample output or screenshots only when they clarify CLI or workflow changes.
