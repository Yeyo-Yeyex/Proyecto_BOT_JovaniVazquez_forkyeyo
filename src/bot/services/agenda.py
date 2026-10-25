"""El día en franjas: la moneda de tiempo que comparten la `pala` y el negocio.

El día tiene `FRANJAS_DIA` franjas (unas 16 horas despierto, en bloques de dos). La
jornada del curro reserva las suyas (`Contract.jornada`: 4, o menos con la reducción
de jornada) aunque no se fiche: faltar al trabajo no deja tiempo para el negocio. Lo
que se hace fuera de la jornada gasta franjas libres:

- una hora extra (legal o en B), un turno de estudio de la oposición: 1 franja;
- una guardia: `GUARD_FRANJAS` (es un turno doble);
- un turno en tu negocio: 1 franja.

Lo libre es lo que queda. Acabadas las franjas del día se puede **trasnochar**: hay
`FRANJAS_NOCHE` más, robadas al sueño, y ninguna después (el día no da para más de 24
horas en bloques de dos). De noche se puede hacer lo mismo, pero la Inspección mira más el B
(`work.inspection_chance`) y, en la `pala`, la familia lo nota (`work.FAMILY_LATE`). Sin
franjas, ni de noche, no hay horas extra, B, guardias, estudio ni turnos del negocio; los
turnos de jornada nunca se bloquean (ya están reservados). El día es el de Canarias
(`levels.local_day`), como el resto de la `pala`.

Lógica pura: `Agenda` se monta con lo que guardan el contrato (`Contract.franjas`) y el
negocio (`Business.franjas_used`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Franjas de un día despierto.
FRANJAS_DIA = 8
#: Franjas de noche que se le pueden robar al sueño después de las del día (🌙 trasnochar).
FRANJAS_NOCHE = 4
#: Franjas que da de sí un día entero: después de estas no se ficha nada.
FRANJAS_MAX = FRANJAS_DIA + FRANJAS_NOCHE
#: Franjas que gasta una guardia (turno doble).
GUARD_FRANJAS = 2
#: Tramos de la jornada en franjas: completa, reducción de 1/4 y media jornada
#: (art. 37.6 ET: de 1/8 a 1/2 de la jornada).
JORNADAS: tuple[int, ...] = (4, 3, 2)
#: Nombre de cada tramo.
JORNADA_NAMES: dict[int, str] = {4: "Jornada completa", 3: "Reducción 3/4", 2: "Media jornada"}

#: Clases de franja fuera de la jornada, como se guardan en `Contract.franjas`.
EXTRA = "extra"
GUARD = "guardia"
STUDY = "estudio"
#: Cómo sale en `Agenda.slots` una franja de noche que aún no se ha usado.
NIGHT = "noche"


@dataclass(frozen=True, slots=True)
class Agenda:
    """Las franjas de hoy de un miembro.

    Attributes:
        jornada: Franjas que reserva el curro (0 sin curro o de excedencia).
        ordinary: Turnos de jornada ya fichados hoy. Cuentan aunque luego se pida la
            excedencia: lo trabajado no vuelve.
        outside: Franjas gastadas hoy en el curro fuera de la jornada, por clase
            (`EXTRA`, `GUARD`, `STUDY`).
        business: Turnos de hoy en el negocio.
    """

    jornada: int = 0
    ordinary: int = 0
    outside: dict[str, int] = field(default_factory=dict)
    business: int = 0

    @property
    def work(self) -> int:
        """Franjas del curro: la jornada (reservada o fichada) más lo de fuera de ella."""
        return max(self.jornada, self.ordinary) + sum(self.outside.values())

    @property
    def used(self) -> int:
        """Franjas usadas o reservadas."""
        return self.work + self.business

    @property
    def free(self) -> int:
        """Franjas libres del día, sin contar las de noche (nunca menos de 0)."""
        return max(0, FRANJAS_DIA - self.used)

    @property
    def night_free(self) -> int:
        """Franjas de noche que aún se pueden trasnochar."""
        return max(0, FRANJAS_MAX - max(self.used, FRANJAS_DIA))

    @property
    def left(self) -> int:
        """Todo lo que queda hoy: las libres del día más las de noche."""
        return self.free + self.night_free

    @property
    def next_night(self) -> int:
        """Qué franja de noche sería la siguiente (de 1 a `FRANJAS_NOCHE`), o 0 si es de día."""
        return max(0, self.used + 1 - FRANJAS_DIA)

    def fits(self, franjas: int) -> bool:
        """Si caben `franjas` más fuera de la jornada, trasnochando si hace falta."""
        return self.left >= franjas

    def late(self, franjas: int) -> int:
        """Cuántas de las próximas `franjas` caen de noche."""
        return max(0, self.used + franjas - FRANJAS_DIA) - max(0, self.used - FRANJAS_DIA)

    def slots(self) -> list[str]:
        """Las `FRANJAS_MAX` franjas en orden, para dibujarlas.

        Primero la jornada (`hecha` lo fichado y `jornada` lo reservado sin fichar),
        luego lo de fuera de ella, el negocio y lo libre: `"libre"` en el día y `NIGHT`
        en las de noche. Lo usado que pasa de las del día ocupa las de noche. Si se pasa
        del máximo (no debería), se corta.
        """
        out = ["hecha"] * self.ordinary + ["jornada"] * max(0, self.jornada - self.ordinary)
        for kind in (EXTRA, GUARD, STUDY):
            out += [kind] * self.outside.get(kind, 0)
        out += ["negocio"] * self.business
        out += ["libre" if i < FRANJAS_DIA else NIGHT for i in range(len(out), FRANJAS_MAX)]
        return out[:FRANJAS_MAX]
