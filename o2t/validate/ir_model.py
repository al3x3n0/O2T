#!/usr/bin/env python3
"""The typed LLVM IR model: O2T's validators read a real parse, not text.

Every Track B validator used to read LLVM IR with its own regexes -- a signature pattern here, an
instruction pattern there, across ~20 modules. That produced a recurring bug CLASS, not isolated bugs:

  * signature readers that matched a forward-reference CALL SITE instead of the `define`, in five
    modules at once (2026-07 review, round 1);
  * a whole-`.cpp` selector that guessed among same-name overloads (round 2);
  * a signature capture that stopped at the first `)`, so every parameter after an attribute
    containing parentheses -- `ptr byval({ i32, i64 }) %s` is valid LLVM 18 -- was silently dropped;
  * an attributed parameter (`i32 noundef %x`) that failed to match at all, declining the function.

Each failed toward a decline rather than a wrong answer, and each was found by accident. "Mostly
declines" is not a soundness argument, so the syntax layer is now LLVM's own: `tools/cv-ir-dump.cpp`
links against LLVM 18 and emits the module through `llvm::parseAssembly` as JSON, and this module is
the typed view of it. That is the SAME parser `opt` used to produce the IR being validated, so the two
cannot disagree about what the text means, and version drift is impossible because the tool links the
LLVM the pipeline runs.

What this buys, concretely, beyond deleting regexes:
  * function lookup is by identity, so a call site above a definition CANNOT be misread as a signature
    -- the round-1 bug class is gone by construction rather than by anchoring on `define`;
  * poison flags (`nsw`/`nuw`/`exact`/`disjoint`/`nneg`) come from LLVM's own accessors, the surface
    every false proof in the review lived on;
  * types are structured, so `getelementptr` field offsets, vector lane counts and scalable-ness stop
    being string surgery;
  * arbitrary-width constants survive (an i128 literal is not truncated to 64 bits);
  * an unmodeled opcode still ARRIVES, with its name and operands, so a validator declines on it
    explicitly instead of a regex silently not matching.

This module carries NO semantics. It reports what the module says; every interpretation -- what a flag
implies about poison, what an instruction computes -- stays in the validators.

`cv-ir-dump` is REQUIRED: a missing dumper raises `IrDumpUnavailable` rather than falling back to a
text reader, because a silent second parser is exactly the dual-path drift this replaces.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class IrDumpUnavailable(RuntimeError):
    """`cv-ir-dump` could not be located. Track B needs it; there is no text fallback."""


class IrParseError(ValueError):
    """LLVM's parser rejected the module. The message is LLVM's own diagnostic."""


def dump_binary() -> str:
    """Locate `cv-ir-dump`: $O2T_IR_DUMP, then the usual build trees, then PATH."""
    env = os.environ.get("O2T_IR_DUMP")
    if env and Path(env).exists():
        return env
    for cand in (ROOT / "build" / "cv-ir-dump", ROOT / "build-llvm" / "cv-ir-dump"):
        if cand.exists():
            return str(cand)
    found = shutil.which("cv-ir-dump")
    if found:
        return found
    raise IrDumpUnavailable(
        "cv-ir-dump not found. Track B parses IR with LLVM 18's own parser and has no text fallback. "
        "Build it with `cmake -S . -B build -DO2T_WITH_LLVM=ON && cmake --build build --target "
        "cv-ir-dump`, or point $O2T_IR_DUMP at it.")


def available() -> bool:
    try:
        dump_binary()
        return True
    except IrDumpUnavailable:
        return False


# --- the typed view ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Type:
    """An LLVM type as LLVM reports it. `kind` is int|ptr|vector|array|struct|void|float|other."""
    raw: dict

    @property
    def kind(self) -> str:
        return self.raw["kind"]

    @property
    def bits(self) -> int | None:
        return self.raw.get("bits")

    @property
    def addrspace(self) -> int:
        """A pointer's address space (0 unless stated). NOT cosmetic: `null` is only guaranteed
        non-dereferenceable in address space 0, so a fold that is sound on `ptr` need not be on
        `ptr addrspace(1)` -- LLVM's own tests carry `_as1` variants asserting exactly that."""
        return int(self.raw.get("addrspace", 0))

    @property
    def elem(self) -> "Type | None":
        e = self.raw.get("elem")
        return Type(e) if e else None

    @property
    def n(self) -> int | None:
        return self.raw.get("n")

    @property
    def scalable(self) -> bool:
        return bool(self.raw.get("scalable"))

    @property
    def fields(self) -> list["Type"]:
        return [Type(f) for f in self.raw.get("fields", [])]

    @property
    def packed(self) -> bool:
        return bool(self.raw.get("packed"))

    def is_int(self, bits: int | None = None) -> bool:
        return self.kind == "int" and (bits is None or self.bits == bits)

    def __str__(self) -> str:
        k = self.kind
        if k == "int":
            return f"i{self.bits}"
        if k == "vector":
            return f"<{'vscale x ' if self.scalable else ''}{self.n} x {self.elem}>"
        if k == "array":
            return f"[{self.n} x {self.elem}]"
        if k == "struct":
            return "{" + ", ".join(str(f) for f in self.fields) + "}"
        return self.raw.get("name") or self.raw.get("text") or k


