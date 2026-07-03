"""Shared formal intent IR helpers for O2T tools."""

from __future__ import annotations

from typing import Any, NamedTuple

from o2t.assumption_algebra import normalize_assumptions
from o2t.facts.value_tracking import scalar_assumption_smt


class FormalIrError(ValueError):
    """Raised when a formal intent block is unsupported or malformed."""


class FormalPair(NamedTuple):
    before: str
    after: str
    variables: tuple[str, ...]
    bits: int = 32
    result_sort: str = ""
    before_poison: str = "false"
    after_poison: str = "false"
    poison_equal: str = "true"
    refinement: str = "equality"
    declarations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    variable_bits: tuple[tuple[str, int], ...] = ()
    variable_sorts: tuple[tuple[str, str], ...] = ()


class TypedExpr(NamedTuple):
    smt: str
    sort: str
    lanes: tuple[str, ...] = ()
    poison: str = "false"
    lane_poisons: tuple[str, ...] = ()
    bits: int = 32


VECTOR_WIDTH = 4
CMP_PREDICATES = {"sgt", "sge", "slt", "sle", "ugt", "uge", "ult", "ule", "eq", "ne"}


class FormalContext:
    def __init__(
        self,
        poison_variables: set[str] | None = None,
        vector_width: int = VECTOR_WIDTH,
        scalable_poison_variables: set[str] | None = None,
        variable_bits: dict[str, int] | None = None,
    ) -> None:
        self.poison_variables = poison_variables or set()
        self.scalable_poison_variables = scalable_poison_variables or set()
        self.variable_bits = variable_bits or {}
        self.vector_width = vector_width
        self.declarations: list[str] = []
        self.freeze_index = 0
        self.named_freezes: dict[str, str] = {}

    def poison_for_var(self, name: str) -> str:
        if name not in self.poison_variables:
            return "false"
        poison_name = f"{name}_poison"
        declaration = f"(declare-const {poison_name} Bool)"
        if declaration not in self.declarations:
            self.declarations.append(declaration)
        return poison_name

    def poison_for_scalable_var(self, name: str, lane: int) -> str:
        if name not in self.scalable_poison_variables:
            return "false"
        return self.poison_for_var(f"{name}{lane}")

    def fresh_bv(self, name_hint: str | None = None) -> str:
        if name_hint is not None:
            if name_hint in self.named_freezes:
                return self.named_freezes[name_hint]
            name = f"freeze_{name_hint}"
            if not name.replace("_", "").isalnum():
                raise FormalIrError("formal freeze name must be alphanumeric")
            self.named_freezes[name_hint] = name
            self.declarations.append(f"(declare-const {name} (_ BitVec 32))")
            return name
        name = f"freeze_{self.freeze_index}"
        self.freeze_index += 1
        self.declarations.append(f"(declare-const {name} (_ BitVec 32))")
        return name


def smt_or(values: list[str]) -> str:
    filtered = [value for value in values if value != "false"]
    if not filtered:
        return "false"
    if any(value == "true" for value in filtered):
        return "true"
    if len(filtered) == 1:
        return filtered[0]
    return f"(or {' '.join(filtered)})"


def smt_and(values: list[str]) -> str:
    filtered = [value for value in values if value != "true"]
    if not filtered:
        return "true"
    if any(value == "false" for value in filtered):
        return "false"
    if len(filtered) == 1:
        return filtered[0]
    return f"(and {' '.join(filtered)})"


def poison_equals(left: tuple[str, ...], right: tuple[str, ...]) -> str:
    if len(left) != len(right):
        raise FormalIrError("formal poison lane counts must match")
    terms = [f"(= {a} {b})" for a, b in zip(left, right)]
    if not terms:
        return "true"
    if len(terms) == 1:
        return terms[0]
    return f"(and {' '.join(terms)})"


def expand_blockwise_mask(base_mask: list[int], vector_width: int, source_count: int = 1) -> list[int]:
    base_lanes = len(base_mask)
    if base_lanes <= 0 or vector_width % base_lanes != 0:
        raise FormalIrError("formal scalable shuffle base mask must divide vector width")
    if source_count not in {1, 2}:
        raise FormalIrError("formal scalable shuffle source count is unsupported")
    source_lanes = base_lanes * source_count
    if any(index < 0 or index >= source_lanes for index in base_mask):
        raise FormalIrError("formal scalable shuffle base mask index out of range")
    if source_count == 1 and sorted(base_mask) != list(range(base_lanes)):
        raise FormalIrError("formal scalable shuffle base mask must be a permutation")
    result: list[int] = []
    for block_start in range(0, vector_width, base_lanes):
        for index in base_mask:
            source = index // base_lanes
            lane = index % base_lanes
            result.append((source * vector_width) + block_start + lane)
    return result


def require_args(expr: dict[str, Any], op: str, count: int) -> list[Any]:
    args = expr.get("args")
    if not isinstance(args, list) or len(args) != count:
        raise FormalIrError(f"formal {op} requires {count} args")
    return args


# Which poison-generating flags each binop accepts (LLVM semantics). A flag turns
# the result poison when its no-overflow / exactness precondition is violated --
# this is exactly what makes flag-bearing rewrites refine (drop a flag => sound)
# and flag-adding rewrites unsound (introduce poison the source lacked).
VALID_FLAGS = {
    "bvadd": {"nsw", "nuw"},
    "bvsub": {"nsw", "nuw"},
    "bvmul": {"nsw", "nuw"},
    "bvshl": {"nsw", "nuw"},
    "bvlshr": {"exact"},
    "bvashr": {"exact"},
}


def flag_poison_smt(op: str, flags: list[str], a: str, b: str, n: int) -> str:
    """SMT bool that is true exactly when `op a b` violates one of `flags`."""
    se1 = f"((_ sign_extend 1) {a})", f"((_ sign_extend 1) {b})"
    ze1 = f"((_ zero_extend 1) {a})", f"((_ zero_extend 1) {b})"
    top = lambda x, i: f"((_ extract {i} {i}) {x})"  # noqa: E731
    width_lit = f"(_ bv{n} {n})"
    conds: list[str] = []
    for fl in flags:
        if op == "bvadd" and fl == "nsw":
            s = f"(bvadd {se1[0]} {se1[1]})"
            conds.append(f"(not (= {top(s, n)} {top(s, n - 1)}))")
        elif op == "bvadd" and fl == "nuw":
            conds.append(f"(= {top(f'(bvadd {ze1[0]} {ze1[1]})', n)} #b1)")
        elif op == "bvsub" and fl == "nsw":
            s = f"(bvsub {se1[0]} {se1[1]})"
            conds.append(f"(not (= {top(s, n)} {top(s, n - 1)}))")
        elif op == "bvsub" and fl == "nuw":
            conds.append(f"(bvult {a} {b})")
        elif op == "bvmul" and fl == "nsw":
            prod = f"(bvmul ((_ sign_extend {n}) {a}) ((_ sign_extend {n}) {b}))"
            lo = f"((_ extract {n - 1} 0) {prod})"
            conds.append(f"(not (= {prod} ((_ sign_extend {n}) {lo})))")
        elif op == "bvmul" and fl == "nuw":
            prod = f"(bvmul ((_ zero_extend {n}) {a}) ((_ zero_extend {n}) {b}))"
            conds.append(f"(not (= ((_ extract {2 * n - 1} {n}) {prod}) (_ bv0 {n})))")
        elif op == "bvshl" and fl == "nuw":
            conds.append(f"(or (bvuge {b} {width_lit}) (not (= (bvlshr (bvshl {a} {b}) {b}) {a})))")
        elif op == "bvshl" and fl == "nsw":
            conds.append(f"(or (bvuge {b} {width_lit}) (not (= (bvashr (bvshl {a} {b}) {b}) {a})))")
        elif op in {"bvlshr", "bvashr"} and fl == "exact":
            shr = f"({op} {a} {b})"
            conds.append(f"(or (bvuge {b} {width_lit}) (not (= (bvshl {shr} {b}) {a})))")
    return smt_or(conds) if conds else "false"


# Fast-math flags modeled as POISON (Alive2-faithful): the result is poison when
# the flag's precondition is violated. nnan/ninf are tractable this way; nsz,
# reassoc, contract, arcp involve value-nondeterminism / inexactness and are NOT
# modeled here (a separate, harder slice).
FP_VALID_FLAGS = {"nnan", "ninf"}


