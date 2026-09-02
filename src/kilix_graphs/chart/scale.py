"""Scales and ticks.

The tick algorithm is d3's `tickSpec`, ported from ISC-licensed source. It is
forty lines and it is the reason d3 axes never show `0.30000000000000004`:
rather than dividing a range into n parts and formatting the results, it picks
a step of 1, 2 or 5 times a power of ten by comparing the raw step against
sqrt(50), sqrt(10) and sqrt(2), then emits *integer multiples* of that step.
When the step is smaller than one it works in reciprocals and divides, so the
values are exact rather than accumulated.

Reimplementing this badly is the single most common way a hand-rolled chart
gives itself away, which is why it is ported rather than invented.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

__all__ = [
    "BandScale",
    "LinearScale",
    "LogScale",
    "PointScale",
    "format_tick",
    "nice_domain",
    "tick_step",
    "ticks",
]

_E10 = math.sqrt(50)
_E5 = math.sqrt(10)
_E2 = math.sqrt(2)


def _tick_spec(start: float, stop: float, count: float) -> tuple[float, float, float]:
    """(first index, last index, increment) for the ticks in [start, stop].

    A negative increment means "divide by its magnitude" rather than multiply,
    which is what keeps sub-unit steps exact.
    """
    step = (stop - start) / max(0.0, count)
    power = math.floor(math.log10(step)) if step > 0 else 0
    error = step / (10.0**power) if step > 0 else 0.0
    factor = 10 if error >= _E10 else 5 if error >= _E5 else 2 if error >= _E2 else 1

    if power < 0:
        inc = (10.0**-power) / factor
        i1 = round(start * inc)
        i2 = round(stop * inc)
        if i1 / inc < start:
            i1 += 1
        if i2 / inc > stop:
            i2 -= 1
        inc = -inc
    else:
        inc = (10.0**power) * factor
        i1 = round(start / inc)
        i2 = round(stop / inc)
        if i1 * inc < start:
            i1 += 1
        if i2 * inc > stop:
            i2 -= 1
    if i2 < i1 and 0.5 <= count < 2:
        return _tick_spec(start, stop, count * 2)
    return (float(i1), float(i2), inc)


def ticks(start: float, stop: float, count: int = 10) -> list[float]:
    """Round tick values inside [start, stop], about ``count`` of them."""
    if count <= 0 or not (math.isfinite(start) and math.isfinite(stop)):
        return []
    if start == stop:
        return [start]
    reverse = stop < start
    lo, hi = (stop, start) if reverse else (start, stop)
    i1, i2, inc = _tick_spec(lo, hi, count)
    if i2 < i1:
        return []
    n = int(i2 - i1) + 1
    if inc < 0:
        values = [(i1 + i) / -inc for i in range(n)]
    else:
        values = [(i1 + i) * inc for i in range(n)]
    return list(reversed(values)) if reverse else values


def tick_step(start: float, stop: float, count: int = 10) -> float:
    reverse = stop < start
    lo, hi = (stop, start) if reverse else (start, stop)
    inc = _tick_spec(lo, hi, count)[2]
    magnitude = 1 / -inc if inc < 0 else inc
    return -magnitude if reverse else magnitude


def nice_domain(start: float, stop: float, count: int = 10) -> tuple[float, float]:
    """Widen a domain out to the nearest round values.

    Iterating matters: widening changes the range, which can change the step,
    which can change how far it should widen. It converges quickly; the cap is
    there so a pathological domain cannot spin.
    """
    if start == stop:
        return (start, stop)
    lo, hi = (stop, start) if stop < start else (start, stop)
    previous = None
    for _ in range(10):
        step = tick_step(lo, hi, count)
        if step == previous or step == 0 or not math.isfinite(step):
            break
        previous = step
        if step > 0:
            lo = math.floor(lo / step) * step
            hi = math.ceil(hi / step) * step
        else:
            lo = math.ceil(lo * step) / step
            hi = math.floor(hi * step) / step
    return (hi, lo) if stop < start else (lo, hi)


def format_tick(value: float, step: float | None = None) -> str:
    """Format a tick with only the digits its step justifies.

    Without the step this shows the shortest exact representation. With it,
    the number of decimals comes from the step, so an axis stepping by 0.5
    reads 0.0 / 0.5 / 1.0 rather than 0 / 0.5 / 1.
    """
    if value == 0:
        value = 0.0  # collapse -0.0
    if step is not None and step != 0 and math.isfinite(step):
        magnitude = abs(step)
        decimals = max(0, -math.floor(math.log10(magnitude)))
        if decimals <= 6:
            return f"{value:.{decimals}f}"
    if value == int(value) and abs(value) < 1e16:
        return str(int(value))
    return f"{value:g}"


@dataclass
class LinearScale:
    """Continuous domain to continuous range."""

    domain: tuple[float, float] = (0.0, 1.0)
    range: tuple[float, float] = (0.0, 1.0)

    def __call__(self, value: float) -> float:
        d0, d1 = self.domain
        r0, r1 = self.range
        if d1 == d0:
            return (r0 + r1) / 2
        return r0 + (value - d0) / (d1 - d0) * (r1 - r0)

    def invert(self, position: float) -> float:
        d0, d1 = self.domain
        r0, r1 = self.range
        if r1 == r0:
            return d0
        return d0 + (position - r0) / (r1 - r0) * (d1 - d0)

    def ticks(self, count: int = 10) -> list[float]:
        return ticks(self.domain[0], self.domain[1], count)

    def tick_step(self, count: int = 10) -> float:
        return tick_step(self.domain[0], self.domain[1], count)

    def nice(self, count: int = 10) -> "LinearScale":
        self.domain = nice_domain(self.domain[0], self.domain[1], count)
        return self


@dataclass
class LogScale:
    """Log-10 domain to continuous range. The domain must not straddle zero."""

    domain: tuple[float, float] = (1.0, 10.0)
    range: tuple[float, float] = (0.0, 1.0)

    def __post_init__(self) -> None:
        lo, hi = self.domain
        if lo <= 0 or hi <= 0:
            raise ValueError("a log scale needs a strictly positive domain")

    def __call__(self, value: float) -> float:
        if value <= 0:
            return self.range[0]
        d0, d1 = math.log10(self.domain[0]), math.log10(self.domain[1])
        r0, r1 = self.range
        if d1 == d0:
            return (r0 + r1) / 2
        return r0 + (math.log10(value) - d0) / (d1 - d0) * (r1 - r0)

    def ticks(self, count: int = 10) -> list[float]:
        lo = math.floor(math.log10(self.domain[0]))
        hi = math.ceil(math.log10(self.domain[1]))
        decades = [10.0**power for power in range(int(lo), int(hi) + 1)]
        inside = [v for v in decades if self.domain[0] <= v <= self.domain[1]]
        if len(inside) >= 2:
            return inside
        # Fewer than two decades in range: fall back to the linear ticks, or
        # the axis has no labels at all.
        return ticks(self.domain[0], self.domain[1], count)


@dataclass
class BandScale:
    """Discrete domain to bands of equal width. Bars live on one of these."""

    domain: Sequence[str] = field(default_factory=list)
    range: tuple[float, float] = (0.0, 1.0)
    padding: float = 0.1

    @property
    def step(self) -> float:
        count = len(self.domain)
        if count == 0:
            return 0.0
        span = self.range[1] - self.range[0]
        return span / count

    @property
    def bandwidth(self) -> float:
        return self.step * (1 - self.padding)

    def __call__(self, value: str) -> float:
        try:
            index = list(self.domain).index(value)
        except ValueError:
            return self.range[0]
        return self.range[0] + index * self.step + self.step * self.padding / 2

    def centre(self, value: str) -> float:
        return self(value) + self.bandwidth / 2

    def ticks(self, count: int = 10) -> list[str]:
        """Every category, thinned to at most ``count`` labels.

        Dropping labels beats overlapping them, and dropping every k-th keeps
        the first and the spacing even, which is what makes a thinned axis
        still readable as an axis.
        """
        values = list(self.domain)
        if count <= 0 or len(values) <= count:
            return values
        stride = math.ceil(len(values) / count)
        return values[::stride]


@dataclass
class PointScale:
    """Discrete domain to evenly spaced points. Lines over categories."""

    domain: Sequence[str] = field(default_factory=list)
    range: tuple[float, float] = (0.0, 1.0)

    def __call__(self, value: str) -> float:
        values = list(self.domain)
        if not values:
            return self.range[0]
        try:
            index = values.index(value)
        except ValueError:
            return self.range[0]
        if len(values) == 1:
            return (self.range[0] + self.range[1]) / 2
        span = self.range[1] - self.range[0]
        return self.range[0] + index * span / (len(values) - 1)

    def ticks(self, count: int = 10) -> list[str]:
        return BandScale(self.domain, self.range).ticks(count)