@dataclass(frozen=True)
class Value:
    """An operand: a register, a constant, `undef`, `poison`, `null`, a vector or a global."""
    raw: dict

    @property
    def kind(self) -> str:
        return self.raw["kind"]

    @property
    def name(self) -> str | None:
        return self.raw.get("name")

    @property
    def type(self) -> Type | None:
        t = self.raw.get("type")
        return Type(t) if t else None

    @property
    def int_value(self) -> int | None:
        """The constant's value, arbitrary width (i128 literals survive)."""
        v = self.raw.get("value")
        return int(v) if v is not None else None

    @property
    def elements(self) -> list["Value"]:
        return [Value(e) for e in self.raw.get("elems", [])]

    @property
    def is_reg(self) -> bool:
        return self.kind == "reg"

    @property
    def splat_elem(self) -> "Value | None":
        """For a splat constant, the value every lane holds. LLVM answers this for scalable vectors
        too, where there is no element list to enumerate."""
        e = self.raw.get("elem")
        return Value(e) if (self.kind == "splat" and e) else None

    @property
    def is_undef(self) -> bool:
        return self.kind == "undef"

    @property
    def is_poison(self) -> bool:
        return self.kind == "poison"

    def __str__(self) -> str:
        if self.is_reg:
            return self.name or "?"
        if self.kind == "int":
            return str(self.int_value)
        return self.kind


@dataclass(frozen=True)
class Instruction:
    raw: dict

    @property
    def op(self) -> str:
        """LLVM's own opcode name (`add`, `icmp`, `getelementptr`, ...)."""
        return self.raw["op"]

    @property
    def result(self) -> str | None:
        return self.raw.get("result")

    @property
    def type(self) -> Type:
        return Type(self.raw["type"])

    @property
    def flags(self) -> tuple:
        """Poison-generating flags, from LLVM's accessors -- not re-derived from text, and in LLVM's
        own print order. ORDERED deliberately: consumers build SMT strings by iterating this, and an
        unordered container made the emitted formula vary between runs under Python's randomized
        string hashing -- a verifier whose output is not reproducible is not trustworthy, whatever the
        semantics."""
        return tuple(self.raw.get("flags", ()))

    @property
    def pred(self) -> str | None:
        return self.raw.get("pred")

    @property
    def operands(self) -> list[Value]:
        return [Value(o) for o in self.raw.get("operands", [])]

    @property
    def args(self) -> list[Value]:
        """A call's arguments (the raw operand list also carries the callee)."""
        return [Value(a) for a in self.raw.get("args", [])]

    @property
    def callee(self) -> str | None:
        c = self.raw.get("callee")
        return c or None

    @property
    def indirect(self) -> bool:
        return bool(self.raw.get("indirect"))

    @property
    def incoming(self) -> list[tuple[Value, str]]:
        return [(Value(i["value"]), i["block"]) for i in self.raw.get("incoming", [])]

    @property
    def source_type(self) -> Type | None:
        t = self.raw.get("source_type")
        return Type(t) if t else None

    @property
    def src_type(self) -> Type | None:
        t = self.raw.get("src_type")
        return Type(t) if t else None

    @property
    def mask(self) -> list[int]:
        """A shufflevector mask; -1 is an undef/poison lane."""
        return list(self.raw.get("mask", ()))

    @property
    def successors(self) -> list[str]:
        return list(self.raw.get("successors", ()))

    @property
    def conditional(self) -> bool:
        return bool(self.raw.get("conditional"))

    @property
    def align(self) -> int | None:
        return self.raw.get("align")

    @property
    def noundef(self) -> bool:
        """`load ... !noundef`: the loaded value is promised to be neither undef nor poison, and
        it is UB if it ever is. That promise is what makes a `freeze` over the result decidable --
        with no poison there is no nondeterministic choice left to collapse."""
        return bool(self.raw.get("noundef", False))

    @property
    def alloc_type(self) -> Type | None:
        t = self.raw.get("alloc_type")
        return Type(t) if t else None

    def __str__(self) -> str:
        lhs = f"{self.result} = " if self.result else ""
        fl = (" " + " ".join(sorted(self.flags))) if self.flags else ""
        return f"{lhs}{self.op}{fl} " + ", ".join(str(o) for o in self.operands)


