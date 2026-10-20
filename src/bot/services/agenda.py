"""El día en franjas: la moneda de tiempo que comparten la `pala` y el negocio.

El día tiene `FRANJAS_DIA` franjas (unas 16 horas despierto, en bloques de dos). La
jornada del curro reserva las suyas (`Contract.jornada`: 4, o menos con la reducción
de jornada) aunque no se fiche: faltar al trabajo no deja tiempo para el negocio. Lo
que se hace fuera de la jornada gasta franjas libres:

- una hora extra (legal o en B), un turno de estudio de la oposición: 1 franja;
- una guardia: `GUARD_FRANJAS` (es un turno doble);
- un turno en tu negocio: 1 franja.

Lo libre es lo que queda. Sin franjas libres no hay horas extra, B, guardias, estudio
ni turnos del negocio; los turnos de jornada nunca se bloquean (ya están reservados).
El día es el de Canarias (`levels.local_day`), como el resto de la `pala`.

Lógica pura: `Agenda` se monta con lo que guardan el contrato (`Contract.franjas`) y el
negocio (`Business.franjas_used`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Franjas de un día.
FRANJAS_DIA = 8
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
        """Franjas libres (nunca menos de 0)."""
        return max(0, FRANJAS_DIA - self.used)

    def fits(self, franjas: int) -> bool:
        """Si caben `franjas` más fuera de la jornada."""
        return self.free >= franjas

    def slots(self) -> list[str]:
        """Las `FRANJAS_DIA` franjas en orden, para dibujarlas.

        Primero la jornada (`hecha` lo fichado y `jornada` lo reservado sin fichar),
        luego lo de fuera de ella, el negocio y lo libre. Si se pasa del día (no
        debería), se corta.
        """
        out = ["hecha"] * self.ordinary + ["jornada"] * max(0, self.jornada - self.ordinary)
        for kind in (EXTRA, GUARD, STUDY):
            out += [kind] * self.outside.get(kind, 0)
        out += ["negocio"] * self.business
        out += ["libre"] * (FRANJAS_DIA - len(out))
        return out[:FRANJAS_DIA]
