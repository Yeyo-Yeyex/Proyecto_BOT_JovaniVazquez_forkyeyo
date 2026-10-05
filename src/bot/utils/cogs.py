"""Buscar un cog cargado desde otro cog sin depender del orden de carga.

Varios cogs llaman a otros a través de funciones puente (`achievements.track`,
`renta.remind`, `shop.xp_multiplier`…). Esas funciones no pueden comprobar el
cog con `isinstance(cog, Clase)`: `load_extension` de discord.py vuelve a
ejecutar el módulo de la extensión aunque otro cog ya lo hubiera importado, así
que quien lo importó antes ve una clase `Clase` distinta de la del cog cargado.
El `isinstance` da `False`, la función puente cree que el cog no está y no hace
nada, sin ningún error. Así se perdieron todos los logros del casino.
"""

from __future__ import annotations

from typing import cast

from discord.ext import commands


def find_cog[CogT: commands.Cog](bot: commands.Bot, cls: type[CogT]) -> CogT | None:
    """El cog cargado de la clase `cls`, o `None` si no está cargado.

    Compara la clase por módulo y nombre, no por identidad, para que
    funcione aunque `cls` venga de una copia anterior del módulo.

    Args:
        bot: Cliente donde buscar.
        cls: Clase del cog; su nombre de cog (`__cog_name__`) es el que se busca.
    """
    cog = bot.get_cog(cls.__cog_name__)
    if cog is None:
        return None
    kind = type(cog)
    if kind.__module__ != cls.__module__ or kind.__qualname__ != cls.__qualname__:
        return None
    return cast(CogT, cog)
