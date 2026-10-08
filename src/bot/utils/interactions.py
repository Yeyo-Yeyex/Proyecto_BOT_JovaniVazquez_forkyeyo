"""Responder a botones, menús y formularios sin pasarse del plazo de Discord.

Discord da 3 segundos para contestar a una interacción. Si no llega nada, el
botón se queda «pensando» y acaba en «Esta interacción ha fallado», aunque el
bot termine el trabajo después. Lo que tarda de verdad no es Python: es la base
de datos (sobre todo esperando a otra escritura), dibujar una imagen o hablar
con otra API.

La regla (ver «Botones rápidos» en CLAUDE.md y la Biblia):

1. Si el botón solo cambia algo en memoria (subir la apuesta, cambiar de
   pestaña con datos ya cargados), se contesta directamente con
   `interaction.response.edit_message`.
2. Si antes de poder enseñar el resultado hay que tocar la base de datos,
   dibujar o llamar a otra API, se llama primero a `ack`. Desde ese momento
   la interacción está contestada y el resto se envía con `edit` y `notify`,
   que eligen solos el camino correcto.

`edit` y `notify` también sirven sin `ack`: si la interacción aún no tiene
respuesta, la usan; si ya la tiene, editan o mandan un mensaje de seguimiento.
"""

from __future__ import annotations

from typing import Any

import discord


async def ack(interaction: discord.Interaction, *, new_message: bool = False) -> None:
    """Acepta el clic ya, antes del trabajo lento.

    En un botón o un menú, `defer()` no enseña nada: el botón deja de girar y el
    mensaje se actualiza cuando llegue `edit`. En un formulario (modal) que se
    abrió desde un mensaje pasa lo mismo. No hace nada si ya se respondió.

    No se puede usar antes de abrir un formulario: `send_modal` tiene que ser la
    primera respuesta.

    Args:
        new_message: `True` si la respuesta no cambia el mensaje del botón sino
            que es un mensaje privado nuevo (abrir tu propio panel, unas
            estadísticas). Discord enseña «pensando…» en privado y `edit`
            rellena ese mensaje; `edit_original_response` también apunta a él,
            así que se puede guardar la interacción para editarlo después.
    """
    if interaction.response.is_done():
        return
    if new_message:
        await interaction.response.defer(ephemeral=True, thinking=True)
    else:
        await interaction.response.defer()


async def edit(interaction: discord.Interaction, **kwargs: Any) -> None:
    """Cambia el mensaje del botón pulsado, se haya aceptado ya o no.

    Tras `ack(new_message=True)`, rellena el mensaje privado nuevo.


    Acepta lo mismo que `InteractionResponse.edit_message` y
    `Interaction.edit_original_response` (`content`, `embed`, `attachments`,
    `view`…).
    """
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    else:
        await interaction.response.edit_message(**kwargs)


async def notify(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    ephemeral: bool = True,
    **kwargs: Any,
) -> None:
    """Manda un mensaje aparte (por defecto, que solo ve quien pulsa).

    Sirve para errores («no tienes saldo») y avisos, tanto antes como después de
    `ack`. Acepta lo mismo que `send_message` (`embed`, `view`, `file`…).
    """
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=ephemeral, **kwargs)
    else:
        await interaction.response.send_message(content, ephemeral=ephemeral, **kwargs)
