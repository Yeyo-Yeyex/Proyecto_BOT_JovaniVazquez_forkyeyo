"""Pruebas del escaparate compacto, la ficha, el carrito y la lista de deseos de la tienda.

Economía y tienda reales sobre un SQLite temporal, como `test_shop.py` (de donde salen los
dobles de Discord). Lo que no se puede ver sin Discord (que el mensaje privado llegue) se
comprueba con el `send` del miembro de prueba.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import ui
from test_shop import (
    BUYER,
    GUILD,
    NOW,
    OTHER,
    Clock,
    FakeRole,
    item,
    make_cog,
    make_guild,
    make_interaction,
    make_member,
    texts,
)

from bot.cogs.shop import Tienda, compact_line
from bot.cogs.shop_front import (
    PAGE_SIZE,
    WISH_TAB,
    CartPanel,
    ItemSheet,
    Storefront,
    _stock_note,
    add_to_cart,
    open_sheet,
)
from bot.repositories.economy import InsufficientFundsError
from bot.repositories.rastro import Listing
from bot.services.achievements import shop_cart_stats
from bot.services.economy import STARTING_BALANCE
from bot.services.levels import TIMEZONE
from bot.services.shop import (
    MAX_CART_LINES,
    MAX_QUANTITY,
    Kind,
    ShopError,
    ShopItem,
    TicketLine,
    cart_preview,
    cart_receipt,
    max_quantity,
    quote,
    receipt,
)
from bot.services.shop_wishlist import (
    MAX_WISHES,
    news_message,
    stored_seen,
    wish_news,
    wish_state,
)

DAY = 86400


async def trophy(cog: Tienda, name: str = "Yate", **fields: object) -> ShopItem:
    """Un coleccionable de la casa; `fields` se cambian después (nivel, rebaja…)."""
    base: dict[str, object] = {
        "kind": Kind.TROPHY,
        "name": name,
        "emoji": "🛥️",
        "description": "",
        "price": 500,
        "igic": "general",
        "now": NOW - 10 * DAY,
    }
    for key in ("igic", "price", "stock", "description", "emoji"):
        if key in fields:
            base[key] = fields.pop(key)
    created = await cog.repository.create_item(GUILD, **base)  # type: ignore[arg-type]
    if fields:
        created = await cog.repository.update_item(GUILD, created.id, **fields)
    return created


def line(it: ShopItem, qty: int = 1) -> TicketLine:
    return TicketLine(it, qty, quote(it, NOW))


def purchases(cog: Tienda) -> int:
    connection = cog.repository._connect()
    try:
        (count,) = connection.execute("SELECT COUNT(*) FROM shop_purchases").fetchone()
    finally:
        connection.close()
    return int(count)


# -- Reglas: cantidad y factura ---------------------------------------------------------


def test_cantidad_maxima_respeta_limite_existencias_y_lo_que_se_tiene_una_vez() -> None:
    assert max_quantity(item(), 0) == MAX_QUANTITY
    assert max_quantity(item(per_user=3), 1) == 2
    assert max_quantity(item(per_user=1), 1) == 0
    assert max_quantity(item(stock=10, sold=8), 0) == 2
    assert max_quantity(item(kind=Kind.ROLE, role_id=5), 0) == 1, "un rol para siempre, una vez"
    assert max_quantity(item(kind=Kind.ROLE, role_id=5, duration=DAY), 0) == MAX_QUANTITY
    assert max_quantity(item(kind=Kind.PET), 0) == 1


def test_factura_de_un_tipo_es_la_de_siempre() -> None:
    single = receipt(item(), quote(item(), NOW), invoice=7, when=NOW, buyer="Diego")
    assert "1 × Yate de Perro Sanxe" in single
    assert "Base imponible" in single and "IGIC 7 %" in single
    assert "s/" not in single, "con un solo tipo no hace falta desglose"


def test_factura_de_un_carrito_desglosa_cada_tipo_y_suma_bien() -> None:
    general = item(id=1, price=1_000)
    zero = item(id=2, name="Gofio", price=300, igic_key="cero")
    luxury = item(id=3, name="Reloj", price=2_000, igic_key="lujo", discount=10)
    lines = [line(general, 2), line(zero), line(luxury)]
    text = cart_receipt(lines, invoices=[11, 12, 13, 14], when=NOW, buyer="Diego")
    assert "Nº T2026-000011 a 000014" in text
    assert "2 × Yate de Perro Sanxe" in text and "2.000" in text
    assert "IGIC 0 % s/ 300" in text
    assert "IGIC 7 % s/ 2.000" in text and "140" in text
    assert "IGIC 15 % s/ 1.800" in text and "270" in text
    assert "Rebaja -10 %" in text
    total = sum(entry.total for entry in lines)
    assert total == 2_000 + 140 + 300 + 1_800 + 270
    assert f"{total:,}".replace(",", ".") + " Y$" in text
    assert all(len(row) <= 30 for row in text.strip("`\n").splitlines())


def test_factura_enseña_los_numeros_de_serie_de_cada_unidad() -> None:
    limited = item(stock=10)
    text = cart_receipt(
        [line(limited, 3)],
        invoices=[1, 2, 3],
        when=NOW,
        buyer="Diego",
        serials={limited.id: [4, 5, 6]},
        editions={limited.id: 10},
    )
    assert "Unidades nº 4, 5, 6 de 10" in text


def test_ticket_previo_de_varias_unidades() -> None:
    text = cart_preview([line(item(), 3)])
    assert "3 × Yate" in text and "3.000" in text and "TOTAL" in text and "3.210 Y$" in text


# -- Carrito: todo o nada ---------------------------------------------------------------


async def test_un_carrito_se_cobra_entero_con_series_seguidas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    limited = await trophy(cog, "Patata", stock=5, price=100)
    gofio = await trophy(cog, "Gofio", igic="cero", price=300)
    guild = make_guild()
    member = make_member(guild)

    bought, balance = await cog.complete_cart(guild, member, [line(limited, 3), line(gofio)])

    assert [len(b.sales) for b in bought] == [3, 1]
    assert [s.serial for s in bought[0].sales] == [1, 2, 3]
    assert balance == STARTING_BALANCE - (107 * 3 + 300)
    assert await cog.economy.balance(GUILD, BUYER) == balance
    assert purchases(cog) == 4
    assert (await cog.repository.item(GUILD, limited.id)).sold == 3  # type: ignore[union-attr]


async def test_si_una_linea_falla_no_se_cobra_ninguna(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    fine = await trophy(cog, "Gofio", price=300)
    once = await trophy(cog, "Corona", per_user=1)
    guild = make_guild()
    member = make_member(guild)

    try:
        await cog.complete_cart(guild, member, [line(fine, 2), line(once, 2)])
    except ShopError as error:
        assert "Corona" in str(error), "el error dice qué línea falla"
    else:
        raise AssertionError("la segunda unidad de la corona se pasa del límite")
    assert await cog.economy.balance(GUILD, BUYER) == STARTING_BALANCE
    assert purchases(cog) == 0
    assert (await cog.repository.item(GUILD, fine.id)).sold == 0  # type: ignore[union-attr]


async def test_sin_saldo_para_el_total_no_se_cobra_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    pricey = await trophy(cog, "Yate", price=STARTING_BALANCE // 2)
    guild = make_guild()
    member = make_member(guild)
    try:
        await cog.complete_cart(guild, member, [line(pricey, 2)])
    except InsufficientFundsError:
        pass
    else:
        raise AssertionError("dos yates con IGIC pasan del saldo inicial")
    assert await cog.economy.balance(GUILD, BUYER) == STARTING_BALANCE
    assert purchases(cog) == 0


async def test_un_rol_que_discord_no_deja_dar_se_devuelve_solo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    role = FakeRole(555, 10)
    guild = make_guild(role)
    vip = await cog.repository.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=role.id,
    )  # fmt: skip
    gofio = await trophy(cog, "Gofio", price=300)
    member = make_member(guild)
    member.add_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "no"))

    bought, balance = await cog.complete_cart(guild, member, [line(vip), line(gofio)])

    assert bought[0].refund is not None and bought[1].refund is None
    assert balance == STARTING_BALANCE - 321, "el gofio se queda; el rol se devuelve entero"
    text = await cog.cart_paid_text(bought, balance, member, NOW)
    assert "Te he devuelto los 107 Y$" in text and "Gofio" in text


async def test_pagar_quita_lo_comprado_de_la_lista_y_apunta_un_solo_logro_de_saldo_cero(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    other = await trophy(cog, "Queso", price=200)
    guild = make_guild()
    member = make_member(guild)
    for it in (gofio, other):
        await cog.toggle_wish(GUILD, member, it, None)
    bought, balance = await cog.complete_cart(guild, member, [line(gofio, 2)])
    track = AsyncMock()
    shop_module = __import__("bot.cogs.shop", fromlist=["logros"])
    original = shop_module.logros.track
    shop_module.logros.track = track
    try:
        await cog.after_cart(make_interaction(member), guild, member, bought, 0)
    finally:
        shop_module.logros.track = original
    assert [w.item_id for w in await cog.repository.wishlist(GUILD, BUYER)] == [other.id]
    delta = track.await_args.args[4]
    assert delta.add["shop_purchases"] == 2
    assert delta.add["shop_broke_buy"] == 1, "quedarse a cero cuenta una vez por compra"
    assert delta.add["shop_wish_buys"] == 1


def test_logros_del_carrito() -> None:
    friday = datetime(2026, 11, 27, 12, tzinfo=TIMEZONE)
    delta = shop_cart_stats(
        lines=3, units=42, rates=2, same_max=10, total=90_000, wished=2, wished_on_sale=1,
        when=friday,
    )  # fmt: skip
    assert delta.add["shop_carts"] == 1
    assert delta.add["shop_cart_black_friday"] == 1
    assert delta.add["shop_cart_42"] == 1
    assert delta.peak["shop_big_buy_max"] == 90_000, "«de una sola vez» es la compra entera"
    single = shop_cart_stats(
        lines=1, units=1, rates=1, same_max=1, total=10, wished=0, wished_on_sale=0,
        when=datetime(2026, 1, 7, tzinfo=TIMEZONE),
    )  # fmt: skip
    assert "shop_carts" not in single.add and "shop_cart_rebajas" not in single.add


# -- Límite por persona antes de la caja ------------------------------------------------


async def test_la_caja_avisa_del_limite_por_persona_antes_de_pagar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    once = await trophy(cog, "Corona", per_user=1)
    guild = make_guild()
    member = make_member(guild)
    await cog.complete_cart(guild, member, [line(once)])
    click = make_interaction(member)
    await cog.open_checkout(click, once.id)
    assert (
        "Solo se puede comprar 1 vez" in click.edit_original_response.await_args.kwargs["content"]
    )


async def test_la_cantidad_no_pasa_del_maximo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    few = await trophy(cog, "Corona", per_user=3)
    guild = make_guild()
    member = make_member(guild)
    problem = cog.check_line(guild, member, few, 4, bought=0, entries=[], level=0, now=NOW)
    assert problem is not None and "3" in problem
    assert cog.check_line(guild, member, few, 3, bought=0, entries=[], level=0, now=NOW) is None


# -- Carrito en memoria -----------------------------------------------------------------


async def test_el_carrito_tiene_tope_de_lineas_y_caduca(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    for item_id in range(MAX_CART_LINES):
        assert cog.put_in_cart(GUILD, BUYER, item_id, 1) is None
    assert cog.put_in_cart(GUILD, BUYER, 999, 1) is not None
    assert cog.put_in_cart(GUILD, BUYER, 0, 50, add=True) is None
    assert cog.cart(GUILD, BUYER)[0] == MAX_QUANTITY
    cog.prune_carts(clock.now + 2 * DAY)
    assert cog.cart(GUILD, BUYER) == {}


# -- Escaparate compacto ----------------------------------------------------------------


async def test_buscar_ordenar_y_filtrar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    banana = await trophy(cog, "Plátano de Canarias", price=120)
    await trophy(cog, "Queso de flor", price=950, discount=20)
    await trophy(cog, "Roca del Teide", price=4_800, description="Con su plátano grabado")
    guild = make_guild()
    member = make_member(guild)
    view = await cog.storefront(guild, member, search="platano")
    assert [i.name for i in view.shown] == ["Plátano de Canarias", "Roca del Teide"]
    view.search = None
    view.prefs.sort = "caro"
    assert [i.name for i in view.shown][0] == "Roca del Teide"
    view.offers = True
    assert [i.name for i in view.shown] == ["Queso de flor"]
    view.offers = False
    await cog.repository.grant(GUILD, BUYER, banana, NOW)
    await view.load()
    view.owned_filter = "tengo"
    assert [i.name for i in view.shown] == ["Plátano de Canarias"]
    view.owned_filter = "falta"
    assert "Plátano de Canarias" not in [i.name for i in view.shown]


async def test_el_desplegable_de_pasillos_dice_cuantos_tienes(tmp_path: Path) -> None:
    from bot.services.shop_catalog import CATALOG_BY_KEY

    catalog = (CATALOG_BY_KEY["platano"], CATALOG_BY_KEY["queso_guia"], CATALOG_BY_KEY["huevo"])
    cog = await make_cog(tmp_path, Clock(), catalog=catalog)
    guild = make_guild()
    member = make_member(guild)
    await cog.stock_up(GUILD)
    banana = await cog.repository.item_by_key(GUILD, "platano")
    assert banana is not None
    await cog.repository.grant(GUILD, BUYER, banana, NOW)
    view = await cog.storefront(guild, member)
    select = next(
        c
        for c in view.walk_children()
        if isinstance(c, ui.Select) and c.custom_id == "tienda:pasillo"
    )
    canarias = next(o for o in select.options if o.value == "canarias")
    assert canarias.description is not None and canarias.description.startswith("Tienes 1 de ")


async def test_cambiar_de_seccion_se_recuerda_para_la_proxima_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    await trophy(cog, "Gofio")
    guild = make_guild()
    owner = make_member(guild)
    view = await cog.storefront(guild, owner)
    select = next(
        c
        for c in view.walk_children()
        if isinstance(c, ui.Select) and c.custom_id == "tienda:orden"
    )
    select._values = ["caro"]  # type: ignore[attr-defined]
    await select.callback(make_interaction(owner))
    view.offers = True
    again = await cog.storefront(guild, owner)
    assert again.prefs.sort == "caro" and not again.offers, "el orden sí, los filtros no"


async def test_otro_miembro_que_toca_un_filtro_abre_su_escaparate(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    await trophy(cog, "Gofio", discount=10)
    guild = make_guild()
    owner = make_member(guild)
    view = await cog.storefront(guild, owner)
    stranger = make_interaction(make_member(guild, OTHER))
    await view.toggle_offers(stranger)
    own = stranger.edit_original_response.await_args.kwargs["view"]
    assert isinstance(own, Storefront) and own.owner.id == OTHER and own.offers
    assert not view.offers


async def test_la_linea_compacta_lleva_sus_etiquetas() -> None:
    text = compact_line(
        item(discount=20, stock=10, sold=7, min_level=5, created_at=NOW),
        NOW,
        owned=2,
        wished=True,
        in_cart=3,
        best_seller=True,
    )
    assert "~~1.000 Y$~~ **800 Y$** 🏷️ -20 %" in text
    for tag in ("✅×2", "🛒×3", "⭐", "📦 quedan 3 de 10", "🔒 nv. 5", "🔥", "🆕"):
        assert tag in text, tag
    assert "✅ máx." in compact_line(item(per_user=1), NOW, owned=0, maxed=True)


def test_el_desplegable_dice_lo_que_queda_de_una_edicion_limitada() -> None:
    assert _stock_note(item(stock=10, sold=7)) == " · quedan 3 de 10"
    assert _stock_note(item(stock=10, sold=10)) == " · agotado"
    assert _stock_note(item()) == ""


# -- Ficha y carrito en Discord ---------------------------------------------------------


async def test_la_ficha_cambia_la_cantidad_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    guild = make_guild()
    member = make_member(guild)
    opening = make_interaction(member)
    await open_sheet(cog, opening, gofio.id)
    sheet = opening.edit_original_response.await_args.kwargs["view"]
    assert isinstance(sheet, ItemSheet) and sheet.qty == 1
    await sheet.more(make_interaction(member))
    await sheet.more(make_interaction(member))
    assert sheet.qty == 3 and "3 × Gofio" in texts(sheet)

    paying = make_interaction(member)
    await sheet.pay(paying)

    assert sheet.done and "FACTURA SIMPLIFICADA" in texts(sheet)
    assert await cog.economy.balance(GUILD, BUYER) == STARTING_BALANCE - 321 * 3
    announcement = paying.followup.send.await_args.args[0]
    assert "Gofio" in announcement and "×3" in announcement


async def test_varios_en_el_desplegable_van_al_carrito_y_el_carrito_se_paga(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    queso = await trophy(cog, "Queso", price=200, igic="cero")
    guild = make_guild()
    owner = make_member(guild)
    view = await cog.storefront(guild, owner)
    picking = make_interaction(owner)
    await add_to_cart(cog, picking, [gofio.id, queso.id], names=view.catalog, storefront=view)
    assert cog.cart(GUILD, BUYER) == {gofio.id: 1, queso.id: 1}
    assert "🛒 Carrito · 2" in [b.label for b in view.walk_children() if isinstance(b, ui.Button)]

    panel = CartPanel(cog, guild, owner)
    await panel.load()
    assert "IGIC 0 % s/ 200" in texts(panel) and "IGIC 7 % s/ 300" in texts(panel)
    await panel.pay(make_interaction(owner))

    assert panel.done and cog.cart(GUILD, BUYER) == {}
    assert await cog.economy.balance(GUILD, BUYER) == STARTING_BALANCE - 321 - 200


async def test_si_algo_se_agota_mientras_tanto_el_carrito_se_queda_y_lo_dice(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock())
    last = await trophy(cog, "Patata", stock=1)
    guild = make_guild()
    owner = make_member(guild)
    cog.put_in_cart(GUILD, BUYER, last.id, 1)
    panel = CartPanel(cog, guild, owner)
    await panel.load()
    # Otro se lleva la última antes de que pague.
    await cog.complete_cart(guild, make_member(guild, OTHER), [line(last)])

    await panel.pay(make_interaction(owner))

    assert not panel.done and panel.note is not None and "Agotado" in panel.note
    assert cog.cart(GUILD, BUYER) == {last.id: 1}
    assert await cog.economy.balance(GUILD, BUYER) == STARTING_BALANCE


# -- Lista de deseos --------------------------------------------------------------------


async def test_la_lista_se_apunta_se_quita_y_tiene_tope(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    member = make_member(guild)
    items = [await trophy(cog, f"Cosa {n}") for n in range(MAX_WISHES + 1)]
    for it in items[:MAX_WISHES]:
        assert await cog.toggle_wish(GUILD, member, it, None) == (True, None)
    wished, refused = await cog.toggle_wish(GUILD, member, items[-1], None)
    assert not wished and refused is not None
    assert await cog.toggle_wish(GUILD, member, items[0], None) == (False, None)
    assert len(await cog.repository.wishlist(GUILD, BUYER)) == MAX_WISHES - 1


async def test_la_lista_es_publica_en_el_escaparate_y_en_perfil(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    guild = make_guild()
    owner = make_member(guild)
    await cog.toggle_wish(GUILD, owner, gofio, None)
    view = await cog.storefront(guild, owner)
    view.prefs.tab = WISH_TAB
    view.rebuild()
    assert [i.name for i in view.shown] == ["Gofio"]
    assert "La lista es pública" in texts(view)
    embed = await cog.wishlist_embed(guild, owner)
    assert "Gofio" in (embed.description or "") and "321 Y$" in (embed.description or "")


def test_avisos_solo_de_lo_que_cambia() -> None:
    plain = item(price=1_000)
    before = wish_state(plain, now=NOW, balance=0, seasonal=False, selling=True)
    assert before == frozenset()
    on_sale = item(price=1_000, discount=20)
    after = wish_state(on_sale, now=NOW, balance=0, seasonal=False, selling=True)
    assert wish_news(before, after) == ["sale"]
    assert wish_news(after, after) == [], "la misma rebaja no se avisa dos veces"
    sold_out = item(stock=1, sold=1)
    restocked = item(stock=2, sold=1)
    gone = wish_state(sold_out, now=NOW, balance=0, seasonal=False, selling=True)
    back = wish_state(restocked, now=NOW, balance=0, seasonal=False, selling=True)
    assert wish_news(gone, back) == ["restock"]
    rich = wish_state(plain, now=NOW, balance=5_000, seasonal=False, selling=True)
    assert wish_news(before, rich) == ["money"]
    kept = stored_seen(rich, before)
    assert "money" in kept and wish_news(kept, rich) == [], "el dinero se avisa una vez"
    season = wish_state(plain, now=NOW, balance=0, seasonal=True, selling=True)
    assert wish_news(before, season) == ["season"]


async def test_la_vigilancia_avisa_por_privado_una_vez_y_respeta_el_silencio(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    guild = make_guild()
    guild.name = "Servidor"
    member = make_member(guild)
    member.send = AsyncMock()
    guild.get_member = MagicMock(return_value=member)
    cog.bot.get_guild = MagicMock(return_value=guild)
    await cog.toggle_wish(GUILD, member, gofio, None)

    assert await cog.check_wishes() == 0, "lo que ya se veía al apuntarlo no se avisa"
    await cog.repository.update_item(GUILD, gofio.id, discount=50)
    assert await cog.check_wishes() == 1
    sent = member.send.await_args.args[0]
    assert "Servidor" in sent and "Gofio" in sent and "rebajado un 50 %" in sent
    assert await cog.check_wishes() == 0

    await cog.repository.set_muted(GUILD, BUYER, True)
    await cog.repository.update_item(GUILD, gofio.id, discount=60)
    assert await cog.check_wishes() == 0
    await cog.repository.set_muted(GUILD, BUYER, False)
    assert await cog.check_wishes() == 0, "lo visto en silencio no se repite al volver"


async def test_privados_cerrados_no_rompen_la_vigilancia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    guild = make_guild()
    guild.name = "Servidor"
    member = make_member(guild)
    member.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "privados cerrados"))
    guild.get_member = MagicMock(return_value=member)
    cog.bot.get_guild = MagicMock(return_value=guild)
    await cog.toggle_wish(GUILD, member, gofio, None)
    await cog.repository.update_item(GUILD, gofio.id, discount=50)
    assert await cog.check_wishes() == 0
    (wish,) = await cog.repository.wishlist(GUILD, BUYER)
    assert any(mark.startswith("sale:") for mark in wish.seen), "no se reintenta para siempre"


async def test_un_puesto_del_rastro_avisa_a_quien_lo_desea_menos_al_que_vende(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    guild = make_guild()
    guild.name = "Servidor"
    buyer = make_member(guild)
    buyer.send = AsyncMock()
    seller = make_member(guild, OTHER)
    seller.send = AsyncMock()
    seller.display_name = "Ana"
    guild.get_member = MagicMock(side_effect={BUYER: buyer, OTHER: seller}.get)
    cog.bot.get_guild = MagicMock(return_value=guild)
    for member in (buyer, seller):
        await cog.toggle_wish(GUILD, member, gofio, None)
    listing = Listing(
        id=1, guild_id=GUILD, seller_id=OTHER, entry_id=1, item_id=gofio.id, name="Gofio",
        emoji="🛥️", serial=None, edition=None, catalog_key=None, start_price=250,
        channel_id=None, created_at=NOW, expires_at=NOW + 600, close_at=None,
        top_bidder=None, top_bid=None, top_escrow=None, top_reference=None, bids=0,
        status="abierto",
    )  # fmt: skip

    assert await cog.alert_stall(GUILD, listing) == 1

    text = buyer.send.await_args.args[0]
    assert "Ana" in text and "Gofio" in text and "250 Y$" in text
    seller.send.assert_not_awaited()


def test_el_mensaje_privado_dice_de_que_servidor_es() -> None:
    text = news_message("Servidor", ["🏷️ algo"])
    assert text.startswith("⭐ **Tu lista de deseos** en **Servidor**") and "🔕" in text


async def test_el_escaparate_cabe_con_la_lista_de_deseos_llena(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    owner = make_member(guild)
    for n in range(MAX_WISHES):
        it = await trophy(
            cog,
            f"Un deseo con nombre muy largo {n:02d}"[:40],
            emoji="<:jovani_moneda_de_oro:123456789012345678>",
            stock=10,
            discount=25,
            min_level=50,
        )
        await cog.toggle_wish(GUILD, owner, it, None)
        cog.put_in_cart(GUILD, BUYER, it.id, 2)
    view = await cog.storefront(guild, owner)
    view.prefs.tab = WISH_TAB
    view.rebuild()
    assert view.total_children_count <= 40
    assert len(texts(view)) <= 4000
    assert view.pages == -(-MAX_WISHES // PAGE_SIZE)
    panel = CartPanel(cog, guild, owner)
    await panel.load()
    assert panel.total_children_count <= 40 and len(texts(panel)) <= 4000


async def test_el_libro_apunta_cada_linea_con_su_concepto_e_impuesto(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    gofio = await trophy(cog, "Gofio", price=300)
    zero = await trophy(cog, "Queso", price=200, igic="cero")
    guild = make_guild()
    await cog.complete_cart(guild, make_member(guild), [line(gofio, 2), line(zero)])
    connection = cog.repository._connect()
    try:
        ledger = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "AND reason LIKE 'tienda:%' ORDER BY id",
            (GUILD, BUYER),
        ).fetchall()
        taxed = connection.execute(
            "SELECT concept, base, tax FROM economy_consumption_tax ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    assert [tuple(row) for row in ledger] == [
        ("tienda:objeto", -600),
        ("tienda:igic", -42),
        ("tienda:objeto", -200),
    ]
    assert [tuple(row) for row in taxed] == [("objeto", 600, 42), ("objeto", 200, 0)]