def fp_flag_poison(flags: list[str], operands: list[str], result: str) -> str:
    conds: list[str] = []
    for fl in flags:
        if fl == "nnan":
            conds += [f"(fp.isNaN {s})" for s in operands] + [f"(fp.isNaN {result})"]
        elif fl == "ninf":
            conds += [f"(fp.isInfinite {s})" for s in operands] + [f"(fp.isInfinite {result})"]
    return smt_or(conds) if conds else "false"


# SMT-LIB reserved command/keyword tokens. A formal variable that happens to be
# named one of these (e.g. `exit`) is rejected by strict parsers (bitwuzla) even
# though z3 tolerates it -- and bitwuzla also rejects the |quoted| symbol form. So
# such names are emitted with a reserved prefix (a valid plain SMT-LIB identifier
# accepted everywhere). Only the SMT text changes; the registry data is untouched.
SMT_RESERVED = {
    "exit", "reset", "push", "pop", "let", "par", "as", "_", "!", "assert",
    "true", "false", "and", "or", "not", "ite", "distinct", "xor", "forall",
    "exists", "check-sat", "declare-const", "declare-fun", "define-fun",
    "set-logic", "get-model", "select", "store", "concat", "bv", "match",
}
SMT_SYM_PREFIX = "cvsym_"


def smt_sym(name: str) -> str:
    return f"{SMT_SYM_PREFIX}{name}" if name in SMT_RESERVED else name


def pack_vector(lanes: tuple[str, ...]) -> str:
    if not lanes:
        raise FormalIrError("formal vector requires at least one lane")
    return f"(concat {' '.join(lanes)})"


def vector_expr(
    lanes: list[str],
    lane_poisons: list[str] | None = None,
    bits: int = 32,
    sort: str = "vec",
) -> TypedExpr:
    lane_tuple = tuple(lanes)
    poison_tuple = tuple(lane_poisons or ["false"] * len(lanes))
    smt = pack_vector(lane_tuple) if sort == "vec" else ""
    return TypedExpr(smt, sort, lane_tuple, smt_or(list(poison_tuple)), poison_tuple, bits)


def require_bv(expr: Any, variables: set[str], context: FormalContext) -> TypedExpr:
    value = typed_expr_to_smt(expr, variables, context)
    if value.sort != "bv":
        raise FormalIrError("formal expression must be bit-vector")
    return value


def require_vec(expr: Any, variables: set[str], context: FormalContext) -> TypedExpr:
    value = typed_expr_to_smt(expr, variables, context)
    if value.sort != "vec":
        raise FormalIrError("formal expression must be vector")
    return value


def require_fp(expr: Any, variables: set[str], context: FormalContext) -> TypedExpr:
    value = typed_expr_to_smt(expr, variables, context)
    if value.sort != "fp":
        raise FormalIrError("formal expression must be floating-point")
    return value


def require_any_vec(expr: Any, variables: set[str], context: FormalContext) -> TypedExpr:
    value = typed_expr_to_smt(expr, variables, context)
    if value.sort not in {"vec", "fpvec"}:
        raise FormalIrError("formal expression must be vector")
    return value


def require_mask(expr: Any, variables: set[str], context: FormalContext) -> TypedExpr:
    value = typed_expr_to_smt(expr, variables, context)
    if value.sort != "mask":
        raise FormalIrError("formal expression must be mask vector")
    return value


def lane_index(expr: dict[str, Any], op: str, vector_width: int = VECTOR_WIDTH) -> int:
    index = expr.get("index")
    if not isinstance(index, int) or index < 0 or index >= vector_width:
        raise FormalIrError(f"formal {op} index must be in range 0..{vector_width - 1}")
    return index


