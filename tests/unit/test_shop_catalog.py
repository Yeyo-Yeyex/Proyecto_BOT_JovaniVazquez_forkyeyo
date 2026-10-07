"""Pruebas del surtido de serie de la tienda y de los objetos que se usan.

Comprueba que el surtido cabe en los límites de la tienda y de Discord, que
cada uso arma sus frases sin huecos, que la caja botín pierde dinero (como
debe) sin ser una estafa total, y los flujos del cog: sembrar el catálogo,
filtrar por pasillo y usar objetos desde la mochila contra alguien.
"""

from __future__ import annotations

import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui

from bot.cogs.shop import TABS, Backpack, Storefront, TargetPicker, Tienda, UseTextForm, in_tab
from bot.cogs.shop_admin import AdminPanel
from bot.repositories.economy import EconomyRepository
from bot.repositories.shop import ShopRepository
from bot.services.achievements import (
    SHOP_AISLES_STAT,
    SHOP_USE_KINDS_STAT,
    shop_hit_stats,
    shop_stats,
    shop_use_stats,
    with_derived,
)
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.pets_catalog import SPECIES, species_of
from bot.services.shop import (
    MAX_BOOST,
    MAX_DESCRIPTION,
    MAX_LEVEL,
    MAX_NAME,
    MAX_PRICE,
    MAX_SPAN,
    MAX_STOCK,
    MIN_BOOST,
    MIN_SPAN,
    Kind,
)
from bot.services.shop_catalog import (
    AISLES,
    CATALOG,
    CATALOG_BY_KEY,
    MIN_AISLE_SIZE,
    RETIRED_AISLES,
    Aisle,
    aisle_of,
    use_of,
)
from bot.services.shop_uses import (
    MYSTERY_JACKPOT,
    USES,
    Target,
    clean_shout,
    mystery_expected,
    mystery_weight,
    resolve,
)
from bot.services.taxes import IGIC_BY_KEY

GUILD = 1
BUYER = 10
OTHER = 11
NOW = datetime(2026, 10, 6, 18, tzinfo=TIMEZONE).timestamp()


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# -- El surtido -------------------------------------------------------------------------


def test_surtido_cabe_en_los_limites_de_la_tienda() -> None:
    assert len(CATALOG) >= 120
    assert len(CATALOG_BY_KEY) == len(CATALOG), "claves repetidas"
    for entry in CATALOG:
        assert re.fullmatch(r"[a-z0-9_]+", entry.key), entry.key
        assert 0 < len(entry.name) <= MAX_NAME, entry.key
        assert 0 < len(entry.description) <= MAX_DESCRIPTION, entry.key
        assert 1 <= entry.price <= MAX_PRICE, entry.key
        assert entry.igic in IGIC_BY_KEY, entry.key
        assert 0 <= entry.min_level <= MAX_LEVEL, entry.key
        assert entry.stock is None or 1 <= entry.stock <= MAX_STOCK, entry.key
        assert entry.kind is not Kind.ROLE, "los roles dependen del servidor"
        if entry.kind is Kind.PET:
            species = species_of(entry.key)
            assert species is not None, entry.key
            assert entry.per_user == 1, "una de cada especie por persona"
            assert entry.visible is species.adoptable, entry.key
        else:
            assert entry.visible, "solo las mascotas que aparecen solas van ocultas"
        if entry.food:
            assert entry.kind is Kind.TROPHY, entry.key
        if entry.kind is Kind.BOOST:
            assert entry.multiplier is not None and MIN_BOOST <= entry.multiplier <= MAX_BOOST
            assert entry.duration is not None and MIN_SPAN <= entry.duration <= MAX_SPAN
            assert entry.use is None
        else:
            assert entry.multiplier is None and entry.duration is None
        if entry.use is not None:
            assert entry.use in USES, entry.key
            assert entry.stock is None, "un gastable numerado se quedaría sin número al usarlo"


def test_cada_uso_tiene_su_articulo_y_cada_pasillo_su_surtido() -> None:
    used = {entry.use for entry in CATALOG if entry.use}
    assert used == set(USES)
    # Un desplegable de Discord admite 25 opciones: «Todos» + pasillos + «De la casa».
    assert len(AISLES) + 2 <= 25


