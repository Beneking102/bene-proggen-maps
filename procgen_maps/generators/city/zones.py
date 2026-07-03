"""Rule-based zone classification for city blocks.

Pure Python - assigns each `Block` a zone label ('commercial', 'park',
'industrial', 'residential') using distance-from-center weighting plus
deterministic noise, while keeping the overall mix close to
`preset.zone_ratios`. Commercial is biased toward the center, industrial
toward the outskirts, parks scattered via noise, and residential fills
whatever remains.
"""
from typing import Dict, List

from .layout import Block
from ...utils import noise as _noise


def classify_zones(blocks: List[Block], preset, seed=None) -> Dict[int, str]:
    """Return {block.id: zone_name} for every block in `blocks`."""
    rng_seed = preset.seed if seed is None else seed
    if not blocks:
        return {}

    max_dist = max((_dist(b.center) for b in blocks), default=1.0) or 1.0

    scored = []
    for block in blocks:
        dist_ratio = _dist(block.center) / max_dist
        noise_val = _noise.value_noise_2d(block.center[0] * 0.01, block.center[1] * 0.01, rng_seed)
        commercial_score = (1.0 - dist_ratio) + noise_val * 0.15
        industrial_score = dist_ratio + noise_val * 0.15
        park_score = noise_val
        scored.append((block, commercial_score, industrial_score, park_score))

    zone_targets = {
        zone: max(0, round(preset.zone_ratios.get(zone, 0.0) * len(blocks)))
        for zone in ("commercial", "park", "industrial")
    }

    assignment: Dict[int, str] = {}
    remaining = list(scored)

    for zone, score_index in (("commercial", 1), ("park", 3), ("industrial", 2)):
        target = zone_targets.get(zone, 0)
        remaining.sort(key=lambda item: item[score_index], reverse=True)
        chosen, remaining = remaining[:target], remaining[target:]
        for block, *_ in chosen:
            assignment[block.id] = zone

    for block, *_ in remaining:
        assignment[block.id] = "residential"

    return assignment


def _dist(point) -> float:
    return (point[0] ** 2 + point[1] ** 2) ** 0.5
