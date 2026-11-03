"""Wall-clock slots: weekdays, month days, and a time or time window.

The scheduler measures intervals on a monotonic clock, which is right for "every 60
seconds" and wrong for "every Monday at 03:00": that answer depends on the calendar and
must survive a restart without drifting. A CalendarSpec turns such a phrase into the next
wall-clock moment, optionally a random moment inside a window, so a fleet of instances
does not wake at the same second.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final

__all__ = ["CalendarSpec", "parse_days", "parse_spec", "parse_time"]

WEEKDAYS: Final[tuple[str, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_GROUPS: Final[dict[str, frozenset[int]]] = {
    "daily": frozenset(),
    "weekdays": frozenset(range(5)),
    "weekends": frozenset({5, 6}),
}
WEEKDAYS_LONG: Final[dict[str, str]] = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}
_MONTH_DAY = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_LOOKAHEAD_DAYS: Final[int] = 400
"""How far ahead a slot is searched: more than a year covers 'the 31st' and 'Feb 29'-free specs."""


def parse_time(text: str) -> time:
    """Return the time of day named by 'HH:MM'; raise ValueError otherwise."""
    match = _TIME.match(str(text).strip())
    if match is None:
        raise ValueError(f"expected a time like '03:00', got {text!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"{text!r} is not a time of day")
    return time(hour, minute)


def parse_days(names: str | list[str] | tuple[str, ...]) -> tuple[frozenset[int], frozenset[int]]:
    """Return (weekdays, month days) named by *names*; both empty means every day.

    Weekdays are 'mon'..'sun' (0 is Monday), the groups 'daily', 'weekdays' and
    'weekends', and a month day is a number optionally suffixed ('1', '1st', '15th').
    """
    items = names.replace(",", " ").split() if isinstance(names, str) else list(names)
    weekdays: set[int] = set()
    month_days: set[int] = set()
    for raw in items:
        word = str(raw).strip().lower()
        if not word:
            continue
        if word in _WEEKDAY_GROUPS:
            weekdays |= _WEEKDAY_GROUPS[word]
        elif word[:3] in WEEKDAYS and (len(word) == 3 or word in WEEKDAYS_LONG):
            weekdays.add(WEEKDAYS.index(word[:3]))
        elif (match := _MONTH_DAY.match(word)) is not None and 1 <= int(match.group(1)) <= 31:
            month_days.add(int(match.group(1)))
        else:
            raise ValueError(f"unknown day {raw!r}: use mon..sun, daily, weekdays, 1st, 15th")
    return frozenset(weekdays), frozenset(month_days)


@dataclass(frozen=True, slots=True)
class CalendarSpec:
    """The days and the time (or time window) a task may run at, in local time."""

    weekdays: frozenset[int] = frozenset()
    month_days: frozenset[int] = frozenset()
    start: time = time(0, 0)
    end: time | None = None
    any_time: bool = False
    """True for a day-only constraint: any moment of an allowed day is fine."""

    def __post_init__(self) -> None:
        if self.end is not None and self.end <= self.start:
            raise ValueError(
                f"a window must end after it starts, got {self.start:%H:%M}-{self.end:%H:%M}"
            )

    def day_allowed(self, day: date) -> bool:
        """Return True when *day* matches the weekday and month-day filters."""
        if self.weekdays and day.weekday() not in self.weekdays:
            return False
        return not self.month_days or day.day in self.month_days

    def allows(self, moment: float) -> bool:
        """Return True when the wall-clock timestamp *moment* lies inside the spec."""
        stamp = datetime.fromtimestamp(moment)
        if not self.day_allowed(stamp.date()):
            return False
        if self.any_time:
            return True
        end = self.end or self.start
        return self.start <= stamp.time().replace(microsecond=0) <= end

    def next_after(self, moment: float, rng: random.Random | None = None) -> float:
        """Return the first allowed wall-clock timestamp after *moment*.

        With a window, the answer is a random instant inside the window (never before
        *moment*); with a single time it is that exact time; with a day-only spec it
        is local midnight of the next allowed day.
        """
        chooser = rng or random
        after = datetime.fromtimestamp(moment)
        for offset in range(_LOOKAHEAD_DAYS):
            day = after.date() + timedelta(days=offset)
            if not self.day_allowed(day):
                continue
            opens = datetime.combine(day, self.start)
            if self.any_time:
                if opens > after:
                    return opens.timestamp()
                continue
            closes = datetime.combine(day, self.end) if self.end is not None else opens
            if closes <= after:
                continue
            low = max(opens, after + timedelta(seconds=1))
            if self.end is None:
                if opens > after:
                    return opens.timestamp()
                continue
            span = (closes - low).total_seconds()
            if span <= 0:
                continue
            return low.timestamp() + chooser.uniform(0.0, span)
        raise ValueError("the calendar spec never matches a day within the next 400 days")

    def describe(self) -> str:
        """Return the spec as the phrase `parse_spec` reads back."""
        names = [WEEKDAYS[index] for index in sorted(self.weekdays)]
        names += [str(day) for day in sorted(self.month_days)]
        days = ",".join(names) or "daily"
        if self.any_time:
            return days
        clock = f"{self.start:%H:%M}" + (f"-{self.end:%H:%M}" if self.end else "")
        return f"{days} {clock}"


def parse_spec(spec: str) -> CalendarSpec:
    """Parse 'mon 03:00', 'mon,thu 02:00-05:00', '1st 04:30' or '03:00' (daily).

    Raises ValueError with a message naming what was wrong, so a typo in a config
    file is reported at start-up and not silently never run.
    """
    tokens = str(spec).strip().lower().split()
    if not tokens:
        raise ValueError("a calendar spec needs at least a time, like '03:00'")
    clock = tokens[-1]
    days_text = " ".join(tokens[:-1])
    if "-" in clock:
        first, _, last = clock.partition("-")
        start, end = parse_time(first), parse_time(last)
    else:
        start, end = parse_time(clock), None
    weekdays, month_days = parse_days(days_text) if days_text else (frozenset(), frozenset())
    return CalendarSpec(weekdays=weekdays, month_days=month_days, start=start, end=end)
