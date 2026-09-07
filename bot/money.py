"""The one place money is rounded, and the one place it is displayed.

Two decimal habits carried straight from ZentraShopBot, because both were
learned the hard way there and both apply here without change:

  * every amount is `Decimal`, constructed from a string — `Decimal("0.1")`,
    never `Decimal(0.1)`. The latter captures the float's own rounding error
    before Decimal ever sees it.
  * quantize to the cent with ROUND_HALF_UP at the one moment a figure is
    about to be shown or stored, not while it is still being computed —
    rounding twice on the way to an answer is how a ledger stops summing to
    itself.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def cents(amount: Decimal) -> Decimal:
    """Round to the cent. The only rounding function in this codebase —
    reach for this rather than writing a new `.quantize(...)` at a call site."""
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_usdt(amount: Decimal) -> str:
    return f"{cents(amount)} USDT"


def fmt_etb(amount: Decimal) -> str:
    """ETB has no sub-birr display anywhere in this shop — rounded to the
    whole birr, never to the cent."""
    return f"{int(amount.to_integral_value(rounding=ROUND_HALF_UP)):,} ETB"


def to_etb(usdt: Decimal, rate: Decimal) -> Decimal:
    return cents(usdt * rate)


def to_usdt(etb: Decimal, rate: Decimal) -> Decimal:
    """ETB → USDT, to the cent. Used to credit a Telebirr or Bank of
    Abyssinia top-up: the customer pays birr, the wallet holds USDT, and
    this is the one division that turns one into the other."""
    if rate <= 0:
        raise ValueError("usdt_to_etb must be greater than zero")
    return cents(Decimal(etb) / rate)
