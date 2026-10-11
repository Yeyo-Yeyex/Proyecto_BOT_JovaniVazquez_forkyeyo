"""Pruebas del motor de Legislatura: partida, puntuación, mercado, cruces y premios."""

from __future__ import annotations

import itertools
import random

import pytest

from bot.services.legislatura import cosmetics, prizes
from bot.services.legislatura.cards import Card, Edition, Enhancement, Seal, Suit
from bot.services.legislatura.catalog import BOSSES, GOVERNMENTS, ITEMS, STAKES, items_of
from bot.services.legislatura.hands import HANDS, HandType, evaluate
from bot.services.legislatura.items import Bloc, Category
from bot.services.legislatura.rng import Rng, daily_seed
from bot.services.legislatura.run import (
    PERIODS,
    SESSION_REWARDS,
    GameError,
    Phase,
    Run,
)


def fresh(seed: str = "PRUEBA01", **kwargs: object) -> Run:
    run = Run.new(seed, **kwargs)  # type: ignore[arg-type]
    run.start_session()
    return run


def set_hand(run: Run, *specs: tuple[int, Suit]) -> list[int]:
    """Cambia la mano por cartas conocidas (ids nuevos) y devuelve sus ids."""
    ids = []
    for rank, suit in specs:
        card = Card(run.next_id, rank, suit)
        run.next_id += 1
        run.cards[card.id] = card
        ids.append(card.id)
    run.hand = ids
    return ids


# -- Azar y guardado --------------------------------------------------------------------------


def test_la_misma_semilla_da_la_misma_partida() -> None:
    a, b = fresh("IGUAL123"), fresh("IGUAL123")
    assert a.hand == b.hand
    assert a.boss == b.boss


def test_comprar_no_cambia_la_baraja_de_la_siguiente_sesion() -> None:
    rng_a, rng_b = Rng("X"), Rng("X")
    rng_b.stream("tienda:1:0")  # Otro flujo gastado no mueve el del mazo.
    assert rng_a.stream("mazo:1:1").random() == rng_b.stream("mazo:1:1").random()


def test_la_semilla_del_dia_es_igual_para_todo_el_servidor() -> None:
    assert daily_seed(1, "2026-10-11") == daily_seed(1, "2026-10-11")
    assert daily_seed(1, "2026-10-11") != daily_seed(2, "2026-10-11")


def test_guardar_y_cargar_deja_la_partida_igual() -> None:
    run = fresh()
    run.add_advisor("credito_social")
    run.play(run.hand[:2])
    again = Run.loads(run.dumps())
    assert again.to_dict() == run.to_dict()


def test_un_formato_desconocido_no_se_carga() -> None:
    data = fresh().to_dict()
    data["v"] = 999
    with pytest.raises(ValueError):
        Run.from_dict(data)


# -- Sesiones -------------------------------------------------------------------------------


def test_jugar_cuenta_votos_y_repone_la_mano() -> None:
    run = fresh()
    ids = set_hand(run, (3, Suit.OROS), (3, Suit.COPAS), (5, Suit.ESPADAS))
    run.draw = [c for c in sorted(run.cards) if c not in ids][:20]
    outcome = run.play(ids[:2])
    votes, mult = HANDS[HandType.PAREJA].at_level(1)
    assert outcome.context.total == (votes + 3 + 3) * mult
    assert run.round_score == outcome.context.total
    assert len(run.hand) == run.hand_size
    assert run.hands_left == run.base_hands - 1


def test_no_se_puede_jugar_lo_que_no_esta_en_la_mano() -> None:
    run = fresh()
    with pytest.raises(GameError):
        run.play([99_999])
    with pytest.raises(GameError):
        run.play([])


def test_llegar_a_los_votos_gana_la_sesion_y_abre_el_mercado() -> None:
    run = fresh()
    run.round_score = run.target - 1
    fondos = run.fondos
    run.play(run.hand[:1])
    assert run.phase is Phase.MERCADO
    assert run.session == 1
    assert run.fondos >= fondos + SESSION_REWARDS[0]
    assert run.market is not None and run.market.offers


def test_sin_jugadas_cae_el_gobierno() -> None:
    run = fresh()
    run.hands_left = 1
    run.round_score = 0
    outcome = run.play(run.hand[:1])
    assert outcome.lost
    assert run.phase is Phase.PERDIDA


def test_el_aforado_salva_una_vez_y_se_va() -> None:
    run = fresh()
    run.add_advisor("aforado")
    run.hands_left = 1
    outcome = run.play(run.hand[:1])
    assert outcome.cleared and not outcome.lost
    assert not run.has_advisor("aforado")
    assert run.phase is Phase.MERCADO