@pytest.mark.parametrize("aisle", AISLES, ids=lambda a: a.key)
def test_ficha_de_alta_de_cada_pasillo(aisle: Aisle) -> None:
    """Las reglas de la Biblia para los pasillos: tema, tamaño y algo que usar."""
    assert aisle.theme, "cada pasillo dice de qué va"
    on_sale = [e for e in CATALOG if e.aisle == aisle.key and e.visible]
    assert len(on_sale) >= MIN_AISLE_SIZE, f"{aisle.key}: si no llega, va dentro de otro"
    assert any(e.use for e in on_sale), f"{aisle.key}: necesita al menos un objeto que se use"


def test_pasillos_retirados_no_vuelven_y_heredan_en_uno_que_existe() -> None:
    keys = {a.key for a in AISLES}
    assert all(e.aisle in keys for e in CATALOG), "cada artículo en un pasillo que existe"
    assert not set(RETIRED_AISLES) & keys, "una clave retirada no se reutiliza"
    assert all(heir is None or heir in keys for heir in RETIRED_AISLES.values())


def test_las_mascotas_comen_lo_que_vende_el_colmado() -> None:
    for species in SPECIES:
        for key in species.favourites:
            entry = CATALOG_BY_KEY[key]
            assert entry.food or species.diet == "todo", (species.key, key)


@pytest.mark.parametrize(
    ("key", "igic"),
    [
        ("manual_resistencia", "cero"),  # libro (art. 52 de la Ley 4/2012)
        ("barra_pan", "cero"),
        ("huevo", "cero"),
        ("saco_gofio", "cero"),  # harina
        ("lingote", "cero"),  # oro de inversión, exento
        ("anillo_diamantes", "lujo"),  # joyería
        ("yate", "lujo"),
        ("seat_ibiza", "incrementado"),  # coche de menos de 11 CV
        ("paella", "general"),
    ],
)
def test_cada_articulo_paga_el_igic_que_le_toca(key: str, igic: str) -> None:
    assert CATALOG_BY_KEY[key].igic == igic


def test_lujo_y_patrimonio() -> None:
    """Lo caro de verdad pide nivel: no se lo lleva el primero que llega con suerte."""
    for entry in CATALOG:
        if entry.price >= 100_000:
            assert entry.min_level >= 10, entry.key


def test_lo_retirado_no_tiene_pasillo_y_va_a_la_casa() -> None:
    assert aisle_of(None).key == "casa"
    assert aisle_of("no_existe").key == "casa"
    assert aisle_of("huevo").key == "espana"
    assert aisle_of("mascota_canario").key == "canarias"
    assert use_of("huevo") is USES["huevo"]
    assert use_of("falcon") is None


# -- Usos -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    sorted(k for k, u in USES.items() if u.special not in {"mystery", "megaphone", "nickname"}),
)
def test_cada_uso_arma_sus_frases_sin_huecos(key: str) -> None:
    use = USES[key]
    for seed in range(40):
        for target, me, bot in (
            (None, False, False),
            ("<@11>", False, False),
            ("<@10>", True, False),
            ("<@99>", False, True),
        ):
            if use.target is not Target.MEMBER and target is not None:
                continue
            if use.target is Target.MEMBER and target is None:
                continue
            result = resolve(
                use, who="**Diego**", item="🥚 Huevo", rng=random.Random(seed),
                now=NOW + seed * 3600, target=target, target_is_self=me, target_is_bot=bot,
            )  # fmt: skip
            assert "{" not in result.text and "}" not in result.text, result.text
            assert 0 < len(result.text) <= 600
            if use.target is Target.MEMBER and not me and not bot and "carta" != key:
                assert "**Diego**" in result.text or "<@11>" in result.text


def test_un_uso_contra_alguien_le_menciona() -> None:
    hits = [
        resolve(USES["huevo"], who="**D**", item="🥚", rng=random.Random(s), now=NOW,
                target="<@11>")
        for s in range(30)
    ]  # fmt: skip
    assert all("<@11>" in r.text for r in hits)
    assert {"hit", "miss", "backfire"} <= set().union(*(r.flags for r in hits))


def test_contra_uno_mismo_o_el_bot_tiene_su_frase() -> None:
    me = resolve(USES["tupper"], who="**D**", item="🍲", rng=random.Random(1), now=NOW,
                 target="<@10>", target_is_self=True)  # fmt: skip
    assert me.flags == {"self"} and "a sí mismo" in me.text
    bot = resolve(USES["huevo"], who="**D**", item="🥚", rng=random.Random(1), now=NOW,
                  target="<@99>", target_is_bot=True)  # fmt: skip
    assert bot.flags == {"bot"} and "Jovani" in bot.text


