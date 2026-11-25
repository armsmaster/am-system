"""Фильтр Калмана «уровень + тренд» (решения 168 и 169, docs/design/06-valuation-nav.md).

Модель — локальный линейный тренд. Состояние — уровень ℓ и тренд b. Часов двое:
время t — по нему копится шум (торговые сессии для стоимости пая, минуты — для
баров цен), и начисление a — по нему растёт уровень (дни начисления по дате
расчётов T+1: в пятницу — три). Не задано начисление — оно равно времени:

    ℓₖ = ℓₖ₋₁ + Δaₖ · bₖ₋₁ + ηₖ       Var ηₖ = q_level · σ² · Δtₖ
    bₖ = bₖ₋₁ + ζₖ                    Var ζₖ = q_trend · σ² · Δtₖ
    yₖ = ℓₖ + εₖ                      Var εₖ = σ²

Шаги могут быть неравными (выходные, перерывы в торгах); Δt > 0, Δa ≥ 0.
q_level и q_trend — шумы уровня и тренда относительно шума наблюдения: больше q —
фильтр быстрее верит новым значениям.

Фильтр считается при σ² = 1: оценки ℓ и b от σ² не зависят. Сам σ² оцениваем
по ошибкам прогноза (концентрированное правдоподобие):

    σ̂² = Σₖ₌₃ⁿ vₖ² / fₖ / (n − 2)

vₖ — ошибка прогноза наблюдения, fₖ — её дисперсия при σ² = 1. Первые два
наблюдения не входят: ими фильтр «узнаёт» уровень и тренд. Начало — диффузное:
уровень — первое наблюдение с дисперсией шума наблюдения, тренд — ноль с дисперсией
10⁸; со вторым наблюдением тренд становится наклоном между первыми двумя.

Прогноз на h времени и g начисления вперёд: ℓ + g · b; стандартное отклонение —
σ̂ · √f, f — дисперсия прогноза при σ² = 1.
Считаем в Decimal с 40 значащими цифрами.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext

from am_quant.bond import QuantError

PRECISION = 40
DIFFUSE = Decimal(10) ** 8  # дисперсия начального тренда при σ² = 1
# Первые наблюдения, по которым фильтр узнаёт уровень и тренд: в оценку σ² не входят.
DIFFUSE_STEPS = 2

# По умолчанию для стоимости пая (решение 168): разовый скачок уровень забывает
# примерно за 2 дня, тренд отражает примерно последние 50 дней.
NAV_Q_LEVEL = Decimal("0.05")
NAV_Q_TREND = Decimal("0.0005")


@dataclass(frozen=True, slots=True)
class Settings:
    q_level: Decimal
    q_trend: Decimal


NAV_SETTINGS = Settings(NAV_Q_LEVEL, NAV_Q_TREND)

# Насколько точно ищем установившийся режим фильтра и сколько шагов на это даём.
STEADY_TOLERANCE = Decimal(10) ** -24
STEADY_STEPS = 100_000


@dataclass(frozen=True, slots=True)
class Memory:
    """Настройки в днях — так их видит пользователь (решение 174).

    В установившемся режиме при шаге 1 фильтр — сглаживание Холта с коэффициентами
    K₁ (уровень) и K₂ (тренд):

        полураспад скачка  h = ln ½ / ln(1 − K₁)
        память тренда      m = 1 / K₂
    """

    half_life: Decimal  # за сколько шагов уровень наполовину догоняет скачок
    trend_memory: Decimal  # по скольким последним шагам считается тренд


def settings_for(memory: Memory) -> Settings:
    """Шумы уровня и тренда для настроек в днях — без подбора, из автоковариаций Δ²y.

    У модели: γ₀ = q_trend + 2·q_level + 6, γ₁ = −q_level − 4, γ₂ = 1 (при σ² = 1).
    У фильтра: Δ²y = vₖ + θ₁vₖ₋₁ + θ₂vₖ₋₂, θ₁ = K₁ + K₂ − 2, θ₂ = 1 − K₁, дисперсия
    ошибки прогноза f = γ₂ / θ₂. Отсюда q_level = −f·θ₁·(1 + θ₂) − 4 и
    q_trend = f·(1 + θ₁² + θ₂²) − 2·q_level − 6. Шум не бывает отрицательным: при
    короткой памяти тренда сильно сгладить нельзя.
    """
    if memory.half_life <= 0 or memory.trend_memory <= 1:
        raise QuantError(
            "bad_settings", "Сглаживание — больше нуля дней, память тренда — больше одного дня."
        )
    q_level, q_trend = _noises(memory.half_life, memory.trend_memory)
    if q_level < 0 or q_trend < 0:
        limit = max_half_life(memory.trend_memory)
        raise QuantError(
            "bad_settings",
            f"При памяти тренда {_days(memory.trend_memory, 0)} дн. сглаживание — "
            f"не больше {_days(limit, 1)} дн.",
        )
    return Settings(q_level, q_trend)


def max_half_life(trend_memory: Decimal) -> Decimal:
    """Самое сильное сглаживание при этой памяти тренда — до 0,1 дня вниз: шум уровня — ноль."""
    low, high = Decimal(0), Decimal(1000)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        for _ in range(100):
            middle = (low + high) / 2
            if _noises(middle, trend_memory)[0] >= 0:
                low = middle
            else:
                high = middle
        return low.quantize(Decimal("0.1"), rounding=ROUND_FLOOR)


def memory_of(settings: Settings) -> Memory:
    """Настройки в днях для шумов уровня и тренда: установившийся режим фильтра."""
    if settings.q_level < 0 or settings.q_trend <= 0:
        raise QuantError("bad_settings", "Для памяти в днях нужен шум тренда больше нуля.")
    with localcontext() as ctx:
        ctx.prec = PRECISION
        cov = (Decimal(1), Decimal(0), DIFFUSE)
        gains, steps = (Decimal(0), Decimal(0)), 0
        while True:
            a11, a12, a22 = _predict_cov(cov, Decimal(1), Decimal(1), settings)
            f = a11 + 1
            k1, k2 = a11 / f, a12 / f
            cov = (a11 - k1 * a11, a12 - k1 * a12, a22 - k2 * a12)
            settled = abs(k1 - gains[0]) + abs(k2 - gains[1]) < STEADY_TOLERANCE
            gains, steps = (k1, k2), steps + 1
            if settled or steps >= STEADY_STEPS:
                break
        return Memory(Decimal("0.5").ln() / (1 - gains[0]).ln(), 1 / gains[1])


def _noises(half_life: Decimal, trend_memory: Decimal) -> tuple[Decimal, Decimal]:
    with localcontext() as ctx:
        ctx.prec = PRECISION
        k1 = 1 - Decimal("0.5") ** (1 / half_life)
        k2 = 1 / trend_memory
        theta1, theta2 = k1 + k2 - 2, 1 - k1
        f = 1 / theta2
        q_level = -f * theta1 * (1 + theta2) - 4
        q_trend = f * (1 + theta1 * theta1 + theta2 * theta2) - 2 * q_level - 6
        return q_level, q_trend


def _days(value: Decimal, places: int) -> str:
    """Число дней по-русски: десятичная запятая."""
    return f"{value:.{places}f}".replace(".", ",")


@dataclass(frozen=True, slots=True)
class Point:
    time: Decimal
    observed: Decimal
    level: Decimal  # сглаженное значение на момент наблюдения (оценка фильтра)
    trend: Decimal  # прирост за единицу начисления
    level_variance: Decimal  # при σ² = 1


@dataclass(frozen=True, slots=True)
class Forecast:
    time: Decimal
    value: Decimal
    std: Decimal | None  # σ̂ · √f; меньше трёх наблюдений — неизвестно


@dataclass(frozen=True, slots=True)
class Filtered:
    points: tuple[Point, ...]
    sigma2: Decimal | None  # оценка дисперсии шума наблюдения; меньше трёх — None
    settings: Settings
    # Последнее состояние и его ковариация при σ² = 1 — для прогноза.
    _state: tuple[Decimal, Decimal]
    _cov: tuple[Decimal, Decimal, Decimal]  # p11, p12, p22

    def forecast(self, ahead: Decimal, accrual: Decimal | None = None) -> Forecast:
        """Прогноз наблюдения через ``ahead`` времени и ``accrual`` начисления после
        последнего; начисление не задано — равно времени."""
        grown = ahead if accrual is None else accrual
        if ahead <= 0 or grown < 0:
            raise QuantError("bad_horizon", "Прогноз — только вперёд.")
        with localcontext() as ctx:
            ctx.prec = PRECISION
            level, trend = self._state
            variance = _predict_cov(self._cov, ahead, grown, self.settings)[0] + 1
            std = None if self.sigma2 is None else (self.sigma2 * variance).sqrt()
            return Forecast(self.points[-1].time + ahead, level + grown * trend, std)


def _predict_cov(
    cov: tuple[Decimal, Decimal, Decimal], elapsed: Decimal, grown: Decimal, settings: Settings
) -> tuple[Decimal, Decimal, Decimal]:
    """F P Fᵀ + Q при F = [[1, Δa], [0, 1]]; шум — за время Δt."""
    p11, p12, p22 = cov
    return (
        p11 + 2 * grown * p12 + grown * grown * p22 + settings.q_level * elapsed,
        p12 + grown * p22,
        p22 + settings.q_trend * elapsed,
    )


def kalman_trend(
    observations: Sequence[tuple[Decimal, Decimal]],
    settings: Settings,
    accruals: Sequence[Decimal] | None = None,
) -> Filtered:
    """Фильтр по наблюдениям (время, значение): время строго растёт. ``accruals`` —
    начисление на момент каждого наблюдения, не убывает; не задано — равно времени."""
    if not observations:
        raise QuantError("no_data", "Нужно хотя бы одно наблюдение.")
    if settings.q_level < 0 or settings.q_trend < 0:
        raise QuantError("bad_settings", "Шумы уровня и тренда не бывают отрицательными.")
    grown_at = [t for t, _ in observations] if accruals is None else list(accruals)
    if len(grown_at) != len(observations):
        raise QuantError("bad_series", "Начисление нужно на каждое наблюдение.")
    with localcontext() as ctx:
        ctx.prec = PRECISION
        first_time, first_value = observations[0]
        # Первое наблюдение: уровень известен с точностью шума наблюдения, тренд — нет.
        level, trend = first_value, Decimal(0)
        cov = (Decimal(1), Decimal(0), DIFFUSE)
        points = [Point(first_time, first_value, level, trend, cov[0])]
        total = Decimal(0)
        previous = first_time
        for index, (time, value) in enumerate(observations[1:], start=2):
            elapsed = time - previous
            grown = grown_at[index - 1] - grown_at[index - 2]
            if elapsed <= 0:
                raise QuantError("bad_series", "Время наблюдений должно строго расти.")
            if grown < 0:
                raise QuantError("bad_series", "Начисление не может убывать.")
            p11, p12, p22 = _predict_cov(cov, elapsed, grown, settings)
            predicted = level + grown * trend
            innovation = value - predicted
            variance = p11 + 1
            gain_level, gain_trend = p11 / variance, p12 / variance
            level = predicted + gain_level * innovation
            trend = trend + gain_trend * innovation
            cov = (
                p11 - gain_level * p11,
                p12 - gain_level * p12,
                p22 - gain_trend * p12,
            )
            if index > DIFFUSE_STEPS:
                total += innovation * innovation / variance
            points.append(Point(time, value, level, trend, cov[0]))
            previous = time
        n = len(observations)
        sigma2 = total / (n - DIFFUSE_STEPS) if n > DIFFUSE_STEPS else None
        return Filtered(tuple(points), sigma2, settings, (level, trend), cov)