def test_abstenerse_da_premio_pero_no_en_la_mocion() -> None:
    run = Run.new("ABST0001")
    run.skip_session()
    assert run.session == 1
    assert run.tags or run.fondos > 4
    run.skip_session()
    with pytest.raises(GameError):
        run.skip_session()


def test_los_descartes_se_acaban() -> None:
    run = fresh()
    for _ in range(run.base_discards):
        run.discard(run.hand[:1])
    with pytest.raises(GameError):
        run.discard(run.hand[:1])


def test_ganar_el_ultimo_periodo_gana_la_legislatura_y_se_puede_prorrogar_con_tope() -> None:
    run = fresh()
    run.period, run.session = PERIODS, 2
    run.round_score = run.target
    run.play(run.hand[:1])
    assert run.phase is Phase.GANADA
    assert run.cleared_periods == 1  # Solo esta moción en la prueba.
    run.extend()
    assert run.phase is Phase.MERCADO and run.extended
    run.period = PERIODS + 3
    run.phase = Phase.GANADA
    assert not run.can_extend
    with pytest.raises(GameError):
        run.extend()


# -- Puntuación y cartas ---------------------------------------------------------------------


def test_las_etiquetas_y_sellos_puntuan_lo_suyo() -> None:
    run = fresh()
    ids = set_hand(run, (3, Suit.OROS), (3, Suit.COPAS))
    run.cards[ids[0]].enhancement = Enhancement.ENCHUFADO
    run.cards[ids[1]].seal = Seal.VOTO_DELEGADO
    fondos = run.fondos
    ctx = run.play(ids).context
    votes, mult = HANDS[HandType.PAREJA].at_level(1)
    assert ctx.votes == votes + 3 + 30 + 3 + 3
    assert ctx.mult == mult
    assert run.fondos >= fondos


def test_el_funcionario_multiplica_desde_la_mano() -> None:
    run = fresh()
    ids = set_hand(run, (3, Suit.OROS), (5, Suit.COPAS))
    run.cards[ids[1]].enhancement = Enhancement.FUNCIONARIO
    ctx = run.play(ids[:1]).context
    assert ctx.mult == HANDS[HandType.CARTA_ALTA].mult * 1.5


def test_la_cuenta_paso_a_paso_cuadra_con_el_total() -> None:
    run = fresh()
    run.add_advisor("veto_hungria")
    run.add_advisor("cumbre_otan")
    ctx = run.play(run.hand[:5]).context
    votes = sum(s.amount for s in ctx.steps if s.kind.value == "votos")
    assert votes == ctx.votes
    assert ctx.total == int(ctx.votes * ctx.mult)


def test_el_jefe_huelga_de_oros_anula_los_oros() -> None:
    run = Run.new("JEFE0001")
    run.boss = "huelga_oros"
    run.session = 2
    run.start_session()
    ids = set_hand(run, (7, Suit.OROS))
    ctx = run.play(ids).context
    assert ctx.votes == HANDS[HandType.CARTA_ALTA].votes


# -- Cruces entre bloques ---------------------------------------------------------------------


def test_los_aranceles_encarecen_lo_chino_al_momento() -> None:
    run = fresh()
    item = ITEMS["credito_social"]
    from bot.services.legislatura.market import price_of

    before = price_of(run, item)
    run.add_advisor("aranceles")
    assert price_of(run, item) > before


def test_el_boicot_a_eurovision_echa_a_israel_y_no_deja_comprarlo() -> None:
    run = fresh()
    run.add_advisor("asentamiento")
    run.add_advisor("boicot_eurovision")
    assert not run.advisors_of(Bloc.ISRAEL)
    assert run.blocked_reason(ITEMS["paquete_ayuda"]) is not None


def test_la_otan_cobra_menos_con_perro_sanxe() -> None:
    def cut(with_sanxe: bool) -> int:
        run = fresh()
        run.fondos = 1_000
        run.add_advisor("cumbre_otan")
        if with_sanxe:
            run.add_advisor("perro_sanxe")
            run.advisors.reverse()  # Que la OTAN cobre antes que el 21 % de Sanxe.
        before = run.fondos
        ITEMS["cumbre_otan"].effect.on_round_end(run, run.advisors[-1])  # type: ignore[union-attr]
        return before - run.fondos

    assert cut(with_sanxe=True) < cut(with_sanxe=False)


def test_el_paquete_de_ayuda_solo_llega_con_eeuu_en_la_mesa() -> None:
    run = fresh()
    slot = run.add_advisor("paquete_ayuda")
    assert slot is not None
    before = run.fondos
    ITEMS["paquete_ayuda"].effect.on_round_end(run, slot)  # type: ignore[union-attr]
    assert run.fondos == before
    run.add_advisor("aranceles")
    ITEMS["paquete_ayuda"].effect.on_round_end(run, slot)  # type: ignore[union-attr]
    assert run.fondos == before + 4


