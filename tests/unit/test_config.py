"""Pruebas de bot.config: validación de la configuración cargada desde el entorno."""

from __future__ import annotations

import pytest

from bot.config import ConfigError, load_config


def test_load_config_lee_token_obligatorio() -> None:
    """Con un DISCORD_TOKEN presente, la configuración se construye correctamente."""
    config = load_config({"DISCORD_TOKEN": "token-de-prueba"})

    assert config.token == "token-de-prueba"
    assert config.log_level == "INFO"
    assert config.command_prefix == "."


def test_load_config_falla_sin_token() -> None:
    """Sin DISCORD_TOKEN, debe fallar de forma clara en vez de arrancar a medias."""
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load_config({})


def test_load_config_falla_con_token_vacio() -> None:
    """Un token compuesto solo de espacios se trata como ausente."""
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load_config({"DISCORD_TOKEN": "   "})


def test_load_config_acepta_log_level_en_minusculas() -> None:
    """LOG_LEVEL no debe ser sensible a mayúsculas/minúsculas."""
    config = load_config({"DISCORD_TOKEN": "t", "LOG_LEVEL": "debug"})

    assert config.log_level == "DEBUG"


def test_load_config_rechaza_log_level_invalido() -> None:
    """Un LOG_LEVEL fuera del conjunto permitido debe fallar explícitamente."""
    with pytest.raises(ConfigError, match="LOG_LEVEL inválido"):
        load_config({"DISCORD_TOKEN": "t", "LOG_LEVEL": "VERBOSE"})


def test_load_config_respeta_command_prefix_personalizado() -> None:
    """Un COMMAND_PREFIX personalizado debe usarse tal cual."""
    config = load_config({"DISCORD_TOKEN": "t", "COMMAND_PREFIX": "?"})

    assert config.command_prefix == "?"


def test_load_config_usa_el_punto_si_command_prefix_esta_vacio() -> None:
    """`COMMAND_PREFIX=` vacío no deja al bot sin comandos de texto."""
    config = load_config({"DISCORD_TOKEN": "t", "COMMAND_PREFIX": "  "})

    assert config.command_prefix == "."


def test_load_config_sin_canales_de_casino_permite_cualquier_canal() -> None:
    """Sin CASINO_CHANNEL_IDS la lista queda vacía (sin restricción)."""
    assert load_config({"DISCORD_TOKEN": "t"}).casino_channel_ids == frozenset()


def test_load_config_lee_canales_de_casino_separados_por_comas() -> None:
    config = load_config({"DISCORD_TOKEN": "t", "CASINO_CHANNEL_IDS": " 123, 456 ,"})

    assert config.casino_channel_ids == frozenset({123, 456})


def test_load_config_rechaza_canales_de_casino_no_numericos() -> None:
    with pytest.raises(ConfigError, match="CASINO_CHANNEL_IDS"):
        load_config({"DISCORD_TOKEN": "t", "CASINO_CHANNEL_IDS": "#casino"})
