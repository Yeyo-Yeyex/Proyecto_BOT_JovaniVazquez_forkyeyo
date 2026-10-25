"""Cuánto dinero da la `pala` al día con las franjas de noche (9.19.0), con el código real.

Uso, desde la raíz del repo: `python docs/noche_pala.py` (imprime una tabla).

Hasta la 9.18 el día tenía 8 franjas y era un muro: pasadas, no se fichaba nada. Antes de
la 8.14.0 no había franjas y el B era infinito: el café costaba 25 Y$ y la batería no
frenaba. Desde la 9.19.0 se pueden trasnochar 4 más, pero en ellas la Inspección pilla
más el B (`work.inspection_chance`) y lo cobra doble (`work.inspection_surcharge`), y cada
una cuesta familia (`work.FAMILY_LATE`). El café vale unas 8 veces más (110 y 200 Y$).
Este script mide si eso da más dinero que el muro.

Puesto 1 de obra (base 1.800 Y$), nota 75, 28 días, 200 semillas. Estilos:

- **normal**: los 4 turnos de jornada.
- **grinder**: ficha todo lo que le dejan las franjas (jornada, 2 extras legales a la
  semana y el resto en B) y se toma un barraquito cada vez que baja de 20 de batería
  (para no ir reventado ni tener accidentes); para si hay intervención familiar.
- **prudente**: como el grinder, pero de noche solo echa el B mientras le salga a cuenta.

Cuenta lo que llega al bolsillo: lo legal neto de IRPF y cotización (el tipo de una
semana a su ritmo), el B entero, menos las multas de la Inspección y los cafés.

Resultado (25/10/2026, Y$ al día; turnos al día entre paréntesis):

- normal: 7.742 (4,0), igual con las reglas de antes y con las de ahora;
- grinder con el muro de 8 franjas y el café a 25 Y$: 12.208 (6,4);
- grinder con 12 franjas y el café a 25 Y$: 12.287 (8,6), un 0,6 % más;
- grinder con 12 franjas y el café nuevo (214 Y$ con IGIC): 10.965 (8,6), un 10 % menos;
- prudente con 12 franjas y el café nuevo: 11.550 (7,4), un 5 % menos.

En un puesto medio (base 16.600, cambiando `BASE`), el café fijo apenas pesa y quien lo
apura todo saca lo mismo que con el muro: 86.531 frente a 86.936 (−0,5 %). Lo que frena la
noche es el riesgo del B, no el café.

Con un riesgo de noche del 25 al 70 % y la multa de día (20 %) el grinder sacaba 13.888, un
14 % más que con el muro: por eso de noche la multa es doble. La familia es lo que más
frena a quien lo apura: 11 o 12 de cada 28 días tiene intervención familiar y no hay extras.
"""

import random
import sys
from datetime import datetime

sys.path.insert(0, "src")
from bot.services.agenda import FRANJAS_DIA, FRANJAS_MAX
from bot.services.levels import TIMEZONE, local_day
from bot.services.taxes import WeekIncome, annual_eur, igic, payroll_rates, week_tax
from bot.services.work import (
    ACCIDENT_TIRED,
    ACCIDENT_ZOMBIE,
    BATTERY_MAX,
    OVERTIME_PAY,
    SICK_LEAVE_SECONDS,
    TIRED,
    Contract,
    ShiftKind,
    battery_now,
    change_family,
    family_breakdown,
    inspection_chance,
    inspection_surcharge,
    next_shift_kind,
    roll_over_day,
    settle,
    shift_cost,
)
from bot.services.work_catalog import COFFEE_BY_KEY

BASE, SCORE = 1_800, 75
FACTOR = 0.7 + 0.6 * SCORE / 100
DAYS, SEEDS = 28, 200
#: Lunes 5 de octubre de 2026, 0:00 en Canarias.
START = datetime(2026, 10, 5, tzinfo=TIMEZONE).timestamp()
BARRAQUITO = COFFEE_BY_KEY["barraquito"]
#: Lo que costaba el barraquito antes de la 9.19.0, con IGIC.
OLD_COFFEE = 25 + igic(25)


