"""Azar de Legislatura: con semilla, por flujos y sin estado pesado que guardar.

Cada sorteo pide un flujo por nombre («mazo:3:1», «tienda:3:1», «asesor:veto»)
y recibe un `random.Random` sembrado con la semilla de la partida, el nombre del
flujo y cuántas veces se ha pedido ese flujo. Así:

- **La partida diaria es justa:** dos jugadores con la misma semilla ven la
  misma baraja y la misma tienda en cada sesión aunque uno compre más o
  descarte menos, porque comprar no gasta el azar de barajar (cada cosa tira de
  su flujo). Es lo que hace Balatro con sus semillas por motivo.
- **Se guarda poco:** solo la semilla y un contador por flujo, no los 625
  enteros del estado del Mersenne Twister.
- **Se reproduce:** con la misma semilla y las mismas acciones sale lo mismo,
  y las pruebas no tienen que sustituir `random`.
"""

from __future__ import annotations

import hashlib
import random
import secrets
from collections.abc import MutableSequence, Sequence
from typing import TypeVar

T = TypeVar("T")


def new_seed() -> str:
    """Semilla nueva para una partida libre (8 caracteres, fácil de compartir)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def daily_seed(guild_id: int, day_iso: str) -> str:
    """Semilla de la sesión del día de un servidor: la misma para todos sus miembros."""
    digest = hashlib.sha256(f"legislatura:{guild_id}:{day_iso}".encode()).hexdigest()
    return digest[:8].upper()


class Rng:
    """Azar por flujos con nombre.

    Args:
        seed: Semilla de la partida.
        counters: Cuántas veces se ha pedido cada flujo (se guarda con la partida).
    """

    def __init__(self, seed: str, counters: dict[str, int] | None = None) -> None:
        self.seed = seed
        self.counters: dict[str, int] = dict(counters or {})

    def stream(self, name: str) -> random.Random:
        """Un generador nuevo y determinista para el siguiente uso del flujo `name`."""
        count = self.counters.get(name, 0)
        self.counters[name] = count + 1
        return random.Random(f"{self.seed}|{name}|{count}")

    def chance(self, name: str, one_in: int) -> bool:
        """Cierto con probabilidad 1/`one_in`."""
        return self.stream(name).randrange(one_in) == 0

    def choice(self, name: str, options: Sequence[T]) -> T:
        """Uno de `options` al azar.

        Raises:
            IndexError: Si `options` está vacío.
        """
        return self.stream(name).choice(options)

    def weighted(self, name: str, options: Sequence[T], weights: Sequence[float]) -> T:
        """Uno de `options` con los pesos dados."""
        return self.stream(name).choices(options, weights=weights, k=1)[0]

    def shuffle(self, name: str, items: MutableSequence[T]) -> None:
        """Baraja `items` en su sitio."""
        self.stream(name).shuffle(items)
