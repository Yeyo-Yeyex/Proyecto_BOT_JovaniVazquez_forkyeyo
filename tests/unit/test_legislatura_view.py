"""Pruebas de lo que se ve en Legislatura (`bot.services.legislatura.view`)."""

from __future__ import annotations

import json

from bot.services.legislatura import cosmetics, view
from bot.services.legislatura.cards import Card, Suit
from bot.services.legislatura.run import Run


def started(seed: str = "VISTA001") -> Run:
    run = Run.new(seed)
    run.start_session()
    return run


def test_los_estados_son_json_para_la_escena() -> None:
    run = started()
    run.add_advisor("cumbre_otan")
    for state in (view.panel_state(run), view.table_state(run), view.meta_state()):
        json.dumps(state)


def test_las_dos_imagenes_tienen_el_mismo_alto() -> None:
    meta = view.meta_state()
    assert meta["h"] == view.H
    assert meta["panel_w"] < meta["table_w"]


def test_el_panel_puede_enseñar_los_votos_de_antes_para_no_destripar() -> None:
    run = started()
    before = run.round_score
    run.play(run.hand[:2])
    assert view.panel_state(run, round_score=before)["score"] == before
    assert view.panel_state(run)["score"] == run.round_score


def test_el_jefe_solo_sale_en_la_mocion() -> None:
    run = started()
    assert view.panel_state(run)["boss"] is None
    run.session = 2
    assert view.panel_state(run)["boss"]["name"]


def test_marcar_cartas_las_sube_en_la_mesa() -> None:
    run = started()
    state = view.table_state(run, selected={run.hand[0]})
    assert [c["up"] for c in state["hand"]][:2] == [True, False]


def test_la_animacion_acaba_en_el_total_de_la_jugada() -> None:
    run = started()
    run.add_advisor("veto_hungria")
    played = run.hand[:3]
    before = view.table_state(run)
    ctx = run.play(played).context
    timeline = view.score_timeline(before, played, ctx)
    last = timeline.frames[-1]
    assert last["stage"] == "total" and last["total"] == ctx.total
    assert len(timeline.frames) == len(timeline.durations) <= view.MAX_FRAMES
    assert timeline.durations[-1] == view.FINAL_FRAME_MS
    assert {c["id"] for c in last["played"]} == set(played)
    json.dumps(timeline.frames)


def test_muchos_pasos_no_pasan_del_tope_de_fotogramas() -> None:
    run = started()
    ids = []
    for _ in range(5):
        card = Card(run.next_id, 3, Suit.OROS)
        run.next_id += 1
        run.cards[card.id] = card
        ids.append(card.id)
    run.hand = ids + run.hand[:3]
    for key in ("perro_sanxe", "fabrica_shenzhen", "cumbre_otan", "credito_social"):
        run.add_advisor(key)
    before = view.table_state(run)
    ctx = run.play(ids).context
    for _ in range(200):  # Una cascada de combos como las de Balatro.
        ctx.add_votes("carta", 1, run.cards[ids[0]])
    assert len(ctx.steps) > view.MAX_FRAMES
    timeline = view.score_timeline(before, ids, ctx)
    assert len(timeline.frames) <= view.MAX_FRAMES
    assert timeline.frames[-1]["total"] == ctx.total


def test_solo_se_anima_si_cabe_en_el_presupuesto() -> None:
    assert view.should_animate(60, None)
    assert view.should_animate(60, view.ANIMATION_BUDGET_MS / 60)
    assert not view.should_animate(60, view.ANIMATION_BUDGET_MS / 30)


def test_los_aspectos_llegan_a_la_escena() -> None:
    skins = cosmetics.resolve(
        {"tapete": "tapete_falcon", "asesor:perro_sanxe": "arte_perro_sanxe_gafas"},
        {"tapete_falcon", "arte_perro_sanxe_gafas"},
    )
    meta = view.meta_state(skins)
    assert meta["felt"] == [list(c) for c in cosmetics.SKIN_BY_KEY["tapete_falcon"].felt]
    assert meta["art"] == {"perro_sanxe": "arte_perro_sanxe_gafas"}