def typed_expr_to_smt(expr: Any, variables: set[str], context: FormalContext | None = None) -> TypedExpr:
    context = context or FormalContext()
    if not isinstance(expr, dict):
        raise FormalIrError("formal expression must be an object")
    op = expr.get("op")
    if op == "var":
        name = expr.get("name")
        if not isinstance(name, str) or name not in variables:
            raise FormalIrError("formal variable is missing or undeclared")
        return TypedExpr(smt_sym(name), "bv", poison=context.poison_for_var(name), bits=context.variable_bits.get(name, 32))
    if op == "memvar":
        name = expr.get("name")
        if not isinstance(name, str) or name not in variables:
            raise FormalIrError("formal memory variable is missing or undeclared")
        return TypedExpr(smt_sym(name), "mem", bits=32)
    if op == "fpvar":
        name = expr.get("name")
        if not isinstance(name, str) or name not in variables:
            raise FormalIrError("formal floating-point variable is missing or undeclared")
        return TypedExpr(smt_sym(name), "fp", poison=context.poison_for_var(name), bits=32)
    if op == "svar":
        name = expr.get("name")
        if not isinstance(name, str) or name not in variables:
            raise FormalIrError("formal scalable variable is missing or undeclared")
        lanes = [f"{name}{index}" for index in range(context.vector_width)]
        poisons = [context.poison_for_scalable_var(name, index) for index in range(context.vector_width)]
        bits = context.variable_bits.get(f"{name}0", context.variable_bits.get(name, 32))
        return vector_expr(lanes, poisons, bits)
    if op == "sfpvar":
        name = expr.get("name")
        if not isinstance(name, str) or name not in variables:
            raise FormalIrError("formal scalable floating-point variable is missing or undeclared")
        lanes = [f"{name}{index}" for index in range(context.vector_width)]
        poisons = [context.poison_for_scalable_var(name, index) for index in range(context.vector_width)]
        return vector_expr(lanes, poisons, bits=32, sort="fpvec")
    if op == "bvconst":
        bits = expr.get("bits")
        value = expr.get("value")
        if not isinstance(bits, int) or bits <= 0:
            raise FormalIrError("formal bvconst bits must be a positive integer")
        if not isinstance(value, int) or value < 0 or value >= (1 << bits):
            raise FormalIrError("formal bvconst value is out of range")
        # Hex literal for multiples of 4 (keeps every existing 8/16/32/64 encoding
        # byte-identical); SMT-LIB decimal form for odd widths (i1/i17/...), which
        # #x cannot express.
        if bits % 4 == 0:
            return TypedExpr(f"#x{value:0{bits // 4}x}", "bv", bits=bits)
        return TypedExpr(f"(_ bv{value} {bits})", "bv", bits=bits)
    if op == "mem_load":
        args = require_args(expr, op, 2)
        memory = typed_expr_to_smt(args[0], variables, context)
        address = require_bv(args[1], variables, context)
        if memory.sort != "mem":
            raise FormalIrError("formal mem_load requires memory arg")
        if address.bits != 32:
            raise FormalIrError("formal mem_load address must be bv32")
        return TypedExpr(f"(select {memory.smt} {address.smt})", "bv", poison=address.poison, bits=32)
    if op == "mem_store":
        args = require_args(expr, op, 3)
        memory = typed_expr_to_smt(args[0], variables, context)
        address = require_bv(args[1], variables, context)
        value = require_bv(args[2], variables, context)
        if memory.sort != "mem":
            raise FormalIrError("formal mem_store requires memory arg")
        if address.bits != 32 or value.bits != 32:
            raise FormalIrError("formal mem_store address and value must be bv32")
        return TypedExpr(
            f"(store {memory.smt} {address.smt} {value.smt})",
            "mem",
            poison=smt_or([address.poison, value.poison]),
            bits=32,
        )
    if op == "poison":
        args = require_args(expr, op, 1)
        value = typed_expr_to_smt(args[0], variables, context)
        if value.sort in {"vec", "fpvec"}:
            return vector_expr(list(value.lanes), ["true"] * len(value.lanes), bits=value.bits, sort=value.sort)
        if value.sort not in {"bv", "fp"}:
            raise FormalIrError("formal poison requires scalar or vector arg")
        return TypedExpr(value.smt, value.sort, poison="true", bits=value.bits)
    if op == "undef":
        raise FormalIrError("formal undef requires freeze")
    if op == "freeze":
        args = require_args(expr, op, 1)
        raw = args[0]
        name_hint = expr.get("name")
        if name_hint is not None and not isinstance(name_hint, str):
            raise FormalIrError("formal freeze name must be a string")
        if isinstance(raw, dict) and raw.get("op") == "undef":
            return TypedExpr(context.fresh_bv(name_hint), "bv")
        value = typed_expr_to_smt(raw, variables, context)
        if value.sort == "bv":
            fresh = context.fresh_bv(name_hint)
            return TypedExpr(f"(ite {value.poison} {fresh} {value.smt})", "bv", bits=value.bits)
        if value.sort == "vec":
            lanes: list[str] = []
            for lane, poison in zip(value.lanes, value.lane_poisons):
                fresh = context.fresh_bv()
                lanes.append(f"(ite {poison} {fresh} {lane})")
            return vector_expr(lanes, bits=value.bits)
        raise FormalIrError("formal freeze requires bit-vector or vector arg")
    if op in {"fpadd", "fpmul", "fpsub"}:
        args = require_args(expr, op, 2)
        left = require_fp(args[0], variables, context)
        right = require_fp(args[1], variables, context)
        smt_op = {"fpadd": "fp.add", "fpmul": "fp.mul", "fpsub": "fp.sub"}[op]
        result = f"({smt_op} roundNearestTiesToEven {left.smt} {right.smt})"
        poison = smt_or([left.poison, right.poison])
        flags = expr.get("flags")
        if flags:
            if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
                raise FormalIrError("formal flags must be a list of strings")
            invalid = sorted(set(flags) - FP_VALID_FLAGS)
            if invalid:
                raise FormalIrError(f"formal {op} invalid fast-math flags {invalid}; "
                                    f"modeled: {sorted(FP_VALID_FLAGS)}")
            poison = smt_or([poison, fp_flag_poison(flags, [left.smt, right.smt], result)])
        return TypedExpr(result, "fp", poison=poison, bits=32)
    if op == "fpneg":
        args = require_args(expr, op, 1)
        value = require_fp(args[0], variables, context)
        return TypedExpr(f"(fp.neg {value.smt})", "fp", poison=value.poison, bits=32)
    if op == "fpconst":
        kind = expr.get("value")
        consts = {
            "zero": "(_ +zero 8 24)", "negzero": "(_ -zero 8 24)",
            "inf": "(_ +oo 8 24)", "neginf": "(_ -oo 8 24)", "nan": "(_ NaN 8 24)",
            "one": "((_ to_fp 8 24) roundNearestTiesToEven 1.0)",
        }
        if kind not in consts:
            raise FormalIrError(f"formal fpconst value must be one of {sorted(consts)}")
        return TypedExpr(consts[kind], "fp", bits=32)
    if op == "bvneg":
        args = require_args(expr, op, 1)
        value = typed_expr_to_smt(args[0], variables, context)
        if value.sort != "bv":
            raise FormalIrError("formal bvneg requires bit-vector arg")
        return TypedExpr(f"(bvneg {value.smt})", "bv", poison=value.poison, bits=value.bits)
    if op in {
        "bvadd",
        "bvsub",
        "bvmul",
        "bvxor",
        "bvand",
        "bvor",
        "bvshl",
        "bvlshr",
        "bvashr",
        "bvudiv",
        "bvurem",
        "bvsdiv",
        "bvsrem",
        "eq",
        "ne",
        "bvslt",
        "bvsle",
        "bvsgt",
        "bvsge",
        "bvult",
        "bvule",
        "bvugt",
        "bvuge",
    }:
        args = require_args(expr, op, 2)
        left = typed_expr_to_smt(args[0], variables, context)
        right = typed_expr_to_smt(args[1], variables, context)
        if left.sort != "bv" or right.sort != "bv":
            raise FormalIrError(f"formal {op} requires bit-vector args")
        if left.bits != right.bits:
            raise FormalIrError(f"formal {op} requires matching bit widths")
        poison = smt_or([left.poison, right.poison])
        flags = expr.get("flags")
        if flags:
            if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
                raise FormalIrError("formal flags must be a list of strings")
            allowed = VALID_FLAGS.get(op)
            if allowed is None:
                raise FormalIrError(f"formal {op} does not take poison-generating flags")
            invalid = sorted(set(flags) - allowed)
            if invalid:
                raise FormalIrError(f"formal {op} invalid flags {invalid}; allowed {sorted(allowed)}")
            poison = smt_or([poison, flag_poison_smt(op, flags, left.smt, right.smt, left.bits)])
        if op == "eq":
            return TypedExpr(f"(= {left.smt} {right.smt})", "bool", poison=poison)
        if op == "ne":
            return TypedExpr(f"(not (= {left.smt} {right.smt}))", "bool", poison=poison)
        if op in {"bvslt", "bvsle", "bvsgt", "bvsge", "bvult", "bvule", "bvugt", "bvuge"}:
            return TypedExpr(f"({op} {left.smt} {right.smt})", "bool", poison=poison)
        return TypedExpr(f"({op} {left.smt} {right.smt})", "bv", poison=poison, bits=left.bits)
    if op in {"zext", "sext", "trunc"}:
        args = require_args(expr, op, 1)
        value = require_bv(args[0], variables, context)
        bits = expr.get("bits")
        if not isinstance(bits, int) or bits <= 0 or bits % 4 != 0:
            raise FormalIrError(f"formal {op} bits must be a positive multiple of 4")
        if op in {"zext", "sext"}:
            if bits <= value.bits:
                raise FormalIrError(f"formal {op} target bits must be wider")
            extend = bits - value.bits
            smt_op = "zero_extend" if op == "zext" else "sign_extend"
            return TypedExpr(f"((_ {smt_op} {extend}) {value.smt})", "bv", poison=value.poison, bits=bits)
        if bits >= value.bits:
            raise FormalIrError("formal trunc target bits must be narrower")
        return TypedExpr(f"((_ extract {bits - 1} 0) {value.smt})", "bv", poison=value.poison, bits=bits)
    if op in {"vzext", "vsext", "vtrunc"}:
        args = require_args(expr, op, 1)
        value = require_vec(args[0], variables, context)
        bits = expr.get("bits")
        if not isinstance(bits, int) or bits <= 0 or bits % 4 != 0:
            raise FormalIrError(f"formal {op} bits must be a positive multiple of 4")
        if op in {"vzext", "vsext"}:
            if bits <= value.bits:
                raise FormalIrError(f"formal {op} target bits must be wider")
            extend = bits - value.bits
            smt_op = "zero_extend" if op == "vzext" else "sign_extend"
            return vector_expr(
                [f"((_ {smt_op} {extend}) {lane})" for lane in value.lanes],
                list(value.lane_poisons),
                bits,
            )
        if bits >= value.bits:
            raise FormalIrError("formal vtrunc target bits must be narrower")
        return vector_expr(
            [f"((_ extract {bits - 1} 0) {lane})" for lane in value.lanes],
            list(value.lane_poisons),
            bits,
        )
    if op == "vec":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != context.vector_width:
            raise FormalIrError(f"formal vec requires {context.vector_width} args")
        lanes = [require_bv(arg, variables, context) for arg in args]
        if len({lane.bits for lane in lanes}) != 1:
            raise FormalIrError("formal vec lanes must have matching bit widths")
        return vector_expr([lane.smt for lane in lanes], [lane.poison for lane in lanes], lanes[0].bits)
    if op == "fpvec":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != context.vector_width:
            raise FormalIrError(f"formal fpvec requires {context.vector_width} args")
        lanes = [require_fp(arg, variables, context) for arg in args]
        return vector_expr([lane.smt for lane in lanes], [lane.poison for lane in lanes], bits=32, sort="fpvec")
    if op in {"vsplat", "svsplat"}:
        args = require_args(expr, op, 1)
        lane = require_bv(args[0], variables, context)
        return vector_expr([lane.smt] * context.vector_width, [lane.poison] * context.vector_width, lane.bits)
    if op == "svindexed_mask":
        base_lanes = expr.get("base_lanes")
        entries = expr.get("entries")
        if not isinstance(base_lanes, int) or base_lanes <= 0 or context.vector_width % base_lanes != 0:
            raise FormalIrError("formal svindexed_mask base_lanes must divide vector width")
        if not isinstance(entries, list) or len(entries) != base_lanes:
            raise FormalIrError("formal svindexed_mask entries must match base_lanes")
        lanes: list[str] = []
        poisons: list[str] = []
        for lane in range(context.vector_width):
            base_lane = lane % base_lanes
            block_start = lane - base_lane
            entry = entries[base_lane]
            if not isinstance(entry, dict):
                raise FormalIrError("formal svindexed_mask entry must be an object")
            kind = entry.get("kind")
            name = entry.get("name")
            if not isinstance(name, str) or name not in variables:
                raise FormalIrError("formal svindexed_mask variable is missing or undeclared")
            if kind == "indexed":
                index = entry.get("index")
                if not isinstance(index, int) or index < 0 or index >= base_lanes:
                    raise FormalIrError("formal svindexed_mask index must be in base lane range")
                source_lane = block_start + index
                lanes.append(f"{name}{source_lane}")
                poisons.append(context.poison_for_scalable_var(name, source_lane))
            elif kind == "symbolic":
                lanes.append(f"{name}{lane}")
                poisons.append(context.poison_for_scalable_var(name, lane))
            else:
                raise FormalIrError("formal svindexed_mask entry kind is unsupported")
        return vector_expr(lanes, poisons, bits=32)
    if op == "svmask_tuple":
        base_lanes = expr.get("base_lanes")
        entries = expr.get("entries")
        if not isinstance(base_lanes, int) or base_lanes <= 0 or context.vector_width % base_lanes != 0:
            raise FormalIrError("formal svmask_tuple base_lanes must divide vector width")
        if not isinstance(entries, list) or len(entries) != base_lanes:
            raise FormalIrError("formal svmask_tuple entries must match base_lanes")

        predicate_ops = {
            "eq": "eq",
            "ne": "ne",
            "slt": "bvslt",
            "sle": "bvsle",
            "sgt": "bvsgt",
            "sge": "bvsge",
            "ult": "bvult",
            "ule": "bvule",
            "ugt": "bvugt",
            "uge": "bvuge",
        }

        def operand_to_smt(operand: Any, lane: int, block_start: int) -> TypedExpr:
            if not isinstance(operand, dict):
                raise FormalIrError("formal svmask_tuple operand must be an object")
            kind = operand.get("kind")
            if kind == "const":
                value = operand.get("value")
                if not isinstance(value, int) or value < 0 or value >= (1 << 32):
                    raise FormalIrError("formal svmask_tuple const operand is out of range")
                return TypedExpr(f"#x{value:08x}", "bv", bits=32)
            name = operand.get("name")
            if not isinstance(name, str) or name not in variables:
                raise FormalIrError("formal svmask_tuple variable is missing or undeclared")
            if kind == "indexed":
                index = operand.get("index")
                if not isinstance(index, int) or index < 0 or index >= base_lanes:
                    raise FormalIrError("formal svmask_tuple index must be in base lane range")
                source_lane = block_start + index
                return TypedExpr(
                    f"{name}{source_lane}",
                    "bv",
                    poison=context.poison_for_scalable_var(name, source_lane),
                    bits=context.variable_bits.get(f"{name}{source_lane}", context.variable_bits.get(name, 32)),
                )
            if kind == "lane":
                return TypedExpr(
                    f"{name}{lane}",
                    "bv",
                    poison=context.poison_for_scalable_var(name, lane),
                    bits=context.variable_bits.get(f"{name}{lane}", context.variable_bits.get(name, 32)),
                )
            raise FormalIrError("formal svmask_tuple operand kind is unsupported")

        def condition_to_smt(condition: Any, lane: int, block_start: int) -> TypedExpr:
            if not isinstance(condition, dict):
                raise FormalIrError("formal svmask_tuple condition must be an object")
            condition_op = condition.get("op")
            if condition_op == "const":
                value = condition.get("value")
                if value is not True and value is not False:
                    raise FormalIrError("formal svmask_tuple const condition must be boolean")
                return TypedExpr("true" if value else "false", "bool")
            if condition_op == "icmp":
                predicate = condition.get("predicate")
                if not isinstance(predicate, str) or predicate not in predicate_ops:
                    raise FormalIrError("formal svmask_tuple predicate is unsupported")
                left = operand_to_smt(condition.get("lhs"), lane, block_start)
                right = operand_to_smt(condition.get("rhs"), lane, block_start)
                if left.bits != right.bits:
                    raise FormalIrError("formal svmask_tuple icmp requires matching bit widths")
                compare_op = predicate_ops[predicate]
                smt = (
                    f"(= {left.smt} {right.smt})"
                    if compare_op == "eq"
                    else f"(not (= {left.smt} {right.smt}))"
                    if compare_op == "ne"
                    else f"({compare_op} {left.smt} {right.smt})"
                )
                return TypedExpr(smt, "bool", poison=smt_or([left.poison, right.poison]))
            if condition_op in {"and", "or"}:
                args = condition.get("args")
                if not isinstance(args, list) or len(args) != 2:
                    raise FormalIrError(f"formal svmask_tuple {condition_op} requires two args")
                left = condition_to_smt(args[0], lane, block_start)
                right = condition_to_smt(args[1], lane, block_start)
                smt = smt_and([left.smt, right.smt]) if condition_op == "and" else smt_or([left.smt, right.smt])
                return TypedExpr(smt, "bool", poison=smt_or([left.poison, right.poison]))
            if condition_op == "not":
                args = condition.get("args")
                if not isinstance(args, list) or len(args) != 1:
                    raise FormalIrError("formal svmask_tuple not requires one arg")
                value = condition_to_smt(args[0], lane, block_start)
                return TypedExpr(f"(not {value.smt})", "bool", poison=value.poison)
            if condition_op == "select":
                args = condition.get("args")
                if not isinstance(args, list) or len(args) != 3:
                    raise FormalIrError("formal svmask_tuple select requires three args")
                selector = condition_to_smt(args[0], lane, block_start)
                then_value = condition_to_smt(args[1], lane, block_start)
                else_value = condition_to_smt(args[2], lane, block_start)
                return TypedExpr(
                    f"(ite {selector.smt} {then_value.smt} {else_value.smt})",
                    "bool",
                    poison=smt_or([selector.poison, then_value.poison, else_value.poison]),
                )
            raise FormalIrError("formal svmask_tuple condition op is unsupported")

        lanes: list[str] = []
        poisons: list[str] = []
        for lane in range(context.vector_width):
            base_lane = lane % base_lanes
            block_start = lane - base_lane
            value = condition_to_smt(entries[base_lane], lane, block_start)
            lanes.append(value.smt)
            poisons.append(value.poison)
        return vector_expr(lanes, poisons, bits=1, sort="mask")
    if op in {
        "vadd",
        "vsub",
        "vmul",
        "vxor",
        "vand",
        "vor",
        "vshl",
        "vlshr",
        "vashr",
        "svadd",
        "svsub",
        "svmul",
        "svxor",
        "svand",
        "svor",
        "svshl",
        "svlshr",
        "svashr",
    }:
        args = require_args(expr, op, 2)
        left = require_vec(args[0], variables, context)
        right = require_vec(args[1], variables, context)
        if left.bits != right.bits:
            raise FormalIrError(f"formal {op} requires matching vector lane bit widths")
        scalar_op = {
            "vadd": "bvadd",
            "vsub": "bvsub",
            "vmul": "bvmul",
            "vxor": "bvxor",
            "vand": "bvand",
            "vor": "bvor",
            "vshl": "bvshl",
            "vlshr": "bvlshr",
            "vashr": "bvashr",
            "svadd": "bvadd",
            "svsub": "bvsub",
            "svmul": "bvmul",
            "svxor": "bvxor",
            "svand": "bvand",
            "svor": "bvor",
            "svshl": "bvshl",
            "svlshr": "bvlshr",
            "svashr": "bvashr",
        }[op]
        return vector_expr(
            [f"({scalar_op} {a} {b})" for a, b in zip(left.lanes, right.lanes)],
            [smt_or([a, b]) for a, b in zip(left.lane_poisons, right.lane_poisons)],
            left.bits,
        )
    if op in {"vsmin", "vsmax", "vumin", "vumax", "svsmin", "svsmax", "svumin", "svumax"}:
        args = require_args(expr, op, 2)
        left = require_vec(args[0], variables, context)
        right = require_vec(args[1], variables, context)
        if left.bits != right.bits:
            raise FormalIrError(f"formal {op} requires matching vector lane bit widths")
        compare_op = {
            "vsmin": "bvslt",
            "vsmax": "bvsgt",
            "vumin": "bvult",
            "vumax": "bvugt",
            "svsmin": "bvslt",
            "svsmax": "bvsgt",
            "svumin": "bvult",
            "svumax": "bvugt",
        }[op]
        return vector_expr(
            [
                f"(ite ({compare_op} {a} {b}) {a} {b})"
                for a, b in zip(left.lanes, right.lanes)
            ],
            [smt_or([a, b]) for a, b in zip(left.lane_poisons, right.lane_poisons)],
            left.bits,
        )
    if op in {"vicmp", "svicmp"}:
        args = require_args(expr, op, 2)
        left = require_vec(args[0], variables, context)
        right = require_vec(args[1], variables, context)
        if left.bits != right.bits:
            raise FormalIrError(f"formal {op} requires matching vector lane bit widths")
        predicate = expr.get("predicate")
        predicate_ops = {
            "eq": "eq",
            "ne": "ne",
            "slt": "bvslt",
            "sle": "bvsle",
            "sgt": "bvsgt",
            "sge": "bvsge",
            "ult": "bvult",
            "ule": "bvule",
            "ugt": "bvugt",
            "uge": "bvuge",
        }
        if not isinstance(predicate, str) or predicate not in predicate_ops:
            raise FormalIrError(f"formal {op} predicate is unsupported")
        compare_op = predicate_ops[predicate]
        lanes = [
            f"(= {a} {b})" if compare_op == "eq"
            else f"(not (= {a} {b}))" if compare_op == "ne"
            else f"({compare_op} {a} {b})"
            for a, b in zip(left.lanes, right.lanes)
        ]
        return vector_expr(
            lanes,
            [smt_or([a, b]) for a, b in zip(left.lane_poisons, right.lane_poisons)],
            bits=1,
            sort="mask",
        )
    if op in {"vselect", "svselect"}:
        args = require_args(expr, op, 3)
        condition = require_mask(args[0], variables, context)
        then_value = require_vec(args[1], variables, context)
        else_value = require_vec(args[2], variables, context)
        if then_value.bits != else_value.bits:
            raise FormalIrError(f"formal {op} requires matching value lane bit widths")
        if len(condition.lanes) != len(then_value.lanes):
            raise FormalIrError(f"formal {op} requires matching mask and value lane counts")
        return vector_expr(
            [
                f"(ite {cond} {then_lane} {else_lane})"
                for cond, then_lane, else_lane in zip(condition.lanes, then_value.lanes, else_value.lanes)
            ],
            [
                smt_or([cond_poison, then_poison, else_poison])
                for cond_poison, then_poison, else_poison in zip(
                    condition.lane_poisons,
                    then_value.lane_poisons,
                    else_value.lane_poisons,
                )
            ],
            then_value.bits,
        )
    if op == "svmask_not":
        args = require_args(expr, op, 1)
        value = require_mask(args[0], variables, context)
        return vector_expr(
            [f"(not {lane})" for lane in value.lanes],
            list(value.lane_poisons),
            bits=1,
            sort="mask",
        )
    if op in {"svmask_and", "svmask_or"}:
        args = require_args(expr, op, 2)
        left = require_mask(args[0], variables, context)
        right = require_mask(args[1], variables, context)
        if len(left.lanes) != len(right.lanes):
            raise FormalIrError(f"formal {op} requires matching lane counts")
        return vector_expr(
            [
                smt_and([a, b]) if op == "svmask_and" else smt_or([a, b])
                for a, b in zip(left.lanes, right.lanes)
            ],
            [smt_or([a, b]) for a, b in zip(left.lane_poisons, right.lane_poisons)],
            bits=1,
            sort="mask",
        )
    if op == "svmask_select":
        args = require_args(expr, op, 3)
        condition = require_mask(args[0], variables, context)
        then_value = require_mask(args[1], variables, context)
        else_value = require_mask(args[2], variables, context)
        if len(condition.lanes) != len(then_value.lanes) or len(condition.lanes) != len(else_value.lanes):
            raise FormalIrError("formal svmask_select requires matching lane counts")
        return vector_expr(
            [
                f"(ite {cond} {then_lane} {else_lane})"
                for cond, then_lane, else_lane in zip(condition.lanes, then_value.lanes, else_value.lanes)
            ],
            [
                smt_or([cond_poison, then_poison, else_poison])
                for cond_poison, then_poison, else_poison in zip(
                    condition.lane_poisons,
                    then_value.lane_poisons,
                    else_value.lane_poisons,
                )
            ],
            bits=1,
            sort="mask",
        )
    if op == "vabs":
        args = require_args(expr, op, 1)
        value = require_vec(args[0], variables, context)
        zero = f"#x{0:0{value.bits // 4}x}"
        return vector_expr(
            [
                f"(ite (bvslt {lane} {zero}) (bvneg {lane}) {lane})"
                for lane in value.lanes
            ],
            list(value.lane_poisons),
            value.bits,
        )
    if op in {"vextract", "svextract"}:
        args = require_args(expr, op, 1)
        value = require_vec(args[0], variables, context)
        index = lane_index(expr, op, context.vector_width)
        return TypedExpr(value.lanes[index], "bv", poison=value.lane_poisons[index], bits=value.bits)
    if op in {"vinsert", "svinsert"}:
        args = require_args(expr, op, 2)
        value = require_vec(args[0], variables, context)
        lane = require_bv(args[1], variables, context)
        index = lane_index(expr, op, context.vector_width)
        lanes = list(value.lanes)
        poisons = list(value.lane_poisons)
        lanes[index] = lane.smt
        poisons[index] = lane.poison
        return vector_expr(lanes, poisons, value.bits)
    if op == "vshuffle":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) not in {1, 2}:
            raise FormalIrError("formal vshuffle requires one or two vector args")
        vectors = [require_any_vec(arg, variables, context) for arg in args]
        if len({vector.sort for vector in vectors}) != 1:
            raise FormalIrError("formal vshuffle requires matching vector sorts")
        mask = expr.get("mask")
        if not isinstance(mask, list) or len(mask) != context.vector_width or not all(isinstance(index, int) for index in mask):
            raise FormalIrError("formal vshuffle mask must match vector width")
        source_lanes = [lane for vector in vectors for lane in vector.lanes]
        source_poisons = [poison for vector in vectors for poison in vector.lane_poisons]
        lanes: list[str] = []
        poisons: list[str] = []
        for index in mask:
            if index < 0 or index >= len(source_lanes):
                raise FormalIrError("formal vshuffle mask index out of range")
            lanes.append(source_lanes[index])
            poisons.append(source_poisons[index])
        return vector_expr(lanes, poisons, vectors[0].bits, sort=vectors[0].sort)
    if op == "svshuffle":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) not in {1, 2}:
            raise FormalIrError("formal svshuffle requires one or two vector args")
        vectors = [require_any_vec(arg, variables, context) for arg in args]
        if len({vector.sort for vector in vectors}) != 1:
            raise FormalIrError("formal svshuffle requires matching vector sorts")
        if len({vector.bits for vector in vectors}) != 1:
            raise FormalIrError("formal svshuffle requires matching vector lane bit widths")
        base_mask = expr.get("base_mask")
        if not isinstance(base_mask, list) or not all(isinstance(index, int) for index in base_mask):
            raise FormalIrError("formal svshuffle base_mask must be an integer array")
        mask = expand_blockwise_mask(base_mask, context.vector_width, len(vectors))
        source_lanes = [lane for vector in vectors for lane in vector.lanes]
        source_poisons = [poison for vector in vectors for poison in vector.lane_poisons]
        lanes = [source_lanes[index] for index in mask]
        poisons = [source_poisons[index] for index in mask]
        return vector_expr(lanes, poisons, vectors[0].bits, sort=vectors[0].sort)
    if op in {
        "vreduce_add",
        "vreduce_mul",
        "vreduce_and",
        "vreduce_or",
        "vreduce_xor",
        "vreduce_smin",
        "vreduce_smax",
        "vreduce_umin",
        "vreduce_umax",
        "svreduce_add",
        "svreduce_mul",
        "svreduce_and",
        "svreduce_or",
        "svreduce_xor",
        "svreduce_smin",
        "svreduce_smax",
        "svreduce_umin",
        "svreduce_umax",
    }:
        args = require_args(expr, op, 1)
        value = require_vec(args[0], variables, context)
        result = value.lanes[0]
        scalar_op = {
            "vreduce_add": "bvadd",
            "vreduce_mul": "bvmul",
            "vreduce_and": "bvand",
            "vreduce_or": "bvor",
            "vreduce_xor": "bvxor",
            "svreduce_add": "bvadd",
            "svreduce_mul": "bvmul",
            "svreduce_and": "bvand",
            "svreduce_or": "bvor",
            "svreduce_xor": "bvxor",
        }.get(op)
        compare_op = {
            "vreduce_smin": "bvslt",
            "vreduce_smax": "bvsgt",
            "vreduce_umin": "bvult",
            "vreduce_umax": "bvugt",
            "svreduce_smin": "bvslt",
            "svreduce_smax": "bvsgt",
            "svreduce_umin": "bvult",
            "svreduce_umax": "bvugt",
        }.get(op)
        for lane in value.lanes[1:]:
            if scalar_op is not None:
                result = f"({scalar_op} {result} {lane})"
            else:
                result = f"(ite ({compare_op} {result} {lane}) {result} {lane})"
        return TypedExpr(result, "bv", poison=value.poison, bits=value.bits)
    if op in {"fpreduce_add", "fpreduce_mul", "svfpreduce_add", "svfpreduce_mul"}:
        args = require_args(expr, op, 1)
        value = typed_expr_to_smt(args[0], variables, context)
        if value.sort != "fpvec":
            raise FormalIrError(f"formal {op} requires floating-point vector arg")
        smt_op = "fp.add" if op in {"fpreduce_add", "svfpreduce_add"} else "fp.mul"
        result = value.lanes[0]
        for lane in value.lanes[1:]:
            result = f"({smt_op} roundNearestTiesToEven {result} {lane})"
        return TypedExpr(result, "fp", poison=value.poison, bits=32)
    if op == "not":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 1:
            raise FormalIrError("formal not requires one arg")
        value = typed_expr_to_smt(args[0], variables, context)
        if value.sort != "bool":
            raise FormalIrError("formal not requires a boolean arg")
        return TypedExpr(f"(not {value.smt})", "bool", poison=value.poison)
    if op in {"and", "or"}:
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 2:
            raise FormalIrError(f"formal {op} requires two args")
        left = typed_expr_to_smt(args[0], variables, context)
        right = typed_expr_to_smt(args[1], variables, context)
        if left.sort != "bool" or right.sort != "bool":
            raise FormalIrError(f"formal {op} requires boolean args")
        smt = smt_and([left.smt, right.smt]) if op == "and" else smt_or([left.smt, right.smt])
        return TypedExpr(smt, "bool", poison=smt_or([left.poison, right.poison]))
    if op == "ite":
        args = expr.get("args")
        if not isinstance(args, list) or len(args) != 3:
            raise FormalIrError("formal ite requires three args")
        condition = typed_expr_to_smt(args[0], variables, context)
        then_value = typed_expr_to_smt(args[1], variables, context)
        else_value = typed_expr_to_smt(args[2], variables, context)
        if condition.sort != "bool":
            raise FormalIrError("formal ite condition must be boolean")
        if then_value.sort != else_value.sort:
            raise FormalIrError("formal ite branches must have matching sorts")
        poison = smt_or([condition.poison, f"(ite {condition.smt} {then_value.poison} {else_value.poison})"])
        if then_value.sort == "vec":
            lanes = [f"(ite {condition.smt} {a} {b})" for a, b in zip(then_value.lanes, else_value.lanes)]
            poisons = [
                smt_or([condition.poison, f"(ite {condition.smt} {a} {b})"])
                for a, b in zip(then_value.lane_poisons, else_value.lane_poisons)
            ]
            return vector_expr(lanes, poisons, bits=then_value.bits)
        return TypedExpr(f"(ite {condition.smt} {then_value.smt} {else_value.smt})", then_value.sort, poison=poison)
    raise FormalIrError(f"unsupported formal op: {op}")


