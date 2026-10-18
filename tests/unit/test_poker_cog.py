"""Pruebas de bot.cogs.poker: la mesa del canal, sentarse, decidir, el 💎 Bote, el club y el dinero.

Se usa la economía real y el club real sobre un SQLite temporal, un reloj falso y
un dibujo falso. Las cartas se fijan sustituyendo `deal_table` del cog. El bucle no
se arranca solo (`cog.start` es un doble): cada prueba llama a `deal()` y
`finish()`.
"""

from __future__ import annotations

import dataclasses
import random
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import poker as cog_module
from bot.cogs.poker import Phase, Poker, Table
from bot.repositories.economy import EconomyRepository
from bot.repositories.poker import PokerRepository
from bot.services import poker_club
from bot.services.blackjack import Card
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.poker import BONUS_SHARE, BONUS_STAKE, CALL_FACTOR, MAX_SEATS, POT_SEED, TableDeal
from bot.services.poker_render import Media
from bot.services.taxes import gambling_day_tax

GUILD_ID = 1
CHANNEL_ID = 777
ANA, LEO = 10, 11
START = 1_800_000_000.0
RANKS = {"A": 1, "T": 10, "J": 11, "Q": 12, "K": 13, **{str(n): n for n in range(2, 10)}}
SUITS = {"s": 0, "h": 1, "d": 2, "c": 3}


def cards(text: str) -> tuple[Card, ...]:
    return tuple(Card(RANKS[c[0]], SUITS[c[1]]) for c in text.split())


def table_deal(*seats: str, dealer: str = "9d 4s", board: str = "Kd 9c 6h 3s 2c") -> TableDeal:
    return TableDeal(
        dealer=cards(dealer),  # type: ignore[arg-type]
        board=cards(board),  # type: ignore[arg-type]
        seats=tuple(cards(s) for s in seats),  # type: ignore[misc]
    )


#: Ana con trío de reyes y Leo con nada contra la pareja de nueves de la banca.
TWO = table_deal("Kh Ks", "7h 8s")


class FakeClock:
    """Reloj que solo avanza cuando el bucle "duerme"."""

    def __init__(self) -> None:
        self.now = START

    def wall(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRenderer:
    def __init__(self) -> None:
        self.dealt: list[list[Any]] = []
        self.called: list[list[bool]] = []

    async def board(self, **kwargs: Any) -> bytes:
        return b"PNG"

    async def deal(self, table: TableDeal, views: list[Any], **kwargs: Any) -> Media:
        self.dealt.append(views)
        return Media(gif=b"DEAL", png=b"DEALPNG", seconds=1.0)

    async def showdown(
        self, table: TableDeal, views: list[Any], called: list[bool], **kwargs: Any
    ) -> Media:
        self.called.append(list(called))
        return Media(gif=b"SHOW", png=b"SHOWPNG", seconds=1.0)

    async def close(self) -> None:
        pass


def make_user(user_id: int, name: str = "Ana") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    message = MagicMock()
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    channel.test_message = message
    return channel


def make_interaction(user_id: int, name: str = "Ana") -> MagicMock:
    interaction = fake_interaction(make_user(user_id, name))
    interaction.guild = None
    return interaction


async def make_cog(tmp_path: Path) -> tuple[Poker, FakeClock]:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    club = PokerRepository(tmp_path / "bot.db")
    await club.initialize()
    clock = FakeClock()
    cog = Poker(
        MagicMock(),
        economy=EconomyService(repository),
        club=club,
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        rng=random.Random(7),
        wall_clock=clock.wall,
        sleep=clock.sleep,
    )
    cog.start = MagicMock()  # type: ignore[method-assign]
    return cog, clock


async def open_table(cog: Poker, channel: MagicMock | None = None) -> Table:
    table, new = await cog.ensure_table(GUILD_ID, channel or make_channel(), ANA)
    assert table is not None and new
    return table


async def balance(cog: Poker, user_id: int = ANA) -> int:
    return await cog.economy.balance(GUILD_ID, user_id)


def ledger(tmp_path: Path, user_id: int = ANA) -> list[tuple[str, int]]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id",
            (GUILD_ID, user_id),
        ).fetchall()
    return [(r[0], r[1]) for r in rows if r[0] != "bienvenida"]


@pytest.fixture(autouse=True)
def fixed_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cog_module, "deal_table", lambda rng, n: TWO)


async def play(table: Table, *decisions: tuple[int, bool]) -> None:
    """Reparte, decide y acaba la mano."""
    await table.deal()
    for user_id, called in decisions:
        await table.decide(make_interaction(user_id), called)
    await table.finish()


# -- Abrir y sentarse ------------------------------------------------------------------


