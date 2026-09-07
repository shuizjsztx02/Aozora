#!/usr/bin/env python3
"""Generate the shared fuzz corpus at contracts/fixtures/reconciliation-fuzz/.

The corpus is a deterministic JSON file that all three platforms (TS, Python, Kotlin)
load instead of generating their own PRNG sequences. This ensures cross-platform
consistency: same seed → same transactions → same invariants hold on all three.

PRNG: 32-bit LCG (glibc constants) — matches TS and Kotlin implementations exactly.
  state = (state * 1664525 + 1013904223) mod 2^32

Type selection uses high-bit comparison (< 0.75) to avoid low-bit modulo bias (P1#7).
Amount uses rejection sampling to avoid modulo bias.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SEED = 42
ITERATIONS = 100

CATEGORIES = ["cat-food", "cat-transport", "cat-shopping", "cat-salary", "cat-bonus"]
TYPES_BIASED = ["expense", "expense", "expense", "income"]  # 3:1 expense bias (match TS)
SOURCES = ["manual", "nlu_local", "nlu_ai"]
MONTHS = ["2026-08", "2026-09", "2026-10"]
DAYS = ["01", "05", "10", "15", "20", "20", "25", "28"]  # 8 entries for cleaner pick
EDGE_AMOUNTS = [0, 1, 99999, 2**53 - 1]  # valid edges: 0, min positive, large, MAX_SAFE_INTEGER
EDGE_AMOUNTS_INVALID = [-1, -(2**53 - 1)]  # invalid edges: negative values (must be rejected)

MASK32 = 0xFFFFFFFF


class LCG:
    """32-bit LCG — identical to TS/Kotlin implementations."""
    def __init__(self, seed: int):
        self.state = seed & MASK32

    def next_u32(self) -> int:
        self.state = (self.state * 1664525 + 1013904223) & MASK32
        return self.state

    def next_double(self) -> float:
        """[0.0, 1.0) via high 24 bits — avoids low-bit bias (P1#7)."""
        return (self.next_u32() >> 8) / (1 << 24)

    def next_int(self, bound: int) -> int:
        """[0, bound) via rejection sampling — avoids modulo bias (P1#7)."""
        if bound <= 0:
            raise ValueError(f"bound must be positive, got {bound}")
        # Rejection sampling: reject values that would produce biased modulo
        threshold = (1 << 32) - ((1 << 32) % bound)
        while True:
            v = self.next_u32()
            if v < threshold:
                return v % bound

    def pick(self, arr: list) -> object:
        return arr[self.next_int(len(arr))]

    def next_bool(self) -> bool:
        return self.next_int(2) == 0


def generate_tx(rng: LCG, index: int) -> dict:
    month = rng.pick(MONTHS)
    day = rng.pick(DAYS)
    tx_type = rng.pick(TYPES_BIASED)
    amount = rng.next_int(99999) + 1  # [1, 99999]
    category = rng.pick(CATEGORIES)
    source = rng.pick(SOURCES)
    return {
        "id": f"fuzz-tx-{index}",
        "bookId": "fuzz-book",
        "type": tx_type,
        "amountCents": amount,
        "categoryId": category,
        "bizDate": f"{month}-{day}",
        "source": source,
        "deletedAt": None,
    }


def generate_iterations(rng: LCG) -> list[dict]:
    """Generate ITERATIONS transactions using the reference LCG."""
    return [generate_tx(rng, i) for i in range(ITERATIONS)]


def generate_mixed_operations_trace(rng: LCG) -> list[dict]:
    """Generate a trace of insert/delete/undo/update operations for mixed-ops test."""
    trace = []
    txs: list[dict] = []
    tx_counter = 0

    for i in range(ITERATIONS):
        op = rng.next_int(10)
        if op < 5 or len(txs) == 0:
            tx = generate_tx(rng, tx_counter)
            tx_counter += 1
            txs.append(tx)
            trace.append({"op": "insert", "tx": tx})
        elif op < 7:
            # Soft-delete a random active tx
            active = [t for t in txs if t["deletedAt"] is None]
            if active:
                target = rng.pick(active)
                target["deletedAt"] = 1788192000000 + i * 86400000  # 2026-09-01 + i days
                trace.append({"op": "softDelete", "id": target["id"]})
        elif op < 9:
            # Undo a random deleted tx
            deleted = [t for t in txs if t["deletedAt"] is not None]
            if deleted:
                target = rng.pick(deleted)
                target["deletedAt"] = None
                trace.append({"op": "undo", "id": target["id"]})
        else:
            # Update amount
            active = [t for t in txs if t["deletedAt"] is None]
            if active:
                target = rng.pick(active)
                target["amountCents"] = rng.next_int(99999) + 1
                trace.append({"op": "update", "id": target["id"], "newAmount": target["amountCents"]})

    return trace


def generate_edge_cases() -> list[dict]:
    """Valid edge cases that the fuzzer should hit but doesn't with bounded PRNG (P1#10)."""
    cases = []
    for i, amt in enumerate(EDGE_AMOUNTS):
        cases.append({
            "id": f"edge-{i}",
            "bookId": "fuzz-book",
            "type": "expense",
            "amountCents": amt,
            "categoryId": "cat-food",
            "bizDate": "2026-09-01",
            "source": "manual",
            "deletedAt": None,
            "note": f"edge case: amount={amt}",
        })
    return cases


def generate_invalid_edges() -> list[dict]:
    """Invalid edge cases — negative amounts that every platform must reject."""
    cases = []
    for i, amt in enumerate(EDGE_AMOUNTS_INVALID):
        cases.append({
            "id": f"edge-invalid-{i + 1}",
            "bookId": "fuzz-book",
            "type": "expense",
            "amountCents": amt,
            "categoryId": "cat-food",
            "bizDate": "2026-09-01",
            "source": "manual",
            "deletedAt": None,
            "note": f"edge case: amount={amt} (must be rejected)",
        })
    return cases


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    out_dir = root / "contracts" / "fixtures" / "reconciliation-fuzz"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. iterations.json — 100 transactions from reference LCG(seed=42)
    rng1 = LCG(SEED)
    iterations = generate_iterations(rng1)
    (out_dir / "iterations.json").write_text(json.dumps({
        "seed": SEED,
        "iterations": ITERATIONS,
        "prng": "LCG(1664525, 1013904223, 2^32)",
        "transactions": iterations,
    }, indent=2))
    print(f"  wrote {out_dir / 'iterations.json'} ({len(iterations)} txs)")

    # 2. mixed-operations-trace.json — trace of insert/delete/undo/update
    rng2 = LCG(SEED)
    trace = generate_mixed_operations_trace(rng2)
    (out_dir / "mixed-operations-trace.json").write_text(json.dumps({
        "seed": SEED,
        "iterations": ITERATIONS,
        "prng": "LCG(1664525, 1013904223, 2^32)",
        "trace": trace,
    }, indent=2))
    print(f"  wrote {out_dir / 'mixed-operations-trace.json'} ({len(trace)} ops)")

    # 3. edge-cases.json — valid boundary values
    edge = generate_edge_cases()
    (out_dir / "edge-cases.json").write_text(json.dumps({
        "description": "Valid boundary values that bounded PRNG fuzz cannot reach (P1#10)",
        "cases": edge,
    }, indent=2))
    print(f"  wrote {out_dir / 'edge-cases.json'} ({len(edge)} cases)")

    # 3b. invalid-edges.json — boundary values that must be REJECTED
    invalid = generate_invalid_edges()
    (out_dir / "invalid-edges.json").write_text(json.dumps({
        "description": "Invalid boundary values — negative amounts that every platform must reject",
        "cases": invalid,
    }, indent=2))
    print(f"  wrote {out_dir / 'invalid-edges.json'} ({len(invalid)} cases)")

    # 4. determinism-check.json — two runs from same seed, should match
    rng_a = LCG(SEED)
    rng_b = LCG(SEED)
    a_txs = [generate_tx(rng_a, i) for i in range(ITERATIONS)]
    b_txs = [generate_tx(rng_b, i) for i in range(ITERATIONS)]
    assert a_txs == b_txs, "Determinism violated — same seed produced different sequences!"
    (out_dir / "determinism-check.json").write_text(json.dumps({
        "seed": SEED,
        "iterations": ITERATIONS,
        "match": True,
        "sample": a_txs[:5],
    }, indent=2))
    print(f"  wrote {out_dir / 'determinism-check.json'} (determinism verified)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
