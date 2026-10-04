"""Pruebas de las reglas puras de la bienvenida (bot.services.welcome)."""

from __future__ import annotations

import random

import pytest

from bot.services.welcome import (
    FALLBACK_RETURNS,
    FALLBACK_WELCOMES,
    GREETING_WINDOW_SECONDS,
    GifError,
    GifKind,
    GreetCheck,
    check_greeting,
    classify_gif,
    is_fast_greeting,
    parse_phrases,
    pick_phrase,
    render_phrase,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://media.tenor.com/abc/tenor.gif",
        "https://i.giphy.com/media/xyz/giphy.webp",
        "https://example.com/cosas/kratos.GIF",
    ],
)
def test_enlace_directo_va_en_el_embed(url: str) -> None:
    assert classify_gif(url) is GifKind.DIRECT


@pytest.mark.parametrize(
    "url",
    ["https://tenor.com/view/kratos-gif-12345", "https://giphy.com/gifs/hola-abc"],
)
def test_pagina_de_tenor_o_giphy_se_manda_como_enlace(url: str) -> None:
    assert classify_gif(url) is GifKind.PAGE


@pytest.mark.parametrize(
    ("url", "motivo"),
    [
        ("http://tenor.com/view/x", "https://"),
        ("tenor.com/view/x", "https://"),
        ("https://cdn.discordapp.com/attachments/1/2/a.gif", "caducan"),
        ("https://example.com/pagina", "no parece un GIF"),
        ("https://tenor.com/" + "a" * 600, "demasiado largo"),
    ],
)
def test_enlaces_que_no_sirven_se_rechazan_con_motivo(url: str, motivo: str) -> None:
    with pytest.raises(GifError, match=motivo):
        classify_gif(url)


def test_frases_separan_entrada_y_vuelta_e_ignoran_comentarios() -> None:
    phrases = parse_phrases("# comentario\n\nhola {usuario}\n[vuelta]\notra vez {usuario}\n")
    assert phrases.welcomes == ("hola {usuario}",)
    assert phrases.returns == ("otra vez {usuario}",)


def test_secciones_vacias_usan_la_reserva() -> None:
    phrases = parse_phrases("# nada\n[vuelta]\n")
    assert phrases.welcomes == FALLBACK_WELCOMES
    assert phrases.returns == FALLBACK_RETURNS


def test_render_sustituye_usuario_o_lo_pone_delante() -> None:
    assert render_phrase("hola {usuario}!", "<@1>") == "hola <@1>!"
    assert render_phrase("pasa, pasa", "<@1>") == "<@1> pasa, pasa"


def test_render_aguanta_llaves_sueltas() -> None:
    """El archivo lo edita cualquiera: `{` sin cerrar no debe romper nada."""
    assert render_phrase("{usuario} trae {pizza", "<@1>") == "<@1> trae {pizza"


def test_quien_vuelve_recibe_frase_de_vuelta() -> None:
    phrases = parse_phrases("nuevo\n[vuelta]\nviejo\n")
    rng = random.Random(1)
    assert pick_phrase(phrases, returning=False, rng=rng) == "nuevo"
    assert pick_phrase(phrases, returning=True, rng=rng) == "viejo"


def test_saludar_reglas_de_uno_mismo_y_plazo() -> None:
    assert check_greeting(1, 1, joined_at=0, now=10) is GreetCheck.SELF
    assert check_greeting(1, 2, joined_at=0, now=GREETING_WINDOW_SECONDS) is GreetCheck.OK
    late = GREETING_WINDOW_SECONDS + 1
    assert check_greeting(1, 2, joined_at=0, now=late) is GreetCheck.EXPIRED


def test_saludo_rapido_es_el_primer_minuto() -> None:
    assert is_fast_greeting(joined_at=100, now=160)
    assert not is_fast_greeting(joined_at=100, now=161)
