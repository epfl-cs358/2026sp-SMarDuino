"""
footprint_envelopes.py
======================
Lookup table mapping KiCad footprint strings to expected geometric envelopes.
Used by PlacementSequencer.is_consistent_with() for WRONG_PART detection.

Each envelope defines what a correctly-picked component should look like from below:
    pad_count_range  : (min, max) inclusive
    aspect_ratio_min : body_width / body_height — 0.0 means unconstrained (round parts)
    body_area_range  : (min_px², max_px²) for the bounding-box area

These are intentionally wide: a tight tolerance would reject good picks due to
lighting variation; we only want to catch gross errors (empty nozzle, wrong reel).

To add a new footprint, add an entry to FOOTPRINT_ENVELOPES.
The key is matched by substring (case-insensitive) against the PCB footprint field,
so "R_0805" matches "Resistor_SMD:R_0805_2012Metric" as well as plain "R_0805".
First match wins, so put more-specific keys before broader ones.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class FootprintEnvelope:
    pad_count_range:  Tuple[int, int]   # (min_pads, max_pads)
    aspect_ratio_min: float             # body long/short ratio minimum; 0.0 = unconstrained
    body_area_range:  Tuple[int, int]   # (min_px², max_px²) for bounding-box area


# ── Envelope table ─────────────────────────────────────────────────────────────
#
# Keys are matched by substring so partial KiCad library prefixes work.
# Order matters: first match wins. Put narrow keys before broad ones.

FOOTPRINT_ENVELOPES: dict = {
    # ── 0805 passives ──────────────────────────────────────────────────────────
    # body_area_range upper bound is intentionally wide: morphological dilation
    # slightly inflates synthetic test frames, and real-hardware camera distance
    # varies.  The pad-count check (when pads are resolved) is the primary signal.
    "R_0805":       FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=1.5, body_area_range=(80,  80_000)),
    "C_0805":       FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=1.5, body_area_range=(80,  80_000)),
    "LED_0805":     FootprintEnvelope(pad_count_range=(1, 6),   aspect_ratio_min=1.5, body_area_range=(80,  80_000)),
    # ── Other common SMD passives ──────────────────────────────────────────────
    "R_0603":       FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=1.5, body_area_range=(40,  40_000)),
    "C_0603":       FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=1.5, body_area_range=(40,  40_000)),
    "R_1206":       FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=1.5, body_area_range=(150, 80_000)),
    "C_1206":       FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=1.5, body_area_range=(150, 80_000)),
    # ── Electrolytic caps (round body, 2 pads) ─────────────────────────────────
    # CP_Elec matches KiCad footprints like CP_Elec_3x5.3 (EEEFC1H1R0R, 1µF 50V)
    "CP_Elec":      FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=0.0, body_area_range=(100, 30_000)),
    "CP_EIA-3528":  FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=0.0, body_area_range=(200, 20_000)),
    "CP_EIA-6032":  FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=0.0, body_area_range=(500, 40_000)),
    "ELCAP":        FootprintEnvelope(pad_count_range=(2, 2),   aspect_ratio_min=0.0, body_area_range=(200, 40_000)),
    # ── ICs ────────────────────────────────────────────────────────────────────
    "SOIC-8":       FootprintEnvelope(pad_count_range=(6, 10),  aspect_ratio_min=1.0, body_area_range=(500, 25_000)),
    "SOIC-16":      FootprintEnvelope(pad_count_range=(12, 18), aspect_ratio_min=1.0, body_area_range=(1_000, 60_000)),
    "SOIC-14":      FootprintEnvelope(pad_count_range=(10, 16), aspect_ratio_min=1.0, body_area_range=(800, 50_000)),
    "SOT-23":       FootprintEnvelope(pad_count_range=(3, 3),   aspect_ratio_min=0.8, body_area_range=(30,   2_000)),
    "SOT-23-5":     FootprintEnvelope(pad_count_range=(4, 6),   aspect_ratio_min=0.8, body_area_range=(50,   3_000)),
    # ── Connectors ─────────────────────────────────────────────────────────────
    "USB_Micro-B":  FootprintEnvelope(pad_count_range=(4, 8),   aspect_ratio_min=1.0, body_area_range=(200, 15_000)),
    "USB_Type-C":   FootprintEnvelope(pad_count_range=(4, 12),  aspect_ratio_min=1.0, body_area_range=(300, 20_000)),
    "USB_Mini-B":   FootprintEnvelope(pad_count_range=(4, 8),   aspect_ratio_min=1.0, body_area_range=(200, 15_000)),
}


def get_envelope(footprint: str) -> Optional[FootprintEnvelope]:
    """
    Return the first envelope whose key is a case-insensitive substring of `footprint`.
    Returns None if no match — callers should treat None as "no check, proceed".
    """
    fp_upper = footprint.upper()
    for key, env in FOOTPRINT_ENVELOPES.items():
        if key.upper() in fp_upper:
            return env
    return None
