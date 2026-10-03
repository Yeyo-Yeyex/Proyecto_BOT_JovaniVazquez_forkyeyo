"""Pruebas de la lectura de argumentos de los efectos de imagen en bot.cogs.images."""

from __future__ import annotations

from unittest.mock import MagicMock

import discord

from bot.cogs.images import build_memes_embed, parse_arguments, resolve_inputs, usage
from bot.services.memes import EFFECTS


def make_user(user_id: int) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    return user


def make_message(*, mentions: list | None = None, attachments: list | None = None) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.mentions = mentions or []
    message.attachments = attachments or []
    return message


def make_image() -> MagicMock:
    attachment = MagicMock(spec=discord.Attachment)
    attachment.content_type = "image/png"
    return attachment


def avatar(user: MagicMock) -> object:
    return user.display_avatar.replace.return_value


def test_parse_arguments_separa_menciones_y_textos() -> None:
    """Las menciones salen en orden y sin repetir; los textos se separan por `|`."""
    mentioned, texts = parse_arguments("<@2> uno | <@!3> dos |  tres <@2>", split_texts=True)

    assert mentioned == [2, 3]
    assert texts == ["uno", "dos", "tres"]


def test_parse_arguments_con_un_solo_texto_conserva_las_barras() -> None:
    """Si el efecto admite un único texto, `|` es parte de él."""
    assert parse_arguments("a | b", split_texts=False) == ([], ["a | b"])


def test_parse_arguments_sin_texto() -> None:
    assert parse_arguments("  <@5>  ", split_texts=True) == ([5], [])


def test_usage_describe_avatares_y_textos() -> None:
    assert usage(EFFECTS["slap"]) == ".slap @miembro"
    assert usage(EFFECTS["trigger"]) == ".trigger [@miembro o imagen]"
    assert usage(EFFECTS["boo"]) == ".boo <texto1> | <texto2>"
    assert usage(EFFECTS["meme"]) == ".meme [@miembro o imagen] <texto> [| <texto abajo>]"


def test_un_avatar_prefiere_la_imagen_adjunta() -> None:
    author, target, image = make_user(1), make_user(2), make_image()
    message = make_message(mentions=[target], attachments=[image])

    inputs = resolve_inputs(
        EFFECTS["trigger"], "<@2>", author=author, message=message, replied=None
    )

    assert inputs.sources == [image]


def test_un_avatar_usa_al_mencionado_y_si_no_al_autor() -> None:
    author, target = make_user(1), make_user(2)

    with_mention = resolve_inputs(
        EFFECTS["trigger"],
        "<@2>",
        author=author,
        message=make_message(mentions=[target]),
        replied=None,
    )
    alone = resolve_inputs(
        EFFECTS["trigger"], "", author=author, message=make_message(), replied=None
    )

    assert with_mention.sources == [avatar(target)]
    assert with_mention.subject is target
    assert alone.sources == [avatar(author)]
    assert alone.subject is author


def test_responder_a_un_mensaje_cuenta_como_mencionar_a_su_autor() -> None:
    author, target = make_user(1), make_user(2)
    replied = make_message()
    replied.author = target

    inputs = resolve_inputs(
        EFFECTS["slap"], "", author=author, message=make_message(), replied=replied
    )

    assert inputs.sources == [avatar(author), avatar(target)]


def test_dos_avatares_con_dos_menciones_usa_ambas() -> None:
    author, first, second = make_user(1), make_user(2), make_user(3)
    message = make_message(mentions=[second, first])

    inputs = resolve_inputs(
        EFFECTS["spank"], "<@2> <@3>", author=author, message=message, replied=None
    )

    assert inputs.sources == [avatar(first), avatar(second)]


def test_dos_avatares_sin_objetivo_devuelve_el_uso() -> None:
    error = resolve_inputs(
        EFFECTS["slap"], "", author=make_user(1), message=make_message(), replied=None
    )

    assert isinstance(error, str)
    assert "Menciona a alguien" in error


def test_faltan_textos_incluye_un_ejemplo() -> None:
    error = resolve_inputs(
        EFFECTS["boo"], "solo uno", author=make_user(1), message=make_message(), replied=None
    )

    assert "Falta texto" in error
    assert ".boo un examen | yo" in error


def test_memes_embed_cabe_en_los_limites_de_discord() -> None:
    """La lista completa respeta 1024 caracteres por campo y 6000 por embed."""
    embed = build_memes_embed()

    listed = " · ".join(field.value for field in embed.fields).split(" · ")
    assert sorted(listed) == sorted(EFFECTS)
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(embed) <= 6000


def test_memes_embed_de_un_efecto_y_de_uno_que_no_existe() -> None:
    assert (
        build_memes_embed("brain").fields[0].value == "`.brain <texto1> | <texto2> | "
        "<texto3> | <texto4>`"
    )
    assert "No existe" in build_memes_embed("inventado").description


def test_en_efectos_sin_avatar_la_mencion_se_escribe_como_nombre() -> None:
    """`.changemymind @Ana es lista` escribe «Ana es lista», no « es lista»."""
    ana = make_user(2)
    ana.display_name = "Ana"

    inputs = resolve_inputs(
        EFFECTS["changemymind"],
        "<@2> es lista",
        author=make_user(1),
        message=make_message(mentions=[ana]),
        replied=None,
    )

    assert inputs.texts == ["Ana es lista"]


def test_en_efectos_con_avatar_la_mencion_elige_el_avatar_y_sale_del_texto() -> None:
    ana = make_user(2)
    ana.display_name = "Ana"

    inputs = resolve_inputs(
        EFFECTS["tweet"],
        "<@2> hola",
        author=make_user(1),
        message=make_message(mentions=[ana]),
        replied=None,
    )

    assert inputs.texts == ["hola"]
    assert inputs.subject is ana
