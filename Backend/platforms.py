"""The delivery platforms, named in one place.

A platform has two names and they are not the same string:

* The **wire name** is what the database stores and what travels over the
  earnings WebSocket - ``"UberEats"``, with no space. Changing it would
  invalidate every ``earnings``, ``orders`` and ``contracts`` row on file.
* The **display name** is what a partner reads - ``"Uber Eats"``.

Hardcoding either one in more than one place is what made the Uber Eats card on
the dashboard stop updating: the backend sent ``"UberEats"`` and the frontend
kept its numbers under ``"Uber Eats"``, so the two never met. Every name now
comes from here.

The frontend has a matching module, ``src/services/platforms.ts``. The wire
names in the two files must stay identical; they are the contract between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    """A delivery platform, by the name the database and the stream use."""

    ZOMATO = "Zomato"
    SWIGGY = "Swiggy"
    UBER_EATS = "UberEats"

    @property
    def display_name(self) -> str:
        """The name to show a partner."""
        return _DISPLAY_NAMES[self]


_DISPLAY_NAMES = {
    Platform.ZOMATO: "Zomato",
    Platform.SWIGGY: "Swiggy",
    Platform.UBER_EATS: "Uber Eats",
}


def platform_key(name: str) -> str:
    """A comparison key: 'Uber Eats', 'ubereats' and 'UberEats' all match.

    Documents, screenshots and older rows spell the platforms inconsistently,
    so comparisons go through this rather than through equality.
    """
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


_BY_KEY = {platform_key(p.value): p for p in Platform}


def canonical(name: str) -> Optional[Platform]:
    """The platform a spelling refers to, or None when it names none of them."""
    return _BY_KEY.get(platform_key(name))


def display_name(name: str) -> str:
    """The display name for any spelling, falling back to what was given.

    An unknown platform is shown as it arrived rather than hidden, because the
    evidence a partner sends may come from an app FairPay does not know yet.
    """
    platform = canonical(name)
    return platform.display_name if platform else name


@dataclass(frozen=True)
class StreamProfile:
    """One simulated platform on the live earnings stream.

    The dashboard's demo mode mirrors these figures in
    ``src/services/platforms.ts`` so that the simulated and the live stream look
    the same.
    """

    platform: Platform
    starting_earnings: int
    tick_seconds: int
    increment: int = 100


# The live earnings demonstration. Replacing a real platform feed means
# replacing one entry here, not writing another near-copy of a loop.
STREAM_PROFILES: tuple[StreamProfile, ...] = (
    StreamProfile(Platform.ZOMATO, starting_earnings=4000, tick_seconds=5),
    StreamProfile(Platform.SWIGGY, starting_earnings=3500, tick_seconds=13),
    StreamProfile(Platform.UBER_EATS, starting_earnings=1700, tick_seconds=18),
)
