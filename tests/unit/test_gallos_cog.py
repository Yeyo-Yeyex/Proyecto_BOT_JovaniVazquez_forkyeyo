"""Pruebas de bot.cogs.gallos: el reñidero contra la casa y los duelos con grada.

Se usa la economía real sobre un SQLite temporal, un dibujante falso (las
imágenes tienen sus propias pruebas), esperas a cero y un azar cuyo primer
número decide quién gana (`Fixed`): el resto de la pelea sale de una semilla.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import gallos as gallos_cog
from bot.cogs.gallos import Duel, Gallos, HousePanel, Phase
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import EconomyRepository
from bot.repositories.gallos import GallosRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.gallos import RIVAL_BY_KEY, Corner, duel_prize, house_payout, pool_payouts
from bot.services.gallos_render import Media

GUILD_ID = 1
OWNER_ID = 10
RIVAL_ID = 11
FAN_IDS = (20, 21, 22)
WIN, LOSE = 0.0, 0.999


class Fixed(random.Random):
    """Azar cuyo primer `random()` es `first` (quién gana); lo demás, de una semilla."""

    def __init__(self, first: float) -> None:
        super().__init__(7)
        self.first: float | None = first

    def random(self) -> float:
        if self.first is not None:
            value, self.first = self.first, None
            return value
        return super().random()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gallos_cog, "REVEAL_MARGIN_SECONDS", 0)


def make_user(user_id: int) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = f"Persona {user_id}"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def fake_renderer() -> MagicMock:
    renderer = MagicMock()
    renderer.still = AsyncMock(return_value=b"png")
    renderer.fight = AsyncMock(return_value=Media(gif=b"GIF89a", png=b"png", seconds=0.0))
    renderer.close = AsyncMock()
    return renderer


async def make_cog(tmp_path: Path, first: float) -> Gallos:
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    repository = GallosRepository(tmp_path / "bot.db")
    await repository.initialize()

    async def no_sleep(seconds: float) -> None:
        # Las esperas del GIF no se esperan; las del reto y la grada, nunca acaban.
        if seconds >= 30:
            await asyncio.Event().wait()

    return Gallos(
        MagicMock(),
        economy=EconomyService(economy_repository),
        repository=repository,
        rng=Fixed(first),
        renderer=fake_renderer(),
        sleep=no_sleep,
    )


def ledger(tmp_path: Path, user_id: int) -> list[tuple[str, int]]:
    """Movimientos de un miembro (motivo, importe), sin el saldo de bienvenida."""
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id",
            (GUILD_ID, user_id),
        ).fetchall()
    finally:
        connection.close()
    return [(row[0], row[1]) for row in rows if row[0] != "bienvenida"]


# -- Contra la casa ----------------------------------------------------------------------


async def open_panel(cog: Gallos, amount: str = "100") -> HousePanel:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    challenged = await cog._gallos_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text=amount,
        rival=None,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    assert not challenged
    assert send.await_args.kwargs["file"].filename == "gallos.png"
    (panel,) = cog.panels
    return panel


async def test_ganar_a_la_casa_cobra_la_cuota_y_apunta_la_victoria(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    rival = RIVAL_BY_KEY["falcon"]
    assert panel.rival is rival
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._fight(interaction)
    assert ledger(tmp_path, OWNER_ID) == [
        ("gallos:apuesta", -100),
        ("gallos:premio", house_payout(100, rival)),
    ]
    assert panel.record.wins == 1 and panel.record.fights == 1
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).wins == 1
    cog.renderer.fight.assert_awaited_once()
    assert "+" in (panel.result or "")


async def test_perder_contra_la_casa_no_paga_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, LOSE)
    panel = await open_panel(cog)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert ledger(tmp_path, OWNER_ID) == [("gallos:apuesta", -100)]
    assert panel.record.wins == 0 and panel.record.fights == 1


async def test_sin_saldo_no_se_pelea(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    panel.stake = STARTING_BALANCE * 10
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel._fight(interaction)
    assert ledger(tmp_path, OWNER_ID) == []
    interaction.followup.send.assert_awaited_once()
    cog.renderer.fight.assert_not_awaited()


async def test_elegir_rival_cambia_la_cuota(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    interaction = fake_interaction(make_user(OWNER_ID))
    interaction.data = {"values": ["marte"]}
    await panel._choose_rival(interaction)
    assert panel.rival is RIVAL_BY_KEY["marte"]
    assert "×9,50" in panel.description()


async def test_bautizar_al_gallo_lo_guarda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await panel.rename(fake_interaction(make_user(OWNER_ID)), "Espolón de Oro")
    assert (await cog.repository.get(GUILD_ID, OWNER_ID)).name == "Espolón de Oro"
    assert "Espolón de Oro" in panel.description()


async def repaint(panel: HousePanel) -> dict:
    """Repinta el panel con un clic nuevo y devuelve lo que se mandó a Discord."""
    interaction = fake_interaction(make_user(OWNER_ID))
    await panel.repaint(interaction)
    return interaction.response.edit_message.await_args.kwargs


async def test_repintar_la_misma_presentacion_no_vuelve_a_subir_la_imagen(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    assert "attachments" in await repaint(panel)
    cog.renderer.still.reset_mock()
    kwargs = await repaint(panel)
    assert "attachments" not in kwargs
    assert kwargs["view"] is panel
    cog.renderer.still.assert_not_awaited()


async def test_repintar_con_otro_rival_nombre_o_apuesta_sube_la_imagen(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await repaint(panel)
    cambios = (
        ("rival", RIVAL_BY_KEY["marte"]),
        ("record", gallos_cog.RoosterRecord(name="Espolón de Oro")),
        ("stake", panel.stake + 1),
    )
    for attribute, value in cambios:
        setattr(panel, attribute, value)
        cog.renderer.still.reset_mock()
        kwargs = await repaint(panel)
        assert len(kwargs["attachments"]) == 1, attribute
        cog.renderer.still.assert_awaited_once()


async def test_tras_una_pelea_el_siguiente_repintado_vuelve_a_subir_la_imagen(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await repaint(panel)
    await panel._fight(fake_interaction(make_user(OWNER_ID)))
    assert "attachments" in await repaint(panel)


# -- Duelos ------------------------------------------------------------------------------


async def challenge(cog: Gallos, amount: str = "500") -> Duel:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    challenged = await cog._gallos_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(OWNER_ID),
        amount_text=amount,
        rival=make_user(RIVAL_ID),
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    assert challenged
    (duel,) = cog.duels
    return duel


async def accept(duel: Duel) -> None:
    await duel.accept(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.LOBBY
    # La pelea se corre a mano: sin esperar a la grada.
    assert duel.task is not None
    duel.task.cancel()


async def test_un_duelo_cobra_a_los_dos_y_paga_al_ganador(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    assert duel.timer is not None
    await accept(duel)
    assert ledger(tmp_path, OWNER_ID) == [("gallos:apuesta", -500)]
    assert ledger(tmp_path, RIVAL_ID) == [("gallos:apuesta", -500)]
    for fan, corner in zip(FAN_IDS, (Corner.RED, Corner.BLUE, Corner.RED), strict=True):
        await duel.place(fake_interaction(make_user(fan)), corner, "200")
    await duel.brawl()
    assert duel.fight is not None and duel.phase is Phase.DONE
    winner = duel.fight.winner
    loser_id = RIVAL_ID if winner is Corner.RED else OWNER_ID
    winner_id = OWNER_ID if winner is Corner.RED else RIVAL_ID
    assert ledger(tmp_path, winner_id)[-1] == ("gallos:premio", duel_prize(500))
    assert ledger(tmp_path, loser_id) == [("gallos:apuesta", -500)]
    payouts = pool_payouts({k: s.bet for k, s in duel.crowd.items()}, winner)
    paid = 0
    for fan in FAN_IDS:
        moves = ledger(tmp_path, fan)
        assert moves[0] == ("gallos:apuesta", -200)
        paid += sum(amount for reason, amount in moves[1:])
        assert sum(amount for reason, amount in moves[1:]) == payouts[fan]
    # La grada no crea dinero: se reparte lo apostado menos la comisión.
    assert paid <= 600
    assert (await cog.repository.get(GUILD_ID, winner_id)).wins == 1
    assert not cog.busy


async def test_los_duenos_no_apuestan_en_la_grada_y_cada_uno_apuesta_una_vez(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    await accept(duel)
    owner = fake_interaction(make_user(OWNER_ID))
    await duel._open_bet(owner, Corner.RED)
    owner.response.send_modal.assert_not_awaited()
    fan = fake_interaction(make_user(FAN_IDS[0]))
    await duel._open_bet(fan, Corner.RED)
    fan.response.send_modal.assert_awaited_once()
    await duel.place(fake_interaction(make_user(FAN_IDS[0])), Corner.RED, "100")
    again = fake_interaction(make_user(FAN_IDS[0]))
    await duel._open_bet(again, Corner.BLUE)
    again.response.send_modal.assert_not_awaited()
    assert len(duel.crowd) == 1


async def test_rechazar_no_mueve_dinero_y_libera_a_los_dos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    stranger = fake_interaction(make_user(FAN_IDS[0]))
    await duel.reject(stranger)
    assert duel.phase is Phase.CHALLENGE
    await duel.reject(fake_interaction(make_user(RIVAL_ID)))
    assert duel.phase is Phase.DONE
    assert ledger(tmp_path, OWNER_ID) == [] and ledger(tmp_path, RIVAL_ID) == []
    assert not cog.busy and not cog.duels


async def test_no_se_puede_estar_en_dos_duelos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    await challenge(cog)
    errors = AsyncMock()
    challenged = await cog._gallos_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(FAN_IDS[0]),
        amount_text="100",
        rival=make_user(RIVAL_ID),
        send=AsyncMock(),
        send_error=errors,
    )
    assert not challenged
    errors.assert_awaited_once()


async def test_apagar_con_la_grada_abierta_devuelve_lo_apostado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    duel = await challenge(cog)
    await accept(duel)
    await duel.place(fake_interaction(make_user(FAN_IDS[0])), Corner.BLUE, "300")
    await cog.cog_unload()
    for user_id, stake in ((OWNER_ID, 500), (RIVAL_ID, 500), (FAN_IDS[0], 300)):
        assert ledger(tmp_path, user_id) == [("gallos:apuesta", -stake), ("gallos:premio", stake)]
    assert duel.phase is Phase.DONE
