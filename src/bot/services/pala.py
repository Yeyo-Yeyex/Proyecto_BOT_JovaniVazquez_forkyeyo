"""Casos de uso del trabajo (`pala`): contratar, fichar, cobrar, ascender y el café.

Junta las reglas (`bot.services.work`), el catálogo (`bot.services.work_catalog`),
los minijuegos (`bot.services.work_games`), la persistencia
(`bot.repositories.work`) y el dinero (`bot.services.economy`). No sabe nada de
Discord: el cog `bot.cogs.work` dibuja y llama a esto.

Todo el dinero pasa por `EconomyService`:

- Turno ordinario o extra: `pay_salary` (nómina con IRPF y Seguridad Social;
  los autónomos, solo IRPF).
- Turno en B, propinas al bolsillo y sobres: `pay_undeclared`. Si te pillan,
  `sanction` (lo cobrado más un 20 % de recargo) y `suspend_imv`.
- Cuota de autónomos: `charge_self_employed_fee`, una vez por semana.
- Baja por accidente: `pay_salary` con la prestación (tributa como
  rendimiento del trabajo, art. 17.1.b LIRPF).
- Café y formación: `purchase`, con IGIC general.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from bot.services.economy import EconomyService, InsufficientFundsError
from bot.services.levels import local_day
from bot.services.taxes import IGIC_GENERAL_RATE, Payslip, igic
from bot.services.work import (
    BATTERY_MAX,
    BATTERY_MIN,
    BATTERY_REGEN_PER_HOUR,
    EVENT_CHANCE,
    FAMILY_MAX,
    GOOD_SCORE,
    IMV_SUSPENSION_SECONDS,
    INSPECTION_CHANCE,
    INSPECTION_SURCHARGE,
    MAX_COFFEES,
    PERFORMANCE_MAX,
    PERFORMANCE_START,
    SELF_EMPLOYED_FEE,
    SICK_LEAVE_SECONDS,
    SICK_PAY_RATE,
    TIRED,
    Contract,
    Job,
    Mechanic,
    Position,
    ShiftKind,
    ShiftRecord,
    accident_chance,
    apply_performance,
    battery_now,
    family_cost,
    holiday,
    is_night,
    next_shift_kind,
    on_leave,
    performance_delta,
    promote,
    promotion_blockers,
    roll,
    roll_over_day,
    shift_cost,
    shift_pay,
    week_of,
)
from bot.services.work_catalog import (
    COFFEE_BY_KEY,
    EVENT_BY_KEY,
    JOB_BY_KEY,
    PARDON_CHANCE,
    RESIGN_EVENT,
    Event,
    events_for,
)
from bot.services.work_games import MiniGame, new_game

if TYPE_CHECKING:
    from bot.repositories.work import WorkRepository


class WorkError(Exception):
    """Algo que impide la acción; el mensaje es para el usuario."""


@dataclass(frozen=True, slots=True)
class Status:
    """Foto del contrato para el panel.

    Attributes:
        battery: Batería ahora mismo (con la recarga).
        next_kind: Qué sería el próximo turno.
        blockers: Lo que falta para el ascenso (vacío = ya toca).
        days_in_position: Días distintos fichando en el puesto.
        on_leave: Si vuelve de una excedencia.
    """

    contract: Contract
    job: Job
    position: Position
    battery: float
    next_kind: ShiftKind
    blockers: list[str]
    days_in_position: int
    trainings: frozenset[str]
    on_leave: bool
    now: float

    @property
    def promotion_ready(self) -> bool:
        """Si le toca la oferta de ascenso."""
        return not self.blockers

    @property
    def next_position(self) -> Position | None:
        """Puesto siguiente, si lo hay."""
        if self.contract.level >= self.job.top:
            return None
        return self.job.position(self.contract.level + 1)

    @property
    def sick(self) -> bool:
        """Si está de baja."""
        return self.contract.sick_until > self.now


@dataclass(slots=True)
class Shift:
    """Un turno en marcha: el minijuego y lo que se sabía al fichar."""

    job: Job
    position: Position
    kind: ShiftKind
    game: MiniGame
    battery_before: float
    tremors: bool
    returned_from_leave: bool


@dataclass(slots=True)
class ShiftOutcome:
    """Todo lo que ha pasado al cerrar un turno (para el resultado y los logros).

    Attributes:
        payslip: Nómina, si el turno fue declarado.
        black: Lo cobrado en negro (turno en B).
        caught: Si la Inspección te pilló en este turno.
        fine: Lo que te cobraron de multa.
        performance: `"warned"`, `"demoted"` o `None`.
        accident: Si hubo accidente laboral (te vas de baja).
        sick_pay: Prestación cobrada por la baja (bruto).
        intervention: Si la familia llegó a 0 en este turno.
        fee: Cuota de autónomos cobrada en este turno.
        event: Evento que sale, si sale.
        week_taxes: `(impuestos, neto)` de los últimos 7 días.
    """

    job: Job
    position: Position
    kind: ShiftKind
    score: int
    gross: int
    balance: int
    battery_before: float
    battery_after: float
    family_before: int
    family_after: int
    now: float
    shifts_today: int
    streak_days: int
    coffees: int
    game: MiniGame
    payslip: Payslip | None = None
    black: int = 0
    caught: bool = False
    fine: int = 0
    performance: str | None = None
    accident: bool = False
    sick_pay: int = 0
    intervention: bool = False
    fee: int = 0
    event: Event | None = None
    promotion_ready: bool = False
    week_taxes: tuple[int, int] = (0, 0)

    @property
    def night(self) -> bool:
        """Si fue de madrugada."""
        return is_night(self.now)

    @property
    def sunday(self) -> bool:
        """Si fue en domingo."""
        return local_day(self.now).weekday() == 6

    @property
    def holiday(self) -> str | None:
        """Festivo en el que se fichó, si lo era."""
        return holiday(local_day(self.now))

    @property
    def net(self) -> int:
        """Lo que llegó al bolsillo por el turno."""
        return (self.payslip.net if self.payslip else 0) + (0 if self.caught else self.black)


@dataclass(slots=True)
class EventResult:
    """Lo que pasa al elegir una opción de un evento."""

    event: Event
    text: str
    paid: Payslip | None = None
    black: int = 0
    caught: bool = False
    caught_by: str = ""
    pardoned: bool = False
    fine: int = 0
    demoted: bool = False
    stats: dict[str, int] = field(default_factory=dict)
    #: Evento que viene a continuación (la pregunta de la dimisión).
    follow_up: Event | None = None


class WorkService:
    """Casos de uso del trabajo.

    Args:
        repository: Persistencia del trabajo.
        economy: Única puerta al dinero.
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        rng: Azar de minijuegos, accidentes y eventos; inyectable en pruebas.
    """

    def __init__(
        self,
        repository: WorkRepository,
        economy: EconomyService,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.repository = repository
        self.economy = economy
        self._clock = clock
        self.rng = rng or random.Random()
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def now(self) -> float:
        """Hora actual según el reloj del servicio (epoch)."""
        return self._clock()

    def _lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            # Se quitan los que nadie usa para que el diccionario no crezca sin fin.
            for other, value in list(self._locks.items()):
                if not value.locked():
                    del self._locks[other]
            lock = self._locks[key] = asyncio.Lock()
        return lock

    # -- Consulta -----------------------------------------------------------------------

    async def status(self, guild_id: int, user_id: int) -> Status | None:
        """Estado del contrato, o `None` si no trabaja."""
        contract = await self.repository.contract(guild_id, user_id)
        if contract is None:
            return None
        return await self._status(guild_id, user_id, contract)

    async def _status(self, guild_id: int, user_id: int, contract: Contract) -> Status:
        now = self._clock()
        leave = on_leave(contract, now)
        roll_over_day(contract, now)
        job = JOB_BY_KEY[contract.job]
        days = await self.repository.days_in_position(
            guild_id, user_id, contract.job, contract.level, contract.position_since
        )
        trainings = await self.repository.trainings(guild_id, user_id)
        blockers = promotion_blockers(
            contract,
            job,
            days_in_position=days,
            trainings=trainings,
            today=local_day(now).isoformat(),
        )
        return Status(
            contract=contract,
            job=job,
            position=job.position(contract.level),
            battery=battery_now(contract, now),
            next_kind=next_shift_kind(contract, now),
            blockers=blockers,
            days_in_position=days,
            trainings=frozenset(trainings),
            on_leave=leave,
            now=now,
        )

    async def history(self, guild_id: int, user_id: int) -> dict[str, tuple[int, int]]:
        """`{oficio: (puesto en el que se quedó, puesto máximo)}`."""
        return await self.repository.history(guild_id, user_id)

    # -- Contratar ----------------------------------------------------------------------

    async def hire(self, guild_id: int, user_id: int, job_key: str) -> Status:
        """Firma (o cambia de) oficio. Se entra en el puesto en el que se dejó.

        La batería y la familia son de la persona, no del oficio: se conservan.

        Raises:
            WorkError: Si el oficio no existe o ya trabaja en él.
        """
        job = JOB_BY_KEY.get(job_key)
        if job is None:
            raise WorkError("Ese oficio no existe (todavía).")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            current = await self.repository.contract(guild_id, user_id)
            if current is not None and current.job == job_key:
                raise WorkError(f"Ya trabajas de {job.position(current.level).title}.")
            history = await self.repository.history(guild_id, user_id)
            level = history.get(job_key, (1, 1))[0]
            contract = Contract(job=job_key, level=level, position_since=now, battery_at=now)
            if current is not None:
                contract.battery = battery_now(current, now)
                for name in (
                    "family",
                    "last_shift_at",
                    "shift_day",
                    "shifts_today",
                    "week",
                    "extras_week",
                    "streak_days",
                    "fee_week",
                    "coffee_day",
                    "coffees",
                    "sick_until",
                    "no_extras_day",
                    "rest_day",
                ):
                    setattr(contract, name, getattr(current, name))
            await self.repository.save_contract(guild_id, user_id, contract)
            return await self._status(guild_id, user_id, contract)

    # -- Fichar -------------------------------------------------------------------------

    async def start_shift(self, guild_id: int, user_id: int, *, black_ok: bool = False) -> Shift:
        """Comprueba que se puede fichar y prepara el minijuego. No guarda nada.

        Args:
            black_ok: Si ya aceptó cobrar en B (cuando el próximo turno sería
                extra pasado el límite legal).

        Raises:
            WorkError: Si no puede fichar (sin contrato, de baja, sin batería,
                intervención familiar) o si hace falta aceptar el B
                (`NeedsBlack`).
        """
        status = await self.status(guild_id, user_id)
        if status is None:
            raise WorkError("No tienes curro. Abre `pala` y elige uno.")
        now = status.now
        contract = status.contract
        if status.sick:
            raise WorkError(
                f"🩹 Estás de baja por accidente laboral hasta <t:{int(contract.sick_until)}:t>. "
                "La mutua te vigila, mi amor."
            )
        if status.battery <= BATTERY_MIN:
            hours = max(1, round(-status.battery / BATTERY_REGEN_PER_HOUR))
            raise WorkError(
                f"🪫 Estás tan reventado que te quedas dormido de pie. Descansa unas "
                f"{hours} h o tómate un barraquito."
            )
        kind = status.next_kind
        today = local_day(now).isoformat()
        if kind is not ShiftKind.ORDINARY and contract.no_extras_day == today:
            raise WorkError(
                "👪 **Intervención familiar.** Hoy tu familia te ha confiscado la pala: "
                "nada de horas extra. Mañana ya veremos."
            )
        if kind is ShiftKind.BLACK and not black_ok:
            raise NeedsBlack()
        game = new_game(status.position, self.rng, now=now, tired=status.battery < TIRED)
        return Shift(
            job=status.job,
            position=status.position,
            kind=kind,
            game=game,
            battery_before=status.battery,
            tremors=contract.coffees > MAX_COFFEES,
            returned_from_leave=status.on_leave,
        )

    async def finish_shift(self, guild_id: int, user_id: int, shift: Shift) -> ShiftOutcome:
        """Cierra el turno: cobra, gasta batería, mueve la barra y tira los dados.

        Raises:
            WorkError: Si el contrato cambió mientras jugaba (otro oficio).
        """
        async with self._lock(guild_id, user_id):
            return await self._finish_shift(guild_id, user_id, shift)

    async def _finish_shift(self, guild_id: int, user_id: int, shift: Shift) -> ShiftOutcome:
        now = self._clock()
        shift.game.finish(now)
        contract = await self.repository.contract(guild_id, user_id)
        if contract is None or contract.job != shift.job.key:
            raise WorkError("Cambiaste de curro a mitad de turno. Este no cuenta.")
        if shift.returned_from_leave:
            contract.performance = PERFORMANCE_START
            contract.warned = False
        roll_over_day(contract, now)
        today = local_day(now)
        position = shift.position
        kind = shift.kind
        score = shift.game.score()
        battery_before = battery_now(contract, now)
        family_before = contract.family
        concept = f"pala:{shift.job.key}"

        # Batería, familia, jornada y racha.
        if contract.shift_day != today.isoformat():
            contract.streak_days += 1
            contract.shift_day = today.isoformat()
        contract.battery = battery_before - shift_cost(kind)
        contract.battery_at = now
        contract.family = max(0, min(FAMILY_MAX, contract.family + family_cost(kind, now)))
        intervention = contract.family == 0 and family_before > 0
        if intervention:
            contract.no_extras_day = (today + timedelta(days=1)).isoformat()
        contract.shifts_today += 1
        if kind is not ShiftKind.ORDINARY:
            contract.extras_week += 1
        contract.last_shift_at = now

        # Dinero del turno.
        gross = shift_pay(position.base_pay, score, kind)
        payslip: Payslip | None = None
        black = fine = 0
        caught = False
        if kind is ShiftKind.BLACK:
            balance = await self.economy.pay_undeclared(
                guild_id, user_id, amount=gross, concept=shift.job.key
            )
            black = gross
            if roll(self.rng, INSPECTION_CHANCE):
                caught = True
                fine, balance = await self._caught(guild_id, user_id, gross, "inspeccion")
        else:
            result = await self.economy.pay_salary(
                guild_id,
                user_id,
                gross=gross,
                concept=concept,
                self_employed=position.self_employed,
            )
            payslip, balance = result.payslip, result.balance

        # Cuota de autónomos, una vez por semana (después de cobrar, para que llegue).
        fee = 0
        week = week_of(today)
        if position.self_employed and contract.fee_week != week:
            contract.fee_week = week
            fee, balance = await self.economy.charge_self_employed_fee(
                guild_id, user_id, amount=SELF_EMPLOYED_FEE, concept="autonomos"
            )

        # Tareas y rendimiento (las tareas primero: si te bajan de puesto, se
        # quedan en el puesto que estabas, que es el que se reinicia).
        progress = contract.progress
        game = shift.game
        if score >= GOOD_SCORE:
            progress["good"] = progress.get("good", 0) + 1
        if kind is not ShiftKind.ORDINARY:
            progress["extras"] = progress.get("extras", 0) + 1
        if game.mechanic is Mechanic.DIG and game.broken == 0 and game.correct:
            progress["clean"] = progress.get("clean", 0) + 1
        if game.mechanic is Mechanic.SPOT:
            progress["caught"] = progress.get("caught", 0) + game.correct
        if game.mechanic is Mechanic.MEMORY:
            progress["perfect_rounds"] = progress.get("perfect_rounds", 0) + game.perfect_rounds
        if game.mechanic is Mechanic.DIALOGUE:
            progress["smooth"] = progress.get("smooth", 0) + game.correct
        performance = apply_performance(contract, performance_delta(score))
        if performance == "demoted":
            contract.position_since = now

        # Accidente laboral.
        accident = roll(self.rng, accident_chance(battery_before))
        sick_pay = 0
        if accident:
            contract.sick_until = now + SICK_LEAVE_SECONDS
            # Base reguladora: el bruto declarado de los últimos 30 días, por día.
            month = await self.repository.gross_since(guild_id, user_id, now - 30 * 86_400)
            sick_pay = round(SICK_PAY_RATE * (month + (payslip.gross if payslip else 0)) / 30)
            if sick_pay > 0:
                balance = (
                    await self.economy.pay_salary(
                        guild_id, user_id, gross=sick_pay, concept="pala:baja"
                    )
                ).balance

        await self.repository.save_contract(guild_id, user_id, contract)
        await self.repository.add_shift(
            guild_id,
            user_id,
            ShiftRecord(
                job=shift.job.key,
                level=position.level,
                day=today.isoformat(),
                created_at=now,
                score=score,
                kind=kind.value,
                gross=gross,
                net=payslip.net if payslip else (0 if caught else black),
            ),
        )

        event = None
        if not accident and roll(self.rng, EVENT_CHANCE):
            options = events_for(shift.job.key, contract.level, contract.family)
            event = self.rng.choice(options) if options else None

        status = await self._status(guild_id, user_id, contract)
        week_taxes = await self.economy.week_tax_burden(guild_id, user_id)
        return ShiftOutcome(
            job=shift.job,
            position=position,
            kind=kind,
            score=score,
            gross=gross,
            balance=balance,
            battery_before=battery_before,
            battery_after=contract.battery,
            family_before=family_before,
            family_after=contract.family,
            now=now,
            shifts_today=contract.shifts_today,
            streak_days=contract.streak_days,
            coffees=contract.coffees,
            game=game,
            payslip=payslip,
            black=black,
            caught=caught,
            fine=fine,
            performance=performance,
            accident=accident,
            sick_pay=sick_pay,
            intervention=intervention,
            fee=fee,
            event=event,
            promotion_ready=status.promotion_ready,
            week_taxes=week_taxes,
        )

    async def _caught(self, guild_id: int, user_id: int, black: int, by: str) -> tuple[int, int]:
        """Regulariza lo cobrado en negro: lo devuelves con recargo y sin IMV 3 días.

        Returns:
            `(multa cobrada, saldo final)`.
        """
        amount = round(black * (1 + INSPECTION_SURCHARGE))
        fine, balance = await self.economy.sanction(guild_id, user_id, amount=amount, concept=by)
        await self.economy.suspend_imv(guild_id, user_id, seconds=IMV_SUSPENSION_SECONDS)
        return fine, balance

    # -- Eventos ------------------------------------------------------------------------

    async def resolve_event(
        self, guild_id: int, user_id: int, event_key: str, option: int
    ) -> EventResult:
        """Aplica la opción elegida de un evento.

        Raises:
            WorkError: Si el evento no existe o ya no tiene contrato.
        """
        event = RESIGN_EVENT if event_key == RESIGN_EVENT.key else EVENT_BY_KEY.get(event_key)
        if event is None or option not in (0, 1):
            raise WorkError("Ese marrón ya no existe.")
        async with self._lock(guild_id, user_id):
            contract = await self.repository.contract(guild_id, user_id)
            if contract is None:
                raise WorkError("No tienes curro.")
            now = self._clock()
            job = JOB_BY_KEY[contract.job]
            base = job.position(contract.level).base_pay
            outcome = event.options[option][1]
            result = EventResult(event=event, text=outcome.text)
            if outcome.stat:
                result.stats[outcome.stat] = 1
            if outcome.pay > 0:
                result.paid = (
                    await self.economy.pay_salary(
                        guild_id,
                        user_id,
                        gross=max(1, round(base * outcome.pay)),
                        concept=f"pala:{contract.job}",
                        self_employed=job.position(contract.level).self_employed,
                    )
                ).payslip
            if outcome.black > 0:
                result.black = max(1, round(base * outcome.black))
                await self.economy.pay_undeclared(
                    guild_id, user_id, amount=result.black, concept=f"{contract.job}:{event.key}"
                )
                if roll(self.rng, outcome.risk):
                    result.caught = True
                    result.caught_by = outcome.caught_by
                    if outcome.caught_by == "uco" and roll(self.rng, PARDON_CHANCE):
                        result.pardoned = True
                        result.stats["work_pardoned"] = 1
                    else:
                        result.fine, _ = await self._caught(
                            guild_id, user_id, result.black, outcome.caught_by
                        )
                    result.stats[f"work_caught_{outcome.caught_by}"] = 1
                    if outcome.caught_by == "uco" and contract.job == "politica":
                        result.follow_up = RESIGN_EVENT
            contract.performance = max(
                0, min(PERFORMANCE_MAX, contract.performance + outcome.performance)
            )
            contract.family = max(0, min(FAMILY_MAX, contract.family + outcome.family))
            if outcome.battery:
                contract.battery = min(
                    float(BATTERY_MAX), battery_now(contract, now) + outcome.battery
                )
                contract.battery_at = now
            if outcome.demote and contract.level > 1:
                contract.level -= 1
                contract.performance = PERFORMANCE_START
                contract.progress = {}
                contract.position_since = now
                result.demoted = True
            if event is not RESIGN_EVENT:
                contract.progress["events"] = contract.progress.get("events", 0) + 1
            await self.repository.save_contract(guild_id, user_id, contract)
            return result

    # -- Ascensos -----------------------------------------------------------------------

    async def accept_promotion(self, guild_id: int, user_id: int) -> Status:
        """Acepta el ascenso si toca.

        Raises:
            WorkError: Si todavía no toca (con lo que falta).
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None:
                raise WorkError("No tienes curro.")
            if not status.promotion_ready:
                raise WorkError("Aún no te toca:\n- " + "\n- ".join(status.blockers))
            promote(status.contract, status.now)
            await self.repository.save_contract(guild_id, user_id, status.contract)
            return await self._status(guild_id, user_id, status.contract)

    async def decline_promotion(self, guild_id: int, user_id: int) -> None:
        """Rechaza el ascenso de hoy (mañana te lo vuelven a ofrecer)."""
        async with self._lock(guild_id, user_id):
            contract = await self.repository.contract(guild_id, user_id)
            if contract is None:
                raise WorkError("No tienes curro.")
            contract.declined_day = local_day(self._clock()).isoformat()
            await self.repository.save_contract(guild_id, user_id, contract)

    # -- Compras ------------------------------------------------------------------------

    async def buy_training(self, guild_id: int, user_id: int) -> tuple[str, int, int, int]:
        """Compra la formación del puesto siguiente.

        Tratamiento fiscal: una compra más, con IGIC general (ver
        `EconomyService.purchase`). Simplificación: algunas formaciones reales
        estarían exentas (enseñanza reglada), pero aquí todas pagan el general.

        Returns:
            `(nombre, base, igic, saldo final)`.

        Raises:
            WorkError: Si no hay formación pendiente, ya la tiene o no le llega.
        """
        status = await self.status(guild_id, user_id)
        if status is None:
            raise WorkError("No tienes curro.")
        following = status.next_position
        if following is None or following.training is None:
            raise WorkError("Para tu próximo puesto no hace falta ningún curso.")
        training = following.training
        tax = igic(training.price, IGIC_GENERAL_RATE)
        try:
            _, balance = await self.economy.purchase(
                guild_id,
                user_id,
                base=training.price,
                tax=tax,
                concept=f"formacion:{training.key}",
                reserve=self.repository.reserve_training(
                    guild_id, user_id, training.key, self._clock()
                ),
            )
        except ValueError as error:
            raise WorkError(str(error)) from error
        except InsufficientFundsError as error:
            raise WorkError(
                f"No te llega: {training.name} cuesta {training.price + tax} Y$ con IGIC."
            ) from error
        return training.name, training.price, tax, balance

    async def buy_coffee(self, guild_id: int, user_id: int, key: str) -> tuple[float, int, int]:
        """Se toma un café: recarga batería. Desde el cuarto del día, temblores.

        Tratamiento fiscal: una compra con IGIC general (ver `EconomyService.purchase`).

        Returns:
            `(batería después, cafés de hoy, saldo final)`.

        Raises:
            WorkError: Si no tiene curro, el café no existe o no le llega.
        """
        coffee = COFFEE_BY_KEY.get(key)
        if coffee is None:
            raise WorkError("Esa máquina no tiene eso.")
        async with self._lock(guild_id, user_id):
            contract = await self.repository.contract(guild_id, user_id)
            if contract is None:
                raise WorkError("La máquina de café es solo para quien tiene curro.")
            now = self._clock()
            roll_over_day(contract, now)
            tax = igic(coffee.price, IGIC_GENERAL_RATE)
            try:
                _, balance = await self.economy.purchase(
                    guild_id,
                    user_id,
                    base=coffee.price,
                    tax=tax,
                    concept=f"cafe:{coffee.key}",
                    reserve=lambda _connection: None,
                )
            except InsufficientFundsError as error:
                raise WorkError("No te llega ni para un café, mi amor.") from error
            contract.battery = min(float(BATTERY_MAX), battery_now(contract, now) + coffee.battery)
            contract.battery_at = now
            contract.coffees += 1
            await self.repository.save_contract(guild_id, user_id, contract)
            return contract.battery, contract.coffees, balance

    # -- Canales ------------------------------------------------------------------------

    async def channels(self, guild_id: int) -> frozenset[int]:
        """Canales donde se permite `pala` (vacío = cualquiera)."""
        return await self.repository.channels(guild_id)

    async def toggle_channel(self, guild_id: int, channel_id: int) -> tuple[bool, frozenset[int]]:
        """Añade o quita un canal. Devuelve `(añadido, canales)`."""
        current = set(await self.repository.channels(guild_id))
        added = channel_id not in current
        if added:
            current.add(channel_id)
        else:
            current.discard(channel_id)
        await self.repository.set_channels(guild_id, frozenset(current))
        return added, frozenset(current)

    async def clear_channels(self, guild_id: int) -> None:
        """Vuelve a permitir `pala` en cualquier canal."""
        await self.repository.set_channels(guild_id, frozenset())


class NeedsBlack(WorkError):
    """El próximo turno sería extra pasado el límite legal: hay que aceptar el B."""

    def __init__(self) -> None:
        super().__init__(
            "Ya has gastado las horas extra legales de la semana (art. 35.2 ET). "
            "Tu jefe te mira: «¿Te lo pago en B?»."
        )
