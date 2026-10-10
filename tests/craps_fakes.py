"""Azar de guion y partidas hechas a mano para las pruebas de los dados (`dados`, craps).

`Scripted` es un `random.Random` falso: `randint` devuelve los dados que se le dan, en
orden, así que `bot.services.craps.throw` (dos `randint(1, 6)` por tirada) saca justo
las tiradas pedidas y ninguna prueba depende de la estadística. Si se acaba el guion
falla (la prueba ha tirado más veces de las previstas), no inventa nada.

Se importa como `from craps_fakes import Scripted, game_after` (`pyproject.toml`
añade `tests/` al path de importación de pytest).
"""

from __future__ import annotations

import random

from bot.services.craps import Bet, CrapsGame, Hand, Roll, Status
from bot.services.craps_render import Table


class Scripted(random.Random):
    """Azar de guion: cada tirada son dos dados, `Scripted((3, 4), (1, 1), ...)`.

    `getrandbits` devuelve 0 para que `randrange` (la semilla del GIF) no gaste el guion.
    """

    def __init__(self, *rolls: tuple[int, int]) -> None:
        super().__init__(0)
        self.queue = [die for roll in rolls for die in roll]

    def randint(self, a: int, b: int) -> int:
        assert self.queue, "se ha tirado más veces de las previstas"
        return self.queue.pop(0)

    def getrandbits(self, k: int) -> int:
        """Para `randrange` (la semilla del GIF): no gasta el guion de los dados."""
        return 0


def game_after(
    *rolls: tuple[int, int], bet: Bet = Bet.PASS, stake: int = 100, odds: int = 0
) -> CrapsGame:
    """Partida en la que ya han salido `rolls`; al poner el punto mete las `odds`."""
    rng = Scripted(*rolls)
    game = CrapsGame.new(stake, bet)
    for _ in rolls:
        game.play(rng)
        if odds and game.status is Status.POINT and game.odds == 0:
            game.add_odds(odds)
    return game


def table_for(game: CrapsGame | None, *, stake: int = 100, bet: Bet = Bet.PASS) -> Table:
    """La mesa que dibuja el cog: la partida, una mano que ha visto sus tiradas y el historial."""
    hand = Hand()
    history: list[tuple[Roll, Bet]] = []
    if game is not None:
        for roll in game.rolls:
            hand.observe(roll)
            history.append((roll, game.bet))
    return Table(game=game, hand=hand, stake=stake, bet=bet, history=tuple(history))
