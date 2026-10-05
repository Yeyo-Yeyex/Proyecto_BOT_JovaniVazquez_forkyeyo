"""Reglas de la lista de tareas del servidor (`lista`).

Aquí vive lo que no depende de Discord: leer la prioridad del texto que
escribe el usuario, validar la tarea y ordenar la lista. El cog
(`bot.cogs.todo`) solo adapta esto a mensajes y menús.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

#: Tareas pendientes como mucho por servidor. Es el máximo de opciones de un
#: menú de Discord, y así el menú para tachar siempre las enseña todas.
MAX_TASKS = 25
#: Largo máximo de una tarea. Una opción de menú admite 100 caracteres, y con
#: 25 tareas el embed sigue lejos de su límite de 4096.
MAX_TASK_LENGTH = 100


class Priority(IntEnum):
    """Prioridad de una tarea; el valor menor sale antes en la lista.

    Los emojis no se distinguen solo por el color (rojo/verde), para que se
    lean igual con daltonismo.
    """

    ALTA = 0
    MEDIA = 1
    BAJA = 2

    @property
    def emoji(self) -> str:
        """Icono que acompaña a la tarea en la lista y en el menú."""
        return {Priority.ALTA: "🔥", Priority.MEDIA: "📌", Priority.BAJA: "💤"}[self]

    @property
    def label(self) -> str:
        """Nombre en minúsculas, como lo escribe la gente."""
        return self.name.lower()


#: Prioridad cuando no se indica ninguna.
DEFAULT_PRIORITY = Priority.MEDIA

# «prioridad alta», «prio baja»… en cualquier punto del texto. Se pide la
# palabra «prioridad» delante para no confundir «comprar una mesa alta».
_PRIORITY_RE = re.compile(r"\s*\b(?:prioridad|prio)\s*:?\s*(alta|media|baja)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Task:
    """Tarea pendiente guardada.

    Attributes:
        id: Identificador estable (autoincremental en la base de datos).
        author_id: Quién la apuntó; puede tacharla además de los administradores.
    """

    id: int
    guild_id: int
    author_id: int
    text: str
    priority: Priority
    created_at: float


class TaskError(ValueError):
    """La tarea no se puede apuntar; el mensaje se enseña tal cual al usuario."""


def parse_task(raw: str, priority: Priority | None = None) -> tuple[str, Priority]:
    """Separa el texto de la tarea y su prioridad.

    Args:
        raw: Lo que escribió el usuario, p. ej. «arreglar purge prioridad alta».
        priority: Prioridad elegida aparte (el desplegable de `/lista`). Si
            llega, manda sobre la escrita en el texto.

    Returns:
        `(texto, prioridad)` con el texto ya limpio de la marca de prioridad.

    Raises:
        TaskError: Si el texto queda vacío o es demasiado largo.
    """
    found: Priority | None = None
    match = _PRIORITY_RE.search(raw)
    if match is not None:
        found = Priority[match.group(1).upper()]
        raw = raw[: match.start()] + " " + raw[match.end() :]
    text = " ".join(raw.split())
    if not text:
        raise TaskError("Escribe qué hay que hacer: `lista arreglar el purge prioridad alta`.")
    if len(text) > MAX_TASK_LENGTH:
        raise TaskError(f"Demasiado larga: como mucho {MAX_TASK_LENGTH} caracteres.")
    # Sin `or`: `Priority.ALTA` vale 0 y contaría como «no hay prioridad».
    if priority is not None:
        return text, priority
    return text, found if found is not None else DEFAULT_PRIORITY


def sort_tasks(tasks: list[Task]) -> list[Task]:
    """Ordena por prioridad (alta primero) y, a igual prioridad, por antigüedad."""
    return sorted(tasks, key=lambda task: (task.priority, task.created_at, task.id))


def can_complete(task: Task, user_id: int, *, is_admin: bool) -> bool:
    """Si `user_id` puede tachar la tarea: quien la apuntó o un administrador."""
    return is_admin or task.author_id == user_id
