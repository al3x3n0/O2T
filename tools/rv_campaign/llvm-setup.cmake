# Supply installed LLVM helpers without editing upstream RV source.
find_package(LLVM 16 REQUIRED CONFIG)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
list(APPEND CMAKE_MODULE_PATH "${LLVM_CMAKE_DIR}")
include(AddLLVM)
set(LLVM_TOOLS_DIR "${LLVM_TOOLS_BINARY_DIR}")
set(LLVM_LIBS_DIR "${LLVM_LIBRARY_DIR}")
include_directories("${CMAKE_SOURCE_DIR}/src")
