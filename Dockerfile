# A reproducible O2T environment: Ubuntu + Z3 + LLVM 18, no macOS assumptions.
#   docker build -t o2t .
#   docker run --rm o2t                 # -> o2t doctor (toolchain check)
#   docker run --rm o2t o2t verify --selftest
#   docker run --rm o2t sh -c 'cmake -S . -B build && ctest --test-dir build'
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# Ubuntu 24.04 (noble) ships z3, llvm-18, and clang-18 in universe -- no apt.llvm.org repo needed.
# llvm-18-dev/libclang provide the headers the Clang-AST source fixtures use (else they self-skip).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip cmake g++ make git ca-certificates \
        z3 llvm-18 llvm-18-tools llvm-18-dev clang-18 libclang-18-dev \
    && rm -rf /var/lib/apt/lists/*

# Expose LLVM 18 under PLAIN names so both o2t/toolchain.py AND the CMake `command -v opt` fixture
# guards find them. (The Python resolver also accepts the versioned opt-18/clang-18 directly.)
RUN for t in opt lli clang clang++ llvm-as llc; do \
        ln -sf /usr/lib/llvm-18/bin/$t /usr/local/bin/$t; done

WORKDIR /opt/o2t
COPY . .
# Container is throwaway, so install into the system interpreter (PEP 668 override).
RUN pip3 install --no-cache-dir --break-system-packages -e .

# Point the Clang-AST source fixtures at this LLVM's headers (optional; they skip without it).
ENV O2T_CLANG=/usr/lib/llvm-18/bin/clang

CMD ["o2t", "doctor"]