class Rigged(random.Random):
    """Azar que devuelve lo que se le diga en `randint` y `random`."""

    def __init__(self, roll: int = 10, chance: float = 0.5) -> None:
        super().__init__(0)
        self.roll = roll
        self.chance = chance

    def randint(self, a: int, b: int) -> int:
        return self.roll

    def random(self) -> float:
        return self.chance


def test_d20_critico_y_pifia() -> None:
    assert resolve(USES["d20"], who="D", item="🎲", rng=Rigged(20), now=NOW).flags == {"nat20"}
    assert resolve(USES["d20"], who="D", item="🎲", rng=Rigged(1), now=NOW).flags == {"nat1"}
    assert "**12**" in resolve(USES["d20"], who="D", item="🎲", rng=Rigged(12), now=NOW).text


def test_pimientos_de_padron_unos_pican_y_otros_no() -> None:
    assert resolve(USES["padron"], who="D", item="", rng=Rigged(chance=0.1), now=NOW).flags == {
        "hot"
    }
    assert not resolve(USES["padron"], who="D", item="", rng=Rigged(chance=0.9), now=NOW).flags


def test_robuso_contesta_segun_la_hora_de_hong_kong() -> None:
    # 18:00 en Canarias (UTC+1 en octubre) son las 01:00 en Hong Kong (UTC+8).
    asleep = resolve(USES["robuso"], who="D", item="", rng=random.Random(), now=NOW)
    assert asleep.flags == {"asleep"} and "**01:00**" in asleep.text
    morning = NOW + 7 * 3600  # 08:00 en Hong Kong
    assert "desayunando" in resolve(USES["robuso"], who="D", item="", rng=random.Random(),
                                    now=morning).text  # fmt: skip


def test_encuesta_del_cis_suma_cien() -> None:
    for seed in range(20):
        text = resolve(USES["cis"], who="D", item="", rng=random.Random(seed), now=NOW).text
        numbers = [float(n.replace(",", ".")) for n in re.findall(r"(\d+,\d) %", text)]
        assert len(numbers) == 3 and abs(sum(numbers) - 100) < 0.05


def test_megafono_limpia_el_grito() -> None:
    assert clean_shout("  hola\n\n  mundo  ") == "hola mundo"
    assert len(clean_shout("a" * 500)) == 150
    with pytest.raises(ValueError):
        clean_shout("   ")


def test_caja_botin_pierde_dinero_pero_no_es_una_estafa_total() -> None:
    box = CATALOG_BY_KEY["caja_botin"]
    prizes = [
        e.price for e in CATALOG if e.kind is Kind.TROPHY and e.use is None and e.stock is None
    ]
    expected = mystery_expected(prizes)
    assert 0.5 * box.price < expected < box.price
    weights = [mystery_weight(p) for p in prizes]
    jackpot = sum(w for p, w in zip(prizes, weights, strict=True) if p >= MYSTERY_JACKPOT)
    assert 0.001 < jackpot / sum(weights) < 0.01


# -- Logros -----------------------------------------------------------------------------


def test_estadisticas_de_usar_y_recibir() -> None:
    halloween = datetime(2026, 10, 31, 4, tzinfo=TIMEZONE).timestamp()
    delta = shop_use_stats(
        use="huevo", flags={"hit"}, consumed=True, targeted=True, at_self=False,
        at_bot=False, prize_price=None, collection=0, when=halloween,
    )  # fmt: skip
    for stat in ("shop_uses", "shop_used_huevo", "shop_consumed", "shop_use_targeted",
                 "shop_use_hits", "shop_use_night", "shop_use_halloween"):  # fmt: skip
        assert delta.add[stat] == 1, stat
    box = shop_use_stats(
        use="caja", flags={"jackpot", "dupe"}, consumed=True, targeted=False, at_self=False,
        at_bot=False, prize_price=180_000, collection=12, when=NOW,
    )  # fmt: skip
    assert box.add["shop_mystery_jackpot"] == 1 and box.add["shop_mystery_dupe"] == 1
    assert box.peak == {"shop_mystery_best": 180_000, "shop_collection_max": 12}
    assert shop_hit_stats(use="huevo").add == {"shop_got_hit": 1, "shop_got_messy": 1}
    assert shop_hit_stats(use="uco").add == {"shop_got_hit": 1, "shop_got_raided": 1}