async def test_abrir_la_mesa_no_cobra_y_enseña_el_bote(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    assert await balance(cog) == STARTING_BALANCE
    assert table.pot == POT_SEED
    labels = [b.label for b in table.view.children if isinstance(b, discord.ui.Button)]
    assert labels[:6] == ["Sentarse", "¡Cartas!", "Ante", "Bote", "Club", "Tabla"]
    assert f"Bote común: {POT_SEED:,}".replace(",", ".") in (table.open_embed().description or "")


async def test_sentarse_cobra_el_ante_y_no_dos_veces(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    interaction = make_interaction(ANA)
    await table.sit_click(interaction)
    assert ledger(tmp_path) == [("poker:apuesta", -100)]
    assert table.deadline is not None and table.first_seat.is_set()
    again = make_interaction(ANA)
    await table.sit_click(again)
    assert "Ya estás sentado" in again.response.send_message.await_args.args[0]
    assert ledger(tmp_path) == [("poker:apuesta", -100)]


async def test_la_mesa_se_llena_con_seis(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    for i in range(MAX_SEATS):
        await table.sit(make_user(100 + i))
    with pytest.raises(cog_module.PokerError, match="llena"):
        await table.sit(make_user(999))


async def test_un_ante_por_encima_del_rango_no_deja_sentarse(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    cog.set_ante(GUILD_ID, ANA, poker_club.RANKS[0].max_ante + 1)  # type: ignore[operator]
    with pytest.raises(cog_module.PokerError, match="ante máximo"):
        await table.sit(make_user(ANA))
    assert ledger(tmp_path) == []


async def test_todos_listos_reparte_ya(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await table.sit(make_user(LEO, "Leo"))
    await table.mark_ready(make_interaction(ANA))
    assert not table.go.is_set()
    await table.mark_ready(make_interaction(LEO, "Leo"))
    assert table.go.is_set()


# -- Una mano --------------------------------------------------------------------------


async def test_una_mano_a_dos_paga_a_quien_va_y_gana_y_cobra_el_ante_al_que_se_retira(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await table.sit(make_user(LEO, "Leo"))
    await table.deal()
    assert table.phase is Phase.DECIDING
    assert "¿Vas o te retiras?" in (table.decide_embed().title or "")
    await table.decide(make_interaction(ANA), True)
    await table.decide(make_interaction(LEO, "Leo"), False)
    assert table.decided.is_set()
    await table.finish()
    assert cog.renderer.called == [[True, False]]  # type: ignore[attr-defined]
    # Trío contra la pareja de la banca: el ante y la de ir pagan 1 a 1.
    won = 100 + 100 * CALL_FACTOR
    assert await balance(cog) == STARTING_BALANCE + won - gambling_day_tax(won)
    assert await balance(cog, LEO) == STARTING_BALANCE - 100
    text = table.open_embed().description or ""
    assert "✅ **Ana**" in text and "🏳️ **Leo**" in text and "bien retirado" in text
    assert table.phase is Phase.OPEN and not table.seats
    ana = await cog.club.player(GUILD_ID, ANA)
    assert ana.xp == 2 and ana.streak == 1


async def test_quien_no_decide_a_tiempo_se_retira(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await table.deal()
    await table.finish()
    assert cog.renderer.called == [[False]]  # type: ignore[attr-defined]
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_si_no_llega_para_ir_solo_puede_retirarse(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.set_ante(GUILD_ID, ANA, STARTING_BALANCE // 2)
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await table.deal()
    interaction = make_interaction(ANA)
    await table.decide(interaction, True)
    seat = table.seats[ANA]
    assert seat.called is None
    assert "Solo puedes retirarte" in interaction.followup.send.await_args.args[0]


async def test_quien_no_esta_sentado_no_decide(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await table.deal()
    intruder = make_interaction(LEO, "Leo")
    await table.decide(intruder, True)
    assert "No estás en esta mano" in intruder.response.send_message.await_args.args[0]


# -- El 💎 Bote ------------------------------------------------------------------------


async def test_el_bote_se_recuerda_se_cobra_al_sentarse_y_alimenta_el_bote_comun(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await table.toggle_bonus(make_interaction(ANA))
    assert (await cog.club.player(GUILD_ID, ANA)).bonus
    await table.sit(make_user(ANA))
    assert ledger(tmp_path) == [("poker:apuesta", -(100 + BONUS_STAKE))]
    await play(table, (ANA, True))
    assert await cog.economy.poker_pot(GUILD_ID, seed=POT_SEED) == POT_SEED + BONUS_SHARE


async def test_una_escalera_real_con_bote_se_lleva_el_bote_y_se_anuncia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    royal = table_deal("Ah Kh", board="Qh Jh Th 3s 2c")
    monkeypatch.setattr(cog_module, "deal_table", lambda rng, n: royal)
    cog, _ = await make_cog(tmp_path)
    channel = make_channel()
    table = await open_table(cog, channel)
    await cog.club.set_bonus(GUILD_ID, ANA, True)
    await table.sit(make_user(ANA))
    await play(table, (ANA, True))
    seat = table.last_round.seats[0]  # type: ignore[union-attr]
    assert seat.pot_won == POT_SEED + BONUS_SHARE
    assert await cog.economy.poker_pot(GUILD_ID, seed=POT_SEED) == POT_SEED
    shouts = [c.args[0] for c in channel.send.await_args_list if c.args]
    assert any("del bote del póker" in s for s in shouts)


# -- El club ---------------------------------------------------------------------------


async def test_cumplir_un_reto_lo_paga_con_irpf_y_lo_dice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    easy = dataclasses.replace(poker_club.CHALLENGE_BY_KEY["manos_15"], goal=1)
    monkeypatch.setattr(cog_module, "daily_challenges", lambda *a: (easy,))
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await play(table, (ANA, False))
    assert ("poker:reto:manos_15", easy.reward) in ledger(tmp_path)
    text = table.open_embed().description or ""
    assert "cumple el reto «Juega 1 manos»" in text
    # El mismo reto no se vuelve a cobrar en el día.
    await table.sit(make_user(ANA))
    await play(table, (ANA, False))
    paid = [r for r in ledger(tmp_path) if r[0].startswith("poker:reto")]
    assert len(paid) == 1


async def test_subir_de_rango_se_anuncia(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    second = poker_club.RANKS[1]
    await cog.club.record_hand(
        GUILD_ID, ANA, day=cog_module.datetime.fromtimestamp(START, cog_module.TIMEZONE).date(),
        xp=second.xp - 1, counts=True, progress={}, goals={},
    )  # fmt: skip
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await play(table, (ANA, False))
    assert f"sube a {second.emoji} **{second.name}**" in (table.open_embed().description or "")


async def test_el_panel_del_club_enseña_rango_racha_y_retos(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    interaction = make_interaction(ANA)
    await table.club_panel(interaction)
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    text = embed.description or ""
    assert poker_club.RANKS[0].name in text
    assert "Retos de hoy" in text and "Racha" in text


# -- Cierre ----------------------------------------------------------------------------


async def test_al_apagar_con_las_apuestas_abiertas_se_devuelve_todo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await cog.club.set_bonus(GUILD_ID, ANA, True)
    await table.sit(make_user(ANA))
    await table.shutdown()
    assert await balance(cog) == STARTING_BALANCE


async def test_al_apagar_decidiendo_quien_no_decidio_se_retira_y_se_paga(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    table = await open_table(cog)
    await table.sit(make_user(ANA))
    await table.sit(make_user(LEO, "Leo"))
    await table.deal()
    await table.decide(make_interaction(ANA), True)
    await table.shutdown()
    won = 100 + 100 * CALL_FACTOR
    assert await balance(cog) == STARTING_BALANCE + won - gambling_day_tax(won)
    assert await balance(cog, LEO) == STARTING_BALANCE - 100
    assert table.phase is Phase.CLOSED


# -- Un solo comando, dos modos --------------------------------------------------------


def test_los_modos_se_reconocen_en_texto() -> None:
    assert cog_module.parse_mode("Timba") == cog_module.TIMBA
    assert cog_module.parse_mode("banca") == cog_module.BANCA
    assert cog_module.parse_mode("500") is None


async def test_sin_modo_pregunta_con_un_boton_por_modo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction(ANA)
    await cog.poker.callback(cog, interaction, None, "300")
    kwargs = interaction.response.send_message.await_args.kwargs
    picker = kwargs["view"]
    assert kwargs["ephemeral"] is True
    labels = [b.label for b in picker.children if isinstance(b, discord.ui.Button)]
    assert labels == ["Contra la banca (Casino Hold'em)", "Timba entre jugadores (Texas Hold'em)"]
    assert picker.amount == "300"


async def test_elegir_banca_en_la_pregunta_abre_la_mesa_y_te_sienta(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    channel = make_channel()
    picker = cog_module.ModePicker(cog, make_user(ANA), "200", channel=channel)
    click = make_interaction(ANA)
    click.guild = MagicMock(id=GUILD_ID)
    await picker.choose(cog_module.BANCA, click)
    table = cog.tables[channel.id]
    assert ANA in table.seats and table.seats[ANA].ante == 200
    assert "Sentado" in click.edit_original_response.await_args.kwargs["content"]


async def test_solo_quien_pregunto_elige_el_modo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    picker = cog_module.ModePicker(cog, make_user(ANA), None, channel=make_channel())
    intruder = make_interaction(LEO, "Leo")
    await picker.choose(cog_module.BANCA, intruder)
    assert "no es tuya" in intruder.response.send_message.await_args.args[0]
    assert not picker.done


async def test_la_timba_sin_su_cog_avisa(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog.play_mode(
        cog_module.TIMBA,
        guild=MagicMock(id=GUILD_ID),
        channel=make_channel(),
        user=make_user(ANA),
        amount_text=None,
        confirm=AsyncMock(),
        send_error=errors,
    )
    assert "no está disponible" in errors.await_args.args[0]
