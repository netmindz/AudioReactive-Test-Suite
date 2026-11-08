"""MoonModules GEQ channel bin maps for the documented settings."""

from __future__ import annotations

from typing import TypeAlias


Band: TypeAlias = tuple[int, int, float]

_FREQUENCY_DISTRIBUTIONS = {
    0: [
        (1, 1, 1.0), (2, 2, 1.0), (3, 4, 1.0), (5, 6, 1.0),
        (7, 9, 1.0), (10, 12, 1.0), (13, 18, 1.0), (19, 25, 1.0),
        (26, 32, 1.0), (33, 43, 1.0), (44, 55, 1.0), (56, 69, 1.0),
        (70, 85, 1.0), (86, 103, 1.0), (104, 164, 0.88), (165, 215, 0.70),
    ],
    1: [
        (1, 1, 1.0), (2, 2, 1.0), (3, 3, 1.0), (4, 4, 1.0),
        (5, 6, 1.0), (7, 8, 1.0), (9, 10, 1.0), (11, 13, 1.0),
        (14, 18, 1.0), (19, 25, 1.0), (26, 36, 1.0), (37, 45, 1.0),
        (46, 66, 1.0), (67, 97, 1.0), (98, 164, 0.88), (165, 215, 0.70),
    ],
}


def geq_bands(frequency_distribution: int = 0, use_input_filter: bool = False) -> list[Band]:
    """Return each band's inclusive bin range and mapping multiplier."""
    if frequency_distribution not in _FREQUENCY_DISTRIBUTIONS:
        raise ValueError(f"unsupported frequency distribution: {frequency_distribution}")
    bands = list(_FREQUENCY_DISTRIBUTIONS[frequency_distribution])
    if not use_input_filter:
        return bands
    if frequency_distribution == 0:
        bands[:4] = [(3, 3, 0.8), (4, 4, 0.9), (5, 5, 1.0), (6, 6, 1.0)]
        bands[15] = (165, 205, 0.75)
    else:
        bands[:4] = [(1, 1, 0.8), (2, 2, 0.9), (3, 3, 1.0), (4, 4, 1.0)]
        bands[15] = (165, 205, 0.75)
    return bands