def net_share(daily_gross: float) -> float:
    """Parte del bruto legal que llega al bolsillo con una semana de ese ritmo."""
    weekly = round(daily_gross * 7)
    ss = payroll_rates(annual_eur(weekly)).ss_worker
    rate = week_tax(WeekIncome(work=weekly, social=round(weekly * ss))).rate
    return 1 - ss - rate


def run(style: str, franjas: int, coffee: float, seed: int) -> dict[str, float]:
    """Un jugador durante `DAYS` días con `franjas` al día y el café a `coffee` Y$."""
    rng = random.Random(seed)
    contract = Contract(job="obra", level=1, battery_at=START, family_at=START)
    s = {"legal": 0.0, "black": 0.0, "fines": 0.0, "coffee": 0.0, "shifts": 0, "intervened": 0}
    pay = BASE * FACTOR
    for day in range(DAYS):
        used, intervened = 0, False
        for i in range(64):  # cada 15 minutos de 8:00 a 23:45: todo en el mismo día
            now = START + day * 86_400 + (8 + i / 4) * 3600
            roll_over_day(contract, now)
            if contract.sick_until > now or used >= franjas:
                continue
            kind = next_shift_kind(contract, now)
            if style == "normal" and kind is not ShiftKind.ORDINARY:
                continue
            if kind is not ShiftKind.ORDINARY and contract.no_extras_day == local_day(now).isoformat():
                intervened = True
                continue
            late = max(0, used + 1 - FRANJAS_DIA)
            chance, surcharge = inspection_chance(late), inspection_surcharge(late)
            if style == "prudente" and late and kind is ShiftKind.BLACK:
                if 1 - chance * (1 + surcharge) <= 0:
                    continue
            cost = shift_cost(kind)
            settle(contract, now)
            while contract.battery < TIRED:
                contract.battery = min(float(BATTERY_MAX), contract.battery + BARRAQUITO.battery)
                s["coffee"] += coffee
            battery = battery_now(contract, now)
            if kind is ShiftKind.BLACK:
                s["black"] += pay
                if rng.random() < chance:
                    s["fines"] += pay * (1 + surcharge)
            else:
                s["legal"] += pay * (OVERTIME_PAY if kind is ShiftKind.EXTRA else 1)
            s["shifts"] += 1
            used += 1
            contract.battery = battery - cost
            family = sum(v for _, v in family_breakdown(kind, now, late=1 if late else 0))
            change_family(contract, family, now)
            contract.shifts_today += 1
            if kind is not ShiftKind.ORDINARY:
                contract.extras_week += 1
            contract.shift_day = local_day(now).isoformat()
            chance = ACCIDENT_ZOMBIE if battery < 0 else ACCIDENT_TIRED if battery < TIRED else 0
            if rng.random() < chance:
                contract.sick_until = now + SICK_LEAVE_SECONDS
        s["intervened"] += intervened
    s["net"] = s["legal"] * net_share(s["legal"] / DAYS) + s["black"] - s["fines"] - s["coffee"]
    return s


def main() -> None:
    new_coffee = BARRAQUITO.price + igic(BARRAQUITO.price)
    print(f"Obra P1 (base {BASE}), nota {SCORE}, {DAYS} días, {SEEDS} semillas. Por día:")
    cases = [
        ("normal", "normal", FRANJAS_DIA, OLD_COFFEE),
        ("grinder, muro de 8, café a 25", "grinder", FRANJAS_DIA, OLD_COFFEE),
        ("grinder, 12 franjas, café a 25", "grinder", FRANJAS_MAX, OLD_COFFEE),
        (f"grinder, 12 franjas, café a {new_coffee}", "grinder", FRANJAS_MAX, new_coffee),
        (f"prudente, 12 franjas, café a {new_coffee}", "prudente", FRANJAS_MAX, new_coffee),
    ]
    for label, style, franjas, coffee in cases:
        runs = [run(style, franjas, coffee, seed) for seed in range(SEEDS)]

        def mean(key: str, runs: list[dict[str, float]] = runs) -> float:
            return sum(r[key] for r in runs) / len(runs)

        print(
            f"{label:34} neto {mean('net') / DAYS:6.0f} · turnos {mean('shifts') / DAYS:4.1f} · "
            f"multas {mean('fines') / DAYS:5.0f} · cafés {mean('coffee') / DAYS:5.0f} · "
            f"días con intervención {mean('intervened'):4.1f}"
        )


if __name__ == "__main__":
    main()
