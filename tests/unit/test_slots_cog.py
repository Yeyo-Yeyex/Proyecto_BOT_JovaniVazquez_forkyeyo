"""Pruebas de bot.cogs.slots: la máquina con botones, el comando `slots` y Auto.

Se usa la economía real sobre un SQLite temporal (para comprobar que el
dinero se mueve de verdad), rodillos trucados y un renderizador falso.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot.cogs.slots as slots_module
from bot.cogs.slots import (
    AUTO_SPINS,
    GIF_NAME,
    PNG_NAME,
    SlotMachineView,
    Slots,
    SlotsPlay,
    auto_text,
    machine_embed,
    parse_stake,
    result_text,
)
from bot.repositories.economy import EconomyRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.slots import (
    FREE_SPINS,
    HEAT_MAX,
    POT_SEED,
    REEL_STRIPS,
    Kind,
    SlotMachine,
    spin_at,
)
from bot.services.slots_render import SlotsMedia

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555
ALL_STOPS = list(itertools.product(*(range(len(s)) for s in REEL_STRIPS)))


def stops_where(predicate) -> tuple[int, int, int]:  # noqa: ANN001
    for stops in ALL_STOPS:
        if predicate(spin_at(stops)):
            return stops
    raise AssertionError("Ninguna parada cumple la condición")


LOSS = stops_where(lambda s: not s.pay_halves and not s.scatters and not s.near_miss)
CHERRY = stops_where(lambda s: s.kind == Kind.CHERRY and not s.scatters)
GRAPES = stops_where(lambda s: s.kind == Kind.THREE and s.symbol == "G" and not s.scatters)
JACKPOT = stops_where(lambda s: s.is_jackpot)
FREE = stops_where(lambda s: s.triggers_free_spins and not s.pay_halves)


class FakeRenderer:
    """Devuelve bytes fijos: las pruebas no necesitan dibujar los rodillos."""

    def render(self, spin, *, turbo: bool = False) -> SlotsMedia:  # noqa: ANN001
        return SlotsMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    def still_png(self, stops, *, highlight: bool = False) -> bytes:  # noqa: ANN001
        return b"STILL"


class RiggedMachine(SlotMachine):
    """Para los rodillos donde se le diga, en orden; después, siempre en `LOSS`."""

    def __init__(self, sequence: Iterable[tuple[int, int, int]] = ()) -> None:
        super().__init__()
        self.sequence = list(sequence)

    def spin(self, *, free: bool = False):  # noqa: ANN201
        stops = self.sequence.pop(0) if self.sequence else LOSS
        return spin_at(stops, count_scatters=not free)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """La animación no hace falta esperarla en las pruebas."""
    monkeypatch.setattr(slots_module, "REVEAL_MARGIN_SECONDS", 0)


async def make_cog(tmp_path: Path, sequence=(), channels=frozenset()) -> Slots:  # noqa: ANN001
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Slots(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        machine=RiggedMachine(sequence),
        casino_channel_ids=channels,
    )


def make_user(user_id: int = OWNER_ID, name: str = "Diego") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    interaction = MagicMock()
    interaction.user = make_user(user_id)
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.edit_original_response = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def make_view(cog: Slots, stake: int = 100) -> SlotMachineView:
    return SlotMachineView(cog, guild_id=GUILD_ID, owner=make_user(), stake=stake)


def attachment_names(call) -> list[str]:  # noqa: ANN001
    return [file.filename for file in call.kwargs["attachments"]]


# -- Presentación -------------------------------------------------------------------


def make_play(stops, *, stake=100, payout=0, jackpot=0, free=False, hot=False) -> SlotsPlay:  # noqa: ANN001
    settlement = MagicMock()
    settlement.jackpot = jackpot
    settlement.bet.balance = 1_000
    return SlotsPlay(
        spin=spin_at(stops),
        stake=stake,
        free=free,
        hot=hot,
        payout=payout,
        settlement=settlement,
        media=SlotsMedia(b"", b"", 0.0),
        heat=0,
        session_spins=1,
    )


def test_el_medio_premio_se_celebra_como_premio() -> None:
    text = result_text(make_play(CHERRY, payout=50), random.Random(0))
    assert "Cobras 50 Y$" in text
    assert "-" not in text.splitlines()[0]


def test_el_jackpot_dice_cuanto_se_lleva_del_bote() -> None:
    text = result_text(make_play(JACKPOT, jackpot=12_345), random.Random(0))
    assert "12.345 Y$ del bote" in text


def test_un_trio_enseña_sus_simbolos() -> None:
    text = result_text(make_play(GRAPES, payout=1_000), random.Random(0))
    assert "🍇 🍇 🍇" in text
    assert "+900 Y$" in text


def test_los_tickets_anuncian_los_giros_gratis() -> None:
    assert "GIROS GRATIS" in result_text(make_play(FREE), random.Random(0))


def test_resumen_de_auto() -> None:
    plays = [make_play(GRAPES, payout=1_000), *[make_play(LOSS) for _ in range(9)]]
    text = auto_text(plays)
    assert "10 tiradas" in text
    assert "+0 Y$" not in text
    assert "1 con premio" in text


def test_maquina_caliente_avisa_de_la_siguiente_tirada() -> None:
    embed = machine_embed(
        owner="Diego",
        balance=10,
        stake=5,
        pot=POT_SEED,
        heat=HEAT_MAX,
        free_spins=0,
        free_stake=0,
        turbo=False,
    )
    assert any("caliente" in field.name for field in embed.fields)


def test_apuesta_por_defecto_y_formatos() -> None:
    assert parse_stake(None, 1_000) == 100
    assert parse_stake(None, 40) == 40
    assert parse_stake("all", 777) == 777
    with pytest.raises(ValueError):
        parse_stake("azul", 1_000)


# -- Máquina ------------------------------------------------------------------------


async def test_tirar_cobra_gira_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES])
    view = make_view(cog)
    interaction = make_interaction()

    await view.play(interaction)

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100 + 1_000
    assert attachment_names(interaction.response.edit_message.await_args) == [GIF_NAME]
    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]
    assert await cog.pot(GUILD_ID) == POT_SEED + 3


async def test_en_turbo_solo_se_edita_una_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.turbo = True
    interaction = make_interaction()

    await view.play(interaction)

    assert attachment_names(interaction.response.edit_message.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_not_awaited()


async def test_el_turbo_se_recuerda_para_la_siguiente_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await make_view(cog)._toggle_turbo(make_interaction())
    assert make_view(cog).turbo


async def test_sin_saldo_no_gira_y_avisa_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=STARTING_BALANCE + 1)
    interaction = make_interaction()

    await view.play(interaction)

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
    assert await cog.pot(GUILD_ID) == POT_SEED


async def test_clic_mientras_gira_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view._busy = True
    interaction = make_interaction()

    await view.play(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_otro_miembro_no_puede_jugar_en_tu_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    interaction = make_interaction(user_id=99)

    assert not await view.interaction_check(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_los_tickets_dan_giros_gratis_que_no_se_cobran(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [FREE])
    view = make_view(cog)

    await view.play(make_interaction())
    assert view.free_spins == FREE_SPINS
    after_trigger = await cog.economy.balance(GUILD_ID, OWNER_ID)

    view.stake = 500  # cambiar la apuesta no cambia la de los giros gratis
    await view.play(make_interaction())

    assert view.free_spins == FREE_SPINS - 1
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == after_trigger


async def test_la_maquina_caliente_paga_doble(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [CHERRY] * HEAT_MAX + [GRAPES])
    view = make_view(cog)
    for _ in range(HEAT_MAX):
        await view.play(make_interaction())
    assert cog.heat(GUILD_ID, OWNER_ID) == HEAT_MAX
    before = await cog.economy.balance(GUILD_ID, OWNER_ID)

    await view.play(make_interaction())

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == before - 100 + 2_000
    assert cog.heat(GUILD_ID, OWNER_ID) == 0


async def test_auto_juega_diez_tiradas_con_una_sola_edicion(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    interaction = make_interaction()

    await view._auto(interaction)

    assert view.session_spins == AUTO_SPINS
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * AUTO_SPINS
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited_once()


async def test_auto_para_cuando_no_llega_el_dinero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=300)

    await view._auto(make_interaction())

    assert view.session_spins == 3
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 900


async def test_auto_para_con_el_bote_y_lo_anuncia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [LOSS, JACKPOT])
    view = make_view(cog)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    view.message = MagicMock()
    view.message.channel = channel

    await view._auto(make_interaction())

    assert view.session_spins == 2
    assert "JOVANAZO" in channel.send.await_args.args[0]
    assert await cog.pot(GUILD_ID) == POT_SEED


async def test_al_cerrar_se_juegan_los_giros_gratis_pendientes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [FREE])
    view = make_view(cog)
    await view.play(make_interaction())

    await view.on_timeout()

    assert view.free_spins == 0
    assert view.session_spins == 1 + FREE_SPINS


async def test_all_in_y_mitad_cambian_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)

    await view._all_in(make_interaction())
    assert view.stake == STARTING_BALANCE
    await view._halve(make_interaction())
    assert view.stake == STARTING_BALANCE // 2


async def test_la_tabla_de_premios_es_privada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = make_interaction()

    await make_view(cog)._paytable(interaction)

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


# -- Comando ------------------------------------------------------------------------


async def test_slots_abre_la_maquina_parada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send = AsyncMock(return_value=MagicMock())
    channel = MagicMock()
    channel.id = CASINO_CHANNEL

    await cog._slots_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(),
        amount_text="250",
        send=send,
        send_error=AsyncMock(),
    )

    kwargs = send.await_args.kwargs
    assert kwargs["file"].filename == PNG_NAME
    assert kwargs["view"].stake == 250
    assert kwargs["view"] in cog.machines


async def test_slots_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))
    send_error = AsyncMock()
    channel = MagicMock()
    channel.id = 1234

    await cog._slots_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(),
        amount_text=None,
        send=AsyncMock(),
        send_error=send_error,
    )

    assert "tragaperras" in send_error.await_args.args[0].lower()


async def test_slots_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send_error = AsyncMock()

    await cog._slots_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(),
        user=make_user(),
        amount_text="5000",
        send=AsyncMock(),
        send_error=send_error,
    )

    send_error.assert_awaited_once()
