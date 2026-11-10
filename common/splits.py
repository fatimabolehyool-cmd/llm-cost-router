"""Data split contract (architecture section E, proposal feedback 9).

Every question is assigned to exactly one split by hashing its group key (normalized question
text). The assignment depends only on the key, so it is stable across runs and machines, and
near-duplicate questions always share a split. The test split is never used for training,
threshold choice or the release gate.
"""

from __future__ import annotations

import hashlib

SPLITS = (("train", 0.60), ("validation", 0.15), ("release_eval", 0.15), ("test", 0.10))


def split_of(group: str) -> str:
    """Deterministic split by hash of the group key, so near-duplicates never straddle splits."""
    u = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for name, share in SPLITS:
        acc += share
        if u <= acc:
            return name
    return SPLITS[-1][0]
