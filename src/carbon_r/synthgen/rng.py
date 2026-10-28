"""Seeded randomness for synthetic documents, and the injectable draw helpers.

One rule governs this module (docs/synthgen/SYNTHGEN_SPEC.md 1.4): a document's random
stream is keyed by **which document it is**, never by its position in a loop. Adding a profile or a
document kind must leave every existing document bit-identical, because an accuracy figure
recorded against a set ("16 of 18") is worthless if the set silently changes underneath it.

Seeds are formatted strings. CPython hashes a string seed with SHA-512, which is stable
across processes. Never seed from `hash(...)`: PYTHONHASHSEED randomises `str.__hash__` per
process, so the same master seed would give different documents on different runs - and the
"same seed, same output" test would fail intermittently, which reads as flakiness rather
than as a bug.
"""

import random


def doc_rng(seed: int, *parts: object) -> random.Random:
    """A stream private to one document, labelled by the parts that identify it.

    doc_rng(seed, account, f"{year}-{month:02d}", "electricity_bill")
    """
    return random.Random(":".join([str(seed), *(str(p) for p in parts)]))


def bill_rng(seed: int, account_number: str, year: int, month: int, doc_kind: str) -> random.Random:
    """The per-bill stream for a monthly document. Label format is fixed by the spec."""
    return doc_rng(seed, account_number, f"{year}-{month:02d}", doc_kind)


def account_rng(seed: int, account_number: str, doc_kind: str) -> random.Random:
    """For per-account facts that are not per-document (opening meter read, identity)."""
    return doc_rng(seed, account_number, doc_kind, "account")


def draw(value: float | None, rng: random.Random, low: float, high: float) -> float:
    """Return `value` if injected, else a uniform draw.

    Every random quantity goes through this, so a test can fix any single factor and assert
    a exact answer instead of asserting on whatever the RNG produced (spec 1.5).
    """
    if value is not None:
        return value
    return rng.uniform(low, high)
