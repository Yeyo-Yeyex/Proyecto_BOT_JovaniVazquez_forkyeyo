"""Dobles de los dibujantes de las máquinas del casino, para pruebas que no miran la imagen.

`SlotsRenderer`, `WheelRenderer` y `PachinkoRenderer` tardan entre 0,05 y 3 s por
tirada (más el precalentamiento de los tres al cargar el cog, que dibuja las 38 casillas
de la ruleta o las piezas de cada tablero en segundo plano). Las pruebas de los botones
(`tests/integration/test_button_speed.py`) y de los puentes entre cogs
(`tests/integration/test_cog_bridges.py`) solo miran el orden de las respuestas, la
base de datos, los logros y las jugadas: la imagen no entra en ninguna aserción.

`use_fake_drawings` cambia las tres clases en sus módulos de servicio ANTES de cargar los
cogs. `load_extension` vuelve a ejecutar cada cog y su `from ... import SlotsRenderer`
recoge el doble, así que el cog, el bot y la base de datos son los reales y solo se
sustituye el dibujo. Los dobles devuelven los mismos tipos (`SlotsMedia`, `SpinMedia`,
`PachinkoMedia`) con bytes de relleno y `seconds=0.0`, y respetan `turbo` (GIF vacío),
que es lo que el cog mira para decidir qué enseña. Las pruebas del dibujo de verdad están
en `tests/unit/test_*_render.py`, que no usan esto.

Se importa como `from render_fakes import use_fake_drawings` (`pyproject.toml` añade
`tests/` al path de importación de pytest).
"""

from __future__ import annotations

import pytest

import bot.services.pachinko_render as pachinko_render
import bot.services.roulette_render as roulette_render
import bot.services.slots_render as slots_render


class FakeSlotsRenderer:
    """`SlotsRenderer` sin dibujar: PNG y GIF de relleno."""

    def still_png(self, stops: object, *, highlight: bool = False) -> bytes:
        return b"PNG"

    def render(
        self, spin: object, *, turbo: bool = False, won: int = 0, stake: int = 0
    ) -> slots_render.SlotsMedia:
        return slots_render.SlotsMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)


class FakeWheelRenderer:
    """`WheelRenderer` sin dibujar: ni la casilla ni el precalentamiento cuestan nada."""

    def media(self, pocket: int) -> roulette_render.SpinMedia:
        return roulette_render.SpinMedia(gif=b"GIF", png=b"PNG")

    def idle_png(self) -> bytes:
        return b"PNG"


class FakePachinkoRenderer:
    """`PachinkoRenderer` sin dibujar: PNG y GIF de relleno, y sin piezas que precalentar."""

    def warm_up(self) -> None:
        return None

    def idle_png(self, board: object) -> bytes:
        return b"PNG"

    def still_png(self, volley: object, motion: object = None) -> bytes:
        return b"PNG"

    def render(
        self, volley: object, *, turbo: bool = False, motion: object = None
    ) -> pachinko_render.PachinkoMedia:
        return pachinko_render.PachinkoMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)


def use_fake_drawings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sustituye los tres dibujantes por sus dobles; llamar antes de cargar los cogs."""
    monkeypatch.setattr(slots_render, "SlotsRenderer", FakeSlotsRenderer)
    monkeypatch.setattr(roulette_render, "WheelRenderer", FakeWheelRenderer)
    monkeypatch.setattr(pachinko_render, "PachinkoRenderer", FakePachinkoRenderer)
