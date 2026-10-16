"""Pruebas de bot.cogs.hold_win: las máquinas de Botes con botones y su dinero.

Se usa la economía y el repositorio reales sobre un SQLite temporal (para
comprobar que el dinero y los maletines se mueven de verdad), tiradas trucadas
y un dibujo falso que apunta lo que se le pide. Pintar antes del clic va apagado
(`ahead=False`) salvo en las pruebas que lo miran: cada sorteo de más gastaría las
tiradas de guion (`rig`).
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.cogs.hold_win as module
from bot.cogs.hold_win import (
    GIF_NAME,
    PNG_NAME,
    BasePlay,
    HoldWin,
    HoldWinView,
    base_panel,
    base_text,
    bonus_text,
    parse_stake,
    paytable_embed,
    reveal_delay,
)
from bot.repositories.economy import BalanceLimitError, EconomyRepository
from bot.repositories.hold_win import HoldWinRepository
from bot.services.economy import (
    STARTING_BALANCE,
    STATE_ACCOUNT_ID,
    EconomyService,
    format_amount,
)
from bot.services.hold_win import (
    CASE_SIZE,
    CELLS,
    MAJOR_BASE,
    MIN_STAKE,
    MINI_BASE,
    MINI_CHIP,
    MINI_MAX,
    ROWS,
    THEMES,
    BonusGame,
    BonusKind,
    BonusStep,
    Cell,
    Kind,
    Landing,
    Meters,
    Tier,
    Trigger,
    evaluate_base,
)
from bot.services.hold_win_render import Media

GUILD_ID = 1
OWNER_ID = 10
VOLCAN = THEMES["volcan"]


def coin(value: int, tier: Tier = Tier.GREEN) -> Cell:
    return Cell(Kind.COIN, tier=tier, value=value)


def grid(cells: dict[tuple[int, int], Cell]) -> list[Cell]:
    filler = Cell(Kind.CHIP, symbol="mini")
    return [cells.get(divmod(i, ROWS), filler) for i in range(CELLS)]


#: Recogedor en el rodillo 1 y monedas por ×0,5 + ×2: paga ×2,5 la apuesta.
COLLECT = evaluate_base(
    grid({(0, 0): Cell(Kind.COLLECT), (2, 1): coin(50), (3, 2): coin(200, Tier.RED)})
)
#: Nada de nada: ni monedas ni ways (solo fichas de bote).
NOTHING = evaluate_base(grid({}))
#: Una moneda azul sin recoger.
BLUE_COIN = evaluate_base(grid({(1, 1): coin(80, Tier.BLUE)}))


class FakeRenderer:
    """Devuelve bytes fijos y apunta lo que se le pide (`HoldWinScene` sin dibujar).

    Si se le da una lista en `events`, apunta en ella cuándo se pinta la entrada al bonus.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.events: list[str] | None = None
        self.closed = False

    async def base(self, theme, spin, *, stake, before, after, banner=None, turbo=False):  # noqa: ANN001, ANN201
        call = {"spin": spin, "stake": stake, "before": before, "after": after, "turbo": turbo}
        self.calls.append(("base", call))
        return Media(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    async def still(self, theme, spin, *, stake, panel, highlight=False):  # noqa: ANN001, ANN201
        call = {"spin": spin, "stake": stake, "panel": panel, "highlight": highlight}
        self.calls.append(("still", call))
        return b"STILL"

    async def bonus_intro(self, theme, spin, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self.calls.append(("bonus_intro", {"spin": spin, **kwargs}))
        if self.events is not None:
            self.events.append("bonus_intro")
        turbo = kwargs.get("turbo", False)
        return Media(gif=b"" if turbo else b"INTRO", png=b"BONUS", seconds=0.0)

    async def bonus_step(self, theme, before, step, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self.calls.append(("bonus_step", {"before": before, "step": step, **kwargs}))
        return Media(gif=b"" if kwargs.get("turbo") else b"GIF", png=b"PNG", seconds=0.0)

    async def close(self) -> None:
        self.closed = True


class NeverLands(random.Random):
    """Azar en el que nunca cae nada en el bonus y no hay gran bonus por sorpresa."""

    def random(self) -> float:
        return 0.999

    def randrange(self, start, stop=None, step=1):  # noqa: ANN001, ANN201
        return start - 1 if stop is None else start


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las animaciones y pausas no hace falta esperarlas."""
    monkeypatch.setattr(module, "REVEAL_MARGIN_SECONDS", 0)
    monkeypatch.setattr(module, "BONUS_AUTO_PAUSE", 0)
    monkeypatch.setattr(module, "BONUS_INTRO_PAUSE", 0)


def rig(monkeypatch: pytest.MonkeyPatch, *spins) -> None:  # noqa: ANN002
    """Las siguientes tiradas salen en este orden; después, siempre `NOTHING`."""
    queue = list(spins)
    monkeypatch.setattr(module, "spin_base", lambda _rng: queue.pop(0) if queue else NOTHING)


async def make_cog(tmp_path: Path, rng: random.Random | None = None) -> HoldWin:
    database = tmp_path / "bot.db"
    economy_repository = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    repository = HoldWinRepository(database)
    await repository.initialize()
    return HoldWin(
        MagicMock(),
        economy=EconomyService(economy_repository),
        repository=repository,
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        rng=rng or NeverLands(),
    )


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


def make_view(cog: HoldWin, stake: int = 100) -> HoldWinView:
    return HoldWinView(cog, theme=VOLCAN, guild_id=GUILD_ID, owner=make_user(), stake=stake)


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


def names(call) -> list[str]:  # noqa: ANN001
    return [file.filename for file in call.kwargs["attachments"]]


def renderer_calls(cog: HoldWin, name: str, since: int = 0) -> list[dict]:
    """Lo que se le pidió al dibujo con ese nombre (desde la llamada `since`)."""
    return [kw for call, kw in cog.renderer.calls[since:] if call == name]


# -- Presentación -------------------------------------------------------------------


def test_apuesta_por_defecto_minimo_y_formatos() -> None:
    assert parse_stake(None, 1_000) == 100
    assert parse_stake(None, 40) == 40
    assert parse_stake("2k", 5_000) == 2_000
    with pytest.raises(ValueError, match="mínima"):
        parse_stake(str(MIN_STAKE - 1), 1_000)


def test_la_tabla_explica_ways_monedas_maletines_y_botes() -> None:
    text = paytable_embed(VOLCAN).description or ""
    for word in ("Ways", "Monedas", "Maletines", "Bonus", "GRAND", "🌋"):
        assert word in text


def test_el_texto_de_la_recogida_dice_cuanto_y_con_que() -> None:
    play = BasePlay(
        spin=COLLECT,
        stake=100,
        payout=250,
        settlement=MagicMock(balance=1_150),
        meters=Meters(),
        before=base_panel(Meters(), 100),
        trigger=None,
        bonus=None,
        media=Media(b"", b"", 0.0),
        session_spins=1,
    )
    text = base_text(VOLCAN, play, random.Random(0))
    assert "+150 Y$" in text.splitlines()[0]
    assert "2 monedas: +250 Y$" in text


def test_el_texto_del_bonus_cuenta_lo_que_falta_para_el_mini() -> None:
    game = BonusGame(kind=BonusKind.RED, stake=100, mini=MINI_BASE, major=MAJOR_BASE)
    game.board[0] = coin(200, Tier.RED)
    text = bonus_text(VOLCAN, game, None)
    assert "Bonus Lava" in text
    assert "9 para el MINI" in text


def base_play(spin, *, gif: bool) -> BasePlay:  # noqa: ANN001
    return BasePlay(
        spin=spin,
        stake=100,
        payout=0,
        settlement=MagicMock(balance=1_000),
        meters=Meters(),
        before=base_panel(Meters(), 100),
        trigger=None,
        bonus=None,
        media=Media(b"GIF" if gif else b"", b"PNG", 1.0 if gif else 0.0),
        session_spins=1,
    )


def find_spin(predicate):  # noqa: ANN001, ANN201
    rng = random.Random(3)
    while True:
        spin = module.spin_base(rng)
        if predicate(spin):
            return spin


def test_el_rescate_y_el_recogedor_que_pasa_de_largo_solo_se_cuentan_si_se_ven() -> None:
    rescue = find_spin(
        lambda s: module.tease_of(s).kind == "rescue" and module.tease_hit(s, module.tease_of(s))
    )
    text = base_text(VOLCAN, base_play(rescue, gif=True), random.Random(0))
    assert any(line in text for line in module.RESCUE_LINES)
    turbo = base_text(VOLCAN, base_play(rescue, gif=False), random.Random(0))
    assert not any(line in turbo for line in module.RESCUE_LINES)
    slip = find_spin(lambda s: module.shows_slip("volcan", s))
    text = base_text(VOLCAN, base_play(slip, gif=True), random.Random(0))
    assert any(line in text for line in module.SLIP_LINES)


def test_el_bonus_dice_si_se_salva_en_la_ultima_y_si_cae_la_moneda_que_faltaba() -> None:
    game = BonusGame(kind=BonusKind.RED, stake=100, mini=MINI_BASE, major=MAJOR_BASE)
    for index in range(10):
        game.board[index] = coin(200, Tier.RED)
    step = BonusStep(
        landings=(Landing(9, coin(200, Tier.RED)),),
        multiplier_added=0,
        instant=10,
        extra=0,
        maximized=False,
        board=tuple(game.board),
        respins_left=3,
    )
    text = bonus_text(VOLCAN, game, step, left_before=1, rng=random.Random(0))
    assert any(line in text for line in module.LAST_SAVE_LINES)
    assert "moneda que faltaba para el MINI" in text
    calm = bonus_text(VOLCAN, game, step, left_before=3)
    assert not any(line in calm for line in module.LAST_SAVE_LINES)


# -- Juego base ---------------------------------------------------------------------


async def test_tirar_cobra_gira_y_paga_la_recogida(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    interaction = make_interaction()

    await view._spin(interaction)

    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance == STARTING_BALANCE - 100 + 250
    assert ledger_sum(tmp_path, OWNER_ID) == balance
    interaction.response.defer.assert_awaited_once()
    first, final = interaction.edit_original_response.await_args_list
    assert names(first) == [GIF_NAME]
    assert names(final) == [PNG_NAME]


async def test_las_monedas_entran_en_el_maletin_y_se_guardan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    await make_view(cog, stake=40)._spin(make_interaction())

    meters = await cog.meters(GUILD_ID, OWNER_ID, "volcan")
    assert meters.cases[Tier.GREEN].coins == 1
    assert meters.cases[Tier.RED].coins == 1
    assert meters.cases[Tier.RED].stake_sum == 40
    # Las fichas de relleno de la rejilla también suben el MINI.
    assert meters.mini == MINI_BASE + MINI_CHIP * COLLECT.chips["mini"]
    # Cada máquina guarda lo suyo.
    assert (await cog.meters(GUILD_ID, OWNER_ID, "otra")).cases[Tier.RED].coins == 0


async def test_en_turbo_solo_se_edita_una_vez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch)
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.turbo = True
    interaction = make_interaction()

    await view._spin(interaction)

    assert names(interaction.edit_original_response.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_awaited_once()


async def test_sin_saldo_no_gira_ni_toca_los_maletines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=STARTING_BALANCE + 1)
    interaction = make_interaction()

    await view._spin(interaction)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).cases[Tier.RED].coins == 0


async def test_solo_juega_el_dueno(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = make_interaction(user_id=99)
    assert not await make_view(cog).interaction_check(interaction)
    assert "volcan" in interaction.response.send_message.await_args.args[0]


async def test_auto_juega_diez_y_un_solo_resumen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch)
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    interaction = make_interaction()

    await view._auto(interaction)

    assert view.session_spins == module.AUTO_SPINS
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    assert "10 tiradas" in interaction.edit_original_response.await_args.kwargs["embed"].description


# -- Bonus --------------------------------------------------------------------------


async def fill_blue(cog: HoldWin) -> None:
    meters = Meters()
    meters.cases[Tier.BLUE].add(CASE_SIZE[Tier.BLUE] - 1, 100)
    await cog.repository.save(GUILD_ID, OWNER_ID, "volcan", meters)


async def test_llenar_un_maletin_abre_el_bonus_sin_cobrar_nada_mas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, BLUE_COIN)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    view = make_view(cog)
    interaction = make_interaction()

    await view._spin(interaction)

    assert view.bonus is not None
    assert view.bonus.kind == BonusKind.BLUE
    assert view.bonus.stake == 100
    assert view.spin_button.label.startswith("🎰 Girar")
    assert view.half_button.disabled
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).cases[Tier.BLUE].coins == 0
    # La tirada que llena el maletín y, después, la entrada al bonus: las dos con su
    # animación y su imagen final.
    edits = interaction.edit_original_response.await_args_list
    assert [names(edit) for edit in edits] == [[GIF_NAME], [PNG_NAME], [GIF_NAME], [PNG_NAME]]
    assert [edit.kwargs["attachments"][0].fp.read() for edit in edits] == [
        b"GIF",
        b"PNG",
        b"INTRO",
        b"BONUS",
    ]


async def test_el_bonus_se_paga_al_acabar_y_cuadra_con_hacienda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, BLUE_COIN)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    view = make_view(cog)
    await view._spin(make_interaction())
    assert view.bonus is not None
    expected = view.bonus.coin_points * 100 // 100

    for _ in range(3):
        await view._spin(make_interaction())

    assert view.bonus is None
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    assert balance + state == STARTING_BALANCE - 100 + expected
    assert ledger_sum(tmp_path, OWNER_ID) == balance
    assert view.spin_button.label.startswith("🎰 Tirar")


async def test_auto_del_bonus_lo_juega_entero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    coins = view.bonus.coin_points

    await view._auto(make_interaction())

    assert view.bonus is None
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance + ledger_sum(tmp_path, STATE_ACCOUNT_ID) == STARTING_BALANCE + coins


async def test_ganar_un_bote_lo_devuelve_a_su_base(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.repository.save(GUILD_ID, OWNER_ID, "volcan", Meters(mini=MINI_MAX))
    game = BonusGame(kind=BonusKind.GREEN, stake=100, mini=MINI_MAX, major=MAJOR_BASE)
    game.board = [coin(10) for _ in range(10)] + [None] * 10
    game.respins_left = 0

    payout = await cog.pay_bonus(GUILD_ID, OWNER_ID, VOLCAN, game)

    assert payout.result.jackpots == ("mini",)
    assert payout.amount == 100 + MINI_MAX
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).mini == MINI_BASE


async def test_cerrar_la_maquina_con_bonus_a_medias_lo_juega_y_lo_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.RED, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    coins = view.bonus.coin_points
    view.message = MagicMock()
    view.message.edit = AsyncMock()

    await view.on_timeout()

    assert view.bonus is None
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance + ledger_sum(tmp_path, STATE_ACCOUNT_ID) == STARTING_BALANCE + coins
    assert "cerrar" in view.message.edit.await_args.kwargs["embed"].description


async def test_al_apagar_se_pagan_los_bonus_pendientes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    cog.machines.add(view)

    await cog.cog_unload()

    assert view.bonus is None
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) > STARTING_BALANCE - 1
    assert not cog.machines
    # Node y el navegador de la escena se cierran con el cog.
    assert cog.renderer.closed


async def test_salir_del_servidor_borra_los_maletines(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    await cog.on_guild_remove(guild)
    assert (await cog.meters(GUILD_ID, OWNER_ID, "volcan")).cases[Tier.BLUE].coins == 0


# -- Comandos -----------------------------------------------------------------------


async def test_abrir_la_maquina_manda_imagen_y_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    ctx = MagicMock()
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.author = make_user()
    ctx.channel = MagicMock(id=5)
    ctx.send = AsyncMock(return_value=MagicMock())

    await cog._open(
        THEMES["volcan"],
        guild=ctx.guild,
        channel=ctx.channel,
        user=ctx.author,
        amount_text=None,
        send=ctx.send,
        send_error=ctx.reply,
    )

    kwargs = ctx.send.await_args.kwargs
    assert kwargs["file"].filename == PNG_NAME
    assert isinstance(kwargs["view"], HoldWinView)
    assert kwargs["view"].theme.key == "volcan"
    assert len(cog.machines) == 1


async def test_fuera_del_canal_de_casino_no_se_abre(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({555})
    ctx = MagicMock()
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.author = make_user()
    ctx.channel = MagicMock(id=5)
    ctx.send = AsyncMock()
    ctx.reply = AsyncMock()

    await cog._open(
        THEMES["volcan"],
        guild=ctx.guild,
        channel=ctx.channel,
        user=ctx.author,
        amount_text=None,
        send=ctx.send,
        send_error=ctx.reply,
    )

    assert not cog.machines


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_cada_maquina_tiene_sus_frases(theme: str) -> None:
    from bot.cogs.hold_win import BONUS_INTRO, COLLECT_LINES, THEME_LOSS_LINES

    assert len(COLLECT_LINES[theme]) >= 4
    assert BONUS_INTRO[theme]
    assert len(THEME_LOSS_LINES[theme]) >= 2


def test_el_nombre_de_cada_maquina_lleva_su_articulo() -> None:
    assert THEMES["volcan"].named == "el Volcán"
    assert THEMES["mina"].named == "la Mina"
    assert THEMES["olimpo"].of_named == "del Olimpo"
    assert THEMES["mina"].of_named == "de la Mina"
    assert THEMES["mina"].sentence_named == "La Mina"


# -- Pintar antes del clic ----------------------------------------------------------


async def test_la_proxima_tirada_se_sortea_y_se_pinta_antes_del_clic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog)
    view.prepare()
    await view._painting
    # Primero Tirar a la apuesta de ahora; después ½, ×2 y All-in (la imagen depende de la
    # apuesta: las monedas y los botes llevan su valor en Y$).
    painted = renderer_calls(cog, "base")
    assert all(call["spin"] is COLLECT for call in painted)
    assert [call["stake"] for call in painted] == [100, 50, 200, STARTING_BALANCE]
    count = len(cog.renderer.calls)
    interaction = make_interaction()

    await view._spin(interaction)
    await view._painting

    # Se juega la tirada sorteada y su GIF no se vuelve a pintar.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100 + 250
    first = interaction.edit_original_response.await_args_list[0]
    assert first.kwargs["attachments"][0].fp.read() == b"GIF"
    assert not [c for c in renderer_calls(cog, "base", count) if c["spin"] is COLLECT]


async def test_tirar_en_turbo_usa_la_imagen_quieta_pintada_antes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog)
    view.turbo = True
    view.prepare()
    await view._painting
    assert {call["turbo"] for call in renderer_calls(cog, "base")} == {True}
    count = len(cog.renderer.calls)
    interaction = make_interaction()

    await view._spin(interaction)

    interaction.edit_original_response.assert_awaited_once()
    assert not [c for c in renderer_calls(cog, "base", count) if c["spin"] is COLLECT]


async def test_cambiar_la_apuesta_o_el_turbo_vuelve_a_planear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog)
    view.prepare()
    await view._painting
    count = len(cog.renderer.calls)

    await view._halve(make_interaction())
    await view._painting

    # La de 50 ya estaba pintada (era la de ½): solo falta el nuevo ½, el de 25.
    assert [call["stake"] for call in renderer_calls(cog, "base", count)] == [25]
    count = len(cog.renderer.calls)

    await view._toggle_turbo(make_interaction())
    await view._painting

    # En turbo la imagen es otra (solo el PNG): se pintan de nuevo todas las apuestas.
    again = renderer_calls(cog, "base", count)
    assert {call["turbo"] for call in again} == {True}
    assert [call["stake"] for call in again] == [50, 25, 100, STARTING_BALANCE]
    count = len(cog.renderer.calls)

    await view._spin(make_interaction())
    await view._painting

    # Tirar a 50 en turbo usa lo pintado.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 50 + 125
    assert not [c for c in renderer_calls(cog, "base", count) if c["spin"] is COLLECT]


async def test_si_los_maletines_cambian_antes_del_clic_se_pinta_en_el_momento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog)
    view.prepare()
    await view._painting
    # Otra máquina del mismo jugador movió los maletines: lo pintado ya no es lo que se verá.
    await fill_blue(cog)
    count = len(cog.renderer.calls)

    await view._spin(make_interaction())

    (call,) = [c for c in renderer_calls(cog, "base", count) if c["spin"] is COLLECT]
    assert call["before"].cases[Tier.BLUE.value][0] == CASE_SIZE[Tier.BLUE] - 1


@pytest.mark.parametrize("error", ["saldo", "banca"])
async def test_un_error_de_saldo_no_deja_el_pintor_congelado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    rig(monkeypatch, COLLECT)
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog, stake=STARTING_BALANCE + 1 if error == "saldo" else 100)
    if error == "banca":
        monkeypatch.setattr(cog.economy, "settle_bet", AsyncMock(side_effect=BalanceLimitError))
    view.prepare()
    await view._painting
    interaction = make_interaction()

    await view._spin(interaction)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    # La tirada sorteada se gastó, pero el pintor sigue: vuelve a sortear y a pintar.
    assert not view._changing
    await view._painting
    assert view._upcoming is NOTHING


async def test_un_auto_sin_saldo_no_deja_el_pintor_congelado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch)
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog, stake=STARTING_BALANCE + 1)
    view.prepare()
    await view._painting
    drawn = view._auto_next
    assert drawn is not None

    await view._auto(make_interaction())

    # Se gastaron las diez, pero el pintor sigue: vuelve a sortear otras diez y a pintar.
    assert not view._changing
    await view._painting
    assert view._auto_next is not None and view._auto_next is not drawn


async def test_el_auto_juega_sus_diez_tiradas_y_su_imagen_pintadas_antes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # La primera es la de Tirar; las diez siguientes, las del Auto (que acaba en la recogida).
    rig(monkeypatch, *[NOTHING] * 10, COLLECT)
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog, stake=10)
    view.prepare()
    await view._painting
    (still,) = renderer_calls(cog, "still")
    assert still["spin"] is COLLECT and still["highlight"] and still["stake"] == 10
    count = len(cog.renderer.calls)
    interaction = make_interaction()

    await view._auto(interaction)
    await view._painting

    assert view.session_spins == module.AUTO_SPINS
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100 + 25
    last = interaction.edit_original_response.await_args
    assert last.kwargs["attachments"][0].fp.read() == b"STILL"
    # Su imagen no se vuelve a pintar; después solo se pinta el Auto siguiente.
    assert not [c for c in renderer_calls(cog, "still", count) if c["spin"] is COLLECT]


async def test_el_auto_pintado_antes_para_en_el_bonus_como_el_de_verdad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tirar, y el Auto: una moneda azul que llena el maletín (y para) y lo que ya no se juega.
    rig(monkeypatch, NOTHING, BLUE_COIN, COLLECT)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    cog.ahead = True
    view = make_view(cog, stake=10)
    view.prepare()
    await view._painting
    (still,) = renderer_calls(cog, "still")
    assert still["spin"] is BLUE_COIN
    assert still["panel"].cases[Tier.BLUE.value][0] == 0
    count = len(cog.renderer.calls)

    await view._auto(make_interaction())
    await view._painting

    assert view.session_spins == 1 and view.bonus is not None
    assert not [c for c in renderer_calls(cog, "still", count) if c["spin"] is BLUE_COIN]


async def test_girar_del_bonus_juega_la_tirada_pintada_antes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, random.Random(7))
    cog.ahead = True
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False),
        mini=MINI_BASE,
        major=MAJOR_BASE,
        rng=random.Random(1),
    )
    original = view.bonus
    view.prepare()
    await view._painting
    (painted,) = renderer_calls(cog, "bonus_step")
    planned = view._bonus_next
    assert planned is not None and painted["step"] is planned.step
    # La tirada pintada se juega sobre una copia: el bonus de verdad sigue como estaba.
    assert original.spins == 0 and planned.game is not original
    count = len(cog.renderer.calls)

    await view._spin(make_interaction())
    await view._painting

    # La copia ya jugada pasa a ser el bonus: lo pintado y lo jugado son lo mismo.
    assert view.bonus is planned.game
    assert view.bonus.history == [planned.step] and view.bonus.spins == 1
    assert not [c for c in renderer_calls(cog, "bonus_step", count) if c["step"] is planned.step]


async def test_la_ultima_tirada_del_bonus_se_pinta_con_su_recuento_y_se_paga(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    view.bonus.respins_left = 1
    coins = view.bonus.coin_points
    view.prepare()
    await view._painting
    (painted,) = renderer_calls(cog, "bonus_step")
    # Nada cae y es la última: el bonus acaba y la imagen lleva lo que paga.
    assert painted["finish"].amount == coins
    assert painted["banner"].amount == f"+{format_amount(coins)}"
    count = len(cog.renderer.calls)

    await view._spin(make_interaction())
    await view._painting

    assert view.bonus is None
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance + ledger_sum(tmp_path, STATE_ACCOUNT_ID) == STARTING_BALANCE + coins
    assert not [c for c in renderer_calls(cog, "bonus_step", count) if c["finish"] is not None]
    # Con el bonus pagado, lo siguiente que se pinta es la tirada base.
    assert renderer_calls(cog, "base", count)


async def test_el_bonus_pintado_no_vale_si_el_bonus_ya_es_otro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.ahead = True
    view = make_view(cog)
    view.bonus = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )
    view.prepare()
    await view._painting
    stale = view._bonus_next
    assert stale is not None
    # Otro bonus (o uno que ha avanzado por su cuenta) no puede adoptar la copia.
    view.bonus = BonusGame.start(
        Trigger(BonusKind.RED, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=NeverLands()
    )

    assert view._take_bonus() is None
    view.bonus = stale.source
    stale.source.spins += 1
    view._bonus_next = stale
    assert view._take_bonus() is None


async def test_la_entrada_al_bonus_se_pinta_mientras_se_ve_la_tirada_y_es_una_animacion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, BLUE_COIN)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    events: list[str] = []
    cog.renderer.events = events
    view = make_view(cog)
    interaction = fake_interaction(make_user(), events=events)

    await view._spin(interaction)

    # Empieza a pintarse con el GIF de la tirada en pantalla, no después de enseñar su PNG.
    assert events == [
        "response.defer",
        "edit_original_response",
        "bonus_intro",
        "edit_original_response",
        "edit_original_response",
        "edit_original_response",
    ]
    (call,) = renderer_calls(cog, "bonus_intro")
    assert call["spin"] is BLUE_COIN and call["game"] is view.bonus
    assert call["base_stake"] == 100 and call["turbo"] is False
    # Los maletines de antes y de después de la tirada, para que destellen y se vacíen.
    blue, size = Tier.BLUE.value, CASE_SIZE[Tier.BLUE]
    assert call["before"].cases[blue] == (size - 1, size)
    assert call["after"].cases[blue] == (0, size)
    assert call["name"] == "Bonus " + VOLCAN.bonus_names[BonusKind.BLUE]
    assert call["banner"].title == call["name"]
    assert view.last_text is not None and "Bonus" in view.last_text
    assert view.spin_button.label.startswith("🎰 Girar") and not view.spin_button.disabled


async def test_la_entrada_al_bonus_en_turbo_es_solo_la_imagen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, BLUE_COIN)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    view = make_view(cog)
    view.turbo = True
    interaction = make_interaction()

    await view._spin(interaction)

    edits = interaction.edit_original_response.await_args_list
    assert [names(edit) for edit in edits] == [[PNG_NAME], [PNG_NAME]]
    assert renderer_calls(cog, "bonus_intro")[0]["turbo"] is True


async def test_un_auto_que_acaba_en_bonus_ensena_su_entrada_animada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, BLUE_COIN)
    cog = await make_cog(tmp_path)
    await fill_blue(cog)
    view = make_view(cog, stake=10)
    interaction = make_interaction()

    await view._auto(interaction)

    assert view.session_spins == 1 and view.bonus is not None
    edits = interaction.edit_original_response.await_args_list
    assert [names(edit) for edit in edits] == [[PNG_NAME], [GIF_NAME], [PNG_NAME]]
    (call,) = renderer_calls(cog, "bonus_intro")
    assert call["before"].cases[Tier.BLUE.value][0] == CASE_SIZE[Tier.BLUE] - 1
    assert call["base_stake"] == 10 and call["game"] is view.bonus


async def test_abrir_la_maquina_empieza_a_pintar_antes_del_clic(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.ahead = True
    send = AsyncMock(return_value=MagicMock())

    await cog._open(
        VOLCAN,
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=5),
        user=make_user(),
        amount_text=None,
        send=send,
        send_error=AsyncMock(),
    )

    (view,) = cog.machines
    await view._painting
    assert [call["stake"] for call in renderer_calls(cog, "base")][0] == 100


async def test_sin_pintar_por_adelantado_no_se_sortea_nada_de_mas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig(monkeypatch, COLLECT, BLUE_COIN)
    cog = await make_cog(tmp_path)
    assert not cog.ahead
    view = make_view(cog)
    view.prepare()
    assert view._painting is None and view._upcoming is None

    await view._spin(make_interaction())

    # Se sortea al jugar, no antes: la primera de las dos de guion es la que sale.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100 + 250
    assert view._painting is None and view._auto_next is None


async def test_al_cargar_se_prepara_lo_fijo_de_cada_maquina(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(module, "meta_state", seen.append)
    cog = await make_cog(tmp_path)

    await cog.cog_load()
    await cog._warm_task

    assert sorted(seen) == sorted(THEMES)


def test_un_gif_pesado_espera_un_poco_mas_antes_del_png() -> None:
    light = Media(gif=b"x" * 1024, png=b"", seconds=2.0)
    heavy = Media(gif=b"x" * 2**21, png=b"", seconds=2.0)
    assert reveal_delay(heavy) == pytest.approx(
        reveal_delay(light) + 2 * module.REVEAL_SECONDS_PER_MB, abs=0.01
    )
