#!/usr/bin/env python3
"""Normalize and summarize source-mined analysis dependency facts."""

from __future__ import annotations

import collections
from typing import Any


FACT_ROLES = {
    "alias.noalias": "memory-safety",
    "alias.unknown": "memory-safety",
    "memoryssa.dead-store": "deadness",
    "memoryssa.clobber": "overwrite-safety",
    "memory.no-intervening-store": "overwrite-safety",
    "memory.no-intervening-read": "overwrite-safety",
    "memory.no-intervening-memory-effect": "overwrite-safety",
    "memory.unknown-intervening-effect": "safety-blocker",
    "memory.overwrite.full": "overwrite-range",
    "memory.overwrite.partial": "overwrite-range",
    "memory.overwrite.partial.fixed-byte-mask": "overwrite-range",
    "memory.overwrite.size.known": "overwrite-range",
    "memory.overwrite.size.symbolic-equal": "overwrite-range",
    "memory.overwrite.size.symbolic-upper-bound": "overwrite-range",
    "memory.overwrite.size.symbolic-bounded-four-lane": "overwrite-range",
    "memory.overwrite.size.symbolic-bounded-eight-lane": "overwrite-range",
    "memory.overwrite.size.bounded-four-lane": "overwrite-range",
    "memory.overwrite.size.bounded-eight-lane": "overwrite-range",
    "memory.overwrite.nonoverlap": "overwrite-range",
    "memory.overwrite.unknown-size": "overwrite-range",
    "memory.volatile-atomic-blocker": "safety-blocker",
    "memory.volatile-blocker": "safety-blocker",
    "memory.atomic-unordered-blocker": "safety-blocker",
    "memory.atomic-ordered-blocker": "safety-blocker",
    "memory.atomic-ordering-unknown-blocker": "safety-blocker",
}


DSE_MARKERS = {"probe.dse.dead-store", "probe.dse.overwritten-store"}

def dse_lane_mask_width(mask: str) -> int | None:
    prefix = "lanes-"
    marker = "-of-"
    if not mask.startswith(prefix) or marker not in mask:
        return None
    body, width_text = mask[len(prefix) :].rsplit(marker, 1)
    if not body or not width_text.isdigit():
        return None
    width = int(width_text)
    if width < 1 or width > 8:
        return None
    return width


def dse_lane_mask_bits(mask: str) -> int | None:
    width = dse_lane_mask_width(mask)
    if width is None:
        return None
    body = mask[len("lanes-") :].rsplit("-of-", 1)[0]
    bits = 0
    seen: set[int] = set()
    for item in body.split("-"):
        if not item.isdigit():
            return None
        lane = int(item)
        if lane < 0 or lane >= width:
            return None
        if lane in seen:
            return None
        seen.add(lane)
        bits |= 1 << lane
    full = (1 << width) - 1
    if bits == 0 or bits == full:
        return None
    return bits


def dse_lane_mask_name(bits: int, width: int = 4) -> str:
    if width < 1 or width > 8:
        return ""
    bits &= (1 << width) - 1
    if bits == 0 or bits == (1 << width) - 1:
        return ""
    lanes = [str(lane) for lane in range(width) if bits & (1 << lane)]
    return "lanes-" + "-".join(lanes) + f"-of-{width}"


def is_supported_dse_byte_mask(mask: str) -> bool:
    return dse_lane_mask_bits(mask) is not None