def expr_to_smt(expr: Any, variables: set[str]) -> str:
    return typed_expr_to_smt(expr, variables).smt


def pair_for_formal(formal: Any) -> FormalPair:
    instances = pair_instances_for_formal(formal)
    if len(instances) != 1:
        raise FormalIrError("formal has multiple proof instances")
    return instances[0][1]


def pair_instances_for_formal(formal: Any) -> list[tuple[int | None, FormalPair]]:
    if not isinstance(formal, dict):
        raise FormalIrError("formal must be an object")
    if formal.get("domain") not in {"scalar-bv32", "scalar-fp32", "cfg-bv32", "memory-bv32", "loop-bv32", "global-initializer-observable-v1", "vector-bv32x4", "vector-bv32xN", "scalable-vector-bv32", "scalable-scalar-bv32", "scalable-scalar-fp32"}:
        raise FormalIrError("unsupported formal domain")
    if formal.get("equivalence") not in {"result", "reachable-result", "loaded-value", "observable-result", "loop-result", "vector-result"}:
        raise FormalIrError("unsupported formal equivalence")
    domain = formal.get("domain")
    if domain == "global-initializer-observable-v1":
        if formal.get("equivalence") != "observable-result":
            raise FormalIrError("formal global initializer equivalence must be observable-result")
        if formal.get("contract") != "remove-global-initializer-if-dead-v1":
            raise FormalIrError("formal global initializer contract is unsupported")
        if formal.get("observability_model") != "local-unobservable-initializer-v1":
            raise FormalIrError("formal global initializer observability_model is unsupported")
        if formal.get("rewrite_api") != "setInitializer":
            raise FormalIrError("formal global initializer rewrite_api is unsupported")
        if formal.get("replacement_kind") != "default-null-initializer":
            raise FormalIrError("formal global initializer replacement_kind is unsupported")
        if formal.get("witness_model") != "global-initializer-default-null-family-v1":
            raise FormalIrError("formal global initializer witness_model is unsupported")
        if formal.get("required_witness_cases") != ["i32", "ptr", "array"]:
            raise FormalIrError("formal global initializer required_witness_cases are unsupported")
        required_safety = formal.get("required_safety_facts")
        if required_safety != ["initializer-dead", "local-linkage", "no-uses"]:
            raise FormalIrError("formal global initializer required_safety_facts are unsupported")
        refinement = str(formal.get("refinement", "equality"))
        if refinement not in {"equality", "refinement"}:
            raise FormalIrError("unsupported formal refinement")
        return [(
            None,
            FormalPair(
                before="a",
                after="a",
                variables=("a",),
                bits=32,
                result_sort="(_ BitVec 32)",
                refinement=refinement,
            ),
        )]
    variables = formal.get("variables")
    if not isinstance(variables, list) or not all(isinstance(name, str) for name in variables):
        raise FormalIrError("formal variables must be a string array")
    variable_set = set(variables)
    if not variable_set:
        raise FormalIrError("formal variables must not be empty")
    raw_variable_bits = formal.get("variable_bits", {})
    if raw_variable_bits is None:
        raw_variable_bits = {}
    if not isinstance(raw_variable_bits, dict):
        raise FormalIrError("formal variable_bits must be an object")
    variable_bits: dict[str, int] = {}
    for name, bits in raw_variable_bits.items():
        if not isinstance(name, str) or name not in variable_set:
            raise FormalIrError("formal variable_bits keys must be declared variables")
        # Any positive width is provable (declarations + bvconst handle odd widths);
        # this enables i1/i17 multi-width coverage. Vectors/casts keep their own
        # mult-of-4 constraints where the encoding needs it.
        if not isinstance(bits, int) or bits <= 0:
            raise FormalIrError("formal variable_bits values must be positive integers")
        variable_bits[name] = bits
    raw_variable_sorts = formal.get("variable_sorts", {})
    if raw_variable_sorts is None:
        raw_variable_sorts = {}
    if not isinstance(raw_variable_sorts, dict):
        raise FormalIrError("formal variable_sorts must be an object")
    variable_sort_overrides: dict[str, str] = {}
    for name, sort in raw_variable_sorts.items():
        if not isinstance(name, str) or name not in variable_set:
            raise FormalIrError("formal variable_sorts keys must be declared variables")
        if sort not in {"memory-bv32"}:
            raise FormalIrError("formal variable_sorts values are unsupported")
        variable_sort_overrides[name] = "(Array (_ BitVec 32) (_ BitVec 32))"
    poison_variables = formal.get("poison_variables", [])
    if poison_variables is None:
        poison_variables = []
    if not isinstance(poison_variables, list) or not all(isinstance(name, str) for name in poison_variables):
        raise FormalIrError("formal poison_variables must be a string array")
    unknown_poison = set(poison_variables) - variable_set
    if unknown_poison:
        raise FormalIrError("formal poison variable is undeclared")
    refinement = str(formal.get("refinement", "equality"))
    if refinement not in {"equality", "refinement"}:
        raise FormalIrError("unsupported formal refinement")
    raw_assumptions = formal.get("assumptions", [])
    if raw_assumptions is None:
        raw_assumptions = []
    if not isinstance(raw_assumptions, list):
        raise FormalIrError("formal assumptions must be an array")
    assumption_algebra = normalize_assumptions(raw_assumptions)
    contradictions = assumption_algebra.get("contradictions") or []
    if contradictions:
        raise FormalIrError("formal contradictory assumptions: " + "; ".join(str(item) for item in contradictions))
    raw_assumptions = assumption_algebra.get("assumptions", raw_assumptions)
    fixed_vector_width = VECTOR_WIDTH
    raw_vector_width = formal.get("vector_width")
    if raw_vector_width is not None:
        if not isinstance(raw_vector_width, int) or raw_vector_width not in {2, 4, 8, 16, 32, 64}:
            raise FormalIrError("formal vector_width must be one of 2, 4, 8, 16, 32, or 64")
        fixed_vector_width = raw_vector_width
    elif domain == "vector-bv32xN":
        raise FormalIrError("formal vector-bv32xN requires vector_width")

    def assumption_to_smt(assumption: Any, context: FormalContext, vector_width: int | None = None) -> str:
        if not isinstance(assumption, dict):
            raise FormalIrError("formal assumption must be an object")
        op = assumption.get("op")
        if op not in {"not-poison", "not-eq", "cmp", "known-bits", "power-of-two", "addr-diseq", "rel"}:
            raise FormalIrError("unsupported formal assumption")
        if op == "addr-diseq":
            left = assumption.get("left")
            right = assumption.get("right")
            if not isinstance(left, str) or not isinstance(right, str):
                raise FormalIrError("formal addr-diseq assumption requires left and right")
            if left not in variable_set or right not in variable_set:
                raise FormalIrError("formal addr-diseq assumption variable is undeclared")
            return f"(not (= {left} {right}))"
        if op == "rel":
            # Relational guard between two SSA values, e.g. isKnownNonEqual(A, B)
            # or a dominating icmp. Generalizes addr-diseq to any predicate.
            left = assumption.get("left")
            right = assumption.get("right")
            predicate = assumption.get("predicate")
            if not isinstance(left, str) or not isinstance(right, str):
                raise FormalIrError("formal rel assumption requires left and right")
            if left not in variable_set or right not in variable_set:
                raise FormalIrError("formal rel assumption variable is undeclared")
            rel_forms = {
                "eq": f"(= {left} {right})",
                "ne": f"(not (= {left} {right}))",
                "slt": f"(bvslt {left} {right})",
                "sle": f"(bvsle {left} {right})",
                "sgt": f"(bvsgt {left} {right})",
                "sge": f"(bvsge {left} {right})",
                "ult": f"(bvult {left} {right})",
                "ule": f"(bvule {left} {right})",
                "ugt": f"(bvugt {left} {right})",
                "uge": f"(bvuge {left} {right})",
            }
            if predicate not in rel_forms:
                raise FormalIrError("formal rel assumption predicate is unsupported")
            return rel_forms[predicate]
        name = assumption.get("name")
        if not isinstance(name, str) or name not in variable_set:
            if op == "not-poison":
                raise FormalIrError("formal not-poison assumption variable is undeclared")
            if op == "not-eq":
                raise FormalIrError("formal not-eq assumption variable is undeclared")
            if op == "known-bits":
                raise FormalIrError("formal known-bits assumption variable is undeclared")
            if op == "power-of-two":
                raise FormalIrError("formal power-of-two assumption variable is undeclared")
            raise FormalIrError("formal cmp assumption variable is undeclared")
        if op == "not-poison":
            if vector_width is not None and name in poison_variables:
                return smt_and([f"(not {context.poison_for_scalable_var(name, lane)})" for lane in range(vector_width)])
            return f"(not {context.poison_for_var(name)})"
        if op == "power-of-two":
            if assumption.get("nonzero") is not True:
                raise FormalIrError("formal power-of-two assumption requires nonzero true")

            # Encoding owned by o2t.facts.value_tracking (shared with the
            # symexec cascade discharge), so the two provers cannot drift.
            def power_of_two_to_smt(variable: str) -> str:
                return scalar_assumption_smt(assumption, variable)

            if vector_width is not None:
                return smt_and([power_of_two_to_smt(f"{name}{lane}") for lane in range(vector_width)])
            return power_of_two_to_smt(name)
        if op == "known-bits":
            zero_mask = assumption.get("zero_mask", 0)
            one_mask = assumption.get("one_mask", 0)
            if not isinstance(zero_mask, int) or not isinstance(one_mask, int):
                raise FormalIrError("formal known-bits masks must be integers")
            if zero_mask < 0 or zero_mask >= (1 << 32) or one_mask < 0 or one_mask >= (1 << 32):
                raise FormalIrError("formal known-bits mask is out of range")
            if zero_mask & one_mask:
                raise FormalIrError("formal known-bits masks overlap")
            # Encoding owned by o2t.facts.value_tracking (shared with the
            # symexec cascade discharge), so the two provers cannot drift.
            def known_bits_to_smt(variable: str) -> str:
                return scalar_assumption_smt(assumption, variable)

            if vector_width is not None:
                return smt_and([known_bits_to_smt(f"{name}{lane}") for lane in range(vector_width)])
            return known_bits_to_smt(name)
        value = assumption.get("value")
        if not isinstance(value, int):
            if op == "not-eq":
                raise FormalIrError("formal not-eq assumption only supports value 0")
            raise FormalIrError("formal cmp assumption value must be an integer")
        if value < -(1 << 31) or value >= (1 << 32):
            raise FormalIrError("formal cmp assumption value is out of range")
        constant = f"#x{value & 0xffffffff:08x}"
        if op == "not-eq":
            if value != 0:
                raise FormalIrError("formal not-eq assumption only supports value 0")
            predicate = "ne"
        else:
            predicate = assumption.get("predicate")
            if predicate not in CMP_PREDICATES:
                raise FormalIrError("unsupported formal cmp assumption predicate")

        # Encoding owned by o2t.facts.value_tracking (shared with the
        # symexec cascade discharge), so the two provers cannot drift. The
        # validation above (range, predicate, not-eq value) is retained here.
        def cmp_to_smt(variable: str) -> str:
            return scalar_assumption_smt(assumption, variable)

        if vector_width is not None:
            return smt_and([cmp_to_smt(f"{name}{lane}") for lane in range(vector_width)])
        return cmp_to_smt(name)

    if domain in {"scalable-vector-bv32", "scalable-scalar-bv32", "scalable-scalar-fp32"}:
        base_lanes = formal.get("base_lanes")
        vscale_values = formal.get("vscale_values")
        if not isinstance(base_lanes, int) or base_lanes <= 0:
            raise FormalIrError("formal scalable base_lanes must be a positive integer")
        if (
            not isinstance(vscale_values, list)
            or not vscale_values
            or not all(isinstance(value, int) and value > 0 for value in vscale_values)
        ):
            raise FormalIrError("formal scalable vscale_values must be positive integers")
        instances: list[tuple[int | None, FormalPair]] = []
        for vscale in vscale_values:
            width = base_lanes * vscale
            expanded_variables = tuple(f"{name}{lane}" for name in variables for lane in range(width))
            expanded_poison = {f"{name}{lane}" for name in poison_variables for lane in range(width)}
            context = FormalContext(expanded_poison, vector_width=width, scalable_poison_variables=set(poison_variables), variable_bits=variable_bits)
            before = typed_expr_to_smt(formal.get("before"), variable_set, context)
            after = typed_expr_to_smt(formal.get("after"), variable_set, context)
            if domain == "scalable-vector-bv32" and (before.sort != "vec" or after.sort != "vec"):
                raise FormalIrError("formal scalable before and after must be vector expressions")
            if domain == "scalable-scalar-bv32" and (before.sort != "bv" or after.sort != "bv"):
                raise FormalIrError("formal scalable scalar before and after must be bit-vector expressions")
            if domain == "scalable-scalar-fp32" and (before.sort != "fp" or after.sort != "fp"):
                raise FormalIrError("formal scalable floating-point scalar before and after must be fp expressions")
            assumptions = tuple(assumption_to_smt(assumption, context, width) for assumption in raw_assumptions)
            result_bits = before.bits * width if domain == "scalable-vector-bv32" else before.bits
            result_sort = "Float32" if domain == "scalable-scalar-fp32" else f"(_ BitVec {result_bits})"
            poison_equal = (
                poison_equals(before.lane_poisons, after.lane_poisons)
                if domain == "scalable-vector-bv32"
                else f"(= {before.poison} {after.poison})"
            )
            instances.append(
                (
                    vscale,
            FormalPair(
                before=before.smt,
                after=after.smt,
                variables=expanded_variables,
                bits=result_bits,
                result_sort=result_sort,
                before_poison=before.poison,
                after_poison=after.poison,
                poison_equal=poison_equal,
                refinement=refinement,
                declarations=tuple(context.declarations),
                assumptions=assumptions,
                variable_bits=tuple(sorted(variable_bits.items())),
                variable_sorts=tuple(
                    (
                        name,
                        "Float32"
                        if domain == "scalable-scalar-fp32"
                        else f"(_ BitVec {variable_bits.get(name, variable_bits.get(name.rstrip('0123456789'), 32))})",
                    )
                    for name in expanded_variables
                ),
            ),
                )
            )
        return instances

    context = FormalContext(set(poison_variables), vector_width=fixed_vector_width, variable_bits=variable_bits)
    before = typed_expr_to_smt(formal.get("before"), variable_set, context)
    after = typed_expr_to_smt(formal.get("after"), variable_set, context)
    assumptions = tuple(assumption_to_smt(assumption, context) for assumption in raw_assumptions)
    if domain in {"vector-bv32x4", "vector-bv32xN"}:
        if before.sort != "vec" or after.sort != "vec":
            raise FormalIrError("formal vector before and after must be vector expressions")
        variable_sort_items = tuple(
            (
                name,
                variable_sort_overrides.get(name, f"(_ BitVec {variable_bits.get(name, 32)})"),
            )
            for name in variables
        )
        return [(
            None,
            FormalPair(
                before=before.smt,
                after=after.smt,
                variables=tuple(variables),
                bits=before.bits * fixed_vector_width,
                result_sort=f"(_ BitVec {before.bits * fixed_vector_width})",
                before_poison=before.poison,
                after_poison=after.poison,
                poison_equal=poison_equals(before.lane_poisons, after.lane_poisons),
                refinement=refinement,
                declarations=tuple(context.declarations),
                assumptions=assumptions,
                variable_bits=tuple(sorted(variable_bits.items())),
                variable_sorts=variable_sort_items,
            ),
        )]
    if domain == "scalar-fp32":
        if before.sort != "fp" or after.sort != "fp":
            raise FormalIrError("formal floating-point before and after must be fp expressions")
        return [(
            None,
            FormalPair(
                before=before.smt,
                after=after.smt,
                variables=tuple(variables),
                bits=32,
                result_sort="Float32",
                before_poison=before.poison,
                after_poison=after.poison,
                poison_equal=f"(= {before.poison} {after.poison})",
                refinement=refinement,
                declarations=tuple(context.declarations),
                assumptions=assumptions,
                variable_bits=tuple(sorted(variable_bits.items())),
                variable_sorts=tuple((name, "Float32") for name in variables),
            ),
        )]
    if before.sort != "bv" or after.sort != "bv":
        raise FormalIrError("formal before and after must be bit-vector expressions")
    variable_sort_items = tuple(
        (
            name,
            variable_sort_overrides.get(name, f"(_ BitVec {variable_bits.get(name, 32)})"),
        )
        for name in variables
    )
    return [(
        None,
        FormalPair(
            before=before.smt,
            after=after.smt,
            variables=tuple(variables),
            bits=before.bits,
            result_sort=f"(_ BitVec {before.bits})",
            before_poison=before.poison,
            after_poison=after.poison,
            poison_equal=f"(= {before.poison} {after.poison})",
            refinement=refinement,
            declarations=tuple(context.declarations),
            assumptions=assumptions,
            variable_bits=tuple(sorted(variable_bits.items())),
            variable_sorts=variable_sort_items,
        ),
    )]


