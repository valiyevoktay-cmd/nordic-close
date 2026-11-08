"""Money helpers. Amounts are Decimal in code and integer minor units in storage."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

MINOR = Decimal("0.01")
ZERO = Decimal(0)


def to_minor(amount: Decimal | str | int) -> int:
    """Convert a decimal amount to integer minor units (two decimals), rounding half up."""
    value = Decimal(amount)
    return int((value / MINOR).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_minor(minor: int) -> Decimal:
    """Convert integer minor units to a Decimal with two decimals."""
    return (Decimal(minor) * MINOR).quantize(MINOR)


def q2(amount: Decimal) -> Decimal:
    """Round to two decimals, half up."""
    return amount.quantize(MINOR, rounding=ROUND_HALF_UP)


def mul_rate(amount: Decimal, rate: Decimal) -> Decimal:
    """Multiply an amount by a rate without losing precision to the default context."""
    with localcontext() as ctx:
        ctx.prec = 40
        return amount * rate


def div_rate(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide two rates with enough precision for cross-rate work."""
    with localcontext() as ctx:
        ctx.prec = 40
        return numerator / denominator


def fmt(amount: Decimal) -> str:
    """Format an amount with thousands separators and two decimals."""
    return f"{q2(amount):,.2f}"