def normalize_analysis_fact(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip()
    if not kind:
        return None
    status = str(value.get("status") or "unknown").strip() or "unknown"
    role = str(value.get("role") or FACT_ROLES.get(kind, "analysis-dependency"))
    record: dict[str, Any] = {
        "kind": kind,
        "role": role,
        "status": status,
    }
    subjects = value.get("subjects")
    if isinstance(subjects, list):
        record["subjects"] = [str(item) for item in subjects if str(item)]
    source = str(value.get("source") or "").strip()
    if source:
        record["source"] = source
    try:
        line = int(value.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    if line:
        record["line"] = line
    provenance = str(value.get("provenance") or "").strip()
    if provenance:
        record["provenance"] = provenance
    byte_mask = str(value.get("byte_mask") or "").strip()
    if byte_mask:
        record["byte_mask"] = byte_mask
        width = dse_lane_mask_width(byte_mask)
        if width is not None:
            record["byte_width"] = width
    try:
        byte_bound = int(value.get("byte_bound") or 0)
    except (TypeError, ValueError):
        byte_bound = 0
    if byte_bound:
        record["byte_bound"] = byte_bound
    return record


def normalize_analysis_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    facts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, str]] = set()
    for item in value:
        fact = normalize_analysis_fact(item)
        if fact is None:
            continue
        key = (
            fact["kind"],
            fact["role"],
            fact["status"],
            int(fact.get("line") or 0),
            str(fact.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        facts.append(fact)
    return facts


def analysis_fact_summary(value: Any) -> dict[str, Any]:
    facts = normalize_analysis_facts(value)
    by_kind = collections.Counter(str(fact.get("kind") or "") for fact in facts)
    by_status = collections.Counter(str(fact.get("status") or "") for fact in facts)
    by_role = collections.Counter(str(fact.get("role") or "") for fact in facts)
    blockers = [
        fact
        for fact in facts
        if str(fact.get("status") or "") == "unsupported"
        or str(fact.get("kind") or "").endswith("-blocker")
    ]
    return {
        "analysis_fact_count": len(facts),
        "analysis_fact_kinds": dict(sorted(by_kind.items())),
        "analysis_fact_status": dict(sorted(by_status.items())),
        "analysis_fact_roles": dict(sorted(by_role.items())),
        "analysis_fact_blockers": [dict(fact) for fact in blockers],
    }


def dse_analysis_fact_contract(marker: str, value: Any) -> dict[str, Any]:
    facts = normalize_analysis_facts(value)
    kinds = {str(fact.get("kind") or "") for fact in facts}
    fixed_masks = {
        str(fact.get("byte_mask") or "")
        for fact in facts
        if str(fact.get("kind") or "") == "memory.overwrite.partial.fixed-byte-mask"
    }
    fixed_masks.discard("")
    unsupported = {
        str(fact.get("kind") or "")
        for fact in facts
        if str(fact.get("status") or "") == "unsupported"
    }
    required: list[str] = []
    missing: list[str] = []
    blockers: list[str] = []
    if marker not in DSE_MARKERS:
        return {
            "applicable": False,
            "complete": False,
            "required": required,
            "missing": missing,
            "blockers": blockers,
            "kinds": sorted(kinds),
        }
    if marker == "probe.dse.dead-store":
        required = ["memoryssa.dead-store"]
    elif marker == "probe.dse.overwritten-store":
        range_required = "memory.overwrite.full"
        if "memory.overwrite.partial.fixed-byte-mask" in kinds:
            range_required = "memory.overwrite.partial.fixed-byte-mask"
        required = [
            "memoryssa.clobber",
            "memory.no-intervening-store",
            "memory.no-intervening-read",
            "memory.no-intervening-memory-effect",
            "memory.overwrite.size.known",
            "memory.overwrite.size.bounded-four-lane",
            range_required,
        ]
    missing = [kind for kind in required if kind not in kinds]
    if marker == "probe.dse.overwritten-store" and "memory.overwrite.size.bounded-eight-lane" in kinds:
        missing = [kind for kind in missing if kind != "memory.overwrite.size.bounded-four-lane"]
    has_symbolic_bounded = bool(
        {
            "memory.overwrite.size.symbolic-bounded-four-lane",
            "memory.overwrite.size.symbolic-bounded-eight-lane",
        }
        & kinds
    )
    if marker == "probe.dse.overwritten-store" and has_symbolic_bounded:
        missing = [
            kind
            for kind in missing
            if kind not in {"memory.overwrite.size.known", "memory.overwrite.size.bounded-four-lane"}
        ]
    memory_order_blockers = [
        "memory.volatile-blocker",
        "memory.atomic-unordered-blocker",
        "memory.atomic-ordered-blocker",
        "memory.atomic-ordering-unknown-blocker",
    ]
    if "memory.volatile-atomic-blocker" in unsupported:
        blockers.append("memory.volatile-atomic-blocker")
    for blocker in memory_order_blockers:
        if blocker in unsupported:
            blockers.append(blocker)
    if "memory.unknown-intervening-effect" in unsupported:
        blockers.append("memory.unknown-intervening-effect")
    if marker == "probe.dse.overwritten-store" and "alias.unknown" in kinds and "alias.noalias" not in kinds:
        blockers.append("alias.unknown")
    if marker == "probe.dse.overwritten-store":
        if "memory.overwrite.partial" in kinds and "memory.overwrite.partial.fixed-byte-mask" not in kinds:
            blockers.append("memory.overwrite.partial")
        if "memory.overwrite.nonoverlap" in kinds:
            blockers.append("memory.overwrite.nonoverlap")
        if (
            "memory.overwrite.unknown-size" in kinds
            and not has_symbolic_bounded
        ):
            blockers.append("memory.overwrite.unknown-size")
        if "memory.overwrite.partial.fixed-byte-mask" in kinds and not fixed_masks:
            blockers.append("memory.overwrite.partial.fixed-byte-mask.missing-mask")
        if any(not is_supported_dse_byte_mask(mask) for mask in fixed_masks):
            blockers.append("memory.overwrite.partial.fixed-byte-mask.unsupported-mask")
    return {
        "applicable": True,
        "complete": bool(facts) and not missing and not blockers,
        "required": required,
        "missing": missing,
        "blockers": blockers,
        "kinds": sorted(kinds),
        "byte_masks": sorted(fixed_masks),
    }


def dse_analysis_fact_parameters(marker: str, value: Any) -> dict[str, Any]:
    contract = dse_analysis_fact_contract(marker, value)
    if not contract["applicable"]:
        return {}
    params = {
        "dse.analysis_facts.contract": "dse-analysis-facts-v1",
        "dse.analysis_facts.complete": bool(contract["complete"]),
        "dse.analysis_facts.required": list(contract["required"]),
        "dse.analysis_facts.missing": list(contract["missing"]),
        "dse.analysis_facts.blockers": list(contract["blockers"]),
    }
    kinds = set(contract["kinds"])
    if marker == "probe.dse.overwritten-store":
        if "memory.overwrite.full" in kinds and not any(
            kind.startswith("memory.overwrite.")
            and not kind.startswith("memory.overwrite.size.")
            and kind != "memory.overwrite.full"
            and not (
                kind == "memory.overwrite.unknown-size"
                and (
                    "memory.overwrite.size.symbolic-bounded-four-lane" in kinds
                    or "memory.overwrite.size.symbolic-bounded-eight-lane" in kinds
                )
            )
            for kind in kinds
        ):
            params["dse.overwrite_range"] = "full"
        elif "memory.overwrite.partial.fixed-byte-mask" in kinds:
            params["dse.overwrite_range"] = "partial"
            masks = [mask for mask in contract.get("byte_masks", []) if is_supported_dse_byte_mask(mask)]
            if masks:
                params["dse.overwrite_byte_mask"] = masks[0]
        elif "memory.overwrite.partial" in kinds:
            params["dse.overwrite_range"] = "partial"
        elif "memory.overwrite.nonoverlap" in kinds:
            params["dse.overwrite_range"] = "nonoverlap"
        elif "memory.overwrite.unknown-size" in kinds:
            params["dse.overwrite_range"] = "unknown-size"
        if (
            "memory.overwrite.size.symbolic-bounded-four-lane" in kinds
            or "memory.overwrite.size.symbolic-bounded-eight-lane" in kinds
        ):
            params["dse.overwrite_size"] = "symbolic"
        elif "memory.overwrite.size.known" in kinds:
            params["dse.overwrite_size"] = "known"
        widths = [
            dse_lane_mask_width(mask)
            for mask in contract.get("byte_masks", [])
            if dse_lane_mask_width(mask) is not None
        ]
        if "memory.overwrite.size.symbolic-bounded-four-lane" in kinds:
            params["dse.overwrite_width_bytes"] = 4
        elif "memory.overwrite.size.symbolic-bounded-eight-lane" in kinds:
            params["dse.overwrite_width_bytes"] = 8
        elif widths:
            params["dse.overwrite_width_bytes"] = max(widths)
        elif "memory.overwrite.full" in kinds:
            params["dse.overwrite_width_bytes"] = 4
        if "memory.overwrite.size.bounded-four-lane" in kinds:
            params["dse.overwrite_size_bound"] = "four-lane"
        if "memory.overwrite.size.bounded-eight-lane" in kinds:
            params["dse.overwrite_size_bound"] = "eight-lane"
        if "memory.overwrite.size.symbolic-bounded-four-lane" in kinds:
            params["dse.overwrite_size_bound"] = "four-lane"
        if "memory.overwrite.size.symbolic-bounded-eight-lane" in kinds:
            params["dse.overwrite_size_bound"] = "eight-lane"
    return params


def missing_dse_analysis_fact_recommendation(marker: str, value: Any) -> str:
    if not marker.startswith("probe.dse."):
        return ""
    contract = dse_analysis_fact_contract(marker, value)
    kinds = set(contract["kinds"])
    if "memory.volatile-blocker" in contract["blockers"]:
        return "keep volatile memory blocked"
    if "memory.atomic-unordered-blocker" in contract["blockers"]:
        return "keep unordered atomic memory blocked"
    if "memory.atomic-ordered-blocker" in contract["blockers"]:
        return "keep ordered atomic memory blocked"
    if "memory.atomic-ordering-unknown-blocker" in contract["blockers"]:
        return "keep unknown-ordering atomic memory blocked"
    if "memory.volatile-atomic-blocker" in contract["blockers"]:
        return "keep volatile/atomic memory blocked"
    if "memory.unknown-intervening-effect" in contract["blockers"]:
        return "model intervening memory effects"
    if "alias.unknown" in contract["blockers"] or ("alias.unknown" in kinds and "alias.noalias" not in kinds):
        return "model alias/noalias evidence"
    if (
        "memory.overwrite.partial" in contract["blockers"]
        or "memory.overwrite.partial.fixed-byte-mask.unsupported-mask" in contract["blockers"]
        or "memory.overwrite.partial.fixed-byte-mask.missing-mask" in contract["blockers"]
    ):
        return "model partial-overwrite byte ranges"
    if "memory.overwrite.nonoverlap" in contract["blockers"]:
        return "keep non-overlapping overwrite blocked"
    if "memory.overwrite.unknown-size" in contract["blockers"]:
        return "model unknown-size overwrite evidence"
    if "memoryssa.dead-store" in contract["missing"] and marker == "probe.dse.dead-store":
        return "model MemorySSA dead-store evidence"
    if "memoryssa.clobber" in contract["missing"] and marker == "probe.dse.overwritten-store":
        return "model MemorySSA clobber evidence"
    if marker == "probe.dse.overwritten-store" and "memory.no-intervening-store" in contract["missing"]:
        return "model no-intervening-store evidence"
    if marker == "probe.dse.overwritten-store" and "memory.no-intervening-read" in contract["missing"]:
        return "model no-intervening-read evidence"
    if marker == "probe.dse.overwritten-store" and "memory.no-intervening-memory-effect" in contract["missing"]:
        return "model no-intervening-memory-effect evidence"
    if marker == "probe.dse.overwritten-store" and "memory.overwrite.size.known" in contract["missing"]:
        return "model known overwrite size evidence"
    if marker == "probe.dse.overwritten-store" and "memory.overwrite.size.bounded-four-lane" in contract["missing"]:
        return "model bounded overwrite size evidence"
    if marker == "probe.dse.overwritten-store" and "memory.overwrite.full" in contract["missing"]:
        return "model full-overwrite byte range evidence"
    if not normalize_analysis_facts(value):
        return "model DSE analysis dependency evidence"
    return ""
