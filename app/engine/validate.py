"""Validation checks, required fields and confidence (docs/03_lld.md §3.9, §4.13-4.14).

A failed check never stops the parse; it is reported with the numbers involved. ERROR
issues block PARSED, WARNING issues do not. A check is skipped when an input is missing.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.engine.fields import DISCOUNT_INSTRUMENTS, REPO_DEAL_TYPES, Severity, required_fields
from app.engine.models import FieldValue, Issue, MarketProfile
from app.engine.normalize import ISIN_RE, isin_check_digit_ok

__all__ = ["days_30_360", "isin_check_digit_ok", "low_confidence", "missing_required", "validate"]

CENT = Decimal("0.01")


def _value(deal: dict[str, Any], path: str) -> Any:
    if path.startswith("repo."):
        return (deal.get("repo") or {}).get(path.removeprefix("repo."))
    return deal.get(path)


def missing_required(deal: dict[str, Any]) -> list[str]:
    return [p for p in required_fields(deal.get("deal_type")) if _value(deal, p) is None]


def low_confidence(fields: dict[str, FieldValue], deal_type: str | None, threshold: float) -> list[str]:
    return [p for p in required_fields(deal_type) if p in fields and fields[p].confidence < threshold]


def days_30_360(d1: date, d2: date) -> int:
    """Same convention as tools/generate_mock_slips.py."""
    dd1, dd2 = min(d1.day, 30), d2.day
    if dd1 == 30 and dd2 == 31:
        dd2 = 30
    return (d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (dd2 - dd1)


def _price_tol(face_value: Decimal, price: Decimal) -> Decimal:
    """Tolerance for amounts computed from a price rounded to its printed decimals."""
    exponent = price.as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    return max(CENT, face_value * Decimal("0.5") * Decimal(10) ** -places / 100)


def _q(d: Decimal) -> Decimal:
    return d.quantize(CENT, ROUND_HALF_UP)


def validate(deal: dict[str, Any], profile: MarketProfile | None) -> list[Issue]:
    issues: list[Issue] = []
    g = deal.get

    def fail(
        code: str,
        msg: str,
        fields: tuple[str, ...],
        *,
        expected: Any = None,
        actual: Any = None,
        tol: Any = None,
        severity: Severity = Severity.ERROR,
    ) -> None:
        issues.append(
            Issue(
                code,
                severity,
                msg,
                fields,
                None if expected is None else str(expected),
                None if actual is None else str(actual),
                None if tol is None else str(tol),
            )
        )

    def close(expected: Decimal, actual: Decimal, tol: Decimal) -> bool:
        return abs(expected - actual) <= tol

    isin = g("isin")
    if isin:
        if not ISIN_RE.fullmatch(isin):
            fail("ISIN_FORMAT", f"{isin} is not a 12-character ISIN", ("isin",))
        elif not isin_check_digit_ok(isin):
            fail("ISIN_CHECK_DIGIT", f"ISIN {isin} fails the check-digit test", ("isin",))

    trade, settle, maturity = g("trade_date"), g("settlement_date"), g("maturity_date")
    if trade and settle and settle < trade:
        fail(
            "SETTLEMENT_BEFORE_TRADE",
            f"settlement {settle} is before trade {trade}",
            ("settlement_date", "trade_date"),
        )
    if settle and maturity and maturity <= settle:
        fail(
            "MATURITY_NOT_AFTER_SETTLEMENT",
            f"maturity {maturity} is not after settlement {settle}",
            ("maturity_date", "settlement_date"),
        )
    weekend = profile.weekend if profile else frozenset({5, 6})
    repo = g("repo") or {}
    for path, d in (
        ("settlement_date", settle),
        ("repo.leg1_date", repo.get("leg1_date")),
        ("repo.leg2_date", repo.get("leg2_date")),
    ):
        if isinstance(d, date) and d.weekday() in weekend:
            fail("WEEKEND_SETTLEMENT", f"{path} {d} falls on a weekend", (path,))

    fv, price, principal = g("face_value"), g("price"), g("principal_amount")
    accrued, consideration, stamp = g("accrued_interest"), g("consideration"), g("stamp_duty")
    if fv is not None and price is not None and principal is not None:
        expected, tol = _q(fv * price / 100), _price_tol(fv, price)
        if not close(expected, principal, tol):
            fail(
                "PRINCIPAL_MISMATCH",
                f"face value x price / 100 = {expected}, slip shows {principal}",
                ("principal_amount", "face_value", "price"),
                expected=expected,
                actual=principal,
                tol=tol,
            )

    instrument, deal_type = g("instrument_type"), g("deal_type")
    if deal_type == "OUTRIGHT" and principal is not None and consideration is not None:
        expected = principal + (accrued or 0)
        if stamp is not None and g("buy_sell") == "BUY":  # the buyer pays the stamp duty
            expected += stamp
        if not close(expected, consideration, CENT):
            fail(
                "CONSIDERATION_MISMATCH",
                f"principal + accrued{' + stamp duty' if stamp is not None else ''} = {expected}, "
                f"slip shows {consideration}",
                ("consideration", "principal_amount"),
                expected=expected,
                actual=consideration,
                tol=CENT,
            )

    coupon, days, last_cpn = g("coupon_rate"), g("accrued_days"), g("last_coupon_date")
    day_count = g("day_count")
    if day_count and last_cpn and settle and days is not None and deal_type not in REPO_DEAL_TYPES:
        expected_days = (
            days_30_360(last_cpn, settle) if day_count.startswith("30") else (settle - last_cpn).days
        )
        if expected_days != days:
            fail(
                "ACCRUED_DAYS_MISMATCH",
                f"{day_count} days {last_cpn} -> {settle} = {expected_days}, slip shows {days}",
                ("accrued_days",),
                expected=expected_days,
                actual=days,
                severity=Severity.WARNING,
            )
    if day_count and fv is not None and coupon is not None and days is not None and accrued is not None:
        basis = 360 if day_count in ("30/360", "30E/360", "ACT/360") else 365
        expected = _q(fv * coupon / 100 * days / basis)
        if not close(expected, accrued, CENT):
            fail(
                "ACCRUED_MISMATCH",
                f"face value x coupon x days / {basis} = {expected}, slip shows {accrued}",
                ("accrued_interest",),
                expected=expected,
                actual=accrued,
                tol=CENT,
            )

    if instrument in DISCOUNT_INSTRUMENTS and fv is not None and consideration is not None:
        discount = g("discount_amount")
        if discount is not None and not close(fv - consideration, discount, CENT):
            fail(
                "DISCOUNT_MISMATCH",
                f"face value - consideration = {fv - consideration}, slip shows {discount}",
                ("discount_amount",),
                expected=fv - consideration,
                actual=discount,
                tol=CENT,
            )
        if price is not None and principal is None:
            expected, tol = _q(fv * price / 100), _price_tol(fv, price)
            if not close(expected, consideration, tol):
                fail(
                    "DISCOUNT_PRICE_MISMATCH",
                    f"face value x price / 100 = {expected}, slip shows {consideration}",
                    ("consideration", "price"),
                    expected=expected,
                    actual=consideration,
                    tol=tol,
                )

    qty, per_unit = g("quantity"), g("face_value_per_unit")
    if qty is not None and per_unit is not None and fv is not None and qty * per_unit != fv:
        fail(
            "QUANTITY_MISMATCH",
            f"{qty} x {per_unit} = {qty * per_unit}, slip shows face value {fv}",
            ("quantity", "face_value_per_unit", "face_value"),
            expected=qty * per_unit,
            actual=fv,
        )

    if deal_type in REPO_DEAL_TYPES and repo:
        for leg in ("leg1", "leg2"):
            lp, la, lai = (
                repo.get(f"{leg}_price"),
                repo.get(f"{leg}_amount"),
                repo.get(f"{leg}_accrued_interest"),
            )
            if fv is not None and lp is not None and la is not None:
                expected, tol = _q(fv * lp / 100 + (lai or 0)), _price_tol(fv, lp)
                if not close(expected, la, tol):
                    fail(
                        "REPO_LEG_AMOUNT_MISMATCH",
                        f"{leg}: face value x price / 100 + accrued = {expected}, slip shows {la}",
                        (f"repo.{leg}_amount",),
                        expected=expected,
                        actual=la,
                        tol=tol,
                    )
        a1, a2, interest = repo.get("leg1_amount"), repo.get("leg2_amount"), repo.get("repo_interest")
        if a1 is not None and a2 is not None and interest is not None and not close(a1 + interest, a2, CENT):
            fail(
                "REPO_INTEREST_MISMATCH",
                f"leg 1 + repo interest = {a1 + interest}, slip shows leg 2 {a2}",
                ("repo.repo_interest",),
                expected=a1 + interest,
                actual=a2,
                tol=CENT,
            )
        d1, d2, rdays = repo.get("leg1_date"), repo.get("leg2_date"), repo.get("repo_days")
        if d1 and d2 and rdays is not None and (d2 - d1).days != rdays:
            fail(
                "REPO_DAYS_MISMATCH",
                f"legs are {(d2 - d1).days} days apart, slip shows {rdays}",
                ("repo.repo_days",),
                expected=(d2 - d1).days,
                actual=rdays,
            )
        rate = repo.get("repo_rate")
        if a1 is not None and rate is not None and rdays and interest is not None:
            basis = 360 if repo.get("day_count") == "ACT/360" else 365
            expected = _q(a1 * rate / 100 * rdays / basis)
            if not close(expected, interest, CENT):
                fail(
                    "REPO_INTEREST_CALC",
                    f"leg 1 x rate x days / {basis} = {expected}, slip shows {interest}",
                    ("repo.repo_interest",),
                    expected=expected,
                    actual=interest,
                    tol=CENT,
                    severity=Severity.WARNING,
                )
    return issues