def pair_for_record_intent(record: dict[str, Any]) -> FormalPair | None:
    instances = pair_instances_for_record_intent(record)
    if instances is None:
        return None
    if len(instances) != 1:
        raise FormalIrError("formal has multiple proof instances")
    return instances[0][1]


def pair_instances_for_record_intent(record: dict[str, Any]) -> list[tuple[int | None, FormalPair]] | None:
    intent = record.get("intent_candidate", {})
    if not isinstance(intent, dict):
        return None
    formal = intent.get("formal")
    if formal is None:
        return None
    return pair_instances_for_formal(formal)


def _obligation_prelude(marker: str, source: str, pair: FormalPair, smt_source: str) -> list[str]:
    """The shared SMT prelude (comments, logic, declarations, before/after define-funs) used by both
    the equivalence obligation and the premise-satisfiability check, so the two cannot drift."""
    variable_bits = dict(pair.variable_bits)
    variable_sorts = dict(pair.variable_sorts)
    declarations = [
        f"(declare-const {smt_sym(name)} {variable_sorts.get(name, f'(_ BitVec {variable_bits.get(name, 32)})')})"
        for name in pair.variables
    ]
    result_sort = pair.result_sort or f"(_ BitVec {pair.bits})"
    uses_arrays = any(sort.startswith("(Array ") for sort in variable_sorts.values()) or any(
        declaration.startswith("(declare-const") and "(Array " in declaration
        for declaration in pair.declarations
    )
    logic = "QF_FP" if result_sort == "Float32" else ("QF_AUFBV" if uses_arrays else "QF_BV")
    return [
        f"; marker: {marker}",
        f"; source: {source}",
        f"; smt_source: {smt_source}",
        f"(set-logic {logic})",
        *declarations,
        *pair.declarations,
        f"(define-fun before () {result_sort} {pair.before})",
        f"(define-fun after () {result_sort} {pair.after})",
        f"(define-fun before_poison () Bool {pair.before_poison})",
        f"(define-fun after_poison () Bool {pair.after_poison})",
    ]


