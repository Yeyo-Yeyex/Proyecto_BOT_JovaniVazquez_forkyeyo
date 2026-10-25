"""Muestras de la carta de `/renta`: una declaración por caso que se puede ver.

Sirve para revisar la escena (`assets/renta/escena.html`) a ojo y a las pruebas
(`tests/unit/test_renta_scene.py`), que importan `samples()`. Cada muestra sale de una
declaración hecha con las funciones reales (`taxes.week_return` y
`renta_report.card_state`), no de cifras escritas a mano.

Uso (desde la raíz del repo, con PYTHONPATH=src):

    python docs/muestras_renta.py <carpeta>

Deja un PNG por muestra en `<carpeta>` y dice lo que tardó cada una.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from bot.repositories.economy import PAYMENT_PAID, PAYMENT_PENDING, IrpfReturn
from bot.services.economy import IrpfStatement
from bot.services.renta_report import EXPIRED, FILED, OWED, PAID, card_state
from bot.services.renta_scene import RentaScene
from bot.services.taxes import (
    INCOME_BUSINESS,
    INCOME_IMAGE,
    INCOME_PRIZE,
    INCOME_PROPERTY,
    INCOME_WORK,
    PropertyWeek,
    WeekIncome,
    week_return,
)

WEEK = date(2026, 10, 19)


def _statement(
    income: WeekIncome, withheld_by: dict[str, int], *, old: bool = False, **filed: Any
) -> IrpfStatement:
    arguments = {"withheld": sum(withheld_by.values()), "donated": filed.pop("donated", 0)}
    detail = week_return(income, **arguments)
    values: dict[str, Any] = {
        "user_id": 1,
        "base": detail.tax.base,
        "quota": detail.tax.quota,
        "deductions": detail.deductions,
        "withheld": detail.withheld,
        "result": detail.result,
        "rate": detail.tax.rate,
        "refund": max(0, -detail.result),
        **filed,
    }
    return IrpfStatement(
        week=WEEK,
        filed=IrpfReturn(**values),
        detail=None if old else detail,
        income=WeekIncome() if old else income,
        withheld_by={} if old else withheld_by,
        opened=True,
        arguments={} if old else arguments,
    )


MIXED = WeekIncome(work=200_000, prizes=50_000, gambling=35_000)
MIXED_HELD = {INCOME_WORK: 24_000, INCOME_PRIZE: 9_000, "juego": 6_650}


def samples() -> dict[str, dict[str, Any]]:
    """Los estados de la carta, por nombre de muestra."""
    landlord = WeekIncome(
        work=120_000,
        business=40_000,
        properties=(
            PropertyWeek(ref="atico", income=30_000, finance=8_000, costs=4_000, reduction=0.5),
            PropertyWeek(ref="bajo", income=3_000, costs=25_000),
        ),
        prizes=12_000,
        gambling=-4_000,
        image=6_000,
    )
    landlord_held = {
        INCOME_WORK: 15_000,
        INCOME_BUSINESS: 6_000,
        INCOME_PROPERTY: 3_000,
        INCOME_PRIZE: 1_500,
        INCOME_IMAGE: 1_440,
        "juego": 900,
    }
    return {
        "a_pagar_pendiente": card_state(
            _statement(MIXED, MIXED_HELD, payment=PAYMENT_PENDING), "Diego"
        ),
        "a_pagar_pagada": card_state(
            _statement(MIXED, MIXED_HELD, payment=PAYMENT_PAID, donated=1_202), "Diego", PAID
        ),
        "con_deuda": card_state(
            _statement(MIXED, MIXED_HELD, payment=PAYMENT_PAID, debt=5_000),
            "Guille 🎸 el del ático",
            OWED,
        ),
        "a_devolver": card_state(_statement(WeekIncome(work=60_000), {INCOME_WORK: 9_000}), "Dani"),
        "presentada": card_state(
            _statement(
                WeekIncome(work=60_000, gambling=12_000), {INCOME_WORK: 9_000, "juego": 2_280}
            ),
            "Dani",
            FILED,
        ),
        "caducada": card_state(
            _statement(WeekIncome(prizes=30_000), {INCOME_PRIZE: 5_700}), "Yeyo", EXPIRED
        ),
        "a_cero": card_state(_statement(WeekIncome(prizes=2_000), {}), "Jovani"),
        "muchos_conceptos": card_state(_statement(landlord, landlord_held), "Casero Máximo"),
        "antigua": card_state(_statement(MIXED, MIXED_HELD, old=True), "Diego"),
    }


async def main(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    scene = RentaScene()
    states = samples()
    try:
        await scene.still(states["a_pagar_pendiente"])  # calienta Node
        for name, state in states.items():
            start = time.perf_counter()
            png = await scene.still(state)
            took = time.perf_counter() - start
            if png is None:
                print(f"{name}: no se pudo pintar")
                continue
            (folder / f"{name}.png").write_bytes(png)
            print(f"{name}: {took * 1000:.0f} ms, {len(png) / 1024:.0f} KB")
    finally:
        await scene.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "muestras_renta")))