def test_el_cis_sube_dos_niveles_con_perro_sanxe() -> None:
    run = fresh()
    run.add_advisor("perro_sanxe")
    run.consumables = ["cis"]
    run.use(0)
    assert run.hand_levels[HandType.PAREJA.value] == 3


def test_la_directiva_europea_llega_tarde() -> None:
    run = fresh()
    run.consumables = ["directiva_europea"]
    target = run.hand[0]
    run.use(0, [target])
    assert run.cards[target].enhancement is None
    run.round_score = run.target
    run.play([i for i in run.hand if i != target][:1])
    assert run.pending  # Falta otra sesión.


# -- Mercado ----------------------------------------------------------------------------------


def to_market(seed: str = "MERCADO1") -> Run:
    run = fresh(seed)
    run.round_score = run.target
    run.play(run.hand[:1])
    assert run.phase is Phase.MERCADO
    return run


def test_comprar_cobra_y_marca_vendido() -> None:
    run = to_market()
    assert run.market is not None
    run.fondos = 100
    price = run.offer_price(run.market.offers[0])
    run.buy(0)
    assert run.fondos == 100 - price
    with pytest.raises(GameError):
        run.buy(0)


def test_sin_fondos_no_se_compra_y_no_cambia_nada() -> None:
    run = to_market()
    run.fondos = 0
    before = run.to_dict()
    with pytest.raises(GameError):
        run.buy(0)
    assert run.to_dict() == before


def test_vender_con_puerta_giratoria_da_el_doble() -> None:
    run = to_market()
    slot = run.add_advisor("veto_hungria")
    assert slot is not None
    from bot.services.legislatura.market import sell_value

    plain = sell_value(run, slot)
    run.add_advisor("puerta_giratoria")
    assert sell_value(run, slot) == plain * 2


def test_offshore_no_ocupa_hueco() -> None:
    run = fresh()
    for key in ("veto_hungria", "aranceles", "cumbre_otan", "credito_social", "aforado"):
        assert run.add_advisor(key) is not None
    assert run.add_advisor("asentamiento") is None
    assert run.add_advisor("asentamiento", Edition.OFFSHORE) is not None


def test_un_sobre_se_abre_y_se_elige() -> None:
    run = to_market()
    assert run.market is not None
    run.fondos = 100
    run.buy_pack(0)
    assert run.phase is Phase.SOBRE
    run.pick(0)
    assert run.phase is Phase.MERCADO


# -- Catálogo -----------------------------------------------------------------------------------


def test_cada_bloque_con_satira_tiene_asesores() -> None:
    blocs = {i.bloc for i in items_of(Category.ASESOR, visible_only=False)}
    assert {Bloc.ESPANA, Bloc.UE, Bloc.EEUU, Bloc.CHINA, Bloc.ISRAEL} <= blocs


def test_hay_una_encuesta_por_combinacion() -> None:
    assert {i.hand for i in items_of(Category.ENCUESTA)} == set(HandType)


def test_cada_cosa_del_catalogo_tiene_lo_que_su_categoria_necesita() -> None:
    for item in ITEMS.values():
        if item.category is Category.ASESOR:
            assert item.effect is not None, item.key
        elif item.category.consumable:
            assert item.usable is not None, item.key
        elif item.category is Category.TRATADO:
            assert item.redeem is not None, item.key
        else:
            assert item.pack is not None, item.key


def test_todos_los_gobiernos_y_primas_arrancan() -> None:
    for government in GOVERNMENTS:
        for stake in STAKES:
            Run.new("GOB00001", government=government, stake=stake).start_session()


def test_todos_los_jefes_se_pueden_jugar() -> None:
    for key in BOSSES:
        run = Run.new("JEFES001")
        run.boss, run.session = key, 2
        run.start_session()
        run.discard(run.hand[:1])
        run.play(run.hand[:5])


# -- Premios y aspectos ---------------------------------------------------------------------------


def test_el_premio_crece_con_los_periodos_y_la_diaria_no_paga() -> None:
    run = Run.new("PREMIO01", stake=8)
    shares = []
    for cleared in range(PERIODS + 1):
        run.cleared_periods = cleared
        shares.append(prizes.payout(run))
    assert shares == sorted(shares)
    assert shares[-1] == prizes.PRIZES[8].prize
    assert prizes.entry_fee(run) == prizes.PRIZES[8].entry
    daily = Run.new("DIARIA01", mode="diaria")
    daily.cleared_periods = PERIODS
    assert prizes.payout(daily) == 0 and prizes.entry_fee(daily) == 0


