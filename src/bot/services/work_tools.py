"""Herramientas de curro: objetos de la tienda que ayudan en el minijuego de `pala`.

Se compran en el pasillo 🛠️ Ferretería del curro de la `tienda`
(`bot.services.shop_catalog`, con la misma clave) y no se usan desde la
mochila: con tenerlas basta. Al fichar, el cog de trabajo pregunta a la
tienda qué claves tiene el miembro (`bot.cogs.shop.owned_keys`) y
`perks_for` junta el efecto de las que sirven para su oficio y su minijuego.

Efectos (`Tool`):

- **Tiempo** (`extra_time`): el reloj del minijuego dura un poco más. Se suman.
- **Un fallo gratis** (`retry`): la primera equivocación del turno no cuenta y
  se repite la jugada.
- **Seguro** (`insured`): al cavar, romper una tubería no resta.
- **Chuleta** (`fifty`): en los diálogos se tacha una respuesta mala.

No mueven dinero: suben la nota y, con ella, el sueldo (del 70 % al 130 % de
la base, `bot.services.work.shift_pay`). Un fallo gratis vale como mucho una
ronda: unos pocos puntos de nota, un 3–9 % del bruto. El precio es el
sumidero que lo compensa.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from bot.services.work import Mechanic


@dataclass(frozen=True, slots=True)
class Tool:
    """Una herramienta de curro.

    Attributes:
        key: Clave del artículo en `bot.services.shop_catalog.CATALOG`.
        emoji: El mismo emoji que el artículo.
        name: Nombre corto para el panel del turno.
        job: Oficio en el que sirve (`None`: en todos).
        mechanics: Minijuegos en los que sirve (vacío: en todos los del oficio).
        extra_time: Tiempo extra, en tanto por uno.
        retry: Si perdona el primer fallo del turno.
        insured: Si romper algo al cavar no resta.
        fifty: Si tacha una respuesta mala en los diálogos.
    """

    key: str
    emoji: str
    name: str
    job: str | None = None
    mechanics: frozenset[Mechanic] = frozenset()
    extra_time: float = 0.0
    retry: bool = False
    insured: bool = False
    fifty: bool = False

    def applies(self, job: str, mechanic: Mechanic) -> bool:
        """Si sirve en ese oficio y ese minijuego."""
        if self.job is not None and self.job != job:
            return False
        return not self.mechanics or mechanic in self.mechanics

    @property
    def effect(self) -> str:
        """Lo que hace, en pocas palabras (para la tienda y el panel)."""
        parts = []
        if self.extra_time:
            parts.append(f"+{round(self.extra_time * 100)} % de tiempo")
        if self.retry:
            parts.append("un fallo gratis por turno")
        if self.insured:
            parts.append("romper algo al cavar no resta")
        if self.fifty:
            parts.append("tacha una respuesta mala en cada pregunta")
        return ", ".join(parts)


_DIG = frozenset({Mechanic.DIG})
_TALK = frozenset({Mechanic.DIALOGUE})

#: Todas las herramientas, en el orden del pasillo.
TOOLS: tuple[Tool, ...] = (
    Tool("reloj_fichar", "⌚", "Reloj de fichar", extra_time=0.10),
    Tool("casco_linterna", "⛑️", "Casco con linterna", job="obra", extra_time=0.15),
    Tool("chaleco_reflectante", "🦺", "Chaleco reflectante", job="obra", retry=True),
    Tool("seguro_rc", "📄", "Seguro de responsabilidad civil", job="obra", mechanics=_DIG,
         insured=True),
    Tool("zuecos", "👞", "Zuecos antideslizantes", job="hosteleria", extra_time=0.15),
    Tool("libreta_comandas", "🗒️", "Libreta de comandas", job="hosteleria", retry=True),
    Tool("hoja_reclamaciones", "📋", "Hoja de reclamaciones", job="hosteleria",
         mechanics=_TALK, fifty=True),
    Tool("cubo_engrudo", "🪣", "Cubo de engrudo", job="politica", extra_time=0.15),
    Tool("pinganillo", "🎧", "Pinganillo del portavoz", job="politica", retry=True),
    Tool("argumentario", "📒", "Argumentario de Ferraz", job="politica", mechanics=_TALK,
         fifty=True),
    Tool("fonendo", "🩺", "Fonendoscopio", job="sanidad", extra_time=0.15),
    Tool("chuleta_triaje", "🧾", "Chuleta de bolsillo", job="sanidad", retry=True),
    Tool("vademecum", "📘", "Vademécum", job="sanidad", mechanics=_TALK, fifty=True),
    Tool("segunda_pantalla", "🖥️", "Segunda pantalla", job="oficina", extra_time=0.15),
    Tool("tecla_deshacer", "⌨️", "Teclado con Ctrl+Z", job="oficina", retry=True),
    Tool("ia_premium", "🤖", "Suscripción a la IA de moda", job="oficina", mechanics=_TALK,
         fifty=True),
)  # fmt: skip
TOOL_BY_KEY: dict[str, Tool] = {tool.key: tool for tool in TOOLS}
TOOL_KEYS = frozenset(TOOL_BY_KEY)


@dataclass(frozen=True, slots=True)
class Perks:
    """Lo que dan juntas las herramientas que sirven en un turno.

    Attributes:
        tools: Las herramientas que cuentan.
        extra_time: Tiempo extra total, en tanto por uno.
        retries: Fallos gratis del turno.
        insured: Si romper algo al cavar no resta.
        fifty: Si se tacha una respuesta mala en los diálogos.
    """

    tools: tuple[Tool, ...] = ()
    extra_time: float = 0.0
    retries: int = 0
    insured: bool = False
    fifty: bool = False

    @property
    def summary(self) -> str:
        """`⌚⛑️ +25 % de tiempo · 🛟 1 fallo gratis`, o `""` sin herramientas."""
        if not self.tools:
            return ""
        parts = []
        if self.extra_time:
            parts.append(f"+{round(self.extra_time * 100)} % de tiempo")
        if self.retries:
            parts.append(f"🛟 {self.retries} fallo gratis")
        if self.insured:
            parts.append("📄 asegurado")
        if self.fifty:
            parts.append("✂️ una respuesta tachada")
        return "".join(t.emoji for t in self.tools) + " " + " · ".join(parts)


NO_PERKS = Perks()


def perks_for(job: str, mechanic: Mechanic, owned: Collection[str]) -> Perks:
    """Junta el efecto de las herramientas de `owned` que sirven en este turno."""
    tools = tuple(t for t in TOOLS if t.key in owned and t.applies(job, mechanic))
    if not tools:
        return NO_PERKS
    return Perks(
        tools=tools,
        extra_time=sum(t.extra_time for t in tools),
        retries=sum(1 for t in tools if t.retry),
        insured=any(t.insured for t in tools),
        fifty=any(t.fifty for t in tools),
    )
