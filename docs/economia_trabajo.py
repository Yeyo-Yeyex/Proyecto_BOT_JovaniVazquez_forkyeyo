"""Datos del análisis de la economía del trabajo, calculados con el código real.

Uso, desde la raíz del repo: `python docs/economia_trabajo.py` (imprime JSON).
"""

import json
import sys

sys.path.insert(0, "src")
from bot.services.economy import IMV_WORK_EXEMPT, imv_after_work
from bot.services.taxes import compute_payslip, payroll_rates
from bot.services.work_catalog import JOBS

OLD = {
    "obra": [150, 250, 400, 715, 1300],
    "hosteleria": [120, 210, 360, 650, 1300],
    "politica": [45, 225, 480, 975, 2500],
    "sanidad": [135, 250, 460, 780, 1400],
    "oficina": [75, 250, 480, 910, 1800],
}
IMV_MAX = 1500


def old_net(gross, recent):
    # Reproduce la escala anterior (10 Y$/€): payroll_rates con el bruto ×10.
    proj = (recent + gross) * 365 // 30
    r = payroll_rates(proj * 10)
    return gross - round(gross * r.ss_worker) - min(round(gross * r.irpf), gross)


def new_net(gross, recent):
    return compute_payslip(gross, recent).net


out = {}
# 1) Neto por turno de nota 50 a jornada completa, según la base del puesto 1.
bases = list(range(200, 3201, 100))
out["base_curve"] = {
    "bases": bases,
    "net": [new_net(b, b * 4 * 30 - b) for b in bases],
    "net_one": [new_net(b, b * 29) for b in bases],
}
# Base mínima: neto >= IMV máx.
opt = next(b for b in range(100, 5000, 10) if new_net(b, b * 29) >= IMV_MAX * 1.05)
out["base_min"] = opt
# 2) Valor de cada acción en tiradas de 100.
cel_old = old_net(135, 135 * 29)
cel_new = new_net(1750, 1750 * 29)
cons_new = new_net(25000, 25000 * 29)
out["actions"] = [
    ["IMV, primer día", 500 / 100],
    ["IMV, racha máxima", 15.0],
    ["Turno de celador (antes)", round(cel_old / 100, 1)],
    ["Turno de celador (ahora)", round(cel_new / 100, 1)],
    ["Turno de consejero (ahora)", round(cons_new / 100, 1)],
]
# 3) Día de un celador según turnos: nómina neta + IMV a racha máxima.
days = []
for n in range(0, 9):
    g = 1750
    net = sum(new_net(g, g * n * 30 - g) for _ in range(n)) if n else 0
    weekly = net * 7
    imv = imv_after_work(IMV_MAX, weekly)
    g0 = 135
    net0 = sum(old_net(g0, g0 * n * 30 - g0) for _ in range(n)) if n else 0
    # Antes: escala del IMV igual a la de las nóminas (exento 1.150 Y$ a la semana, 0,5 por Y$).
    exc = max(0, net0 * 7 - 1150)
    imv0 = max(300, IMV_MAX - -(-int(exc * 0.5) // 7))
    days.append(
        {"turnos": n, "nomina": net, "imv": imv, "total": net + imv, "total_antes": net0 + imv0}
    )
out["days"] = days
# 4) IRPF de la jornada completa en sanidad: antes, sueldos nuevos a 10 Y$/€ y a 100 Y$/€.
lad_old = OLD["sanidad"]
lad_new = [p.base_pay for p in JOBS[3].positions]


def irpf_rate(gross, scale):
    proj = gross * 4 * 365
    return round(payroll_rates(proj * 100 // scale).irpf * 100, 1)


out["irpf"] = {
    "labels": [p.title for p in JOBS[3].positions],
    "antes": [irpf_rate(g, 10) for g in lad_old],
    "sin_escala": [irpf_rate(g, 10) for g in lad_new],
    "ahora": [irpf_rate(g, 100) for g in lad_new],
}
# 5) Y$ por minuto de atención.
out["per_min"] = [
    ["IMV (5 s, una vez al día)", round(IMV_MAX / (5 / 60))],
    ["Turno de celador (≈1 min)", cel_new],
    ["Turno de celador antes", cel_old],
    ["Casino, tiradas de 100 (≈6 s)", -round(100 * 0.04 * 10)],
]
# 6) Escalera de sueldos.
out["ladder"] = [
    {
        "job": j.name,
        "emoji": j.emoji,
        "old": OLD[j.key],
        "new": [p.base_pay for p in j.positions],
        "net_one": [new_net(p.base_pay, p.base_pay * 29) for p in j.positions],
        "old_one": [old_net(g, g * 29) for g in OLD[j.key]],
        "net_full": [new_net(p.base_pay, p.base_pay * 4 * 30 - p.base_pay) for p in j.positions],
        "irpf": [irpf_rate(p.base_pay, 100) for p in j.positions],
        "eur_year": [round(p.base_pay * 4 * 365 / 100) for p in j.positions],
    }
    for j in JOBS
]
out["imv_exempt"] = IMV_WORK_EXEMPT
print(json.dumps(out, ensure_ascii=False))