def test_la_prorroga_dobla_y_si_caes_no_cobras() -> None:
    run = Run.new("PRORRO01", stake=1)
    run.extended = True
    run.cleared_periods = PERIODS + 1
    run.phase = Phase.GANADA
    assert prizes.payout(run) == 2 * prizes.PRIZES[1].prize
    run.phase = Phase.PERDIDA
    assert prizes.payout(run) == 0


def test_los_aspectos_vuelven_al_de_serie_si_ya_no_los_tienes() -> None:
    choice = {"tapete": "tapete_falcon", "asesor:perro_sanxe": "arte_perro_sanxe_gafas"}
    owned = {"tapete_falcon", "arte_perro_sanxe_gafas"}
    resolved = cosmetics.resolve(choice, owned)
    assert resolved["tapete"].key == "tapete_falcon"
    assert resolved["asesor:perro_sanxe"].key == "arte_perro_sanxe_gafas"
    fallback = cosmetics.resolve(choice, set())
    assert fallback["tapete"].default
    assert "asesor:perro_sanxe" not in fallback


def test_los_aspectos_de_asesor_visten_asesores_que_existen() -> None:
    for skin in cosmetics.SKINS:
        if skin.slot is cosmetics.SkinSlot.ASESOR:
            assert skin.target in ITEMS


# -- Partidas enteras con un jugador al azar ---------------------------------------------


def _greedy(run: Run) -> list[int]:
    hand = [run.cards[i] for i in run.hand]
    best: tuple[int, list[int]] | None = None
    for size in range(1, 6):
        for combo in itertools.combinations(hand, size):
            info = HANDS[evaluate(list(combo)).hand]
            value = info.votes * info.mult
            if best is None or value > best[0]:
                best = (value, [c.id for c in combo])
    assert best is not None
    return best[1]


@pytest.mark.parametrize("seed", range(20))
def test_partidas_al_azar_no_rompen_nada_y_se_guardan_igual(seed: int) -> None:
    rnd = random.Random(seed)
    run = Run.new(f"AZAR{seed:04}", stake=rnd.choice(list(STAKES)))
    for _ in range(400):
        if run.phase in (Phase.PERDIDA, Phase.GANADA):
            break
        try:
            if run.phase is Phase.ELEGIR:
                run.start_session()
            elif run.phase is Phase.SESION:
                if run.discards_left and rnd.random() < 0.3:
                    run.discard(rnd.sample(run.hand, min(3, len(run.hand))))
                else:
                    run.play(_greedy(run))
            elif run.phase is Phase.MERCADO:
                assert run.market is not None
                if run.market.offers and rnd.random() < 0.5:
                    run.buy(rnd.randrange(len(run.market.offers)))
                else:
                    run.leave_market()
            elif run.phase is Phase.SOBRE:
                run.close_pack()
        except GameError:
            if run.phase is Phase.MERCADO:
                run.leave_market()
        assert Run.loads(run.dumps()).to_dict() == run.to_dict()
    assert run.fondos >= 0 or run.phase is Phase.PERDIDA
    assert all(isinstance(c, Card) for c in run.cards.values())
    assert run.boss in BOSSES and Suit(run.cards[min(run.cards)].suit)


def test_el_cunado_conspiranoico_se_crece_al_perder_y_se_desinfla_al_ganar() -> None:
    run = fresh()
    slot = run.add_advisor("cunado_conspiranoico")
    assert slot is not None
    run.round_score = 0
    ctx = run.play(run.hand[:1]).context
    assert any(s.source == "cunado_conspiranoico" and s.kind.value == "nota" for s in ctx.steps)
    assert slot.state["razon"] == 3
    run.round_score = run.target
    run.play(run.hand[:1])
    assert slot.state["razon"] == 1  # 3 // 2: al ganar pierde la mitad.


def test_el_cunado_no_suma_si_hay_un_asesor_israeli_que_vigilar() -> None:
    run = fresh()
    slot = run.add_advisor("cunado_conspiranoico")
    assert slot is not None
    slot.state["razon"] = 30
    run.add_advisor("paquete_ayuda")
    ctx = run.play(run.hand[:1]).context
    assert not any(s.source == "cunado_conspiranoico" and s.kind.value == "mult" for s in ctx.steps)


def test_las_frases_del_cunado_son_suyas_y_se_rien_de_el() -> None:
    quotes = ITEMS["cunado_conspiranoico"].quotes
    assert len(quotes) >= 6
    # Cada frase va entre comillas: siempre es él quien habla, nunca el bot.
    assert all(q.startswith("«") and q.endswith("»") for q in quotes)