def test_compras_del_surtido_cuentan_pasillo_y_articulo_famoso() -> None:
    delta = shop_stats(
        kind="objeto", total=10, tax=0, discount_pct=0, luxury=False, serial=None,
        last_unit=False, renewed=False, collection=1, queued_boosts=0, balance_after=5,
        catalog_key="piedra", aisle="shitpost",
    )  # fmt: skip
    assert delta.add["shop_aisle_shitpost"] == 1 and delta.add["shop_key_piedra"] == 1
    house = shop_stats(
        kind="objeto", total=10, tax=0, discount_pct=0, luxury=False, serial=None,
        last_unit=False, renewed=False, collection=1, queued_boosts=0, balance_after=5,
        catalog_key=None, aisle="casa",
    )  # fmt: skip
    assert not any(k.startswith(("shop_aisle_", "shop_key_")) for k in house.add)


def test_derivados_cuentan_usos_y_pasillos_distintos() -> None:
    full = with_derived(
        {"shop_used_huevo": 3, "shop_used_d20": 1, "shop_used_inventado": 9,
         "shop_aisle_moncloa": 1, "shop_aisle_casa": 4, "shop_aisle_lujo": 0}
    )  # fmt: skip
    assert full[SHOP_USE_KINDS_STAT] == 2
    assert full[SHOP_AISLES_STAT] == 1


def test_lo_comprado_en_un_pasillo_retirado_cuenta_para_su_heredero() -> None:
    full = with_derived(
        {"shop_aisle_ultramarinos": 2, "shop_aisle_espana": 1, "shop_aisle_farmacia": 5}
    )
    assert full[SHOP_AISLES_STAT] == 1


# -- Repositorio ------------------------------------------------------------------------


async def make_stores(tmp_path: Path, clock: Clock) -> tuple[EconomyService, ShopRepository]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE * 1_000)
    await economy_repo.initialize()
    shop = ShopRepository(tmp_path / "bot.db")
    await shop.initialize()
    return EconomyService(economy_repo, clock=clock), shop


async def test_siembra_una_vez_y_respeta_lo_retirado(tmp_path: Path) -> None:
    _economy, shop = await make_stores(tmp_path, Clock())
    some = CATALOG[:6]
    assert await shop.stock_catalog(GUILD, some, NOW) == 6
    assert await shop.stock_catalog(GUILD, some, NOW) == 0
    items = await shop.items(GUILD)
    assert {i.catalog_key for i in items} == {e.key for e in some}

    gone, hidden = items[0], items[1]
    await shop.delete_item(GUILD, gone.id)
    await shop.update_item(GUILD, hidden.id, visible=False)
    assert await shop.stock_catalog(GUILD, some, NOW) == 0, "lo retirado no vuelve solo"
    assert await shop.stock_catalog(GUILD, CATALOG[:7], NOW) == 1, "lo nuevo sí entra"
    assert await shop.stock_catalog(GUILD, some, NOW, restock=True) == 1, "reponer"
    every = await shop.items(GUILD, include_hidden=True)
    assert len(every) == 7 and len({i.catalog_key for i in every}) == 7
    assert await shop.stock_catalog(2, some, NOW) == 6, "cada servidor tiene su surtido"


async def test_siembra_copia_limites_y_potenciadores(tmp_path: Path) -> None:
    _economy, shop = await make_stores(tmp_path, Clock())
    picks = [CATALOG_BY_KEY[k] for k in ("escano", "oposicion", "falcon")]
    await shop.stock_catalog(GUILD, picks, NOW)
    by_key = {i.catalog_key: i for i in await shop.items(GUILD)}
    assert (by_key["escano"].stock, by_key["escano"].per_user) == (350, 1)
    assert by_key["oposicion"].kind is Kind.BOOST and by_key["oposicion"].multiplier == 150
    assert by_key["falcon"].min_level == 50 and by_key["falcon"].igic_key == "lujo"


