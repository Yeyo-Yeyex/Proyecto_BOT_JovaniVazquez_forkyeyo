"""Pruebas de bot.services.hold_win_render: las tres máquinas se dibujan bien."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image

from bot.services.hold_win import (
    MAJOR_BASE,
    MINI_BASE,
    THEMES,
    BonusGame,
    BonusKind,
    Tier,
    Trigger,
    spin_base,
)
from bot.services.hold_win_render import (
    HEIGHT,
    WIDTH,
    Banner,
    BonusPanel,
    HoldWinRenderer,
    Panel,
    coin_image,
    short_amount,
)

PANEL = Panel(mini=150, major=600, grand=100_000, cases=((10, 108), (5, 50), (21, 22)))


@pytest.fixture(scope="module")
def renderer() -> HoldWinRenderer:
    return HoldWinRenderer()


def frames(gif: bytes) -> list[Image.Image]:
    image = Image.open(io.BytesIO(gif))
    out = []
    for index in range(image.n_frames):
        image.seek(index)
        out.append(image.convert("RGB").copy())
    return out


def test_cantidades_cortas_para_las_monedas() -> None:
    assert short_amount(950) == "950"
    assert short_amount(1_250) == "1,2k"
    assert short_amount(35_000) == "35k"
    assert short_amount(1_500_000) == "1,5M"


def test_las_monedas_se_distinguen_por_la_forma() -> None:
    """Con deuteranopia el verde y el rojo se confunden: la silueta no debe ser igual."""
    silhouettes = {
        tier: coin_image(tier, "").getchannel("A").point(lambda a: 255 if a > 128 else 0)
        for tier in Tier
    }
    green, blue, red = (silhouettes[t].tobytes() for t in Tier)
    assert len({green, blue, red}) == 3


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_la_tirada_acaba_en_la_imagen_final(renderer: HoldWinRenderer, theme: str) -> None:
    rng = random.Random(4)
    spin = spin_base(rng)
    media = renderer.render_base(
        theme, spin, stake=100, panel=PANEL, banner=Banner("¡GRAN PREMIO!", "+1.000 Y$", 1)
    )
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert png.size == (WIDTH, HEIGHT)
    last = frames(media.gif)[-1]
    assert last.tobytes() == png.tobytes()
    assert media.seconds > 0.5


def test_en_turbo_no_hay_gif(renderer: HoldWinRenderer) -> None:
    media = renderer.render_base("volcan", spin_base(random.Random(1)), stake=10, panel=PANEL,
                                 turbo=True)  # fmt: skip
    assert media.gif == b""
    assert media.png.startswith(b"\x89PNG")


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_cada_tirada_del_bonus_tiene_animacion_y_final(
    renderer: HoldWinRenderer, theme: str
) -> None:
    rng = random.Random(8)
    game = BonusGame.start(
        Trigger(BonusKind.GRAND, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=rng
    )

    def panel() -> BonusPanel:
        return BonusPanel(
            name="¡ERUPCIÓN!",
            mini=150,
            major=600,
            grand=100_000,
            respins_left=game.respins_left,
            reset_value=game.reset_value,
            multiplier=game.multiplier,
            coins=game.coins,
            won=game.jackpots(),
        )

    while not game.finished:
        before, panel_before = list(game.board), panel()
        step = game.step(rng)
        media = renderer.render_bonus_step(
            theme,
            before,
            step,
            stake=100,
            panel_before=panel_before,
            panel_after=panel(),
            banner=Banner("TOTAL", "+9.999 Y$", 2) if game.finished else None,
            rng=rng,
        )
        png = Image.open(io.BytesIO(media.png)).convert("RGB")
        assert frames(media.gif)[-1].tobytes() == png.tobytes()


def test_la_imagen_parada_del_juego_base_y_del_bonus(renderer: HoldWinRenderer) -> None:
    spin = spin_base(random.Random(2))
    assert renderer.base_still("olimpo", spin, stake=100, panel=PANEL).startswith(b"\x89PNG")
    game = BonusGame(kind=BonusKind.RED, stake=100, mini=MINI_BASE, major=MAJOR_BASE)
    panel = BonusPanel("Bonus Ares", 150, 600, 100_000, 3, 3, 1, 0)
    png = renderer.bonus_still("olimpo", game.board, stake=100, panel=panel)
    assert png.startswith(b"\x89PNG")
