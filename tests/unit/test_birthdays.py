"""Pruebas de los cumpleaños: reglas, persistencia y cog."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import birthdays as birthdays_cog
from bot.cogs.birthdays import Birthdays, GreetOutcome
from bot.repositories.birthdays import BirthdayRepository
from bot.repositories.economy import EconomyRepository
from bot.services.birthdays import (
    BIRTHDAY_GIFT,
    GREETED_BONUS,
    GREETER_REWARD,
    celebration_date,
    days_until,
    format_birthday,
    is_greeting,
    parse_birthday,
)
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.taxes import compute_withholding

GUILD = 1
TODAY = date(2026, 10, 4)

# -- Reglas ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("14/02", (14, 2)), ("4-10", (4, 10)), ("29/02", (29, 2)), ("01.12.1999", (1, 12))],
)
def test_parse_birthday_acepta_formatos_y_descarta_el_anio(
    text: str, expected: tuple[int, int]
) -> None:
    assert parse_birthday(text) == expected


@pytest.mark.parametrize("text", ["31/02", "00/05", "12/13", "mañana", "1402"])
def test_parse_birthday_rechaza_fechas_imposibles(text: str) -> None:
    with pytest.raises(ValueError):
        parse_birthday(text)


def test_29_de_febrero_se_celebra_el_28_si_no_es_bisiesto() -> None:
    assert celebration_date(29, 2, 2026) == date(2026, 2, 28)
    assert celebration_date(29, 2, 2028) == date(2028, 2, 29)


def test_dias_hasta_el_proximo() -> None:
    assert days_until(4, 10, TODAY) == 0
    assert days_until(5, 10, TODAY) == 1
    assert days_until(3, 10, TODAY) == 364


def test_formato_en_espanol() -> None:
    assert format_birthday(14, 2) == "14 de febrero"


@pytest.mark.parametrize(
    "text", ["Feliz cumple!!", "felicidades bro", "HBD", "🎂🎂", "happy birthday"]
)
def test_felicitaciones_reconocidas(text: str) -> None:
    assert is_greeting(text)


def test_mensaje_normal_no_es_felicitacion() -> None:
    assert not is_greeting("oye mañana jugamos?")


# -- Repositorio ------------------------------------------------------------------------


async def make_repo(tmp_path: Path) -> BirthdayRepository:
    repository = BirthdayRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def test_cumple_solo_se_pone_una_vez_salvo_con_overwrite(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)

    assert await repository.set_birthday(GUILD, 10, 4, 10, overwrite=False)
    assert not await repository.set_birthday(GUILD, 10, 5, 10, overwrite=False)
    assert (await repository.get_birthday(GUILD, 10)).day == 4  # type: ignore[union-attr]
    assert await repository.set_birthday(GUILD, 10, 5, 10, overwrite=True)
    assert (await repository.get_birthday(GUILD, 10)).day == 5  # type: ignore[union-attr]


async def test_celebracion_y_felicitacion_cuentan_una_vez(tmp_path: Path) -> None:
    repository = await make_repo(tmp_path)

    assert await repository.mark_celebrated(GUILD, 10, 2026)
    assert not await repository.mark_celebrated(GUILD, 10, 2026)
    assert await repository.mark_celebrated(GUILD, 10, 2027)
    assert await repository.add_greeting(GUILD, 10, 2026, 20)
    assert not await repository.add_greeting(GUILD, 10, 2026, 20)
    assert await repository.add_greeting(GUILD, 10, 2026, 21)


# -- Cog --------------------------------------------------------------------------------


async def make_cog(tmp_path: Path) -> tuple[Birthdays, BirthdayRepository, EconomyService]:
    repository = await make_repo(tmp_path)
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    economy = EconomyService(economy_repository)
    return Birthdays(MagicMock(), repository, economy), repository, economy


def user(user_id: int, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, bot=bot)


async def test_felicitar_paga_a_ambos_una_sola_vez(tmp_path: Path) -> None:
    cog, repository, economy = await make_cog(tmp_path)
    await repository.set_birthday(GUILD, 10, 4, 10, overwrite=False)
    guild = SimpleNamespace(id=GUILD)

    first = (await cog.greet(guild, 10, user(20), TODAY)).outcome  # type: ignore[arg-type]
    second = (await cog.greet(guild, 10, user(20), TODAY)).outcome  # type: ignore[arg-type]

    assert first is GreetOutcome.OK
    assert second is GreetOutcome.REPEATED
    assert await economy.balance(GUILD, 20) == STARTING_BALANCE + GREETER_REWARD
    assert await economy.balance(GUILD, 10) == STARTING_BALANCE + GREETED_BONUS


async def test_no_se_puede_felicitar_a_uno_mismo_ni_fuera_de_fecha(tmp_path: Path) -> None:
    cog, repository, economy = await make_cog(tmp_path)
    await repository.set_birthday(GUILD, 10, 5, 10, overwrite=False)
    guild = SimpleNamespace(id=GUILD)

    assert (await cog.greet(guild, 10, user(10), TODAY)).outcome is GreetOutcome.SELF  # type: ignore[arg-type]
    assert (await cog.greet(guild, 10, user(20), TODAY)).outcome is GreetOutcome.NOT_TODAY  # type: ignore[arg-type]
    assert await economy.balance(GUILD, 20) == STARTING_BALANCE


async def test_mensaje_que_menciona_al_cumpleanero_cuenta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, repository, economy = await make_cog(tmp_path)
    await repository.set_birthday(GUILD, 10, 4, 10, overwrite=False)
    monkeypatch.setattr(birthdays_cog, "local_day", lambda _now: TODAY)
    cog._today[GUILD] = (TODAY, frozenset({10}))
    message = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        author=user(20),
        mentions=[user(10)],
        reference=None,
        content="feliz cumple crack",
        add_reaction=AsyncMock(),
    )

    await cog.on_message(message)  # type: ignore[arg-type]

    message.add_reaction.assert_awaited_once_with("🎉")
    assert await economy.balance(GUILD, 20) == STARTING_BALANCE + GREETER_REWARD


async def test_mensaje_sin_felicitacion_no_cuenta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, repository, economy = await make_cog(tmp_path)
    await repository.set_birthday(GUILD, 10, 4, 10, overwrite=False)
    monkeypatch.setattr(birthdays_cog, "local_day", lambda _now: TODAY)
    cog._today[GUILD] = (TODAY, frozenset({10}))
    message = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        author=user(20),
        mentions=[user(10)],
        reference=None,
        content="pásame el link",
        add_reaction=AsyncMock(),
    )

    await cog.on_message(message)  # type: ignore[arg-type]

    message.add_reaction.assert_not_awaited()
    assert await economy.balance(GUILD, 20) == STARTING_BALANCE


async def test_el_cumpleanero_cobra_el_regalo_y_se_anuncia_una_sola_vez(
    tmp_path: Path,
) -> None:
    cog, repository, economy = await make_cog(tmp_path)
    await repository.set_birthday(GUILD, 10, 4, 10, overwrite=False)
    await repository.set_birthday(GUILD, 11, 5, 10, overwrite=False)
    member = MagicMock(spec=discord.Member)
    member.id = 10
    members = {10: member, 11: MagicMock(id=11)}
    guild = SimpleNamespace(id=GUILD, get_member=members.get, roles=[])
    cog._announce = AsyncMock()  # type: ignore[method-assign]

    await cog._check_guild(guild, TODAY)  # type: ignore[arg-type]
    await cog._check_guild(guild, TODAY)  # type: ignore[arg-type]

    cog._announce.assert_awaited_once()
    assert cog._announce.await_args.args[:3] == (guild, member, 2026)
    # Retención proyectando el regalo solo, con la ventana de 7 días de la Renta.
    gift = compute_withholding(BIRTHDAY_GIFT, recent_income=0)
    assert await economy.balance(GUILD, 10) == STARTING_BALANCE + gift.net
    assert await economy.balance(GUILD, 11) == STARTING_BALANCE
    assert cog._today[GUILD] == (TODAY, frozenset({10}))


class Recorder:
    """Responder mínimo que guarda lo enviado."""

    def __init__(self, member: SimpleNamespace) -> None:
        self.guild = SimpleNamespace(id=GUILD)
        self.member = member
        self.sent: list[str] = []
        self.errors: list[str] = []

    async def send(self, content: str | None = None, **_: object) -> None:
        self.sent.append(content or "")

    async def send_error(self, content: str) -> None:
        self.errors.append(content)


def member(user_id: int, *, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        display_name=f"m{user_id}",
        guild_permissions=SimpleNamespace(administrator=admin),
    )


async def test_cumple_no_deja_cambiarlo_a_un_miembro_pero_si_a_un_admin(tmp_path: Path) -> None:
    cog, repository, _ = await make_cog(tmp_path)
    normal = member(10)
    admin = member(99, admin=True)

    await cog._cumple_impl(Recorder(normal), "14/02", None)  # type: ignore[arg-type]
    second = Recorder(normal)
    await cog._cumple_impl(second, "15/02", None)  # type: ignore[arg-type]
    other = Recorder(normal)
    await cog._cumple_impl(other, "15/02", member(11))  # type: ignore[arg-type]
    await cog._cumple_impl(Recorder(admin), "15/02", normal)  # type: ignore[arg-type]

    assert "administrador" in second.errors[0]
    assert "administrador" in other.errors[0]
    assert (await repository.get_birthday(GUILD, 10)).day == 15  # type: ignore[union-attr]


async def test_felicitar_por_encima_del_minimo_retiene_irpf_para_el_estado(
    tmp_path: Path,
) -> None:
    """El regalo lo pone el bot: es ganancia patrimonial (art. 33.1 LIRPF), no donación."""
    cog, repository, economy = await make_cog(tmp_path)
    await repository.set_birthday(GUILD, 10, 4, 10, overwrite=False)
    await economy.pay_income(GUILD, 20, gross=20_000, concept="nivel:20")
    collected = (await economy.treasury(GUILD, since=0)).collected_total
    guild = SimpleNamespace(id=GUILD)

    greeting = await cog.greet(guild, 10, user(20), TODAY)  # type: ignore[arg-type]

    assert greeting.income is not None and greeting.income.tax > 0
    after = await economy.treasury(GUILD, since=0)
    assert after.collected_total - collected == greeting.income.tax
