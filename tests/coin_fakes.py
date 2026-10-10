"""Azar de guion y partidas hechas a mano para las pruebas de Cara o cruz (`moneda`).

`Scripted` es un `random.Random` falso: en vez de números al azar devuelve los que
necesita `bot.services.coin.toss` para que salgan los resultados que se le piden,
en orden. Así ninguna prueba depende de la estadística. Si se acaba el guion falla
(la prueba ha sorteado más veces de las previstas), no inventa nada.

Se importa como `from coin_fakes import Scripted, game_after` (`pyproject.toml`
añade `tests/` al path de importación de pytest).
"""

from __future__ import annotations

import random

from bot.services.coin import CoinGame, Outcome, Side

#: Los números de azar que hacen que `toss` devuelva cada resultado.
ROLLS: dict[Outcome, list[float]] = {
    Outcome.EDGE: [0.0],
    Outcome.CARA: [0.5, 0.2],
    Outcome.CRUZ: [0.5, 0.8],
}


class Scripted(random.Random):
    """Azar de guion: `random()` devuelve los números que dan esos resultados, en orden.

    Cada lanzamiento consume dos números (canto y lado) o uno solo si cae de canto.
    El primer resultado es el de `CoinGame.new` (el `upcoming`); cada `flip` gasta
    uno más para sortear el siguiente.
    """

    def __init__(self, *outcomes: Outcome) -> None:
        super().__init__(0)
        self.queue = [roll for outcome in outcomes for roll in ROLLS[outcome]]

    def random(self) -> float:
        assert self.queue, "se ha sorteado más veces de las previstas"
        return self.queue.pop(0)

    def getrandbits(self, k: int) -> int:
        """Para `randrange` (la semilla del GIF): no gasta el guion de la moneda."""
        return 0


def game_after(
    picks: list[Side],
    outcomes: list[Outcome],
    *,
    stake: int = 100,
    upcoming: Outcome = Outcome.CARA,
) -> CoinGame:
    """Partida en la que se pide `picks[i]` y cae `outcomes[i]`; el siguiente es `upcoming`."""
    rng = Scripted(*outcomes, upcoming)
    game = CoinGame.new(stake, rng)
    for pick in picks:
        game.flip(pick, rng)
    return game