def equivalence_smt(marker: str, source: str, pair: FormalPair, smt_source: str = "formal") -> str:
    assumptions = smt_and(list(pair.assumptions))
    if pair.refinement == "refinement":
        assertion = f"(assert {smt_and([assumptions, f'(not {pair.before_poison})', f'(or {pair.after_poison} (not (= before after)))'])})"
    else:
        assertion = f"(assert {smt_and([assumptions, f'(not (and (= before after) {pair.poison_equal}))'])})"
    return "\n".join([*_obligation_prelude(marker, source, pair, smt_source), assertion, "(check-sat)", ""])


def premise_smt(marker: str, source: str, pair: FormalPair, smt_source: str = "formal") -> str | None:
    """Companion to `equivalence_smt`: assert ONLY the assumptions and `(check-sat)`. An `unsat`
    equivalence result proves the rewrite only if the premises are jointly SATISFIABLE -- otherwise
    `(and assumptions (not goal))` is trivially unsat and the "proof" is VACUOUS. The syntactic
    `normalize_assumptions` algebra catches only a few contradiction shapes; this is the general
    semantic guard. Returns None when there are no assumptions (nothing can be vacuous)."""
    if not pair.assumptions:
        return None
    assumptions = smt_and(list(pair.assumptions))
    return "\n".join([*_obligation_prelude(marker, source, pair, smt_source),
                      f"(assert {assumptions})", "(check-sat)", ""])