async def test_base_de_datos_antigua_gana_la_columna(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('rol', 'xp', 'objeto')),
                name TEXT NOT NULL, emoji TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL, igic TEXT NOT NULL, role_id INTEGER, duration INTEGER,
                multiplier INTEGER, stock INTEGER, sold INTEGER NOT NULL DEFAULT 0,
                per_user INTEGER, min_level INTEGER NOT NULL DEFAULT 0,
                discount INTEGER NOT NULL DEFAULT 0, discount_until REAL,
                visible INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
            );
            INSERT INTO shop_items (guild_id, kind, name, emoji, price, igic, created_at)
            VALUES (1, 'objeto', 'Yate viejo', '🛥️', 10, 'general', 0);
            """
        )
    shop = ShopRepository(path)
    await shop.initialize()
    await shop.initialize()  # idempotente
    (old,) = await shop.items(GUILD)
    assert old.catalog_key is None and aisle_of(old.catalog_key).key == "casa"


async def test_gastar_es_una_sola_vez_y_se_puede_devolver(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    await shop.stock_catalog(GUILD, [CATALOG_BY_KEY["huevo"]], NOW)
    (egg,) = await shop.items(GUILD)
    await buy(economy, shop, egg.id)
    (entry,) = await shop.inventory(GUILD, BUYER, NOW)
    assert entry.catalog_key == "huevo"
    assert not await shop.consume(GUILD, OTHER, entry.id), "solo su dueño"
    assert await shop.consume(GUILD, BUYER, entry.id)
    assert not await shop.consume(GUILD, BUYER, entry.id), "doble clic"
    assert await shop.inventory(GUILD, BUYER, NOW) == []
    await shop.restore(entry.id)
    assert len(await shop.inventory(GUILD, BUYER, NOW)) == 1


async def test_regalo_de_la_caja_no_gasta_existencias(tmp_path: Path) -> None:
    _economy, shop = await make_stores(tmp_path, Clock())
    await shop.stock_catalog(GUILD, [CATALOG_BY_KEY["piedra"]], NOW)
    (stone,) = await shop.items(GUILD)
    _entry, dupe = await shop.grant(GUILD, BUYER, stone, NOW)
    assert not dupe
    _entry, dupe = await shop.grant(GUILD, BUYER, stone, NOW)
    assert dupe
    after = await shop.item(GUILD, stone.id)
    assert after is not None and after.sold == 0
    assert await shop.collection_size(GUILD, BUYER) == 1


async def buy(economy: EconomyService, shop: ShopRepository, item_id: int) -> None:
    from bot.services.shop import quote

    current = await shop.item(GUILD, item_id)
    assert current is not None
    price = quote(current, NOW)
    await economy.purchase(
        GUILD, BUYER, base=price.base, tax=price.tax, concept=current.kind.value,
        reserve=shop.reserve(GUILD, BUYER, item_id, expected=price, level=99, now=NOW),
    )  # fmt: skip


# -- Cog --------------------------------------------------------------------------------


def make_guild() -> MagicMock:
    guild = MagicMock()
    guild.id = GUILD
    guild.owner_id = 1
    guild.me.guild_permissions.manage_roles = True
    guild.me.guild_permissions.manage_nicknames = True
    guild.get_role = lambda role_id: None
    return guild


def make_member(guild: MagicMock, user_id: int = BUYER, *, bot: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = f"Miembro{user_id}"
    member.mention = f"<@{user_id}>"
    member.bot = bot
    member.guild = guild
    member.roles = []
    member.edit = AsyncMock()
    member.guild_permissions.administrator = True
    return member


def make_interaction(member: MagicMock) -> MagicMock:
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = member.guild
    interaction.channel = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


async def make_cog(tmp_path: Path, clock: Clock, catalog: tuple = CATALOG) -> Tienda:
    economy, shop = await make_stores(tmp_path, clock)
    levels = MagicMock()
    levels.member_xp = AsyncMock(return_value=10**9)
    bot = MagicMock()
    bot.get_cog.return_value = None  # sin logros ni Renta en estas pruebas
    return Tienda(bot, economy, shop, levels=levels, clock=clock, catalog=catalog,
                  rng=random.Random(7))  # fmt: skip


def texts(view: ui.LayoutView) -> str:
    return "\n".join(c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay))


async def test_escaparate_se_llena_solo_y_cabe_en_cada_pestana_y_pasillo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    view = await cog.storefront(guild, make_member(guild))
    assert len(view.items_all) == sum(1 for e in CATALOG if e.visible)
    for tab, _label in TABS:
        for aisle in (None, *(a.key for a in AISLES)):
            view.tab, view.aisle, view.page = tab, aisle, 0
            view.rebuild()
            assert view.total_children_count <= 40, (tab, aisle)
            assert len(texts(view)) <= 4000, (tab, aisle)
            assert all(in_tab(i, tab) for i in view.shown)
            if aisle:
                assert all(aisle_of(i.catalog_key).key == aisle for i in view.shown)
    view.tab, view.aisle = "uso", None
    assert {use_of(i.catalog_key) is not None for i in view.shown} == {True}
    view.tab = "mascota"
    assert view.shown and {i.kind for i in view.shown} == {Kind.PET}


async def test_pasillo_desde_el_desplegable(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    owner = make_member(guild)
    view = await cog.storefront(guild, owner)
    select = next(c for c in view.walk_children() if isinstance(c, ui.Select))
    select._values = ["hongkong"]  # type: ignore[attr-defined]
    click = make_interaction(owner)
    await select.callback(click)
    assert view.aisle == "hongkong" and view.shown
    assert "Hong Kong" in texts(view)
    stranger = make_interaction(make_member(guild, OTHER))
    select = next(c for c in view.walk_children() if isinstance(c, ui.Select))
    select._values = ["lujo"]  # type: ignore[attr-defined]
    await select.callback(stranger)
    own = stranger.response.send_message.await_args.kwargs["view"]
    assert isinstance(own, Storefront) and own.aisle == "lujo" and view.aisle == "hongkong"


async def give(cog: Tienda, key: str, *, times: int = 1) -> int:
    """Compra `times` veces un artículo del surtido y devuelve su id."""
    item = next(i for i in await cog.repository.items(GUILD) if i.catalog_key == key)
    for _ in range(times):
        await buy(cog.economy, cog.repository, item.id)
    return item.id


async def backpack_for(cog: Tienda, guild: MagicMock, member: MagicMock) -> Backpack:
    view = await cog.backpack_view(guild, member, member)
    view.interaction = make_interaction(member)
    return view


async def test_usar_un_gastable_lo_publica_y_lo_gasta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    await cog.stock_up(GUILD)
    cookie = await give(cog, "galleta_suerte", times=2)
    backpack = await backpack_for(cog, guild, member)
    assert "Para usar" in texts(backpack) and "×2" in texts(backpack)
    use_select = next(
        c for c in backpack.walk_children()
        if isinstance(c, ui.Select) and c.placeholder == "🫳 Usar algo"
    )  # fmt: skip
    assert use_select.options[0].value == str(cookie)

    click = make_interaction(member)
    await cog.start_use(click, backpack, cookie)
    public = click.response.send_message.await_args
    assert "galleta de la suerte" in public.args[0]
    assert "ephemeral" not in public.kwargs
    assert len(await cog.repository.inventory(GUILD, BUYER, NOW)) == 1
    assert "×2" not in texts(backpack), "la mochila se repinta"


async def test_usar_contra_alguien_abre_el_desplegable_y_le_menciona(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member, victim = make_member(guild), make_member(guild, OTHER)
    await cog.stock_up(GUILD)
    egg = await give(cog, "huevo")
    backpack = await backpack_for(cog, guild, member)

    click = make_interaction(member)
    await cog.start_use(click, backpack, egg)
    picker = click.response.send_message.await_args.kwargs["view"]
    assert isinstance(picker, TargetPicker)
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True
    assert await cog.repository.inventory(GUILD, BUYER, NOW), "elegir no gasta"

    picker.select._values = [victim]  # type: ignore[attr-defined]
    pick = make_interaction(member)
    await picker._picked(pick)
    sent = pick.response.send_message.await_args
    assert "<@11>" in sent.args[0] or "**Miembro10**" in sent.args[0]
    assert sent.kwargs["allowed_mentions"].users == [victim]
    assert await cog.repository.inventory(GUILD, BUYER, NOW) == []
    await picker._picked(make_interaction(member))  # doble clic: no pasa nada


async def test_contra_el_bot_no_menciona_a_nadie(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    await cog.stock_up(GUILD)
    await give(cog, "tomate")
    backpack = await backpack_for(cog, guild, member)
    (entry,) = backpack.entries
    click = make_interaction(member)
    await cog.perform_use(click, backpack, entry, USES["tomate"],
                          target=make_member(guild, 99, bot=True))  # fmt: skip
    sent = click.response.send_message.await_args
    assert "Jovani" in sent.args[0]
    assert sent.kwargs["allowed_mentions"].users is False


async def test_lo_que_no_se_gasta_tiene_espera(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    guild = make_guild()
    member = make_member(guild)
    await cog.stock_up(GUILD)
    horn = await give(cog, "vuvuzela")
    backpack = await backpack_for(cog, guild, member)
    first = make_interaction(member)
    await cog.start_use(first, backpack, horn)
    assert "vuvuzela" in first.response.send_message.await_args.args[0]
    again = make_interaction(member)
    await cog.start_use(again, backpack, horn)
    assert "Espera" in again.response.send_message.await_args.args[0]
    clock.now += USES["vuvuzela"].cooldown
    later = make_interaction(member)
    await cog.start_use(later, backpack, horn)
    assert "vuvuzela" in later.response.send_message.await_args.args[0]
    assert len(await cog.repository.inventory(GUILD, BUYER, clock.now)) == 1


async def test_caja_botin_da_un_coleccionable_ilimitado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    await cog.stock_up(GUILD)
    box = await give(cog, "caja_botin")
    backpack = await backpack_for(cog, guild, member)
    click = make_interaction(member)
    await cog.start_use(click, backpack, box)
    assert "caja botín" in click.response.send_message.await_args.args[0]
    (prize,) = await cog.repository.inventory(GUILD, BUYER, NOW)
    item = await cog.repository.item(GUILD, prize.item_id)
    assert item is not None and item.stock is None and use_of(item.catalog_key) is None
    assert item.sold == 0


async def test_caja_botin_sin_premios_se_devuelve(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(CATALOG_BY_KEY["caja_botin"],))
    guild = make_guild()
    member = make_member(guild)
    await cog.stock_up(GUILD)
    box = await give(cog, "caja_botin")
    backpack = await backpack_for(cog, guild, member)
    click = make_interaction(member)
    await cog.start_use(click, backpack, box)
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True
    assert len(await cog.repository.inventory(GUILD, BUYER, NOW)) == 1


async def test_megafono_pide_texto_y_no_menciona(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    await cog.stock_up(GUILD)
    megaphone = await give(cog, "megafono")
    backpack = await backpack_for(cog, guild, member)
    click = make_interaction(member)
    await cog.start_use(click, backpack, megaphone)
    form = click.response.send_modal.await_args.args[0]
    assert isinstance(form, UseTextForm)
    form.text._value = "@everyone **VIVA EL GOFIO**"
    submit = make_interaction(member)
    await form.on_submit(submit)
    sent = submit.response.send_message.await_args
    assert "VIVA EL GOFIO" in sent.args[0] and "@everyone" not in sent.args[0].replace(
        "@​everyone", ""
    )
    assert sent.kwargs["allowed_mentions"].everyone is False


async def test_dni_falso_sin_permiso_no_se_gasta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    guild.me.guild_permissions.manage_nicknames = False
    member = make_member(guild)
    await cog.stock_up(GUILD)
    await give(cog, "dni_falso")
    backpack = await backpack_for(cog, guild, member)
    (entry,) = backpack.entries
    click = make_interaction(member)
    await cog.perform_use(click, backpack, entry, USES["dni_falso"], text="Perro Sanxe")
    assert "Gestionar apodos" in click.response.send_message.await_args.args[0]
    assert len(await cog.repository.inventory(GUILD, BUYER, NOW)) == 1


async def test_dni_falso_cambia_el_apodo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    guild.me.top_role = 50
    member = make_member(guild)
    member.top_role = 10
    await cog.stock_up(GUILD)
    await give(cog, "dni_falso")
    backpack = await backpack_for(cog, guild, member)
    (entry,) = backpack.entries
    click = make_interaction(member)
    await cog.perform_use(click, backpack, entry, USES["dni_falso"], text="  El  Fontanero ")
    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["nick"] == "El Fontanero"
    assert "**El Fontanero**" in click.response.send_message.await_args.args[0]
    assert await cog.repository.inventory(GUILD, BUYER, NOW) == []


async def test_reponer_surtido_desde_la_trastienda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    admin = make_member(guild)
    panel = AdminPanel(cog, guild, admin)
    await cog.stock_up(GUILD)
    await panel.load()
    assert panel.total_children_count <= 40
    first = panel.items_all[0]
    await cog.repository.delete_item(GUILD, first.id)
    click = make_interaction(admin)
    await panel._restock(click)
    assert "Repuestos 1" in (panel.notice or "")
    assert len(panel.items_all) == len(CATALOG)
