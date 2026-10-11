"""Entradas y premios de Legislatura en yapdollars.

Legislatura es la fuente de dinero de habilidad del bot: quien juega bien en
primas altas gana mucho; quien juega mal en primas bajas, poco o nada.

**Tratamiento fiscal: juego.** La entrada se cobra con
`EconomyService.place_bet` y el premio con `pay_winnings` (`game="legislatura"`),
así que entra en la retención diaria y en la declaración semanal del casino. La
Ley 13/2011, de regulación del juego, art. 3.a, define el juego como arriesgar
dinero «sobre resultados futuros e inciertos, dependientes en alguna medida del
azar […] con independencia de que predomine en ellos el grado de destreza de
los jugadores»: un torneo con entrada y premio en el que se baraja es juego
aunque gane el que mejor juega, como el póker.

**Cifras provisionales.** Se fijan con un simulador que juegue partidas con el
motor real (`docs/legislatura.md`, «Calibrar»), como pide la Biblia para toda
fuente de dinero nueva, antes de abrir el juego. Lo que no cambia es la forma:

- La entrada es ~15 % del premio por ganar, así que perder pronto cuesta y
  ganar compensa mucho (premio convexo, `PRIZE_SHARE`).
- La prima N se desbloquea ganando la N−1: a la mesa de 100.000 Y$ solo llega
  quien ya ha demostrado que sabe jugar.
- Presupuestos prorrogados: tras ganar, el premio se dobla por periodo extra
  (×2, ×4) con tope; si cae el Gobierno en la prórroga, no se cobra nada.
- La partida diaria es gratis y no paga: da clasificación y logros.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.legislatura.run import PERIODS, Phase, Run

#: Motivo en el libro de la economía y clave en las estadísticas del casino.
GAME_KEY = "legislatura"


@dataclass(frozen=True, slots=True)
class StakePrize:
    """Entrada y premio por ganar la legislatura completa en una prima."""

    entry: int
    prize: int


#: Por prima de riesgo (`catalog.STAKES`). Escala general: 10 Y$ = 1 € y una tirada, 100 Y$.
PRIZES: dict[int, StakePrize] = {
    1: StakePrize(300, 2_000),
    2: StakePrize(600, 4_000),
    3: StakePrize(1_000, 7_000),
    4: StakePrize(2_000, 12_000),
    5: StakePrize(3_000, 20_000),
    6: StakePrize(5_000, 35_000),
    7: StakePrize(9_000, 60_000),
    8: StakePrize(15_000, 100_000),
}
#: Parte del premio según los periodos superados (0 a 8). Convexa a propósito.
PRIZE_SHARE: tuple[float, ...] = (0.0, 0.02, 0.05, 0.10, 0.18, 0.28, 0.42, 0.62, 1.0)
#: Multiplicador del premio por periodos de prórroga superados (1 y 2).
EXTENSION_MULTIPLIER: tuple[int, ...] = (1, 2, 4)


def entry_fee(run: Run) -> int:
    """Lo que cuesta empezar esta partida (0 en la diaria)."""
    return 0 if run.mode == "diaria" else PRIZES[run.stake].entry


def payout(run: Run) -> int:
    """Lo que se cobra al cerrar la partida en su estado actual (bruto, antes de IRPF).

    - Partida diaria: nada.
    - Sin prórroga: la parte del premio que toca por periodos superados.
    - En prórroga: el premio entero por el multiplicador de los periodos extra
      superados, pero nada si ha caído el Gobierno.
    """
    if run.mode == "diaria":
        return 0
    prize = PRIZES[run.stake].prize
    if run.extended:
        if run.phase is Phase.PERDIDA:
            return 0
        extra = min(run.cleared_periods - PERIODS, len(EXTENSION_MULTIPLIER) - 1)
        return prize * EXTENSION_MULTIPLIER[max(extra, 0)]
    cleared = min(run.cleared_periods, PERIODS)
    return int(prize * PRIZE_SHARE[cleared])