@dataclass(frozen=True)
class Block:
    raw: dict

    @property
    def name(self) -> str:
        return self.raw["name"]

    @property
    def instructions(self) -> list[Instruction]:
        return [Instruction(i) for i in self.raw.get("instrs", [])]

    @property
    def terminator(self) -> Instruction | None:
        instrs = self.raw.get("instrs", [])
        return Instruction(instrs[-1]) if instrs else None


@dataclass(frozen=True)
class Param:
    raw: dict

    @property
    def name(self) -> str:
        return self.raw["name"]

    @property
    def type(self) -> Type:
        return Type(self.raw["type"])

    @property
    def attrs(self) -> list[str]:
        return list(self.raw.get("attrs", ()))

    @property
    def noundef(self) -> bool:
        """Declared `noundef`: the argument is guaranteed to be a definite value. Modeling a
        parameter as ONE SMT constant assumes exactly this (see scalar_ir's undef-risk guard)."""
        return any(a.split("(")[0].strip() == "noundef" for a in self.attrs)


@dataclass(frozen=True)
class Function:
    raw: dict

    @property
    def name(self) -> str:
        """The bare name, without the leading `@`."""
        return self.raw["name"].lstrip("@")

    @property
    def params(self) -> list[Param]:
        return [Param(p) for p in self.raw.get("params", [])]

    @property
    def ret_type(self) -> Type:
        return Type(self.raw["ret"])

    @property
    def is_declaration(self) -> bool:
        return bool(self.raw.get("declaration"))

    @property
    def linkage(self) -> str:
        return self.raw.get("linkage", "external")

    @property
    def is_internal(self) -> bool:
        return self.linkage in ("internal", "private")

    @property
    def varargs(self) -> bool:
        return bool(self.raw.get("varargs"))

    @property
    def blocks(self) -> list[Block]:
        return [Block(b) for b in self.raw.get("blocks", [])]

    @property
    def is_single_block(self) -> bool:
        return len(self.raw.get("blocks", [])) == 1

    def instructions(self):
        for b in self.blocks:
            yield from b.instructions

    @property
    def int_params(self) -> dict:
        """Integer parameter name -> width. The common case for the scalar validators."""
        return {p.name: p.type.bits for p in self.params if p.type.is_int()}


@dataclass(frozen=True)
class Module:
    raw: dict

    @property
    def functions(self) -> list[Function]:
        return [Function(f) for f in self.raw.get("functions", [])]

    def function(self, name: str) -> Function | None:
        """Look a function up BY IDENTITY. A call site above the definition cannot be mistaken for
        the signature, and a name that is a substring of another (`foo` vs `foobar`) cannot collide --
        the two failure modes that produced the round-1 and round-2 findings."""
        want = name.lstrip("@")
        for f in self.functions:
            if f.name == want:
                return f
        return None

    @property
    def defined_names(self) -> list[str]:
        return [f.name for f in self.functions if not f.is_declaration]


# --- parsing -------------------------------------------------------------------------------------

def parse(ll_text: str) -> Module:
    """Parse a module with LLVM's own parser. Raises IrParseError on invalid IR (the message is
    LLVM's diagnostic) and IrDumpUnavailable if the tool is not built."""
    return Module(_parse_cached(ll_text))


@lru_cache(maxsize=256)
def _parse_cached(ll_text: str) -> dict:
    """Validators translate the same module repeatedly (before/after, per function), so the
    subprocess round-trip is cached on the exact text."""
    binary = dump_binary()
    with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False) as fh:
        fh.write(ll_text)
        path = fh.name
    try:
        proc = subprocess.run([binary, path], capture_output=True, text=True)
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise IrParseError(proc.stderr.strip() or f"cv-ir-dump exited {proc.returncode}")
    return json.loads(proc.stdout)


def clear_cache() -> None:
    _parse_cached.cache_clear()
