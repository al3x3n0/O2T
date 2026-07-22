cmake_minimum_required(VERSION 3.16)

project(O2T LANGUAGES C CXX)

option(O2T_BUILD_TESTS "Build O2T tests" ON)
option(O2T_WITH_KLEE "Compile harnesses against real KLEE headers" OFF)
option(O2T_WITH_LLVM "Enable LLVM-backed probe backend when implemented" OFF)
option(O2T_USE_LLVM_BACKEND "Select LLVM probe backend for harnesses" OFF)
option(O2T_BUILD_CLANG_TOOLS "Build Clang LibTooling instrumentation tools" OFF)

foreach(_o2t_option BUILD_TESTS WITH_KLEE WITH_LLVM USE_LLVM_BACKEND BUILD_CLANG_TOOLS)
    if(DEFINED COMPILERVERIF_${_o2t_option})
        set(O2T_${_o2t_option} "${COMPILERVERIF_${_o2t_option}}" CACHE BOOL "Legacy CompilerVerif alias" FORCE)
    endif()
endforeach()

add_library(o2t_core
    src/GeneratorConfig.cpp
    src/IRTextGenerator.cpp
    src/ProbeBackend.cpp
)
add_library(compilerverif_core ALIAS o2t_core)

target_include_directories(o2t_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(o2t_core PUBLIC cxx_std_17)

target_compile_options(o2t_core PRIVATE
    -Wall
    -Wextra
    -Wpedantic
)

if(O2T_WITH_KLEE)
    target_compile_definitions(o2t_core PUBLIC O2T_WITH_KLEE=1 COMPILERVERIF_WITH_KLEE=1)
endif()
if(O2T_WITH_LLVM)
    find_package(LLVM REQUIRED CONFIG)
    message(STATUS "Found LLVM ${LLVM_PACKAGE_VERSION}")
    target_include_directories(o2t_core PRIVATE ${LLVM_INCLUDE_DIRS})
    separate_arguments(O2T_LLVM_DEFINITIONS_LIST NATIVE_COMMAND "${LLVM_DEFINITIONS}")
    target_compile_options(o2t_core PRIVATE ${O2T_LLVM_DEFINITIONS_LIST})
    target_compile_definitions(o2t_core PUBLIC O2T_WITH_LLVM=1 COMPILERVERIF_WITH_LLVM=1)
    llvm_map_components_to_libnames(O2T_LLVM_LIBS
        support
        core
        irreader
        asmparser
        passes
        analysis
        transformutils
        instcombine
        scalaropts
        ipo
    )
    target_link_libraries(o2t_core PRIVATE ${O2T_LLVM_LIBS})
endif()
if(O2T_USE_LLVM_BACKEND)
    target_compile_definitions(o2t_core PUBLIC O2T_USE_LLVM_BACKEND=1 COMPILERVERIF_USE_LLVM_BACKEND=1)
endif()

add_executable(cv-replay tools/cv-replay.cpp)
target_link_libraries(cv-replay PRIVATE o2t_core)
target_compile_options(cv-replay PRIVATE -Wall -Wextra -Wpedantic)

add_executable(cv-reduce-config tools/cv-reduce-config.cpp)
target_link_libraries(cv-reduce-config PRIVATE o2t_core)
target_compile_options(cv-reduce-config PRIVATE -Wall -Wextra -Wpedantic)

add_executable(cv-instcombine-harness harnesses/instcombine_harness.cpp)
target_link_libraries(cv-instcombine-harness PRIVATE o2t_core)
target_compile_options(cv-instcombine-harness PRIVATE -Wall -Wextra -Wpedantic)

add_executable(cv-probe-demo tools/cv-probe-demo.cpp)
target_link_libraries(cv-probe-demo PRIVATE o2t_core)
target_compile_options(cv-probe-demo PRIVATE -Wall -Wextra -Wpedantic)

add_executable(cv-probe-oracle tools/cv-probe-oracle.cpp)
target_link_libraries(cv-probe-oracle PRIVATE o2t_core)
target_compile_options(cv-probe-oracle PRIVATE -Wall -Wextra -Wpedantic)

if(O2T_WITH_LLVM)
    add_executable(cv-llvm-probe tools/cv-llvm-probe.cpp)
    target_link_libraries(cv-llvm-probe PRIVATE o2t_core)
    target_compile_options(cv-llvm-probe PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(O2T_BUILD_CLANG_TOOLS)
    find_package(LLVM REQUIRED CONFIG)
    find_package(Clang REQUIRED CONFIG)
    llvm_map_components_to_libnames(O2T_SOURCE_MINING_LLVM_LIBS
        support
    )

    add_library(o2t_source_mining
        src/SourceProgramGraph.cpp
    )
    add_library(compilerverif_source_mining ALIAS o2t_source_mining)
    target_include_directories(o2t_source_mining
        PUBLIC
            ${CMAKE_CURRENT_SOURCE_DIR}/include
        SYSTEM PRIVATE
            ${LLVM_INCLUDE_DIRS}
    )
    target_compile_features(o2t_source_mining PUBLIC cxx_std_17)
    target_compile_options(o2t_source_mining PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(o2t_source_mining PRIVATE
        ${O2T_SOURCE_MINING_LLVM_LIBS}
    )

    add_executable(cv-instrument-pass-source tools/cv-instrument-pass-source.cpp)
    target_include_directories(cv-instrument-pass-source SYSTEM PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${LLVM_INCLUDE_DIRS}
        ${CLANG_INCLUDE_DIRS}
    )
    target_compile_features(cv-instrument-pass-source PRIVATE cxx_std_17)
    target_compile_options(cv-instrument-pass-source PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(cv-instrument-pass-source PRIVATE
        clangTooling
        clangASTMatchers
        clangRewrite
        clangBasic
        clangFrontend
    )

    add_executable(cv-mine-pass-source-ast tools/cv-mine-pass-source-ast.cpp)
    target_include_directories(cv-mine-pass-source-ast SYSTEM PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
        ${LLVM_INCLUDE_DIRS}
        ${CLANG_INCLUDE_DIRS}
    )
    target_compile_features(cv-mine-pass-source-ast PRIVATE cxx_std_17)
    target_compile_options(cv-mine-pass-source-ast PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(cv-mine-pass-source-ast PRIVATE
        o2t_source_mining
        clangAnalysis
        clangTooling
        clangASTMatchers
        clangBasic
        clangFrontend
        clangLex
    )

    llvm_map_components_to_libnames(O2T_PASS_IMPL_IR_LLVM_LIBS
        core
        irreader
        analysis
        support
        passes
    )
    add_executable(cv-mine-pass-impl-ir tools/cv-mine-pass-impl-ir.cpp)
    target_include_directories(cv-mine-pass-impl-ir SYSTEM PRIVATE
        ${LLVM_INCLUDE_DIRS}
    )
    target_compile_features(cv-mine-pass-impl-ir PRIVATE cxx_std_17)
    target_compile_options(cv-mine-pass-impl-ir PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(cv-mine-pass-impl-ir PRIVATE
        ${O2T_PASS_IMPL_IR_LLVM_LIBS}
    )

    add_executable(cv-sourcegraph-legacy-namespace-tests tests/sourcegraph_legacy_namespace_tests.cpp)
    target_include_directories(cv-sourcegraph-legacy-namespace-tests SYSTEM PRIVATE
        ${LLVM_INCLUDE_DIRS}
    )
    target_compile_features(cv-sourcegraph-legacy-namespace-tests PRIVATE cxx_std_17)
    target_compile_options(cv-sourcegraph-legacy-namespace-tests PRIVATE -Wall -Wextra -Wpedantic)
    target_link_libraries(cv-sourcegraph-legacy-namespace-tests PRIVATE compilerverif_source_mining)
endif()

if(O2T_BUILD_TESTS)
    enable_testing()
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    find_program(Z3_EXECUTABLE z3)
    if(TARGET cv-mine-pass-source-ast)
        set(O2T_AST_MINER "$<TARGET_FILE:cv-mine-pass-source-ast>")
    else()
        set(O2T_AST_MINER "${CMAKE_CURRENT_BINARY_DIR}/cv-mine-pass-source-ast")
    endif()

    add_executable(cv-generator-tests tests/generator_tests.cpp)
    target_link_libraries(cv-generator-tests PRIVATE o2t_core)
    target_compile_options(cv-generator-tests PRIVATE -Wall -Wextra -Wpedantic)

    add_executable(cv-legacy-target-compat-tests tests/legacy_target_compat_tests.cpp)
    target_link_libraries(cv-legacy-target-compat-tests PRIVATE compilerverif_core)
    target_compile_options(cv-legacy-target-compat-tests PRIVATE -Wall -Wextra -Wpedantic)

    add_test(NAME generator_tests COMMAND cv-generator-tests)
    add_test(NAME legacy_target_compat_tests COMMAND cv-legacy-target-compat-tests)
    if(TARGET cv-sourcegraph-legacy-namespace-tests)
        add_test(NAME sourcegraph_legacy_namespace_tests COMMAND cv-sourcegraph-legacy-namespace-tests)
    endif()
    add_test(
        NAME package_import_compat_fixture
        COMMAND
            ${CMAKE_COMMAND} -E env "PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}"
            ${Python3_EXECUTABLE}
            -c "import o2t; import compilerverif; import o2t.symexec.modelcheck_intents; import compilerverif.symexec.modelcheck_intents"
    )
    add_test(
        NAME python_package_wheel_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/python_package_wheel_fixture.py
            --repo ${CMAKE_CURRENT_SOURCE_DIR}
            --work-dir ${CMAKE_CURRENT_BINARY_DIR}/python-package-wheel
    )
    add_test(
        NAME cmake_legacy_option_compat_fixture
        COMMAND
            /bin/sh -c
            "${CMAKE_COMMAND} -S ${CMAKE_CURRENT_SOURCE_DIR} -B ${CMAKE_CURRENT_BINARY_DIR}/cmake-legacy-option-compat -DCOMPILERVERIF_BUILD_TESTS=OFF -DCOMPILERVERIF_BUILD_CLANG_TOOLS=OFF -DCOMPILERVERIF_WITH_KLEE=ON -DCOMPILERVERIF_USE_LLVM_BACKEND=ON && grep -q '^O2T_BUILD_TESTS:BOOL=OFF$' ${CMAKE_CURRENT_BINARY_DIR}/cmake-legacy-option-compat/CMakeCache.txt && grep -q '^O2T_BUILD_CLANG_TOOLS:BOOL=OFF$' ${CMAKE_CURRENT_BINARY_DIR}/cmake-legacy-option-compat/CMakeCache.txt && grep -q '^O2T_WITH_KLEE:BOOL=ON$' ${CMAKE_CURRENT_BINARY_DIR}/cmake-legacy-option-compat/CMakeCache.txt && grep -q '^O2T_USE_LLVM_BACKEND:BOOL=ON$' ${CMAKE_CURRENT_BINARY_DIR}/cmake-legacy-option-compat/CMakeCache.txt"
    )
    add_test(
        NAME cmake_legacy_with_llvm_option_compat_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/cmake_legacy_with_llvm_fixture.py
            --repo ${CMAKE_CURRENT_SOURCE_DIR}
            --work-dir ${CMAKE_CURRENT_BINARY_DIR}/cmake-legacy-with-llvm-option-compat
            --cmake ${CMAKE_COMMAND}
    )
    add_test(
        NAME ktest_extract_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-ktest-extract.py
            --dump-dir ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures
            --cases ${CMAKE_CURRENT_BINARY_DIR}/ktest-fixture-cases
            --replay $<TARGET_FILE:cv-replay>
    )
    add_test(
        NAME ktest_extract_reduce_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-ktest-extract.py
            --dump-dir ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures
            --cases ${CMAKE_CURRENT_BINARY_DIR}/ktest-fixture-reduced-cases
            --replay $<TARGET_FILE:cv-replay>
            --reduce
            --reducer $<TARGET_FILE:cv-reduce-config>
    )
    add_test(
        NAME reduce_failing_config_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-reduce-failing-config.py --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/composed_diamond_mem_loop.cfg --replay $<TARGET_FILE:cv-replay> --oracle \"grep -q loop.header {ll}\" --out ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.cfg --report ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.json && $<TARGET_FILE:cv-replay> --config ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.cfg --out ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.ll && grep -q loop.header ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.ll && grep -q '^compose_bits=0$' ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.cfg && grep -q '^memory_shape=0$' ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.cfg && grep -q '^shape=0$' ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.cfg && ! grep -q '^loop_shape=0$' ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.cfg && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r[\"oracle_calls\"] > 0; assert \"compose_bits\" in r[\"changed_fields\"]; assert r[\"reduced_config\"][\"loop_shape\"] != 0' ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/reduced.json"
    )
    add_test(
        NAME reduce_failing_config_uninteresting_start_fixture
        COMMAND
            /bin/sh -c
            "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-reduce-failing-config.py --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/composed_diamond_mem_loop.cfg --replay $<TARGET_FILE:cv-replay> --oracle \"grep -q cv_does_not_exist_marker {ll}\" --out ${CMAKE_CURRENT_BINARY_DIR}/reduce-failing/should-not-appear.cfg); test $? -eq 1"
    )
    add_test(
        NAME minimize_campaign_failures_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign && printf '%s\\n' '{\"case\":\"composed_fail\",\"config\":\"${CMAKE_CURRENT_SOURCE_DIR}/examples/composed_diamond_mem_loop.cfg\",\"status\":\"failed\",\"message\":\"fake failure\",\"passes\":\"instcombine\"}' '{\"case\":\"scalar_ok\",\"config\":\"${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg\",\"status\":\"passed\",\"message\":\"\",\"passes\":\"instcombine\"}' > ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-minimize-campaign-failures.py --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/manifest.jsonl --out-dir ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/out --reducer ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-reduce-failing-config.py --replay $<TARGET_FILE:cv-replay> --oracle \"grep -q loop.header {ll}\" && test -f ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/out/composed_fail.cfg && ! test -f ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/out/scalar_ok.cfg && grep -q '^compose_bits=0$' ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/out/composed_fail.cfg && ! grep -q '^loop_shape=0$' ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/out/composed_fail.cfg && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed_cases\"]==1; assert s[\"minimized\"]==1; assert s[\"results\"][0][\"oracle_calls\"]>0' ${CMAKE_CURRENT_BINARY_DIR}/minimize-campaign/out/summary.json"
    )
    add_test(
        NAME extended_identities_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-identities.py --no-z3 --require-all --widths 8,16,32,64 --report ${CMAKE_CURRENT_BINARY_DIR}/id/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0; assert s[\"identities\"]>=17; fams=s[\"family_proofs\"]; assert {\"reassociate\",\"instsimplify\",\"shift\"} <= set(fams), fams; assert all(v==\"proved\" for r in s[\"results\"] for v in r[\"widths\"].values())' ${CMAKE_CURRENT_BINARY_DIR}/id/report.json"
    )
    add_test(
        NAME negative_intents_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-check-negative-intents.py --no-z3 --report ${CMAKE_CURRENT_BINARY_DIR}/neg/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"negatives\"]==7, s[\"negatives\"]; assert s[\"failures\"]==0; assert s[\"rejected\"]==7; assert all(r[\"witness\"] and r[\"witness\"][\"before\"]!=r[\"witness\"][\"after\"] for r in s[\"results\"] if r[\"kind\"]==\"negative\")' ${CMAKE_CURRENT_BINARY_DIR}/neg/report.json"
    )
    add_test(
        NAME multiwidth_encoding_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-multiwidth.py --no-z3 --widths 8,16,32,64 --report ${CMAKE_CURRENT_BINARY_DIR}/mw/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"intents_checked\"]>=11, s[\"intents_checked\"]; assert s[\"failed\"]==0 and s[\"skipped\"]==0; assert all(v==\"encoded\" for r in s[\"results\"] for v in r[\"widths\"].values())' ${CMAKE_CURRENT_BINARY_DIR}/mw/report.json"
    )
    add_test(
        NAME multiwidth_extreme_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-multiwidth.py --z3 z3 --widths 1,4,17,128 --require-all --report ${CMAKE_CURRENT_BINARY_DIR}/mwx/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s[\"counterexamples\"]; assert s[\"proved\"]>=20, s; w1=[r[\"widths\"].get(\"1\") for r in s[\"results\"]]; w17=[r[\"widths\"].get(\"17\") for r in s[\"results\"]]; assert \"proved\" in w1 and \"proved\" in w17, (w1,w17)' ${CMAKE_CURRENT_BINARY_DIR}/mwx/report.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-identities.py --z3 z3 --widths 1,4,17,128 --require-all --report ${CMAKE_CURRENT_BINARY_DIR}/mwx/id.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s; assert s[\"proved\"]>=60, s' ${CMAKE_CURRENT_BINARY_DIR}/mwx/id.json; else echo 'multiwidth_extreme_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mini_alive_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/mini-alive && printf '%s\\n' 'define i8 @before(i8 %a) {' 'entry:' '  %t0 = add i8 %a, 0' '  ret i8 %t0' '}' > ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/sound_before.ll && printf '%s\\n' 'define i8 @after(i8 %a) {' 'entry:' '  ret i8 %a' '}' > ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/id_after.ll && printf '%s\\n' 'define i8 @before(i8 %a) {' 'entry:' '  %t0 = shl i8 %a, 1' '  ret i8 %t0' '}' > ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/bad_before.ll && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mini-alive.py --no-z3 --before ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/sound_before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/id_after.ll --out ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/sound.json); test $? -eq 0 && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mini-alive.py --no-z3 --before ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/bad_before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/id_after.ll --out ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/bad.json); test $? -eq 1 && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"status\"]==\"proved\", s; b=json.load(open(sys.argv[2])); assert b[\"status\"]==\"refuted\", b; w=b[\"detail\"]; assert w[\"before\"]!=w[\"after\"], w' ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/sound.json ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/bad.json"
    )
    add_test(
        NAME mini_alive_flag_refinement_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/mini-alive && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mini-alive.py --selftest --z3-bin z3 > ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/flag-selftest.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); f=s[\"flag_refinement\"]; assert f[\"proved\"]>=4 and f[\"failed\"]==0, s; assert s[\"selftest\"][\"failed\"]==0, s' ${CMAKE_CURRENT_BINARY_DIR}/mini-alive/flag-selftest.json; else echo 'mini_alive_flag_refinement_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME lift_grammar_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-lift-grammar-selftest.py --no-z3 --report ${CMAKE_CURRENT_BINARY_DIR}/lift/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s; assert s[\"structural_ok\"]==s[\"cases\"]; shapes={r[\"shape\"] for r in s[\"results\"]}; assert {\"shift\",\"neg\",\"not\",\"select\",\"facts-shift\"} <= shapes, shapes; assert all(r[\"status\"]==\"structural-ok\" for r in s[\"results\"])' ${CMAKE_CURRENT_BINARY_DIR}/lift/report.json"
    )
    add_test(
        NAME alive2_check_fixture
        COMMAND
            /bin/sh -c
            "if command -v alive-tv >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-alive2-check-ir.py --selftest --out ${CMAKE_CURRENT_BINARY_DIR}/alive2/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; cs={c[\"case\"]:c[\"status\"] for c in d[\"alive2_selftest\"]}; assert cs[\"sound\"]==\"proved\", cs; assert cs[\"unsound\"]==\"failed\", cs' ${CMAKE_CURRENT_BINARY_DIR}/alive2/report.json; else echo 'alive2_check_fixture: alive-tv not found, skipped'; fi"
    )
    add_test(
        NAME prove_flags_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-flags.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/proveflags/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert d[\"cases\"]>=6, d; assert d[\"failed\"]==0, d; v={r[\"case\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"add-nsw-unsound\"]==\"sat\", v; assert v[\"drop-nsw-add-sound\"]==\"unsat\", v; assert v[\"shl-nuw-lshr-roundtrip-sound\"]==\"unsat\" and v[\"shl-lshr-roundtrip-unsound\"]==\"sat\", v' ${CMAKE_CURRENT_BINARY_DIR}/proveflags/report.json; else echo 'prove_flags_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_multi_instr_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-multi-instr.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/multiinstr/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0 and d[\"cases\"]>=7, d; v={r[\"case\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"reassociate-add-chain-sound\"]==\"unsat\" and v[\"distribute-mul-over-add-sound\"]==\"unsat\", v; assert v[\"gvn-sub-noncommutative-unsound\"]==\"sat\" and v[\"factor-wrong-sign-unsound\"]==\"sat\", v' ${CMAKE_CURRENT_BINARY_DIR}/multiinstr/report.json; else echo 'prove_multi_instr_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_cfg_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-cfg.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/cfg/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0 and d[\"cases\"]>=6, d; v={r[\"case\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"nested-branch-collapse-and-sound\"]==\"unsat\", v; assert v[\"nested-branch-collapse-or-unsound\"]==\"sat\", v; assert v[\"branch-nontautology-fold-unsound\"]==\"sat\", v' ${CMAKE_CURRENT_BINARY_DIR}/cfg/report.json; else echo 'prove_cfg_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_interproc_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-interproc.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/interproc/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0 and d[\"cases\"]>=5, d; v={r[\"case\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"inline-distribute-sound\"]==\"unsat\", v; assert v[\"inline-arg-swap-unsound\"]==\"sat\" and v[\"dead-arg-drop-used-unsound\"]==\"sat\", v' ${CMAKE_CURRENT_BINARY_DIR}/interproc/report.json; else echo 'prove_interproc_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mine_branch_shapes_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-shapes.py --selftest --z3-bin z3 --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/branch_shapes.cpp --report ${CMAKE_CURRENT_BINARY_DIR}/mineshapes/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0, d; v={r[\"transform\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"nestedCollapse_after\"]==\"unsat\", v; assert v[\"nestedCollapse_unsound_after\"]==\"sat\", v; assert v[\"identicalArms_after\"]==\"unsat\", v; assert v[\"loopSum_after\"]==\"unsat\" and v[\"loopLicm_after\"]==\"unsat\", v; assert v[\"loopSum_unsound_after\"]==\"sat\", v' ${CMAKE_CURRENT_BINARY_DIR}/mineshapes/report.json; else echo 'mine_branch_shapes_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mine_nonlocal_select_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && test -x ${O2T_AST_MINER}; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-nonlocal.py --selftest --z3-bin z3 --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/select_folds.cpp --miner ${O2T_AST_MINER} --report ${CMAKE_CURRENT_BINARY_DIR}/minenonlocal/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert d[\"proved\"]>=2, d; assert any(\"m_Select\" in r[\"matcher\"] and r[\"status\"]==\"proved\" for r in d[\"results\"]), d; assert any(\"m_SpecificICmp\" in r[\"matcher\"] and r[\"status\"]==\"proved\" for r in d[\"results\"]), (\"icmp-guarded select missing\", d)' ${CMAKE_CURRENT_BINARY_DIR}/minenonlocal/report.json; else echo 'mine_nonlocal_select_fixture: miner/z3 not present, skipped'; fi"
    )
    add_test(
        NAME prove_loop_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-loop.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/loop/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0 and d[\"cases\"]>=5, d; v={r[\"case\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"licm-invariant-hoist-sound\"]==\"unsat\" and v[\"loop-unswitch-invariant-sound\"]==\"unsat\", v; assert v[\"licm-variant-hoist-unsound\"]==\"sat\" and v[\"loop-unswitch-variant-unsound\"]==\"sat\", v' ${CMAKE_CURRENT_BINARY_DIR}/loop/report.json; else echo 'prove_loop_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mine_loop_invariant_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-loop-invariant.py --selftest --z3-bin z3 --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/branch_shapes.cpp --report ${CMAKE_CURRENT_BINARY_DIR}/mineloopinv/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"proved\"]>=2, d; r={x[\"function\"]:x for x in d[\"results\"]}; assert r[\"loopSum_before\"][\"status\"]==\"proved\" and \"n * a\" in r[\"loopSum_before\"][\"closed_form\"], r; assert r[\"loopLicm_before\"][\"status\"]==\"proved\", r; assert r[\"loopQuadratic_recurrence\"][\"status\"]==\"non-affine\", r' ${CMAKE_CURRENT_BINARY_DIR}/mineloopinv/report.json; else echo 'mine_loop_invariant_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_memory_loop_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-memory-loop.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/provmemloop/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0, d; r={x[\"case\"]:x for x in d[\"results\"]}; assert r[\"licm-load-sound\"][\"proved\"] is True, r; assert r[\"gvn-redundant-load-sound\"][\"proved\"] is True, r; assert r[\"different-array-unsound\"][\"proved\"] is False, r' ${CMAKE_CURRENT_BINARY_DIR}/provmemloop/report.json; else echo 'prove_memory_loop_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_cond_geom_loop_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-cond-geom-loop.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/provcondgeom/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0, d; r={x[\"case\"]:x for x in d[\"results\"]}; assert r[\"cond-invariant-closed-form\"][\"proved\"] is True, r; assert r[\"geom-commute-sound\"][\"proved\"] is True, r; assert r[\"geom-reassoc-sound\"][\"proved\"] is True, r; assert r[\"geom-wrong-factor-unsound\"][\"proved\"] is False, r' ${CMAKE_CURRENT_BINARY_DIR}/provcondgeom/report.json; else echo 'prove_cond_geom_loop_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mine_clang_pass_fixture
        COMMAND
            /bin/sh -c
            "CLANG=$(command -v clang || true); if [ -z \"$CLANG\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/clang ]; then CLANG=/opt/homebrew/opt/llvm@18/bin/clang; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$CLANG\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-clang-pass.py --selftest --z3-bin z3 --clang-bin \"$CLANG\" --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_pass_scev.cpp --stub ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/scev_pass_api.h --report ${CMAKE_CURRENT_BINARY_DIR}/mineclangpass/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"proved\"]>=1, d; r={x[\"transform\"]:x for x in d[\"results\"]}; sr=r[\"strengthReduce\"]; assert sr[\"status\"]==\"proved\", sr; rel=\" \".join(sr[\"relation\"]); assert \"B_k == (c * i)\" in rel and \"A_acc == B_acc\" in rel, sr; assert r[\"wrongStride\"][\"status\"]!=\"proved\", (\"wrong stride not caught\", r[\"wrongStride\"]); assert r[\"invariantOnly\"][\"status\"]==\"no-idiom\", (\"non-idiom not declared skipped\", r[\"invariantOnly\"])' ${CMAKE_CURRENT_BINARY_DIR}/mineclangpass/report.json; else echo 'mine_clang_pass_fixture: z3 or clang not found, skipped'; fi"
    )
    add_test(
        NAME translation_validation_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; CLANG=$(command -v clang || true); if [ -z \"$CLANG\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/clang ]; then CLANG=/opt/homebrew/opt/llvm@18/bin/clang; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ] && [ -n \"$CLANG\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-translation-validate.py --selftest --z3-bin z3 --opt-bin \"$OPT\" --clang-bin \"$CLANG\" --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/translation_validation.ll --report ${CMAKE_CURRENT_BINARY_DIR}/transval/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert all(s[\"clean\"] for s in d[\"sound\"]), (\"a sound pass was refuted\", d[\"sound\"]); assert d[\"closed_form\"][\"any_loop_eliminated\"] is True, (\"indvars closed-form not surfaced\", d[\"closed_form\"]); cf=d[\"closed_form\"]; assert cf[\"boundary_ops\"] and set(cf[\"boundary_ops\"]) <= {\"udiv\",\"trunc\",\"zext\",\"sext\",\"smax\",\"smin\",\"umax\",\"umin\"}, (\"loop-eliminated boundary ops not named\", cf.get(\"boundary_ops\")); assert any(r.get(\"closed_form_ops\") for r in cf[\"results\"] if r[\"status\"]==\"loop-eliminated\"), \"per-function closed_form_ops missing\"; assert {\"sumConst\",\"sumProduct\",\"shiftMul\",\"affineCombo\",\"sumSquares\",\"sumCubes\"} <= set(cf[\"formally_proved\"]), (\"affine..quartic loop->closed-form not all formally proved (incl. N-ary zext/trunc/udiv widening)\", cf.get(\"formally_proved\")); assert any(r[\"function\"]==\"sumCubes\" for r in cf[\"results\"] if r[\"status\"]==\"proved-closed-form\" and (r.get(\"widenings\") or 0)>=3), (\"quartic 4-factor widening not proved\", cf[\"results\"]); assert {\"sumSquares\",\"sumCubes\"} <= {r[\"function\"] for r in d[\"closed_form_teeth\"][\"results\"]}, (\"cubic/quartic formal teeth not exercised\", d[\"closed_form_teeth\"]); assert d[\"closed_form_teeth\"][\"caught\"] is True, (\"formal closed-form teeth not caught\", d[\"closed_form_teeth\"]); assert all(r.get(\"witness\") for r in d[\"closed_form_teeth\"][\"results\"]), (\"formal closed-form refutation lacks witness\", d[\"closed_form_teeth\"]); assert d[\"teeth\"][\"caught\"] is True, (\"mutated miscompile not caught\", d[\"teeth\"]); assert d[\"teeth\"][\"witnessed\"] is True, (\"refutation lacks a concrete witness\", d[\"teeth\"]); ws=[r[\"witness\"] for r in d[\"teeth\"][\"results\"] if r.get(\"witness\")]; assert ws and all(w[\"source\"]!=w[\"optimized\"] for w in ws), (\"witness does not diverge\", ws); proved=sum(1 for s in d[\"sound\"] for r in s[\"results\"] if r[\"status\"]==\"proved\"); assert proved>=20, (\"too few real proofs\", proved); assert len(d[\"sound\"])>=12, (\"too few passes validated\", len(d[\"sound\"])); sf=d.get(\"semiformal\"); assert sf is not None and sf[\"all_pass\"] is True, (\"semi-formal indvars differential did not pass\", sf); assert d[\"semiformal_teeth\"][\"caught\"] is True, (\"semi-formal teeth: corrupted indvars not caught\", d[\"semiformal_teeth\"])' ${CMAKE_CURRENT_BINARY_DIR}/transval/report.json; else echo 'translation_validation_fixture: z3, opt, or clang not found, skipped'; fi"
    )
    add_test(
        NAME mine_scev_relational_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-scev-relational.py --selftest --z3-bin z3 --opt-bin \"$OPT\" --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/scev_rotated_loops.ll --report ${CMAKE_CURRENT_BINARY_DIR}/scevrel/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"proved\"]>=1, d; r={x[\"transform\"]:x for x in d[\"results\"]}; sr=r[\"rotSR\"]; assert sr[\"status\"]==\"proved\", sr; rel=\" \".join(sr[\"relation\"]); assert \"B_k == (c * i)\" in rel and \"A_acc == B_acc\" in rel, sr; assert r[\"wrongStride\"][\"status\"]!=\"proved\", (\"wrong stride not caught\", r[\"wrongStride\"])' ${CMAKE_CURRENT_BINARY_DIR}/scevrel/report.json; else echo 'mine_scev_relational_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME mine_pass_scev_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-scev.py --selftest --z3-bin z3 --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_pass_scev.cpp --report ${CMAKE_CURRENT_BINARY_DIR}/minepassscev/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"proved\"]>=1, d; r={x[\"transform\"]:x for x in d[\"results\"]}; sr=r[\"strengthReduce\"]; assert sr[\"status\"]==\"proved\", sr; rel=\" \".join(sr[\"relation\"]); assert \"B_k == (c * i)\" in rel and \"A_acc == B_acc\" in rel, sr; assert r[\"wrongStride\"][\"status\"]!=\"proved\", (\"wrong stride not caught\", r[\"wrongStride\"]); assert r[\"invariantOnly\"][\"status\"]==\"no-idiom\", (\"non-idiom not declared skipped\", r[\"invariantOnly\"])' ${CMAKE_CURRENT_BINARY_DIR}/minepassscev/report.json; else echo 'mine_pass_scev_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mine_llvm_relational_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-llvm-relational.py --selftest --z3-bin z3 --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_loop_transforms.ll --report ${CMAKE_CURRENT_BINARY_DIR}/minellvmrel/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"proved\"]>=1, d; r={x[\"transform\"]:x for x in d[\"results\"]}; sr=r[\"strengthReduce\"]; assert sr[\"status\"]==\"proved\", sr; rel=\" \".join(sr[\"relation\"]); assert \"B_k == (c * i)\" in rel and \"A_acc == B_acc\" in rel, sr; assert r[\"wrongStride\"][\"status\"]!=\"proved\", (\"wrong stride not caught\", r[\"wrongStride\"])' ${CMAKE_CURRENT_BINARY_DIR}/minellvmrel/report.json; else echo 'mine_llvm_relational_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mine_llvm_loop_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-llvm-loop.py --selftest --z3-bin z3 --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_loops.ll --report ${CMAKE_CURRENT_BINARY_DIR}/minellvm/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"proved\"]>=4, d; r={x[\"function\"]:x for x in d[\"results\"]}; sc=r[\"sum_const\"][\"accumulators\"][0][\"invariant\"]; assert sc==\"acc == (a * i)\", sc; tri=r[\"triangular\"][\"accumulators\"][0][\"invariant\"]; assert tri and \"2*acc\" in tri and \"i * i\" in tri, tri; sq=r[\"sum_squares\"][\"accumulators\"][0][\"invariant\"]; assert sq and \"6*acc\" in sq, (\"cubic IR loop not proved\", sq)' ${CMAKE_CURRENT_BINARY_DIR}/minellvm/report.json; CLANG=/opt/homebrew/opt/llvm@18/bin/clang; if test -x $CLANG; then mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/minellvm && printf '%s\\n' 'void run(int *o, int a, int n){ int acc=0; for(int i=0;i<n;++i){ acc=acc+a; o[i]=acc; } }' > ${CMAKE_CURRENT_BINARY_DIR}/minellvm/run.c && $CLANG -O1 -S -emit-llvm -fno-vectorize -fno-unroll-loops ${CMAKE_CURRENT_BINARY_DIR}/minellvm/run.c -o ${CMAKE_CURRENT_BINARY_DIR}/minellvm/run.ll 2>/dev/null && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-llvm-loop.py --z3-bin z3 --source ${CMAKE_CURRENT_BINARY_DIR}/minellvm/run.ll --report ${CMAKE_CURRENT_BINARY_DIR}/minellvm/run.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); r=[x for x in d[\"results\"] if x[\"function\"]==\"run\"][0]; assert r[\"status\"]==\"proved\", (\"real clang -O1 loop not proved\", r); assert any(\" * i\" in (a[\"invariant\"] or \"\") for a in r[\"accumulators\"]), r' ${CMAKE_CURRENT_BINARY_DIR}/minellvm/run.json; else echo 'mine_llvm_loop_fixture: clang@18 absent, real-IR leg skipped'; fi; else echo 'mine_llvm_loop_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME synth_pairing_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-synth-pairing.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/synthpair/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0, d; perm=[x for x in d[\"results\"] if x[\"loop\"].startswith(\"permuted\")][0]; assert perm[\"pairing\"]=={\"s\":\"q\",\"t\":\"p\"}, perm; npar=[x for x in d[\"results\"] if x[\"loop\"].startswith(\"no partner\")][0]; assert npar[\"status\"]==\"no-partner\", npar' ${CMAKE_CURRENT_BINARY_DIR}/synthpair/report.json; else echo 'synth_pairing_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME mine_relational_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-relational.py --selftest --z3-bin z3 --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_transforms.cpp --report ${CMAKE_CURRENT_BINARY_DIR}/minerel/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"proved\"]>=2, d; r={x[\"transform\"]:x for x in d[\"results\"]}; sr=r[\"strengthReduce\"]; assert sr[\"status\"]==\"proved\", sr; assert sr[\"pairing\"]=={\"A_acc\":\"B_acc\"}, (\"wrong discovered pairing\", sr); rel=\" \".join(sr[\"relation\"]); assert \"B_k == (c * i)\" in rel and \"A_acc == B_acc\" in rel, sr; assert r[\"wrongOrder\"][\"status\"]!=\"proved\", (\"order bug not caught\", r[\"wrongOrder\"]); ms=r[\"multiSR\"]; assert ms[\"status\"]==\"proved\", ms; assert ms[\"pairing\"]=={\"A_acc1\":\"B_acc1\",\"A_acc2\":\"B_acc2\"}, (\"multi-output bijection wrong\", ms)' ${CMAKE_CURRENT_BINARY_DIR}/minerel/report.json; else echo 'mine_relational_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME synth_relational_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-synth-relational.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/synthrel/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0, d; sr=[x for x in d[\"results\"] if x[\"transform\"].startswith(\"strength\")][0]; assert sr[\"status\"]==\"proved\", sr; rel=\" \".join(sr[\"relation\"]); assert \"k == (c * i)\" in rel and \"accA == accB\" in rel, sr; ws=[x for x in d[\"results\"] if \"wrong stride\" in x[\"transform\"]][0]; assert ws[\"status\"]!=\"proved\", ws' ${CMAKE_CURRENT_BINARY_DIR}/synthrel/report.json; else echo 'synth_relational_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME synth_invariant_coupled_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-synth-invariant-coupled.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/synthcoupled/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0, d; r={x[\"loop\"]:x for x in d[\"results\"]}; sos=[x for x in d[\"results\"] if \"sum-of-sums\" in x[\"loop\"]][0]; inv={i[\"name\"]:i[\"invariant\"] for i in sos[\"invariants\"]}; assert inv[\"s\"] and \"a * i\" in inv[\"s\"], inv; assert inv[\"t\"] and \"2*t\" in inv[\"t\"] and \"i * i\" in inv[\"t\"], inv; cub=[x for x in d[\"results\"] if \"cubic\" in x[\"loop\"]][0]; assert cub[\"all_found\"] is False, cub' ${CMAKE_CURRENT_BINARY_DIR}/synthcoupled/report.json; else echo 'synth_invariant_coupled_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME synth_invariant_poly_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-synth-invariant-poly.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/synthpoly/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0 and d[\"models\"]>=4, d; tri=[x for x in d[\"results\"] if \"triangular\" in x[\"loop\"]][0]; assert tri[\"synthesized\"] and \"2*acc\" in tri[\"synthesized\"] and \"i * i\" in tri[\"synthesized\"], tri; cub=[x for x in d[\"results\"] if \"sum of squares\" in x[\"loop\"]][0]; assert cub[\"synthesized\"] and \"6*acc\" in cub[\"synthesized\"], (\"cubic not synthesized\", cub); q=[x for x in d[\"results\"] if \"quartic\" in x[\"loop\"]][0]; assert q[\"synthesized\"] is None, q' ${CMAKE_CURRENT_BINARY_DIR}/synthpoly/report.json; else echo 'synth_invariant_poly_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME synth_invariant_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-synth-invariant.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/synthinv/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0 and d[\"models\"]>=4, d; r={x[\"loop\"]:x for x in d[\"results\"]}; sr=[x for x in d[\"results\"] if \"strength-reduction\" in x[\"loop\"]][0]; assert sr[\"synthesized\"] and \"i * c\" in sr[\"synthesized\"], sr; q=[x for x in d[\"results\"] if \"quadratic\" in x[\"loop\"]][0]; assert q[\"synthesized\"] is None, q' ${CMAKE_CURRENT_BINARY_DIR}/synthinv/report.json; else echo 'synth_invariant_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_loop_induction_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-loop-induction.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/loopind/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"failed\"]==0 and d[\"cases\"]>=3, d; r={x[\"case\"]:x for x in d[\"results\"]}; sr=r[\"strength-reduction-unbounded-sound\"]; assert sr[\"base\"]==\"unsat\" and sr[\"step\"]==\"unsat\", sr; assert r[\"licm-lockstep-unbounded-sound\"][\"step\"]==\"unsat\", r; assert r[\"strength-reduction-wrong-stride-caught\"][\"step\"]==\"sat\", r' ${CMAKE_CURRENT_BINARY_DIR}/loopind/report.json; else echo 'prove_loop_induction_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_memory_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-memory.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/provemem/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert d[\"cases\"]>=6 and d[\"failed\"]==0, d; v={r[\"case\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"dse-dead-store-same-addr-sound\"]==\"unsat\", v; assert v[\"dse-dead-store-diff-addr-unsound\"]==\"sat\", v; assert v[\"store-load-forward-sound\"]==\"unsat\", v' ${CMAKE_CURRENT_BINARY_DIR}/provemem/report.json; else echo 'prove_memory_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_fastmath_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-prove-fastmath.py --selftest --z3-bin z3 --report ${CMAKE_CURRENT_BINARY_DIR}/fastmath/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert d[\"cases\"]>=5 and d[\"failed\"]==0, d; v={r[\"case\"]:r[\"verdict\"] for r in d[\"results\"]}; assert v[\"fadd-x-negx-zero-fastmath-sound\"]==\"unsat\", v; assert v[\"fadd-x-negx-zero-strict-unsound\"]==\"sat\", v; assert v[\"fadd-x-one-not-identity-even-with-flags\"]==\"sat\", v' ${CMAKE_CURRENT_BINARY_DIR}/fastmath/report.json; else echo 'prove_fastmath_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME differential_lift_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-differential-lift.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/difflift/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert len(d[\"leg1_empirical_disagreements\"])==0, d[\"leg1_empirical_disagreements\"]; assert len(d[\"negatives_not_refuted\"])==0, d[\"negatives_not_refuted\"]; assert len(d[\"leg2_opt_preservation_violations\"])==0, d[\"leg2_opt_preservation_violations\"]; assert d[\"intents\"]>=30, d' ${CMAKE_CURRENT_BINARY_DIR}/difflift/report.json && if command -v opt >/dev/null 2>&1 || test -x /opt/homebrew/opt/llvm@18/bin/opt; then ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"opt_available\"] is True, d; assert d[\"leg2_preserved\"]>=15, d; assert d[\"leg2_opt_matches_after\"]>=15, d' ${CMAKE_CURRENT_BINARY_DIR}/difflift/report.json; else echo 'differential_lift_fixture: opt absent, leg2 skipped'; fi"
    )
    add_test(
        NAME cross_solver_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && command -v bitwuzla >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-cross-solver.py --selftest --z3-bin z3 --second-bin bitwuzla --report ${CMAKE_CURRENT_BINARY_DIR}/crosssolver/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert len(d[\"disagreements\"])==0, d[\"disagreements\"]; assert d[\"agree\"]>=80, d; assert len(d[\"second_inconclusive\"])==0, d[\"second_inconclusive\"]' ${CMAKE_CURRENT_BINARY_DIR}/crosssolver/report.json; else echo 'cross_solver_fixture: z3/bitwuzla not both present, skipped'; fi"
    )
    add_test(
        NAME klee_fold_fixture
        COMMAND
            /bin/sh -c
            "if test -x /opt/homebrew/opt/llvm@18/bin/clang++ || command -v clang++ >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-klee-fold.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/kleefold/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert d[\"sound_variant\"][\"status\"]==\"sound\", d; assert d[\"buggy_variant\"][\"status\"]==\"miscompile\", d' ${CMAKE_CURRENT_BINARY_DIR}/kleefold/report.json; else echo 'klee_fold_fixture: clang not found, skipped'; fi"
    )
    add_test(
        NAME gen_fold_harness_fixture
        COMMAND
            /bin/sh -c
            "if test -x /opt/homebrew/opt/llvm@18/bin/clang++ || command -v clang++ >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-gen-fold-harness.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/genfold/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True, d; assert d[\"sound_control\"][\"status\"]==\"sound\", d; assert any(r[\"status\"]==\"miscompile\" for r in d[\"mined_runs\"]), d' ${CMAKE_CURRENT_BINARY_DIR}/genfold/report.json; else echo 'gen_fold_harness_fixture: clang not found, skipped'; fi"
    )
    add_test(
        NAME extract_pass_model_native_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && test -x ${O2T_AST_MINER}; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-extract-pass-model.py --mine ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --miner ${O2T_AST_MINER} --report ${CMAKE_CURRENT_BINARY_DIR}/extractnative/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); ms=d[\"models\"]; assert len(ms)>=3, ms; assert all(m.get(\"function\") for m in ms), ms; got={m[\"function\"]:m[\"opcode\"] for m in ms}; exp={\"foldAddZero\":\"bvadd\",\"foldMulOne\":\"bvmul\",\"foldXorSelf\":\"bvxor\"}; assert all(got.get(k)==v for k,v in exp.items()), (\"opcode-from-miner\",got); rs=d[\"model_reports\"]; assert all(r[\"miscompiles\"]==0 for r in rs), rs; assert any(r[\"counts\"].get(\"sound\",0)>=1 for r in rs), rs' ${CMAKE_CURRENT_BINARY_DIR}/extractnative/report.json; else echo 'extract_pass_model_native_fixture: miner/z3 not present, skipped'; fi"
    )
    add_test(
        NAME extract_pass_model_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-extract-pass-model.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/extract/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert len(d[\"models\"])==1, d; m=d[\"models\"][0]; assert m[\"function\"]==\"foldAddMulti\" and len(m[\"branches\"])==3, m; r=d[\"model_reports\"][0]; assert r[\"counts\"][\"sound\"]==1 and r[\"counts\"][\"unsound\"]==1 and r[\"counts\"][\"dead\"]==1 and r[\"miscompiles\"]==1, r' ${CMAKE_CURRENT_BINARY_DIR}/extract/report.json; else echo 'extract_pass_model_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME extract_pass_model_facts_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-extract-pass-model.py --findings ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fact_guarded_branches.jsonl --symexec --report ${CMAKE_CURRENT_BINARY_DIR}/extractfacts/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); got={m[\"function\"]:m[\"opcode\"] for m in d[\"models\"]}; assert got=={\"foldURemPow2\":\"bvurem\",\"foldSDivNonNeg\":\"bvsdiv\"}, got; rs={r[\"function\"]:r for r in d[\"model_reports\"]}; assert all(r[\"counts\"][\"sound\"]==1 and r[\"miscompiles\"]==0 for r in rs.values()), rs' ${CMAKE_CURRENT_BINARY_DIR}/extractfacts/report.json; else echo 'extract_pass_model_facts_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME symexec_pass_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-symexec-pass.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/symexec/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); c=d[\"counts\"]; assert c[\"sound\"]==2 and c[\"dead\"]==1 and c[\"unsound\"]==1, c; assert d[\"fallthrough_reachable\"] is True, d; bad=[p for p in d[\"paths\"] if p[\"status\"]==\"UNSOUND\"][0]; assert bad.get(\"counterexample\"), bad; assert any(p[\"status\"]==\"dead-branch\" for p in d[\"paths\"]), d' ${CMAKE_CURRENT_BINARY_DIR}/symexec/report.json; else echo 'symexec_pass_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME value_tracking_consolidation_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/value_tracking_consolidation_fixture.py
    )
    add_test(
        NAME closed_form_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/closed_form_fixture.py
    )
    add_test(
        NAME orchestrate_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/orchestrate_fixture.py
    )
    add_test(
        NAME memory_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/memory_model_fixture.py
    )
    add_test(
        NAME extract_memory_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/extract_memory_model_fixture.py
    )
    add_test(
        NAME slp_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_model_fixture.py
    )
    add_test(
        NAME extract_slp_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/extract_slp_model_fixture.py
    )
    add_test(
        NAME globalopt_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/globalopt_model_fixture.py
    )
    add_test(
        NAME extract_globalopt_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/extract_globalopt_model_fixture.py
    )
    add_test(
        NAME dce_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dce_model_fixture.py
    )
    add_test(
        NAME extract_dce_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/extract_dce_model_fixture.py
    )
    add_test(
        NAME loop_structural_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_structural_model_fixture.py
    )
    add_test(
        NAME extract_loop_structural_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/extract_loop_structural_model_fixture.py
    )
    add_test(
        NAME extract_cfg_model_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/extract_cfg_model_fixture.py
    )
    add_test(
        NAME proof_audit_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/proof_audit_fixture.py
    )
    add_test(
        NAME pass_graph_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_graph_fixture.py
    )
    add_test(
        NAME pass_graph_operand_loop_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_graph_operand_loop_fixture.py; else echo 'pass_graph_operand_loop_fixture: z3 not found, skipped'; fi"
    )
    # Pass IR phase fixtures (phases 15-33): each was committed with its phase but not gated. Register
    # them here so their soundness claims -- memory/theory-of-arrays, 2QBF refinement, second-solver
    # cross-check, structured-tree recovery, compiler-grounding, re-checkable certificates, cross-width
    # -- are enforced by CI. z3 required; each fixture self-skips its optional clang++/bitwuzla.
    foreach(_pg_fixture
        pass_graph_width_fixture
        pass_graph_ir_fixture
        pass_graph_miner_fixture
        pass_graph_certificate_fixture
        pass_graph_synthesis_fixture
        pass_graph_memory_fixture
        pass_graph_refinement_fixture
        pass_graph_solver_fixture
        pass_graph_structured_fixture
        pass_graph_grounding_fixture
        pass_graph_reduction_loop_fixture
        pass_graph_return_form_fixture
        pass_graph_compose_fixture
        pass_graph_contract_fixture
        pass_graph_predset_fixture
        pass_graph_inline_mask_fixture
        pass_graph_twoicmp_fixture)
        add_test(
            NAME ${_pg_fixture}
            COMMAND
                /bin/sh -c
                "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/${_pg_fixture}.py; else echo '${_pg_fixture}: z3 not found, skipped'; fi"
        )
    endforeach()
    # Verification agent: the LLM-driven batch-triage loop (deterministic scenario stub; the
    # z3-guarded fixture dispatches REAL verifiers, the synthesis fixture is pure quarantine).
    add_test(
        NAME agent_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/agent_fixture.py; else echo 'agent_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME agent_synthesis_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/agent_synthesis_fixture.py
    )
    add_test(
        NAME agent_selftest
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-agent.py --selftest
    )
    add_test(
        NAME passir_corpus_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/passir_corpus_fixture.py; else echo 'passir_corpus_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME clang_tree_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && { command -v clang >/dev/null 2>&1 || test -x /opt/homebrew/opt/llvm@18/bin/clang; }; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/clang_tree_fixture.py; else echo 'clang_tree_fixture: z3 or clang not found, skipped'; fi"
    )
    add_test(
        NAME clang_tree_source_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && { command -v clang >/dev/null 2>&1 || test -x /opt/homebrew/opt/llvm@18/bin/clang; }; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/clang_tree_source_fixture.py; else echo 'clang_tree_source_fixture: z3 or clang not found, skipped'; fi"
    )
    # Whole-.cpp mode: recover folds from the UNMODIFIED upstream InstCombineAndOrXor.cpp in its real
    # lib context. Skips (hermetically) unless an LLVM 18 InstCombine source dir is located via
    # O2T_INSTCOMBINE_DIR / O2T_LLVM_SRC -- the fixture's docstring gives the two-curl reproduction.
    add_test(
        NAME clang_tree_wholecpp_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/clang_tree_wholecpp_fixture.py
    )
    add_test(
        NAME frontend_robustness_fixture
        COMMAND
            /bin/sh -c
            "if command -v opt >/dev/null 2>&1 || test -x /opt/homebrew/opt/llvm@18/bin/opt; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/frontend_robustness_fixture.py; else echo 'frontend_robustness_fixture: opt not found, skipped'; fi"
    )
    add_test(
        NAME tv_matrix_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && { command -v opt >/dev/null 2>&1 || test -x /opt/homebrew/opt/llvm@18/bin/opt; }; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/tv_matrix_fixture.py; else echo 'tv_matrix_fixture: z3 or opt not found, skipped'; fi"
    )
    # Observational validation: a fold recovered FROM SOURCE is emitted as IR, the real
    # `opt -passes=instcombine` is run, and its actual output is checked against the recovered `after`
    # -- closing the source-intent <-> actual-behavior loop for peephole folds.
    add_test(
        NAME observe_fold_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && { command -v opt >/dev/null 2>&1 || test -x /opt/homebrew/opt/llvm@18/bin/opt; }; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/observe_fold_fixture.py; else echo 'observe_fold_fixture: z3 or opt not found, skipped'; fi"
    )
    # Whole-function TV over a real InstCombine test corpus: real IR -> real opt -> whole transform
    # proved sound (verifies the composition of whatever folds fired, not an isolated obligation).
    add_test(
        NAME corpus_tv_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && { command -v opt >/dev/null 2>&1 || test -x /opt/homebrew/opt/llvm@18/bin/opt; }; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/corpus_tv_fixture.py; else echo 'corpus_tv_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME mutation_catchrate_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/mutation_catchrate_fixture.py; else echo 'mutation_catchrate_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME prove_timing_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/prove_timing_fixture.py; else echo 'prove_timing_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME passir_ablation_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/passir_ablation_fixture.py; else echo 'passir_ablation_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME symexec_real_pass_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && command -v clang++ >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/symexec_real_pass_fixture.py; else echo 'symexec_real_pass_fixture: z3 or clang++ not found, skipped'; fi"
    )
    add_test(
        NAME modelcheck_real_pass_fake_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_real_pass_fake_fixture.py
    )
    add_test(
        NAME modelcheck_intents_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_intents_fixture.py
    )
    add_test(
        NAME modelcheck_cfg_source_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_cfg_source_fixture.py
    )
    add_test(
        NAME modelcheck_memory_source_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_memory_source_fixture.py
    )
    add_test(
        NAME modelcheck_licm_source_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_licm_source_fixture.py
    )
    add_test(
        NAME modelcheck_globalopt_source_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_globalopt_source_fixture.py
    )
    add_test(
        NAME modelcheck_dce_source_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_dce_source_fixture.py
    )
    add_test(
        NAME modelcheck_slp_source_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_slp_source_fixture.py
    )
    add_test(
        NAME pass_source_audit_modelcheck_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_source_audit_modelcheck_fixture.py --work-dir ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-modelcheck-fixture
    )
    add_test(
        NAME modelcheck_real_pass_fixture
        COMMAND
            /bin/sh -c
            "if command -v cbmc >/dev/null 2>&1 || command -v esbmc >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_real_pass_fixture.py; else echo 'modelcheck_real_pass_fixture: cbmc/esbmc not found, skipped'; fi"
    )
    add_test(
        NAME modelcheck_generated_real_fixture
        COMMAND
            /bin/sh -c
            "if command -v cbmc >/dev/null 2>&1 || command -v esbmc >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/modelcheck_generated_real_fixture.py; else echo 'modelcheck_generated_real_fixture: cbmc/esbmc not found, skipped'; fi"
    )
    add_test(
        NAME klee_symexec_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1 && command -v klee >/dev/null 2>&1 && [ -x /opt/homebrew/opt/llvm@16/bin/clang ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/klee_symexec_fixture.py; else echo 'klee_symexec_fixture: z3/klee/llvm@16 clang not found, skipped'; fi"
    )
    add_test(
        NAME parametric_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/parametric_fixture.py
    )
    add_test(
        NAME cross_check_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/cross_check_fixture.py
    )
    add_test(
        NAME dse_ir_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dse_ir_fixture.py; else echo 'dse_ir_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME instcombine_ir_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/instcombine_ir_fixture.py; else echo 'instcombine_ir_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME slp_ir_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_ir_fixture.py; else echo 'slp_ir_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME scalar_tv_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/scalar_tv_fixture.py; else echo 'scalar_tv_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME mem2reg_ir_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/mem2reg_ir_fixture.py; else echo 'mem2reg_ir_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME loop_cfg_ir_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_cfg_ir_fixture.py; else echo 'loop_cfg_ir_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME loop_induction_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_induction_fixture.py; else echo 'loop_induction_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME loop_simulation_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_simulation_fixture.py
    )
    add_test(
        NAME loop_multiexit_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_multiexit_fixture.py
    )
    add_test(
        NAME loop_nested_fixture
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_nested_fixture.py
    )
    add_test(
        NAME loop_rotate_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/loop_rotate_fixture.py; else echo 'loop_rotate_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME orchestrate_sweep_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/orchestrate_sweep_fixture.py; else echo 'orchestrate_sweep_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME cfg_shape_fixture
        COMMAND
            /bin/sh -c
            "OPT=$(command -v opt || true); if [ -z \"$OPT\" ] && [ -x /opt/homebrew/opt/llvm@18/bin/opt ]; then OPT=/opt/homebrew/opt/llvm@18/bin/opt; fi; if command -v z3 >/dev/null 2>&1 && [ -n \"$OPT\" ]; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/cfg_shape_fixture.py; else echo 'cfg_shape_fixture: z3 or opt not found, skipped'; fi"
    )
    add_test(
        NAME fact_bridge_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-fact-bridge.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/factbridge/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"ok\"] is True and d[\"cases\"]>=3, d; r={x[\"fact\"]:x for x in d[\"results\"]}; assert all(x[\"with_guard\"]==\"sound\" for x in d[\"results\"]), d; assert all(x[\"without_guard\"]==\"UNSOUND\" and x[\"counterexample\"] for x in d[\"results\"]), d; assert any(\"PowerOfTwo\" in f for f in r), r' ${CMAKE_CURRENT_BINARY_DIR}/factbridge/report.json; else echo 'fact_bridge_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME pass_model_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-lift-pass-model.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/passmodel/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); c=d[\"counts\"]; assert {\"unconditionally-sound\",\"guard-sufficient\",\"insufficient-guard\"} <= set(c), c; assert d[\"insufficient_guard_bugs\"]==1, d; bad=[b for b in d[\"branches\"] if b[\"status\"]==\"insufficient-guard\"][0]; assert bad.get(\"counterexample\"), bad' ${CMAKE_CURRENT_BINARY_DIR}/passmodel/report.json; else echo 'pass_model_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME verify_pass_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-pass.py --selftest --loop --promote ${CMAKE_CURRENT_BINARY_DIR}/verifypass/promoted.json --report ${CMAKE_CURRENT_BINARY_DIR}/verifypass/loop.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d[\"converged\"] is True, d; assert d[\"rounds\"][-1][\"new_promoted\"]==0, d' ${CMAKE_CURRENT_BINARY_DIR}/verifypass/loop.json; r=${CMAKE_CURRENT_BINARY_DIR}/verifypass/promoted.json; if [ -s \"$r\" ] && ${Python3_EXECUTABLE} -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))[\"rules\"] else 1)' \"$r\"; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-lift-rules.py --rules \"$r\" >/dev/null; fi; else echo 'verify_pass_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME triage_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-triage.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/triage/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s; v=s[\"verdicts\"]; assert {\"lifter-issue\",\"missing-precondition\",\"suspected-miscompile\",\"real-miscompile\"} <= set(v), v' ${CMAKE_CURRENT_BINARY_DIR}/triage/report.json; else echo 'triage_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME pass_dossier_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-pass-dossier.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/dossier/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; d=json.load(open(sys.argv[1])); t=d[\"totals\"]; assert t[\"mined_sites\"]==4, t; assert t[\"suspected_bugs\"]==1, t; p=d[\"passes\"][\"instcombine\"]; assert p[\"verified\"]+p[\"proved-only\"]==3, p; assert any(m[\"marker\"]==\"probe.instcombine.mul-one\" and m[\"status\"]==\"suspected-bug\" and m[\"faithful\"]==False for m in p[\"markers\"]), p' ${CMAKE_CURRENT_BINARY_DIR}/dossier/report.json; else echo 'pass_dossier_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME cross_validate_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-cross-validate.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/crossval/report.json; ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"bug_demo\"][\"verdict\"]==\"bug\", s[\"bug_demo\"]; t=s[\"transforms\"]; v=s[\"verdicts\"]; assert t==0 or v.get(\"verified\")==t, s' ${CMAKE_CURRENT_BINARY_DIR}/crossval/report.json; else echo 'cross_validate_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME autoverify_lift_finding_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-lift-finding.py --findings ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/autoverify_findings.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/autoverify/report.json; ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); c=s[\"counts\"]; assert c[\"proved\"]==3 and c[\"faithful\"]==3, c; assert c[\"refuted\"]==1 and c[\"unfaithful\"]==1, c; assert s[\"transforms\"]==4, s' ${CMAKE_CURRENT_BINARY_DIR}/autoverify/report.json; else echo 'autoverify_lift_finding_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME lift_matcher_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-lift-matcher.py --selftest --no-z3 --report ${CMAKE_CURRENT_BINARY_DIR}/lift-matcher/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s; assert s[\"lifted\"]==s[\"cases\"] and s[\"cases\"]>=12, s; assert any(\"Mul\" in r[\"name\"] for r in s[\"results\"]), s; assert sum(1 for r in s[\"results\"] if r.get(\"kind\")==\"transform\")>=6, s; assert any(\"select\" in r[\"name\"].lower() or \"if-conversion\" in r[\"name\"].lower() for r in s[\"results\"]), (\"non-local select pattern missing\", s)' ${CMAKE_CURRENT_BINARY_DIR}/lift-matcher/report.json"
    )
    add_test(
        NAME lift_rules_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-lift-rules.py --no-z3 --rules ${CMAKE_CURRENT_SOURCE_DIR}/constraints/lift_rules.json --report ${CMAKE_CURRENT_BINARY_DIR}/lift-rules/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s; assert s[\"instantiated\"]==s[\"rules\"] and s[\"rules\"]>=10, s; names={r[\"name\"] for r in s[\"results\"]}; assert {\"and-self\",\"or-self\",\"sub-self\",\"shl-zero\",\"ashr-zero\"} <= names, names' ${CMAKE_CURRENT_BINARY_DIR}/lift-rules/report.json"
    )
    add_test(
        NAME lift_rules_production_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-lift-rules.py --production
    )
    add_test(
        NAME guard_inference_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-guard.py --selftest --report ${CMAKE_CURRENT_BINARY_DIR}/guard-infer/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s; assert s[\"cases\"]>=4, s; st={r[\"name\"]: r[\"got\"] for r in s[\"results\"]}; assert any(v==\"guard-found\" for v in st.values()) and any(v==\"no-precondition\" for v in st.values()), st' ${CMAKE_CURRENT_BINARY_DIR}/guard-infer/report.json; else echo 'guard_inference_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME guard_lift_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-guard-lift-selftest.py --no-z3 --report ${CMAKE_CURRENT_BINARY_DIR}/guard-lift/report.json && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"failed\"]==0, s; assert s[\"structural_ok\"]==s[\"cases\"] and s[\"cases\"]>=2, s' ${CMAKE_CURRENT_BINARY_DIR}/guard-lift/report.json"
    )
    add_test(
        NAME idiom_header_drift_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-generate-idiom-header.py
            --idioms ${CMAKE_CURRENT_SOURCE_DIR}/constraints/llvm_idioms.json
            --check ${CMAKE_CURRENT_SOURCE_DIR}/include/o2t/GeneratedLlvmIdioms.h
    )
    add_test(
        NAME vector_intent_header_drift_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-generate-vector-intent-header.py
            --facts ${CMAKE_CURRENT_SOURCE_DIR}/constraints/semantic_facts.json
            --check ${CMAKE_CURRENT_SOURCE_DIR}/include/o2t/GeneratedVectorIntentParts.h
    )
    add_test(
        NAME verify_candidates_fixture
        COMMAND
            /bin/sh -c
            "if command -v z3 >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-candidates.py --candidates ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/born_proven_candidates.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/born-proven/out.jsonl; ${Python3_EXECUTABLE} -c 'import json,sys; rows=[json.loads(l) for l in open(sys.argv[1])]; st={r[\"marker\"]: r[\"formal_verification\"][\"formal_status\"] for r in rows}; assert st[\"probe.test.sound\"]==\"proved\", st; assert st[\"probe.test.unsound\"]==\"failed\", st; sound=next(r for r in rows if r[\"marker\"]==\"probe.test.sound\"); assert sound[\"formal_verification\"][\"formal_widths\"]==dict.fromkeys([\"8\",\"16\",\"32\",\"64\"],\"proved\"), sound[\"formal_verification\"]' ${CMAKE_CURRENT_BINARY_DIR}/born-proven/out.jsonl; else echo 'verify_candidates_fixture: z3 not found, skipped'; fi"
    )
    add_test(
        NAME inference_golden_fixture
        COMMAND
            /bin/sh -c
            "cd ${CMAKE_CURRENT_SOURCE_DIR} && if test -x ${O2T_AST_MINER}; then ${Python3_EXECUTABLE} tools/cv-infer-golden.py --check tests/golden/inference-digest-fast.jsonl --miner ${O2T_AST_MINER} --snippets dse_analysis_facts_snippet.cpp,global_dead_initializer_nonnull_replacement_snippet.cpp,global_dead_initializer_snippet.cpp,intent_inference_snippet.cpp,llvm_pass_snippet.cpp,slp_fp_reduction_permutation_reassoc_transaction_snippet.cpp,slp_reduction_complex_region_snippet.cpp,vector_pass_snippet.cpp; else echo 'inference_golden_fixture: miner not built in this build dir, skipped'; fi"
    )
    add_test(
        NAME grammar_main_ubfree_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-grammar-gen.py --seed 0 --main --cfg --out ${CMAKE_CURRENT_BINARY_DIR}/diff/mm.ll && ${Python3_EXECUTABLE} -c 'import sys; t=open(sys.argv[1]).read(); assert \"define i32 @main()\" in t and \"call \" in t and \"@test(\" in t and \"ret i32 %m.fin\" in t; assert \" nsw\" not in t and \" nuw\" not in t and \" exact\" not in t; assert \"alloca\" not in t and \" = load \" not in t; assert \"declare i32 @putchar(i32)\" in t and t.count(\"call i32 @putchar\")==4, \"wide 32-bit stdout observable missing\"; assert \"mul i32 %m.acc, 1000003\" in t, \"order-sensitive rolling hash missing\"' ${CMAKE_CURRENT_BINARY_DIR}/diff/mm.ll"
    )
    add_test(
        NAME differential_check_one_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-grammar-gen.py --seed 0 --main --out ${CMAKE_CURRENT_BINARY_DIR}/diff/m1.ll && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-differential.py --check-one ${CMAKE_CURRENT_BINARY_DIR}/diff/m1.ll --opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --lli ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-lli.sh --passes-list 'default<O2>' --extra-opt buggy=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-miscompile-exit.sh); test $? -eq 0 && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-differential.py --check-one ${CMAKE_CURRENT_BINARY_DIR}/diff/m1.ll --opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --lli ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-lli.sh --passes-list 'default<O2>'); test $? -eq 1"
    )
    add_test(
        NAME grammar_self_minimize_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-grammar-gen.py --seed 0 --count 2 --instructions 12 --validate --minimize --out-dir ${CMAKE_CURRENT_BINARY_DIR}/grammar-min --opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-invalid.sh --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as-strict.sh --llvm-reduce ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-reduce.sh --report ${CMAKE_CURRENT_BINARY_DIR}/grammar-min/report.json; ${Python3_EXECUTABLE} -c 'import json,os,sys; s=json.load(open(sys.argv[1])); assert len(s[\"opt_findings\"])==2; assert s[\"minimized\"]==2; assert all(r[\"minimize_rc\"]==0 and os.path.exists(r[\"minimized_path\"]) and \"_text\" not in r for r in s[\"findings\"])' ${CMAKE_CURRENT_BINARY_DIR}/grammar-min/report.json"
    )
    add_test(
        NAME reduce_ir_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-grammar-gen.py --seed 0 --instructions 20 --out ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/w.ll && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-reduce-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/w.ll --out ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/reduced.ll --oracle 'grep -q @test {ll}' --llvm-reduce ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-reduce.sh --report ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/report.json && ${Python3_EXECUTABLE} -c 'import json,os,sys; r=json.load(open(sys.argv[1])); assert r[\"still_interesting\"] is True; assert r[\"lines_after\"]>0; assert os.path.exists(sys.argv[2])' ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/report.json ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/reduced.ll && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-reduce-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/w.ll --out ${CMAKE_CURRENT_BINARY_DIR}/reduce-ir/x.ll --oracle 'grep -q cv_absent_marker {ll}' --llvm-reduce ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-reduce.sh); test $? -eq 1"
    )
    add_test(
        NAME klee_feedback_generator_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-generate-klee-feedback.py --markers probe.instcombine.add-zero,probe.simplifycfg.diamond --out ${CMAKE_CURRENT_BINARY_DIR}/klee-fb/GeneratedKleeFeedback.h --update-state ${CMAKE_CURRENT_BINARY_DIR}/klee-fb/state.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-generate-klee-feedback.py --state ${CMAKE_CURRENT_BINARY_DIR}/klee-fb/state.json --markers probe.dse.dead-store --update-state ${CMAKE_CURRENT_BINARY_DIR}/klee-fb/state2.json && ${Python3_EXECUTABLE} -c 'import json,sys; h=open(sys.argv[1]).read(); assert \"probe.instcombine.add-zero\" in h and \"probe.simplifycfg.diamond\" in h; assert \"kCoveredMarkerCount = 2\" in h; assert \"isNovelCoverage\" in h; s=json.load(open(sys.argv[2])); assert sorted(s[\"covered\"])==[\"probe.instcombine.add-zero\",\"probe.simplifycfg.diamond\"]; s2=json.load(open(sys.argv[3])); assert len(s2[\"covered\"])==3 and \"probe.dse.dead-store\" in s2[\"covered\"]' ${CMAKE_CURRENT_BINARY_DIR}/klee-fb/GeneratedKleeFeedback.h ${CMAKE_CURRENT_BINARY_DIR}/klee-fb/state.json ${CMAKE_CURRENT_BINARY_DIR}/klee-fb/state2.json"
    )
    add_test(
        NAME grammar_intrinsics_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/grammar_intrinsics_fixture.py
    )
    add_test(
        NAME grammar_fp_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/grammar_fp_fixture.py
    )
    add_test(
        NAME grammar_vectors_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/grammar_vectors_fixture.py
    )
    add_test(
        NAME grammar_scalable_vectors_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/grammar_scalable_vectors_fixture.py
    )
    add_test(
        NAME grammar_gen_structure_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-grammar-gen.py --seed 0 --count 24 --instructions 30 --out-dir ${CMAKE_CURRENT_BINARY_DIR}/grammar-gen && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-grammar-gen.py --seed 0 --instructions 30 --out ${CMAKE_CURRENT_BINARY_DIR}/grammar-gen/repeat.ll && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-grammar-gen.py --seed 5 --cfg --out ${CMAKE_CURRENT_BINARY_DIR}/grammar-gen/cfg.ll && ${Python3_EXECUTABLE} -c 'import glob,sys; d=sys.argv[1]; fs=sorted(glob.glob(d+\"/grammar*.ll\")); assert len(fs)==24, len(fs); assert all(\"define \" in open(f).read() and \"@test(\" in open(f).read() and open(f).read().rstrip().endswith(\"}\") and \"  ret \" in open(f).read() for f in fs); assert open(d+\"/grammar00000.ll\").read()!=open(d+\"/grammar00001.ll\").read(), \"seeds should differ\"; assert open(d+\"/grammar00000.ll\").read()==open(d+\"/repeat.ll\").read(), \"same seed must be deterministic\"; cfg=open(d+\"/cfg.ll\").read(); assert \"br \" in cfg and \"phi \" in cfg and cfg.count(\":\\n\")>=2, \"cfg should have branches/phis/blocks\"' ${CMAKE_CURRENT_BINARY_DIR}/grammar-gen"
    )
    add_test(
        NAME fuzz_campaign_coverage_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-fuzz-campaign.py --out-dir ${CMAKE_CURRENT_BINARY_DIR}/fuzz-cov --replay $<TARGET_FILE:cv-replay> --opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh --iterations 20 --corpus-seeds 4 --seed 1 && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"generator_invalid\"]==0; assert s[\"corpus_size\"]>=2; assert s[\"coverage_size\"]>0; assert s[\"findings\"]==0' ${CMAKE_CURRENT_BINARY_DIR}/fuzz-cov/summary.json"
    )
    add_test(
        NAME fuzz_campaign_finding_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-fuzz-campaign.py --out-dir ${CMAKE_CURRENT_BINARY_DIR}/fuzz-find --replay $<TARGET_FILE:cv-replay> --opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-invalid.sh --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as-strict.sh --iterations 8 --corpus-seeds 2 --seed 3 && ${Python3_EXECUTABLE} -c 'import json,sys; s=json.load(open(sys.argv[1])); assert s[\"findings\"]>0; assert s[\"generator_invalid\"]==0; assert \"failed to verify\" in s[\"finding_records\"][0][\"reason\"]' ${CMAKE_CURRENT_BINARY_DIR}/fuzz-find/summary.json"
    )
    add_test(
        NAME fuzz_delta_coverage_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fuzz_delta_coverage_fixture.py
    )
    add_test(
        NAME pass_source_miner_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp
            --require-marker probe.instcombine.add-zero
            --require-marker probe.instcombine.mul-one
            --require-marker probe.instcombine.xor-self
            --require-marker probe.dce.dead-instruction
            --require-marker probe.simplifycfg.unreachable-block
            --require-marker probe.simplifycfg.diamond
            --require-marker probe.simplifycfg.branch-chain
            --require-marker probe.mem2reg.promotable-alloca
            --require-marker probe.mem2reg.store-load-forward
            --require-marker probe.dse.dead-store
            --require-marker probe.dse.overwritten-store
            --require-marker probe.instcombine.redundant-load
            --require-marker probe.cleanup.unused-alloca
            --require-marker probe.loop.canonical-header
            --require-marker probe.loop.induction-phi
            --require-marker probe.loop.simple-trip-count
            --require-marker probe.licm.invariant-op
            --require-marker probe.dce.dead-loop-instruction
            --require-marker probe.simplifycfg.loop-exit
    )
    add_test(
        NAME source_miner_ambiguity_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_miner_ambiguity_fixture.py
            --repo ${CMAKE_CURRENT_SOURCE_DIR}
    )
    add_test(
        NAME constraints_to_configs_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp > ${CMAKE_CURRENT_BINARY_DIR}/mined-pass-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-constraints-to-configs.py --input ${CMAKE_CURRENT_BINARY_DIR}/mined-pass-findings.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/mined-configs --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config>"
    )
    add_test(
        NAME intent_to_ir_mixed_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed && printf '%s\\n' '{\"marker\":\"probe.instcombine.add-zero\",\"file\":\"InstCombine.cpp\",\"line\":10}' '{\"marker\":\"probe.vector.shuffle-identity\",\"file\":\"VectorCombine.cpp\",\"line\":22}' '{\"marker\":\"probe.instcombine.add-zero\",\"file\":\"InstCombine.cpp\",\"line\":11}' '{\"marker\":\"probe.globalopt.dead-initializer\",\"file\":\"GlobalOpt.cpp\",\"line\":33}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-constraints-to-configs.py --input ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/input.jsonl --out-dir ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> --summary-json ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/unsupported.jsonl > ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/stdout.txt 2> ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/stderr.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/instcombine_add_zero.cfg && test -f ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/instcombine_add_zero.ll && test -f ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/vector_shuffle_identity.cfg && test -f ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/vector_shuffle_identity.ll && test -f ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/globalopt_dead_initializer.cfg && test -f ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/globalopt_dead_initializer.ll && grep -q '^global_shape=1$' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/globalopt_dead_initializer.cfg && grep -q '@cv_dead_init = internal global i32 42' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/globalopt_dead_initializer.ll && test ! -s ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/unsupported.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; summary=json.load(open(sys.argv[1])); assert summary[\"generated\"] == 3; assert summary[\"skipped\"] == 0; assert summary[\"duplicates\"] == 1; assert summary[\"unsupported_markers\"] == []; records=[json.loads(line) for line in open(sys.argv[2]) if line.strip()]; assert len(records) == 3; by_marker={r[\"marker\"]: r for r in records}; assert by_marker[\"probe.instcombine.add-zero\"][\"generation_source\"] == \"marker\"; assert by_marker[\"probe.vector.shuffle-identity\"][\"coverage\"] == [\"probe.vector.shuffle-identity\"]; assert by_marker[\"probe.globalopt.dead-initializer\"][\"coverage\"] == [\"probe.globalopt.dead-initializer\"]; assert by_marker[\"probe.globalopt.dead-initializer\"][\"source\"] == \"GlobalOpt.cpp\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/summary.json ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-mixed/out/manifest.jsonl"
    )
    add_test(
        NAME intent_to_ir_strict_unsupported_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict && printf '%s\\n' '{\"marker\":\"probe.unsupported.example\",\"file\":\"Example.cpp\",\"line\":33}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/input.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-constraints-to-configs.py --input ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/input.jsonl --out-dir ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/out --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> --summary-json ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/unsupported.jsonl --strict > ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/stdout.txt 2> ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/stderr.txt; test $? -eq 1) && grep -q 'unsupported constraints for probe.unsupported.example' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/stderr.txt && grep -q 'probe.unsupported.example' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/unsupported.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; summary=json.load(open(sys.argv[1])); assert summary[\"generated\"] == 0; assert summary[\"skipped\"] == 1; assert summary[\"duplicates\"] == 0; assert summary[\"unsupported_markers\"] == [\"probe.unsupported.example\"]' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-strict/summary.json"
    )
    add_test(
        NAME intent_to_ir_vector_minmax_abs_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax && printf '%s\\n' '{\"marker\":\"probe.vector.smin\",\"file\":\"VectorCombine.cpp\",\"line\":40}' '{\"marker\":\"probe.vector.smax\",\"file\":\"VectorCombine.cpp\",\"line\":41}' '{\"marker\":\"probe.vector.umin\",\"file\":\"VectorCombine.cpp\",\"line\":42}' '{\"marker\":\"probe.vector.umax\",\"file\":\"VectorCombine.cpp\",\"line\":43}' '{\"marker\":\"probe.vector.abs\",\"file\":\"VectorCombine.cpp\",\"line\":44}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-constraints-to-configs.py --input ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/input.jsonl --out-dir ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> --summary-json ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/unsupported.jsonl > ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/stdout.txt 2> ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/stderr.txt && test ! -s ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/unsupported.jsonl && grep -q '^vector_shape=20$' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_smin.cfg && grep -q '^vector_shape=21$' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_smax.cfg && grep -q '^vector_shape=22$' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_umin.cfg && grep -q '^vector_shape=23$' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_umax.cfg && grep -q '^vector_shape=24$' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_abs.cfg && grep -q 'icmp slt <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_smin.ll && grep -q 'icmp sgt <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_smax.ll && grep -q 'icmp ult <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_umin.ll && grep -q 'icmp ugt <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_umax.ll && grep -q 'sub <4 x i32> zeroinitializer, %vec' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/vector_abs.ll && ${Python3_EXECUTABLE} -c 'import json,sys; summary=json.load(open(sys.argv[1])); assert summary[\"generated\"] == 5; assert summary[\"skipped\"] == 0; assert summary[\"duplicates\"] == 0; assert summary[\"unsupported_markers\"] == []; records=[json.loads(line) for line in open(sys.argv[2]) if line.strip()]; assert len(records) == 5; assert {r[\"marker\"] for r in records} == {\"probe.vector.smin\",\"probe.vector.smax\",\"probe.vector.umin\",\"probe.vector.umax\",\"probe.vector.abs\"}; assert all(r[\"coverage\"] == [r[\"marker\"]] for r in records)' ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/summary.json ${CMAKE_CURRENT_BINARY_DIR}/intent-to-ir-vector-minmax/out/manifest.jsonl"
    )
    add_test(
        NAME globalopt_replay_witness_shapes_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes && printf '%s\\n' 'global_shape=2' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/ptr.cfg && printf '%s\\n' 'global_shape=3' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/array.cfg && $<TARGET_FILE:cv-replay> --config ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/ptr.cfg --out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/ptr.ll --write-config ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/ptr.normalized.cfg && $<TARGET_FILE:cv-replay> --config ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/array.cfg --out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/array.ll --write-config ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/array.normalized.cfg && grep -q '^global_shape=2$' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/ptr.normalized.cfg && grep -q '^global_shape=3$' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/array.normalized.cfg && grep -q '@cv_dead_init = internal global ptr @cv_target' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/ptr.ll && grep -q '@cv_dead_init = internal global \\[2 x i32\\] \\[i32 1, i32 2\\]' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/array.ll && grep -q 'marker=probe.globalopt.dead-initializer' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/ptr.ll && grep -q 'marker=probe.globalopt.dead-initializer' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-replay-shapes/array.ll"
    )
    add_test(
        NAME globalopt_config_witnesses_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/cases && printf '%s\\n' 'global_shape=1' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/cases/i32.cfg && printf '%s\\n' 'global_shape=2' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/cases/ptr.cfg && printf '%s\\n' 'global_shape=3' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/cases/array.cfg && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-globalopt-config-witnesses.py --cases-dir ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/cases --out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out --replay $<TARGET_FILE:cv-replay> > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/stdout.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/i32.i32.before.ll && test -f ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/i32.i32.after.ll && test -f ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/ptr.ptr.before.ll && test -f ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/array.array.after.ll && grep -q '@cv_dead_init = internal global i32 42' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/i32.i32.before.ll && grep -q '@cv_dead_init = internal global i32 0' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/i32.i32.after.ll && grep -q '@cv_dead_init = internal global ptr null' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/ptr.ptr.after.ll && grep -q '@cv_dead_init = internal global \\[2 x i32\\] zeroinitializer' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/witnesses/array.array.after.ll && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); records=data[\"witnesses\"][\"records\"]; assert len(records) == 1; w=records[0]; assert w[\"status\"] == \"passed\"; assert w[\"missing_required_cases\"] == []; assert w[\"witness_contract\"][\"status\"] == \"passed\"; assert w[\"witness_contract\"][\"structural_status\"] == \"passed\"; cases={c[\"name\"]:c for c in w[\"cases\"]}; assert set(cases) == {\"i32\",\"ptr\",\"array\"}; assert all(c[\"structural_details\"][\"changed_line_count\"] == 1 for c in cases.values())' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses/out/globalopt-witnesses.json"
    )
    add_test(
        NAME globalopt_config_witnesses_strict_empty_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-empty/cases && printf '%s\\n' 'global_shape=0' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-empty/cases/scalar.cfg && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-globalopt-config-witnesses.py --cases-dir ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-empty/cases --out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-empty/out --replay $<TARGET_FILE:cv-replay> --strict > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-empty/stdout.txt 2> ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-empty/stderr.txt; test $? -eq 1) && grep -q 'no GlobalOpt witness configs found' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-empty/stderr.txt"
    )
    if(Z3_EXECUTABLE)
        add_test(
            NAME globalopt_config_witnesses_contract_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/cases && printf '%s\\n' 'global_shape=1' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/cases/i32.cfg && printf '%s\\n' 'global_shape=2' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/cases/ptr.cfg && printf '%s\\n' 'global_shape=3' > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/cases/array.cfg && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-globalopt-config-witnesses.py --cases-dir ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/cases --out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/out --replay $<TARGET_FILE:cv-replay> --verify-contracts --contract-out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/contract --z3 ${Z3_EXECUTABLE} --require-clean > ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/stdout.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); summary=data[\"summary\"]; assert summary[\"status\"][\"passed\"] == 1; assert summary[\"structural_status\"][\"passed\"] == 1; assert summary[\"formal_status\"][\"proved\"] == 3; assert summary[\"failed_checks\"] == {}' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/contract/globalopt-witness-contract-verification.json && grep -q '^formal_status: proved=3$' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-config-witnesses-contract/contract/globalopt-witness-contract-verification.txt"
        )
    endif()
    add_test(
        NAME slp_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); r=[x for x in data if x.get(\"optimization_transaction\")][0]; tx=r[\"optimization_transaction\"]; assert r[\"predicate_kind\"] == \"transaction\"; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"opcode\"] == \"add\"; assert [a[\"kind\"] for a in tx[\"actions\"]] == [\"pack-scalars\",\"emit-vector-binop\",\"replace-scalar-uses\"]; assert tx[\"functions\"] == [\"vectorizeTree\"]; assert len(tx[\"role_provenance\"]) == 5' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-findings.json"
    )
    add_test(
        NAME slp_transaction_8lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_8lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-8lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 8; assert tx[\"lane_mapping\"][\"map\"] == list(range(8)); assert tx[\"result_lane_mapping\"][\"map\"] == list(range(8)); assert len(tx[\"scalar_lane_pairs\"]) == 8' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-8lane-findings.json"
    )
    add_test(
        NAME slp_transaction_16lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_16lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-16lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 16; assert tx[\"lane_mapping\"][\"map\"] == list(range(16)); assert tx[\"result_lane_mapping\"][\"map\"] == list(range(16)); assert len(tx[\"scalar_lane_pairs\"]) == 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-16lane-findings.json"
    )
    add_test(
        NAME slp_transaction_32lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_32lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-32lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 32; assert tx[\"lane_mapping\"][\"map\"] == list(range(32)); assert tx[\"result_lane_mapping\"][\"map\"] == list(range(32)); assert len(tx[\"scalar_lane_pairs\"]) == 32' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-32lane-findings.json"
    )
    add_test(
        NAME slp_transaction_64lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_64lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-64lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 64; assert tx[\"lane_mapping\"][\"map\"] == list(range(64)); assert tx[\"result_lane_mapping\"][\"map\"] == list(range(64)); assert len(tx[\"scalar_lane_pairs\"]) == 64' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-64lane-findings.json"
    )
    add_test(
        NAME slp_transaction_128lane_consistency_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_128lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-128lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"failed\"; assert tx[\"lanes\"] == 128; assert \"unsupported-lane-count:128\" in tx[\"consistency_errors\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-128lane-findings.json"
    )
    add_test(
        NAME slp_minmax_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; records=json.load(open(sys.argv[1])); r=[x for x in records if x.get(\"marker\") == \"probe.slp.vectorize-binop\"][0]; tx=r[\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-minmax\"; assert tx[\"opcode\"] == \"smin\"; assert tx[\"predicate\"] == \"slt\"; assert [a[\"kind\"] for a in tx[\"actions\"]] == [\"pack-scalars\",\"emit-vector-minmax\",\"replace-scalar-uses\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"compare_sources\"] and tx[\"select_sources\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-findings.json"
    )
    add_test(
        NAME slp_minmax_transaction_16lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_16lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-16lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[r for r in json.load(open(sys.argv[1])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-minmax\"; assert tx[\"opcode\"] == \"smin\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 16; assert tx[\"result_lane_mapping\"][\"map\"] == list(range(16)); assert len(tx[\"scalar_lane_pairs\"]) == 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-16lane-findings.json"
    )
    add_test(
        NAME slp_scalable_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[r for r in json.load(open(sys.argv[1])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"base_lanes\"] == 4; assert tx[\"vscale_values\"] == [1,2,4]; assert tx[\"opcode\"] == \"add\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_transaction_base_lanes_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_base2_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base2-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_base8_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base8-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; b2=[r for r in json.load(open(sys.argv[1])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; b8=[r for r in json.load(open(sys.argv[2])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert b2[\"consistency\"] == b8[\"consistency\"] == \"ok\"; assert b2[\"scalable\"] is True and b8[\"scalable\"] is True; assert b2[\"base_lanes\"] == 2; assert b8[\"base_lanes\"] == 8' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base2-findings.json ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base8-findings.json"
    )
    add_test(
        NAME slp_scalable_transaction_reorder_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_reorder_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-reorder-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[r for r in json.load(open(sys.argv[1])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"opcode\"] == \"xor\"; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert tx[\"result_lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-reorder-findings.json"
    )
    add_test(
        NAME slp_scalable_minmax_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_minmax_transaction_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[r for r in json.load(open(sys.argv[1])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-minmax\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"base_lanes\"] == 4; assert tx[\"consistency_errors\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-findings.json"
    )
    add_test(
        NAME slp_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; records=json.load(open(sys.argv[1])); r=[x for x in records if x.get(\"optimization_transaction\") and x.get(\"marker\") == \"probe.slp.vectorize-reduction\"][0]; tx=r[\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"add\"; assert tx[\"reduction_opcode\"] == \"add\"; assert [a[\"kind\"] for a in tx[\"actions\"]] == [\"pack-scalars\",\"emit-vector-reduction\",\"replace-scalar-uses\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"reduction_sources\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_transaction_to_ir_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/reorder.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/minmax.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/reduction.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_128lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/unsupported.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/reorder.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/minmax.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/reduction.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/unsupported.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/out --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/unsupported.jsonl > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/stdout.txt 2> ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/stderr.txt && grep -R -q 'shufflevector <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/out && grep -R -q 'icmp slt <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/out && grep -R -q '@llvm.vector.reduce.add.v4i32' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/out && grep -q 'unsupported-lane-count:128' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/unsupported.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; out=pathlib.Path(sys.argv[1]); summary=json.load(open(sys.argv[2])); records=[json.loads(line) for line in open(out/\"manifest.jsonl\") if line.strip()]; assert summary[\"generated\"] == 3; assert summary[\"skipped\"] == 1; assert len(records) == 3; assert {r[\"transaction_kind\"] for r in records} == {\"slp-vectorize-binop\",\"slp-vectorize-minmax\",\"slp-vectorize-reduction\"}; assert any(r[\"opcode\"] == \"add\" and r[\"marker\"] == \"probe.slp.vectorize-binop\" for r in records); assert any(r[\"opcode\"] == \"smin\" for r in records); assert any(r[\"opcode\"] == \"add\" and r[\"marker\"] == \"probe.slp.vectorize-reduction\" for r in records); assert all((out/r[\"ir\"]).exists() for r in records)' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir/summary.json"
    )
    add_test(
        NAME slp_transaction_to_ir_validation_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/reorder.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/reorder.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/skipped --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/skipped-summary.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/reorder.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/passed --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/passed-summary.json --validate-ir --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/reorder.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/failed --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation/failed-summary.json --validate-ir --llvm-as /bin/false && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); checks=[(\"skipped\",\"skipped\"),(\"passed\",\"passed\"),(\"failed\",\"failed\")]; assert all(([json.loads(line) for line in open(root/directory/\"manifest.jsonl\") if line.strip()][0][\"ir_validation_status\"] == status and json.load(open(root/f\"{directory}-summary.json\"))[\"ir_validation\"][status] == 1) for directory,status in checks); assert json.load(open(root/\"skipped-summary.json\"))[\"ir_validation\"][\"passed\"] == 0' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-to-ir-validation"
    )
    add_test(
        NAME slp_reduction_transaction_2lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_2lane_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-2lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 2; assert tx[\"reduction_lanes\"] == 2; assert tx[\"lane_mapping\"][\"map\"] == [0,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-2lane-findings.json"
    )
    add_test(
        NAME slp_reduction_transaction_8lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_8lane_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-8lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 8; assert tx[\"reduction_lanes\"] == 8; assert tx[\"lane_mapping\"][\"map\"] == [0,1,2,3,4,5,6,7]' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-8lane-findings.json"
    )
    add_test(
        NAME slp_reduction_transaction_16lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_16lane_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-16lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 16; assert tx[\"reduction_lanes\"] == 16; assert tx[\"lane_mapping\"][\"map\"] == list(range(16))' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-16lane-findings.json"
    )
    add_test(
        NAME slp_reduction_transaction_32lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_32lane_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-32lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 32; assert tx[\"reduction_lanes\"] == 32; assert tx[\"lane_mapping\"][\"map\"] == list(range(32))' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-32lane-findings.json"
    )
    add_test(
        NAME slp_reduction_transaction_64lane_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_64lane_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-64lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 64; assert tx[\"reduction_lanes\"] == 64; assert tx[\"lane_mapping\"][\"map\"] == list(range(64))' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-64lane-findings.json"
    )
    add_test(
        NAME slp_scalable_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"base_lanes\"] == 4; assert tx[\"vscale_values\"] == [1,2,4]; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_reduction_base2_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_base2_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base2-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"base_lanes\"] == 2; assert tx[\"vscale_values\"] == [1,2,4]; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base2-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_reduction_base8_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_base8_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base8-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"base_lanes\"] == 8; assert tx[\"vscale_values\"] == [1,2,4]; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base8-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_reduction_permutation_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_permutation_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"opcode\"] == \"xor\"; assert tx[\"scalable\"] is True; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_reduction_unsupported_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_ambiguous_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-ambiguous-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; wd=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert wd[\"consistency\"] == \"failed\" and \"unsupported-scalable-widening-reduction\" in wd[\"unsupported_reduction_reasons\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-ambiguous-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_fp_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"opcode\"] == \"fadd\"; assert tx[\"scalable\"] is True; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_widening_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 32; assert tx[\"reduction_extend_kind\"] == \"zext\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_scalable_widening_alias_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_alias_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; kinds={p[\"kind\"] for p in tx[\"reduction_width_provenance\"]}; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"reduction_width_status\"] == \"complete\"; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 32; assert \"type-alias-width\" in kinds and \"extension-target-width\" in kinds; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_minmax_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; records=json.load(open(sys.argv[1])); r=[x for x in records if x.get(\"optimization_transaction\") and x.get(\"marker\") == \"probe.slp.vectorize-reduction\"][0]; tx=r[\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"smin\"; assert tx[\"reduction_opcode\"] == \"smin\"; assert tx[\"actions\"][1][\"kind\"] == \"emit-vector-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"reduction_sources\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_minmax_reduction_16lane_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_reduction_16lane_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-16lane-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; records=json.load(open(sys.argv[1])); tx=[x for x in records if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"smin\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 16; assert tx[\"reduction_lanes\"] == 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-16lane-transaction-findings.json"
    )
    add_test(
        NAME slp_reduction_transaction_consistency_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_invalid_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-invalid-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; records=json.load(open(sys.argv[1])); tx=[x for x in records if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; errors=set(tx[\"consistency_errors\"]); assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"failed\"; assert \"opcode-mismatch:legality:mul!=vector-emission:add\" in errors; assert \"unsupported-lane-count:32\" not in errors; assert \"incomplete-pack-builder\" in errors; assert tx[\"reduction_opcode\"] == \"add\"; assert tx[\"reduction_lanes\"] == 32; assert tx[\"reduction_sources\"] and tx[\"reduction_result\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-invalid-findings.json"
    )
    add_test(
        NAME slp_fp_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"fadd\"; assert tx[\"reduction_opcode\"] == \"fadd\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_fp_reduction_policy_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_unordered_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-unordered-policy-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_fast_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-fast-policy-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; fixed=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; scalable=[x for x in json.load(open(sys.argv[2])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert fixed[\"consistency\"] == scalable[\"consistency\"] == \"ok\"; assert fixed[\"fp_policy\"][\"semantics\"] == \"unordered-fp-reduction\"; assert scalable[\"fp_policy\"][\"semantics\"] == \"fast-math-fp-reduction\"; assert scalable[\"scalable\"] is True; assert fixed[\"unsupported_reduction_reasons\"] == scalable[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-unordered-policy-findings.json ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-fast-policy-findings.json"
    )
    add_test(
        NAME slp_widening_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"add\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 32; assert tx[\"reduction_extend_kind\"] == \"zext\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_widening_trunc_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_trunc_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-trunc-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"add\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 16; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-trunc-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_widening_ambiguous_reduction_transaction_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_ambiguous_reduction_transaction_snippet.cpp --require-marker probe.slp.vectorize-reduction > ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-ambiguous-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[x for x in json.load(open(sys.argv[1])) if x.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; errors=set(tx[\"consistency_errors\"]); assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"failed\"; assert \"unsupported-reduction-ambiguous-width\" in errors; assert tx[\"reduction_width_status\"] == \"ambiguous\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-ambiguous-reduction-transaction-findings.json"
    )
    add_test(
        NAME slp_transaction_summary_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_split_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; roles={item[\"role\"]: item[\"function\"] for item in tx[\"role_provenance\"]}; assert tx[\"opcode\"] == \"add\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lane_source\"][\"lanes\"] == 4; assert {\"discoverCandidate\",\"checkLegality\",\"isProfitable\",\"emitVectorOp\",\"replaceExternalUses\"} <= set(tx[\"functions\"]); assert roles[\"candidate-tree\"] == \"discoverCandidate\"; assert roles[\"legality\"] == \"checkLegality\"; assert roles[\"profitability\"] == \"isProfitable\"; assert roles[\"vector-emission\"] == \"emitVectorOp\"; assert roles[\"scalar-replacement\"] == \"replaceExternalUses\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-findings.json"
    )
    add_test(
        NAME slp_transaction_consistency_mismatch_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_mismatch_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"failed\"; assert tx[\"opcode\"] == \"mul\"; assert tx[\"lane_source\"][\"lanes\"] == 4; assert any(\"opcode-mismatch\" in item for item in tx[\"consistency_errors\"]); sources={item[\"role\"]: item[\"opcode\"] for item in tx[\"opcode_sources\"]}; assert sources[\"legality\"] == \"add\" and sources[\"vector-emission\"] == \"mul\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-findings.json"
    )
    add_test(
        NAME slp_transaction_lane_mapping_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; lm=tx[\"lane_mapping\"]; ops=tx[\"operand_lane_mappings\"]; assert tx[\"consistency\"] == \"ok\"; assert lm[\"kind\"] == \"permutation\"; assert lm[\"lanes\"] == 4; assert lm[\"map\"] == [2,0,3,1]; assert lm[\"inverse_map\"] == [1,3,0,2]; assert lm[\"source\"][\"kind\"] == \"explicit-lane-map\"; assert ops[\"lhs\"][\"map\"] == [2,0,3,1] and ops[\"rhs\"][\"map\"] == [2,0,3,1]; assert ops[\"lhs\"][\"pack_source\"][\"operand_index\"] == 0 and ops[\"rhs\"][\"pack_source\"][\"operand_index\"] == 1' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-findings.json"
    )
    add_test(
        NAME slp_transaction_8lane_lane_mapping_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_8lane_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-8lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; lm=tx[\"lane_mapping\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 8; assert lm[\"map\"] == [4,0,6,2,7,3,5,1]; assert lm[\"inverse_map\"] == [1,7,3,5,0,6,2,4]; assert tx[\"result_lane_mapping\"][\"map\"] == [4,0,6,2,7,3,5,1]; assert len(tx[\"scalar_lane_pairs\"]) == 8' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-8lane-findings.json"
    )
    add_test(
        NAME slp_transaction_invalid_lane_mapping_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_invalid_lane_map_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"failed\"; assert tx[\"lane_mapping\"][\"map\"] == [0,0,1,2]; assert any(item == \"unsupported-lane-map-kind\" for item in tx[\"consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-findings.json"
    )
    add_test(
        NAME slp_transaction_operand_lane_mapping_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_operand_maps_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; ops=tx[\"operand_lane_mappings\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lane_mapping\"][\"map\"] == [1,3,0,2]; assert tx[\"lane_mapping\"][\"inverse_map\"] == [2,0,3,1]; assert ops[\"lhs\"][\"source\"][\"kind\"] == \"explicit-lhs-lane-map\"; assert ops[\"rhs\"][\"source\"][\"kind\"] == \"explicit-rhs-lane-map\"; assert ops[\"lhs\"][\"map\"] == [1,3,0,2] and ops[\"rhs\"][\"map\"] == [1,3,0,2]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-findings.json"
    )
    add_test(
        NAME slp_transaction_operand_lane_mapping_mismatch_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_operand_mismatch_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; ops=tx[\"operand_lane_mappings\"]; assert tx[\"consistency\"] == \"failed\"; assert ops[\"lhs\"][\"map\"] == [1,3,0,2]; assert ops[\"rhs\"][\"map\"] == [2,0,3,1]; assert any(item == \"operand-lane-map-mismatch\" for item in tx[\"consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-findings.json"
    )
    add_test(
        NAME slp_transaction_helper_pack_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; ops=tx[\"operand_lane_mappings\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert ops[\"lhs\"][\"pack_source\"][\"kind\"] == \"pack-helper-call\"; assert ops[\"lhs\"][\"pack_builder\"][\"function\"] == \"buildLHS\"; assert ops[\"rhs\"][\"pack_builder\"][\"function\"] == \"buildRHS\"; assert ops[\"lhs\"][\"pack_builder\"][\"status\"] == \"complete\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-findings.json"
    )
    add_test(
        NAME slp_transaction_helper_loop_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_loop_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-loop-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; ops=tx[\"operand_lane_mappings\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lane_mapping\"][\"map\"] == [1,3,0,2]; assert ops[\"lhs\"][\"pack_builder\"][\"kind\"] == \"helper-loop-map\"; assert ops[\"rhs\"][\"pack_builder\"][\"kind\"] == \"helper-loop-map\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-loop-findings.json"
    )
    add_test(
        NAME slp_transaction_helper_partial_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_partial_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; ops=tx[\"operand_lane_mappings\"]; assert tx[\"consistency\"] == \"failed\"; assert ops[\"lhs\"][\"pack_builder\"][\"status\"] == \"incomplete\"; assert any(item == \"incomplete-pack-builder\" for item in tx[\"consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-findings.json"
    )
    add_test(
        NAME slp_transaction_helper_mismatch_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_mismatch_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; ops=tx[\"operand_lane_mappings\"]; assert tx[\"consistency\"] == \"failed\"; assert ops[\"lhs\"][\"map\"] == [1,3,0,2]; assert ops[\"rhs\"][\"map\"] == [2,0,3,1]; assert any(item == \"operand-lane-map-mismatch\" for item in tx[\"consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-findings.json"
    )
    add_test(
        NAME slp_transaction_result_direct_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_direct_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-direct-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; rm=tx[\"result_lane_mapping\"]; pairs=tx[\"scalar_lane_pairs\"]; assert tx[\"consistency\"] == \"ok\"; assert rm[\"map\"] == [0,1,2,3]; assert rm[\"replacement_source\"][\"kind\"] == \"direct-result-scalar-indexes\"; assert len(pairs) == 4 and pairs[2][\"result_lane\"] == 2' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-direct-findings.json"
    )
    add_test(
        NAME slp_transaction_result_helper_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_helper_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; rm=tx[\"result_lane_mapping\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert rm[\"map\"] == [2,0,3,1]; assert rm[\"source\"][\"kind\"] == \"result-explicit-lane-map\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-findings.json"
    )
    add_test(
        NAME slp_transaction_result_mismatch_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_mismatch_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"failed\"; assert tx[\"result_lane_mapping\"][\"map\"] == [0,1,2,3]; assert any(item == \"unsupported-lane-pairing\" for item in tx[\"consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-findings.json"
    )
    add_test(
        NAME slp_transaction_result_partial_miner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_partial_snippet.cpp --require-marker probe.slp.vectorize-binop > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-partial-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"failed\"; assert tx[\"result_lane_mapping\"][\"replacement_source\"][\"status\"] == \"incomplete\"; assert any(item == \"incomplete-result-lane-mapping\" for item in tx[\"consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-partial-findings.json"
    )
        add_test(
            NAME intent_inference_high_confidence_fixture
            COMMAND
                /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/intent-high.json --require-marker probe.instcombine.add-zero --require-marker probe.instcombine.mul-one --require-marker probe.instcombine.xor-self --require-marker probe.dce.dead-instruction && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); markers={r[\"marker\"] for r in data}; assert {\"probe.instcombine.add-zero\",\"probe.instcombine.mul-one\",\"probe.instcombine.xor-self\",\"probe.dce.dead-instruction\"} <= markers; assert all(r[\"confidence\"] == \"high\" for r in data); assert any(\"replaceInstUsesWith\" in r[\"rewrite_source\"] for r in data); assert all(r[\"intent_candidate\"][\"formal\"][\"domain\"] == \"scalar-bv32\" for r in data)' ${CMAKE_CURRENT_BINARY_DIR}/intent-high.json"
    )
    add_test(
        NAME slp_transaction_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"confidence\"] == \"high\"; assert r[\"side_conditions\"] == []; assert ev[\"formal_inference\"] == \"source-derived-transaction\"; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert ev[\"formal_parameters\"][\"transaction.opcode\"] == \"add\"; assert formal[\"before\"][\"op\"] == \"vec\" and formal[\"after\"][\"op\"] == \"vadd\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-intent.json"
    )
    add_test(
        NAME slp_minmax_transaction_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_reorder_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert ev[\"formal_inference\"] == \"source-derived-transaction\"; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.kind\"] == \"slp-vectorize-minmax\"; assert params[\"transaction.opcode\"] == \"umax\"; assert params[\"transaction.predicate\"] == \"ugt\"; assert formal[\"before\"][\"args\"][0][\"op\"] == \"ite\"; assert formal[\"after\"][\"op\"] == \"vshuffle\" and formal[\"after\"][\"args\"][0][\"op\"] == \"vumax\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-intent.json"
    )
    add_test(
        NAME slp_transaction_summary_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_split_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; assert r[\"confidence\"] == \"high\"; assert r[\"side_conditions\"] == []; assert ev[\"formal_inference\"] == \"source-derived-transaction\"; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.consistency\"] == \"ok\"; assert params[\"transaction.lane_source\"][\"lanes\"] == 4; assert \"emitVectorOp\" in params[\"transaction.functions\"]; assert len(params[\"transaction.role_provenance\"]) >= 5' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-intent.json"
    )
    add_test(
        NAME slp_transaction_consistency_blocks_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_mismatch_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert ev[\"formal_parameters\"][\"transaction.consistency\"] == \"failed\"; assert any(\"opcode-mismatch\" in item for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-intent.json"
    )
    add_test(
        NAME slp_transaction_lane_mapping_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; ops=params[\"transaction.operand_lane_mappings\"]; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.lane_mapping.map\"] == [2,0,3,1]; assert params[\"transaction.lane_mapping.inverse_map\"] == [1,3,0,2]; assert ops[\"lhs\"][\"map\"] == [2,0,3,1] and ops[\"rhs\"][\"map\"] == [2,0,3,1]; assert formal[\"after\"][\"op\"] == \"vshuffle\" and formal[\"after\"][\"mask\"] == [1,3,0,2]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-intent.json"
    )
    add_test(
        NAME slp_transaction_invalid_lane_mapping_blocks_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_invalid_lane_map_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert ev[\"formal_parameters\"][\"transaction.consistency\"] == \"failed\"; assert any(item == \"unsupported-lane-map-kind\" for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-intent.json"
    )
    add_test(
        NAME slp_transaction_operand_lane_mapping_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_operand_maps_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; ops=params[\"transaction.operand_lane_mappings\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.lane_mapping.map\"] == [1,3,0,2]; assert ops[\"lhs\"][\"source\"][\"kind\"] == \"explicit-lhs-lane-map\" and ops[\"rhs\"][\"source\"][\"kind\"] == \"explicit-rhs-lane-map\"; assert formal[\"after\"][\"mask\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-intent.json"
    )
    add_test(
        NAME slp_transaction_operand_lane_mapping_mismatch_blocks_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_operand_mismatch_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert ev[\"formal_parameters\"][\"transaction.consistency\"] == \"failed\"; assert any(item == \"operand-lane-map-mismatch\" for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"]); assert ev[\"formal_parameters\"][\"transaction.operand_lane_mappings\"][\"lhs\"][\"map\"] == [1,3,0,2]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-intent.json"
    )
    add_test(
        NAME slp_transaction_helper_pack_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; ops=params[\"transaction.operand_lane_mappings\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert ops[\"lhs\"][\"pack_source\"][\"kind\"] == \"pack-helper-call\"; assert ops[\"lhs\"][\"pack_builder\"][\"function\"] == \"buildLHS\"; assert formal[\"after\"][\"mask\"] == [1,3,0,2]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-intent.json"
    )
    add_test(
        NAME slp_transaction_helper_loop_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_loop_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-loop-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; ops=params[\"transaction.operand_lane_mappings\"]; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.lane_mapping.map\"] == [1,3,0,2]; assert ops[\"rhs\"][\"pack_builder\"][\"kind\"] == \"helper-loop-map\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-loop-intent.json"
    )
    add_test(
        NAME slp_transaction_helper_partial_blocks_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_partial_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert ev[\"formal_parameters\"][\"transaction.consistency\"] == \"failed\"; assert any(item == \"incomplete-pack-builder\" for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-intent.json"
    )
    add_test(
        NAME slp_transaction_helper_mismatch_blocks_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_mismatch_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert any(item == \"operand-lane-map-mismatch\" for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-intent.json"
    )
    add_test(
        NAME slp_transaction_result_direct_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_direct_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-direct-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.result_lane_mapping\"][\"map\"] == [0,1,2,3]; assert len(params[\"transaction.scalar_lane_pairs\"]) == 4' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-direct-intent.json"
    )
    add_test(
        NAME slp_transaction_result_helper_intent_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_helper_snippet.cpp --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.result_lane_mapping\"][\"map\"] == [2,0,3,1]; assert formal[\"after\"][\"mask\"] == [1,3,0,2]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-intent.json"
    )
    add_test(
        NAME slp_transaction_result_mismatch_blocks_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_mismatch_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert any(item == \"unsupported-lane-pairing\" for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"]); assert ev[\"formal_parameters\"][\"transaction.result_lane_mapping\"][\"map\"] == [0,1,2,3]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-intent.json"
    )
    add_test(
        NAME slp_transaction_result_partial_blocks_inference_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_partial_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-partial-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert any(item == \"incomplete-result-lane-mapping\" for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-partial-intent.json"
    )
    add_test(
        NAME intent_inference_side_condition_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/intent-all.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); guarded=[r for r in data if r[\"line\"] == 47][0]; assert guarded[\"confidence\"] == \"high\"; assert guarded[\"side_conditions\"] == []; assert guarded[\"modeled_side_conditions\"][0][\"kind\"] == \"no-poison-generating-flags\"; assert guarded[\"evidence\"][\"side_condition_lowering\"] == \"modeled\"; assert guarded[\"intent_candidate\"][\"intent\"] == \"result-equivalence\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-all.json"
    )
    add_test(
        NAME intent_inference_low_confidence_jsonl_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --out ${CMAKE_CURRENT_BINARY_DIR}/intent-all.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; low=[r for r in records if r[\"line\"] == 54][0]; assert low[\"confidence\"] == \"low\"; assert low[\"rewrite_source\"] == \"\"; assert low[\"intent_candidate\"][\"rewrite\"]' ${CMAKE_CURRENT_BINARY_DIR}/intent-all.jsonl"
    )
    add_test(
        NAME intent_inference_registry_formal_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); assert len(data) == 19; domains={r[\"intent_candidate\"].get(\"formal\",{}).get(\"domain\") for r in data}; assert {\"scalar-bv32\",\"cfg-bv32\",\"memory-bv32\",\"loop-bv32\"} <= domains; assert all(\"formal\" in r[\"intent_candidate\"] for r in data); scalar={\"probe.instcombine.add-zero\",\"probe.instcombine.mul-one\",\"probe.instcombine.xor-self\",\"probe.dce.dead-instruction\"}; assert all(any(r[\"marker\"] == marker and r[\"intent_candidate\"][\"formal\"][\"domain\"] == \"scalar-bv32\" for r in data) for marker in scalar); assert any(r[\"marker\"] == \"probe.mem2reg.store-load-forward\" and r[\"intent_candidate\"][\"formal\"][\"domain\"] == \"memory-bv32\" for r in data); assert any(r[\"marker\"] == \"probe.licm.invariant-op\" and r[\"intent_candidate\"][\"formal\"][\"domain\"] == \"loop-bv32\" for r in data)' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed.json"
    )
    add_test(
        NAME globalopt_intent_inference_registry_formal_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/global_dead_initializer_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-intent-registry-backed.json --require-marker probe.globalopt.dead-initializer && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); records=[r for r in data if r[\"marker\"] == \"probe.globalopt.dead-initializer\"]; assert len(records) == 1; formal=records[0][\"intent_candidate\"][\"formal\"]; params=records[0][\"evidence\"][\"formal_parameters\"]; assert formal[\"domain\"] == \"global-initializer-observable-v1\"; assert formal[\"contract\"] == \"remove-global-initializer-if-dead-v1\"; assert formal[\"required_safety_facts\"] == [\"initializer-dead\",\"local-linkage\",\"no-uses\"]; assert formal[\"required_witness_cases\"] == [\"i32\",\"ptr\",\"array\"]; assert params[\"global.initializer.safety_status\"] == \"complete\"; assert params[\"global.initializer.contract\"] == \"remove-global-initializer-if-dead-v1\"' ${CMAKE_CURRENT_BINARY_DIR}/globalopt-intent-registry-backed.json"
    )
    add_test(
        NAME campaign_driver_intent_infer_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-infer --infer-intents --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-infer/intent-candidates.jsonl && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-infer/intent-summary.txt && grep -q '^candidates: 6$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-infer/intent-summary.txt && grep -q 'intent-candidates.jsonl' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-infer/commands.log"
    )
    add_test(
        NAME klee_harness_full_probe_surface_fixture
        COMMAND
            /bin/sh -c
            "grep -q 'config.memoryShape < 6' ${CMAKE_CURRENT_SOURCE_DIR}/harnesses/instcombine_harness.cpp && grep -q 'config.loopShape < 5' ${CMAKE_CURRENT_SOURCE_DIR}/harnesses/instcombine_harness.cpp && grep -q 'probe.mem2reg.promotable-alloca' ${CMAKE_CURRENT_SOURCE_DIR}/harnesses/instcombine_harness.cpp && grep -q 'probe.instcombine.redundant-load' ${CMAKE_CURRENT_SOURCE_DIR}/harnesses/instcombine_harness.cpp && grep -q 'probe.loop.canonical-header' ${CMAKE_CURRENT_SOURCE_DIR}/harnesses/instcombine_harness.cpp && grep -q 'probe.licm.invariant-op' ${CMAKE_CURRENT_SOURCE_DIR}/harnesses/instcombine_harness.cpp"
    )
    add_test(
        NAME llm_candidate_pack_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-llm-candidate-pack.py --out ${CMAKE_CURRENT_BINARY_DIR}/llm-prompts.jsonl ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp && grep -q '\"response_schema\"' ${CMAKE_CURRENT_BINARY_DIR}/llm-prompts.jsonl && grep -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/llm-prompts.jsonl && grep -q 'source_excerpt' ${CMAKE_CURRENT_BINARY_DIR}/llm-prompts.jsonl"
    )
    add_test(
        NAME llm_candidate_import_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/llm-import && printf '%s\\n' '{\"candidates\":[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\",\"predicate_source\":\"match(Op1, m_Zero())\",\"constraints\":{\"instruction.opcode\":\"add\",\"rhs.value\":0},\"confidence\":0.91,\"rationale\":\"zero RHS matcher\"},{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp\",\"line\":5,\"marker\":\"probe.unknown\",\"predicate_source\":\"x\",\"constraints\":{},\"confidence\":1.0,\"rationale\":\"bad\"},{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\",\"predicate_source\":\"hasPoisonGeneratingFlags(I)\",\"constraints\":{\"poison.flags\":true},\"confidence\":0.81,\"rationale\":\"unsupported\"}]}' > ${CMAKE_CURRENT_BINARY_DIR}/llm-import/mixed.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-llm-import-candidates.py --input ${CMAKE_CURRENT_BINARY_DIR}/llm-import/mixed.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/llm-import/findings.json --rejected-out ${CMAKE_CURRENT_BINARY_DIR}/llm-import/rejected.jsonl --unsupported-out ${CMAKE_CURRENT_BINARY_DIR}/llm-import/unsupported.jsonl && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/llm-import/findings.json && grep -q '\"marker\": \"probe.instcombine.add-zero\"' ${CMAKE_CURRENT_BINARY_DIR}/llm-import/findings.json && grep -q 'unknown marker' ${CMAKE_CURRENT_BINARY_DIR}/llm-import/rejected.jsonl && grep -q 'poison.flags' ${CMAKE_CURRENT_BINARY_DIR}/llm-import/unsupported.jsonl"
    )
    add_test(
        NAME llm_runner_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-llm-candidate-pack.py --out ${CMAKE_CURRENT_BINARY_DIR}/llm-runner-prompts.jsonl ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-llm-runner.py --prompts ${CMAKE_CURRENT_BINARY_DIR}/llm-runner-prompts.jsonl --out-dir ${CMAKE_CURRENT_BINARY_DIR}/llm-runner --command '${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llm-model.py' && grep -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/llm-runner/responses.jsonl && test -f ${CMAKE_CURRENT_BINARY_DIR}/llm-runner/raw/bundle-0000.json && test ! -s ${CMAKE_CURRENT_BINARY_DIR}/llm-runner/runner-errors.jsonl"
    )
    add_test(
        NAME llm_runner_error_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-llm-candidate-pack.py --out ${CMAKE_CURRENT_BINARY_DIR}/llm-runner-error-prompts.jsonl ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-llm-runner.py --prompts ${CMAKE_CURRENT_BINARY_DIR}/llm-runner-error-prompts.jsonl --out-dir ${CMAKE_CURRENT_BINARY_DIR}/llm-runner-error --command '${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llm-error.py'; test $? -eq 1) && grep -q '\"exit_code\": 7' ${CMAKE_CURRENT_BINARY_DIR}/llm-runner-error/runner-errors.jsonl && grep -q 'synthetic model failure' ${CMAKE_CURRENT_BINARY_DIR}/llm-runner-error/runner-errors.jsonl"
    )
    add_test(
        NAME opt_pipeline_selection_fixture
        COMMAND
            /bin/sh -c
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh --list-pipelines ${CMAKE_CURRENT_SOURCE_DIR}/examples > ${CMAKE_CURRENT_BINARY_DIR}/opt-pipelines.txt && grep -q '^add_zero[[:space:]]scalar[[:space:]]instcombine$' ${CMAKE_CURRENT_BINARY_DIR}/opt-pipelines.txt && grep -q '^nested_diamond[[:space:]]cfg[[:space:]]simplifycfg,instcombine$' ${CMAKE_CURRENT_BINARY_DIR}/opt-pipelines.txt && grep -q '^promotable_alloca[[:space:]]memory[[:space:]]mem2reg,dse,instcombine$' ${CMAKE_CURRENT_BINARY_DIR}/opt-pipelines.txt && grep -q '^counted_loop[[:space:]]loop[[:space:]]loop-simplify,licm,indvars,simplifycfg,instcombine$' ${CMAKE_CURRENT_BINARY_DIR}/opt-pipelines.txt && grep -q '^vector_add_zero[[:space:]]vector[[:space:]]instcombine$' ${CMAKE_CURRENT_BINARY_DIR}/opt-pipelines.txt"
    )
    add_test(
        NAME probe_oracle_cli_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle && printf 'probe.instcombine.add-zero\\nprobe.dce.dead-instruction\\n' > ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/observed.txt && $<TARGET_FILE:cv-probe-oracle> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --observed ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/observed.txt > ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/matched.txt && grep -q '^oracle_status=matched$' ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/matched.txt && : > ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/empty.txt && $<TARGET_FILE:cv-probe-oracle> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --observed ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/empty.txt > ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/empty-result.txt && grep -q '^oracle_status=not-instrumented$' ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/empty-result.txt && printf 'probe.extra\\n' > ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/mismatch-log.txt && $<TARGET_FILE:cv-probe-oracle> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --observed ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/mismatch-log.txt > ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/mismatch.txt && grep -q '^oracle_status=mismatch$' ${CMAKE_CURRENT_BINARY_DIR}/probe-oracle/mismatch.txt"
    )
    add_test(
        NAME semantic_check_ir_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture && $<TARGET_FILE:cv-replay> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --out ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/before.ll && cp ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/before.ll ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/after.ll && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-semantic-check-ir.py --before ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/after.ll --work-dir ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/matched-work > ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/matched.txt && grep -q '^semantic_status=matched$' ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/matched.txt && printf '%s\\n' '; ModuleID = '\\''bad'\\''' 'source_filename = \"bad.ll\"' '' 'define i32 @test(i32 %a, i32 %b) {' 'entry:' '  ret i32 123' '}' > ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/bad.ll && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-semantic-check-ir.py --before ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/bad.ll --work-dir ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/mismatch-work > ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/mismatch.txt; test $? -eq 2) && grep -q '^semantic_status=mismatch$' ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/mismatch.txt && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-semantic-check-ir.py --before ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/after.ll --work-dir ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/error-work --clang ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/no-such-clang > ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/error.txt; test $? -eq 1) && grep -q '^semantic_status=error$' ${CMAKE_CURRENT_BINARY_DIR}/semantic-fixture/error.txt"
    )
    add_test(
        NAME alive2_check_ir_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture && $<TARGET_FILE:cv-replay> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --out ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/before.ll && cp ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/before.ll ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/after.ll && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-alive2-check-ir.py --before ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/after.ll --alive-tv ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --out ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/proved.json --output-log ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/proved.txt && grep -q '\"alive2_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/proved.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-alive2-check-ir.py --before ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/after.ll --alive-tv ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-fail.sh --out ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/failed.json > /dev/null; test $? -eq 1) && grep -q '\"alive2_status\": \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/failed.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-alive2-check-ir.py --before ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/before.ll --after ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/after.ll --alive-tv ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-unsupported.sh --out ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/unsupported.json > /dev/null && grep -q '\"alive2_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/alive2-fixture/unsupported.json"
    )
    add_test(
        NAME opt_host_probe_manifest_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/host-opt-cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/host-opt-cases/add_zero.cfg && O2T_HOST_OPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh O2T_HOST_LLVM_AS=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh ${CMAKE_CURRENT_BINARY_DIR}/host-opt-cases && grep -q '\"observed_markers\":\"probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-cases/opt/manifest.jsonl && grep -q '\"oracle_status\":\"matched\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-cases/opt/manifest.jsonl && grep -q '\"semantic_status\":\"matched\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-cases/opt/manifest.jsonl"
    )
    add_test(
        NAME opt_host_legacy_env_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/host-opt-legacy-env-cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/host-opt-legacy-env-cases/add_zero.cfg && COMPILERVERIF_HOST_OPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh COMPILERVERIF_HOST_LLVM_AS=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh ${CMAKE_CURRENT_BINARY_DIR}/host-opt-legacy-env-cases && grep -q '\"observed_markers\":\"probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-legacy-env-cases/opt/manifest.jsonl && grep -q '\"oracle_status\":\"matched\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-legacy-env-cases/opt/manifest.jsonl && grep -q '\"semantic_status\":\"matched\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-legacy-env-cases/opt/manifest.jsonl"
    )
    add_test(
        NAME opt_host_strict_probe_match_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-cases/add_zero.cfg && O2T_HOST_OPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh O2T_HOST_LLVM_AS=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh --require-observed-probes ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-cases && grep -q '\"oracle_status\":\"matched\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-cases/opt/manifest.jsonl && grep -q '\"status\":\"passed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-cases/opt/manifest.jsonl"
    )
    add_test(
        NAME opt_host_strict_probe_missing_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-missing-cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-missing-cases/add_zero.cfg && (O2T_HOST_OPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-no-probes.sh O2T_HOST_LLVM_AS=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh --require-observed-probes ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-missing-cases; test $? -eq 1) && grep -q '\"oracle_status\":\"not-instrumented\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-missing-cases/opt/manifest.jsonl && grep -q '\"status\":\"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-missing-cases/opt/manifest.jsonl && grep -q '\"message\":\"probe oracle failed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-strict-missing-cases/opt/manifest.jsonl"
    )
    add_test(
        NAME opt_host_semantic_mismatch_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/host-opt-mismatch-cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/host-opt-mismatch-cases/add_zero.cfg && (O2T_HOST_OPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-miscompile.sh O2T_HOST_LLVM_AS=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh ${CMAKE_CURRENT_BINARY_DIR}/host-opt-mismatch-cases; test $? -eq 1) && grep -q '\"semantic_status\":\"mismatch\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-mismatch-cases/opt/manifest.jsonl && grep -q '\"status\":\"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-mismatch-cases/opt/manifest.jsonl"
    )
    add_test(
        NAME opt_host_alive2_manifest_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-cases/add_zero.cfg && O2T_HOST_OPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh O2T_HOST_LLVM_AS=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-cases && grep -q '\"alive2_status\":\"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-cases/opt/manifest.jsonl && grep -q '\"alive2_output\":\"${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-cases/opt/add_zero.alive2.txt\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-cases/opt/manifest.jsonl && grep -q '\"status\":\"passed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-cases/opt/manifest.jsonl"
    )
    add_test(
        NAME opt_host_alive2_failure_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-fail-cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-fail-cases/add_zero.cfg && (O2T_HOST_OPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh O2T_HOST_LLVM_AS=${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/scripts/opt-check-cases.sh --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-fail.sh ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-fail-cases; test $? -eq 1) && grep -q '\"alive2_status\":\"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-fail-cases/opt/manifest.jsonl && grep -q '\"message\":\"Alive2 check failed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-fail-cases/opt/manifest.jsonl && grep -q '\"status\":\"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/host-opt-alive2-fail-cases/opt/manifest.jsonl"
    )
    add_test(
        NAME manifest_summary_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture && printf '%s\\n' '{\"case\":\"ok\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"alive2_status\":\"proved\",\"oracle_status\":\"matched\",\"category\":\"scalar\"}' '{\"case\":\"bad\",\"status\":\"failed\",\"semantic_status\":\"mismatch\",\"alive2_status\":\"failed\",\"oracle_status\":\"matched\",\"category\":\"scalar\",\"semantic_mismatch_input\":\"0,0\",\"before\":\"before.ll\",\"after\":\"after.ll\"}' > ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-manifest.py ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/summary.txt > ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/stdout.txt && grep -q '^cases: 2$' ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/summary.txt && grep -q '^Alive2 status$' ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/summary.txt && grep -q '^  proved: 1$' ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/summary.txt && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/summary.txt && grep -q 'semantic=mismatch alive2=failed' ${CMAKE_CURRENT_BINARY_DIR}/summary-fixture/summary.txt"
    )
    add_test(
        NAME campaign_driver_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-fixture --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-fixture/findings.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-fixture/cases/manifest.jsonl && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-fixture/cases/opt/manifest.jsonl && grep -q '^O2T Campaign Summary$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-fixture/summary.txt && grep -q '^  passed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-fixture/summary.txt"
    )
    add_test(
        NAME campaign_driver_alive2_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2 --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && grep -q 'opt-check-cases.sh --alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2/commands.log && grep -q '\"alive2_status\":\"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2/cases/opt/manifest.jsonl && grep -q '^Alive2 status$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2/summary.txt && grep -q '^  proved: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2/summary.txt"
    )
    add_test(
        NAME campaign_driver_alive2_failure_fixture
        COMMAND
            /bin/sh -c
            "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --mode verify --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2-fail --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-fail.sh --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp 2> ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2-fail.err; test $? -eq 1) && grep -q '\"alive2_status\":\"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2-fail/cases/opt/manifest.jsonl && grep -q 'Alive2 check failed' ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2-fail.err && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-alive2-fail/summary.txt"
    )
    add_test(
        NAME instrumentation_audit_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"matched_pattern\":\"m_Zero\"},{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":4,\"marker\":\"probe.instcombine.mul-one\",\"pass\":\"instcombine\",\"matched_pattern\":\"m_One\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/findings.json && printf '%s\\n' '{\"original_file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"status\":\"rewritten\"}' '{\"original_file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":4,\"marker\":\"probe.instcombine.mul-one\",\"status\":\"skipped\",\"message\":\"no candidate predicate matched\"}' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/manifest.jsonl && printf '%s\\n' 'diff --git a/x b/x' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/instrumentation.patch && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-instrumentation-candidates.py --findings ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/findings.json --manifest ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/manifest.jsonl --patch ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/instrumentation.patch --out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/audit.json --text-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/audit.txt --recommendations-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/recommendations.jsonl && grep -q '\"rewritten\": 1' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/audit.json && grep -q '\"skipped\": 1' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/audit.json && grep -q 'unsupported-ast-shape' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/audit.json && grep -q '^recommendations:$' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/audit.txt && grep -q 'unsupported-ast-shape' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit/recommendations.jsonl"
    )
    add_test(
        NAME instrumentation_audit_missing_manifest_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"matched_pattern\":\"m_Zero\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/findings.json && : > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/manifest.jsonl && : > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/instrumentation.patch && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-instrumentation-candidates.py --require-coverage --findings ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/findings.json --manifest ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/manifest.jsonl --patch ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/instrumentation.patch --out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/audit.json --text-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/audit.txt 2> ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/stderr.txt; test $? -eq 1) && grep -q 'missing-from-manifest' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/audit.json && grep -q 'instrumentation coverage issues: 1' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-missing/stderr.txt"
    )
    add_test(
        NAME instrumentation_audit_error_recommendation_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"matched_pattern\":\"m_Zero\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/findings.json && printf '%s\\n' '{\"original_file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"status\":\"error\",\"message\":\"compile command failed\"}' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/manifest.jsonl && : > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/instrumentation.patch && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-instrumentation-candidates.py --findings ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/findings.json --manifest ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/manifest.jsonl --patch ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/instrumentation.patch --out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/audit.json --text-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/audit.txt --recommendations-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/recommendations.jsonl && grep -q 'instrumenter-error' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/audit.json && grep -q 'compile command failed' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-audit-error/recommendations.jsonl"
    )
    add_test(
        NAME instrumentation_repair_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"predicate_source\":\"custom\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/findings.json && printf '%s\\n' '{\"kind\":\"predicate-text-mismatch\",\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\"}' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/recommendations.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-repair-instrumentation-candidates.py --findings ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/findings.json --recommendations ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/recommendations.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/repaired.json --report-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/report.txt && grep -q '\"predicate_source\": \"customLegalityCheck(X)\"' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/repaired.json && grep -q '^  repaired: 1$' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair/report.txt"
    )
    add_test(
        NAME instrumentation_repair_nearby_line_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":4,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"predicate_source\":\"stale\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/findings.json && printf '%s\\n' '{\"kind\":\"line-mismatch\",\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":4,\"marker\":\"probe.instcombine.add-zero\"}' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/recommendations.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-repair-instrumentation-candidates.py --findings ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/findings.json --recommendations ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/recommendations.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/repaired.json --report-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/report.txt && grep -q '\"line\": 5' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/repaired.json && grep -q 'customLegalityCheck(X)' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-nearby/repaired.json"
    )
    add_test(
        NAME instrumentation_repair_non_repairable_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"predicate_source\":\"custom\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/findings.json && printf '%s\\n' '{\"kind\":\"unsupported-ast-shape\",\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\"}' '{\"kind\":\"instrumenter-error\",\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\",\"message\":\"compile command failed\"}' > ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/recommendations.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-repair-instrumentation-candidates.py --findings ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/findings.json --recommendations ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/recommendations.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/repaired.json --report-out ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/report.txt && grep -q '^  not-repairable: 2$' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/report.txt && grep -q '\"predicate_source\": \"custom\"' ${CMAKE_CURRENT_BINARY_DIR}/instrumentation-repair-non/repaired.json"
    )
    add_test(
        NAME campaign_driver_instrumentation_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-dry-run --emit-instrumentation --instrumentation-dry-run --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-dry-run/instrumentation/instrumentation-candidates.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-dry-run/instrumentation/instrumentation-manifest.jsonl && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-dry-run/instrumentation/instrumentation.patch && grep -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-dry-run/instrumentation/instrumentation-manifest.jsonl && grep -q 'cv-instrument-llvm-tree.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-dry-run/commands.log"
    )
    add_test(
        NAME campaign_driver_instrumentation_audit_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-dry-run --emit-instrumentation --instrumentation-dry-run --audit-instrumentation --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-dry-run/instrumentation-audit.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-dry-run/instrumentation-audit.txt && grep -q 'candidate-only' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-dry-run/instrumentation-audit.json && grep -q 'cv-audit-instrumentation-candidates.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-dry-run/commands.log"
    )
    add_test(
        NAME campaign_driver_instrumentation_recommend_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-recommend-dry-run --emit-instrumentation --instrumentation-dry-run --recommend-instrumentation --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-recommend-dry-run/instrumentation-audit.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-recommend-dry-run/instrumentation-recommendations.jsonl && grep -q 'cv-audit-instrumentation-candidates.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-recommend-dry-run/commands.log && grep -q -- '--recommendations-out' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-recommend-dry-run/commands.log"
    )
    add_test(
        NAME campaign_driver_instrumentation_repair_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-repair-dry-run --emit-instrumentation --instrumentation-dry-run --repair-instrumentation-candidates --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-repair-dry-run/instrumentation-repaired-findings.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-repair-dry-run/instrumentation-repair-report.txt && grep -q 'cv-repair-instrumentation-candidates.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-repair-dry-run/commands.log"
    )
    add_test(
        NAME campaign_driver_instrumentation_audit_strict_fixture
        COMMAND
            /bin/sh -c
            "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-strict --emit-instrumentation --instrumentation-dry-run --require-instrumentation-coverage --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp 2> ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-strict.err; test $? -eq 0) && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-strict/instrumentation-audit.json && grep -q 'candidate-only' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-audit-strict/instrumentation-audit.json"
    )
    add_test(
        NAME campaign_driver_instrumentation_llm_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-llm && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.mul-one\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"matched_pattern\":\"synthetic m_One\",\"source\":\"synthetic m_One\",\"constraints\":{\"instruction.opcode\":\"mul\",\"rhs.value\":1},\"finding_source\":\"llm\",\"confidence\":0.88}]' > ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-llm/llm-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-llm/out --emit-instrumentation --instrumentation-dry-run --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-llm/llm-findings.json --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-llm/out/instrumentation/instrumentation-candidates.json && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-llm/out/instrumentation/instrumentation-manifest.jsonl && grep -q 'probe.instcombine.mul-one' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-llm/out/instrumentation/instrumentation-manifest.jsonl"
    )
    add_test(
        NAME campaign_driver_instrumentation_missing_tool_fixture
        COMMAND
            /bin/sh -c
            "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-missing-tool --emit-instrumentation --instrumenter ${CMAKE_CURRENT_BINARY_DIR}/no-such-instrumenter --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp 2> ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-missing-tool.err; test $? -eq 2) && grep -q 'source instrumenter is not executable' ${CMAKE_CURRENT_BINARY_DIR}/campaign-instrumentation-missing-tool.err"
    )
    add_test(
        NAME campaign_driver_strict_probe_verify_fixture
        COMMAND
            /bin/sh -c
            "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --mode verify --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-strict-probe-verify --require-observed-probes --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-no-probes.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp; test $? -eq 1) && grep -q '\"oracle_status\":\"not-instrumented\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-strict-probe-verify/cases/opt/manifest.jsonl && grep -q '\"status\":\"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-strict-probe-verify/cases/opt/manifest.jsonl && grep -q '\"message\":\"probe oracle failed\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-strict-probe-verify/cases/opt/manifest.jsonl"
    )
    add_test(
        NAME campaign_driver_llm_merge_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.mul-one\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"matched_pattern\":\"synthetic m_One\",\"source\":\"synthetic m_One\",\"constraints\":{\"instruction.opcode\":\"mul\",\"rhs.value\":1},\"finding_source\":\"llm\",\"confidence\":0.88}]' > ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm/llm-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm/out --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm/llm-findings.json --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm/out/findings.json && grep -q '^cases: 2$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm/out/summary.txt"
    )
    add_test(
        NAME llm_candidate_review_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/llm-review && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"source\":\"m_Zero\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/llm-review/static.json && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"source\":\"duplicate\"},{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.mul-one\",\"source\":\"accepted\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/llm-review/llm.json && printf '%s\\n' '{\"candidate\":{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.xor-self\",\"predicate_source\":\"bad\"},\"reason\":\"invalid line\"}' > ${CMAKE_CURRENT_BINARY_DIR}/llm-review/rejected.jsonl && printf '%s\\n' '{\"candidate\":{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"predicate_source\":\"poison\"},\"reason\":\"unsupported constraints: poison.flags\",\"unsupported_constraints\":[\"poison.flags\"]}' > ${CMAKE_CURRENT_BINARY_DIR}/llm-review/unsupported.jsonl && printf '%s\\n' '{\"case\":\"mul_one\",\"expected_markers\":\"probe.instcombine.mul-one\",\"observed_markers\":\"\"}' > ${CMAKE_CURRENT_BINARY_DIR}/llm-review/cases.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-llm-review-candidates.py --static-findings ${CMAKE_CURRENT_BINARY_DIR}/llm-review/static.json --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/llm-review/llm.json --rejected ${CMAKE_CURRENT_BINARY_DIR}/llm-review/rejected.jsonl --unsupported ${CMAKE_CURRENT_BINARY_DIR}/llm-review/unsupported.jsonl --cases-manifest ${CMAKE_CURRENT_BINARY_DIR}/llm-review/cases.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/llm-review/review.md && grep -q '^accepted: 1$' ${CMAKE_CURRENT_BINARY_DIR}/llm-review/review.md && grep -q '^duplicate: 1$' ${CMAKE_CURRENT_BINARY_DIR}/llm-review/review.md && grep -q '^unsupported_constraints: 1$' ${CMAKE_CURRENT_BINARY_DIR}/llm-review/review.md && grep -q '^generated_case: 1$' ${CMAKE_CURRENT_BINARY_DIR}/llm-review/review.md"
    )
    add_test(
        NAME campaign_driver_llm_review_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.mul-one\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"matched_pattern\":\"synthetic m_One\",\"source\":\"synthetic m_One\",\"constraints\":{\"instruction.opcode\":\"mul\",\"rhs.value\":1},\"finding_source\":\"llm\",\"confidence\":0.88}]' > ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/llm-findings.json && printf '%s\\n' '{\"candidate\":{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.unknown\",\"predicate_source\":\"x\"},\"reason\":\"unknown marker\"}' > ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/rejected.jsonl && printf '%s\\n' '{\"candidate\":{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"predicate_source\":\"poison\"},\"reason\":\"unsupported constraints: poison.flags\",\"unsupported_constraints\":[\"poison.flags\"]}' > ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/unsupported.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/out --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/llm-findings.json --llm-rejected ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/rejected.jsonl --llm-unsupported ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/unsupported.jsonl --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/out/llm-review.txt && grep -q '^invalid: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/out/llm-review.txt && grep -q '^unsupported_constraints: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-review/out/llm-review.txt"
    )
    add_test(
        NAME campaign_driver_llm_command_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-command --llm-command '${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llm-model.py' --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-command/llm-prompts.jsonl && grep -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-command/llm-runner/responses.jsonl && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-command/llm-findings.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-command/llm-review.txt && grep -q '^duplicate: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-command/llm-review.txt && grep -q '^  passed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-llm-command/summary.txt"
    )
    add_test(
        NAME instrumented_llvm_playbook_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build/bin ${CMAKE_CURRENT_BINARY_DIR}/playbook/cases && touch ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build/compile_commands.json ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build/bin/opt ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build/bin/llvm-as ${CMAKE_CURRENT_BINARY_DIR}/playbook/instrumentation.patch && chmod +x ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build/bin/opt ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build/bin/llvm-as && ${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrumented-llvm-playbook.sh check ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build > ${CMAKE_CURRENT_BINARY_DIR}/playbook/check.txt && grep -q '^LLVM source:' ${CMAKE_CURRENT_BINARY_DIR}/playbook/check.txt && ${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrumented-llvm-playbook.sh configure ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build > ${CMAKE_CURRENT_BINARY_DIR}/playbook/configure.txt && grep -q 'cmake .*--target opt llvm-as' ${CMAKE_CURRENT_BINARY_DIR}/playbook/configure.txt && ${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrumented-llvm-playbook.sh --allow-dirty apply ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/playbook/instrumentation.patch > ${CMAKE_CURRENT_BINARY_DIR}/playbook/apply.txt && grep -q 'git .* apply ' ${CMAKE_CURRENT_BINARY_DIR}/playbook/apply.txt && ${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrumented-llvm-playbook.sh run-opt ${CMAKE_CURRENT_BINARY_DIR}/playbook/llvm-build ${CMAKE_CURRENT_BINARY_DIR}/playbook/cases > ${CMAKE_CURRENT_BINARY_DIR}/playbook/run-opt.txt && grep -q 'O2T_HOST_OPT=' ${CMAKE_CURRENT_BINARY_DIR}/playbook/run-opt.txt"
    )
    add_test(
        NAME instrumented_llvm_playbook_strict_run_opt_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/playbook-strict/llvm-build/bin ${CMAKE_CURRENT_BINARY_DIR}/playbook-strict/cases && ${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrumented-llvm-playbook.sh run-opt --require-observed-probes ${CMAKE_CURRENT_BINARY_DIR}/playbook-strict/llvm-build ${CMAKE_CURRENT_BINARY_DIR}/playbook-strict/cases > ${CMAKE_CURRENT_BINARY_DIR}/playbook-strict/run-opt.txt && grep -q 'opt-check-cases.sh --require-observed-probes' ${CMAKE_CURRENT_BINARY_DIR}/playbook-strict/run-opt.txt"
    )
    add_test(
        NAME instrumented_llvm_playbook_alive2_run_opt_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/playbook-alive2/llvm-build/bin ${CMAKE_CURRENT_BINARY_DIR}/playbook-alive2/cases && ${CMAKE_CURRENT_SOURCE_DIR}/scripts/instrumented-llvm-playbook.sh run-opt --require-observed-probes --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh ${CMAKE_CURRENT_BINARY_DIR}/playbook-alive2/llvm-build ${CMAKE_CURRENT_BINARY_DIR}/playbook-alive2/cases > ${CMAKE_CURRENT_BINARY_DIR}/playbook-alive2/run-opt.txt && grep -q 'opt-check-cases.sh --require-observed-probes --alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/playbook-alive2/run-opt.txt"
    )
    add_test(
        NAME instrumented_campaign_runner_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/campaign/instrumentation ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/campaign/cases ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/llvm-build/bin && touch ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/campaign/instrumentation/instrumentation.patch && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-instrumented-campaign.py --allow-dirty --campaign ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/campaign --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/llvm-build > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/stdout.txt && grep -q 'git .* apply .*instrumentation.patch' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/stdout.txt && grep -q 'opt-check-cases.sh --require-observed-probes' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/stdout.txt && grep -q 'run-opt --require-observed-probes' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/campaign/instrumented-commands.log && grep -q 'cv-summarize-verification-campaign.py --campaign' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner/campaign/instrumented-commands.log"
    )
    add_test(
        NAME instrumented_campaign_runner_allow_dirty_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/campaign/instrumentation ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/campaign/cases ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/llvm-build/bin && touch ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/campaign/instrumentation/instrumentation.patch && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-instrumented-campaign.py --allow-dirty --campaign ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/campaign --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/llvm-build > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/stdout.txt && grep -q -- '--allow-dirty apply' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-dirty/campaign/instrumented-commands.log"
    )
    add_test(
        NAME instrumented_campaign_runner_missing_patch_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/campaign/instrumentation ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/campaign/cases ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/llvm-build && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-instrumented-campaign.py --campaign ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/campaign --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/llvm-build 2> ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/stderr.txt; test $? -eq 2) && grep -q 'instrumentation patch does not exist' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-missing/stderr.txt"
    )
    add_test(
        NAME instrumented_campaign_runner_execute_summary_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/instrumentation ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/cases ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/llvm-build/bin && touch ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/instrumentation/instrumentation.patch && printf '%s\\n' '{\"case\":\"klee_add_zero\",\"origin\":\"klee\",\"config\":\"klee_add_zero.cfg\"}' > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/cases/source-manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-instrumented-campaign.py --execute --allow-dirty --playbook ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-instrumented-playbook.sh --campaign ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/llvm-build > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/stdout.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/instrumented-summary.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/verification-summary.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/verification-summary.json && grep -q '^  klee: 1$' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/campaign/verification-summary.txt && grep -q 'verification_summary:' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-exec/stdout.txt"
    )
    add_test(
        NAME instrumented_campaign_runner_alive2_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign/instrumentation ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign/cases ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/llvm-build/bin && touch ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign/instrumentation/instrumentation.patch && printf '%s\\n' '{\"case\":\"klee_add_zero\",\"origin\":\"klee\",\"config\":\"klee_add_zero.cfg\"}' > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign/cases/source-manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-instrumented-campaign.py --execute --allow-dirty --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --playbook ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-instrumented-playbook.sh --campaign ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/llvm-build > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/stdout.txt && grep -q 'run-opt --require-observed-probes --alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign/instrumented-commands.log && grep -q '^Alive2 status$' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign/verification-summary.txt && grep -q '^  proved: 1$' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-runner-alive2/campaign/verification-summary.txt"
    )
    add_test(
        NAME verification_workflow_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/llvm-build && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/out --sources ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/llvm-build --backfill-gaps --package-campaign --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/stdout.txt && grep -q '\\[instrument\\].*cv-run-campaign.py' ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/out/workflow-commands.log && grep -q '\\[klee\\].*cv-run-klee-campaign.py' ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/out/workflow-commands.log && grep -q '\\[package\\].*cv-package-verification-campaign.py' ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/out/workflow-commands.log && grep -q '\\[verify\\].*cv-run-instrumented-campaign.py' ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/out/workflow-commands.log && grep -q '\"status\": \"planned\"' ${CMAKE_CURRENT_BINARY_DIR}/workflow-dry/out/workflow-summary.json"
    )
    add_test(
        NAME verification_workflow_alive2_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/llvm-build && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/out --sources ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/llvm-build --klee-check --backfill-gaps --backfill-check --package-campaign --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/stdout.txt && grep -q '\\[instrument\\].*--alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/out/workflow-commands.log && grep -q '\\[klee\\].*--alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/out/workflow-commands.log && grep -q '\\[verify\\].*--alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/workflow-alive2-dry/out/workflow-commands.log"
    )
    add_test(
        NAME verification_workflow_audit_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/workflow-audit-dry/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/workflow-audit-dry/llvm-build && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --from-stage instrument --to-stage instrument --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-audit-dry/out --sources ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp --audit-instrumentation --require-instrumentation-coverage --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/workflow-audit-dry/stdout.txt && grep -q '\\[instrument\\].*--audit-instrumentation' ${CMAKE_CURRENT_BINARY_DIR}/workflow-audit-dry/out/workflow-commands.log && grep -q '\\[instrument\\].*--require-instrumentation-coverage' ${CMAKE_CURRENT_BINARY_DIR}/workflow-audit-dry/out/workflow-commands.log"
    )
    add_test(
        NAME verification_workflow_recommend_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --from-stage instrument --to-stage instrument --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-recommend-dry/out --sources ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp --recommend-instrumentation --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/workflow-recommend-dry.stdout && grep -q '\\[instrument\\].*--recommend-instrumentation' ${CMAKE_CURRENT_BINARY_DIR}/workflow-recommend-dry/out/workflow-commands.log"
    )
    add_test(
        NAME verification_workflow_repair_retry_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --from-stage instrument --to-stage instrument --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-repair-retry-dry/out --sources ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp --repair-instrumentation-candidates --retry-repaired-instrumentation --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/workflow-repair-retry-dry.stdout && grep -q '\\[instrument\\].*--repair-instrumentation-candidates' ${CMAKE_CURRENT_BINARY_DIR}/workflow-repair-retry-dry/out/workflow-commands.log && grep -q '\\[instrument\\].*--retry-repaired-instrumentation' ${CMAKE_CURRENT_BINARY_DIR}/workflow-repair-retry-dry/out/workflow-commands.log"
    )
    add_test(
        NAME verification_workflow_instrument_package_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/klee/cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/klee/cases/add_zero.cfg && printf '%s\\n' '{\"case\":\"add_zero\",\"config\":\"add_zero.cfg\"}' > ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/klee/cases/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --execute --from-stage instrument --to-stage package --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/out --sources ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp --klee-campaign ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/klee --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/stdout.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/out/instrumentation/instrumentation/instrumentation.patch && test -f ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/out/verification-campaign/cases/klee_add_zero.cfg && test -f ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/out/verification-campaign/instrumentation/instrumentation.patch && grep -q '\"status\": \"passed\"' ${CMAKE_CURRENT_BINARY_DIR}/workflow-instrument-package/out/workflow-summary.json"
    )
    add_test(
        NAME verification_workflow_klee_package_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --from-stage klee --to-stage package --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-klee-package --backfill-gaps > ${CMAKE_CURRENT_BINARY_DIR}/workflow-klee-package.stdout && grep -q '\\[klee\\].*--backfill-gaps' ${CMAKE_CURRENT_BINARY_DIR}/workflow-klee-package/workflow-commands.log && grep -q '\\[package\\].*cv-package-verification-campaign.py' ${CMAKE_CURRENT_BINARY_DIR}/workflow-klee-package/workflow-commands.log"
    )
    add_test(
        NAME verification_workflow_verify_fake_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/campaign/instrumentation ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/campaign/cases ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/llvm-src ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/llvm-build/bin && touch ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/campaign/instrumentation/instrumentation.patch && printf '%s\\n' '{\"case\":\"klee_add_zero\",\"origin\":\"klee\",\"config\":\"klee_add_zero.cfg\"}' > ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/campaign/cases/source-manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --execute --from-stage verify --to-stage verify --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/out --package-out ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/campaign --llvm-source ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/llvm-src --llvm-build ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/llvm-build --execute-instrumented --allow-dirty --playbook ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-instrumented-playbook.sh > ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/stdout.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/campaign/verification-summary.json && grep -q '\"status\": \"passed\"' ${CMAKE_CURRENT_BINARY_DIR}/workflow-verify/out/workflow-summary.json"
    )
    add_test(
        NAME verification_workflow_missing_sources_fixture
        COMMAND
            /bin/sh -c
            "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-verification-workflow.py --from-stage instrument --to-stage instrument --out ${CMAKE_CURRENT_BINARY_DIR}/workflow-missing-sources 2> ${CMAKE_CURRENT_BINARY_DIR}/workflow-missing-sources.err; test $? -eq 2) && grep -q 'instrument stage requires --sources' ${CMAKE_CURRENT_BINARY_DIR}/workflow-missing-sources.err"
    )
    add_test(
        NAME verification_summary_origins_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/cases/opt && printf '%s\\n' '{\"case\":\"klee_add_zero\",\"origin\":\"klee\",\"config\":\"klee_add_zero.cfg\",\"marker\":\"\",\"gap_type\":\"\"}' '{\"case\":\"backfill_loop\",\"origin\":\"backfill\",\"config\":\"backfill_loop.cfg\",\"marker\":\"probe.loop.canonical-header\",\"gap_type\":\"never_generated\"}' > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/cases/source-manifest.jsonl && printf '%s\\n' '{\"case\":\"klee_add_zero\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"alive2_status\":\"proved\",\"oracle_status\":\"matched\",\"expected_markers\":\"probe.instcombine.add-zero\",\"observed_markers\":\"probe.instcombine.add-zero\",\"missing_markers\":\"\",\"unexpected_markers\":\"\"}' '{\"case\":\"backfill_loop\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"alive2_status\":\"unsupported\",\"oracle_status\":\"matched\",\"expected_markers\":\"probe.loop.canonical-header\",\"observed_markers\":\"probe.loop.canonical-header\",\"missing_markers\":\"\",\"unexpected_markers\":\"\"}' > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/cases/opt/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-verification-campaign.py --campaign ${CMAKE_CURRENT_BINARY_DIR}/verification-summary --out ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/stdout.txt && grep -q '^  klee: 1$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.txt && grep -q '^  backfill: 1$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.txt && grep -q '^Alive2 status$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.txt && grep -q '^  proved: 1$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.txt && grep -q '^  unsupported: 1$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.txt && grep -q 'probe.loop.canonical-header' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary/summary.json"
    )
    add_test(
        NAME verification_summary_missing_probe_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/cases/opt && printf '%s\\n' '{\"case\":\"backfill_add_zero\",\"origin\":\"backfill\",\"config\":\"backfill_add_zero.cfg\",\"marker\":\"probe.instcombine.add-zero\",\"gap_type\":\"generated_not_observed\"}' > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/cases/source-manifest.jsonl && printf '%s\\n' '{\"case\":\"backfill_add_zero\",\"status\":\"failed\",\"message\":\"probe oracle failed\",\"semantic_status\":\"matched\",\"alive2_status\":\"failed\",\"alive2_output\":\"alive2.txt\",\"oracle_status\":\"not-instrumented\",\"expected_markers\":\"probe.instcombine.add-zero\",\"observed_markers\":\"\",\"missing_markers\":\"probe.instcombine.add-zero\",\"unexpected_markers\":\"\",\"probe_log\":\"probe.log\",\"before\":\"before.ll\",\"after\":\"after.ll\"}' > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/cases/opt/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-verification-campaign.py --campaign ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing --out ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/stdout.txt && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.txt && grep -q '^  not-instrumented: 1$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.txt && grep -q '^Alive2 status$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.txt && grep -q 'case=backfill_add_zero origin=backfill marker=probe.instcombine.add-zero gap=generated_not_observed status=failed semantic=matched alive2=failed oracle=not-instrumented' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.txt && grep -q 'alive2_output=alive2.txt' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.txt && grep -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-missing/summary.json"
    )
    add_test(
        NAME verification_summary_unknown_case_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/cases/opt && : > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/cases/source-manifest.jsonl && printf '%s\\n' '{\"case\":\"foreign_case\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"oracle_status\":\"matched\",\"expected_markers\":\"probe.instcombine.mul-one\",\"observed_markers\":\"probe.instcombine.mul-one\",\"missing_markers\":\"\",\"unexpected_markers\":\"\"}' > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/cases/opt/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-verification-campaign.py --campaign ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown --out ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/stdout.txt && grep -q '^  unknown: 1$' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/summary.txt && grep -q 'foreign_case' ${CMAKE_CURRENT_BINARY_DIR}/verification-summary-unknown/summary.json"
    )
    add_test(
        NAME klee_coverage_summary_extract_only_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only && printf '%s\\n' '{\"case\":\"scalar\",\"coverage\":[\"probe.instcombine.add-zero\"],\"reduced\":false}' '{\"case\":\"memory\",\"coverage\":[\"probe.mem2reg.promotable-alloca\",\"probe.mem2reg.store-load-forward\"],\"reduced\":true}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/cases.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-klee-campaign.py --cases-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/cases.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/stdout.txt && grep -q '^generated_cases: 2$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/summary.txt && grep -q '^reduced_cases: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/summary.txt && grep -q '^  memory: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/summary.txt && grep -q 'probe.mem2reg.promotable-alloca' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-extract-only/summary.json"
    )
    add_test(
        NAME klee_coverage_summary_vector_extract_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract && printf '%s\\n' '{\"case\":\"vector_shuffle\",\"coverage\":[\"probe.vector.shuffle-identity\"],\"reduced\":false}' '{\"case\":\"vector_scalable\",\"coverage\":[\"probe.vector.scalable.or-zero\"],\"reduced\":false}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/cases.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-klee-campaign.py --cases-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/cases.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/stdout.txt && grep -q '^generated_cases: 2$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt && grep -q '^Generated categories$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt && grep -q '^  vector: 2$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt && grep -q '^  probe.vector.shuffle-identity$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt && grep -q '^  probe.vector.scalable.or-zero$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt && ! grep -A80 '^Never generated markers$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt | grep -q '^  probe.vector.shuffle-identity$' && ! grep -A80 '^Never generated markers$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.txt | grep -q '^  probe.vector.scalable.or-zero$' && grep -q 'probe.vector.scalable.or-zero' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-extract/summary.json"
    )
    add_test(
        NAME klee_coverage_summary_replay_matched_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay && printf '%s\\n' '{\"case\":\"scalar\",\"coverage\":[\"probe.instcombine.add-zero\"],\"reduced\":false}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/cases.jsonl && printf '%s\\n' '{\"case\":\"scalar\",\"category\":\"scalar\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"alive2_status\":\"proved\",\"oracle_status\":\"matched\",\"expected_markers\":\"probe.instcombine.add-zero\",\"observed_markers\":\"probe.instcombine.add-zero\",\"missing_markers\":\"\",\"unexpected_markers\":\"\"}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/opt.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-klee-campaign.py --cases-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/cases.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/opt.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/stdout.txt && grep -q '^checked_cases: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt && grep -q '^  passed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt && grep -q '^  matched: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt && grep -q '^Alive2 status$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt && grep -q '^  proved: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt && grep -q '^Generated but not observed markers$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt && grep -A1 '^Generated but not observed markers$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-replay/summary.txt | grep -q '^  none$'"
    )
    add_test(
        NAME klee_coverage_summary_vector_replay_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay && printf '%s\\n' '{\"case\":\"vector_shuffle\",\"coverage\":[\"probe.vector.shuffle-identity\"],\"reduced\":false}' '{\"case\":\"vector_scalable\",\"coverage\":[\"probe.vector.scalable.or-zero\"],\"reduced\":false}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/cases.jsonl && printf '%s\\n' '{\"case\":\"vector_shuffle\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"alive2_status\":\"proved\",\"oracle_status\":\"matched\",\"expected_markers\":\"probe.vector.shuffle-identity\",\"observed_markers\":\"probe.vector.shuffle-identity\",\"missing_markers\":\"\",\"unexpected_markers\":\"\"}' '{\"case\":\"vector_scalable\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"alive2_status\":\"proved\",\"oracle_status\":\"matched\",\"expected_markers\":\"probe.vector.scalable.or-zero\",\"observed_markers\":\"probe.vector.scalable.or-zero\",\"missing_markers\":\"\",\"unexpected_markers\":\"\"}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/opt.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-klee-campaign.py --cases-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/cases.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/opt.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/stdout.txt && grep -q '^  vector: 2$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt && grep -q '^Checked categories$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt && grep -q '^  vector: 2$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt && grep -q '^Observed markers$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt && grep -q '^  probe.vector.shuffle-identity$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt && grep -q '^  probe.vector.scalable.or-zero$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt && grep -q '^Generated but not observed markers$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt && grep -A1 '^Generated but not observed markers$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-vector-replay/summary.txt | grep -q '^  none$'"
    )
    add_test(
        NAME klee_coverage_summary_missing_probe_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing && printf '%s\\n' '{\"case\":\"scalar\",\"coverage\":[\"probe.instcombine.add-zero\"],\"reduced\":false}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/cases.jsonl && printf '%s\\n' '{\"case\":\"scalar\",\"category\":\"scalar\",\"status\":\"failed\",\"message\":\"probe oracle failed\",\"semantic_status\":\"matched\",\"alive2_status\":\"failed\",\"oracle_status\":\"not-instrumented\",\"expected_markers\":\"probe.instcombine.add-zero\",\"observed_markers\":\"\",\"missing_markers\":\"probe.instcombine.add-zero\",\"unexpected_markers\":\"\"}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/opt.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-summarize-klee-campaign.py --cases-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/cases.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/opt.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.txt --json-out ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.json > ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/stdout.txt && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.txt && grep -q '^  not-instrumented: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.txt && grep -q '^Alive2 status$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.txt && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.txt && grep -q '^  probe.instcombine.add-zero$' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.txt && grep -q 'case=scalar status=failed semantic=matched alive2=failed oracle=not-instrumented missing=probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/klee-coverage-missing/summary.txt"
    )
    add_test(
        NAME klee_backfill_explicit_loop_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-backfill-coverage-gaps.py --markers probe.licm.invariant-op --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-loop --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> --reduce && grep -q '^loop_shape=3$' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-loop/licm_invariant_op.reduced.cfg && grep -q '\"marker\": \"probe.licm.invariant-op\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-loop/manifest.jsonl && test -f ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-loop/licm_invariant_op.ll"
    )
    add_test(
        NAME klee_backfill_gap_families_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families && printf '%s\\n' '{\"markers\":{\"never_generated\":[\"probe.instcombine.mul-one\",\"probe.simplifycfg.diamond\",\"probe.dse.dead-store\",\"probe.dce.dead-loop-instruction\",\"probe.vector.shuffle-identity\",\"probe.vector.scalable.or-zero\"],\"generated_not_observed\":[]}}' > ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/coverage.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-backfill-coverage-gaps.py --coverage-json ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/coverage.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> && grep -q '^arith_opcode=2$' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/instcombine_mul_one.cfg && grep -q '^shape=1$' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/simplifycfg_diamond.cfg && grep -q '^memory_shape=3$' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/dse_dead_store.cfg && grep -q '^loop_shape=4$' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/dce_dead_loop_instruction.cfg && grep -q '^vector_shape=4$' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/vector_shuffle_identity.cfg && grep -q '^vector_shape=17$' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/vector_scalable_or_zero.cfg && grep -q 'shufflevector <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/vector_shuffle_identity.ll && grep -q 'or <vscale x 4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/vector_scalable_or_zero.ll && test $(grep -c '\"gap_type\": \"never_generated\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-families/out/manifest.jsonl) -eq 6"
    )
    add_test(
        NAME klee_backfill_strict_unsupported_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-strict && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-backfill-coverage-gaps.py --markers probe.unsupported.example --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-strict/out --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> --strict > ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-strict/stdout.txt 2> ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-strict/stderr.txt; test $? -eq 1) && grep -q 'unsupported marker: probe.unsupported.example' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-strict/stderr.txt"
    )
    add_test(
        NAME klee_backfill_check_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-backfill-coverage-gaps.py --markers probe.instcombine.add-zero --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-check --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> --check --require-observed-probes --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-backfill-match.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh && grep -q '\"oracle_status\":\"matched\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-check/opt/manifest.jsonl && grep -q '\"status\":\"passed\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-check/opt/manifest.jsonl"
    )
    add_test(
        NAME klee_backfill_alive2_check_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-backfill-coverage-gaps.py --markers probe.instcombine.add-zero --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-alive2-check --replay $<TARGET_FILE:cv-replay> --reducer $<TARGET_FILE:cv-reduce-config> --check --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --require-observed-probes --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt-backfill-match.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh && grep -q '\"alive2_status\":\"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-alive2-check/opt/manifest.jsonl && grep -q '\"status\":\"passed\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-backfill-alive2-check/opt/manifest.jsonl"
    )
    add_test(
        NAME verification_package_klee_only_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/in/cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/in/cases/add_zero.cfg && printf '; fake ir\\n' > ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/in/cases/add_zero.ll && printf '%s\\n' '{\"case\":\"add_zero\",\"config\":\"add_zero.cfg\",\"ir\":\"add_zero.ll\",\"coverage\":[\"probe.instcombine.add-zero\"]}' > ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/in/cases/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-package-verification-campaign.py --klee-campaign ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/in --out ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/out > ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/stdout.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/out/cases/klee_add_zero.cfg && test -f ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/out/cases/klee_add_zero.ll && grep -q '\"origin\": \"klee\"' ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/out/cases/source-manifest.jsonl && grep -q '\"cases\": 1' ${CMAKE_CURRENT_BINARY_DIR}/package-klee-only/out/package-summary.json"
    )
    add_test(
        NAME verification_package_backfill_collision_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/in/cases ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/in/backfill && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/in/cases/shared.cfg && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/counted_loop.cfg ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/in/backfill/shared.cfg && printf '%s\\n' '{\"case\":\"shared\",\"config\":\"shared.cfg\",\"coverage\":[\"probe.instcombine.add-zero\"]}' > ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/in/cases/manifest.jsonl && printf '%s\\n' '{\"case\":\"shared\",\"config\":\"shared.cfg\",\"marker\":\"probe.loop.canonical-header\",\"gap_type\":\"never_generated\",\"coverage\":[\"probe.loop.canonical-header\"]}' > ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/in/backfill/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-package-verification-campaign.py --klee-campaign ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/in --out ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/out > ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/stdout.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/out/cases/klee_shared.cfg && test -f ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/out/cases/backfill_shared.cfg && grep -q '\"origin\": \"backfill\"' ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/out/cases/source-manifest.jsonl && grep -q '\"backfill\": 1' ${CMAKE_CURRENT_BINARY_DIR}/package-backfill/out/package-summary.json"
    )
    add_test(
        NAME verification_package_instrumentation_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/in/cases ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/inst && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/in/cases/add_zero.cfg && printf '%s\\n' '{\"case\":\"add_zero\",\"config\":\"add_zero.cfg\"}' > ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/in/cases/manifest.jsonl && printf 'patch-body\\n' > ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/inst/instrumentation.patch && printf '{}\\n' > ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/inst/instrumentation-candidates.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-package-verification-campaign.py --klee-campaign ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/in --instrumentation ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/inst --out ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/out > ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/stdout.txt && grep -q 'patch-body' ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/out/instrumentation/instrumentation.patch && test -f ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/out/instrumentation/instrumentation-candidates.json && grep -q '\"instrumentation_patch\": true' ${CMAKE_CURRENT_BINARY_DIR}/package-instrumentation/out/package-summary.json"
    )
    add_test(
        NAME verification_package_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/package-dry-run/in/cases && cp ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg ${CMAKE_CURRENT_BINARY_DIR}/package-dry-run/in/cases/add_zero.cfg && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-package-verification-campaign.py --dry-run --klee-campaign ${CMAKE_CURRENT_BINARY_DIR}/package-dry-run/in --out ${CMAKE_CURRENT_BINARY_DIR}/package-dry-run/out > ${CMAKE_CURRENT_BINARY_DIR}/package-dry-run/stdout.txt) && grep -q '\"dry_run\": true' ${CMAKE_CURRENT_BINARY_DIR}/package-dry-run/stdout.txt && test ! -e ${CMAKE_CURRENT_BINARY_DIR}/package-dry-run/out/cases/klee_add_zero.cfg"
    )
    add_test(
        NAME klee_campaign_runner_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-klee-campaign.py --dry-run --run-id fixture --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner --check --passes instcombine,simplifycfg --require-observed-probes --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh --klee-arg=--max-time=10s > ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/stdout.txt && grep -q 'klee-shell.sh bash -lc' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/commands.log && grep -q 'cv-ktest-extract.py --dump-dir' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/commands.log && grep -q 'opt-check-cases.sh --require-observed-probes' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/commands.log && grep -q 'cv-summarize-klee-campaign.py --cases-manifest' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/commands.log && grep -q 'coverage_summary:' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/stdout.txt && grep -q '\"dry_run\": true' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/summary.json && grep -q '\"check_status\": \"dry-run\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-runner/summary.json"
    )
    add_test(
        NAME klee_campaign_legacy_run_id_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-legacy-run-id && COMPILERVERIF_RUN_ID=legacy-env-run ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-klee-campaign.py --dry-run --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-legacy-run-id > ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-legacy-run-id/stdout.txt && grep -q '\"run_id\": \"legacy-env-run\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-legacy-run-id/summary.json && grep -q '\"dry_run\": true' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-legacy-run-id/summary.json"
    )
    add_test(
        NAME klee_campaign_runner_alive2_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-alive2-runner && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-klee-campaign.py --dry-run --run-id alive2 --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-alive2-runner --check --backfill-gaps --backfill-check --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-alive2-runner/stdout.txt && grep -q 'opt-check-cases.sh --alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-alive2-runner/commands.log && grep -q 'cv-backfill-coverage-gaps.py .*--alive2 --alive2-bin' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-alive2-runner/commands.log && grep -q '\"check_status\": \"dry-run\"' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-alive2-runner/summary.json"
    )
    add_test(
        NAME klee_campaign_runner_backfill_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-backfill-runner && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-klee-campaign.py --dry-run --run-id backfill --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-backfill-runner --backfill-gaps --backfill-check --check --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh > ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-backfill-runner/stdout.txt && grep -q 'cv-backfill-coverage-gaps.py --coverage-json' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-backfill-runner/commands.log && grep -q 'backfill/coverage-summary.json' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-backfill-runner/commands.log && grep -q 'backfill_summary:' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-backfill-runner/stdout.txt"
    )
    add_test(
        NAME klee_campaign_runner_package_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-package-runner && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-klee-campaign.py --dry-run --run-id package --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-package-runner --package-campaign --package-out ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-package-out > ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-package-runner/stdout.txt && grep -q 'cv-package-verification-campaign.py --klee-campaign' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-package-runner/commands.log && grep -q 'verification_campaign:' ${CMAKE_CURRENT_BINARY_DIR}/klee-campaign-package-runner/stdout.txt"
    )
    add_test(
        NAME klee_instcombine_wrapper_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/klee-wrapper-runner && ${CMAKE_CURRENT_SOURCE_DIR}/scripts/klee-run-instcombine.sh --dry-run --run-id wrapper --out-dir ${CMAKE_CURRENT_BINARY_DIR}/klee-wrapper-runner --reduce --klee-arg=--max-memory=1024 > ${CMAKE_CURRENT_BINARY_DIR}/klee-wrapper-runner/stdout.txt && grep -q 'commands:' ${CMAKE_CURRENT_BINARY_DIR}/klee-wrapper-runner/stdout.txt && grep -q -- '--reduce --reducer' ${CMAKE_CURRENT_BINARY_DIR}/klee-wrapper-runner/commands.log && grep -q -- '--max-memory=1024' ${CMAKE_CURRENT_BINARY_DIR}/klee-wrapper-runner/commands.log"
    )
    add_test(
        NAME llvm_tree_instrument_dry_run_fixture
        COMMAND
            /bin/sh -c
            "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-instrument-llvm-tree.py --dry-run --out-dir ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-dry-run ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp && grep -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-dry-run/instrumentation-manifest.jsonl && grep -q 'probe.simplifycfg.branch-chain' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-dry-run/instrumentation-manifest.jsonl && grep -q 'probe.mem2reg.promotable-alloca' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-dry-run/instrumentation-manifest.jsonl && grep -q 'probe.licm.invariant-op' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-dry-run/instrumentation-manifest.jsonl"
    )
    add_test(
        NAME llvm_tree_instrument_llm_dry_run_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":3,\"marker\":\"probe.instcombine.mul-one\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"matched_pattern\":\"synthetic m_One\",\"source\":\"synthetic m_One\",\"constraints\":{\"instruction.opcode\":\"mul\",\"rhs.value\":1},\"finding_source\":\"llm\",\"confidence\":0.88}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm/llm-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-instrument-llvm-tree.py --dry-run --out-dir ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm/out --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm/llm-findings.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm/out/instrumentation-candidates.json && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm/out/instrumentation-manifest.jsonl && grep -q 'probe.instcombine.mul-one' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm/out/instrumentation-manifest.jsonl"
    )
    add_test(
        NAME llvm_tree_instrument_llm_dedup_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm-dedup && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp\",\"line\":4,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"matched_pattern\":\"duplicate m_Zero\",\"source\":\"duplicate m_Zero\",\"constraints\":{\"instruction.opcode\":\"add\",\"rhs.value\":0},\"finding_source\":\"llm\",\"confidence\":0.91}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm-dedup/llm-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-instrument-llvm-tree.py --dry-run --out-dir ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm-dedup/out --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm-dedup/llm-findings.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp && test $(grep -c 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm-dedup/out/instrumentation-manifest.jsonl) -eq 1 && grep -q '\"finding_source\": \"static\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm-dedup/out/instrumentation-manifest.jsonl && ! grep -q 'duplicate m_Zero' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-llm-dedup/out/instrumentation-candidates.json"
    )
    add_test(
        NAME llvm_tree_instrument_missing_llm_fixture
        COMMAND
            /bin/sh -c
            "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-instrument-llvm-tree.py --dry-run --out-dir ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-missing-llm --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/no-such-llm-findings.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp 2> ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-missing-llm.err; test $? -eq 1) && grep -q 'LLM findings file does not exist' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-missing-llm.err"
    )
    add_test(
        NAME probe_demo_scalar
        COMMAND
            /bin/sh -c
            "$<TARGET_FILE:cv-probe-demo> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --require-observed | grep -q '^oracle_status=matched$'"
    )
    add_test(
        NAME probe_demo_cfg
        COMMAND
            /bin/sh -c
            "$<TARGET_FILE:cv-probe-demo> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/nested_diamond.cfg --require-observed | grep -q '^oracle_status=matched$'"
    )
    add_test(
        NAME probe_demo_memory
        COMMAND
            /bin/sh -c
            "$<TARGET_FILE:cv-probe-demo> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/promotable_alloca.cfg --require-observed | grep -q '^oracle_status=matched$'"
    )
    add_test(
        NAME probe_demo_loop
        COMMAND
            /bin/sh -c
            "$<TARGET_FILE:cv-probe-demo> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/counted_loop.cfg --require-observed | grep -q '^oracle_status=matched$'"
    )
    add_test(
        NAME probe_demo_negative
        COMMAND
            /bin/sh -c
            "$<TARGET_FILE:cv-probe-demo> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --drop-marker probe.instcombine.add-zero > ${CMAKE_CURRENT_BINARY_DIR}/probe-demo-negative.txt; test $? -eq 3 && grep -q '^oracle_status=mismatch$' ${CMAKE_CURRENT_BINARY_DIR}/probe-demo-negative.txt"
    )
    add_test(
        NAME intent_evidence_verified_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified && printf '%s\\n' '{\"marker\":\"probe.instcombine.add-zero\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"intent\":\"result-equivalence\",\"precondition\":\"add zero\",\"rewrite\":\"x\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/validated.jsonl && printf '%s\\n' '{\"case\":\"add_zero\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"oracle_status\":\"matched\",\"alive2_status\":\"proved\",\"expected_markers\":\"probe.instcombine.add-zero\",\"observed_markers\":\"probe.instcombine.add-zero\",\"before\":\"before.ll\",\"after\":\"after.ll\",\"probe_log\":\"probe.txt\",\"alive2_output\":\"alive2.txt\"}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-build-intent-evidence.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/validated.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/evidence.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/report.txt --require-clean && grep -q '\"evidence_status\": \"verified\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/evidence.jsonl && grep -q '^  verified: 1$' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-verified/report.txt"
    )
    add_test(
        NAME intent_evidence_uncovered_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered && printf '%s\\n' '{\"marker\":\"probe.instcombine.mul-one\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.instcombine.mul-one\",\"intent\":\"result-equivalence\",\"precondition\":\"mul one\",\"rewrite\":\"x\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/validated.jsonl && : > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/manifest.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-build-intent-evidence.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/validated.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/evidence.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/report.txt --require-clean 2> ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/stderr.txt; test $? -eq 1) && grep -q '\"evidence_status\": \"uncovered\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/evidence.jsonl && grep -q 'intent evidence issues: 1' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-uncovered/stderr.txt"
    )
    add_test(
        NAME intent_evidence_blocked_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked && printf '%s\\n' '{\"marker\":\"probe.instcombine.xor-self\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.instcombine.xor-self\",\"intent\":\"result-equivalence\",\"precondition\":\"xor self\",\"rewrite\":\"0\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/validated.jsonl && printf '%s\\n' '{\"case\":\"xor_self\",\"status\":\"failed\",\"message\":\"Alive2 check failed\",\"semantic_status\":\"matched\",\"oracle_status\":\"matched\",\"alive2_status\":\"failed\",\"expected_markers\":\"probe.instcombine.xor-self\",\"observed_markers\":\"probe.instcombine.xor-self\"}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/manifest.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-build-intent-evidence.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/validated.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/evidence.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/report.txt --require-clean 2> ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/stderr.txt; test $? -eq 1) && grep -q '\"evidence_status\": \"blocked\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/evidence.jsonl && grep -q '\"alive2_status\": {\"failed\": 1}' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-blocked/evidence.jsonl"
    )
    add_test(
        NAME intent_evidence_unsupported_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported && printf '%s\\n' '{\"marker\":\"probe.simplifycfg.diamond\",\"proof_status\":\"unsupported\",\"proof_result\":\"unsupported-marker\",\"promotion_status\":\"blocked\",\"confidence\":\"medium\",\"intent_candidate\":{\"marker\":\"probe.simplifycfg.diamond\",\"intent\":\"reachable-result-equivalence\",\"precondition\":\"diamond\",\"rewrite\":\"fold\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/validated.jsonl && printf '%s\\n' '{\"case\":\"diamond\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"oracle_status\":\"matched\",\"alive2_status\":\"unsupported\",\"expected_markers\":\"probe.simplifycfg.diamond\",\"observed_markers\":\"probe.simplifycfg.diamond\"}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-build-intent-evidence.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/validated.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/evidence.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/report.txt --require-clean && grep -q '\"evidence_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/evidence.jsonl && grep -q '^  unsupported: 1$' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-unsupported/report.txt"
    )
    add_test(
        NAME intent_evidence_graph_consistency_verified_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.vector.add-zero\",\"intent\":\"vector-result-equivalence\",\"precondition\":\"vector add zero\",\"rewrite\":\"lhs\"},\"evidence\":{\"source_intent_graph_lowering\":\"formal-ir\",\"formal_parameters\":{\"source_intent_graph.status\":\"complete\",\"source_intent_graph.consistency\":\"ok\",\"source_intent_graph.predicate_nodes\":2,\"source_intent_graph.rewrite_nodes\":1,\"source_intent_graph.bindings\":2,\"source_intent_graph.formal_symbols\":{\"X\":\"x\"}}}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/validated.jsonl && printf '%s\\n' '{\"case\":\"vector_add_zero\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"oracle_status\":\"matched\",\"alive2_status\":\"proved\",\"expected_markers\":\"probe.vector.add-zero\",\"observed_markers\":\"probe.vector.add-zero\"}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-build-intent-evidence.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/validated.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/evidence.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/report.txt --require-clean && grep -q '\"evidence_status\": \"verified\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/evidence.jsonl && grep -q '\"source_intent_graph_consistency\": \"ok\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/evidence.jsonl && grep -q '^  ok: 1$' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-ok/report.txt"
    )
    add_test(
        NAME intent_evidence_graph_consistency_blocked_fixture
        COMMAND
            /bin/sh -c
            "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.vector.add-zero\",\"intent\":\"vector-result-equivalence\",\"precondition\":\"vector add zero\",\"rewrite\":\"lhs\"},\"evidence\":{\"source_intent_graph_lowering\":\"formal-ir\",\"formal_parameters\":{\"source_intent_graph.status\":\"complete\",\"source_intent_graph.consistency\":\"failed\",\"source_intent_graph.consistency_errors\":[\"rewrite mismatch\"],\"source_intent_graph.predicate_nodes\":2,\"source_intent_graph.rewrite_nodes\":1,\"source_intent_graph.bindings\":2}}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/validated.jsonl && printf '%s\\n' '{\"case\":\"vector_add_zero\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"oracle_status\":\"matched\",\"alive2_status\":\"proved\",\"expected_markers\":\"probe.vector.add-zero\",\"observed_markers\":\"probe.vector.add-zero\"}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/manifest.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-build-intent-evidence.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/validated.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/evidence.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/report.txt --require-clean 2> ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/stderr.txt; test $? -eq 1) && grep -q '\"evidence_status\": \"blocked\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/evidence.jsonl && grep -q '\"source_intent_graph_consistency\": \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/evidence.jsonl && grep -q '^Graph-blocked evidence: 1$' ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-graph-failed/report.txt"
    )
    add_test(
        NAME intent_evidence_transaction_provenance_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/transaction_evidence_fixture.py
            --repo ${CMAKE_CURRENT_SOURCE_DIR}
            --work-dir ${CMAKE_CURRENT_BINARY_DIR}/intent-evidence-transaction
            --mode evidence
    )
    add_test(
        NAME intent_promotion_transaction_provenance_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/transaction_evidence_fixture.py
            --repo ${CMAKE_CURRENT_SOURCE_DIR}
            --work-dir ${CMAKE_CURRENT_BINARY_DIR}/intent-promotion-transaction
            --mode promotion
    )
    add_test(
        NAME intent_coverage_transaction_reduction_provenance_fixture
        COMMAND
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/transaction_evidence_fixture.py
            --repo ${CMAKE_CURRENT_SOURCE_DIR}
            --work-dir ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-reduction
            --mode audit
    )
    if(Z3_EXECUTABLE)
        add_test(
            NAME globalopt_coverage_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/globalopt_coverage_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
                --work-dir ${CMAKE_CURRENT_BINARY_DIR}/globalopt-coverage-fixture
                --z3 ${Z3_EXECUTABLE}
        )
        add_test(
            NAME globalopt_strict_check_fixture
            COMMAND
                ${CMAKE_CURRENT_SOURCE_DIR}/scripts/globalopt-strict-check.sh
                --out ${CMAKE_CURRENT_BINARY_DIR}/globalopt-strict-check
                --z3 ${Z3_EXECUTABLE}
        )
        # ~780s isolated; raised so heavy `ctest -j` parallel contention does not trip a false timeout.
        set_tests_properties(globalopt_strict_check_fixture PROPERTIES TIMEOUT 2400)
        add_test(
            NAME campaign_driver_intent_validate_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-validate --validate-intents --require-intent-proof --intent-emit-smt --z3 ${Z3_EXECUTABLE} --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-validate/intent-validated.jsonl && test -d ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-validate/intent-smt && grep -q '^  ready: 5$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-validate/intent-summary.txt && grep -q '^  blocked: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-validate/intent-summary.txt && grep -q 'cv-validate-intent-candidates.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-validate/commands.log"
        )
        add_test(
            NAME campaign_driver_intent_coverage_audit_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-coverage --validate-intents --audit-intent-coverage --z3 ${Z3_EXECUTABLE} --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-coverage/intent-coverage.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-coverage/intent-coverage.txt && grep -q 'cv-audit-intent-coverage.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-coverage/commands.log && grep -q '^O2T Intent Coverage Audit$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-coverage/intent-coverage.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data[\"summary\"][\"records\"] == 6; assert data[\"summary\"][\"missing_registry_markers\"] > 0; assert data[\"summary\"][\"recommendation\"][\"covered by source-derived formal IR\"] == 6; assert \"model side conditions\" not in data[\"summary\"][\"recommendation\"]' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-coverage/intent-coverage.json"
        )
        add_test(
            NAME campaign_driver_intent_evidence_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence --validate-intents --emit-intent-evidence --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --z3 ${Z3_EXECUTABLE} --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence/intent-evidence.jsonl && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence/intent-evidence-summary.txt && grep -q 'cv-build-intent-evidence.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence/commands.log && grep -q '\"evidence_status\": \"blocked\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence/intent-evidence.jsonl && grep -q '^  blocked: 6$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence/intent-evidence-summary.txt"
        )
        add_test(
            NAME campaign_driver_intent_evidence_required_failure_fixture
            COMMAND
                /bin/sh -c
                "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-fail --require-intent-evidence --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-fail.sh --z3 ${Z3_EXECUTABLE} --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp 2> ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-fail.err; test $? -eq 1) && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-fail/intent-evidence.jsonl && grep -q '\"evidence_status\": \"blocked\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-fail/intent-evidence.jsonl && grep -q 'intent evidence issues:' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-fail.err"
        )
        add_test(
            NAME campaign_driver_intent_proof_required_failure_fixture
            COMMAND
                /bin/sh -c
                "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-bad --validate-intents --require-intent-proof --intent-inferer ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-bad-intent-inferer.py --z3 ${Z3_EXECUTABLE} --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/campaign_snippet.cpp 2> ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-bad.err; test $? -eq 1) && grep -q 'intent proof issues: 1' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-bad.err && grep -q '\"proof_status\": \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-bad/intent-validated.jsonl && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-bad/intent-summary.txt"
        )
        add_test(
            NAME intent_promotion_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-candidates.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/promotion-candidates.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-validated.jsonl && printf '%s\\n' '[]' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-empty-current.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-validated.jsonl --current ${CMAKE_CURRENT_BINARY_DIR}/promotion-empty-current.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-proposed.json --report ${CMAKE_CURRENT_BINARY_DIR}/promotion-report.txt --require-ready && grep -q '^  added: 4$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-report.txt && grep -q '^  skipped-duplicate: 1$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-report.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); assert len(data) == 4; assert all(\"evidence\" in r for r in data); assert all(\"formal\" in r for r in data)' ${CMAKE_CURRENT_BINARY_DIR}/promotion-proposed.json"
        )
        add_test(
            NAME intent_promotion_verified_evidence_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence && printf '%s\\n' '{\"marker\":\"probe.instcombine.add-zero\",\"promotion_status\":\"ready\",\"proof_status\":\"proved\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"precondition\":\"rhs == 0\",\"rewrite\":\"x\",\"intent\":\"semantic-equivalence\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/validated.jsonl && printf '%s\\n' '{\"marker\":\"probe.instcombine.add-zero\",\"evidence_status\":\"verified\",\"replay_cases\":2,\"replay_status\":{\"passed\":2},\"semantic_status\":{\"matched\":2},\"oracle_status\":{\"matched\":2},\"alive2_status\":{\"verified\":2}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/evidence.jsonl && printf '%s\\n' '[]' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/current.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/validated.jsonl --evidence ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/evidence.jsonl --current ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/current.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/proposed.json --report ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/report.txt --require-ready --require-verified-evidence && grep -q '^  added: 1$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/report.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data[0][\"evidence\"][\"evidence_status\"] == \"verified\"; assert data[0][\"evidence\"][\"replay_cases\"] == 2; assert data[0][\"evidence\"][\"alive2_status\"][\"verified\"] == 2' ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence/proposed.json"
        )
        add_test(
            NAME intent_promotion_blocked_evidence_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked && printf '%s\\n' '{\"marker\":\"probe.instcombine.add-zero\",\"promotion_status\":\"ready\",\"proof_status\":\"proved\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"precondition\":\"rhs == 0\",\"rewrite\":\"x\",\"intent\":\"semantic-equivalence\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/validated.jsonl && printf '%s\\n' '{\"marker\":\"probe.instcombine.add-zero\",\"evidence_status\":\"blocked\",\"replay_cases\":1,\"replay_status\":{\"failed\":1}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/evidence.jsonl && printf '%s\\n' '[]' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/current.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/validated.jsonl --evidence ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/evidence.jsonl --current ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/current.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/proposed.json --report ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/report.txt && grep -q '^  evidence-blocked: 1$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/report.txt && ${Python3_EXECUTABLE} -c 'import json,sys; assert json.load(open(sys.argv[1])) == []' ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/proposed.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/validated.jsonl --evidence ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/evidence.jsonl --current ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/current.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/required.json --require-verified-evidence 2> ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/required.err; test $? -eq 1) && grep -q 'no promotion-ready intent candidates with verified evidence' ${CMAKE_CURRENT_BINARY_DIR}/promotion-evidence-blocked/required.err"
        )
        add_test(
            NAME intent_promotion_graph_provenance_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"promotion_status\":\"ready\",\"proof_status\":\"proved\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.vector.add-zero\",\"precondition\":\"vector add zero\",\"rewrite\":\"lhs\",\"intent\":\"vector-result-equivalence\"},\"evidence\":{\"formal_parameters\":{\"source_intent_graph.status\":\"complete\",\"source_intent_graph.consistency\":\"ok\",\"source_intent_graph.predicate_nodes\":2,\"source_intent_graph.rewrite_nodes\":1,\"source_intent_graph.bindings\":2,\"source_intent_graph.formal_symbols\":{\"X\":\"x\"}},\"source_intent_graph_lowering\":\"formal-ir\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/validated.jsonl && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"evidence_status\":\"verified\",\"replay_cases\":1,\"replay_status\":{\"passed\":1},\"semantic_status\":{\"matched\":1},\"oracle_status\":{\"matched\":1},\"alive2_status\":{\"proved\":1},\"source_intent_graph\":{\"status\":\"complete\",\"lowering\":\"formal-ir\",\"consistency\":\"ok\",\"predicate_nodes\":2,\"rewrite_nodes\":1,\"bindings\":2,\"formal_symbols\":{\"X\":\"x\"}},\"source_intent_graph_status\":\"complete\",\"source_intent_graph_lowering\":\"formal-ir\",\"source_intent_graph_consistency\":\"ok\",\"source_intent_graph_predicate_nodes\":2,\"source_intent_graph_rewrite_nodes\":1,\"source_intent_graph_bindings\":2,\"source_intent_graph_formal_symbols\":{\"X\":\"x\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/evidence.jsonl && printf '%s\\n' '[]' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/current.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/validated.jsonl --evidence ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/evidence.jsonl --current ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/current.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/proposed.json --report ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/report.txt --require-ready --require-verified-evidence && grep -q '^  ok: 1$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/report.txt && ${Python3_EXECUTABLE} -c 'import json,sys; ev=json.load(open(sys.argv[1]))[0][\"evidence\"]; assert ev[\"source_intent_graph\"][\"consistency\"] == \"ok\"; assert ev[\"source_intent_graph_formal_symbols\"] == {\"X\":\"x\"}; assert ev[\"source_intent_graph_bindings\"] == 2' ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph/proposed.json"
        )
        add_test(
            NAME intent_promotion_graph_blocked_evidence_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"promotion_status\":\"ready\",\"proof_status\":\"proved\",\"confidence\":\"high\",\"intent_candidate\":{\"marker\":\"probe.vector.add-zero\",\"precondition\":\"vector add zero\",\"rewrite\":\"lhs\",\"intent\":\"vector-result-equivalence\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/validated.jsonl && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"evidence_status\":\"blocked\",\"replay_cases\":1,\"replay_status\":{\"passed\":1},\"source_intent_graph\":{\"status\":\"complete\",\"lowering\":\"formal-ir\",\"consistency\":\"failed\",\"consistency_errors\":[\"rewrite mismatch\"]},\"source_intent_graph_status\":\"complete\",\"source_intent_graph_lowering\":\"formal-ir\",\"source_intent_graph_consistency\":\"failed\",\"source_intent_graph_consistency_errors\":[\"rewrite mismatch\"]}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/evidence.jsonl && printf '%s\\n' '[]' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/current.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/validated.jsonl --evidence ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/evidence.jsonl --current ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/current.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/proposed.json --report ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/report.txt --require-verified-evidence 2> ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/required.err; test $? -eq 1) && grep -q 'no promotion-ready intent candidates with verified evidence' ${CMAKE_CURRENT_BINARY_DIR}/promotion-graph-blocked/required.err"
        )
        add_test(
            NAME intent_promotion_existing_replace_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-replace-candidates.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/promotion-replace-candidates.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-replace-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-replace-validated.jsonl --current ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-existing-proposed.json --report ${CMAKE_CURRENT_BINARY_DIR}/promotion-existing-report.txt && grep -q '^  already-present: 4$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-existing-report.txt && grep -q '^  skipped-duplicate: 1$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-existing-report.txt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-replace-validated.jsonl --current ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-replaced-proposed.json --report ${CMAKE_CURRENT_BINARY_DIR}/promotion-replaced-report.txt --replace-existing && grep -q '^  replaced: 4$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-replaced-report.txt && grep -q '^  skipped-duplicate: 1$' ${CMAKE_CURRENT_BINARY_DIR}/promotion-replaced-report.txt && grep -q 'return replaceInstUsesWith' ${CMAKE_CURRENT_BINARY_DIR}/promotion-replaced-proposed.json"
        )
        add_test(
            NAME intent_promotion_require_ready_fixture
            COMMAND
                /bin/sh -c
                "printf '%s\\n' '{\"marker\":\"probe.instcombine.add-zero\",\"promotion_status\":\"blocked\",\"proof_status\":\"unsupported\",\"confidence\":\"low\",\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"precondition\":\"x\",\"rewrite\":\"y\",\"intent\":\"semantic-equivalence\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/promotion-no-ready.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/promotion-no-ready.jsonl --current ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json --out ${CMAKE_CURRENT_BINARY_DIR}/promotion-no-ready-out.json --require-ready 2> ${CMAKE_CURRENT_BINARY_DIR}/promotion-no-ready.err; test $? -eq 1) && grep -q 'no promotion-ready intent candidates' ${CMAKE_CURRENT_BINARY_DIR}/promotion-no-ready.err"
        )
        add_test(
            NAME campaign_driver_intent_promotion_fixture
            COMMAND
                /bin/sh -c
                "shasum -a 256 ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json > ${CMAKE_CURRENT_BINARY_DIR}/intent-before.sha && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-promotion --validate-intents --promote-intents --z3 ${Z3_EXECUTABLE} --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && shasum -a 256 ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json > ${CMAKE_CURRENT_BINARY_DIR}/intent-after.sha && diff ${CMAKE_CURRENT_BINARY_DIR}/intent-before.sha ${CMAKE_CURRENT_BINARY_DIR}/intent-after.sha && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-promotion/proposed-optimization-intents.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-promotion/intent-promotion-report.txt && grep -q '^  already-present: 4$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-promotion/intent-promotion-report.txt && grep -q '^  skipped-duplicate: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-promotion/intent-promotion-report.txt && grep -q 'cv-promote-intent-candidates.py' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-promotion/commands.log"
        )
        add_test(
            NAME campaign_driver_intent_evidence_promotion_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-promotion --validate-intents --emit-intent-evidence --promote-intents --alive2 --alive2-bin ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-alive-tv-success.sh --z3 ${Z3_EXECUTABLE} --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-promotion/intent-evidence.jsonl && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-promotion/intent-promotion-report.txt && grep -q '^  evidence-blocked: 4$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-promotion/intent-promotion-report.txt && grep -q '^  skipped-duplicate: 1$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-promotion/intent-promotion-report.txt && grep -q -- '--evidence' ${CMAKE_CURRENT_BINARY_DIR}/campaign-intent-evidence-promotion/commands.log"
        )
        add_test(
            NAME intent_validation_proved_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/intent-validate-high.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-validate-high.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-validated-high.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/intent-smt && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 5; assert all(r[\"proof_status\"] == \"proved\" for r in records); assert all(r[\"promotion_status\"] == \"ready\" for r in records); assert all(r[\"smt_file\"] for r in records)' ${CMAKE_CURRENT_BINARY_DIR}/intent-validated-high.jsonl"
        )
        add_test(
            NAME slp_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"formal_parameters\"][\"transaction.kind\"] == \"slp-vectorize-binop\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_wide_lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_8lane_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-8lane-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-8lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-8lane-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_16lane_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-16lane-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-16lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-16lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r8=json.loads(open(sys.argv[1]).readline()); r16=json.loads(open(sys.argv[2]).readline()); assert r8[\"proof_status\"] == r16[\"proof_status\"] == \"proved\"; assert r8[\"promotion_status\"] == r16[\"promotion_status\"] == \"ready\"; assert r8[\"intent_candidate\"][\"formal\"][\"domain\"] == \"vector-bv32xN\"; assert r16[\"intent_candidate\"][\"formal\"][\"domain\"] == \"vector-bv32xN\"; assert r8[\"intent_candidate\"][\"formal\"][\"vector_width\"] == 8; assert r16[\"intent_candidate\"][\"formal\"][\"vector_width\"] == 16; assert r8[\"evidence\"][\"formal_parameters\"][\"transaction.vector_width\"] == 8; assert r16[\"evidence\"][\"formal_parameters\"][\"transaction.vector_width\"] == 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-8lane-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-16lane-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_wide_reorder_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_8lane_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-8lane-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-8lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-8lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"vector-bv32xN\"; assert formal[\"vector_width\"] == 8; assert formal[\"after\"][\"op\"] == \"vshuffle\"; assert params[\"transaction.lane_mapping.map\"] == [4,0,6,2,7,3,5,1]; assert params[\"transaction.result_lane_mapping\"][\"map\"] == [4,0,6,2,7,3,5,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-8lane-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_32lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_32lane_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-32lane-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-32lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-32lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); formal=r[\"intent_candidate\"][\"formal\"]; params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"vector-bv32xN\"; assert formal[\"vector_width\"] == 32; assert params[\"transaction.vector_width\"] == 32; assert params[\"transaction.consistency\"] == \"ok\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-32lane-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_64lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_64lane_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-64lane-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-64lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-64lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); formal=r[\"intent_candidate\"][\"formal\"]; params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"vector-bv32xN\"; assert formal[\"vector_width\"] == 64; assert params[\"transaction.vector_width\"] == 64; assert params[\"transaction.consistency\"] == \"ok\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-64lane-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-vector-bv32\"; assert formal[\"base_lanes\"] == 4; assert [i[\"vscale\"] for i in r[\"proof_instances\"]] == [1,2,4]; assert params[\"transaction.scalable\"] is True; assert params[\"transaction.base_lanes\"] == 4; assert params[\"transaction.vscale_values\"] == [1,2,4]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_transaction_base_lanes_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_base2_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base2-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base2-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base2-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_base8_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base8-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base8-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base8-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; b2=json.loads(open(sys.argv[1]).readline()); b8=json.loads(open(sys.argv[2]).readline()); assert b2[\"proof_status\"] == b8[\"proof_status\"] == \"proved\"; assert b2[\"intent_candidate\"][\"formal\"][\"base_lanes\"] == 2; assert b8[\"intent_candidate\"][\"formal\"][\"base_lanes\"] == 8; assert [i[\"vscale\"] for i in b2[\"proof_instances\"]] == [1,2,4]; assert [i[\"vscale\"] for i in b8[\"proof_instances\"]] == [1,2,4]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base2-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-base8-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_transaction_reorder_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_reorder_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-reorder-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-reorder-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-reorder-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-vector-bv32\"; assert formal[\"after\"][\"op\"] == \"svshuffle\"; assert params[\"transaction.scalable_lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-transaction-reorder-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_minmax_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_minmax_transaction_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"domain\"] == \"scalable-vector-bv32\"; assert formal[\"after\"][\"op\"] == \"svsmin\"; assert [i[\"vscale\"] for i in r[\"proof_instances\"]] == [1,2,4]; assert params[\"transaction.kind\"] == \"slp-vectorize-minmax\"; assert params[\"transaction.opcode\"] == \"smin\"; assert params[\"transaction.predicate\"] == \"slt\"; assert params[\"transaction.scalable\"] is True' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_minmax_transaction_reorder_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_minmax_transaction_reorder_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-reorder-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-reorder-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-reorder-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-vector-bv32\"; assert formal[\"after\"][\"op\"] == \"svshuffle\"; assert formal[\"after\"][\"args\"][0][\"op\"] == \"svumax\"; assert params[\"transaction.scalable_lane_mapping\"][\"map\"] == [2,0,3,1]; assert params[\"transaction.opcode\"] == \"umax\"; assert params[\"transaction.predicate\"] == \"ugt\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-transaction-reorder-validated.jsonl"
        )
        add_test(
            NAME slp_minmax_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.kind\"] == \"slp-vectorize-minmax\"; assert params[\"transaction.opcode\"] == \"smin\"; assert params[\"transaction.predicate\"] == \"slt\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_minmax_transaction_16lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_16lane_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-16lane-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-16lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-16lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"vector-bv32xN\"; assert formal[\"vector_width\"] == 16; assert params[\"transaction.kind\"] == \"slp-vectorize-minmax\"; assert params[\"transaction.vector_width\"] == 16; assert params[\"transaction.opcode\"] == \"smin\"; assert params[\"transaction.predicate\"] == \"slt\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-transaction-16lane-validated.jsonl"
        )
        add_test(
            NAME slp_reduction_transaction_intent_validation_fixture
            COMMAND
                ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
                --work-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-intent
                --z3 ${Z3_EXECUTABLE}
        )
        add_test(
            NAME slp_reduction_transaction_2lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_2lane_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-2lane-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-2lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-2lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 2; assert params[\"transaction.vector_width\"] == 2; assert params[\"transaction.reduction_lanes\"] == 2' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-2lane-validated.jsonl"
        )
        add_test(
            NAME slp_reduction_transaction_8lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_8lane_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-8lane-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-8lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-8lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 8; assert params[\"transaction.vector_width\"] == 8; assert params[\"transaction.reduction_lanes\"] == 8' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-8lane-validated.jsonl"
        )
        add_test(
            NAME slp_reduction_transaction_16lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_16lane_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-16lane-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-16lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-16lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 16; assert params[\"transaction.vector_width\"] == 16; assert params[\"transaction.reduction_lanes\"] == 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-16lane-validated.jsonl"
        )
        add_test(
            NAME slp_reduction_transaction_32lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_32lane_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-32lane-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-32lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-32lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 32; assert params[\"transaction.vector_width\"] == 32; assert params[\"transaction.reduction_lanes\"] == 32' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-32lane-validated.jsonl"
        )
        add_test(
            NAME slp_reduction_transaction_64lane_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_64lane_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-64lane-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-64lane-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-64lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 64; assert params[\"transaction.vector_width\"] == 64; assert params[\"transaction.reduction_lanes\"] == 64' ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-64lane-validated.jsonl"
        )
        add_test(
            NAME slp_minmax_reduction_16lane_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_reduction_16lane_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-16lane-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-16lane-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-16lane-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 16; assert formal[\"after\"][\"op\"] == \"vreduce_smin\"; assert params[\"transaction.vector_width\"] == 16; assert params[\"transaction.reduction_opcode\"] == \"smin\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-minmax-reduction-16lane-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-smt && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-scalar-bv32\"; assert len(r[\"proof_instances\"]) == 3; assert [i[\"vscale\"] for i in r[\"proof_instances\"]] == [1,2,4]; assert params[\"transaction.scalable\"] is True; assert params[\"transaction.base_lanes\"] == 4; assert params[\"transaction.vscale_values\"] == [1,2,4]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-transaction-validated.jsonl && grep -R -q 'formal-vscale-4' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-smt"
        )
        add_test(
            NAME slp_scalable_reduction_base_lanes_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_base2_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base2-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base2-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base2-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_base8_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base8-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base8-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base8-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; b2=json.loads(open(sys.argv[1]).readline()); b8=json.loads(open(sys.argv[2]).readline()); assert b2[\"proof_status\"] == b8[\"proof_status\"] == \"proved\"; assert b2[\"evidence\"][\"formal_parameters\"][\"transaction.base_lanes\"] == 2; assert b8[\"evidence\"][\"formal_parameters\"][\"transaction.base_lanes\"] == 8; assert [i[\"vscale\"] for i in b2[\"proof_instances\"]] == [1,2,4]; assert [i[\"vscale\"] for i in b8[\"proof_instances\"]] == [1,2,4]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base2-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-base8-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_reduction_permutation_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_permutation_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-smt && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"after\"][\"args\"][0][\"op\"] == \"svshuffle\"; assert formal[\"after\"][\"args\"][0][\"base_mask\"] == [2,0,3,1]; assert params[\"transaction.scalable_lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-transaction-validated.jsonl && grep -R -q 'formal-vscale-4' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-reduction-permutation-smt"
        )
        add_test(
            NAME slp_scalable_minmax_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_minmax_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-reduction-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-scalar-bv32\"; assert formal[\"after\"][\"op\"] == \"svreduce_smin\"; assert params[\"transaction.reduction_opcode\"] == \"smin\"; assert len(r[\"proof_instances\"]) == 3' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-minmax-reduction-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_scalable_widening_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-smt && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-scalar-bv32\"; assert formal[\"variable_bits\"] == {\"a\":16}; assert formal[\"before\"][\"args\"][0][\"op\"] == \"vzext\"; assert params[\"transaction.reduction_input_bits\"] == 16; assert params[\"transaction.reduction_accumulator_bits\"] == 32; assert params[\"transaction.reduction_result_bits\"] == 32; assert len(r[\"proof_instances\"]) == 3' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-transaction-validated.jsonl && grep -R -q '(_ BitVec 16)' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-smt && grep -R -q 'zero_extend 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-reduction-smt"
        )
        add_test(
            NAME slp_scalable_widening_alias_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_alias_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-smt && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; provenance=params[\"transaction.reduction_width_provenance\"]; kinds={p[\"kind\"] for p in provenance}; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.reduction_width_status\"] == \"complete\"; assert params[\"transaction.reduction_input_bits\"] == 16; assert params[\"transaction.reduction_accumulator_bits\"] == 32; assert params[\"transaction.reduction_result_bits\"] == 32; assert \"type-alias-width\" in kinds and \"extension-target-width\" in kinds' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-transaction-validated.jsonl && grep -R -q 'zero_extend 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-alias-reduction-smt"
        )
        add_test(
            NAME slp_scalable_widening_variants_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_sext_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-sext-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-sext-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-sext-reduction-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-sext-reduction-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_trunc_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-trunc-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-trunc-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-trunc-reduction-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-trunc-reduction-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_permutation_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-permutation-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-permutation-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-permutation-reduction-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; sext=json.loads(open(sys.argv[1]).readline()); trunc=json.loads(open(sys.argv[2]).readline()); perm=json.loads(open(sys.argv[3]).readline()); assert sext[\"proof_status\"] == trunc[\"proof_status\"] == perm[\"proof_status\"] == \"proved\"; assert sext[\"intent_candidate\"][\"formal\"][\"before\"][\"args\"][0][\"op\"] == \"vsext\"; assert trunc[\"evidence\"][\"formal_parameters\"][\"transaction.reduction_result_bits\"] == 16; assert trunc[\"intent_candidate\"][\"formal\"][\"before\"][\"op\"] == \"trunc\"; assert perm[\"intent_candidate\"][\"formal\"][\"after\"][\"args\"][0][\"args\"][0][\"op\"] == \"svshuffle\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-sext-reduction-transaction-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-trunc-reduction-transaction-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-permutation-reduction-transaction-validated.jsonl && grep -R -q 'sign_extend 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-sext-reduction-smt && grep -R -q 'extract 15 0' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-trunc-reduction-smt"
        )
        add_test(
            NAME slp_scalable_reduction_unsupported_intent_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_ambiguous_reduction_transaction_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-ambiguous-reduction-transaction-intent.json --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} -c 'import json,sys; wd=json.load(open(sys.argv[1]))[0]; assert \"formal\" not in wd[\"intent_candidate\"] and wd[\"evidence\"][\"transaction_lowering\"] == \"fallback\"; assert \"unsupported-scalable-widening-reduction\" in wd[\"evidence\"][\"formal_parameters\"][\"transaction.unsupported_reduction_reasons\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-widening-ambiguous-reduction-transaction-intent.json"
        )
        add_test(
            NAME slp_scalable_fp_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fmul_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fmul-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fmul-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fmul-reduction-transaction-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fmul-reduction-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_permutation_transaction_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-permutation-reduction-transaction-intent.json --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} -c 'import json,sys; add=json.loads(open(sys.argv[1]).readline()); mul=json.loads(open(sys.argv[2]).readline()); perm=json.load(open(sys.argv[3]))[0]; assert add[\"proof_status\"] == mul[\"proof_status\"] == \"proved\"; assert add[\"intent_candidate\"][\"formal\"][\"domain\"] == \"scalable-scalar-fp32\"; assert add[\"intent_candidate\"][\"formal\"][\"after\"][\"op\"] == \"svfpreduce_add\"; assert mul[\"intent_candidate\"][\"formal\"][\"after\"][\"op\"] == \"svfpreduce_mul\"; assert \"formal\" not in perm[\"intent_candidate\"]; assert perm[\"evidence\"][\"transaction_lowering\"] == \"fallback\"; assert \"unsupported-reduction-fp-permutation\" in perm[\"evidence\"][\"formal_parameters\"][\"transaction.unsupported_reduction_reasons\"]; assert add[\"evidence\"][\"formal_parameters\"][\"transaction.fp_semantics\"] == \"ordered-fp32\"; assert add[\"evidence\"][\"formal_parameters\"][\"transaction.fp_rounding\"] == \"rne\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-transaction-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fmul-reduction-transaction-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-permutation-reduction-transaction-intent.json && grep -R -q '(set-logic QF_FP)' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-smt && grep -R -q 'fp.add roundNearestTiesToEven' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-smt && grep -R -q 'fp.mul roundNearestTiesToEven' ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fmul-reduction-smt"
        )
        add_test(
            NAME slp_fp_reduction_relaxed_policy_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_permutation_reassoc_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-policy-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-policy-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-policy-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_permutation_reassoc_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-policy-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-policy-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-policy-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; fixed=json.loads(open(sys.argv[1]).readline()); scalable=json.loads(open(sys.argv[2]).readline()); assert fixed[\"proof_status\"] == scalable[\"proof_status\"] == \"proved\"; assert fixed[\"proof_result\"] == scalable[\"proof_result\"] == \"policy-contract\"; assert fixed[\"promotion_status\"] == scalable[\"promotion_status\"] == \"ready\"; assert fixed[\"evidence\"][\"transaction_lowering\"] == scalable[\"evidence\"][\"transaction_lowering\"] == \"relaxed-fp-policy\"; assert fixed[\"intent_candidate\"][\"relaxed_fp_policy\"][\"semantics\"] == \"relaxed-reassoc\"; assert scalable[\"intent_candidate\"][\"relaxed_fp_policy\"][\"scalable\"] is True' ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-policy-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-reduction-policy-validated.jsonl"
        )
        add_test(
            NAME slp_fp_reduction_general_policy_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_unordered_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-unordered-policy-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-unordered-policy-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-unordered-policy-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_fast_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-fast-policy-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-fast-policy-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-fast-policy-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; unordered=json.loads(open(sys.argv[1]).readline()); fast=json.loads(open(sys.argv[2]).readline()); assert unordered[\"proof_status\"] == fast[\"proof_status\"] == \"proved\"; assert unordered[\"proof_result\"] == fast[\"proof_result\"] == \"policy-contract\"; assert unordered[\"promotion_status\"] == fast[\"promotion_status\"] == \"ready\"; assert unordered[\"evidence\"][\"transaction_lowering\"] == fast[\"evidence\"][\"transaction_lowering\"] == \"relaxed-fp-policy\"; assert unordered[\"intent_candidate\"][\"relaxed_fp_policy\"][\"semantics\"] == \"unordered-fp-reduction\"; assert fast[\"intent_candidate\"][\"relaxed_fp_policy\"][\"semantics\"] == \"fast-math-fp-reduction\"; assert fast[\"intent_candidate\"][\"relaxed_fp_policy\"][\"scalable\"] is True' ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-unordered-policy-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-scalable-fp-fast-policy-validated.jsonl"
        )
        add_test(
            NAME slp_reduction_transaction_consistency_blocks_validation_fixture
            COMMAND
                ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_consistency_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
                --work-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-reduction-transaction-consistency
                --z3 ${Z3_EXECUTABLE}
        )
        add_test(
            NAME slp_fp_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"domain\"] == \"scalar-fp32\"; assert formal[\"after\"][\"op\"] == \"fpreduce_add\"; assert params[\"transaction.reduction_opcode\"] == \"fadd\"; assert params[\"transaction.fp_semantics\"] == \"ordered-fp32\"; assert params[\"transaction.fp_rounding\"] == \"rne\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_fmul_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fmul_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fmul-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-fmul-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fmul-reduction-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalar-fp32\"; assert formal[\"after\"][\"op\"] == \"fpreduce_mul\"; assert params[\"transaction.reduction_opcode\"] == \"fmul\"; assert params[\"transaction.fp_semantics\"] == \"ordered-fp32\"; assert params[\"transaction.fp_rounding\"] == \"rne\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-fmul-reduction-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_fp_reduction_8lane_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_8lane_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-8lane-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-8lane-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-8lane-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalar-fp32\"; assert formal[\"vector_width\"] == 8; assert formal[\"after\"][\"op\"] == \"fpreduce_add\"; assert params[\"transaction.vector_width\"] == 8; assert params[\"transaction.reduction_lanes\"] == 8; assert params[\"transaction.fp_semantics\"] == \"ordered-fp32\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-fp-reduction-8lane-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_widening_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.reduction_input_bits\"] == 16; assert params[\"transaction.reduction_accumulator_bits\"] == 32; assert params[\"transaction.reduction_result_bits\"] == 32' ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_widening_reduction_8lane_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_reduction_8lane_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-8lane-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-8lane-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-8lane-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 8; assert params[\"transaction.reduction_input_bits\"] == 16; assert params[\"transaction.reduction_accumulator_bits\"] == 32; assert params[\"transaction.reduction_result_bits\"] == 32; assert params[\"transaction.reduction_lanes\"] == 8' ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-reduction-8lane-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_widening_trunc_reduction_transaction_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_trunc_reduction_transaction_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-trunc-reduction-transaction-intent.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-trunc-reduction-transaction-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-trunc-reduction-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.reduction_result_bits\"] == 16' ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-trunc-reduction-transaction-validated.jsonl"
        )
        add_test(
            NAME slp_widening_ambiguous_reduction_transaction_intent_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_ambiguous_reduction_transaction_snippet.cpp --format json --out ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-ambiguous-reduction-transaction-intent.json --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} -c 'import json,sys; r=[x for x in json.load(open(sys.argv[1])) if x.get(\"evidence\",{}).get(\"optimization_transaction\")][0]; params=r[\"evidence\"][\"formal_parameters\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert r[\"evidence\"][\"transaction_lowering\"] == \"fallback\"; assert params[\"transaction.reduction_width_status\"] == \"ambiguous\"; assert \"unsupported-reduction-ambiguous-width\" in params[\"transaction.consistency_errors\"]' ${CMAKE_CURRENT_BINARY_DIR}/slp-widening-ambiguous-reduction-transaction-intent.json"
        )
        add_test(
            NAME slp_transaction_summary_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_split_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.consistency\"] == \"ok\"; assert \"replaceExternalUses\" in params[\"transaction.functions\"]; assert len(params[\"transaction.role_provenance\"]) >= 5' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-summary-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_consistency_validation_blocked_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_mismatch_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"proof_status\"] == \"unsupported\"; assert r[\"promotion_status\"] == \"blocked\"; assert r[\"proof_result\"] == \"unsupported-rewrite\"; assert r[\"evidence\"][\"formal_parameters\"][\"transaction.consistency\"] == \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-mismatch-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_lane_mapping_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.lane_mapping.map\"] == [2,0,3,1]; assert params[\"transaction.lane_mapping.inverse_map\"] == [1,3,0,2]; assert params[\"transaction.operand_lane_mappings\"][\"rhs\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-reorder-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_invalid_lane_mapping_validation_blocked_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_invalid_lane_map_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"proof_status\"] == \"unsupported\"; assert r[\"promotion_status\"] == \"blocked\"; assert r[\"evidence\"][\"formal_parameters\"][\"transaction.consistency\"] == \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-invalid-lane-map-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_operand_lane_mapping_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_operand_maps_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.lane_mapping.map\"] == [1,3,0,2]; assert params[\"transaction.operand_lane_mappings\"][\"lhs\"][\"source\"][\"kind\"] == \"explicit-lhs-lane-map\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-maps-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_operand_lane_mapping_mismatch_validation_blocked_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_operand_mismatch_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"proof_status\"] == \"unsupported\"; assert r[\"promotion_status\"] == \"blocked\"; assert any(item == \"operand-lane-map-mismatch\" for item in r[\"evidence\"][\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-operand-mismatch-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_helper_pack_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.operand_lane_mappings\"][\"lhs\"][\"pack_builder\"][\"function\"] == \"buildLHS\"' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-pack-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_helper_partial_validation_blocked_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_partial_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"proof_status\"] == \"unsupported\"; assert r[\"promotion_status\"] == \"blocked\"; assert any(item == \"incomplete-pack-builder\" for item in r[\"evidence\"][\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-partial-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_helper_mismatch_validation_blocked_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_mismatch_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"proof_status\"] == \"unsupported\"; assert r[\"promotion_status\"] == \"blocked\"; assert any(item == \"operand-lane-map-mismatch\" for item in r[\"evidence\"][\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-helper-mismatch-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_result_helper_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_helper_snippet.cpp --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.result_lane_mapping\"][\"map\"] == [2,0,3,1]; assert len(params[\"transaction.scalar_lane_pairs\"]) == 4' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-helper-validated.jsonl"
        )
        add_test(
            NAME slp_transaction_result_mismatch_validation_blocked_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_mismatch_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"proof_status\"] == \"unsupported\"; assert r[\"promotion_status\"] == \"blocked\"; assert any(item == \"unsupported-lane-pairing\" for item in r[\"evidence\"][\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-result-mismatch-validated.jsonl"
        )
        add_test(
            NAME intent_validation_unsupported_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-validate-all.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-validate-all.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-validated-all.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; guarded=[r for r in records if r[\"line\"] == 47][0]; low=[r for r in records if r[\"line\"] == 54][0]; assert guarded[\"proof_status\"] == \"proved\" and guarded[\"proof_result\"] == \"unsat\"; assert guarded[\"promotion_status\"] == \"ready\"; assert guarded[\"modeled_side_conditions\"][0][\"kind\"] == \"no-poison-generating-flags\"; assert low[\"proof_status\"] == \"proved\" and low[\"proof_result\"] == \"unsat\"; assert low[\"promotion_status\"] == \"blocked\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-validated-all.jsonl"
        )
        add_test(
            NAME intent_validation_poison_side_condition_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/poison_side_condition_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-candidates.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-candidates.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-smt && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; modeled=[r for r in records if r[\"line\"] == 27][0]; guaranteed=[r for r in records if r[\"line\"] == 34][0]; one_use=[r for r in records if r[\"line\"] == 41][0]; nonzero=[r for r in records if r[\"line\"] == 48][0]; positive=[r for r in records if r[\"line\"] == 55][0]; nonnegative=[r for r in records if r[\"line\"] == 62][0]; masked=[r for r in records if r[\"line\"] == 69][0]; power=[r for r in records if r[\"line\"] == 76][0]; assert len(records) == 8; assert modeled[\"proof_status\"] == \"proved\" and modeled[\"promotion_status\"] == \"ready\"; assert modeled[\"side_conditions\"] == []; assert modeled[\"modeled_side_conditions\"][0][\"kind\"] == \"no-poison-generating-flags\"; assert modeled[\"modeled_side_conditions\"][0][\"formal_effect\"] == \"semantic-side-condition\"; assert modeled[\"evidence\"][\"side_condition_lowering\"] == \"modeled\"; assert guaranteed[\"proof_status\"] == \"proved\" and guaranteed[\"promotion_status\"] == \"ready\"; assert guaranteed[\"side_conditions\"] == []; assert guaranteed[\"modeled_side_conditions\"][0][\"kind\"] == \"guaranteed-not-poison\"; assert guaranteed[\"modeled_side_conditions\"][0][\"formal_effect\"] == \"not-poison-assumption\"; assert guaranteed[\"intent_candidate\"][\"formal\"][\"assumptions\"] == [{\"op\":\"not-poison\",\"name\":\"a\"}]; assert one_use[\"proof_status\"] == \"proved\" and one_use[\"promotion_status\"] == \"ready\"; assert one_use[\"side_conditions\"] == []; assert one_use[\"modeled_side_conditions\"][0][\"kind\"] == \"one-use\"; assert one_use[\"modeled_side_conditions\"][0][\"formal_effect\"] == \"structural-only\"; assert one_use[\"evidence\"][\"formal_parameters\"][\"side_conditions.structural_preconditions\"][0][\"subject\"] == \"Op0\"; assert nonzero[\"proof_status\"] == \"proved\" and nonzero[\"promotion_status\"] == \"ready\"; assert nonzero[\"side_conditions\"] == []; assert nonzero[\"modeled_side_conditions\"][0][\"kind\"] == \"known-nonzero\"; assert nonzero[\"modeled_side_conditions\"][0][\"formal_effect\"] == \"not-eq-zero-assumption\"; assert nonzero[\"intent_candidate\"][\"formal\"][\"assumptions\"] == [{\"op\":\"not-eq\",\"name\":\"a\",\"value\":0}]; assert positive[\"modeled_side_conditions\"][0][\"kind\"] == \"known-positive\"; assert positive[\"intent_candidate\"][\"formal\"][\"assumptions\"] == [{\"op\":\"cmp\",\"predicate\":\"sgt\",\"name\":\"a\",\"value\":0}]; assert nonnegative[\"modeled_side_conditions\"][0][\"kind\"] == \"known-nonnegative\"; assert nonnegative[\"intent_candidate\"][\"formal\"][\"assumptions\"] == [{\"op\":\"cmp\",\"predicate\":\"sge\",\"name\":\"a\",\"value\":0}]; assert masked[\"modeled_side_conditions\"][0][\"kind\"] == \"masked-value-is-zero\"; assert masked[\"intent_candidate\"][\"formal\"][\"assumptions\"] == [{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":255}]; assert power[\"modeled_side_conditions\"][0][\"kind\"] == \"known-power-of-two\"; assert power[\"intent_candidate\"][\"formal\"][\"assumptions\"] == [{\"op\":\"power-of-two\",\"name\":\"a\",\"nonzero\":True}]' ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-validated.jsonl && grep -R -q '(not a_poison)' ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-smt && grep -R -q '(not (= a #x00000000))' ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-smt && grep -R -q '(bvsgt a #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-smt && grep -R -q '(bvsge a #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-smt && grep -R -q '(= (bvand a #x000000ff) #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-smt && grep -R -q '(= (bvand a (bvsub a #x00000001)) #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/intent-poison-side-smt"
        )
        add_test(
            NAME intent_guard_effect_lowering_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} -c 'import importlib.machinery,pathlib,sys; sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent)); mod=importlib.machinery.SourceFileLoader(\"infer\", sys.argv[1]).load_module(); formal={}; params={}; guards=[{\"kind\":\"known-nonzero\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"none\",\"subject\":\"X\"},{\"kind\":\"guaranteed-not-poison\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"not-poison-assumption\",\"formal_effect_args\":{\"assumption\":{\"op\":\"not-poison\",\"requires_poison_variable\":True}},\"proof_effect\":\"assumes-subject-not-poison\",\"subject\":\"X\"},{\"kind\":\"one-use\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"structural-only\",\"formal_effect_args\":{\"structural\":True},\"proof_effect\":\"catalog-structural\",\"subject\":\"X\"},{\"kind\":\"no-poison-generating-flags\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"semantic-side-condition\",\"subject\":\"X\"}]; assert mod.lower_guard_effects(formal, params, guards, {\"X\":\"x\"}.get, \"source_intent\"); assert formal[\"assumptions\"] == [{\"op\":\"not-poison\",\"name\":\"x\"}]; assert formal[\"poison_variables\"] == [\"x\"]; assert params[\"source_intent.structural_preconditions\"] == [{\"kind\":\"one-use\",\"subject\":\"X\",\"proof_effect\":\"catalog-structural\"}]; formal2={}; params2={}; assert mod.lower_guard_effects(formal2, params2, [{\"kind\":\"known-nonzero\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"not-eq-zero-assumption\",\"formal_effect_args\":{\"assumption\":{\"op\":\"not-eq\",\"value\":0}},\"subject\":\"X\"}], {\"X\":\"x\"}.get, \"source_intent\"); assert formal2[\"assumptions\"] == [{\"op\":\"not-eq\",\"name\":\"x\",\"value\":0}]; formal3={}; params3={}; assert mod.apply_modeled_side_condition_semantics(formal3, params3, [{\"kind\":\"known-nonzero\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"none\",\"subject\":\"Op0\"}], \"probe.instcombine.add-zero\", \"return replaceInstUsesWith(I, Op0);\"); assert \"assumptions\" not in formal3; assert params3 == {}; formal4={}; params4={}; assert not mod.lower_guard_effects(formal4, params4, [{\"kind\":\"known-nonzero\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"not-eq-zero-assumption\",\"subject\":\"X\"}], {\"X\":\"x\"}.get, \"source_intent\"); formal5={}; params5={}; assert mod.apply_source_intent_guard_semantics(formal5, params5, {\"guards\":[{\"kind\":\"known-nonzero\",\"role\":\"modeled-side-condition\",\"formal_effect\":\"none\",\"subject\":\"X\"}]}, {\"X\":\"x\"}); assert formal5[\"assumptions\"] == [{\"op\":\"not-eq\",\"name\":\"x\",\"value\":0}]' ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py"
        )
        add_test(
            NAME assumption_algebra_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} -c 'import sys; sys.path.insert(0, sys.argv[1]); from cv_assumption_algebra import normalize_assumptions; r=normalize_assumptions([{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":1},{\"op\":\"known-bits\",\"name\":\"a\",\"one_mask\":2},{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":1}]); assert r[\"assumptions\"] == [{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":1,\"one_mask\":2}]; assert {\"op\":\"not-eq\",\"name\":\"a\",\"value\":0,\"reason\":\"known-one-bits\"} in r[\"derived\"]; assert r[\"contradictions\"] == []; p=normalize_assumptions([{\"op\":\"power-of-two\",\"name\":\"x\",\"nonzero\":True}]); assert p[\"derived\"] == [{\"op\":\"not-eq\",\"name\":\"x\",\"value\":0,\"reason\":\"power-of-two-nonzero\"}]; s=normalize_assumptions([{\"op\":\"cmp\",\"predicate\":\"sgt\",\"name\":\"x\",\"value\":0}]); assert {\"op\":\"cmp\",\"predicate\":\"sge\",\"name\":\"x\",\"value\":0,\"reason\":\"sgt-zero\"} in s[\"derived\"]; assert {\"op\":\"not-eq\",\"name\":\"x\",\"value\":0,\"reason\":\"sgt-zero\"} in s[\"derived\"]; c=normalize_assumptions([{\"op\":\"not-eq\",\"name\":\"z\",\"value\":0},{\"op\":\"known-bits\",\"name\":\"z\",\"zero_mask\":4294967295}]); assert c[\"contradictions\"] == [\"z: known all-zero bits conflict with not-eq zero\"]' ${CMAKE_CURRENT_SOURCE_DIR}/tools"
        )
        add_test(
            NAME intent_coverage_audit_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit && printf '%s\\n' '{\"file\":\"synthetic.cpp\",\"line\":1,\"marker\":\"probe.vector.add-zero\",\"confidence\":\"high\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"modeled_side_conditions\":[{\"kind\":\"guaranteed-not-poison\"}],\"intent_candidate\":{\"marker\":\"probe.vector.add-zero\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\"}},\"evidence\":{\"formal_inference\":\"source-derived-vector\",\"semantic_lowering\":\"formal-ir\"}}' '{\"file\":\"synthetic.cpp\",\"line\":2,\"marker\":\"probe.simplifycfg.diamond\",\"confidence\":\"medium\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"blocked\",\"modeled_side_conditions\":[{\"kind\":\"one-use\"}],\"profitability_guards\":[{\"kind\":\"profitability\"}],\"intent_candidate\":{\"marker\":\"probe.simplifycfg.diamond\",\"intent\":\"reachable-result-equivalence\",\"formal\":{\"domain\":\"cfg-bv32\"}},\"evidence\":{\"formal_inference\":\"registry-fallback\"}}' '{\"file\":\"synthetic.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\",\"confidence\":\"high\",\"proof_status\":\"unsupported\",\"proof_result\":\"unsupported-side-conditions\",\"promotion_status\":\"blocked\",\"side_conditions\":[{\"kind\":\"unknown\",\"source\":\"requires nsw\",\"line\":3}],\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"intent\":\"result-equivalence\"}}' '{\"file\":\"synthetic.cpp\",\"line\":4,\"marker\":\"probe.vector.shuffle-splat\",\"confidence\":\"high\",\"proof_status\":\"unsupported\",\"proof_result\":\"unsupported-formal-ir\",\"promotion_status\":\"blocked\",\"intent_candidate\":{\"marker\":\"probe.vector.shuffle-splat\",\"intent\":\"vector-result-equivalence\"},\"evidence\":{\"semantic_lowering\":\"unsupported\",\"formal_parameters\":{\"semantic.unsupported_reason\":\"missing fields\"}}}' '{\"file\":\"synthetic.cpp\",\"line\":5,\"marker\":\"probe.unknown\",\"confidence\":\"high\",\"proof_status\":\"unsupported\",\"proof_result\":\"unsupported-marker\",\"promotion_status\":\"blocked\",\"intent_candidate\":{\"marker\":\"probe.unknown\",\"intent\":\"semantic-equivalence\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-intent-coverage.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.json --report ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q '^O2T Intent Coverage Audit$' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q '^Guard handling$' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q '^Guard recognizers$' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q 'text: guaranteed-not-poison, known-nonequal, known-nonnegative, known-nonzero, known-positive, known-power-of-two, masked-value-is-zero, no-poison-generating-flags, one-use' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q 'ast: guaranteed-not-poison, known-nonequal, known-nonnegative, known-nonzero, known-positive, known-power-of-two, masked-value-is-zero, one-use' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q '^Top unsupported guards$' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q 'unsupported: unknown=1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q 'unknown count=1 first=synthetic.cpp:3 marker=probe.instcombine.add-zero source=requires nsw' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q 'recommendation=covered by source-derived formal IR' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && grep -q 'probe.vector.scalable.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); summary=data[\"summary\"]; assert summary[\"records\"] == 5; assert summary[\"missing_registry_markers\"] == 47; assert summary[\"recommendation\"][\"covered by source-derived formal IR\"] == 1; assert summary[\"recommendation\"][\"improve source semantic lowering\"] == 2; assert summary[\"recommendation\"][\"model guard semantics: unknown\"] == 1; assert summary[\"recommendation\"][\"add registry formal block\"] == 1; assert summary[\"formal_inference\"][\"source-derived-vector\"] == 1; assert summary[\"formal_inference\"][\"registry-fallback\"] == 1; assert summary[\"guard_handling\"][\"modeled\"] == {\"guaranteed-not-poison\":1,\"one-use\":1}; assert summary[\"guard_handling\"][\"structural\"] == {\"one-use\":1}; assert summary[\"guard_handling\"][\"profitability\"] == {\"profitability\":1}; assert summary[\"guard_handling\"][\"unsupported\"] == {\"unknown\":1}; assert summary[\"unsupported_guard_records\"][0][\"kind\"] == \"unknown\"; assert summary[\"unsupported_guard_records\"][0][\"locations\"][0] == {\"file\":\"synthetic.cpp\",\"line\":3,\"marker\":\"probe.instcombine.add-zero\"}; assert data[\"guard_recognizers\"][\"text\"] == [\"guaranteed-not-poison\",\"known-nonequal\",\"known-nonnegative\",\"known-nonzero\",\"known-positive\",\"known-power-of-two\",\"masked-value-is-zero\",\"no-poison-generating-flags\",\"one-use\"]; assert data[\"guard_recognizers\"][\"ast\"] == [\"guaranteed-not-poison\",\"known-nonequal\",\"known-nonnegative\",\"known-nonzero\",\"known-positive\",\"known-power-of-two\",\"masked-value-is-zero\",\"one-use\"]; assert any(r[\"marker\"] == \"probe.unknown\" and not r[\"registry_covered\"] for r in data[\"records\"])' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-audit/audit.json"
        )
        add_test(
            NAME intent_coverage_transaction_audit_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction && printf '%s\\n' '{\"file\":\"synthetic-slp.cpp\",\"line\":10,\"marker\":\"probe.slp.vectorize-binop\",\"confidence\":\"high\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"intent_candidate\":{\"marker\":\"probe.slp.vectorize-binop\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\"}},\"evidence\":{\"formal_inference\":\"source-derived-transaction\",\"transaction_lowering\":\"formal-ir\",\"optimization_transaction\":{\"model\":\"optimization-transaction-v1\",\"kind\":\"slp-vectorize-binop\",\"opcode\":\"add\",\"lanes\":4,\"consistency\":\"ok\",\"consistency_errors\":[],\"lane_mapping\":{\"map\":[2,0,3,1]},\"result_lane_mapping\":{\"map\":[2,0,3,1]},\"scalar_lane_pairs\":[{\"vector_lane\":0},{\"vector_lane\":1},{\"vector_lane\":2},{\"vector_lane\":3}]},\"formal_parameters\":{\"transaction.kind\":\"slp-vectorize-binop\",\"transaction.opcode\":\"add\",\"transaction.lanes\":4,\"transaction.consistency\":\"ok\",\"transaction.consistency_errors\":[],\"transaction.lane_mapping\":{\"map\":[2,0,3,1]},\"transaction.result_lane_mapping\":{\"map\":[2,0,3,1]},\"transaction.scalar_lane_pairs\":[{\"vector_lane\":0},{\"vector_lane\":1},{\"vector_lane\":2},{\"vector_lane\":3}]}}}' '{\"file\":\"synthetic-slp.cpp\",\"line\":20,\"marker\":\"probe.slp.vectorize-binop\",\"confidence\":\"high\",\"proof_status\":\"unsupported\",\"proof_result\":\"unsupported-marker\",\"promotion_status\":\"blocked\",\"intent_candidate\":{\"marker\":\"probe.slp.vectorize-binop\",\"intent\":\"vector-result-equivalence\"},\"evidence\":{\"formal_inference\":\"source-derived-transaction\",\"transaction_lowering\":\"fallback\",\"optimization_transaction\":{\"model\":\"optimization-transaction-v1\",\"kind\":\"slp-vectorize-binop\",\"opcode\":\"add\",\"lanes\":4,\"consistency\":\"failed\",\"consistency_errors\":[\"unsupported-lane-pairing\"],\"lane_mapping\":{\"map\":[2,0,3,1]},\"result_lane_mapping\":{\"map\":[0,1,2,3]},\"scalar_lane_pairs\":[{\"vector_lane\":0},{\"vector_lane\":1},{\"vector_lane\":2},{\"vector_lane\":3}]},\"formal_parameters\":{\"transaction.kind\":\"slp-vectorize-binop\",\"transaction.opcode\":\"add\",\"transaction.lanes\":4,\"transaction.consistency\":\"failed\",\"transaction.consistency_errors\":[\"unsupported-lane-pairing\"],\"transaction.lane_mapping\":{\"map\":[2,0,3,1]},\"transaction.result_lane_mapping\":{\"map\":[0,1,2,3]},\"transaction.scalar_lane_pairs\":[{\"vector_lane\":0},{\"vector_lane\":1},{\"vector_lane\":2},{\"vector_lane\":3}]}}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-intent-coverage.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.json --report ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.txt && grep -q '^Optimization transactions$' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.txt && grep -q 'lowering: fallback=1, formal-ir=1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.txt && grep -q 'consistency_errors: unsupported-lane-pairing=1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.txt && grep -q 'recommendation=covered by source-derived transaction formal IR' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.txt && grep -q 'recommendation=fix transaction consistency: unsupported-lane-pairing' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); summary=data[\"summary\"]; tx=summary[\"optimization_transactions\"]; assert summary[\"recommendation\"] == {\"covered by source-derived transaction formal IR\":1,\"fix transaction consistency: unsupported-lane-pairing\":1}; assert tx[\"records\"] == 2; assert tx[\"formal_ir\"] == 1 and tx[\"fallback\"] == 1; assert tx[\"kind\"] == {\"slp-vectorize-binop\":2}; assert tx[\"opcode\"] == {\"add\":2}; assert tx[\"lanes\"] == {\"4\":2}; assert tx[\"consistency\"] == {\"failed\":1,\"ok\":1}; assert tx[\"consistency_errors\"] == {\"unsupported-lane-pairing\":1}; assert tx[\"with_lane_mapping\"] == 2; assert tx[\"with_result_lane_mapping\"] == 2; assert tx[\"scalar_lane_pairs\"] == 8; assert all(r[\"transaction_present\"] for r in data[\"records\"]); assert data[\"records\"][0][\"recommendation\"] == \"covered by source-derived transaction formal IR\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction/audit.json"
        )
        add_test(
            NAME intent_coverage_transaction_source_slice_audit_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice && printf '%s\\n' '{\"file\":\"synthetic-slp.cpp\",\"line\":10,\"marker\":\"probe.slp.vectorize-binop\",\"confidence\":\"high\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"intent_candidate\":{\"marker\":\"probe.slp.vectorize-binop\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\"}},\"evidence\":{\"formal_inference\":\"source-derived-transaction\",\"transaction_lowering\":\"formal-ir\",\"optimization_transaction\":{\"model\":\"optimization-transaction-v1\",\"kind\":\"slp-vectorize-binop\",\"opcode\":\"add\",\"lanes\":4,\"consistency\":\"ok\"},\"formal_parameters\":{\"transaction.kind\":\"slp-vectorize-binop\",\"transaction.opcode\":\"add\",\"transaction.lanes\":4,\"transaction.consistency\":\"ok\",\"transaction.source_slice.control_root_function\":\"vectorizeTree\",\"transaction.source_slice.completeness\":{\"has_candidate\":true,\"has_legality\":true,\"has_profitability\":true,\"has_emitter\":true,\"has_replacement\":true,\"has_lane_mapping\":true},\"transaction.source_slice.predicate_expansion_roles\":[\"candidate-tree\",\"legality\",\"profitability\"],\"transaction.source_slice.contract.status\":\"complete\",\"transaction.source_slice.contract.missing_roles\":[],\"transaction.source_slice.contract.role_paths\":[{\"role\":\"legality\",\"function\":\"isTreeLegal\",\"path\":[\"vectorizeTree\",\"isTreeLegal\"]}]}}}' '{\"file\":\"synthetic-slp.cpp\",\"line\":20,\"marker\":\"probe.slp.vectorize-binop\",\"confidence\":\"high\",\"proof_status\":\"unsupported\",\"proof_result\":\"unsupported-marker\",\"promotion_status\":\"blocked\",\"intent_candidate\":{\"marker\":\"probe.slp.vectorize-binop\",\"intent\":\"vector-result-equivalence\"},\"evidence\":{\"formal_inference\":\"source-derived-transaction\",\"transaction_lowering\":\"fallback\",\"optimization_transaction\":{\"model\":\"optimization-transaction-v1\",\"kind\":\"slp-vectorize-binop\",\"opcode\":\"add\",\"lanes\":4,\"consistency\":\"failed\",\"consistency_errors\":[\"missing-expanded-legality\",\"missing-contract-role:legality\"]},\"formal_parameters\":{\"transaction.kind\":\"slp-vectorize-binop\",\"transaction.opcode\":\"add\",\"transaction.lanes\":4,\"transaction.consistency\":\"failed\",\"transaction.consistency_errors\":[\"missing-expanded-legality\",\"missing-contract-role:legality\"],\"transaction.source_slice.control_root_function\":\"vectorizeTree\",\"transaction.source_slice.completeness\":{\"has_candidate\":true,\"has_legality\":false,\"has_profitability\":true,\"has_emitter\":true,\"has_replacement\":true,\"has_lane_mapping\":true},\"transaction.source_slice.predicate_expansion_roles\":[\"candidate-tree\",\"unknown-helper\",\"profitability\"],\"transaction.source_slice.contract.status\":\"failed\",\"transaction.source_slice.contract.missing_roles\":[\"legality\"],\"transaction.source_slice.contract.role_paths\":[{\"role\":\"profitability\",\"function\":\"isTreeProfitable\",\"path\":[\"vectorizeTree\",\"isTreeProfitable\"]}]}}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-intent-coverage.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.json --report ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'with_source_slice: 2' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'complete_source_slice: 1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'incomplete_source_slice: 1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'source_slice_missing: has_legality=1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'predicate_expansion_roles: candidate-tree=2, legality=1, profitability=2, unknown-helper=1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'with_source_slice_contract: 2' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'complete_source_slice_contract: 1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'incomplete_source_slice_contract: 1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'source_slice_contract_missing_roles: legality=1' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && grep -q 'recommendation=model source predicate helper semantics' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); summary=data[\"summary\"]; tx=summary[\"optimization_transactions\"]; assert summary[\"recommendation\"] == {\"covered by source-derived transaction formal IR\":1,\"model source predicate helper semantics\":1}; assert tx[\"with_source_slice\"] == 2; assert tx[\"complete_source_slice\"] == 1; assert tx[\"incomplete_source_slice\"] == 1; assert tx[\"source_slice_missing\"] == {\"has_legality\":1}; assert tx[\"predicate_expansion_roles\"] == {\"candidate-tree\":2,\"legality\":1,\"profitability\":2,\"unknown-helper\":1}; assert tx[\"with_source_slice_contract\"] == 2; assert tx[\"complete_source_slice_contract\"] == 1; assert tx[\"incomplete_source_slice_contract\"] == 1; assert tx[\"source_slice_contract_missing_roles\"] == {\"legality\":1}; assert data[\"records\"][1][\"transaction_source_slice_missing\"] == [\"has_legality\"]; assert data[\"records\"][1][\"transaction_source_slice_contract_missing_roles\"] == [\"legality\"]' ${CMAKE_CURRENT_BINARY_DIR}/intent-coverage-transaction-source-slice/audit.json"
        )
        add_test(
            NAME intent_assumption_algebra_diagnostics_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra && printf '%s\\n' '[{\"file\":\"tests/fixtures/source_intent_matcher_snippet.cpp\",\"line\":40,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"matched_pattern\":\"m_Add\",\"predicate_source\":\"if (match(V, m_Add(m_Value(X), m_Zero()))) {\",\"rewrite_source\":\"return replaceInstUsesWith(I, X);\",\"source_intent\":{\"model\":\"source-intent-v1\",\"before\":{\"shape\":\"scalar\",\"operation\":\"add\",\"operands\":[\"X\",\"0\"]},\"after\":{\"result\":\"X\"},\"rewrite\":{\"action\":\"replace-with-lhs\"},\"guards\":[{\"kind\":\"known-nonzero\",\"subject\":\"X\"},{\"kind\":\"masked-value-is-zero\",\"subject\":\"X\",\"zero_mask\":4294967295}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/out.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-intent-coverage.py --validated ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/out.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/audit.json --report ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/audit.txt && grep -q '\"proof_result\": \"unsupported-contradictory-assumptions\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/out.jsonl && grep -q 'assumption_algebra.contradictions' ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/out.jsonl && grep -q '^Assumption algebra$' ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/audit.txt && grep -q '^  contradictions: 1$' ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/audit.txt && grep -q 'recommendation=fix contradictory guard assumptions' ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/audit.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); summary=data[\"summary\"]; assert summary[\"recommendation\"] == {\"fix contradictory guard assumptions\":1}; assert summary[\"assumption_algebra\"][\"contradictions\"] == 1; assert summary[\"assumption_algebra\"][\"contradiction_records\"][0][\"messages\"] == [\"x: known all-zero bits conflict with not-eq zero\"]; r=data[\"records\"][0]; assert r[\"assumption_algebra_contradictions\"] == 1; assert r[\"proof_result\"] == \"unsupported-contradictory-assumptions\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-assumption-algebra/audit.json"
        )
        add_test(
            NAME source_intent_graph_audit_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit && printf '%s\\n' '{\"file\":\"synthetic.cpp\",\"line\":1,\"marker\":\"probe.instcombine.add-zero\",\"confidence\":\"high\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"promotion_status\":\"ready\",\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\"}},\"evidence\":{\"formal_inference\":\"source-derived-intent-graph\",\"formal_parameters\":{\"source_intent_graph.consistency\":\"ok\"},\"source_intent_graph_lowering\":\"formal-ir\",\"source_intent_graph\":{\"model\":\"source-intent-graph-v1\",\"status\":\"complete\",\"predicate_nodes\":[{\"kind\":\"condition\"},{\"kind\":\"call\"}],\"rewrite_nodes\":[{\"kind\":\"rewrite\"}],\"bindings\":[{\"source_symbol\":\"X\",\"role\":\"operand\"}]}}}' '{\"file\":\"synthetic.cpp\",\"line\":2,\"marker\":\"probe.instcombine.mul-one\",\"confidence\":\"high\",\"proof_status\":\"unsupported\",\"proof_result\":\"unsupported-formal-ir\",\"promotion_status\":\"blocked\",\"intent_candidate\":{\"marker\":\"probe.instcombine.mul-one\",\"intent\":\"result-equivalence\"},\"evidence\":{\"formal_inference\":\"unset\",\"formal_parameters\":{\"source_intent_graph.consistency\":\"failed\",\"source_intent_graph.consistency_errors\":[\"rewrite-replacement-mismatch\"]},\"source_intent_graph_lowering\":\"fallback\",\"source_intent_graph\":{\"model\":\"source-intent-graph-v1\",\"status\":\"incomplete\",\"predicate_nodes\":[{\"kind\":\"condition\"}],\"rewrite_nodes\":[],\"bindings\":[],\"unsupported_reasons\":[\"missing-rewrite\"]}}}' > ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-audit-intent-coverage.py --validated ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.json --report ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.txt && grep -q '^Source intent graph$' ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.txt && grep -q 'status: complete=1, incomplete=1' ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.txt && grep -q 'missing_rewrite: 1' ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.txt && grep -q 'consistency: failed=1, ok=1' ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.txt && grep -q 'consistency_failures: 1' ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.txt && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); g=data[\"summary\"][\"source_intent_graph\"]; assert g[\"complete\"] == 1; assert g[\"with_bindings\"] == 1; assert g[\"missing_rewrite\"] == 1; assert g[\"unsupported_reasons\"] == {\"missing-rewrite\":1}; assert g[\"consistency\"] == {\"failed\":1,\"ok\":1}; assert g[\"consistency_failures\"] == 1; assert g[\"consistency_errors\"] == {\"rewrite-replacement-mismatch\":1}; assert g[\"lowered_despite_inconsistency\"] == 0; assert data[\"records\"][0][\"source_intent_graph_bindings\"] == 1' ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-audit/audit.json"
        )
        add_test(
            NAME source_intent_graph_consistency_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-consistency && printf '%s\\n' '[{\"file\":\"tests/fixtures/source_intent_matcher_snippet.cpp\",\"line\":40,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"matched_pattern\":\"m_Add\",\"predicate_source\":\"match(V, m_Add(m_Value(X), m_Zero()))\",\"rewrite_source\":\"return replaceInstUsesWith(I, X);\",\"source_intent\":{\"model\":\"source-intent-v1\",\"before\":{\"shape\":\"scalar\",\"operation\":\"add\",\"operands\":[{\"symbol\":\"X\"},{\"constant\":0}]},\"after\":{\"result\":{\"symbol\":\"X\"}},\"rewrite\":{\"action\":\"replace-result\",\"api\":\"replaceInstUsesWith\",\"replacement\":{\"symbol\":\"X\"}},\"guards\":[{\"kind\":\"matcher\",\"role\":\"semantic\",\"source\":\"match\"}]},\"source_intent_graph\":{\"model\":\"source-intent-graph-v1\",\"status\":\"complete\",\"predicate_nodes\":[{\"kind\":\"condition\"}],\"rewrite_nodes\":[{\"kind\":\"rewrite\",\"action\":\"replace-result\",\"replacement\":\"Y\"}],\"bindings\":[{\"role\":\"operand\",\"source_symbol\":\"X\"}],\"guards\":[]}},{\"file\":\"tests/fixtures/vector_pass_snippet.cpp\",\"line\":53,\"marker\":\"probe.vector.shuffle-splat\",\"pass\":\"instcombine\",\"predicate_kind\":\"vector\",\"matched_pattern\":\"isSplatMask(I)\",\"predicate_source\":\"isSplatMask(I)\",\"rewrite_source\":\"return;\",\"constraints\":{\"vector.shuffle\":\"splat\",\"vector.shuffle.mask\":[2,2,2,2],\"vector.shuffle.splat_lane\":2},\"source_intent\":{\"model\":\"source-intent-v1\",\"subject\":\"vector\",\"before\":{\"shape\":\"fixed-vector\",\"operation\":\"shuffle\",\"identity\":\"splat-mask\",\"parameters\":{\"vector.shuffle.mask\":[2,2,2,2],\"vector.shuffle.splat_lane\":2}},\"after\":{\"rewrite\":\"replace-with-lane-splat\",\"result\":\"vector\"},\"rewrite\":{\"api\":\"source-vector-helper\",\"action\":\"replace-with-lane-splat\"},\"guards\":[{\"kind\":\"vector-helper\",\"role\":\"semantic\",\"source\":\"isSplatMask(I)\"}]},\"source_intent_graph\":{\"model\":\"source-intent-graph-v1\",\"status\":\"complete\",\"predicate_nodes\":[{\"kind\":\"condition\"}],\"rewrite_nodes\":[{\"kind\":\"rewrite\",\"action\":\"replace-with-lane-splat\"}],\"bindings\":[{\"role\":\"parameter\",\"source_symbol\":\"vector.shuffle.mask\",\"value\":[1,1,1,1]},{\"role\":\"parameter\",\"source_symbol\":\"vector.shuffle.splat_lane\",\"value\":2}],\"guards\":[]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-consistency/findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-consistency/findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-consistency/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 2; scalar=[r for r in records if r[\"marker\"] == \"probe.instcombine.add-zero\"][0]; vector=[r for r in records if r[\"marker\"] == \"probe.vector.shuffle-splat\"][0]; assert scalar[\"evidence\"][\"formal_inference\"] == \"source-derived-source-intent\"; assert scalar[\"evidence\"][\"source_intent_graph_lowering\"] == \"fallback\"; assert scalar[\"evidence\"][\"formal_parameters\"][\"source_intent_graph.consistency\"] == \"failed\"; assert scalar[\"evidence\"][\"formal_parameters\"][\"source_intent_graph.consistency_errors\"] == [\"rewrite-replacement-mismatch\"]; assert vector[\"evidence\"][\"formal_inference\"] == \"source-derived-source-intent\"; assert vector[\"evidence\"][\"source_intent_graph_lowering\"] == \"fallback\"; assert vector[\"evidence\"][\"formal_parameters\"][\"source_intent_graph.consistency\"] == \"failed\"; assert vector[\"evidence\"][\"formal_parameters\"][\"source_intent_graph.consistency_errors\"] == [\"vector-parameter-binding-mismatch:vector.shuffle.mask\"]; assert vector[\"evidence\"][\"formal_parameters\"][\"vector.shuffle.mask\"] == [2,2,2,2]' ${CMAKE_CURRENT_BINARY_DIR}/source-intent-graph-consistency/out.jsonl"
        )
        add_test(
            NAME intent_validation_negative_fixture
            COMMAND
                /bin/sh -c
                "printf '%s\\n' '{\"file\":\"synthetic.cpp\",\"line\":1,\"marker\":\"probe.instcombine.add-zero\",\"confidence\":\"high\",\"rewrite_source\":\"return replaceInstUsesWith(I, Op0);\",\"side_conditions\":[],\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"precondition\":\"instruction.opcode == add && rhs == 0\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"smt_before\":\"(bvadd a #x00000000)\",\"smt_after\":\"b\"}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-bad.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-bad.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-bad-validated.jsonl; test $? -eq 1) && grep -q '\"proof_status\": \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-bad-validated.jsonl && grep -q '\"counterexample\":' ${CMAKE_CURRENT_BINARY_DIR}/intent-bad-validated.jsonl"
        )
        add_test(
            NAME intent_validation_formal_ir_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-formal && printf '%s\\n' '{\"file\":\"synthetic.cpp\",\"line\":1,\"marker\":\"probe.instcombine.add-zero\",\"confidence\":\"high\",\"rewrite_source\":\"return replaceInstUsesWith(I, Op0);\",\"side_conditions\":[],\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"precondition\":\"add zero\",\"rewrite\":\"x\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\",\"b\"],\"before\":{\"op\":\"bvadd\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"bvconst\",\"value\":0,\"bits\":32}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\"}}}' '{\"file\":\"synthetic.cpp\",\"line\":2,\"marker\":\"probe.instcombine.mul-one\",\"confidence\":\"high\",\"rewrite_source\":\"return replaceInstUsesWith(I, Op0);\",\"side_conditions\":[],\"intent_candidate\":{\"marker\":\"probe.instcombine.mul-one\",\"precondition\":\"mul one\",\"rewrite\":\"x\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\",\"b\"],\"before\":{\"op\":\"bvmul\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"bvconst\",\"value\":1,\"bits\":32}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\"}}}' '{\"file\":\"synthetic.cpp\",\"line\":3,\"marker\":\"probe.instcombine.xor-self\",\"confidence\":\"high\",\"rewrite_source\":\"return replaceInstUsesWith(I, Constant::getNullValue(0));\",\"side_conditions\":[],\"intent_candidate\":{\"marker\":\"probe.instcombine.xor-self\",\"precondition\":\"xor self\",\"rewrite\":\"0\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\",\"b\"],\"before\":{\"op\":\"bvxor\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"a\"}]},\"after\":{\"op\":\"bvconst\",\"value\":0,\"bits\":32},\"equivalence\":\"result\"}}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-formal/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-formal/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-formal/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/intent-formal/smt && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 3; assert all(r[\"proof_status\"] == \"proved\" for r in records); assert all(r[\"promotion_status\"] == \"ready\" for r in records)' ${CMAKE_CURRENT_BINARY_DIR}/intent-formal/out.jsonl && grep -R -q 'smt_source: formal' ${CMAKE_CURRENT_BINARY_DIR}/intent-formal/smt"
        )
        add_test(
            NAME intent_validation_formal_ir_negative_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-negative && printf '%s\\n' '{\"file\":\"synthetic.cpp\",\"line\":1,\"marker\":\"probe.instcombine.add-zero\",\"confidence\":\"high\",\"rewrite_source\":\"return replaceInstUsesWith(I, Op0);\",\"side_conditions\":[],\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\",\"b\"],\"before\":{\"op\":\"bvadd\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"bvconst\",\"value\":0,\"bits\":32}]},\"after\":{\"op\":\"var\",\"name\":\"b\"},\"equivalence\":\"result\"}}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-negative/input.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-negative/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-negative/out.jsonl; test $? -eq 1) && grep -q '\"proof_status\": \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-negative/out.jsonl && grep -q '\"counterexample\":' ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-negative/out.jsonl"
        )
        add_test(
            NAME intent_validation_formal_ir_malformed_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-malformed && printf '%s\\n' '{\"file\":\"synthetic.cpp\",\"line\":1,\"marker\":\"probe.instcombine.add-zero\",\"confidence\":\"high\",\"rewrite_source\":\"return replaceInstUsesWith(I, Op0);\",\"side_conditions\":[],\"intent_candidate\":{\"marker\":\"probe.instcombine.add-zero\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"bad-op\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\"}}}' > ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-malformed/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-malformed/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-malformed/out.jsonl && grep -q '\"proof_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-malformed/out.jsonl && grep -q '\"proof_result\": \"unsupported-formal-ir\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-formal-malformed/out.jsonl"
        )
        add_test(
            NAME intent_validation_registry_backed_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/smt && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 19; assert all(r[\"proof_status\"] == \"proved\" for r in records); assert all(r[\"promotion_status\"] == \"blocked\" for r in records); domains={r[\"intent_candidate\"].get(\"formal\",{}).get(\"domain\") for r in records}; assert {\"scalar-bv32\",\"cfg-bv32\",\"memory-bv32\",\"loop-bv32\"} <= domains' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/out.jsonl && grep -R -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/smt && grep -R -q 'probe.mem2reg.store-load-forward' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/smt && grep -R -q 'probe.licm.invariant-op' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-backed/smt"
        )
        add_test(
            NAME intent_validation_scalar_registry_fallback_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback/smt && ${Python3_EXECUTABLE} -c 'import json,sys; scalar={\"probe.instcombine.add-zero\",\"probe.instcombine.mul-one\",\"probe.instcombine.xor-self\",\"probe.dce.dead-instruction\"}; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; chosen=[r for r in records if r[\"marker\"] in scalar]; assert len(chosen) == 4; assert all(r[\"rewrite_source\"] == \"return;\" for r in chosen); assert all(r[\"intent_candidate\"][\"formal\"][\"domain\"] == \"scalar-bv32\" for r in chosen); assert all(r[\"proof_status\"] == \"proved\" for r in chosen)' ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback/out.jsonl && grep -R -q 'probe.instcombine.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback/smt && grep -R -q 'probe.dce.dead-instruction' ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-registry-fallback/smt"
        )
        add_test(
            NAME intent_validation_vector_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-vector && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/smt && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; vector=[r for r in records if r[\"marker\"].startswith(\"probe.vector.\")]; assert len(vector) == 24; assert all(r[\"proof_status\"] == \"proved\" for r in vector); assert {r[\"intent_candidate\"][\"formal\"][\"domain\"] for r in vector} == {\"vector-bv32x4\",\"scalar-bv32\",\"scalable-vector-bv32\"}; assert all(r.get(\"evidence\",{}).get(\"formal_inference\") == \"source-derived-vector\" for r in vector); assert all(r.get(\"evidence\",{}).get(\"semantic_lowering\") == \"formal-ir\" for r in vector); assert all(r.get(\"evidence\",{}).get(\"semantic_facts\",{}).get(\"model\") == \"optimization-semantic-v1\" for r in vector); by_marker={r[\"marker\"]:r for r in vector}; assert by_marker[\"probe.vector.add-zero\"][\"evidence\"][\"semantic_facts\"][\"operation\"] == \"add\"; assert by_marker[\"probe.vector.smin\"][\"evidence\"][\"semantic_facts\"][\"identity\"] == \"signed-min\"; assert by_marker[\"probe.vector.umax\"][\"evidence\"][\"semantic_facts\"][\"identity\"] == \"unsigned-max\"; assert by_marker[\"probe.vector.abs\"][\"evidence\"][\"semantic_facts\"][\"operation\"] == \"abs\"; assert by_marker[\"probe.vector.shuffle-identity\"][\"evidence\"][\"formal_parameters\"][\"vector.shuffle.mask\"] == [0,1,2,3]; assert by_marker[\"probe.vector.shuffle-splat\"][\"evidence\"][\"formal_parameters\"][\"vector.shuffle.mask\"] == [2,2,2,2]; assert by_marker[\"probe.vector.shuffle-splat\"][\"evidence\"][\"formal_parameters\"][\"vector.shuffle.splat_lane\"] == 2; assert by_marker[\"probe.vector.extract-insert\"][\"evidence\"][\"formal_parameters\"][\"vector.extract_insert.lane\"] == 1; assert by_marker[\"probe.vector.insert-extract-identity\"][\"evidence\"][\"formal_parameters\"][\"vector.insert_extract.lane\"] == 1; assert by_marker[\"probe.vector.reduction-add-single-lane\"][\"evidence\"][\"formal_parameters\"][\"vector.reduction.lane\"] == 0; assert by_marker[\"probe.vector.scalable.add-zero\"][\"evidence\"][\"formal_parameters\"][\"vector.vscale_values\"] == [1,2,4]; assert len(by_marker[\"probe.vector.scalable.add-zero\"][\"proof_instances\"]) == 3' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/out.jsonl && grep -R -q 'probe.vector.shuffle-splat' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/smt && grep -R -q 'probe.vector.and-allones' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/smt && grep -R -q 'probe.vector.smin' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/smt && grep -R -q 'probe.vector.abs' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/smt && grep -R -q 'probe.vector.scalable.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/smt && grep -R -q '(_ BitVec 512)' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector/smt"
        )
        add_test(
            NAME intent_validation_vector_parameters_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-vector-params && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_parameter_findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-vector-params/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-vector-params/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-vector-params/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 2; assert all(r[\"proof_status\"] == \"proved\" for r in records); assert all(r[\"evidence\"][\"semantic_lowering\"] == \"formal-ir\" for r in records); by_marker={r[\"marker\"]:r for r in records}; splat=by_marker[\"probe.vector.shuffle-splat\"]; assert splat[\"evidence\"][\"formal_parameters\"][\"vector.shuffle.mask\"] == [3,3,3,3]; assert splat[\"evidence\"][\"formal_parameters\"][\"vector.shuffle.splat_lane\"] == 3; assert splat[\"intent_candidate\"][\"formal\"][\"before\"][\"mask\"] == [3,3,3,3]; assert splat[\"intent_candidate\"][\"formal\"][\"after\"][\"args\"][0][\"index\"] == 3; extract=by_marker[\"probe.vector.extract-insert\"]; assert extract[\"evidence\"][\"formal_parameters\"][\"vector.extract_insert.lane\"] == 3; assert extract[\"intent_candidate\"][\"formal\"][\"before\"][\"index\"] == 3; assert extract[\"intent_candidate\"][\"formal\"][\"before\"][\"args\"][0][\"index\"] == 3' ${CMAKE_CURRENT_BINARY_DIR}/intent-vector-params/out.jsonl"
        )
        add_test(
            NAME intent_validation_semantic_facts_incomplete_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-semantic-incomplete && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp\",\"line\":53,\"marker\":\"probe.vector.shuffle-splat\",\"pass\":\"instcombine\",\"predicate_kind\":\"vector\",\"matched_pattern\":\"isSplatMask\",\"predicate_source\":\"if (isSplatMask(I)) {\",\"rewrite_source\":\"return;\",\"constraints\":{\"vector.shuffle\":\"splat\",\"vector.shuffle.mask\":[3,3,3,3],\"vector.shuffle.splat_lane\":3},\"semantic_facts\":{\"model\":\"optimization-semantic-v1\",\"shape\":\"fixed-vector\",\"operation\":\"shuffle\"},\"finding_source\":\"fixture\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/intent-semantic-incomplete/findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/intent-semantic-incomplete/findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-semantic-incomplete/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-semantic-incomplete/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-semantic-incomplete/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 1; r=records[0]; assert r[\"proof_status\"] == \"unsupported\"; assert r[\"evidence\"][\"semantic_lowering\"] == \"unsupported\"; assert \"formal\" not in r[\"intent_candidate\"]' ${CMAKE_CURRENT_BINARY_DIR}/intent-semantic-incomplete/out.jsonl"
        )
        add_test(
            NAME semantic_facts_schema_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-semantic-facts.py --semantic-facts ${CMAKE_CURRENT_SOURCE_DIR}/constraints/semantic_facts.json --pass-constraints ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --optimization-intents ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json && ${Python3_EXECUTABLE} -c 'import json,sys; sys.path.insert(0, sys.argv[1]); from cv_semantic_facts import load_semantic_registry, semantic_facts_for_marker, semantic_facts_valid_for_marker; registry=load_semantic_registry(); pass_markers={r[\"marker\"] for r in json.load(open(sys.argv[2]))}; intent_markers={r[\"marker\"] for r in json.load(open(sys.argv[3]))}; assert len(registry) == 51; assert pass_markers == set(registry); assert intent_markers <= set(registry); scalar=semantic_facts_for_marker(\"probe.instcombine.add-zero\"); assert scalar[\"shape\"] == \"scalar\" and scalar[\"operation\"] == \"add\"; global_facts=semantic_facts_for_marker(\"probe.globalopt.dead-initializer\"); assert global_facts[\"shape\"] == \"global\" and global_facts[\"rewrite\"] == \"remove-global-initializer-if-dead-v1\"; cfg=semantic_facts_for_marker(\"probe.simplifycfg.nested-branch\"); assert cfg[\"shape\"] == \"cfg\" and cfg[\"rewrite\"] == \"collapse-nested-branch\"; mem=semantic_facts_for_marker(\"probe.mem2reg.store-load-forward\"); assert mem[\"shape\"] == \"memory\" and mem[\"result\"] == \"loaded-value\"; loop=semantic_facts_for_marker(\"probe.licm.invariant-op\"); assert loop[\"shape\"] == \"loop\" and loop[\"result\"] == \"loop-result\"; facts=semantic_facts_for_marker(\"probe.vector.scalable.add-zero\"); assert facts[\"model\"] == \"optimization-semantic-v1\"; assert facts[\"shape\"] == \"scalable-vector\"; assert semantic_facts_valid_for_marker(\"probe.vector.scalable.add-zero\", facts) == (True, \"\"); assert semantic_facts_for_marker(\"probe.vector.smin\")[\"identity\"] == \"signed-min\"; assert semantic_facts_for_marker(\"probe.vector.abs\")[\"operation\"] == \"abs\"; bad=dict(facts); bad[\"operation\"]=\"shuffle\"; ok,msg=semantic_facts_valid_for_marker(\"probe.vector.scalable.add-zero\", bad); assert not ok and \"match marker\" in msg; incomplete={\"model\":\"optimization-semantic-v1\",\"shape\":\"fixed-vector\",\"operation\":\"shuffle\"}; ok,msg=semantic_facts_valid_for_marker(\"probe.vector.shuffle-splat\", incomplete); assert not ok and \"missing fields\" in msg; unknown=dict(facts); unknown[\"identity\"]=\"mystery\"; ok,msg=semantic_facts_valid_for_marker(\"probe.vector.scalable.add-zero\", unknown); assert not ok and \"identity\" in msg' ${CMAKE_CURRENT_SOURCE_DIR}/tools ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json"
        )
        add_test(
            NAME guard_semantics_schema_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-guard-semantics.py --guard-semantics ${CMAKE_CURRENT_SOURCE_DIR}/constraints/guard_semantics.json > ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-schema.txt && grep -q '\"errors\": 0' ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-schema.txt && ${Python3_EXECUTABLE} -c 'import json,sys; sys.path.insert(0, sys.argv[1]); from cv_guard_semantics import load_guard_semantics, recognizer_summary; catalog=load_guard_semantics(); summary=recognizer_summary(catalog); assert catalog[\"known-nonzero\"][\"formal_effect\"] == \"not-eq-zero-assumption\"; assert catalog[\"known-nonzero\"][\"formal_effect_args\"][\"assumption\"] == {\"op\":\"not-eq\",\"value\":0}; assert catalog[\"known-power-of-two\"][\"formal_effect_args\"][\"assumption\"] == {\"op\":\"power-of-two\",\"nonzero\":True}; assert catalog[\"known-positive\"][\"formal_effect_args\"][\"assumption\"] == {\"op\":\"cmp\",\"predicate\":\"sgt\",\"value\":0}; assert catalog[\"known-nonnegative\"][\"formal_effect_args\"][\"assumption\"] == {\"op\":\"cmp\",\"predicate\":\"sge\",\"value\":0}; assert catalog[\"masked-value-is-zero\"][\"formal_effect_args\"][\"assumption\"] == {\"op\":\"known-bits\",\"zero_mask\":0}; assert catalog[\"guaranteed-not-poison\"][\"formal_effect\"] == \"not-poison-assumption\"; assert catalog[\"guaranteed-not-poison\"][\"formal_effect_args\"][\"assumption\"] == {\"op\":\"not-poison\",\"requires_poison_variable\":True}; assert catalog[\"guaranteed-not-poison\"][\"recognizers\"][\"subject_group\"] == \"subject\"; assert catalog[\"one-use\"][\"audit_category\"] == \"structural\"; assert catalog[\"one-use\"][\"formal_effect_args\"] == {\"structural\":True}; assert catalog[\"unknown\"][\"role\"] == \"unmodeled-side-condition\"; assert summary[\"text\"] == [\"guaranteed-not-poison\",\"known-nonequal\",\"known-nonnegative\",\"known-nonzero\",\"known-positive\",\"known-power-of-two\",\"masked-value-is-zero\",\"no-poison-generating-flags\",\"one-use\"]; assert summary[\"ast\"] == [\"guaranteed-not-poison\",\"known-nonequal\",\"known-nonnegative\",\"known-nonzero\",\"known-positive\",\"known-power-of-two\",\"masked-value-is-zero\",\"one-use\"]' ${CMAKE_CURRENT_SOURCE_DIR}/tools"
        )
        add_test(
            NAME guard_semantics_negative_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-negative && printf '%s\\n' '[{\"kind\":\"bad\",\"role\":\"profitability\",\"proof_effect\":\"x\",\"formal_effect\":\"not-poison-assumption\",\"audit_category\":\"modeled\",\"recognizers\":{\"text_patterns\":[\"(\"],\"subject_group\":\"subject\"}}]' > ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-negative/bad.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-guard-semantics.py --guard-semantics ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-negative/bad.json > ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-negative/out.txt; test $? -eq 1) && grep -q 'profitability role requires profitability audit_category' ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-negative/out.txt && grep -q 'invalid text pattern' ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-negative/out.txt && grep -q 'registry must define unknown fallback guard' ${CMAKE_CURRENT_BINARY_DIR}/guard-semantics-negative/out.txt"
        )
        add_test(
            NAME registry_contract_check_no_z3_fixture
            COMMAND
                /bin/sh -c
                "env CV_SKIP_Z3=1 PYTHON3=${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/scripts/check-registries.sh > ${CMAKE_CURRENT_BINARY_DIR}/registry-contract-no-z3.txt && grep -q 'json syntax validation:' ${CMAKE_CURRENT_BINARY_DIR}/registry-contract-no-z3.txt && grep -q 'guard semantics validation:' ${CMAKE_CURRENT_BINARY_DIR}/registry-contract-no-z3.txt && grep -q '\"errors\": 0' ${CMAKE_CURRENT_BINARY_DIR}/registry-contract-no-z3.txt && grep -q 'formal registry validation: skipped' ${CMAKE_CURRENT_BINARY_DIR}/registry-contract-no-z3.txt"
        )
        add_test(
            NAME semantic_facts_validator_negative_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); json.dump(data+[data[0]], open(sys.argv[2],\"w\"))' ${CMAKE_CURRENT_SOURCE_DIR}/constraints/semantic_facts.json ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/duplicate.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-semantic-facts.py --semantic-facts ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/duplicate.json --pass-constraints ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json 2> ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/duplicate.err; test $? -eq 1) && grep -q 'duplicate semantic marker' ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/duplicate.err && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); json.dump(data[:-1], open(sys.argv[2],\"w\"))' ${CMAKE_CURRENT_SOURCE_DIR}/constraints/semantic_facts.json ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/missing.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-semantic-facts.py --semantic-facts ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/missing.json --pass-constraints ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --optimization-intents ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json 2> ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/missing.err; test $? -eq 1) && grep -q 'missing semantic facts' ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/missing.err && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); data[0][\"semantic_facts\"][\"identity\"]=\"mystery\"; json.dump(data, open(sys.argv[2],\"w\"))' ${CMAKE_CURRENT_SOURCE_DIR}/constraints/semantic_facts.json ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/invalid.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-semantic-facts.py --semantic-facts ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/invalid.json --pass-constraints ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json 2> ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/invalid.err; test $? -eq 1) && grep -q 'unsupported semantic_facts identity' ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/invalid.err && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); data[4][\"semantic_facts\"][\"shape\"]=\"memory\"; json.dump(data, open(sys.argv[2],\"w\"))' ${CMAKE_CURRENT_SOURCE_DIR}/constraints/semantic_facts.json ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/mismatch.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-semantic-facts.py --semantic-facts ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/mismatch.json --pass-constraints ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json 2> ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/mismatch.err; test $? -eq 1) && grep -q 'does not match marker' ${CMAKE_CURRENT_BINARY_DIR}/semantic-validator-negative/mismatch.err"
        )
        add_test(
            NAME intent_validation_scalar_semantic_facts_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-semantic && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-semantic/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-semantic/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-semantic/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; scalar={\"probe.instcombine.add-zero\",\"probe.instcombine.mul-one\",\"probe.instcombine.xor-self\",\"probe.dce.dead-instruction\"}; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; chosen=[r for r in records if r[\"marker\"] in scalar]; assert len(chosen) == 4; assert all(r[\"proof_status\"] == \"proved\" for r in chosen); assert all(r[\"evidence\"][\"semantic_lowering\"] == \"formal-ir\" for r in chosen); assert {r[\"evidence\"][\"semantic_facts\"][\"shape\"] for r in chosen} == {\"scalar\"}; assert all(r[\"evidence\"][\"formal_inference\"] == \"source-derived-scalar\" for r in chosen)' ${CMAKE_CURRENT_BINARY_DIR}/intent-scalar-semantic/out.jsonl"
        )
        add_test(
            NAME intent_validation_cfg_memory_loop_semantic_facts_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; groups={\"cfg\":\"cfg-bv32\",\"memory\":\"memory-bv32\",\"loop\":\"loop-bv32\"}; chosen=[r for r in records if r.get(\"evidence\",{}).get(\"semantic_facts\",{}).get(\"shape\") in groups]; assert len(chosen) == 15; assert all(r[\"proof_status\"] == \"proved\" for r in chosen); assert all(r[\"evidence\"][\"semantic_lowering\"] == \"formal-ir\" for r in chosen); assert all(r[\"intent_candidate\"][\"formal\"][\"domain\"] == groups[r[\"evidence\"][\"semantic_facts\"][\"shape\"]] for r in chosen); fi={r[\"evidence\"][\"formal_inference\"] for r in chosen}; assert {\"source-derived-cfg\",\"source-derived-memory\",\"source-derived-loop\"} <= fi <= {\"source-derived-cfg\",\"source-derived-memory\",\"source-derived-loop\",\"registry-fallback\"}, fi; assert all(r[\"evidence\"][\"formal_parameters\"][\"semantic.lowering\"] == \"semantic-facts\" for r in chosen)' ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic/out.jsonl"
        )
        add_test(
            NAME intent_validation_nested_branch_semantic_facts_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-nested-semantic && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp\",\"line\":70,\"marker\":\"probe.simplifycfg.nested-branch\",\"pass\":\"simplifycfg\",\"predicate_kind\":\"cfg\",\"matched_pattern\":\"NestedBranch\",\"predicate_source\":\"if (BB.getSinglePredecessor()) {\",\"rewrite_source\":\"return;\",\"constraints\":{\"cfg.shape\":\"nested-branch\"},\"semantic_facts\":{\"model\":\"optimization-semantic-v1\",\"shape\":\"cfg\",\"operation\":\"simplify-branch\",\"identity\":\"nested-branch\",\"rewrite\":\"collapse-nested-branch\",\"result\":\"reachable-result\"},\"finding_source\":\"fixture\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/intent-nested-semantic/findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/intent-nested-semantic/findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-nested-semantic/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-nested-semantic/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-nested-semantic/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 1; r=records[0]; assert r[\"marker\"] == \"probe.simplifycfg.nested-branch\"; assert r[\"proof_status\"] == \"proved\"; assert r[\"intent_candidate\"][\"formal\"][\"domain\"] == \"cfg-bv32\"; assert r[\"evidence\"][\"formal_inference\"] == \"source-derived-cfg\"; assert r[\"evidence\"][\"semantic_lowering\"] == \"formal-ir\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-nested-semantic/out.jsonl"
        )
        add_test(
            NAME intent_validation_nonvector_semantic_mismatch_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic-bad && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp\",\"line\":70,\"marker\":\"probe.simplifycfg.diamond\",\"pass\":\"simplifycfg\",\"predicate_kind\":\"cfg\",\"matched_pattern\":\"getSinglePredecessor(\",\"predicate_source\":\"if (BB.getSinglePredecessor()) {\",\"rewrite_source\":\"return;\",\"constraints\":{\"cfg.shape\":\"diamond\"},\"semantic_facts\":{\"model\":\"optimization-semantic-v1\",\"shape\":\"memory\",\"operation\":\"remove-store\",\"identity\":\"dead-store\",\"rewrite\":\"remove-dead-store\",\"result\":\"observable-result\"},\"finding_source\":\"fixture\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic-bad/findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic-bad/findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic-bad/input.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic-bad/input.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic-bad/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert len(records) == 1; r=records[0]; assert r[\"proof_status\"] == \"unsupported\"; assert r[\"evidence\"][\"semantic_lowering\"] == \"unsupported\"; assert \"formal\" not in r[\"intent_candidate\"]; assert \"match marker\" in r[\"evidence\"][\"formal_parameters\"][\"semantic.unsupported_reason\"]' ${CMAKE_CURRENT_BINARY_DIR}/intent-nonvector-semantic-bad/out.jsonl"
        )
        add_test(
            NAME semantic_facts_promotion_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/semantic-promotion && printf '%s\\n' '[]' > ${CMAKE_CURRENT_BINARY_DIR}/semantic-promotion/current.json && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"pass\":\"instcombine\",\"file\":\"tests/fixtures/vector_pass_snippet.cpp\",\"line\":36,\"confidence\":\"high\",\"promotion_status\":\"ready\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"predicate_source\":\"match(Vec1, m_SplatOrPoison(m_Zero()))\",\"rewrite_source\":\"return;\",\"side_conditions\":[],\"intent_candidate\":{\"marker\":\"probe.vector.add-zero\",\"precondition\":\"vector add zero\",\"rewrite\":\"replace add with lhs\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\",\"variables\":[\"a0\",\"a1\",\"a2\",\"a3\"],\"before\":{\"op\":\"vadd\",\"args\":[{\"op\":\"vec\",\"args\":[{\"op\":\"var\",\"name\":\"a0\"},{\"op\":\"var\",\"name\":\"a1\"},{\"op\":\"var\",\"name\":\"a2\"},{\"op\":\"var\",\"name\":\"a3\"}]},{\"op\":\"vsplat\",\"args\":[{\"op\":\"bvconst\",\"value\":0,\"bits\":32}]}]},\"after\":{\"op\":\"vec\",\"args\":[{\"op\":\"var\",\"name\":\"a0\"},{\"op\":\"var\",\"name\":\"a1\"},{\"op\":\"var\",\"name\":\"a2\"},{\"op\":\"var\",\"name\":\"a3\"}]},\"equivalence\":\"vector-result\"}},\"evidence\":{\"matched_pattern\":\"m_SplatOrPoison(m_Zero())\",\"semantic_lowering\":\"formal-ir\",\"semantic_facts\":{\"model\":\"optimization-semantic-v1\",\"shape\":\"fixed-vector\",\"operation\":\"add\",\"identity\":\"zero\",\"rewrite\":\"replace-with-lhs\",\"result\":\"vector\"},\"formal_parameters\":{\"semantic.lowering\":\"semantic-facts\",\"semantic.operation\":\"add\",\"semantic.rewrite\":\"replace-with-lhs\"}}}' > ${CMAKE_CURRENT_BINARY_DIR}/semantic-promotion/validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-promote-intent-candidates.py --validated ${CMAKE_CURRENT_BINARY_DIR}/semantic-promotion/validated.jsonl --current ${CMAKE_CURRENT_BINARY_DIR}/semantic-promotion/current.json --out ${CMAKE_CURRENT_BINARY_DIR}/semantic-promotion/proposed.json --require-ready && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); ev=data[0][\"evidence\"]; assert ev[\"semantic_lowering\"] == \"formal-ir\"; assert ev[\"semantic_facts\"][\"operation\"] == \"add\"; assert ev[\"semantic_parameters\"][\"semantic.operation\"] == \"add\"' ${CMAKE_CURRENT_BINARY_DIR}/semantic-promotion/proposed.json"
        )
        add_test(
            NAME semantic_facts_evidence_report_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence && printf '%s\\n' '{\"marker\":\"probe.vector.add-zero\",\"pass\":\"instcombine\",\"file\":\"tests/fixtures/vector_pass_snippet.cpp\",\"line\":36,\"confidence\":\"high\",\"promotion_status\":\"ready\",\"proof_status\":\"proved\",\"proof_result\":\"unsat\",\"intent_candidate\":{\"marker\":\"probe.vector.add-zero\",\"precondition\":\"vector add zero\",\"rewrite\":\"replace add with lhs\",\"intent\":\"vector-result-equivalence\"},\"evidence\":{\"semantic_lowering\":\"formal-ir\",\"semantic_facts\":{\"model\":\"optimization-semantic-v1\",\"shape\":\"fixed-vector\",\"operation\":\"add\",\"identity\":\"zero\",\"rewrite\":\"replace-with-lhs\",\"result\":\"vector\"},\"formal_parameters\":{\"semantic.lowering\":\"semantic-facts\",\"semantic.operation\":\"add\"}}}' > ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/validated.jsonl && printf '%s\\n' '{\"case\":\"vector_add_zero\",\"status\":\"passed\",\"semantic_status\":\"matched\",\"oracle_status\":\"matched\",\"alive2_status\":\"proved\",\"expected_markers\":\"probe.vector.add-zero\",\"observed_markers\":\"probe.vector.add-zero\"}' > ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/manifest.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-build-intent-evidence.py --validated ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/validated.jsonl --opt-manifest ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/manifest.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/evidence.jsonl --report ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/report.txt && grep -q '^Semantic lowering$' ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/report.txt && grep -q '^  formal-ir: 1$' ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/report.txt && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); assert r[\"semantic_lowering\"] == \"formal-ir\"; assert r[\"semantic_facts\"][\"operation\"] == \"add\"; assert r[\"semantic_parameters\"][\"semantic.operation\"] == \"add\"' ${CMAKE_CURRENT_BINARY_DIR}/semantic-evidence/evidence.jsonl"
        )
        add_test(
            NAME formal_ir_sort_validation_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort && printf '%s\\n' '[{\"marker\":\"probe.instcombine.add-zero\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\",\"b\"],\"before\":{\"op\":\"eq\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\"}},{\"marker\":\"probe.simplifycfg.diamond\",\"category\":\"cfg\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"reachable-result-equivalence\",\"formal\":{\"domain\":\"cfg-bv32\",\"variables\":[\"a\",\"b\"],\"before\":{\"op\":\"ite\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"reachable-result\"}},{\"marker\":\"probe.simplifycfg.branch-chain\",\"category\":\"cfg\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"reachable-result-equivalence\",\"formal\":{\"domain\":\"cfg-bv32\",\"variables\":[\"a\",\"b\"],\"before\":{\"op\":\"ite\",\"args\":[{\"op\":\"eq\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"}]},{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"eq\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"}]}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"reachable-result\"}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort/out.jsonl && test $(grep -c '\"formal_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort/out.jsonl) -eq 3 && grep -q 'formal before and after must be bit-vector expressions' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort/out.jsonl && grep -q 'formal ite condition must be boolean' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort/out.jsonl && grep -q 'formal ite branches must have matching sorts' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-sort/out.jsonl"
        )
        add_test(
            NAME formal_ir_vector_validation_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vector && printf '%s\\n' '[{\"marker\":\"probe.vector.bad-lanes\",\"category\":\"vector\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\",\"variables\":[\"a\"],\"before\":{\"op\":\"vec\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"after\":{\"op\":\"vsplat\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"equivalence\":\"vector-result\"}},{\"marker\":\"probe.vector.bad-index\",\"category\":\"vector\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\",\"variables\":[\"a\"],\"before\":{\"op\":\"vinsert\",\"args\":[{\"op\":\"vsplat\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},{\"op\":\"var\",\"name\":\"a\"}],\"index\":4},\"after\":{\"op\":\"vsplat\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"equivalence\":\"vector-result\"}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vector/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vector/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vector/out.jsonl && test $(grep -c '\"formal_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vector/out.jsonl) -eq 2 && grep -q 'formal vec requires 4 args' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vector/out.jsonl && grep -q 'formal vinsert index must be in range 0..3' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vector/out.jsonl"
        )
        add_test(
            NAME formal_ir_fp_reduction_validation_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction && printf '%s\\n' '[{\"marker\":\"probe.fp.reduction-add\",\"category\":\"vector\",\"precondition\":\"ordered fp lanes\",\"rewrite\":\"vector fp reduction\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-fp32\",\"variables\":[\"a0\",\"a1\",\"a2\",\"a3\"],\"before\":{\"op\":\"fpadd\",\"args\":[{\"op\":\"fpadd\",\"args\":[{\"op\":\"fpadd\",\"args\":[{\"op\":\"fpvar\",\"name\":\"a0\"},{\"op\":\"fpvar\",\"name\":\"a1\"}]},{\"op\":\"fpvar\",\"name\":\"a2\"}]},{\"op\":\"fpvar\",\"name\":\"a3\"}]},\"after\":{\"op\":\"fpreduce_add\",\"args\":[{\"op\":\"fpvec\",\"args\":[{\"op\":\"fpvar\",\"name\":\"a0\"},{\"op\":\"fpvar\",\"name\":\"a1\"},{\"op\":\"fpvar\",\"name\":\"a2\"},{\"op\":\"fpvar\",\"name\":\"a3\"}]}]},\"equivalence\":\"result\"}},{\"marker\":\"probe.fp.bad-sort\",\"category\":\"vector\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-fp32\",\"variables\":[\"a0\",\"a1\"],\"before\":{\"op\":\"fpadd\",\"args\":[{\"op\":\"fpvar\",\"name\":\"a0\"},{\"op\":\"var\",\"name\":\"a1\"}]},\"after\":{\"op\":\"fpvar\",\"name\":\"a0\"},\"equivalence\":\"result\"}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/smt && test $(grep -c '\"formal_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/out.jsonl) -eq 1 && test $(grep -c '\"formal_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/out.jsonl) -eq 1 && grep -q 'formal expression must be floating-point' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/out.jsonl && grep -R -q '(set-logic QF_FP)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/smt && grep -R -q 'Float32' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/smt && grep -R -q 'fp.add roundNearestTiesToEven' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-fp-reduction/smt"
        )
        add_test(
            NAME formal_ir_poison_refinement_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison && printf '%s\\n' '[{\"marker\":\"probe.poison.add-refines\",\"category\":\"scalar\",\"precondition\":\"defined x\",\"rewrite\":\"add zero preserves defined value\",\"intent\":\"result-refinement\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"poison_variables\":[\"a\"],\"before\":{\"op\":\"bvadd\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"bvconst\",\"value\":0,\"bits\":32}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"refinement\":\"refinement\"}},{\"marker\":\"probe.poison.assume-not-poison\",\"category\":\"scalar\",\"precondition\":\"a is not poison\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"poison_variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"not-poison\",\"name\":\"a\"}]}},{\"marker\":\"probe.poison.freeze-stable\",\"category\":\"scalar\",\"precondition\":\"freeze creates stable defined value\",\"rewrite\":\"same named freeze is stable\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"poison_variables\":[\"a\"],\"before\":{\"op\":\"freeze\",\"name\":\"x\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"after\":{\"op\":\"freeze\",\"name\":\"x\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"equivalence\":\"result\"}},{\"marker\":\"probe.poison.undef-rejected\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"undef\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\"}},{\"marker\":\"probe.poison.bad-assumption\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"not-poison\",\"name\":\"missing\"}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/smt && test $(grep -c '\"formal_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/out.jsonl) -eq 3 && grep -q 'formal undef requires freeze' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/out.jsonl && grep -q 'formal not-poison assumption variable is undeclared' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/out.jsonl && grep -R -q 'before_poison' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/smt && grep -R -q 'a_poison' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/smt && grep -R -q '(not a_poison)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison/smt"
        )
        add_test(
            NAME formal_ir_value_assumption_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption && printf '%s\\n' '[{\"marker\":\"probe.value.assume-nonzero\",\"category\":\"scalar\",\"precondition\":\"a nonzero\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"not-eq\",\"name\":\"a\",\"value\":0}]}},{\"marker\":\"probe.value.assume-vector-lane-nonzero\",\"category\":\"vector\",\"precondition\":\"a nonzero\",\"rewrite\":\"same vector\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\",\"variables\":[\"a\",\"b\",\"c\",\"d\"],\"before\":{\"op\":\"vec\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"},{\"op\":\"var\",\"name\":\"c\"},{\"op\":\"var\",\"name\":\"d\"}]},\"after\":{\"op\":\"vec\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"},{\"op\":\"var\",\"name\":\"c\"},{\"op\":\"var\",\"name\":\"d\"}]},\"equivalence\":\"vector-result\",\"assumptions\":[{\"op\":\"not-eq\",\"name\":\"a\",\"value\":0}]}},{\"marker\":\"probe.value.bad-assumption-var\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"not-eq\",\"name\":\"missing\",\"value\":0}]}},{\"marker\":\"probe.value.bad-assumption-value\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"not-eq\",\"name\":\"a\",\"value\":1}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/smt && test $(grep -c '\"formal_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/out.jsonl) -eq 2 && grep -q 'formal not-eq assumption variable is undeclared' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/out.jsonl && grep -q 'formal not-eq assumption only supports value 0' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/out.jsonl && grep -R -q '(not (= a #x00000000))' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-value-assumption/smt"
        )
        add_test(
            NAME formal_ir_cmp_assumption_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption && printf '%s\\n' '[{\"marker\":\"probe.value.assume-positive\",\"category\":\"scalar\",\"precondition\":\"a positive\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"cmp\",\"predicate\":\"sgt\",\"name\":\"a\",\"value\":0}]}},{\"marker\":\"probe.value.assume-nonnegative\",\"category\":\"scalar\",\"precondition\":\"a nonnegative\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"cmp\",\"predicate\":\"sge\",\"name\":\"a\",\"value\":0}]}},{\"marker\":\"probe.value.assume-unsigned\",\"category\":\"scalar\",\"precondition\":\"a unsigned below one\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"cmp\",\"predicate\":\"ult\",\"name\":\"a\",\"value\":1}]}},{\"marker\":\"probe.value.bad-cmp-predicate\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"cmp\",\"predicate\":\"bad\",\"name\":\"a\",\"value\":0}]}},{\"marker\":\"probe.value.bad-cmp-var\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"cmp\",\"predicate\":\"sge\",\"name\":\"missing\",\"value\":0}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/smt && test $(grep -c '\"formal_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/out.jsonl) -eq 3 && grep -q 'unsupported formal cmp assumption predicate' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/out.jsonl && grep -q 'formal cmp assumption variable is undeclared' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/out.jsonl && grep -R -q '(bvsgt a #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/smt && grep -R -q '(bvsge a #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/smt && grep -R -q '(bvult a #x00000001)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-cmp-assumption/smt"
        )
        add_test(
            NAME formal_ir_vacuous_premises_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises && printf '%s\\n' '[{\"marker\":\"probe.value.assume-positive\",\"category\":\"scalar\",\"precondition\":\"a positive\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"cmp\",\"predicate\":\"sgt\",\"name\":\"a\",\"value\":0}]}},{\"marker\":\"probe.value.contradictory-premises\",\"category\":\"scalar\",\"precondition\":\"a>0 and a<0 (impossible)\",\"rewrite\":\"a -> a+1 (would refute, but premises contradict)\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"bvadd\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"bvconst\",\"value\":1,\"bits\":32}]},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"cmp\",\"predicate\":\"sgt\",\"name\":\"a\",\"value\":0},{\"op\":\"cmp\",\"predicate\":\"slt\",\"name\":\"a\",\"value\":0}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises/out.jsonl && test $(grep -c '\"formal_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises/out.jsonl) -eq 1 && test $(grep -c '\"formal_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises/out.jsonl) -eq 1 && grep -q 'unsupported-vacuous-premises' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises/out.jsonl && grep -q 'vacuous proof' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-vacuous-premises/out.jsonl"
        )
        add_test(
            NAME formal_ir_known_bits_assumption_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption && printf '%s\\n' '[{\"marker\":\"probe.value.assume-known-zero-mask\",\"category\":\"scalar\",\"precondition\":\"low byte zero\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":255}]}},{\"marker\":\"probe.value.assume-known-one-mask\",\"category\":\"scalar\",\"precondition\":\"low bit one\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"known-bits\",\"name\":\"a\",\"one_mask\":1}]}},{\"marker\":\"probe.value.bad-known-bits-overlap\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":1,\"one_mask\":1}]}},{\"marker\":\"probe.value.bad-known-bits-var\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"known-bits\",\"name\":\"missing\",\"zero_mask\":255}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/smt && test $(grep -c '\"formal_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/out.jsonl) -eq 2 && grep -q 'formal known-bits masks overlap' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/out.jsonl && grep -q 'formal known-bits assumption variable is undeclared' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/out.jsonl && grep -R -q '(= (bvand a #x000000ff) #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/smt && grep -R -q '(= (bvand a #x00000001) #x00000001)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-known-bits-assumption/smt"
        )
        add_test(
            NAME formal_ir_power_of_two_assumption_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption && printf '%s\\n' '[{\"marker\":\"probe.value.assume-power-of-two\",\"category\":\"scalar\",\"precondition\":\"a is power of two\",\"rewrite\":\"same value\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"power-of-two\",\"name\":\"a\",\"nonzero\":true}]}},{\"marker\":\"probe.value.assume-vector-power-of-two\",\"category\":\"vector\",\"precondition\":\"lanes are power of two\",\"rewrite\":\"same vector\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\",\"variables\":[\"a\",\"b\",\"c\",\"d\"],\"before\":{\"op\":\"vec\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"},{\"op\":\"var\",\"name\":\"c\"},{\"op\":\"var\",\"name\":\"d\"}]},\"after\":{\"op\":\"vec\",\"args\":[{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"b\"},{\"op\":\"var\",\"name\":\"c\"},{\"op\":\"var\",\"name\":\"d\"}]},\"equivalence\":\"vector-result\",\"assumptions\":[{\"op\":\"power-of-two\",\"name\":\"a\",\"nonzero\":true}]}},{\"marker\":\"probe.value.bad-power-of-two-var\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"power-of-two\",\"name\":\"missing\",\"nonzero\":true}]}},{\"marker\":\"probe.value.bad-power-of-two-nonzero\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"power-of-two\",\"name\":\"a\",\"nonzero\":false}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/smt && test $(grep -c '\"formal_status\": \"proved\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/out.jsonl) -eq 2 && grep -q 'formal power-of-two assumption variable is undeclared' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/out.jsonl && grep -q 'formal power-of-two assumption requires nonzero true' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/out.jsonl && grep -R -q '(not (= a #x00000000))' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/smt && grep -R -q '(= (bvand a (bvsub a #x00000001)) #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-power-of-two-assumption/smt"
        )
        add_test(
            NAME formal_ir_assumption_algebra_contradiction_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra && printf '%s\\n' '[{\"marker\":\"probe.value.bad-assumption-algebra-zero-nonzero\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"not-eq\",\"name\":\"a\",\"value\":0},{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":4294967295}]}},{\"marker\":\"probe.value.bad-assumption-algebra-zero-power\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"var\",\"name\":\"a\"},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\",\"assumptions\":[{\"op\":\"power-of-two\",\"name\":\"a\",\"nonzero\":true},{\"op\":\"known-bits\",\"name\":\"a\",\"zero_mask\":4294967295}]}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra/out.jsonl && test $(grep -c '\"formal_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra/out.jsonl) -eq 2 && grep -q '\"formal_result\": \"unsupported-contradictory-assumptions\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra/out.jsonl && grep -q 'formal contradictory assumptions' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra/out.jsonl && grep -q 'known all-zero bits conflict with power-of-two' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-assumption-algebra/out.jsonl"
        )
        add_test(
            NAME formal_ir_poison_negative_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison-negative && printf '%s\\n' '[{\"marker\":\"probe.poison.strict-fails\",\"category\":\"scalar\",\"precondition\":\"bad\",\"rewrite\":\"drops poison\",\"intent\":\"result-equivalence\",\"formal\":{\"domain\":\"scalar-bv32\",\"variables\":[\"a\"],\"before\":{\"op\":\"poison\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"result\"}},{\"marker\":\"probe.poison.vector-lane-fails\",\"category\":\"vector\",\"precondition\":\"bad\",\"rewrite\":\"drops lane poison\",\"intent\":\"vector-result-equivalence\",\"formal\":{\"domain\":\"vector-bv32x4\",\"variables\":[\"a\"],\"before\":{\"op\":\"vec\",\"args\":[{\"op\":\"poison\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"a\"},{\"op\":\"var\",\"name\":\"a\"}]},\"after\":{\"op\":\"vsplat\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"equivalence\":\"vector-result\"}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison-negative/input.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison-negative/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison-negative/out.jsonl; test $? -eq 1) && test $(grep -c '\"formal_status\": \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison-negative/out.jsonl) -eq 2 && grep -q '\"formal_result\": \"sat\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-poison-negative/out.jsonl"
        )
        add_test(
            NAME intent_registry_formal_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-registry && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json --out ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/out.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt > ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/stdout.txt && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert sum(r[\"formal_status\"] == \"proved\" for r in records) == 51; assert not [r for r in records if r.get(\"formal_result\") == \"no-formal-ir\"]; assert {r[\"marker\"] for r in records if r[\"formal_status\"] == \"proved\"} == {r[\"marker\"] for r in records}; assert len([r for r in records if r[\"marker\"] == \"probe.vector.scalable.add-zero\"][0][\"formal_instances\"]) == 3' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/out.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); required={\"probe.instcombine.add-zero\",\"probe.instcombine.mul-one\",\"probe.instcombine.xor-self\",\"probe.vector.add-zero\",\"probe.vector.mul-one\",\"probe.vector.xor-self\",\"probe.vector.shuffle-identity\",\"probe.vector.shuffle-splat\",\"probe.vector.extract-insert\",\"probe.vector.sub-zero\",\"probe.vector.or-zero\",\"probe.vector.and-allones\",\"probe.vector.smin\",\"probe.vector.smax\",\"probe.vector.umin\",\"probe.vector.umax\",\"probe.vector.abs\",\"probe.vector.insert-extract-identity\",\"probe.vector.reduction-add-single-lane\",\"probe.vector.scalable.add-zero\",\"probe.vector.scalable.mul-one\",\"probe.vector.scalable.xor-self\",\"probe.vector.scalable.sub-zero\",\"probe.vector.scalable.or-zero\",\"probe.vector.scalable.and-allones\"}; by_marker={r[\"marker\"]:r for r in data}; assert all(by_marker[m][\"formal\"].get(\"refinement\") == \"refinement\" for m in required); assert all(by_marker[m][\"formal\"].get(\"poison_variables\") for m in required); global_formal=by_marker[\"probe.globalopt.dead-initializer\"][\"formal\"]; assert global_formal[\"domain\"] == \"global-initializer-observable-v1\"; assert global_formal[\"contract\"] == \"remove-global-initializer-if-dead-v1\"; assert global_formal[\"required_safety_facts\"] == [\"initializer-dead\",\"local-linkage\",\"no-uses\"]; assert global_formal[\"required_witness_cases\"] == [\"i32\",\"ptr\",\"array\"]' ${CMAKE_CURRENT_SOURCE_DIR}/constraints/optimization_intents.json && grep -q '\"proved\": 51' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/stdout.txt && grep -R -q 'smt_source: formal' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'formal-vscale-4' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q '(ite ' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'probe.globalopt.dead-initializer' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'probe.loop.canonical-header' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'probe.licm.invariant-op' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'probe.vector.shuffle-identity' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'probe.vector.smin' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'probe.vector.abs' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'probe.vector.scalable.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q '(_ BitVec 512)' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'a_poison' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'a0_poison' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt && grep -R -q 'before_poison' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry/smt"
        )
        add_test(
            NAME formal_ir_global_initializer_malformed_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-global-malformed && printf '%s\\n' '[{\"marker\":\"probe.globalopt.dead-initializer\",\"category\":\"global\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"global-initializer-observable-equivalence\",\"formal\":{\"domain\":\"global-initializer-observable-v1\",\"contract\":\"remove-global-initializer-if-dead-v1\",\"observability_model\":\"local-unobservable-initializer-v1\",\"required_safety_facts\":[\"initializer-dead\",\"no-uses\"],\"rewrite_api\":\"setInitializer\",\"replacement_kind\":\"default-null-initializer\",\"witness_model\":\"global-initializer-default-null-family-v1\",\"required_witness_cases\":[\"i32\",\"ptr\",\"array\"],\"equivalence\":\"observable-result\"}},{\"marker\":\"probe.globalopt.dead-initializer\",\"category\":\"global\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"global-initializer-observable-equivalence\",\"formal\":{\"domain\":\"global-initializer-observable-v1\",\"contract\":\"remove-global-initializer-if-dead-v1\",\"observability_model\":\"local-unobservable-initializer-v1\",\"required_safety_facts\":[\"initializer-dead\",\"local-linkage\",\"no-uses\"],\"rewrite_api\":\"setInitializer\",\"replacement_kind\":\"nonnull-initializer\",\"witness_model\":\"global-initializer-default-null-family-v1\",\"required_witness_cases\":[\"i32\",\"ptr\",\"array\"],\"equivalence\":\"observable-result\"}}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-global-malformed/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-global-malformed/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-global-malformed/out.jsonl && test $(grep -c '\"formal_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-global-malformed/out.jsonl) -eq 2 && grep -q 'formal global initializer required_safety_facts are unsupported' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-global-malformed/out.jsonl && grep -q 'formal global initializer replacement_kind is unsupported' ${CMAKE_CURRENT_BINARY_DIR}/formal-ir-global-malformed/out.jsonl"
        )
        add_test(
            NAME registry_contract_check_formal_fixture
            COMMAND
                /bin/sh -c
                "env PYTHON3=${Python3_EXECUTABLE} Z3=${Z3_EXECUTABLE} CHECK_REGISTRIES_OUT=${CMAKE_CURRENT_BINARY_DIR}/registry-contract ${CMAKE_CURRENT_SOURCE_DIR}/scripts/check-registries.sh > ${CMAKE_CURRENT_BINARY_DIR}/registry-contract.txt && grep -q 'formal registry validation: completed with' ${CMAKE_CURRENT_BINARY_DIR}/registry-contract.txt && grep -q '\"proved\": 51' ${CMAKE_CURRENT_BINARY_DIR}/registry-contract.txt && test -f ${CMAKE_CURRENT_BINARY_DIR}/registry-contract/intent-registry.jsonl && test -d ${CMAKE_CURRENT_BINARY_DIR}/registry-contract/intent-registry-smt"
        )
        add_test(
            NAME intent_registry_formal_malformed_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-malformed && printf '%s\\n' '[{\"marker\":\"probe.loop.simple-trip-count\",\"category\":\"loop\",\"precondition\":\"bad\",\"rewrite\":\"bad\",\"intent\":\"loop-result-equivalence\",\"formal\":{\"domain\":\"loop-bv32\",\"variables\":[\"a\",\"trip\"],\"before\":{\"op\":\"bvmul\",\"args\":[{\"op\":\"var\",\"name\":\"a\"}]},\"after\":{\"op\":\"var\",\"name\":\"a\"},\"equivalence\":\"loop-result\"}}]' > ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-malformed/input.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-registry.py --z3 ${Z3_EXECUTABLE} --intents ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-malformed/input.json --out ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-malformed/out.jsonl && grep -q '\"formal_status\": \"unsupported\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-malformed/out.jsonl && grep -q '\"formal_result\": \"unsupported-formal-ir\"' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-malformed/out.jsonl && grep -q 'formal bvmul requires 2 args' ${CMAKE_CURRENT_BINARY_DIR}/intent-registry-malformed/out.jsonl"
        )
        add_test(
            NAME formal_check_scalar_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --marker probe.instcombine.add-zero > ${CMAKE_CURRENT_BINARY_DIR}/formal-scalar.txt && grep -q 'formal_status=proved marker=probe.instcombine.add-zero result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-scalar.txt && printf '%s\\n' 'arith_opcode=3' 'rhs_mode=2' 'extra_opcode=3' 'shape=0' > ${CMAKE_CURRENT_BINARY_DIR}/formal-xor.cfg && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_BINARY_DIR}/formal-xor.cfg --marker probe.instcombine.xor-self > ${CMAKE_CURRENT_BINARY_DIR}/formal-xor.txt && grep -q 'formal_status=proved marker=probe.instcombine.xor-self result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-xor.txt"
        )
        add_test(
            NAME formal_check_cfg_memory_loop_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/nested_diamond.cfg > ${CMAKE_CURRENT_BINARY_DIR}/formal-cfg.txt && grep -q 'formal_status=proved marker=probe.simplifycfg.nested-branch result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-cfg.txt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/overwritten_store.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory.txt && grep -q 'formal_status=proved marker=probe.dse.overwritten-store result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory.txt && grep -R -q 'dse-byte-model: 4 independent 8-bit lanes' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-smt && grep -R -q 'dse-observation-window: no-intervening-read' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-smt && grep -R -q 'dse-observation-window: no-unknown-memory-effect' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-smt && grep -R -q 'declare-const kill0 (_ BitVec 8)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/partial_overwritten_store.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial.txt && grep -q 'formal_status=proved marker=probe.dse.overwritten-store result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial.txt && grep -R -q 'dse-byte-mask: lanes-0-1-of-4' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-smt && grep -R -q '(concat original3 original2 kill1 kill0)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/partial_overwritten_store_high.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-high-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-high.txt && grep -q 'formal_status=proved marker=probe.dse.overwritten-store result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-high.txt && grep -R -q 'dse-byte-mask: lanes-2-3-of-4' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-high-smt && grep -R -q '(concat kill3 kill2 original1 original0)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-high-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/partial_overwritten_store_sparse.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-sparse-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-sparse.txt && grep -q 'formal_status=proved marker=probe.dse.overwritten-store result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-sparse.txt && grep -R -q 'dse-byte-mask: lanes-0-3-of-4' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-sparse-smt && grep -R -q '(concat kill3 original2 original1 kill0)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-partial-sparse-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/invariant_loop.cfg > ${CMAKE_CURRENT_BINARY_DIR}/formal-loop.txt && grep -q 'formal_status=proved marker=probe.licm.invariant-op result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-loop.txt"
        )
        add_test(
            NAME formal_check_dse_size_model_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/overwritten_store.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-size-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-size.txt && grep -R -q 'dse-size-model: known bounded eight-byte memory' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-size-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/overwritten_store_symbolic_width1.cfg --marker probe.dse.overwritten-store --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width1-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width1.txt && grep -q 'formal_status=proved marker=probe.dse.overwritten-store result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width1.txt && grep -R -q 'dse-byte-model: 1 independent 8-bit lanes' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width1-smt && grep -R -q 'dse-size-model: symbolic bounded eight-byte memory' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width1-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/overwritten_store_symbolic_width4.cfg --marker probe.dse.overwritten-store --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width4-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width4.txt && grep -q 'formal_status=proved marker=probe.dse.overwritten-store result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width4.txt && grep -R -q 'dse-byte-model: 4 independent 8-bit lanes' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width4-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/overwritten_store_symbolic_width8.cfg --marker probe.dse.overwritten-store --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width8-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width8.txt && grep -q 'formal_status=proved marker=probe.dse.overwritten-store result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width8.txt && grep -R -q 'dse-byte-model: 8 independent 8-bit lanes' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-symbolic-width8-smt"
        )
        add_test(
            NAME formal_check_dse_general_mask_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/partial_overwritten_store_sparse_triple.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-sparse-triple-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-sparse-triple.txt && grep -R -q 'dse-byte-mask: lanes-0-2-3-of-4' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-sparse-triple-smt && grep -R -q '(concat kill3 kill2 original1 kill0)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-sparse-triple-smt"
        )
        add_test(
            NAME formal_check_dse_variable_width_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/partial_overwritten_store_width2.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width2-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width2.txt && grep -R -q 'dse-byte-model: 2 independent 8-bit lanes' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width2-smt && grep -R -q 'dse-byte-mask: lanes-1-of-2' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width2-smt && grep -R -q '(concat kill1 original0)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width2-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/partial_overwritten_store_width3.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width3-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width3.txt && grep -R -q 'dse-byte-model: 3 independent 8-bit lanes' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width3-smt && grep -R -q 'dse-byte-mask: lanes-0-2-of-3' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width3-smt && grep -R -q '(concat kill2 original1 kill0)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width3-smt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/partial_overwritten_store_width8.cfg --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width8-smt > ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width8.txt && grep -R -q 'dse-byte-model: 8 independent 8-bit lanes' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width8-smt && grep -R -q 'dse-byte-mask: lanes-1-3-5-of-8' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width8-smt && grep -R -q '(concat original7 original6 kill5 original4 kill3 original2 kill1 original0)' ${CMAKE_CURRENT_BINARY_DIR}/formal-memory-width8-smt"
        )
        add_test(
            NAME formal_check_vector_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/vector_add_zero.cfg > ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-add.txt && grep -q 'formal_status=proved marker=probe.vector.add-zero result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-add.txt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/vector_reduction_add_zero.cfg > ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-reduce.txt && grep -q 'formal_status=proved marker=probe.vector.reduction-add-zero result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-reduce.txt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/vector_and_allones.cfg > ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-and.txt && grep -q 'formal_status=proved marker=probe.vector.and-allones result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-and.txt && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/vector_reduction_add_single_lane.cfg > ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-single-reduce.txt && grep -q 'formal_status=proved marker=probe.vector.reduction-add-single-lane result=unsat' ${CMAKE_CURRENT_BINARY_DIR}/formal-vector-single-reduce.txt && $<TARGET_FILE:cv-probe-demo> --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/vector_shuffle_splat.cfg > ${CMAKE_CURRENT_BINARY_DIR}/probe-demo-vector.txt && grep -q '^category=vector$' ${CMAKE_CURRENT_BINARY_DIR}/probe-demo-vector.txt && grep -q 'expected_markers=probe.vector.shuffle-splat' ${CMAKE_CURRENT_BINARY_DIR}/probe-demo-vector.txt"
        )
        add_test(
            NAME formal_check_negative_fixture
            COMMAND
                /bin/sh -c
                "(${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --marker probe.instcombine.add-zero --intent ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/formal_bad_intent.json > ${CMAKE_CURRENT_BINARY_DIR}/formal-negative.txt; test $? -eq 3) && grep -q 'formal_status=failed marker=probe.instcombine.add-zero result=sat' ${CMAKE_CURRENT_BINARY_DIR}/formal-negative.txt && grep -q 'counterexample=' ${CMAKE_CURRENT_BINARY_DIR}/formal-negative.txt"
        )
        add_test(
            NAME formal_check_validation_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --marker probe.instcombine.mul-one > ${CMAKE_CURRENT_BINARY_DIR}/formal-not-triggered.txt && grep -q 'formal_status=not-triggered marker=probe.instcombine.mul-one result=not-triggered' ${CMAKE_CURRENT_BINARY_DIR}/formal-not-triggered.txt && printf '%s\\n' '[{\"marker\":\"probe.instcombine.add-zero\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/formal-malformed-intent.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-formal-check-config.py --z3 ${Z3_EXECUTABLE} --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg --intent ${CMAKE_CURRENT_BINARY_DIR}/formal-malformed-intent.json > ${CMAKE_CURRENT_BINARY_DIR}/formal-malformed.txt; test $? -eq 1) && grep -q 'formal_status=error' ${CMAKE_CURRENT_BINARY_DIR}/formal-malformed.txt"
        )
    endif()
    if(TARGET cv-llvm-probe)
        add_test(NAME llvm_probe_scalar COMMAND cv-llvm-probe --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/add_zero.cfg)
        add_test(NAME llvm_probe_cfg COMMAND cv-llvm-probe --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/nested_diamond.cfg)
        add_test(NAME llvm_probe_memory COMMAND cv-llvm-probe --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/promotable_alloca.cfg)
        add_test(NAME llvm_probe_loop COMMAND cv-llvm-probe --config ${CMAKE_CURRENT_SOURCE_DIR}/examples/counted_loop.cfg)
    endif()
    if(TARGET cv-mine-pass-impl-ir)
        add_test(
            NAME pass_impl_ir_direct_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-impl-ir> --input ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_impl_ir_fixture.ll > ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-direct.json && ${Python3_EXECUTABLE} -c 'import json,sys; g=json.load(open(sys.argv[1])); assert g[\"model\"] == \"llvm-pass-impl-ir-graph-v1\"; assert g[\"functions\"]; assert g[\"basic_blocks\"]; assert g[\"instructions\"]; assert g[\"cfg_edges\"]; assert any(e[\"callee\"] == \"helper\" for e in g[\"call_edges\"]); assert g[\"ssa_def_use_edges\"]; assert g[\"call_argument_edges\"]; assert any(e.get(\"callee\") == \"helper\" and e.get(\"arg_index\") == 0 for e in g[\"call_argument_edges\"]); assert g[\"call_operand_refs\"]; assert any(e.get(\"callee\") == \"helper\" and e.get(\"arg_index\") == 0 and e.get(\"value_kind\") == \"instruction\" for e in g[\"call_operand_refs\"])' ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-direct.json"
        )
        add_test(
            NAME pass_impl_ir_source_wrapper_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-wrapper-db ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-wrapper && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); compiler=sys.argv[3]; f=root/\"tests/fixtures/pass_impl_ir_snippet.cpp\"; out.write_text(json.dumps([{\"directory\":str(root),\"command\":compiler+\" -std=c++17 \"+str(f),\"file\":str(f)}]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-wrapper-db/compile_commands.json ${CMAKE_CXX_COMPILER} && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-impl-ir-from-source.py ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_impl_ir_snippet.cpp --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-wrapper-db/compile_commands.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-wrapper --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir> > ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-wrapper.json && ${Python3_EXECUTABLE} -c 'import json,sys; g=json.load(open(sys.argv[1])); assert g[\"functions\"]; assert g[\"cfg_edges\"]; assert any(e.get(\"demangled_callee\") == \"helper(int)\" for e in g[\"call_edges\"]); assert any(i.get(\"debug_location\",{}).get(\"line\",0) for i in g[\"instructions\"])' ${CMAKE_CURRENT_BINARY_DIR}/pass-impl-ir-wrapper.json"
        )
    endif()
    if(TARGET cv-mine-pass-source-ast AND TARGET cv-mine-pass-impl-ir)
        add_test(
            NAME pass_source_audit_impl_ir_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir-db && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); compiler=sys.argv[3]; f=root/\"tests/fixtures/intent_inference_snippet.cpp\"; out.write_text(json.dumps([{\"directory\":str(root),\"command\":compiler+\" -std=c++17 \"+str(f),\"file\":str(f)}]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir-db/compile_commands.json ${CMAKE_CXX_COMPILER} && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir-db/compile_commands.json --out ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --mine-pass-impl-ir --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir> --z3 ${Z3_EXECUTABLE} --marker probe.instcombine.add-zero ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && ${Python3_EXECUTABLE} -c 'import json,pathlib,sys; out=pathlib.Path(sys.argv[3]); findings=json.load(open(sys.argv[1])); manifest=[json.loads(line) for line in open(sys.argv[2]) if line.strip()]; summary=json.load(open(sys.argv[4])); readiness=json.load(open(sys.argv[5])); assert len(findings) == 3; assert all(\"pass_impl_ir_graph\" not in f for f in findings); assert all(\"pass_impl_ir_graph_ref\" in f and \"pass_impl_ir_slice\" in f and \"pass_impl_ir_intent_check\" in f for f in findings); graph_path=out/findings[0][\"pass_impl_ir_graph_ref\"]; assert graph_path.is_file(); g=json.load(open(graph_path)); assert g[\"model\"] == \"llvm-pass-impl-ir-graph-v1\"; s=findings[0][\"pass_impl_ir_slice\"]; assert s[\"model\"] == \"llvm-pass-impl-ir-slice-v1\"; assert s[\"status\"] == \"matched\"; assert s[\"instructions\"] and s[\"functions\"]; assert s[\"call_edges\"] or s[\"ssa_def_use_edges\"]; assert s[\"call_operand_refs\"]; c=findings[0][\"pass_impl_ir_intent_check\"]; assert c[\"model\"] == \"llvm-pass-impl-ir-intent-check-v1\"; assert c[\"status\"] == \"matched\"; assert c[\"intent_shape\"] == \"predicate-and-rewrite\"; assert c[\"expected_predicate\"] is True and c[\"expected_rewrite\"] is True; assert c[\"predicate_evidence\"] and c[\"rewrite_evidence\"]; assert any(\"m_Zero\" in (e.get(\"demangled_callee\") or e.get(\"callee\") or \"\") or \"match\" in (e.get(\"demangled_callee\") or e.get(\"callee\") or \"\") for e in c[\"predicate_evidence\"]); assert any(\"replaceInstUsesWith\" in (e.get(\"demangled_callee\") or e.get(\"callee\") or \"\") for e in c[\"rewrite_evidence\"]); oe=c[\"rewrite_operand_evidence\"]; assert set(oe) == {\"instruction-subject-to-replace-uses\",\"replacement-to-replace-uses\"}; assert oe[\"instruction-subject-to-replace-uses\"] and oe[\"instruction-subject-to-replace-uses\"][0][\"source_variable\"] == \"I\"; assert oe[\"replacement-to-replace-uses\"] and oe[\"replacement-to-replace-uses\"][0][\"source_variable\"] == \"Op0\"; assert c[\"rewrite_binding_status\"] == \"matched\"; be=c[\"rewrite_binding_evidence\"]; assert be[\"replacement_role\"] == \"preserved-source-operand\"; assert be[\"replacement_symbol\"] == \"Op0\"; assert be[\"source_result\"] == \"Op0\"; incomplete=[f for f in findings if f[\"pass_impl_ir_intent_check\"][\"status\"] == \"source-incomplete\"]; assert len(incomplete) == 1; ic=incomplete[0][\"pass_impl_ir_intent_check\"]; assert ic[\"intent_shape\"] == \"predicate-only\" and ic[\"expected_rewrite\"] is False; assert ic[\"reasons\"] == [\"missing-source-rewrite\",\"empty-then-block\"]; assert manifest[0][\"pass_impl_ir\"] == \"present\"; assert manifest[0][\"pass_impl_ir_graph\"] == findings[0][\"pass_impl_ir_graph_ref\"]; assert manifest[0][\"pass_impl_ir_slice_matched\"] >= 1; assert summary[\"source_rewrites\"][\"rewrite_status\"] == {\"absent\":1,\"found\":2}; assert summary[\"source_rewrites\"][\"rewrite_absent_reason\"] == {\"empty-then-block\":1}; assert summary[\"pass_impl_ir\"][\"slice_status\"] == {\"matched\":3}; assert summary[\"pass_impl_ir\"][\"intent_check_status\"] == {\"matched\":2,\"source-incomplete\":1}; assert summary[\"pass_impl_ir\"][\"intent_check_reasons\"] == {\"empty-then-block\":1,\"missing-source-rewrite\":1}; assert readiness[\"diagnostics\"][\"pass_impl_ir_intent_recommendation\"] == \"improve source rewrite extraction for predicate-only findings\"' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir/findings.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir/source-manifest.jsonl ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir/run-summary.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-impl-ir/real-pass-readiness.json"
        )
        add_test(
            NAME pass_source_audit_globalopt_impl_ir_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-globalopt-impl-ir-db && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); compiler=sys.argv[3]; f=root/\"tests/fixtures/global_dead_initializer_snippet.cpp\"; out.write_text(json.dumps([{\"directory\":str(root),\"command\":compiler+\" -std=c++17 \"+str(f),\"file\":str(f)}]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-globalopt-impl-ir-db/compile_commands.json ${CMAKE_CXX_COMPILER} && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-globalopt-impl-ir-db/compile_commands.json --out ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-globalopt-impl-ir --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --mine-pass-impl-ir --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir> --z3 ${Z3_EXECUTABLE} --marker probe.globalopt.dead-initializer ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/global_dead_initializer_snippet.cpp && ${Python3_EXECUTABLE} -c 'import json,pathlib,sys; out=pathlib.Path(sys.argv[3]); findings=json.load(open(sys.argv[1])); summary=json.load(open(sys.argv[2])); assert len(findings) == 1; f=findings[0]; assert f[\"rewrite_status\"] == \"found\"; assert f[\"rewrite_source\"].startswith(\"GV->setInitializer\"); graph_path=out/f[\"pass_impl_ir_graph_ref\"]; assert graph_path.is_file(); c=f[\"pass_impl_ir_intent_check\"]; assert c[\"status\"] == \"matched\"; assert c[\"intent_shape\"] == \"global-rewrite\"; assert c[\"expected_predicate\"] is True and c[\"expected_rewrite\"] is True; assert c[\"global_rewrite_api\"] == \"setInitializer\"; assert c[\"global_rewrite_evidence\"] and c[\"rewrite_evidence\"]; assert any(\"setInitializer\" in (e.get(\"demangled_callee\") or e.get(\"callee\") or \"\") for e in c[\"global_rewrite_evidence\"]); assert {e.get(\"fact\") for e in c[\"global_safety_evidence\"]} == {\"initializer-dead\",\"local-linkage\",\"no-uses\"}; ir=c[\"global_safety_ir_evidence\"]; assert set(ir) == {\"initializer-dead\",\"local-linkage\",\"no-uses\"}; expected={\"initializer-dead\":\"isGlobalInitializerDead\",\"local-linkage\":\"hasLocalLinkage\",\"no-uses\":\"use_empty\"}; assert all(ir[fact] and any(needle in (e.get(\"demangled_callee\") or e.get(\"callee\") or \"\") for e in ir[fact]) for fact,needle in expected.items()); assert c[\"global_required_safety_facts\"] == [\"initializer-dead\",\"local-linkage\",\"no-uses\"]; assert c[\"global_replacement_kind\"] == \"default-null-initializer\"; assert c[\"global_replacement_expr\"] == \"Constant::getNullValue(GV->getValueType())\"; assert c[\"global_value_type_expr\"] == \"GV->getValueType()\"; repl=c[\"global_replacement_ir_evidence\"]; assert set(repl) == {\"null_factory\",\"value_type\"}; assert repl[\"null_factory\"] and any(\"getNullValue\" in (e.get(\"demangled_callee\") or e.get(\"callee\") or \"\") for e in repl[\"null_factory\"]); assert repl[\"value_type\"] and any(\"getValueType\" in (e.get(\"demangled_callee\") or e.get(\"callee\") or \"\") for e in repl[\"value_type\"]); generic_flow=c[\"rewrite_flow_evidence\"]; assert set(generic_flow) == {\"value-type-to-null-factory\",\"null-factory-to-set-initializer\"}; assert generic_flow[\"value-type-to-null-factory\"] and generic_flow[\"null-factory-to-set-initializer\"]; flow=c[\"global_replacement_flow_evidence\"]; assert set(flow) == {\"value_type_to_null_factory\",\"null_factory_to_set_initializer\"}; assert flow[\"value_type_to_null_factory\"] and any(\"getValueType\" in e.get(\"producer_text\",\"\") and \"getNullValue\" in e.get(\"consumer_text\",\"\") for e in flow[\"value_type_to_null_factory\"]); assert flow[\"null_factory_to_set_initializer\"] and any(\"getNullValue\" in e.get(\"producer_text\",\"\") and \"setInitializer\" in e.get(\"consumer_text\",\"\") for e in flow[\"null_factory_to_set_initializer\"]); assert summary[\"pass_impl_ir\"][\"intent_check_status\"] == {\"matched\":1}; assert summary[\"pass_impl_ir\"][\"intent_check_family_status\"] == {\"globalopt\":{\"matched\":1}}' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-globalopt-impl-ir/findings.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-globalopt-impl-ir/run-summary.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-globalopt-impl-ir"
        )
        add_test(
            NAME pass_source_audit_dse_impl_ir_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dse_pass_impl_ir_audit_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
                --work-dir ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-dse-impl-ir
                --compiler ${CMAKE_CXX_COMPILER}
                --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir>
        )
        add_test(
            NAME pass_impl_ir_slice_absent_fixture
            COMMAND
                ${Python3_EXECUTABLE} -c [=[
import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location("audit", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
source = pathlib.Path(sys.argv[2])
graph = {
    "instructions": [
        {
            "id": "f:i0",
            "function": "f",
            "block": "f:bb0",
            "opcode": "add",
            "debug_location": {"file": str(source), "line": 10},
        }
    ],
    "basic_blocks": [{"id": "f:bb0", "function": "f"}],
    "functions": [{"id": "f", "name": "f"}],
    "cfg_edges": [],
    "call_edges": [],
    "ssa_def_use_edges": [],
}
s = mod.pass_impl_ir_slice_for({"line": 100}, graph, source, 0)
assert s["status"] == "absent"
assert s["reason"] == "no-debug-location-match"

base = {
    "marker": "probe.instcombine.add-zero",
    "predicate_source": "match(Op1, m_Zero())",
    "rewrite_source": "return replaceInstUsesWith(I, Op0)",
    "pass_impl_ir_slice": {
        "model": "llvm-pass-impl-ir-slice-v1",
        "status": "matched",
        "instructions": [{"id": "f:i0", "opcode": "add"}],
        "call_edges": [],
    },
}
c = mod.pass_impl_ir_intent_check_for(base)
assert c["status"] == "mismatch"
assert "missing-predicate-evidence" in c["reasons"]
assert "missing-rewrite-evidence" in c["reasons"]
base["pass_impl_ir_slice"]["call_edges"] = [
    {"from": "f:i1", "callee": "match", "demangled_callee": "llvm::match(llvm::Value*, int)"}
]
p = mod.pass_impl_ir_intent_check_for(base)
assert p["status"] == "partial"
assert "missing-rewrite-evidence" in p["reasons"]
assert p["intent_shape"] == "predicate-and-rewrite"
q = mod.pass_impl_ir_intent_check_for(
    {
        "marker": "probe.instcombine.add-zero",
        "predicate_source": "match(Op1, m_Zero())",
        "rewrite_source": "return replaceInstUsesWith(I, Op0)",
        "pass_impl_ir_slice": {
            "model": "llvm-pass-impl-ir-slice-v1",
            "status": "matched",
            "instructions": [],
            "call_edges": [
                {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                {
                    "from": "f:i2",
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_operand_refs": [],
        },
    }
)
assert q["status"] == "partial"
assert q["rewrite_operand_evidence"] == {
    "instruction-subject-to-replace-uses": [],
    "replacement-to-replace-uses": [],
}
assert "missing-rewrite-operand-flow:replacement-to-replace-uses" in q["reasons"]


def instcombine_source_intent(operation="add", operands=None, result="Op0", guards=None):
    return {
        "before": {
            "shape": "scalar",
            "operation": operation,
            "operands": operands if operands is not None else ["Op0", {"constant": 0}],
        },
        "after": {"result": result},
        "rewrite": {
            "action": "replace-result",
            "api": "replaceInstUsesWith",
            "replacement": "Op0",
        },
        "guards": guards or [],
    }


def instcombine_operand_ref(arg_index, source_variable):
    return {
        "call": "f:i2",
        "arg_index": arg_index,
        "source_variable": source_variable,
        "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
    }


def instcombine_check(
    source_intent=None,
    operand_refs=None,
    marker="probe.instcombine.add-zero",
    predicate_source="match(Op1, m_Zero())",
    rewrite_source="return replaceInstUsesWith(I, Op0)",
):
    return mod.pass_impl_ir_intent_check_for(
        {
            "marker": marker,
            "predicate_source": predicate_source,
            "rewrite_source": rewrite_source,
            "pass_impl_ir_slice": {
                "model": "llvm-pass-impl-ir-slice-v1",
                "status": "matched",
                "instructions": [],
                "call_edges": [
                    {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                    {
                        "from": "f:i2",
                        "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                    },
                ],
                "call_operand_refs": operand_refs or [
                    instcombine_operand_ref(0, "I"),
                    instcombine_operand_ref(1, "Op0"),
                ],
            },
            "source_intent": source_intent if source_intent is not None else instcombine_source_intent(),
        }
    )


r = instcombine_check()
assert r["status"] == "matched"
assert r["rewrite_binding_status"] == "matched"
assert r["rewrite_binding_evidence"]["replacement_role"] == "preserved-source-operand"
assert r["rewrite_binding_evidence"]["replacement_symbol"] == "Op0"
assert r["rewrite_binding_evidence"]["normalized_replacement"] == "Op0"
assert r["rewrite_binding_evidence"]["normalized_source_result"] == "Op0"

s_bad = instcombine_check(instcombine_source_intent(result="Op1"))
assert s_bad["status"] == "mismatch"
assert "replacement-binding-mismatch" in s_bad["reasons"]

mul = instcombine_check(
    instcombine_source_intent("mul", ["Op0", {"constant": 1}], "Op0"),
    marker="probe.instcombine.mul-one",
    predicate_source="match(Op1, m_One())",
    rewrite_source="return replaceInstUsesWith(I, Op0)",
)
assert mul["status"] == "matched"
assert mul["rewrite_binding_evidence"]["replacement_role"] == "preserved-source-operand"

xor = instcombine_check(
    instcombine_source_intent(
        "xor",
        ["Op0", "Op0"],
        {"constant": 0},
    ),
    operand_refs=[
        instcombine_operand_ref(0, "I"),
        {
            "call": "f:i2",
            "arg_index": 1,
            "value_name": "llvm::Constant::getNullValue",
            "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
        },
    ],
    marker="probe.instcombine.xor-self",
    predicate_source="match(V, m_Xor(m_Value(Op0), m_Deferred(Op0)))",
    rewrite_source="return replaceInstUsesWith(I, Constant::getNullValue(0))",
)
assert xor["status"] == "matched"
assert xor["rewrite_binding_evidence"]["replacement_role"] == "constant-replacement"
assert xor["rewrite_binding_evidence"]["normalized_replacement"] == "0"
assert xor["rewrite_binding_evidence"]["normalized_source_result"] == "0"

bad_xor = instcombine_check(
    instcombine_source_intent("xor", ["Op0", "Op0"], {"constant": 0}),
    marker="probe.instcombine.xor-self",
    predicate_source="match(V, m_Xor(m_Value(Op0), m_Deferred(Op0)))",
    rewrite_source="return replaceInstUsesWith(I, Op0)",
)
assert bad_xor["status"] == "mismatch"
assert "replacement-binding-mismatch" in bad_xor["reasons"]

bad_ref = instcombine_check(operand_refs=[instcombine_operand_ref(0, "I"), instcombine_operand_ref(1, "Op1")])
assert bad_ref["status"] == "mismatch"
assert "replacement-binding-mismatch" in bad_ref["reasons"]

guarded = instcombine_check(
    instcombine_source_intent(
        guards=[{"kind": "guaranteed-not-poison", "subject": "Op0"}],
    )
)
assert guarded["status"] == "matched"
assert guarded["rewrite_binding_evidence"]["replacement_qualifiers"] == [
    "guarded-replacement",
    "poison-sensitive-replacement",
]

derived_matched = instcombine_check(
    instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 0}]}),
    rewrite_source="return replaceInstUsesWith(I, Builder.CreateAdd(Op0, Constant::getNullValue(0)))",
)
assert derived_matched["status"] == "matched"
assert derived_matched["rewrite_binding_status"] == "matched"
assert derived_matched["rewrite_binding_evidence"]["replacement_role"] == "derived-expression"
assert derived_matched["rewrite_binding_evidence"]["normalized_replacement"] == "add(Op0,0)"
assert derived_matched["rewrite_binding_evidence"]["normalized_source_result"] == "add(Op0,0)"

derived_bad = instcombine_check(
    instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 0}]}),
    rewrite_source="return replaceInstUsesWith(I, Builder.CreateAdd(Op1, Constant::getNullValue(0)))",
)
assert derived_bad["status"] == "mismatch"
assert "replacement-binding-mismatch" in derived_bad["reasons"]

derived_temp_matched = mod.pass_impl_ir_intent_check_for(
    {
        "marker": "probe.instcombine.add-zero",
        "predicate_source": "match(Op1, m_Zero())",
        "rewrite_source": "Value *New = Builder.CreateAdd(Op0, Constant::getNullValue(0)); return replaceInstUsesWith(I, New)",
        "pass_impl_ir_slice": {
            "model": "llvm-pass-impl-ir-slice-v1",
            "status": "matched",
            "instructions": [],
            "call_edges": [
                {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                {"from": "f:i2", "demangled_callee": "llvm::IRBuilderBase::CreateAdd(llvm::Value*, llvm::Value*)"},
                {
                    "from": "f:i3",
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_argument_edges": [
                {
                    "from": "f:i2",
                    "to": "f:i3",
                    "arg_index": 1,
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_operand_refs": [
                instcombine_operand_ref(0, "I"),
                instcombine_operand_ref(1, "New"),
            ],
        },
        "source_intent": instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 0}]}),
        "source_intent_graph": {
            "rewrite_nodes": [
                {
                    "action": "replace-result",
                    "callee": "replaceInstUsesWith",
                    "replacement": "New",
                    "local_definitions": [
                        {
                            "name": "New",
                            "source": "Builder.CreateAdd(Op0, Constant::getNullValue(0))",
                            "value": {"operation": "add", "operands": ["Op0", {"constant": 0}]},
                        }
                    ],
                }
            ]
        },
    }
)
assert derived_temp_matched["status"] == "matched"
assert derived_temp_matched["rewrite_binding_status"] == "matched"
assert derived_temp_matched["rewrite_binding_evidence"]["replacement_symbol"] == "New"
assert derived_temp_matched["rewrite_binding_evidence"]["replacement_definition_source"] == "Builder.CreateAdd(Op0, Constant::getNullValue(0))"
assert derived_temp_matched["rewrite_binding_evidence"]["normalized_replacement"] == "add(Op0,0)"
assert derived_temp_matched["rewrite_flow_evidence"]["derived-builder-to-replacement"]

derived_temp_bad = mod.pass_impl_ir_intent_check_for(
    {
        "marker": "probe.instcombine.add-zero",
        "predicate_source": "match(Op1, m_Zero())",
        "rewrite_source": "Value *New = Builder.CreateAdd(Op1, Constant::getNullValue(0)); return replaceInstUsesWith(I, New)",
        "pass_impl_ir_slice": {
            "model": "llvm-pass-impl-ir-slice-v1",
            "status": "matched",
            "instructions": [],
            "call_edges": [
                {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                {"from": "f:i2", "demangled_callee": "llvm::IRBuilderBase::CreateAdd(llvm::Value*, llvm::Value*)"},
                {
                    "from": "f:i3",
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_argument_edges": [
                {
                    "from": "f:i2",
                    "to": "f:i3",
                    "arg_index": 1,
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_operand_refs": [
                instcombine_operand_ref(0, "I"),
                instcombine_operand_ref(1, "New"),
            ],
        },
        "source_intent": instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 0}]}),
        "source_intent_graph": {
            "rewrite_nodes": [
                {
                    "action": "replace-result",
                    "callee": "replaceInstUsesWith",
                    "replacement": "New",
                    "local_definitions": [
                        {
                            "name": "New",
                            "source": "Builder.CreateAdd(Op1, Constant::getNullValue(0))",
                            "value": {"operation": "add", "operands": ["Op1", {"constant": 0}]},
                        }
                    ],
                }
            ]
        },
    }
)
assert derived_temp_bad["status"] == "mismatch"
assert "replacement-binding-mismatch" in derived_temp_bad["reasons"]

derived_helper_matched = mod.pass_impl_ir_intent_check_for(
    {
        "marker": "probe.instcombine.add-zero",
        "predicate_source": "match(Op1, m_Zero())",
        "rewrite_source": "Value *New = buildNeutralAdd(Builder, Op0); return replaceInstUsesWith(I, New)",
        "pass_impl_ir_slice": {
            "model": "llvm-pass-impl-ir-slice-v1",
            "status": "matched",
            "instructions": [],
            "call_edges": [
                {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                {"from": "f:i2", "demangled_callee": "buildNeutralAdd(llvm::IRBuilderBase&, llvm::Value*)"},
                {
                    "from": "f:i3",
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_argument_edges": [
                {
                    "from": "f:i2",
                    "to": "f:i3",
                    "arg_index": 1,
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_operand_refs": [
                instcombine_operand_ref(0, "I"),
                instcombine_operand_ref(1, "New"),
            ],
        },
        "source_intent": instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 0}]}),
        "source_intent_graph": {
            "rewrite_nodes": [
                {
                    "action": "replace-result",
                    "callee": "replaceInstUsesWith",
                    "replacement": "New",
                    "local_definitions": [
                        {
                            "name": "New",
                            "source": "buildNeutralAdd(Builder, Op0)",
                            "value": {"operation": "add", "operands": ["Op0", {"constant": 0}]},
                            "helper_summary": {
                                "name": "buildNeutralAdd",
                                "status": "complete",
                                "return_source": "Builder.CreateAdd(Kept, Constant::getNullValue(0))",
                                "value": {"operation": "add", "operands": ["Op0", {"constant": 0}]},
                            },
                        }
                    ],
                }
            ]
        },
    }
)
assert derived_helper_matched["status"] == "matched"
assert derived_helper_matched["rewrite_binding_evidence"]["replacement_helper"] == "buildNeutralAdd"
assert derived_helper_matched["rewrite_binding_evidence"]["normalized_replacement"] == "add(Op0,0)"
assert derived_helper_matched["rewrite_flow_evidence"]["derived-builder-to-replacement"]

derived_helper_bad = mod.pass_impl_ir_intent_check_for(
    {
        "marker": "probe.instcombine.add-zero",
        "predicate_source": "match(Op1, m_Zero())",
        "rewrite_source": "Value *New = buildNeutralAdd(Builder, Op1); return replaceInstUsesWith(I, New)",
        "pass_impl_ir_slice": {
            "model": "llvm-pass-impl-ir-slice-v1",
            "status": "matched",
            "instructions": [],
            "call_edges": [
                {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                {"from": "f:i2", "demangled_callee": "buildNeutralAdd(llvm::IRBuilderBase&, llvm::Value*)"},
                {
                    "from": "f:i3",
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_argument_edges": [
                {
                    "from": "f:i2",
                    "to": "f:i3",
                    "arg_index": 1,
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_operand_refs": [
                instcombine_operand_ref(0, "I"),
                instcombine_operand_ref(1, "New"),
            ],
        },
        "source_intent": instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 0}]}),
        "source_intent_graph": {
            "rewrite_nodes": [
                {
                    "action": "replace-result",
                    "callee": "replaceInstUsesWith",
                    "replacement": "New",
                    "local_definitions": [
                        {
                            "name": "New",
                            "source": "buildNeutralAdd(Builder, Op1)",
                            "value": {"operation": "add", "operands": ["Op1", {"constant": 0}]},
                            "helper_summary": {
                                "name": "buildNeutralAdd",
                                "status": "complete",
                                "return_source": "Builder.CreateAdd(Kept, Constant::getNullValue(0))",
                                "value": {"operation": "add", "operands": ["Op1", {"constant": 0}]},
                            },
                        }
                    ],
                }
            ]
        },
    }
)
assert derived_helper_bad["status"] == "mismatch"
assert "replacement-binding-mismatch" in derived_helper_bad["reasons"]

derived_helper_partial = mod.pass_impl_ir_intent_check_for(
    {
        "marker": "probe.instcombine.add-zero",
        "predicate_source": "match(Op1, m_Zero())",
        "rewrite_source": "Value *New = unknownBuildNeutralAdd(Builder, Op0); return replaceInstUsesWith(I, New)",
        "pass_impl_ir_slice": {
            "model": "llvm-pass-impl-ir-slice-v1",
            "status": "matched",
            "instructions": [],
            "call_edges": [
                {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                {
                    "from": "f:i3",
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_operand_refs": [
                instcombine_operand_ref(0, "I"),
                instcombine_operand_ref(1, "New"),
            ],
        },
        "source_intent": instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 0}]}),
        "source_intent_graph": {
            "rewrite_nodes": [
                {
                    "action": "replace-result",
                    "callee": "replaceInstUsesWith",
                    "replacement": "New",
                    "local_definitions": [
                        {
                            "name": "New",
                            "source": "unknownBuildNeutralAdd(Builder, Op0)",
                            "helper_summary": {
                                "name": "unknownBuildNeutralAdd",
                                "status": "unsupported",
                                "reason": "missing-helper-body",
                            },
                        }
                    ],
                }
            ]
        },
    }
)
assert derived_helper_partial["status"] == "partial"
assert "missing-rewrite-binding-evidence" in derived_helper_partial["reasons"]

derived = instcombine_check(
    instcombine_source_intent(result={"operation": "add", "operands": ["Op0", {"constant": 1}]})
)
assert derived["status"] == "partial"
assert derived["rewrite_binding_status"] == "partial"
assert derived["rewrite_binding_evidence"]["replacement_role"] == "derived-expression"
assert "missing-rewrite-binding-evidence" in derived["reasons"]

t_missing = mod.pass_impl_ir_intent_check_for(
    {
        "marker": "probe.instcombine.add-zero",
        "predicate_source": "match(Op1, m_Zero())",
        "rewrite_source": "return replaceInstUsesWith(I, Op0)",
        "pass_impl_ir_slice": {
            "model": "llvm-pass-impl-ir-slice-v1",
            "status": "matched",
            "instructions": [],
            "call_edges": [
                {"from": "f:i1", "demangled_callee": "llvm::match(llvm::Value*, int)"},
                {
                    "from": "f:i2",
                    "demangled_callee": "llvm::replaceInstUsesWith(llvm::Instruction&, llvm::Value*)",
                },
            ],
            "call_operand_refs": [
                instcombine_operand_ref(0, "I"),
                instcombine_operand_ref(1, "Op0"),
            ],
        },
    }
)
assert t_missing["status"] == "partial"
assert t_missing["rewrite_binding_status"] == "partial"
assert "missing-rewrite-binding-evidence" in t_missing["reasons"]


def src(replacement_kind="default-null-initializer"):
    replacement_expr = (
        "Constant::getNullValue(GV->getValueType())"
        if replacement_kind == "default-null-initializer"
        else "SomeOtherValue"
    )
    return {
        "required_safety_facts": ["initializer-dead", "local-linkage", "no-uses"],
        "missing_safety_facts": [],
        "safety_provenance_status": "complete",
        "safety_provenance": [
            {"fact": "initializer-dead"},
            {"fact": "local-linkage"},
            {"fact": "no-uses"},
        ],
        "rewrite_nodes": [
            {
                "action": "remove-global-initializer-if-dead-v1",
                "replacement_kind": replacement_kind,
                "replacement_expr": replacement_expr,
                "value_type_expr": "GV->getValueType()",
            }
        ],
    }


def edge(index, callee):
    return {"from": f"f:i{index}", "demangled_callee": callee}


set_init = [edge(0, "llvm::GlobalVariable::setInitializer(llvm::Constant*)")]
safety = [
    edge(1, "llvm::isGlobalInitializerDead(llvm::GlobalVariable*)"),
    edge(2, "llvm::GlobalVariable::hasLocalLinkage()"),
    edge(3, "llvm::GlobalVariable::use_empty()"),
]
replacement = [
    edge(4, "llvm::GlobalVariable::getValueType()"),
    edge(5, "llvm::Constant::getNullValue(llvm::Type*)"),
]


flow_edges = [
    {
        "from": "f:i4",
        "to": "f:i5",
        "kind": "ir-call-argument",
        "arg_index": 0,
        "demangled_callee": "llvm::Constant::getNullValue(llvm::Type*)",
    },
    {
        "from": "f:i5",
        "to": "f:i0",
        "kind": "ir-call-argument",
        "arg_index": 1,
        "demangled_callee": "llvm::GlobalVariable::setInitializer(llvm::Constant*)",
    },
]

contract_result = mod.pass_impl_ir_rewrite_flow_contract_evidence(
    {"call_edges": set_init + safety + replacement, "call_argument_edges": flow_edges},
    [
        {
            "name": "value-type-to-null-factory",
            "producer_needles": ["getValueType"],
            "consumer_needles": ["getNullValue"],
            "consumer_arg_index": 0,
            "required": True,
        },
        {
            "name": "null-factory-to-set-initializer",
            "producer_needles": ["getNullValue"],
            "consumer_needles": ["setInitializer"],
            "consumer_arg_index": 1,
            "required": True,
        },
    ],
)
assert contract_result["missing_rewrite_flows"] == []
assert set(contract_result["rewrite_flow_evidence"]) == {
    "value-type-to-null-factory",
    "null-factory-to-set-initializer",
}
assert contract_result["rewrite_flow_evidence"]["value-type-to-null-factory"]
assert contract_result["rewrite_flow_evidence"]["null-factory-to-set-initializer"]


def global_check(call_edges, call_argument_edges=None, source_intent_graph=None):
    return mod.pass_impl_ir_intent_check_for(
        {
            "marker": "probe.globalopt.dead-initializer",
            "rewrite_status": "found",
            "rewrite_source": "GV->setInitializer(Constant::getNullValue(GV->getValueType()))",
            "pass_impl_ir_slice": {
                "model": "llvm-pass-impl-ir-slice-v1",
                "status": "matched",
                "instructions": [],
                "call_edges": call_edges,
                "call_argument_edges": call_argument_edges or [],
            },
            "source_intent_graph": source_intent_graph or src(),
        }
    )


g = global_check(set_init + safety[:2] + replacement, flow_edges)
assert g["status"] == "partial"
assert g["intent_shape"] == "global-rewrite"
assert g["global_rewrite_api"] == "setInitializer"
assert g["reasons"] == ["missing-global-safety-evidence:no-uses"]

h = global_check(safety + replacement, flow_edges[:1])
assert h["status"] == "partial"
assert h["reasons"] == [
    "missing-rewrite-evidence",
    "missing-global-replacement-flow:null-factory-to-set-initializer",
]
assert set(h["global_safety_ir_evidence"]) == {"initializer-dead", "local-linkage", "no-uses"}

i = global_check(set_init + safety + [replacement[0]])
assert i["status"] == "partial"
assert "missing-global-replacement-evidence:null-factory" in i["reasons"]

j = global_check(set_init + safety + [replacement[1]])
assert j["status"] == "partial"
assert "missing-global-replacement-evidence:value-type" in j["reasons"]

m = global_check(set_init + safety + replacement)
assert m["status"] == "partial"
assert "missing-global-replacement-flow:null-factory-to-set-initializer" in m["reasons"]

k = global_check(set_init + safety + replacement, flow_edges, src("nonnull-initializer"))
assert k["status"] == "mismatch"
assert k["reasons"] == ["unsupported-global-replacement-kind"]
]=]
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp
        )
    endif()
    if(TARGET cv-mine-pass-source-ast)
        add_test(
            NAME ast_pass_source_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.instcombine.add-zero --require-marker probe.instcombine.mul-one --require-marker probe.instcombine.xor-self --require-marker probe.dce.dead-instruction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.json && grep -q '\"finding_source\":\"ast\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.json && grep -q '\"predicate_source\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.json && grep -q '\"rewrite_source\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.json && grep -q '\"source_intent\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.json && grep -q '\"source_intent_graph\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; findings=json.load(open(sys.argv[1])); assert any(f.get(\"rewrite_status\") == \"found\" and f.get(\"rewrite_source\") for f in findings); missing=[f for f in findings if f.get(\"line\") == 54 and f.get(\"marker\") == \"probe.instcombine.add-zero\"]; assert missing and missing[0][\"rewrite_status\"] == \"absent\" and missing[0][\"rewrite_absent_reason\"] == \"empty-then-block\" and missing[0][\"rewrite_search_scope\"] == \"then-block-v1\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.json"
        )
        add_test(
            NAME ast_pass_source_miner_jsonl_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --format jsonl --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.jsonl && grep -q '\"finding_source\":\"ast\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-mined-pass-findings.jsonl && ($<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.missing ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-missing.json 2> ${CMAKE_CURRENT_BINARY_DIR}/ast-missing.err; test $? -eq 1) && grep -q 'missing required markers: probe.missing' ${CMAKE_CURRENT_BINARY_DIR}/ast-missing.err"
        )
        add_test(
            NAME ast_dce_globalopt_guard_provenance_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dce_unused_alloca_folds.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/ast-dce-unused-alloca.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); by={r[\"function\"]: r for r in data if r.get(\"marker\") == \"probe.cleanup.unused-alloca\"}; expected={\"eraseUnusedAlloca\",\"eraseUserEmptyAlloca\",\"eraseHasNUsesZeroAlloca\",\"eraseUsersEmptyAlloca\",\"eraseNotHasNUsesOrMoreAlloca\"}; assert expected <= set(by), by.keys(); assert \"erasePositiveHasNUsesOrMoreAlloca\" not in by, by.keys(); bad={name:{g.get(\"kind\") for g in by[name].get(\"source_intent_graph\",{}).get(\"guards\", [])} for name in expected if {g.get(\"kind\") for g in by[name].get(\"source_intent_graph\",{}).get(\"guards\", [])} != {\"unused-alloca\"}}; assert not bad, bad' ${CMAKE_CURRENT_BINARY_DIR}/ast-dce-unused-alloca.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/global_dead_initializer_snippet.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/ast-globalopt-guards.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); assert not [r for r in data if r.get(\"marker\") == \"probe.cleanup.unused-alloca\"], data; rows=[r for r in data if r.get(\"marker\") == \"probe.globalopt.dead-initializer\"]; assert rows, data; graph={g.get(\"kind\") for r in rows for g in r.get(\"source_intent_graph\",{}).get(\"guards\", [])}; intent={g.get(\"kind\") for r in rows for g in r.get(\"source_intent\",{}).get(\"guards\", [])}; assert graph == {\"dead-global-initializer\"}, graph; assert intent == {\"dead-global-initializer\"}, intent' ${CMAKE_CURRENT_BINARY_DIR}/ast-globalopt-guards.json"
        )
        add_test(
            NAME ast_unused_alloca_intent_validation_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dce_unused_alloca_folds.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/ast-unused-alloca-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-unused-alloca-findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-unused-alloca-intents.jsonl --require-marker probe.cleanup.unused-alloca && if command -v ${Z3_EXECUTABLE} >/dev/null 2>&1; then ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-unused-alloca-intents.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-unused-alloca-validated.jsonl --z3 ${Z3_EXECUTABLE} && ${Python3_EXECUTABLE} -c 'import json,sys; rows=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; expected={18,23,28,33,38}; by={r[\"line\"]: r for r in rows if r.get(\"marker\") == \"probe.cleanup.unused-alloca\"}; assert expected <= set(by), by.keys(); assert 43 not in by, by.keys(); bad={line:(r.get(\"confidence\"), r.get(\"proof_status\"), r.get(\"promotion_status\"), r.get(\"evidence\",{}).get(\"formal_inference\"), r.get(\"evidence\",{}).get(\"source_intent_graph_lowering\"), r.get(\"evidence\",{}).get(\"formal_parameters\",{}).get(\"source_intent_graph.status\"), r.get(\"evidence\",{}).get(\"formal_parameters\",{}).get(\"source_intent_graph.consistency\"), r.get(\"intent_candidate\",{}).get(\"formal\",{}).get(\"domain\")) for line,r in by.items() if line in expected and not (r.get(\"confidence\") == \"high\" and r.get(\"proof_status\") == \"proved\" and r.get(\"promotion_status\") == \"ready\" and r.get(\"evidence\",{}).get(\"formal_inference\") == \"source-derived-intent-graph\" and r.get(\"evidence\",{}).get(\"source_intent_graph_lowering\") == \"formal-ir\" and r.get(\"evidence\",{}).get(\"formal_parameters\",{}).get(\"source_intent_graph.status\") == \"complete\" and r.get(\"evidence\",{}).get(\"formal_parameters\",{}).get(\"source_intent_graph.consistency\") == \"ok\" and r.get(\"intent_candidate\",{}).get(\"formal\",{}).get(\"domain\") == \"memory-bv32\")}; assert not bad, bad; assert by[52].get(\"evidence\",{}).get(\"formal_inference\") != \"source-derived-intent-graph\", by[52].get(\"evidence\",{})' ${CMAKE_CURRENT_BINARY_DIR}/ast-unused-alloca-validated.jsonl; else echo 'ast_unused_alloca_intent_validation_fixture: z3 not present, skipped validation'; fi"
        )
        add_test(
            NAME ast_rewrite_absent_reason_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/rewrite_absent_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-rewrite-absent.json && ${Python3_EXECUTABLE} -c 'import json,sys; findings=json.load(open(sys.argv[1])); assert len(findings) == 1; f=findings[0]; assert f[\"rewrite_status\"] == \"absent\"; assert f[\"rewrite_absent_reason\"] == \"no-known-rewrite-call\"; assert f[\"rewrite_search_scope\"] == \"then-block-v1\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-rewrite-absent.json"
        )
        add_test(
            NAME ast_guard_semantics_catalog_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --guard-semantics ${CMAKE_CURRENT_SOURCE_DIR}/constraints/guard_semantics.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_intent_matcher_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics/findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; findings=json.load(open(sys.argv[1])); guards=[g for f in findings for g in f.get(\"source_intent\",{}).get(\"guards\",[])]; by_kind={g.get(\"kind\"):g for g in guards if g.get(\"kind\") in {\"guaranteed-not-poison\",\"known-nonzero\",\"one-use\"}}; assert by_kind[\"guaranteed-not-poison\"][\"role\"] == \"modeled-side-condition\"; assert by_kind[\"guaranteed-not-poison\"][\"proof_effect\"] == \"assumes-subject-not-poison\"; assert by_kind[\"guaranteed-not-poison\"][\"formal_effect\"] == \"not-poison-assumption\"; assert by_kind[\"known-nonzero\"][\"role\"] == \"modeled-side-condition\"; assert by_kind[\"known-nonzero\"][\"proof_effect\"] == \"assumes-subject-nonzero\"; assert by_kind[\"known-nonzero\"][\"formal_effect\"] == \"not-eq-zero-assumption\"; assert by_kind[\"one-use\"][\"role\"] == \"modeled-side-condition\"; assert by_kind[\"one-use\"][\"proof_effect\"] == \"structural-precondition-no-smt-effect\"; assert by_kind[\"one-use\"][\"formal_effect\"] == \"structural-only\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics/findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); json.dump([x for x in data if x.get(\"kind\") != \"known-nonzero\"], open(sys.argv[2], \"w\"))' ${CMAKE_CURRENT_SOURCE_DIR}/constraints/guard_semantics.json ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics/missing-known-nonzero.json && ($<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --guard-semantics ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics/missing-known-nonzero.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_intent_matcher_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics/missing.json 2> ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics/missing.err; test $? -eq 1) && grep -q 'guard semantics missing kind: known-nonzero' ${CMAKE_CURRENT_BINARY_DIR}/ast-guard-semantics/missing.err"
        )
        add_test(
            NAME ast_vector_source_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.vector.add-zero --require-marker probe.vector.mul-one --require-marker probe.vector.xor-self --require-marker probe.vector.shuffle-identity --require-marker probe.vector.shuffle-splat --require-marker probe.vector.extract-insert --require-marker probe.vector.reduction-add-zero --require-marker probe.vector.sub-zero --require-marker probe.vector.or-zero --require-marker probe.vector.and-allones --require-marker probe.vector.smin --require-marker probe.vector.smax --require-marker probe.vector.umin --require-marker probe.vector.umax --require-marker probe.vector.abs --require-marker probe.vector.insert-extract-identity --require-marker probe.vector.reduction-add-single-lane --require-marker probe.vector.scalable.add-zero --require-marker probe.vector.scalable.mul-one --require-marker probe.vector.scalable.xor-self --require-marker probe.vector.scalable.sub-zero --require-marker probe.vector.scalable.or-zero --require-marker probe.vector.scalable.and-allones --require-marker probe.vector.scalable.reduction-add-zero ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q '\"finding_source\":\"ast\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q 'probe.vector.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q 'probe.vector.smin' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q 'probe.vector.abs' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q 'probe.vector.reduction-add-single-lane' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q 'probe.vector.scalable.add-zero' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q '\"semantic_facts\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q '\"source_intent\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json && grep -q '\"source_intent_graph\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-findings.json"
        )
        add_test(
            NAME ast_slp_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; assert r[\"finding_source\"] == \"ast\"; assert r[\"predicate_kind\"] == \"transaction\"; assert tx[\"model\"] == \"optimization-transaction-v1\"; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert tx[\"operand_lane_mappings\"][\"lhs\"][\"pack_source\"][\"kind\"] == \"pack-helper-call\"; assert len(tx[\"scalar_lane_pairs\"]) == 4' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_source_slice_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-slice-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; sl=tx[\"source_slice\"]; spg=tx[\"source_program_graph\"]; assert sl[\"model\"] == \"ast-rewrite-slice-v1\"; assert sl[\"status\"] == \"complete\"; assert sl[\"rewrite_site\"][\"function\"] == \"emitVectorOp\"; assert {x[\"role\"] for x in sl[\"backward_slice\"]} >= {\"candidate-tree\",\"legality\",\"profitability\",\"vector-emission\"}; assert sl[\"forward_slice\"][0][\"role\"] == \"scalar-replacement\"; assert any(e[\"caller\"] == \"emitVectorOp\" and e[\"callee\"] == \"buildLHS\" for e in tx[\"call_graph\"]); summaries={s[\"function\"]:s for s in tx[\"helper_summaries\"]}; assert \"emitVectorOp\" in summaries and any(c[\"callee\"] == \"CreateAdd\" for c in summaries[\"emitVectorOp\"][\"calls\"]); assert summaries[\"emitVectorOp\"][\"source_range\"][\"begin_line\"] > 0; assert spg[\"model\"] == \"llvm-pass-source-program-graph-v1\"; assert spg[\"cfg_precision\"] == \"clang-cfg-block-v1\"; assert spg[\"cfg_blocks\"]; assert any(e[\"kind\"] == \"clang-cfg-successor\" and \":bb\" in e[\"from\"] and \":bb\" in e[\"to\"] for e in spg[\"cfg_edges\"]); assert spg[\"dfg_precision\"] == \"clang-ast-decl-use-v1\"; assert spg[\"interprocedural_dfg\"] is True; nodes=spg[\"nodes\"]; lhs=next(n for n in nodes if n.get(\"function\") == \"emitVectorOp\" and \"Value *LHS\" in n.get(\"source\", \"\")); rhs=next(n for n in nodes if n.get(\"function\") == \"emitVectorOp\" and \"Value *RHS\" in n.get(\"source\", \"\")); ret=next(n for n in nodes if n.get(\"function\") == \"emitVectorOp\" and \"CreateAdd(LHS, RHS)\" in n.get(\"source\", \"\")); entry=next(n for n in nodes if n[\"id\"] == \"emitVectorOp:entry\"); build_lhs_ret=next(n for n in nodes if n.get(\"function\") == \"buildLHS\" and n.get(\"kind\") == \"return\"); build_rhs_ret=next(n for n in nodes if n.get(\"function\") == \"buildRHS\" and n.get(\"kind\") == \"return\"); assert \"LHS\" in lhs.get(\"defs\", []) and \"RHS\" in rhs.get(\"defs\", []); assert {\"LHS\", \"RHS\"} <= set(ret.get(\"uses\", [])); assert any(e[\"from\"] == lhs[\"id\"] and e[\"to\"] == ret[\"id\"] and e[\"symbol\"] == \"LHS\" and e[\"kind\"] == \"clang-ast-decl-use\" for e in spg[\"dfg_edges\"]); assert any(e[\"from\"] == rhs[\"id\"] and e[\"to\"] == ret[\"id\"] and e[\"symbol\"] == \"RHS\" and e[\"kind\"] == \"clang-ast-decl-use\" for e in spg[\"dfg_edges\"]); assert any(e[\"from\"] == entry[\"id\"] and e[\"to\"] == \"buildLHS:entry\" and e[\"symbol\"] == \"Entry\" and e.get(\"parameter\") == \"Entry\" and e[\"kind\"] == \"interproc-argument\" for e in spg[\"dfg_edges\"]); assert any(e[\"from\"] == entry[\"id\"] and e[\"to\"] == \"buildRHS:entry\" and e[\"symbol\"] == \"Entry\" and e.get(\"parameter\") == \"Entry\" and e[\"kind\"] == \"interproc-argument\" for e in spg[\"dfg_edges\"]); assert any(e[\"from\"] == build_lhs_ret[\"id\"] and e[\"to\"] == lhs[\"id\"] and e[\"symbol\"] == \"LHS\" and e[\"kind\"] == \"interproc-return\" for e in spg[\"dfg_edges\"]); assert any(e[\"from\"] == build_rhs_ret[\"id\"] and e[\"to\"] == rhs[\"id\"] and e[\"symbol\"] == \"RHS\" and e[\"kind\"] == \"interproc-return\" for e in spg[\"dfg_edges\"]); assert any(e[\"caller\"] == \"emitVectorOp\" and e[\"callee\"] == \"buildLHS\" and e[\"kind\"] == \"call\" and e.get(\"assigned_symbol\") == \"LHS\" for e in spg[\"call_edges\"])' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-slice-findings.json"
        )
        add_test(
            NAME ast_slp_source_dfg_assignment_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_source_dfg_assignment_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-assignment-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; spg=tx[\"source_program_graph\"]; assert spg[\"dfg_precision\"] == \"clang-ast-decl-use-v1\"; assert spg[\"interprocedural_dfg\"] is True; nodes=spg[\"nodes\"]; init=next(n for n in nodes if n.get(\"function\") == \"emitVectorOp\" and \"Value *Tmp\" in n.get(\"source\", \"\")); assign=next(n for n in nodes if n.get(\"function\") == \"emitVectorOp\" and n.get(\"source\", \"\").startswith(\"Tmp =\")); ret=next(n for n in nodes if n.get(\"function\") == \"emitVectorOp\" and \"CreateAdd(Tmp, Tmp)\" in n.get(\"source\", \"\")); build_rhs_ret=next(n for n in nodes if n.get(\"function\") == \"buildRHS\" and n.get(\"kind\") == \"return\"); assert \"Tmp\" in init.get(\"defs\", []) and \"Tmp\" in assign.get(\"defs\", []); assert \"Tmp\" in ret.get(\"uses\", []); assert any(e[\"from\"] == assign[\"id\"] and e[\"to\"] == ret[\"id\"] and e[\"symbol\"] == \"Tmp\" and e[\"kind\"] == \"clang-ast-decl-use\" for e in spg[\"dfg_edges\"]); assert not any(e[\"from\"] == init[\"id\"] and e[\"to\"] == ret[\"id\"] and e[\"symbol\"] == \"Tmp\" for e in spg[\"dfg_edges\"]); assert any(e[\"from\"] == build_rhs_ret[\"id\"] and e[\"to\"] == assign[\"id\"] and e[\"symbol\"] == \"Tmp\" and e[\"kind\"] == \"interproc-return\" for e in spg[\"dfg_edges\"])' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-assignment-findings.json"
        )
        add_test(
            NAME ast_slp_source_dfg_access_path_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_source_dfg_access_path_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-access-path-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; spg=tx[\"source_program_graph\"]; assert spg[\"dfg_precision\"] == \"clang-ast-decl-use-v1\"; nodes=spg[\"nodes\"]; s0=next(n for n in nodes if n.get(\"function\") == \"buildLHS\" and \"Value *S0\" in n.get(\"source\", \"\")); s1=next(n for n in nodes if n.get(\"function\") == \"buildLHS\" and \"Value *S1\" in n.get(\"source\", \"\")); entry=next(n for n in nodes if n[\"id\"] == \"buildLHS:entry\"); assert \"Entry.Scalars[0]\" in s0.get(\"uses\", []); assert \"Entry.Scalars[ReorderMask[1]]\" in s1.get(\"uses\", []); assert \"Entry.Scalars\" in s0.get(\"uses\", []); assert \"Entry\" in s0.get(\"uses\", []); assert \"ReorderMask[1]\" in s1.get(\"uses\", []); assert any(e[\"from\"] == entry[\"id\"] and e[\"to\"] == s0[\"id\"] and e[\"symbol\"] == \"Entry.Scalars[0]\" and e[\"kind\"] == \"clang-ast-decl-use\" for e in spg[\"dfg_edges\"]); assert any(e[\"from\"] == entry[\"id\"] and e[\"to\"] == s1[\"id\"] and e[\"symbol\"] == \"Entry.Scalars[ReorderMask[1]]\" and e[\"kind\"] == \"clang-ast-decl-use\" for e in spg[\"dfg_edges\"])' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-access-path-findings.json"
        )
        add_test(
            NAME ast_slp_source_dfg_access_path_provenance_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_source_dfg_access_path_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-access-path-provenance-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_dfg_access_path_provenance_fixture.py ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-access-path-provenance-findings.json"
        )
        add_test(
            NAME source_graph_contract_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_graph_contract_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME source_program_graph_contract_gate_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_program_graph_contract_gate_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
                --work-dir ${CMAKE_CURRENT_BINARY_DIR}/source-program-graph-contract-gate
                --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
        )
        add_test(
            NAME dse_analysis_facts_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dse_analysis_facts_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
                --work-dir ${CMAKE_CURRENT_BINARY_DIR}/dse-analysis-facts
                --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                --replay $<TARGET_FILE:cv-replay>
                --reducer $<TARGET_FILE:cv-reduce-config>
        )
        add_test(
            NAME dse_lane_mask_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dse_lane_mask_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME dse_real_source_audit_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/dse_real_source_audit_fixture.py
                --repo ${CMAKE_CURRENT_SOURCE_DIR}
                --work-dir ${CMAKE_CURRENT_BINARY_DIR}/dse-real-source-audit
                --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                --replay $<TARGET_FILE:cv-replay>
                --reducer $<TARGET_FILE:cv-reduce-config>
        )
        add_test(
            NAME ast_slp_source_dfg_memory_access_path_provenance_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_memory_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-memory-access-path-findings.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_store_sink_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-store-access-path-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_dfg_memory_access_path_provenance_fixture.py ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-memory-access-path-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-source-dfg-store-access-path-findings.json"
        )
        add_test(
            NAME ast_slp_complex_region_slice_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_complex_region_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-region-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"root\"].startswith(\"Value *materializeVector\"); assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert tx[\"operand_lane_mappings\"][\"lhs\"][\"pack_source\"][\"function\"] == \"buildLeftPack\"; sl=tx[\"source_slice\"]; assert sl[\"control_root_function\"] == \"vectorizeTree\"; reachable=set(sl[\"reachable_functions\"]); assert {\"buildTree_rec\",\"isTreeLegal\",\"isTreeProfitable\",\"materializeVector\",\"buildLeftPack\",\"buildRightPack\",\"collectOperand\",\"commitVectorizedUses\",\"vectorizeTree\"} <= reachable; roles={x[\"function\"]:x[\"role\"] for x in sl[\"predicate_expansion\"]}; assert roles[\"buildTree_rec\"] == \"candidate-tree\"; assert roles[\"isTreeLegal\"] == \"legality\"; assert roles[\"isTreeProfitable\"] == \"profitability\"; assert sl[\"completeness\"] == {\"has_candidate\":True,\"has_legality\":True,\"has_profitability\":True,\"has_emitter\":True,\"has_replacement\":True,\"has_lane_mapping\":True}; contract=sl[\"contract\"]; assert contract[\"status\"] == \"complete\" and contract[\"missing_roles\"] == []; assert set(contract[\"reachable_roles\"]) >= {\"candidate-tree\",\"legality\",\"profitability\",\"vector-emission\",\"scalar-replacement\",\"lane-mapping\"}; assert any(p[\"role\"] == \"legality\" and p[\"path\"] == [\"vectorizeTree\",\"isTreeLegal\"] for p in contract[\"role_paths\"]); assert any(e[\"caller\"] == \"materializeVector\" and e[\"callee\"] == \"buildLeftPack\" for e in tx[\"slice_call_graph\"]); assert any(e[\"caller\"] == \"buildLeftPack\" and e[\"callee\"] == \"collectOperand\" for e in tx[\"slice_call_graph\"]); summaries={s[\"function\"]:s for s in tx[\"slice_helper_summaries\"]}; assert summaries[\"collectOperand\"][\"called_functions\"] == [\"buildPack\"]; assert any(c[\"callee\"] == \"CreateAdd\" for c in summaries[\"materializeVector\"][\"calls\"]); assert {x[\"role\"] for x in sl[\"backward_slice\"]} >= {\"candidate-tree\",\"legality\",\"profitability\",\"vector-emission\"}' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-region-findings.json"
        )
        add_test(
            NAME ast_slp_complex_unknown_legality_slice_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_complex_unknown_legality_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-unknown-legality-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"failed\"; assert \"missing-expanded-legality\" in tx[\"consistency_errors\"]; assert \"missing-contract-role:legality\" in tx[\"consistency_errors\"]; sl=tx[\"source_slice\"]; assert sl[\"control_root_function\"] == \"vectorizeTree\"; assert sl[\"completeness\"][\"has_legality\"] is False; assert sl[\"contract\"][\"status\"] == \"failed\" and sl[\"contract\"][\"missing_roles\"] == [\"legality\"]; roles={x[\"function\"]:x[\"role\"] for x in sl[\"predicate_expansion\"]}; assert roles[\"opaqueVectorLegality\"] == \"unknown-helper\"; assert roles[\"buildTree_rec\"] == \"candidate-tree\"; assert roles[\"isTreeProfitable\"] == \"profitability\"; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-unknown-legality-findings.json"
        )
        add_test(
            NAME ast_slp_source_slice_contract_verifier_clean_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_complex_region_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-clean-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-source-slice-contract.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-clean-findings.json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-clean-verification.json --report ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-clean-verification.txt --require-clean && grep -q '^  passed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-clean-verification.txt && grep -q 'source-graph:interprocedural-dfg: 1' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-clean-verification.txt && grep -q '^  none$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-clean-verification.txt"
        )
        add_test(
            NAME ast_slp_source_slice_contract_verifier_known_failed_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_complex_unknown_legality_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-source-slice-contract.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-findings.json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-verification.json --report ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-verification.txt --require-clean && grep -q '^  passed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-verification.txt && grep -q 'predicate-expands-legality: 1' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-verification.txt && grep -q 'role-reachability:legality: 1' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-verification.txt && grep -q '^  none$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-known-failed-verification.txt"
        )
        add_test(
            NAME ast_slp_source_slice_contract_verifier_tampered_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_complex_unknown_legality_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-source.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); data[0][\"optimization_transaction\"][\"source_slice\"][\"contract\"][\"status\"]=\"complete\"; json.dump(data, open(sys.argv[2], \"w\"), sort_keys=True)' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-source.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-findings.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-source-slice-contract.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-findings.json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-verification.json --report ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-verification.txt --require-clean; test $? -eq 1) && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-verification.txt && grep -q 'status-mismatch: 1' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-verification.txt"
        )
        add_test(
            NAME ast_slp_source_slice_contract_verifier_tampered_graph_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-source.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); graph=data[0][\"optimization_transaction\"][\"source_program_graph\"]; graph[\"dfg_edges\"]=[e for e in graph[\"dfg_edges\"] if e.get(\"kind\") != \"interproc-return\"]; json.dump(data, open(sys.argv[2], \"w\"), sort_keys=True)' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-source.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-findings.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-source-slice-contract.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-findings.json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-verification.json --report ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-verification.txt --require-clean; test $? -eq 1) && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-verification.txt && grep -q 'source-graph:interprocedural-dfg: 1' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-verification.txt && grep -q 'status-mismatch: 2' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-contract-tampered-graph-verification.txt"
        )
        add_test(
            NAME ast_slp_transaction_formalization_verifier_clean_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-verification.json --report ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-verification.txt --require-clean --require-provenance-complete && grep -q '^  passed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-verification.txt && grep -q '^Formal provenance coverage$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-verification.txt && grep -q '^  passed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-clean-verification.txt"
        )
        add_test(
            NAME transaction_formalization_verifier_diff_fixture
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/transaction_formalization_verifier_fixture.py
                --repo
                ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME ast_slp_transaction_formalization_verifier_tampered_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-intent.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; rows=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; rows[0][\"intent_candidate\"][\"formal\"][\"after\"]={\"op\":\"vec\",\"args\":[]}; open(sys.argv[2],\"w\").write(\"\\n\".join(json.dumps(row, sort_keys=True) for row in rows)+\"\\n\")' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-intent.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-mutated.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-mutated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-verification.json --report ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-verification.txt --require-clean; test $? -eq 1) && grep -q '^  failed: 1$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-verification.txt && grep -q 'after-mismatch: 1' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-verification.txt && grep -q '^Top mismatch paths$' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-verification.txt && grep -q 'after-mismatch after.args\\[0\\]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-verification.txt && grep -q 'source=opcode/opcode_sources' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-formalization-tampered-verification.txt"
        )
        add_test(
            NAME ast_slp_complex_region_intent_inference_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_complex_region_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-region-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-region-intent-findings.json --format json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-region-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; assert ev[\"formal_inference\"] == \"source-derived-transaction\"; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.source_slice.control_root_function\"] == \"vectorizeTree\"; assert params[\"transaction.source_slice.completeness\"] == {\"has_candidate\":True,\"has_legality\":True,\"has_profitability\":True,\"has_emitter\":True,\"has_replacement\":True,\"has_lane_mapping\":True}; assert set(params[\"transaction.source_slice.predicate_expansion_roles\"]) >= {\"candidate-tree\",\"legality\",\"profitability\"}; assert params[\"transaction.source_slice.contract.status\"] == \"complete\"; assert params[\"transaction.source_slice.contract.missing_roles\"] == []; assert any(p[\"role\"] == \"lane-mapping\" for p in params[\"transaction.source_slice.contract.role_paths\"])' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-region-intent.json"
        )
        add_test(
            NAME ast_slp_complex_unknown_legality_intent_inference_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_complex_unknown_legality_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-unknown-legality-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-unknown-legality-intent-findings.json --format json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-unknown-legality-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert params[\"transaction.consistency\"] == \"failed\"; assert \"missing-expanded-legality\" in params[\"transaction.consistency_errors\"]; assert \"missing-contract-role:legality\" in params[\"transaction.consistency_errors\"]; assert params[\"transaction.source_slice.completeness\"][\"has_legality\"] is False; assert params[\"transaction.source_slice.contract.status\"] == \"failed\"; assert params[\"transaction.source_slice.contract.missing_roles\"] == [\"legality\"]; assert \"unknown-helper\" in params[\"transaction.source_slice.predicate_expansion_roles\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-unknown-legality-intent.json"
        )
        add_test(
            NAME ast_slp_transaction_wide_lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_8lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-8lane-findings.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_16lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-16lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx8=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; tx16=json.load(open(sys.argv[2]))[0][\"optimization_transaction\"]; assert tx8[\"consistency\"] == tx16[\"consistency\"] == \"ok\"; assert tx8[\"lanes\"] == 8; assert tx16[\"lanes\"] == 16; assert tx8[\"result_lane_mapping\"][\"map\"] == list(range(8)); assert tx16[\"result_lane_mapping\"][\"map\"] == list(range(16)); assert len(tx8[\"scalar_lane_pairs\"]) == 8; assert len(tx16[\"scalar_lane_pairs\"]) == 16' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-8lane-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-16lane-findings.json"
        )
        add_test(
            NAME ast_slp_transaction_wide_reorder_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_8lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 8; assert tx[\"lane_mapping\"][\"map\"] == [4,0,6,2,7,3,5,1]; assert tx[\"result_lane_mapping\"][\"map\"] == [4,0,6,2,7,3,5,1]; assert len(tx[\"scalar_lane_pairs\"]) == 8' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-findings.json"
        )
        add_test(
            NAME ast_slp_transaction_32lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_32lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-32lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 32; assert tx[\"result_lane_mapping\"][\"map\"] == list(range(32)); assert len(tx[\"scalar_lane_pairs\"]) == 32' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-32lane-findings.json"
        )
        add_test(
            NAME ast_slp_transaction_64lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_64lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-64lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 64; assert tx[\"result_lane_mapping\"][\"map\"] == list(range(64)); assert len(tx[\"scalar_lane_pairs\"]) == 64' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-64lane-findings.json"
        )
        add_test(
            NAME ast_slp_transaction_128lane_consistency_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_128lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-128lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"failed\"; assert tx[\"lanes\"] == 128; assert \"unsupported-lane-count:128\" in tx[\"consistency_errors\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-128lane-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[r for r in json.load(open(sys.argv[1])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-binop\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"base_lanes\"] == 4; assert tx[\"vscale_values\"] == [1,2,4]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_transaction_variants_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_base2_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-base2-findings.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_base8_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-base8-findings.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-reorder-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=lambda p:[r for r in json.load(open(p)) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; b2=tx(sys.argv[1]); b8=tx(sys.argv[2]); reorder=tx(sys.argv[3]); assert b2[\"consistency\"] == b8[\"consistency\"] == reorder[\"consistency\"] == \"ok\"; assert b2[\"base_lanes\"] == 2; assert b8[\"base_lanes\"] == 8; assert reorder[\"lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-base2-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-base8-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-reorder-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_minmax_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_minmax_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=[r for r in json.load(open(sys.argv[1])) if r.get(\"optimization_transaction\")][0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-minmax\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"consistency_errors\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_minmax_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-minmax-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; assert r[\"finding_source\"] == \"ast\"; assert tx[\"kind\"] == \"slp-vectorize-minmax\"; assert tx[\"opcode\"] == \"smin\"; assert tx[\"predicate\"] == \"slt\"; assert tx[\"actions\"][1][\"kind\"] == \"emit-vector-minmax\"; assert tx[\"compare_sources\"] and tx[\"select_sources\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-minmax-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_minmax_transaction_16lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_transaction_16lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-minmax-transaction-16lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-minmax\"; assert tx[\"opcode\"] == \"smin\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 16; assert tx[\"result_lane_mapping\"][\"map\"] == list(range(16)); assert len(tx[\"scalar_lane_pairs\"]) == 16' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-minmax-transaction-16lane-findings.json"
        )
        add_test(
            NAME ast_slp_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; assert r[\"finding_source\"] == \"ast\"; assert r[\"marker\"] == \"probe.slp.vectorize-reduction\"; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"add\"; assert tx[\"actions\"][1][\"kind\"] == \"emit-vector-reduction\"; assert tx[\"reduction_sources\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_complex_reduction_slice_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_complex_region_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-reduction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"root\"].startswith(\"Value *materializeReduction\"); assert tx[\"reduction_lanes\"] == 4; sl=tx[\"source_slice\"]; assert sl[\"control_root_function\"] == \"vectorizeReduction\"; roles={x[\"function\"]:x[\"role\"] for x in sl[\"predicate_expansion\"]}; assert roles[\"buildTree_rec\"] == \"candidate-tree\"; assert roles[\"isReductionLegal\"] == \"legality\"; assert roles[\"isReductionProfitable\"] == \"profitability\"; assert sl[\"completeness\"] == {\"has_candidate\":True,\"has_legality\":True,\"has_profitability\":True,\"has_emitter\":True,\"has_replacement\":True,\"has_lane_mapping\":True}; assert sl[\"contract\"][\"status\"] == \"complete\" and sl[\"contract\"][\"missing_roles\"] == []; assert any(p[\"role\"] == \"vector-emission\" and p[\"path\"] == [\"vectorizeReduction\",\"materializeReduction\"] for p in sl[\"contract\"][\"role_paths\"]); assert any(e[\"caller\"] == \"materializeReduction\" and e[\"callee\"] == \"collectReductionOperand\" for e in tx[\"slice_call_graph\"]); assert tx[\"operand_lane_mappings\"][\"lhs\"][\"pack_source\"][\"function\"] == \"collectReductionOperand\"; assert tx[\"lane_mapping\"][\"map\"] == [0,1,2,3]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-reduction-findings.json"
        )
        add_test(
            NAME ast_slp_complex_reduction_intent_inference_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_complex_region_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-reduction-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-reduction-intent-findings.json --format json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-reduction-intent.json --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; assert ev[\"formal_inference\"] == \"source-derived-transaction\"; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.kind\"] == \"slp-vectorize-reduction\"; assert params[\"transaction.source_slice.control_root_function\"] == \"vectorizeReduction\"; assert set(params[\"transaction.source_slice.predicate_expansion_roles\"]) >= {\"candidate-tree\",\"legality\",\"profitability\"}; assert all(params[\"transaction.source_slice.completeness\"].values()); assert params[\"transaction.source_slice.contract.status\"] == \"complete\"; assert params[\"transaction.source_slice.contract.missing_roles\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-complex-reduction-intent.json"
        )
        add_test(
            NAME ast_slp_reduction_transaction_2lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_2lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 2; assert tx[\"lane_source\"][\"lanes\"] == 2; assert tx[\"reduction_lanes\"] == 2; assert tx[\"lane_mapping\"][\"map\"] == [0,1]; assert tx[\"operand_lane_mappings\"][\"lhs\"][\"map\"] == [0,1]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-findings.json"
        )
        add_test(
            NAME ast_slp_reduction_transaction_8lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_8lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 8; assert tx[\"lane_source\"][\"lanes\"] == 8; assert tx[\"reduction_lanes\"] == 8; assert tx[\"lane_mapping\"][\"map\"] == [0,1,2,3,4,5,6,7]; assert tx[\"operand_lane_mappings\"][\"lhs\"][\"map\"] == [0,1,2,3,4,5,6,7]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-findings.json"
        )
        add_test(
            NAME ast_slp_reduction_transaction_16lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_16lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-16lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 16; assert tx[\"lane_source\"][\"lanes\"] == 16; assert tx[\"reduction_lanes\"] == 16; assert tx[\"lane_mapping\"][\"map\"] == list(range(16)); assert tx[\"operand_lane_mappings\"][\"lhs\"][\"map\"] == list(range(16))' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-16lane-findings.json"
        )
        add_test(
            NAME ast_slp_reduction_transaction_32lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_32lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-32lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 32; assert tx[\"lane_source\"][\"lanes\"] == 32; assert tx[\"reduction_lanes\"] == 32; assert tx[\"lane_mapping\"][\"map\"] == list(range(32)); assert tx[\"operand_lane_mappings\"][\"lhs\"][\"map\"] == list(range(32))' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-32lane-findings.json"
        )
        add_test(
            NAME ast_slp_reduction_transaction_64lane_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_64lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-64lane-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 64; assert tx[\"lane_source\"][\"lanes\"] == 64; assert tx[\"reduction_lanes\"] == 64; assert tx[\"lane_mapping\"][\"map\"] == list(range(64)); assert tx[\"operand_lane_mappings\"][\"lhs\"][\"map\"] == list(range(64))' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-64lane-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"base_lanes\"] == 4; assert tx[\"vscale_values\"] == [1,2,4]; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_reduction_base_lanes_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_base2_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-reduction-base2-findings.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_reduction_base8_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-reduction-base8-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; b2=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; b8=json.load(open(sys.argv[2]))[0][\"optimization_transaction\"]; assert b2[\"consistency\"] == b8[\"consistency\"] == \"ok\"; assert b2[\"scalable\"] is True and b8[\"scalable\"] is True; assert b2[\"base_lanes\"] == 2; assert b8[\"base_lanes\"] == 8; assert b2[\"vscale_values\"] == [1,2,4]; assert b8[\"vscale_values\"] == [1,2,4]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-reduction-base2-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-reduction-base8-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_reduction_unsupported_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_ambiguous_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-widening-ambiguous-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; wd=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert wd[\"consistency\"] == \"failed\" and \"unsupported-scalable-widening-reduction\" in wd[\"unsupported_reduction_reasons\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-widening-ambiguous-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_fp_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-fp-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"opcode\"] == \"fadd\"; assert tx[\"scalable\"] is True; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-fp-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_fp_reduction_relaxed_policy_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_permutation_reassoc_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-fp-policy-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert tx[\"fp_policy\"][\"semantics\"] == \"relaxed-reassoc\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-fp-policy-findings.json"
        )
        add_test(
            NAME ast_slp_fp_reduction_general_policy_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_unordered_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-fp-unordered-policy-findings.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_fast_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-fp-fast-policy-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; fixed=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; scalable=json.load(open(sys.argv[2]))[0][\"optimization_transaction\"]; assert fixed[\"consistency\"] == scalable[\"consistency\"] == \"ok\"; assert fixed[\"fp_policy\"][\"semantics\"] == \"unordered-fp-reduction\"; assert scalable[\"fp_policy\"][\"semantics\"] == \"fast-math-fp-reduction\"; assert scalable[\"scalable\"] is True; assert fixed[\"unsupported_reduction_reasons\"] == scalable[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-fp-unordered-policy-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-fp-fast-policy-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_widening_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-widening-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"scalable\"] is True; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 32; assert tx[\"reduction_extend_kind\"] == \"zext\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-widening-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_scalable_widening_alias_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_alias_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-widening-alias-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; kinds={p[\"kind\"] for p in tx[\"reduction_width_provenance\"]}; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"reduction_width_status\"] == \"complete\"; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 32; assert \"type-alias-width\" in kinds and \"extension-target-width\" in kinds; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-widening-alias-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_minmax_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_minmax_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-minmax-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; assert r[\"finding_source\"] == \"ast\"; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"smin\"; assert tx[\"reduction_opcode\"] == \"smin\"; assert tx[\"actions\"][1][\"kind\"] == \"emit-vector-reduction\"; assert tx[\"reduction_sources\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-minmax-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_reduction_transaction_consistency_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_invalid_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-invalid-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; errors=set(tx[\"consistency_errors\"]); assert r[\"finding_source\"] == \"ast\"; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"failed\"; assert tx[\"lanes\"] == 32; assert tx[\"reduction_lanes\"] == 32; assert \"opcode-mismatch:legality:mul!=vector-emission:add\" in errors; assert \"unsupported-lane-count:32\" not in errors; assert tx[\"reduction_sources\"] and tx[\"reduction_result\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-invalid-findings.json"
        )
        add_test(
            NAME ast_slp_fp_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_fp_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-fp-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; assert r[\"finding_source\"] == \"ast\"; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"fadd\"; assert tx[\"reduction_opcode\"] == \"fadd\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-fp-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_widening_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-widening-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; assert r[\"finding_source\"] == \"ast\"; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"add\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 32; assert tx[\"reduction_extend_kind\"] == \"zext\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-widening-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_widening_reduction_8lane_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_reduction_8lane_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-widening-reduction-8lane-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"opcode\"] == \"add\"; assert tx[\"consistency\"] == \"ok\"; assert tx[\"lanes\"] == 8; assert tx[\"reduction_lanes\"] == 8; assert tx[\"reduction_input_bits\"] == 16; assert tx[\"reduction_accumulator_bits\"] == 32; assert tx[\"reduction_result_bits\"] == 32; assert tx[\"reduction_extend_kind\"] == \"zext\"; assert tx[\"unsupported_reduction_reasons\"] == []' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-widening-reduction-8lane-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_widening_ambiguous_reduction_transaction_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_widening_ambiguous_reduction_transaction_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-widening-ambiguous-reduction-transaction-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; tx=r[\"optimization_transaction\"]; errors=set(tx[\"consistency_errors\"]); assert r[\"finding_source\"] == \"ast\"; assert tx[\"kind\"] == \"slp-vectorize-reduction\"; assert tx[\"consistency\"] == \"failed\"; assert \"unsupported-reduction-ambiguous-width\" in errors; assert tx[\"reduction_width_status\"] == \"ambiguous\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-widening-ambiguous-reduction-transaction-findings.json"
        )
        add_test(
            NAME ast_slp_transaction_result_mismatch_miner_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-binop ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_mismatch_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-result-mismatch-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; tx=json.load(open(sys.argv[1]))[0][\"optimization_transaction\"]; assert tx[\"consistency\"] == \"failed\"; assert tx[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert tx[\"result_lane_mapping\"][\"map\"] == [0,1,2,3]; assert any(item == \"unsupported-lane-pairing\" for item in tx[\"consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-result-mismatch-findings.json"
        )
        add_test(
            NAME ast_slp_transaction_intent_inference_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-intent-findings.json --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; params=ev[\"formal_parameters\"]; assert ev[\"formal_inference\"] == \"source-derived-transaction\"; assert ev[\"transaction_lowering\"] == \"formal-ir\"; assert params[\"transaction.lane_mapping.map\"] == [2,0,3,1]; assert params[\"transaction.result_lane_mapping\"][\"map\"] == [2,0,3,1]; assert r[\"intent_candidate\"][\"formal\"][\"after\"][\"mask\"] == [1,3,0,2]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-intent.json"
        )
        add_test(
            NAME ast_slp_transaction_result_mismatch_blocks_inference_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_result_mismatch_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-result-mismatch-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-result-mismatch-intent-findings.json --format json --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-result-mismatch-intent.json --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.load(open(sys.argv[1]))[0]; ev=r[\"evidence\"]; assert \"formal\" not in r[\"intent_candidate\"]; assert ev[\"transaction_lowering\"] == \"fallback\"; assert any(item == \"unsupported-lane-pairing\" for item in ev[\"formal_parameters\"][\"transaction.consistency_errors\"])' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-result-mismatch-intent.json"
        )
        add_test(
            NAME semantic_facts_ast_text_parity_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp --format json > ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/text-vector.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/ast-vector.json && ${Python3_EXECUTABLE} -c 'import json,sys; text=json.load(open(sys.argv[1])); ast=json.load(open(sys.argv[2])); tm={r[\"marker\"]:r[\"semantic_facts\"] for r in text if r.get(\"line\",0) >= 35 and r.get(\"marker\",\"\").startswith(\"probe.vector.\")}; am={r[\"marker\"]:r[\"semantic_facts\"] for r in ast if r.get(\"line\",0) >= 35 and r.get(\"marker\",\"\").startswith(\"probe.vector.\")}; assert len(tm) == 24, tm.keys(); assert len(am) == 24, am.keys(); assert tm == am' ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/text-vector.json ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/ast-vector.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp --format json > ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/text-scalar.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/ast-scalar.json && ${Python3_EXECUTABLE} -c 'import json,sys; wanted={\"probe.instcombine.add-zero\",\"probe.instcombine.mul-one\",\"probe.instcombine.xor-self\",\"probe.dce.dead-instruction\"}; text=json.load(open(sys.argv[1])); ast=json.load(open(sys.argv[2])); tm={r[\"marker\"]:r[\"semantic_facts\"] for r in text if r.get(\"marker\") in wanted}; am={r[\"marker\"]:r[\"semantic_facts\"] for r in ast if r.get(\"marker\") in wanted}; assert set(tm) == wanted, tm.keys(); assert set(am) == wanted, am.keys(); assert tm == am' ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/text-scalar.json ${CMAKE_CURRENT_BINARY_DIR}/semantic-parity/ast-scalar.json"
        )
        add_test(
            NAME semantic_facts_ast_text_nonvector_parity_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/semantic-nonvector-parity && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-mine-pass-source.py --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp --format json > ${CMAKE_CURRENT_BINARY_DIR}/semantic-nonvector-parity/text.json && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/semantic-nonvector-parity/ast.json && ${Python3_EXECUTABLE} -c 'import json,sys; text=json.load(open(sys.argv[1])); ast=json.load(open(sys.argv[2])); tm={r[\"marker\"]:r[\"semantic_facts\"] for r in text if r.get(\"semantic_facts\",{}).get(\"shape\") in {\"cfg\",\"memory\",\"loop\"}}; am={r[\"marker\"]:r[\"semantic_facts\"] for r in ast if r.get(\"semantic_facts\",{}).get(\"shape\") in {\"cfg\",\"memory\",\"loop\"}}; common=set(tm)&set(am); assert len(common) >= 14, common; assert {tm[m][\"shape\"] for m in common} == {\"cfg\",\"memory\",\"loop\"}; assert all(tm[m] == am[m] for m in common)' ${CMAKE_CURRENT_BINARY_DIR}/semantic-nonvector-parity/text.json ${CMAKE_CURRENT_BINARY_DIR}/semantic-nonvector-parity/ast.json"
        )
        add_test(
            NAME ast_intent_inference_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-intent-findings.json --format json --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-intent-high.json --require-marker probe.instcombine.add-zero --require-marker probe.instcombine.mul-one --require-marker probe.instcombine.xor-self --require-marker probe.dce.dead-instruction && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); assert all(r[\"confidence\"] == \"high\" for r in data); assert any(\"replaceInstUsesWith\" in r[\"rewrite_source\"] for r in data); source_intent=[r for r in data if r.get(\"evidence\",{}).get(\"formal_inference\") == \"source-derived-intent-graph\"]; assert len(source_intent) == 5; assert all(r[\"evidence\"][\"source_intent_lowering\"] == \"formal-ir\" for r in source_intent); assert all(r[\"evidence\"][\"source_intent_graph_lowering\"] == \"formal-ir\" for r in source_intent)' ${CMAKE_CURRENT_BINARY_DIR}/ast-intent-high.json"
        )
        if(Z3_EXECUTABLE)
            add_test(
                NAME ast_source_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; source_intent=[r for r in records if r.get(\"evidence\",{}).get(\"formal_inference\") == \"source-derived-intent-graph\"]; assert len(source_intent) == 5; assert all(r[\"proof_status\"] == \"proved\" for r in source_intent); assert all(r[\"promotion_status\"] == \"ready\" for r in source_intent); assert any(r.get(\"modeled_side_conditions\") for r in source_intent)' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-validated.jsonl"
            )
            add_test(
                NAME ast_source_intent_matcher_interpreter_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/source_intent_matcher_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher.jsonl && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-validated.jsonl --emit-smt ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-smt; test $? -eq 1) && ${Python3_EXECUTABLE} -c 'import json,sys; findings=json.load(open(sys.argv[1])); records=[json.loads(line) for line in open(sys.argv[2]) if line.strip()]; assert len(findings) == 16; assert len(records) == 16; assert all(r[\"evidence\"][\"formal_inference\"] == \"source-derived-intent-graph\" for r in records); assert all(f.get(\"source_intent_graph\",{}).get(\"status\") == \"complete\" for f in findings); assert sum(r[\"proof_status\"] == \"proved\" for r in records) == 13; assert sum(r[\"proof_status\"] == \"failed\" and r[\"proof_result\"] == \"sat\" for r in records) == 2; assert sum(r[\"proof_status\"] == \"unsupported\" and r[\"proof_result\"] == \"unsupported-side-conditions\" for r in records) == 1; assert any(f[\"source_intent\"][\"before\"].get(\"commutative\") is True for f in findings); assert any(g.get(\"kind\") == \"known-nonzero\" and g.get(\"subject\") == \"X\" for f in findings for g in f.get(\"source_intent\",{}).get(\"guards\",[])); assert any(g.get(\"kind\") == \"known-positive\" and g.get(\"subject\") == \"X\" for f in findings for g in f.get(\"source_intent\",{}).get(\"guards\",[])); assert any(g.get(\"kind\") == \"known-nonnegative\" and g.get(\"subject\") == \"X\" for f in findings for g in f.get(\"source_intent\",{}).get(\"guards\",[])); assert any(g.get(\"kind\") == \"masked-value-is-zero\" and g.get(\"subject\") == \"X\" and g.get(\"zero_mask\") == 255 for f in findings for g in f.get(\"source_intent\",{}).get(\"guards\",[])); assert any(g.get(\"kind\") == \"known-power-of-two\" and g.get(\"subject\") == \"X\" for f in findings for g in f.get(\"source_intent\",{}).get(\"guards\",[])); bad_add=[r for r in records if r[\"marker\"] == \"probe.instcombine.add-zero\" and r[\"intent_candidate\"][\"formal\"].get(\"variables\") == [\"x\",\"y\"]][0]; assert bad_add[\"evidence\"][\"formal_parameters\"][\"source_intent.symbols\"] == {\"X\":\"x\",\"Y\":\"y\"}; assert bad_add[\"evidence\"][\"formal_parameters\"][\"source_intent_graph.formal_symbols\"] == {\"X\":\"x\",\"Y\":\"y\"}; assert any(r.get(\"modeled_side_conditions\") and r.get(\"proof_status\") == \"proved\" for r in records); assert any(r.get(\"profitability_guards\") and r.get(\"proof_status\") == \"proved\" for r in records); assert any(r.get(\"modeled_side_conditions\") and r[\"modeled_side_conditions\"][0][\"kind\"] == \"one-use\" and r[\"proof_status\"] == \"proved\" for r in records); assert any(r[\"intent_candidate\"][\"formal\"].get(\"assumptions\") == [{\"op\":\"not-poison\",\"name\":\"x\"}] for r in records if r[\"proof_status\"] == \"proved\"); assert any(r[\"intent_candidate\"][\"formal\"].get(\"assumptions\") == [{\"op\":\"not-eq\",\"name\":\"x\",\"value\":0}] for r in records if r[\"proof_status\"] == \"proved\"); assert any(r[\"intent_candidate\"][\"formal\"].get(\"assumptions\") == [{\"op\":\"cmp\",\"predicate\":\"sgt\",\"name\":\"x\",\"value\":0}] for r in records if r[\"proof_status\"] == \"proved\"); assert any(r[\"intent_candidate\"][\"formal\"].get(\"assumptions\") == [{\"op\":\"cmp\",\"predicate\":\"sge\",\"name\":\"x\",\"value\":0}] for r in records if r[\"proof_status\"] == \"proved\"); assert any(r[\"intent_candidate\"][\"formal\"].get(\"assumptions\") == [{\"op\":\"known-bits\",\"name\":\"x\",\"zero_mask\":255}] for r in records if r[\"proof_status\"] == \"proved\"); assert any(r[\"intent_candidate\"][\"formal\"].get(\"assumptions\") == [{\"op\":\"power-of-two\",\"name\":\"x\",\"nonzero\":True}] for r in records if r[\"proof_status\"] == \"proved\"); unsupported_kinds={sc[\"kind\"] for r in records for sc in r.get(\"side_conditions\",[])}; assert unsupported_kinds == {\"unknown\"}; by_marker={}; [by_marker.setdefault(r[\"marker\"], []).append(r) for r in records]; assert len(by_marker[\"probe.instcombine.add-zero\"]) == 13; assert len(by_marker[\"probe.instcombine.mul-one\"]) == 1; assert len(by_marker[\"probe.instcombine.xor-self\"]) == 2' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-validated.jsonl && grep -R -q '(not x_poison)' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-smt && grep -R -q '(not (= x #x00000000))' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-smt && grep -R -q '(bvsgt x #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-smt && grep -R -q '(bvsge x #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-smt && grep -R -q '(= (bvand x #x000000ff) #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-smt && grep -R -q '(= (bvand x (bvsub x #x00000001)) #x00000000)' ${CMAKE_CURRENT_BINARY_DIR}/ast-source-intent-matcher-smt"
            )
            add_test(
                NAME ast_vector_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-intent-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-intent-findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-intent.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-intent.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; records=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; vector=[r for r in records if r[\"marker\"].startswith(\"probe.vector.\")]; assert len(vector) == 24; assert all(r[\"proof_status\"] == \"proved\" for r in vector); assert all(r.get(\"evidence\",{}).get(\"formal_inference\") == \"source-derived-intent-graph\" for r in vector); assert all(r.get(\"evidence\",{}).get(\"source_intent_lowering\") == \"formal-ir\" for r in vector); assert all(r.get(\"evidence\",{}).get(\"source_intent_graph_lowering\") == \"formal-ir\" for r in vector); assert all(r.get(\"evidence\",{}).get(\"semantic_lowering\") == \"formal-ir\" for r in vector); by_marker={r[\"marker\"]:r for r in vector}; assert by_marker[\"probe.vector.smin\"][\"intent_candidate\"][\"formal\"][\"domain\"] == \"vector-bv32x4\"; assert by_marker[\"probe.vector.shuffle-splat\"][\"evidence\"][\"formal_parameters\"][\"vector.shuffle.mask\"] == [2,2,2,2]; assert by_marker[\"probe.vector.shuffle-splat\"][\"evidence\"][\"formal_parameters\"][\"source_intent_graph.bindings\"] == 2; assert by_marker[\"probe.vector.extract-insert\"][\"evidence\"][\"formal_parameters\"][\"vector.extract_insert.lane\"] == 1; assert len(by_marker[\"probe.vector.scalable.add-zero\"][\"proof_instances\"]) == 3' ${CMAKE_CURRENT_BINARY_DIR}/ast-vector-validated.jsonl"
            )
            add_test(
                NAME ast_slp_transaction_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-validation-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-validation-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-validation.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-validation.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.lane_mapping.map\"] == [2,0,3,1]; assert params[\"transaction.result_lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-validated.jsonl"
            )
            add_test(
                NAME ast_slp_transaction_graph_chain_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_chain_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-formalization.json --require-clean --require-provenance-complete && ${Python3_EXECUTABLE} -c 'import json,sys; finding=json.load(open(sys.argv[1]))[0]; r=json.loads(open(sys.argv[2]).readline()); verification=json.load(open(sys.argv[3])); tx=finding[\"optimization_transaction\"]; graph=tx[\"transaction_graph\"]; params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert len(graph[\"nodes\"]) == 2; assert graph[\"edges\"] == [{\"from\":\"n0\",\"to\":\"n1\",\"operand\":0}]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert params[\"transaction.graph.node_count\"] == 2; assert params[\"transaction.graph.edge_count\"] == 1; assert formal[\"after\"][\"op\"] == \"vmul\"; assert formal[\"after\"][\"args\"][0][\"op\"] == \"vadd\"; assert verification[\"summary\"][\"status\"] == {\"passed\":1}; assert verification[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-chain-formalization.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_generalized_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_sub_xor_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_three_node_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_unresolved_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-unresolved-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; sub=json.load(open(sys.argv[1]))[0]; subv=json.loads(open(sys.argv[2]).readline()); three=json.load(open(sys.argv[3]))[0]; threev=json.loads(open(sys.argv[4]).readline()); unresolved=json.load(open(sys.argv[5]))[0]; subg=sub[\"optimization_transaction\"][\"transaction_graph\"]; threeg=three[\"optimization_transaction\"][\"transaction_graph\"]; assert [n[\"opcode\"] for n in subg[\"nodes\"]] == [\"sub\",\"xor\"]; assert subv[\"proof_status\"] == \"proved\"; assert subv[\"intent_candidate\"][\"formal\"][\"after\"][\"op\"] == \"vxor\"; assert subv[\"intent_candidate\"][\"formal\"][\"after\"][\"args\"][0][\"op\"] == \"vsub\"; assert len(threeg[\"nodes\"]) == 3; assert threeg[\"edges\"] == [{\"from\":\"n0\",\"to\":\"n1\",\"operand\":0},{\"from\":\"n1\",\"to\":\"n2\",\"operand\":0}]; assert threev[\"proof_status\"] == \"proved\"; assert threev[\"evidence\"][\"formal_parameters\"][\"transaction.graph.node_count\"] == 3; assert threev[\"intent_candidate\"][\"formal\"][\"after\"][\"op\"] == \"vxor\"; assert threev[\"intent_candidate\"][\"formal\"][\"after\"][\"args\"][0][\"op\"] == \"vmul\"; assert \"transaction_graph\" not in unresolved[\"optimization_transaction\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-sub-xor-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-unresolved-findings.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_permutation_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_three_node_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_result_mismatch_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-result-mismatch-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; reorder=json.load(open(sys.argv[1]))[0]; reorderv=json.loads(open(sys.argv[2]).readline()); threev=json.loads(open(sys.argv[3]).readline()); mismatch=json.load(open(sys.argv[4]))[0]; graph=reorder[\"optimization_transaction\"][\"transaction_graph\"]; formal=reorderv[\"intent_candidate\"][\"formal\"]; assert graph[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert reorderv[\"proof_status\"] == \"proved\"; assert formal[\"after\"][\"op\"] == \"vshuffle\" and formal[\"after\"][\"mask\"] == [1,3,0,2]; assert formal[\"after\"][\"args\"][0][\"op\"] == \"vmul\"; assert formal[\"after\"][\"args\"][0][\"args\"][0][\"args\"][0][\"op\"] == \"vshuffle\"; assert threev[\"proof_status\"] == \"proved\"; assert threev[\"intent_candidate\"][\"formal\"][\"after\"][\"op\"] == \"vshuffle\"; assert threev[\"evidence\"][\"formal_parameters\"][\"transaction.graph.node_count\"] == 3; assert \"transaction_graph\" not in mismatch[\"optimization_transaction\"]; assert mismatch[\"optimization_transaction\"][\"consistency\"] == \"failed\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-reorder-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-three-node-reorder-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-result-mismatch-findings.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_helper_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_helper_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_helper_node_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_helper_incomplete_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-incomplete-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; pack=json.load(open(sys.argv[1]))[0]; packv=json.loads(open(sys.argv[2]).readline()); node=json.load(open(sys.argv[3]))[0]; nodev=json.loads(open(sys.argv[4]).readline()); incomplete=json.load(open(sys.argv[5]))[0]; packg=pack[\"optimization_transaction\"][\"transaction_graph\"]; nodeg=node[\"optimization_transaction\"][\"transaction_graph\"]; assert len(packg[\"operands\"]) == 3; assert packv[\"proof_status\"] == \"proved\"; assert packv[\"intent_candidate\"][\"formal\"][\"after\"][\"op\"] == \"vmul\"; assert [n[\"opcode\"] for n in nodeg[\"nodes\"]] == [\"add\",\"mul\"]; assert nodeg[\"edges\"] == [{\"from\":\"n0\",\"to\":\"n1\",\"operand\":0}]; assert nodev[\"proof_status\"] == \"proved\"; assert nodev[\"evidence\"][\"formal_parameters\"][\"transaction.graph.node_count\"] == 2; assert \"transaction_graph\" not in incomplete[\"optimization_transaction\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-pack-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-node-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-helper-incomplete-findings.json"
            )
            add_test(
                NAME ast_slp_scalable_transaction_graph_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_graph_chain_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_graph_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-formalization.json --require-clean --require-provenance-complete && ${Python3_EXECUTABLE} -c 'import json,sys; chain=json.load(open(sys.argv[1]))[0]; chainv=json.loads(open(sys.argv[2]).readline()); chainf=json.load(open(sys.argv[3])); reorder=json.load(open(sys.argv[4]))[0]; reorderv=json.loads(open(sys.argv[5]).readline()); reorderf=json.load(open(sys.argv[6])); cg=chain[\"optimization_transaction\"][\"transaction_graph\"]; rg=reorder[\"optimization_transaction\"][\"transaction_graph\"]; cf=chainv[\"intent_candidate\"][\"formal\"]; rf=reorderv[\"intent_candidate\"][\"formal\"]; assert chain[\"optimization_transaction\"][\"scalable\"] is True; assert cg[\"edges\"] == [{\"from\":\"n0\",\"to\":\"n1\",\"operand\":0}]; assert chainv[\"proof_status\"] == reorderv[\"proof_status\"] == \"proved\"; assert len(chainv[\"proof_instances\"]) == len(reorderv[\"proof_instances\"]) == 3; assert [p[\"vscale\"] for p in chainv[\"proof_instances\"]] == [1,2,4]; assert cf[\"domain\"] == rf[\"domain\"] == \"scalable-vector-bv32\"; assert cf[\"after\"][\"op\"] == \"svmul\" and cf[\"after\"][\"args\"][0][\"op\"] == \"svadd\"; assert rg[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert rf[\"after\"][\"op\"] == \"svshuffle\" and rf[\"after\"][\"base_mask\"] == [1,3,0,2]; assert rf[\"after\"][\"args\"][0][\"op\"] == \"svmul\"; assert rf[\"after\"][\"args\"][0][\"args\"][0][\"args\"][0][\"op\"] == \"svshuffle\"; assert reorderv[\"evidence\"][\"formal_parameters\"][\"transaction.graph.node_count\"] == 2; assert reorderv[\"evidence\"][\"formal_parameters\"][\"transaction.scalable\"] is True; assert chainf[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert reorderf[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-chain-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-reorder-formalization.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_minmax_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_minmax_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_minmax_intermediate_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_graph_minmax_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_minmax_reversed_select_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-reversed-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; minf=json.load(open(sys.argv[1]))[0]; minv=json.loads(open(sys.argv[2]).readline()); minver=json.load(open(sys.argv[3])); mid=json.load(open(sys.argv[4]))[0]; midv=json.loads(open(sys.argv[5]).readline()); sc=json.load(open(sys.argv[6]))[0]; scv=json.loads(open(sys.argv[7]).readline()); scver=json.load(open(sys.argv[8])); rev=json.load(open(sys.argv[9]))[0]; ming=minf[\"optimization_transaction\"][\"transaction_graph\"]; midg=mid[\"optimization_transaction\"][\"transaction_graph\"]; scg=sc[\"optimization_transaction\"][\"transaction_graph\"]; minformal=minv[\"intent_candidate\"][\"formal\"]; midformal=midv[\"intent_candidate\"][\"formal\"]; scformal=scv[\"intent_candidate\"][\"formal\"]; assert [n[\"opcode\"] for n in ming[\"nodes\"]] == [\"add\",\"smin\"]; assert minv[\"proof_status\"] == \"proved\"; assert minformal[\"after\"][\"op\"] == \"vsmin\"; assert minformal[\"before\"][\"args\"][0][\"op\"] == \"ite\"; assert [n[\"opcode\"] for n in midg[\"nodes\"]] == [\"umax\",\"xor\"]; assert midv[\"proof_status\"] == \"proved\"; assert midformal[\"after\"][\"op\"] == \"vxor\" and midformal[\"after\"][\"args\"][0][\"op\"] == \"vumax\"; assert sc[\"optimization_transaction\"][\"scalable\"] is True; assert scg[\"lane_mapping\"][\"map\"] == [2,0,3,1]; assert scv[\"proof_status\"] == \"proved\"; assert [p[\"vscale\"] for p in scv[\"proof_instances\"]] == [1,2,4]; assert scformal[\"domain\"] == \"scalable-vector-bv32\"; assert scformal[\"after\"][\"op\"] == \"svshuffle\" and scformal[\"after\"][\"args\"][0][\"op\"] == \"svumax\"; assert minver[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert scver[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; revg=rev[\"optimization_transaction\"][\"transaction_graph\"]; assert [n[\"opcode\"] for n in revg[\"nodes\"]] == [\"add\",\"icmp\",\"select\"]; assert revg[\"nodes\"][1][\"predicate\"] == \"slt\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-intermediate-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-minmax-reorder-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-minmax-reversed-findings.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_shift_cast_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shift_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_graph_shift_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_cast_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_cast_unresolved_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-unresolved-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; shift=json.load(open(sys.argv[1]))[0]; shiftv=json.loads(open(sys.argv[2]).readline()); shiftver=json.load(open(sys.argv[3])); scalable=json.load(open(sys.argv[4]))[0]; scalablev=json.loads(open(sys.argv[5]).readline()); scalablever=json.load(open(sys.argv[6])); cast=json.load(open(sys.argv[7]))[0]; castv=json.loads(open(sys.argv[8]).readline()); castver=json.load(open(sys.argv[9])); unresolved=json.load(open(sys.argv[10]))[0]; sg=shift[\"optimization_transaction\"][\"transaction_graph\"]; svg=scalable[\"optimization_transaction\"][\"transaction_graph\"]; cg=cast[\"optimization_transaction\"][\"transaction_graph\"]; sf=shiftv[\"intent_candidate\"][\"formal\"]; svf=scalablev[\"intent_candidate\"][\"formal\"]; cf=castv[\"intent_candidate\"][\"formal\"]; assert [n[\"opcode\"] for n in sg[\"nodes\"]] == [\"add\",\"shl\",\"xor\"]; assert shiftv[\"proof_status\"] == \"proved\"; assert sf[\"after\"][\"op\"] == \"vxor\" and sf[\"after\"][\"args\"][0][\"op\"] == \"vshl\"; assert scalable[\"optimization_transaction\"][\"scalable\"] is True; assert [n[\"opcode\"] for n in svg[\"nodes\"]] == [\"add\",\"lshr\"]; assert scalablev[\"proof_status\"] == \"proved\"; assert [p[\"vscale\"] for p in scalablev[\"proof_instances\"]] == [1,2,4]; assert svf[\"after\"][\"op\"] == \"svshuffle\" and svf[\"after\"][\"args\"][0][\"op\"] == \"svlshr\"; assert [n[\"opcode\"] for n in cg[\"nodes\"]] == [\"zext\",\"zext\",\"add\",\"trunc\"]; assert [n.get(\"kind\") for n in cg[\"nodes\"]] == [\"cast\",\"cast\",\"binop\",\"cast\"]; assert [n.get(\"bits\") for n in cg[\"nodes\"] if n.get(\"kind\") == \"cast\"] == [64,64,32]; assert castv[\"proof_status\"] == \"proved\"; assert cf[\"after\"][\"op\"] == \"vtrunc\" and cf[\"after\"][\"args\"][0][\"op\"] == \"vadd\"; assert cf[\"after\"][\"args\"][0][\"args\"][0][\"op\"] == \"vzext\"; assert shiftver[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert scalablever[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert castver[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert \"transaction_graph\" not in unresolved[\"optimization_transaction\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shift-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-shift-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-cast-unresolved-findings.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_const_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_const_shift_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_const_mask_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-mask-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-mask-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-mask.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-mask.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-mask-validated.jsonl && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_graph_const_shift_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_const_unresolved_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-unresolved-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; shift=json.load(open(sys.argv[1]))[0]; shiftv=json.loads(open(sys.argv[2]).readline()); shiftver=json.load(open(sys.argv[3])); mask=json.load(open(sys.argv[4]))[0]; maskv=json.loads(open(sys.argv[5]).readline()); scalable=json.load(open(sys.argv[6]))[0]; scalablev=json.loads(open(sys.argv[7]).readline()); scalablever=json.load(open(sys.argv[8])); unresolved=json.load(open(sys.argv[9]))[0]; sg=shift[\"optimization_transaction\"][\"transaction_graph\"]; mg=mask[\"optimization_transaction\"][\"transaction_graph\"]; svg=scalable[\"optimization_transaction\"][\"transaction_graph\"]; sf=shiftv[\"intent_candidate\"][\"formal\"]; mf=maskv[\"intent_candidate\"][\"formal\"]; svf=scalablev[\"intent_candidate\"][\"formal\"]; assert [n[\"opcode\"] for n in sg[\"nodes\"]] == [\"add\",\"shl\",\"xor\"]; assert sg[\"nodes\"][1][\"operands\"][1] == {\"kind\":\"const\",\"value\":3,\"bits\":32,\"source\":\"ConstantInt::get(Type::getInt32Ty(), 3)\"}; assert shiftv[\"proof_status\"] == \"proved\"; assert sf[\"after\"][\"args\"][0][\"args\"][1][\"op\"] == \"vsplat\"; assert sf[\"after\"][\"args\"][0][\"args\"][1][\"args\"][0] == {\"op\":\"bvconst\",\"value\":3,\"bits\":32}; assert [n[\"opcode\"] for n in mg[\"nodes\"]] == [\"add\",\"and\",\"xor\"]; assert mg[\"nodes\"][1][\"operands\"][1][\"value\"] == 4294967295; assert mg[\"nodes\"][2][\"operands\"][1][\"value\"] == 255; assert maskv[\"proof_status\"] == \"proved\"; assert mf[\"after\"][\"op\"] == \"vxor\" and mf[\"after\"][\"args\"][1][\"op\"] == \"vsplat\"; assert scalable[\"optimization_transaction\"][\"scalable\"] is True; assert [n[\"opcode\"] for n in svg[\"nodes\"]] == [\"add\",\"lshr\"]; assert scalablev[\"proof_status\"] == \"proved\"; assert [p[\"vscale\"] for p in scalablev[\"proof_instances\"]] == [1,2,4]; assert svf[\"after\"][\"op\"] == \"svshuffle\" and svf[\"after\"][\"args\"][0][\"args\"][1][\"op\"] == \"svsplat\"; assert shiftver[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert scalablever[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert \"transaction_graph\" not in unresolved[\"optimization_transaction\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-shift-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-mask-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-mask-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-const-shift-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-const-unresolved-findings.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_select_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_select_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_graph_select_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-validated.jsonl && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-verify-transaction-formalization.py --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-validated.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-formalization.json --require-clean --require-provenance-complete && $<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_select_unresolved_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-unresolved-findings.json && ${Python3_EXECUTABLE} -c 'import json,sys; fixed=json.load(open(sys.argv[1]))[0]; fixedv=json.loads(open(sys.argv[2]).readline()); fixedver=json.load(open(sys.argv[3])); scalable=json.load(open(sys.argv[4]))[0]; scalablev=json.loads(open(sys.argv[5]).readline()); scalablever=json.load(open(sys.argv[6])); unresolved=json.load(open(sys.argv[7]))[0]; fg=fixed[\"optimization_transaction\"][\"transaction_graph\"]; sg=scalable[\"optimization_transaction\"][\"transaction_graph\"]; ff=fixedv[\"intent_candidate\"][\"formal\"]; sf=scalablev[\"intent_candidate\"][\"formal\"]; assert [n[\"opcode\"] for n in fg[\"nodes\"]] == [\"add\",\"icmp\",\"select\",\"xor\"]; assert fg[\"nodes\"][1][\"kind\"] == \"icmp\" and fg[\"nodes\"][1][\"predicate\"] == \"ule\"; assert fg[\"nodes\"][2][\"kind\"] == \"select\"; assert fixedv[\"proof_status\"] == \"proved\"; assert ff[\"after\"][\"op\"] == \"vxor\" and ff[\"after\"][\"args\"][0][\"op\"] == \"vselect\"; assert ff[\"after\"][\"args\"][0][\"args\"][0][\"op\"] == \"vicmp\"; assert scalable[\"optimization_transaction\"][\"scalable\"] is True; assert [n[\"opcode\"] for n in sg[\"nodes\"]] == [\"add\",\"icmp\",\"select\"]; assert sg[\"nodes\"][1][\"predicate\"] == \"eq\"; assert scalablev[\"proof_status\"] == \"proved\"; assert [p[\"vscale\"] for p in scalablev[\"proof_instances\"]] == [1,2,4]; assert sf[\"after\"][\"op\"] == \"svshuffle\" and sf[\"after\"][\"args\"][0][\"op\"] == \"svselect\"; assert sf[\"after\"][\"args\"][0][\"args\"][0][\"op\"] == \"svicmp\"; assert fixedver[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert scalablever[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert \"transaction_graph\" not in unresolved[\"optimization_transaction\"]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-findings.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-validated.jsonl ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-graph-select-formalization.json ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-select-unresolved-findings.json"
            )
            add_test(
                NAME ast_slp_transaction_graph_shuffle_core_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-core
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
                    --prove-only ast-slp-transaction-graph-shuffle,ast-slp-transaction-graph-static-pack-index,ast-slp-transaction-graph-static-helper-pack-index,ast-slp-transaction-graph-helper-node-chain,ast-slp-transaction-graph-shuffle-reorder,ast-slp-transaction-graph-shuffle-two-input,ast-slp-transaction-graph-shuffle-two-input-reorder,ast-slp-scalable-transaction-graph-shuffle,ast-slp-scalable-transaction-graph-shuffle-two-input,ast-slp-transaction-graph-extract-insert,ast-slp-transaction-graph-static-extract-insert-index,ast-slp-transaction-graph-extract-insert-reorder,ast-slp-scalable-transaction-graph-extract-insert
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_core_validation_fixture PROPERTIES TIMEOUT 240)
            add_test(
                NAME ast_slp_transaction_graph_shuffle_scalable_memory_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-scalable_memory
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
                    --prove-only ast-slp-transaction-graph-memory-pack,ast-slp-scalable-transaction-graph-memory-pack,ast-slp-scalable-transaction-graph-memory-gather,ast-slp-scalable-transaction-graph-masked-memory,ast-slp-scalable-transaction-graph-symbolic-mask-index-memory,ast-slp-scalable-transaction-graph-guarded-symbolic-mask-index-memory,ast-slp-scalable-transaction-graph-symbolic-undef-passthru-memory,ast-slp-scalable-transaction-graph-implicit-undef-passthru-memory,ast-slp-scalable-transaction-graph-mask-provenance-memory,ast-slp-scalable-transaction-graph-mask-tuple-memory,ast-slp-scalable-transaction-graph-rich-mask-tuple-memory,ast-slp-scalable-transaction-graph-helper-mask-memory,ast-slp-scalable-transaction-graph-masked-gather-memory,ast-slp-scalable-transaction-graph-store-sink,ast-slp-scalable-transaction-graph-masked-store-sink,ast-slp-scalable-transaction-graph-gather-scatter-memory
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_scalable_memory_validation_fixture PROPERTIES TIMEOUT 360)
            add_test(
                NAME ast_slp_transaction_graph_shuffle_memory_gather_store_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-memory_gather_store
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
                    --prove-only ast-slp-transaction-graph-memory-pack-reorder,ast-slp-transaction-graph-memory-pack-extract-insert,ast-slp-transaction-graph-memory-gather,ast-slp-transaction-graph-symbolic-gather-index,ast-slp-transaction-graph-masked-symbolic-gather-index,ast-slp-transaction-graph-symbolic-gather-index-constant,ast-slp-transaction-graph-memory-gather-reorder,ast-slp-transaction-graph-memory-gather-shuffle,ast-slp-transaction-graph-memory-gather-extract-insert,ast-slp-transaction-graph-store-sink,ast-slp-transaction-graph-symbolic-store-sink,ast-slp-transaction-graph-masked-symbolic-store-sink,ast-slp-transaction-graph-symbolic-store-sink-constant,ast-slp-transaction-graph-symbolic-store-duplicate-term,ast-slp-transaction-graph-store-scatter,ast-slp-transaction-graph-load-store-memory,ast-slp-transaction-graph-load-store-mayalias-guard,ast-slp-transaction-graph-load-store-aa-guard,ast-slp-transaction-graph-gather-scatter-memory,ast-slp-transaction-graph-symbolic-gather-store-noalias
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_memory_gather_store_validation_fixture PROPERTIES TIMEOUT 420)
            add_test(
                NAME ast_slp_transaction_graph_shuffle_masked_passthru_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-masked_passthru
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
                    --prove-only ast-slp-transaction-graph-symbolic-same-base-load-store,ast-slp-transaction-graph-masked-load-store-memory,ast-slp-transaction-graph-symbolic-undef-passthru-memory,ast-slp-transaction-graph-implicit-undef-passthru-memory,ast-slp-transaction-graph-passthru-alias-memory,ast-slp-transaction-graph-helper-passthru-memory,ast-slp-transaction-graph-undef-passthru-alias-memory,ast-slp-transaction-graph-helper-undef-passthru-memory,ast-slp-transaction-graph-static-mask-index-memory,ast-slp-transaction-graph-variable-mask-index-memory,ast-slp-transaction-graph-symbolic-mask-index-memory,ast-slp-transaction-graph-static-symbolic-mask-index-memory,ast-slp-transaction-graph-guarded-symbolic-mask-index-memory,ast-slp-transaction-graph-static-memory-index
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_masked_passthru_validation_fixture PROPERTIES TIMEOUT 900)
            add_test(
                NAME ast_slp_transaction_graph_shuffle_mask_index_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-mask_index
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
                    --prove-only ast-slp-transaction-graph-masked-gather-scatter-memory,ast-slp-transaction-graph-mask-provenance-memory,ast-slp-transaction-graph-alias-mask-memory,ast-slp-transaction-graph-split-mask-assignment-memory,ast-slp-transaction-graph-branch-mask-memory,ast-slp-transaction-graph-branch-alias-mask-memory,ast-slp-transaction-graph-nested-branch-mask-memory,ast-slp-transaction-graph-branch-store-mask-memory,ast-slp-transaction-graph-generalized-mask-syntax-memory
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_mask_index_validation_fixture PROPERTIES TIMEOUT 720)
            add_test(
                NAME ast_slp_transaction_graph_shuffle_branch_mask_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-branch_mask
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
                    --prove-only ast-slp-transaction-graph-helper-mask-memory,ast-slp-transaction-graph-helper-opaque-mask-memory,ast-slp-transaction-graph-helper-unresolved-opaque-slice,ast-slp-transaction-graph-helper-indexed-slice,ast-slp-transaction-graph-helper-default-args-slice,ast-slp-transaction-graph-helper-simple-multi-return-slice,ast-slp-transaction-graph-helper-opaque-multi-return-slice,ast-slp-transaction-graph-helper-deep-chain-slice,ast-slp-transaction-graph-helper-depth-limit-slice
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_branch_mask_validation_fixture PROPERTIES TIMEOUT 900)
            add_test(
                NAME ast_slp_transaction_graph_shuffle_helper_mask_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-helper_mask
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
                    --prove-only ast-slp-transaction-graph-helper-boolean-mask-memory,ast-slp-transaction-graph-nested-helper-memory,ast-slp-transaction-graph-helper-store-sink,ast-slp-transaction-graph-boolean-mask-memory,ast-slp-transaction-graph-rich-mask-memory,ast-slp-transaction-graph-normalized-mask-memory,ast-slp-transaction-graph-guarded-temp-mask-memory,ast-slp-transaction-graph-guarded-masked-load-store-memory,ast-slp-transaction-graph-guarded-masked-gather-scatter-memory
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_helper_mask_validation_fixture PROPERTIES TIMEOUT 900)
            add_test(
                NAME ast_slp_transaction_graph_shuffle_unsupported_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_unsupported_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-graph-shuffle-unsupported
                    --miner $<TARGET_FILE:cv-mine-pass-source-ast>
            )
            set_tests_properties(ast_slp_transaction_graph_shuffle_unsupported_validation_fixture PROPERTIES TIMEOUT 240)
            add_test(
                NAME slp_transaction_graph_to_ir_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_select_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/select.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_shuffle_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/shuffle.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/select.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/shuffle.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/out --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/unsupported.jsonl --validate-ir --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh && grep -R -q 'icmp ule <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/out && grep -R -q 'select <4 x i1>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/out && grep -R -q 'shufflevector <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/out && grep -R -q '; ir_lowering_mode=transaction-graph' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir/out && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); summary=json.load(open(root/\"summary.json\")); records=[json.loads(line) for line in open(root/\"out/manifest.jsonl\") if line.strip()]; assert summary[\"generated\"] == 2; assert summary[\"graph_ir\"] == {\"used\":2}; assert summary[\"ir_validation\"] == {\"failed\":0,\"passed\":2,\"skipped\":0}; assert summary[\"unsupported_graph_reasons\"] == {}; assert len(records) == 2; assert all(r[\"ir_lowering_mode\"] == \"transaction-graph\" for r in records); assert all(r[\"graph_ir_status\"] == \"used\" for r in records)' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-to-ir"
            )
            add_test(
                NAME slp_transaction_graph_typed_to_ir_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_cast_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/cast.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_extract_insert_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/extract_insert.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_memory_pack_noncontiguous_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/memory_pack_noncontiguous.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/cast.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/extract_insert.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/memory_pack_noncontiguous.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/out --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/unsupported.jsonl --validate-ir --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh && grep -R -q 'zext <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/out && grep -R -q 'add <4 x i64>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/out && grep -R -q 'trunc <4 x i64>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/out && grep -R -q 'extractelement <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/out && grep -R -q 'insertelement <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/out && grep -R -q 'getelementptr i32, ptr %a, i32 6' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir/out && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); summary=json.load(open(root/\"summary.json\")); records=[json.loads(line) for line in open(root/\"out/manifest.jsonl\") if line.strip()]; assert summary[\"generated\"] == 3; assert summary[\"graph_ir\"] == {\"used\":3}; assert summary[\"ir_validation\"] == {\"failed\":0,\"passed\":3,\"skipped\":0}; assert summary[\"unsupported_graph_reasons\"] == {}; assert len(records) == 3; assert all(r[\"ir_lowering_mode\"] == \"transaction-graph\" for r in records); assert all(r[\"graph_ir_status\"] == \"used\" for r in records)' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-typed-to-ir"
            )
            add_test(
                NAME slp_transaction_graph_memory_pack_to_ir_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_memory_pack_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/memory_pack.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_memory_pack_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/memory_pack_reorder.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_memory_pack_noncontiguous_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/memory_pack_noncontiguous.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_symbolic_gather_index_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/symbolic_gather.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/memory_pack.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/memory_pack_reorder.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/memory_pack_noncontiguous.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/symbolic_gather.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/out --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/unsupported.jsonl --validate-ir --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh && grep -R -q 'ptr %a' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/out && grep -R -q 'getelementptr i32, ptr %a' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/out && grep -R -q 'getelementptr i32, ptr %a, i32 6' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/out && grep -R -q 'load i32, ptr' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/out && grep -R -q 'shufflevector <4 x i32>' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir/out && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); summary=json.load(open(root/\"summary.json\")); records=[json.loads(line) for line in open(root/\"out/manifest.jsonl\") if line.strip()]; text=\"\\n\".join(path.read_text() for path in (root/\"out\").glob(\"*.ll\")); assert summary[\"generated\"] == 4; assert summary[\"graph_ir\"] == {\"unsupported\":1,\"used\":3}; assert summary[\"ir_validation\"] == {\"failed\":0,\"passed\":4,\"skipped\":0}; assert summary[\"unsupported_graph_reasons\"] == {\"unsupported-memory-pack-contract:symbolic-gather-pack-v1\":1}; assert sum(r[\"ir_lowering_mode\"] == \"transaction-graph\" for r in records) == 3; assert sum(r[\"graph_ir_status\"] == \"unsupported\" and r[\"graph_ir_reason\"] == \"unsupported-memory-pack-contract:symbolic-gather-pack-v1\" for r in records) == 1; assert text.count(\"load i32, ptr\") >= 12' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-memory-pack-to-ir"
            )
            add_test(
                NAME slp_transaction_graph_store_sink_to_ir_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_store_sink_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/store_sink.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_gather_scatter_memory_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/gather_scatter.json && $<TARGET_FILE:cv-mine-pass-source-ast> --format json --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_graph_symbolic_store_sink_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/symbolic_store_sink.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-slp-transaction-to-ir.py --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/store_sink.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/gather_scatter.json --input ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/symbolic_store_sink.json --out-dir ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/out --summary-json ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/summary.json --unsupported-jsonl ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/unsupported.jsonl --validate-ir --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh && grep -R -q 'ptr %out' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/out && grep -R -q 'getelementptr i32, ptr %out' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/out && grep -R -q 'getelementptr i32, ptr %out, i32 6' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/out && grep -R -q 'store i32' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir/out && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); summary=json.load(open(root/\"summary.json\")); records=[json.loads(line) for line in open(root/\"out/manifest.jsonl\") if line.strip()]; text=\"\\n\".join(path.read_text() for path in (root/\"out\").glob(\"*.ll\")); assert summary[\"generated\"] == 3; assert summary[\"graph_ir\"] == {\"unsupported\":1,\"used\":2}; assert summary[\"ir_validation\"] == {\"failed\":0,\"passed\":3,\"skipped\":0}; assert summary[\"unsupported_graph_reasons\"] == {\"unsupported-store-sink-contract:symbolic-store-pack-v1\":1}; assert sum(r[\"ir_lowering_mode\"] == \"transaction-graph\" for r in records) == 2; assert sum(r[\"graph_ir_status\"] == \"unsupported\" and r[\"graph_ir_reason\"] == \"unsupported-store-sink-contract:symbolic-store-pack-v1\" for r in records) == 1; assert text.count(\"store i32\") >= 8' ${CMAKE_CURRENT_BINARY_DIR}/slp-transaction-graph-store-sink-to-ir"
            )
            add_test(
                NAME ast_slp_transaction_wide_lane_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_reorder_8lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-validation-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-validation-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-validation.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-validation.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"vector-bv32xN\"; assert formal[\"vector_width\"] == 8; assert params[\"transaction.vector_width\"] == 8; assert params[\"transaction.lane_mapping.map\"] == [4,0,6,2,7,3,5,1]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-transaction-reorder-8lane-validated.jsonl"
            )
            add_test(
                NAME ast_slp_scalable_transaction_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_transaction_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-validation-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-validation-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-validation.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-validation.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-vector-bv32\"; assert formal[\"after\"][\"op\"] == \"svshuffle\"; assert params[\"transaction.scalable\"] is True; assert params[\"transaction.scalable_lane_mapping\"][\"map\"] == [2,0,3,1]' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-transaction-validated.jsonl"
            )
            add_test(
                NAME ast_slp_scalable_minmax_transaction_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_minmax_transaction_reorder_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-validation-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-validation-findings.json --format jsonl --min-confidence high --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-validation.jsonl --require-marker probe.slp.vectorize-binop && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-validation.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert formal[\"domain\"] == \"scalable-vector-bv32\"; assert formal[\"after\"][\"op\"] == \"svshuffle\"; assert formal[\"after\"][\"args\"][0][\"op\"] == \"svumax\"; assert params[\"transaction.scalable\"] is True; assert params[\"transaction.scalable_lane_mapping\"][\"map\"] == [2,0,3,1]; assert params[\"transaction.opcode\"] == \"umax\"' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-minmax-transaction-validated.jsonl"
            )
            add_test(
                NAME ast_slp_reduction_transaction_2lane_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_2lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-validation-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-validation-findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-validation.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-validation.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 2; assert params[\"transaction.vector_width\"] == 2; assert params[\"transaction.reduction_lanes\"] == 2' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-2lane-validated.jsonl"
            )
            add_test(
                NAME ast_slp_reduction_transaction_8lane_intent_validation_fixture
                COMMAND
                    /bin/sh -c
                    "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json --require-marker probe.slp.vectorize-reduction ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_reduction_transaction_8lane_snippet.cpp -- -std=c++17 > ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-validation-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-infer-optimization-intent.py --findings ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-validation-findings.json --format jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-validation.jsonl --require-marker probe.slp.vectorize-reduction && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-validate-intent-candidates.py --z3 ${Z3_EXECUTABLE} --input ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-validation.jsonl --out ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-validated.jsonl && ${Python3_EXECUTABLE} -c 'import json,sys; r=json.loads(open(sys.argv[1]).readline()); params=r[\"evidence\"][\"formal_parameters\"]; formal=r[\"intent_candidate\"][\"formal\"]; assert r[\"proof_status\"] == \"proved\"; assert r[\"promotion_status\"] == \"ready\"; assert r[\"evidence\"][\"transaction_lowering\"] == \"formal-ir\"; assert formal[\"vector_width\"] == 8; assert params[\"transaction.vector_width\"] == 8; assert params[\"transaction.reduction_lanes\"] == 8' ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-reduction-transaction-8lane-validated.jsonl"
            )
            add_test(
                NAME ast_slp_scalable_widening_reduction_intent_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_widening_reduction_validation_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-widening-reduction-validation
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
            )
            add_test(
                NAME ast_slp_scalable_fp_reduction_intent_validation_fixture
                COMMAND
                    ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_scalable_fp_reduction_validation_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/ast-slp-scalable-fp-reduction-validation
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
            )
            add_test(
                NAME pass_source_audit_slp_transaction_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-db && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); f=root/\"tests/fixtures/slp_transaction_helper_pack_snippet.cpp\"; out.write_text(json.dumps([{\"directory\":str(root),\"command\":\"clang++ -std=c++17 \"+str(f),\"file\":str(f)}]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-db/compile_commands.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-db/compile_commands.json --out ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --z3 ${Z3_EXECUTABLE} --verify-source-slice-contracts --verify-transaction-formalization --max-incomplete-formal-provenance 0 ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/findings.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/intent-coverage.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/source-slice-contract-verification.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/transaction-formalization-verification.json && grep -q 'cv-verify-source-slice-contract.py' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/commands.log && grep -q 'cv-verify-transaction-formalization.py' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/commands.log && grep -q '^Optimization transactions$' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/intent-coverage.txt && grep -q 'formal_provenance_coverage: passed=1 incomplete=0' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/run-summary.txt && ${Python3_EXECUTABLE} -c 'import json,sys; findings=json.load(open(sys.argv[1])); audit=json.load(open(sys.argv[2])); verification=json.load(open(sys.argv[3])); formalization=json.load(open(sys.argv[4])); summary=json.load(open(sys.argv[5])); records=audit[\"records\"]; tx=audit[\"summary\"][\"optimization_transactions\"]; graph_contract=audit[\"summary\"][\"source_program_graph_contract\"]; provenance=audit[\"summary\"][\"transaction_formal_provenance_coverage\"]; assert len(findings) == 1; assert findings[0][\"marker\"] == \"probe.slp.vectorize-binop\"; assert verification[\"summary\"][\"status\"] == {\"passed\":1}; assert formalization[\"summary\"][\"status\"] == {\"passed\":1}; assert formalization[\"summary\"][\"provenance_coverage\"] == {\"passed\":1}; assert provenance[\"status\"] == {\"passed\":1}; assert summary[\"coverage\"][\"transaction_formal_provenance_coverage\"][\"incomplete\"] == 0; assert audit[\"summary\"][\"records\"] == 1; assert audit[\"summary\"][\"recommendation\"] == {\"covered by source-derived transaction formal IR\":1}; assert graph_contract[\"status\"] == {\"passed\":1}; assert graph_contract[\"gaps\"][\"records\"] == 0; assert summary[\"coverage\"][\"source_program_graph_contract\"][\"status\"] == {\"passed\":1}; assert summary[\"coverage\"][\"source_program_graph_contract\"][\"gaps\"][\"records\"] == 0; assert tx[\"records\"] == 1; assert tx[\"formal_ir\"] == 1 and tx[\"fallback\"] == 0; assert tx[\"kind\"] == {\"slp-vectorize-binop\":1}; assert tx[\"opcode\"] == {\"add\":1}; assert tx[\"consistency\"] == {\"ok\":1}; assert tx[\"with_lane_mapping\"] == 1 and tx[\"with_result_lane_mapping\"] == 1; assert records[0][\"transaction_scalar_lane_pairs\"] == 4; assert records[0][\"recommendation\"] == \"covered by source-derived transaction formal IR\"' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/findings.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/intent-coverage.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/source-slice-contract-verification.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/transaction-formalization-verification.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp/run-summary.json"
            )
            add_test(
                NAME pass_source_audit_slp_transaction_ir_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir-db && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); f=root/\"tests/fixtures/slp_transaction_helper_pack_snippet.cpp\"; out.write_text(json.dumps([{\"directory\":str(root),\"command\":\"clang++ -std=c++17 \"+str(f),\"file\":str(f)}]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir-db/compile_commands.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir-db/compile_commands.json --out ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --z3 ${Z3_EXECUTABLE} --emit-slp-transaction-ir --validate-slp-ir --llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/slp_transaction_helper_pack_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir/slp-transaction-ir/manifest.jsonl && grep -q 'cv-slp-transaction-to-ir.py' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir/commands.log && grep -q '^SLP transaction IR$' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir/run-summary.txt && ${Python3_EXECUTABLE} -c 'import json,sys; summary=json.load(open(sys.argv[1])); records=[json.loads(line) for line in open(sys.argv[2]) if line.strip()]; slp=summary[\"slp_transaction_ir\"]; assert slp[\"enabled\"] is True; assert slp[\"generated\"] == 1; assert slp[\"ir_validation\"] == {\"failed\":0,\"passed\":1,\"skipped\":0}; assert records[0][\"ir_validation_status\"] == \"passed\"; assert records[0][\"transaction_kind\"] == \"slp-vectorize-binop\"' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir/run-summary.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-slp-ir/slp-transaction-ir/manifest.jsonl"
            )
            add_test(
                NAME pass_source_audit_runner_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-db && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); files=[root/\"tests/fixtures/intent_inference_snippet.cpp\", root/\"tests/fixtures/vector_pass_snippet.cpp\"]; out.write_text(json.dumps([{\"directory\":str(root),\"command\":\"clang++ -std=c++17 \"+str(f),\"file\":str(f)} for f in files]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-db/compile_commands.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-db/compile_commands.json --out ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --z3 ${Z3_EXECUTABLE} --emit-smt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/findings.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/intent-coverage.json && test -d ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/intent-smt && grep -q 'cv-mine-pass-source-ast' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/commands.log && grep -q '^O2T Pass Source Intent Summary$' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/intent-summary.txt && ${Python3_EXECUTABLE} -c 'import json,sys; findings=json.load(open(sys.argv[1])); audit=json.load(open(sys.argv[2])); manifest=[json.loads(line) for line in open(sys.argv[3]) if line.strip()]; assert len(findings) == 30; assert audit[\"summary\"][\"records\"] == 30; assert audit[\"summary\"][\"recommendation\"][\"covered by source-derived formal IR\"] == 30; assert sum(r[\"status\"] == \"selected\" for r in manifest) == 2' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/findings.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/intent-coverage.json ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit/source-manifest.jsonl"
            )
            add_test(
                NAME pass_source_audit_compile_db_skip_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-skip-db && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); f=root/\"tests/fixtures/intent_inference_snippet.cpp\"; out.write_text(json.dumps([{\"directory\":str(root),\"command\":\"clang++ -std=c++17 \"+str(f),\"file\":str(f)}]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-skip-db/compile_commands.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-skip-db/compile_commands.json --out ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-skip --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --z3 ${Z3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/vector_pass_snippet.cpp && ${Python3_EXECUTABLE} -c 'import json,sys; manifest=[json.loads(line) for line in open(sys.argv[1]) if line.strip()]; assert sum(r[\"status\"] == \"selected\" for r in manifest) == 1; assert any(r[\"status\"] == \"skipped\" and r[\"reason\"] == \"missing-compile-command\" and r[\"file\"].endswith(\"vector_pass_snippet.cpp\") for r in manifest)' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-skip/source-manifest.jsonl"
            )
            add_test(
                NAME pass_source_audit_require_clean_fixture
                COMMAND
                    /bin/sh -c
                    "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-clean-db && ${Python3_EXECUTABLE} -c 'import json,sys,pathlib; root=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); f=root/\"tests/fixtures/intent_inference_snippet.cpp\"; out.write_text(json.dumps([{\"directory\":str(root),\"command\":\"clang++ -std=c++17 \"+str(f),\"file\":str(f)}]))' ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-clean-db/compile_commands.json && (${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-pass-source-audit.py --compile-commands ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-clean-db/compile_commands.json --out ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-clean --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --z3 ${Z3_EXECUTABLE} --require-clean-mining ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp ${CMAKE_CURRENT_BINARY_DIR}/missing-pass.cpp 2> ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-clean.err; test $? -eq 1) && grep -q 'AST mining issues found' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-clean.err && grep -q 'source-not-found' ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-clean/source-manifest.jsonl"
            )
            add_test(
                NAME pass_source_audit_production_runner_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_source_audit_runner_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-production
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
            )
            add_test(
                NAME pass_source_audit_third_party_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_source_audit_third_party_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-third-party
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir>
                    --z3 ${Z3_EXECUTABLE}
                    --compiler ${CMAKE_CXX_COMPILER}
            )
            add_test(
                NAME pass_source_audit_upstream_dse_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/pass_source_audit_upstream_dse_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/pass-source-audit-upstream-dse
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir>
                    --compiler ${CMAKE_CXX_COMPILER}
            )
            add_test(
                NAME upstream_dse_readiness_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/upstream_dse_readiness_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/upstream-dse-readiness
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir>
                    --compiler ${CMAKE_CXX_COMPILER}
                    --z3 ${Z3_EXECUTABLE}
            )
            add_test(
                NAME optimization_registry_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/optimization_registry_fixture.py
                    ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME ast_bind_marker_coverage_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/ast_bind_marker_coverage_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME ast_mining_metadata_schema_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/ast_mining_metadata_schema_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME scalar_more_ops_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/scalar_more_ops_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/scalar-more-ops
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --z3 ${Z3_EXECUTABLE}
            )
            add_test(
                NAME external_pass_audit_runner_fixture
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/external_pass_audit_runner_fixture.py
                    --repo ${CMAKE_CURRENT_SOURCE_DIR}
                    --work-dir ${CMAKE_CURRENT_BINARY_DIR}/external-pass-audit-runner
                    --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast>
                    --ir-miner $<TARGET_FILE:cv-mine-pass-impl-ir>
                    --z3 ${Z3_EXECUTABLE}
                    --compiler ${CMAKE_CXX_COMPILER}
            )
        endif()
        add_test(
            NAME campaign_driver_ast_miner_intent_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-run-campaign.py --out ${CMAKE_CURRENT_BINARY_DIR}/campaign-ast-intent --ast-miner $<TARGET_FILE:cv-mine-pass-source-ast> --infer-intents --intent-min-confidence high --host-opt ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-opt.sh --host-llvm-as ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/fake-llvm-as.sh ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/intent_inference_snippet.cpp && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-ast-intent/findings.json && test -f ${CMAKE_CURRENT_BINARY_DIR}/campaign-ast-intent/intent-candidates.jsonl && grep -q 'cv-mine-pass-source-ast' ${CMAKE_CURRENT_BINARY_DIR}/campaign-ast-intent/commands.log && grep -q '^candidates: 5$' ${CMAKE_CURRENT_BINARY_DIR}/campaign-ast-intent/intent-summary.txt"
        )
    endif()
    if(TARGET cv-instrument-pass-source)
        add_test(
            NAME clang_instrumentation_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-pass-candidates.json && $<TARGET_FILE:cv-instrument-pass-source> --candidate-file=${CMAKE_CURRENT_BINARY_DIR}/instrumented-pass-candidates.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/instrumented-pass-snippet.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.add-zero\"' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-pass-snippet.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.mem2reg.promotable-alloca\"' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-pass-snippet.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.licm.invariant-op\"' ${CMAKE_CURRENT_BINARY_DIR}/instrumented-pass-snippet.cpp"
        )
        add_test(
            NAME generated_source_marker_context_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-mine-pass-source-ast> --registry ${CMAKE_CURRENT_SOURCE_DIR}/constraints/pass_constraints.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/scalar_more_ops_snippet.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-context.json && ${Python3_EXECUTABLE} -c 'import json,sys; data=json.load(open(sys.argv[1])); by_line={}; [by_line.setdefault(r[\"line\"], set()).add(r[\"marker\"]) for r in data]; assert \"probe.instcombine.sub-zero\" in by_line.get(23,set()); assert \"probe.instcombine.or-zero\" in by_line.get(30,set()); assert \"probe.instcombine.and-allones\" in by_line.get(37,set()); assert \"probe.instcombine.and-self\" in by_line.get(44,set()); assert \"probe.instcombine.add-zero\" not in by_line.get(23,set()); assert \"probe.instcombine.add-zero\" not in by_line.get(30,set()); assert \"probe.instcombine.and-self\" not in by_line.get(37,set()); assert \"probe.instcombine.and-allones\" not in by_line.get(44,set())' ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-context.json"
        )
        add_test(
            NAME generated_source_marker_instrumentation_fixture
            COMMAND
                /bin/sh -c
                "$<TARGET_FILE:cv-instrument-pass-source> ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/scalar_more_ops_snippet.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-instrumented.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.sub-zero\"' ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-instrumented.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.or-zero\"' ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-instrumented.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.and-allones\"' ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-instrumented.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.and-self\"' ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-instrumented.cpp && ! grep -q 'probe.instcombine.and-allones.*m_Deferred' ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-instrumented.cpp && ! grep -q 'probe.instcombine.and-self.*m_AllOnes' ${CMAKE_CURRENT_BINARY_DIR}/generated-source-marker-instrumented.cpp"
        )
        add_test(
            NAME llvm_tree_instrument_rewrite_fixture
            COMMAND
                /bin/sh -c
                "${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-instrument-llvm-tree.py --out-dir ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-rewrite --instrumenter $<TARGET_FILE:cv-instrument-pass-source> --markers probe.instcombine.add-zero,probe.mem2reg.promotable-alloca ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llvm_pass_snippet.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.add-zero\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-rewrite/instrumentation.patch && grep -q 'CV_PASS_PROBE_IF(\"probe.mem2reg.promotable-alloca\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-rewrite/instrumentation.patch && ! grep -q 'CV_PASS_PROBE_IF(\"probe.licm.invariant-op\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-rewrite/instrumentation.patch"
        )
        add_test(
            NAME clang_line_aware_instrumentation_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/line-aware && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\",\"predicate_source\":\"customLegalityCheck(X)\"},{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":8,\"marker\":\"probe.instcombine.mul-one\",\"predicate_source\":\"otherCheck(X)\"}]' > ${CMAKE_CURRENT_BINARY_DIR}/line-aware/candidates.json && $<TARGET_FILE:cv-instrument-pass-source> --candidate-file=${CMAKE_CURRENT_BINARY_DIR}/line-aware/candidates.json --markers=probe.instcombine.add-zero ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp -- -std=c++17 -I${CMAKE_CURRENT_SOURCE_DIR}/include > ${CMAKE_CURRENT_BINARY_DIR}/line-aware/instrumented.cpp && grep -q '#include \"o2t/PassInstrumentation.h\"' ${CMAKE_CURRENT_BINARY_DIR}/line-aware/instrumented.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.add-zero\", customLegalityCheck(X))' ${CMAKE_CURRENT_BINARY_DIR}/line-aware/instrumented.cpp && ! grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.mul-one\", otherCheck(X))' ${CMAKE_CURRENT_BINARY_DIR}/line-aware/instrumented.cpp"
        )
        add_test(
            NAME llvm_tree_line_aware_rewrite_fixture
            COMMAND
                /bin/sh -c
                "mkdir -p ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-line-aware && printf '%s\\n' '[{\"file\":\"${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp\",\"line\":5,\"marker\":\"probe.instcombine.add-zero\",\"pass\":\"instcombine\",\"predicate_kind\":\"matcher\",\"predicate_source\":\"customLegalityCheck(X)\",\"matched_pattern\":\"customLegalityCheck(X)\",\"constraints\":{\"instruction.opcode\":\"add\",\"rhs.value\":0},\"finding_source\":\"llm\",\"confidence\":0.9}]' > ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-line-aware/llm-findings.json && ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/cv-instrument-llvm-tree.py --out-dir ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-line-aware/out --instrumenter $<TARGET_FILE:cv-instrument-pass-source> --llm-findings ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-line-aware/llm-findings.json ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/llm_line_rewrite_snippet.cpp && grep -q 'CV_PASS_PROBE_IF(\"probe.instcombine.add-zero\", customLegalityCheck(X))' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-line-aware/out/instrumentation.patch && grep -q '\"status\": \"rewritten\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-line-aware/out/instrumentation-manifest.jsonl && grep -q '\"finding_source\": \"llm\"' ${CMAKE_CURRENT_BINARY_DIR}/instrument-tree-line-aware/out/instrumentation-manifest.jsonl"
        )
    endif()
endif()
