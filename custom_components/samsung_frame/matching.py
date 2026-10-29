"""Pick the SmartThings media_player that belongs to a discovered TV.

Pure Python so it is unit-testable. Signals, strongest first: MAC address on
the device registry entry, model number prefix, then device name. The best
candidate must be unique, otherwise nothing is picked (we never guess).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional

MODEL_PREFIX = 10  # e.g. QE43LS03DA: same for QE43LS03DAU / QE43LS03DAUXXU


@dataclass(frozen=True)
class StCandidate:
    entity_id: str
    macs: frozenset[str] = frozenset()
    model: Optional[str] = None
    name: Optional[str] = None


def norm_mac(mac: Optional[str]) -> str:
    return re.sub(r"[^0-9a-f]", "", (mac or "").lower())


def norm_model(model: Optional[str]) -> str:
    return re.sub(r"[^0-9a-z]", "", (model or "").lower())[:MODEL_PREFIX]


def pick_smartthings_entity(
    candidates: Iterable[StCandidate],
    mac: Optional[str],
    model: Optional[str],
    name: Optional[str],
) -> Optional[str]:
    want_mac, want_model = norm_mac(mac), norm_model(model)
    want_name = (name or "").strip().casefold()
    scored: list[tuple[int, str]] = []
    for c in candidates:
        score = 0
        if want_mac and want_mac in {norm_mac(m) for m in c.macs}:
            score += 100
        if len(want_model) == MODEL_PREFIX and norm_model(c.model) == want_model:
            score += 10
        if want_name and (c.name or "").strip().casefold() == want_name:
            score += 5
        if score:
            scored.append((score, c.entity_id))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous, e.g. two identical TVs and no MAC
    return scored[0][1]
