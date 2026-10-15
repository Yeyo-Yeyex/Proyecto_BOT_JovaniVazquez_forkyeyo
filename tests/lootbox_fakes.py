"""Azar de guion para las pruebas de las cajas (`bot.services.lootboxes`).

`Scripted` es un `random.Random` al que se le dice qué resultado sale en cada sorteo de
`choices` (`"C"`, `"R"`, `"E"`, `"L"`, `"M"` o `"tapete"`), por orden. Cuando se acaba el
guion sigue con el azar de siempre, con su semilla. Se importa como
`from lootbox_fakes import Scripted` (`pyproject.toml` añade `tests/` al path).
"""

from __future__ import annotations

import random
from collections.abc import Sequence


class Scripted(random.Random):
    """Azar de guion: `choices` devuelve lo que se le pida, por orden."""

    def __init__(self, picks: Sequence[str] | None = None, seed: int = 1) -> None:
        super().__init__(seed)
        self.picks = list(picks or [])

    def choices(  # type: ignore[no-untyped-def,override]
        self, population, weights=None, *, cum_weights=None, k=1
    ):
        if self.picks:
            return [self.picks.pop(0)]
        return super().choices(population, weights, cum_weights=cum_weights, k=k)
