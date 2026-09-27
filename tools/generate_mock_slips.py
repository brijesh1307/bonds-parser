"""Generate mock bond deal slips (PDF) plus expected parser output (JSON).

Four deal types, each with a deliberately different layout so the parser
has to cope with real-world variety:

  1. G-Sec outright purchase (NDS-OM)     - two-column label/value table
  2. Corporate bond / NCD outright sale    - header block + horizontal grid
  3. T-Bill primary auction allotment      - letter / memo style prose
  4. Market repo (reverse repo) in G-Sec   - two-leg table

All figures are computed (accrued interest, consideration, YTM, repo leg 2),
so the slips are internally consistent. All names and ISINs are fictitious.

Usage:  python tools/generate_mock_slips.py
Output: samples/mock/*.pdf and samples/mock/expected/*.json
"""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "samples" / "mock"
EXPECTED_DIR = OUT_DIR / "expected"

OUR_ENTITY = "Orca Treasury Bank Ltd (MOCK)"
MOCK_FOOTER = "MOCK DATA - FOR PARSER TESTING ONLY - NOT A REAL TRANSACTION"

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=styles["Title"], fontSize=15, spaceAfter=4)
SUB = ParagraphStyle("s", parent=styles["Normal"], fontSize=9, alignment=1, textColor=colors.grey)
BODY = ParagraphStyle("b", parent=styles["Normal"], fontSize=10, leading=14)
FOOT = ParagraphStyle("f", parent=styles["Normal"], fontSize=7, alignment=1, textColor=colors.red)


# --------------------------------------------------------------------------- helpers

def q(x: Decimal | float, places: str = "0.01") -> Decimal:
    return Decimal(str(x)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def isin_with_check(base11: str) -> str:
    """Append the ISO 6166 check digit to an 11-char ISIN body."""
    digits = "".join(str(int(c, 36)) for c in base11)
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return base11 + str((10 - total % 10) % 10)


def inr(amount: Decimal) -> str:
    """Format with Indian digit grouping: 12,34,56,789.00"""
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{sign}{whole}.{frac}"


def days_30_360(d1: date, d2: date) -> int:
    dd1, dd2 = min(d1.day, 30), d2.day
    if dd1 == 30 and dd2 == 31:
        dd2 = 30
    return (d2.year - d1.year) * 360 + (d2.month - d1.month) * 30 + (dd2 - dd1)


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, d.day)


def solve_ytm(dirty: float, cashflows: list[tuple[float, float]], freq: int) -> float:
    """Bisection on yield; cashflows = [(t_in_periods, amount_per_100)]."""
    lo, hi = -0.5, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        pv = sum(cf / (1 + mid / freq) ** t for t, cf in cashflows)
        lo, hi = (mid, hi) if pv > dirty else (lo, mid)
    return (lo + hi) / 2


def weekday_check(*ds: date) -> None:
    for d in ds:
        assert d.weekday() < 5, f"{d} is a weekend"


def fmt_d(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def kv_table(rows: list[tuple[str, str]], col_widths=(65 * mm, 105 * mm)) -> Table:
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def grid_table(rows: list[list[str]], col_widths=None, header_bg=colors.HexColor("#1f3b63")) -> Table:
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    return t


def signatures() -> Table:
    t = Table([["Dealer", "Checker (Mid Office)", "Authorised Signatory (Back Office)"],
               ["\n\n________________", "\n\n________________", "\n\n________________"]],
              colWidths=(56 * mm, 56 * mm, 58 * mm))
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    return t


def build_pdf(path: Path, story: list) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=path.stem, author="bonds-parser mock generator")
    doc.build(story + [Spacer(1, 8 * mm), Paragraph(MOCK_FOOTER, FOOT)])


def write_expected(name: str, data: dict) -> None:
    def conv(o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, date):
            return o.isoformat()
        raise TypeError(o)
    (EXPECTED_DIR / f"{name}.json").write_text(json.dumps(data, indent=2, default=conv), encoding="utf-8")


# --------------------------------------------------------------------------- 1. G-Sec outright

def gsec_outright() -> None:
    name = "01_gsec_outright_purchase"
    isin = isin_with_check("IN0020240A7")
    trade, settle = date(2026, 9, 24), date(2026, 9, 25)
    weekday_check(trade, settle)
    maturity, coupon = date(2034, 4, 8), Decimal("7.10")
    last_cpn, next_cpn = date(2026, 4, 8), date(2026, 10, 8)
    fv, price = Decimal("50000000"), Decimal("101.2350")

    acc_days = days_30_360(last_cpn, settle)
    accrued = q(fv * coupon / 100 * acc_days / 360)
    principal = q(fv * price / 100)
    consideration = principal + accrued

    # YTM (semi-annual, 30/360 street convention)
    w = days_30_360(settle, next_cpn) / 180
    cfs, d, k = [], next_cpn, 0
    while d <= maturity:
        cfs.append((w + k, float(coupon) / 2 + (100 if d == maturity else 0)))
        d, k = add_months(d, 6), k + 1
    dirty = float(price) + float(coupon) * acc_days / 360
    ytm = q(solve_ytm(dirty, cfs, 2) * 100, "0.0001")

    deal_id = "GS/NDSOM/2026/004571"
    rows = [
        ("Deal Reference No.", deal_id),
        ("Deal Type", "OUTRIGHT PURCHASE"),
        ("Trading Platform", "NDS-OM (Anonymous Order Matching)"),
        ("Trade Date", fmt_d(trade)),
        ("Trade Time", "11:42:17"),
        ("Settlement Date", f"{fmt_d(settle)} (T+1)"),
        ("Security Type", "Central Government Security (Dated)"),
        ("Security Description", "7.10% GS 2034"),
        ("ISIN", isin),
        ("Coupon Rate", f"{coupon}% p.a. (Semi-Annual)"),
        ("Maturity Date", fmt_d(maturity)),
        ("Last Coupon Date", fmt_d(last_cpn)),
        ("Face Value (INR)", inr(fv)),
        ("Clean Price", f"{price}"),
        ("Yield to Maturity (%)", f"{ytm}"),
        ("Principal Amount (INR)", inr(principal)),
        ("Accrued Interest Days", f"{acc_days} (30/360)"),
        ("Accrued Interest (INR)", inr(accrued)),
        ("Total Consideration (INR)", inr(consideration)),
        ("Counterparty", "Anonymous (NDS-OM) - CCIL Novated"),
        ("Settlement Mode", "DVP-III through CCIL"),
        ("Portfolio / Book", "AFS - Treasury"),
        ("SGL / CSGL A/c", "SGL-0457 (RBI)"),
        ("Dealer", "R. Mehta"),
    ]
    story = [
        Paragraph(OUR_ENTITY, TITLE),
        Paragraph("Treasury Department - Fixed Income Desk", SUB),
        Spacer(1, 4 * mm),
        Paragraph("<b>DEAL SLIP - GOVERNMENT SECURITIES</b>", ParagraphStyle("h", parent=BODY, alignment=1, fontSize=12)),
        Spacer(1, 4 * mm),
        kv_table(rows),
        Spacer(1, 6 * mm),
        signatures(),
    ]
    build_pdf(OUT_DIR / f"{name}.pdf", story)
    write_expected(name, {
        "deal_id": deal_id, "deal_type": "OUTRIGHT", "instrument_type": "GSEC", "buy_sell": "BUY",
        "platform": "NDS-OM", "trade_date": trade, "settlement_date": settle,
        "security_name": "7.10% GS 2034", "isin": isin, "coupon_rate": coupon, "coupon_frequency": 2,
        "maturity_date": maturity, "last_coupon_date": last_cpn, "day_count": "30/360",
        "face_value": fv, "quantity": None, "price": price, "yield": ytm,
        "principal_amount": principal, "accrued_days": acc_days, "accrued_interest": accrued,
        "consideration": consideration, "currency": "INR",
        "counterparty": "Anonymous (NDS-OM) - CCIL Novated", "settlement_mode": "DVP-III",
        "portfolio": "AFS - Treasury", "dealer": "R. Mehta",
    })


# --------------------------------------------------------------------------- 2. Corporate bond / NCD

def ncd_outright() -> None:
    name = "02_corporate_ncd_outright_sale"
    isin = isin_with_check("INE917S0703")
    trade, settle = date(2026, 9, 23), date(2026, 9, 24)
    weekday_check(trade, settle)
    maturity, coupon = date(2029, 3, 15), Decimal("8.25")
    last_cpn, next_cpn = date(2026, 3, 15), date(2027, 3, 15)
    fv_per_bond, qty = Decimal("100000"), 250
    fv = fv_per_bond * qty
    price = Decimal("100.4523")

    acc_days = (settle - last_cpn).days
    accrued = q(fv * coupon / 100 * acc_days / 365)
    principal = q(fv * price / 100)
    consideration = principal + accrued

    # YTM (annual, actual/actual)
    w = (next_cpn - settle).days / 365
    cfs = [(w, float(coupon)), (w + 1, float(coupon)), (w + 2, float(coupon) + 100)]
    dirty = float(price) + float(coupon) * acc_days / 365
    ytm = q(solve_ytm(dirty, cfs, 1) * 100, "0.0001")

    deal_id = "CB-OTC-260923-0112"
    header = kv_table([
        ("Ticket No", deal_id),
        ("Transaction", "SELL - Outright (OTC, reported on NSE RFQ)"),
        ("Deal Date / Time", f"{fmt_d(trade)}  15:07"),
        ("Value Date", fmt_d(settle)),
        ("Counterparty", "Meridian Asset Management Co. Ltd (MOCK) - Meridian Short Term Fund"),
        ("Broker", "Direct (No Broker)"),
    ], col_widths=(45 * mm, 125 * mm))

    sec = grid_table([
        ["ISIN", "Security Name", "Issuer", "Rating", "Coupon", "Maturity"],
        [isin, "8.25% SIFL NCD 2029 (Secured)", "Sunrise Infra Finance Ltd", "CRISIL AA+",
         f"{coupon}% Annual", fmt_d(maturity)],
    ], col_widths=(25 * mm, 43 * mm, 36 * mm, 20 * mm, 24 * mm, 22 * mm))

    amt = grid_table([
        ["No. of Bonds", "FV per Bond", "Total Face Value", "Clean Price", "YTM %"],
        [str(qty), inr(fv_per_bond), inr(fv), f"{price}", f"{ytm}"],
    ], col_widths=(30 * mm, 32 * mm, 40 * mm, 34 * mm, 34 * mm))
    amt2 = grid_table([
        ["Principal Amount", "Interest Days", "Accrued Interest", "Net Consideration"],
        [inr(principal), str(acc_days), inr(accrued), inr(consideration)],
    ], col_widths=(45 * mm, 30 * mm, 45 * mm, 50 * mm))

    settle_tbl = kv_table([
        ("Settlement", "Through NSE Clearing Ltd (DVP-I), T+1"),
        ("Day Count", "Actual/Actual"),
        ("Depository", "NSDL  -  DP ID IN300999 / Client ID 10458822"),
        ("Last Interest Paid On", fmt_d(last_cpn)),
        ("Book", "HFT - Credit"),
        ("Dealer", "S. Iyer"),
    ], col_widths=(45 * mm, 125 * mm))

    story = [
        Paragraph(OUR_ENTITY, TITLE),
        Paragraph("Corporate Bond Desk  |  Deal Confirmation Slip", SUB),
        Spacer(1, 5 * mm), header,
        Spacer(1, 4 * mm), Paragraph("<b>Security Details</b>", BODY), Spacer(1, 1 * mm), sec,
        Spacer(1, 4 * mm), Paragraph("<b>Amount Details (INR)</b>", BODY), Spacer(1, 1 * mm), amt, Spacer(1, 2 * mm), amt2,
        Spacer(1, 4 * mm), Paragraph("<b>Settlement Instructions</b>", BODY), Spacer(1, 1 * mm), settle_tbl,
        Spacer(1, 6 * mm), signatures(),
    ]
    build_pdf(OUT_DIR / f"{name}.pdf", story)
    write_expected(name, {
        "deal_id": deal_id, "deal_type": "OUTRIGHT", "instrument_type": "CORPORATE_BOND", "buy_sell": "SELL",
        "platform": "OTC / NSE RFQ", "trade_date": trade, "settlement_date": settle,
        "security_name": "8.25% SIFL NCD 2029 (Secured)", "issuer": "Sunrise Infra Finance Ltd",
        "credit_rating": "CRISIL AA+", "isin": isin, "coupon_rate": coupon, "coupon_frequency": 1,
        "maturity_date": maturity, "last_coupon_date": last_cpn, "day_count": "ACT/ACT",
        "face_value": fv, "face_value_per_unit": fv_per_bond, "quantity": qty, "price": price, "yield": ytm,
        "principal_amount": principal, "accrued_days": acc_days, "accrued_interest": accrued,
        "consideration": consideration, "currency": "INR",
        "counterparty": "Meridian Asset Management Co. Ltd (MOCK) - Meridian Short Term Fund",
        "broker": None, "settlement_mode": "DVP-I", "portfolio": "HFT - Credit", "dealer": "S. Iyer",
    })


# --------------------------------------------------------------------------- 3. T-Bill primary auction

def tbill_auction() -> None:
    name = "03_tbill_primary_auction_allotment"
    isin = isin_with_check("IN002026Y18")
    auction, settle = date(2026, 9, 23), date(2026, 9, 24)
    weekday_check(auction, settle)
    tenor = 182
    maturity = date.fromordinal(settle.toordinal() + tenor)
    bid_amt = Decimal("300000000")
    allotted = Decimal("250000000")
    cutoff_yield = Decimal("5.6512")
    price = q(Decimal(100) / (1 + cutoff_yield / 100 * tenor / 364), "0.0001")
    consideration = q(allotted * price / 100)
    discount = allotted - consideration

    deal_id = "TB/AUC/2026/182D/039"
    body = f"""
    Ref: <b>{deal_id}</b><br/>
    Date: {fmt_d(auction)}<br/><br/>
    To: The Head - Treasury Back Office<br/><br/>
    <b>Sub: Allotment in Primary Auction of {tenor} Day Treasury Bill</b><br/><br/>
    We have to inform you that in the auction of the <b>{tenor} Day Treasury Bill</b>
    (ISIN <b>{isin}</b>) conducted by the Reserve Bank of India on E-Kuber on
    <b>{fmt_d(auction)}</b>, the Bank participated through a <b>Competitive Bid</b>
    for a Face Value of INR {inr(bid_amt)} at a yield of 5.6400%.<br/><br/>
    The bid was accepted on a partial allotment basis. Details of the allotment are given below:
    """
    tbl = kv_table([
        ("Security", f"{tenor} DTB {maturity.strftime('%d%m%Y')}"),
        ("Auction Date", fmt_d(auction)),
        ("Date of Issue / Settlement", fmt_d(settle)),
        ("Date of Maturity", fmt_d(maturity)),
        ("Bid Type", "Competitive"),
        ("Amount Bid (FV, INR)", inr(bid_amt)),
        ("Amount Allotted (FV, INR)", inr(allotted)),
        ("Cut-off Yield (%)", f"{cutoff_yield}"),
        ("Cut-off Price (per INR 100)", f"{price}"),
        ("Discount Amount (INR)", inr(discount)),
        ("Amount Payable (INR)", inr(consideration)),
        ("Debit A/c", "RBI Current Account - 0457"),
        ("Credit to", "SGL A/c SGL-0457"),
        ("Portfolio", "HTM - SLR"),
    ])
    story = [
        Paragraph(OUR_ENTITY, TITLE),
        Paragraph("Treasury Front Office  -  Primary Market Desk", SUB),
        Spacer(1, 6 * mm), Paragraph(body, BODY), Spacer(1, 3 * mm), tbl,
        Spacer(1, 4 * mm),
        Paragraph("Kindly arrange to fund the RBI current account on the settlement date.", BODY),
        Spacer(1, 6 * mm),
        Paragraph("Yours faithfully,<br/><br/>(A. Kulkarni)<br/>Dealer - Primary Market", BODY),
    ]
    build_pdf(OUT_DIR / f"{name}.pdf", story)
    write_expected(name, {
        "deal_id": deal_id, "deal_type": "PRIMARY_AUCTION", "instrument_type": "TBILL", "buy_sell": "BUY",
        "platform": "RBI E-Kuber", "trade_date": auction, "settlement_date": settle,
        "security_name": f"{tenor} DTB {maturity.strftime('%d%m%Y')}", "isin": isin,
        "tenor_days": tenor, "maturity_date": maturity, "bid_type": "COMPETITIVE",
        "bid_amount": bid_amt, "face_value": allotted, "price": price, "yield": cutoff_yield,
        "discount_amount": discount, "consideration": consideration, "currency": "INR",
        "counterparty": "Reserve Bank of India", "portfolio": "HTM - SLR", "dealer": "A. Kulkarni",
    })


# --------------------------------------------------------------------------- 4. Market repo (two legs)

def market_repo() -> None:
    name = "04_market_repo_reverse_repo"
    isin = isin_with_check("IN0020210C5")
    trade = date(2026, 9, 25)
    leg1, leg2 = date(2026, 9, 25), date(2026, 9, 28)
    weekday_check(trade, leg1, leg2)
    repo_days = (leg2 - leg1).days
    coupon, maturity = Decimal("6.99"), date(2031, 6, 17)
    last_cpn = date(2026, 6, 17)
    fv = Decimal("100000000")
    repo_rate = Decimal("5.40")
    leg1_price = Decimal("99.8500")

    acc1_days = days_30_360(last_cpn, leg1)
    acc1 = q(fv * coupon / 100 * acc1_days / 360)
    leg1_amount = q(fv * leg1_price / 100) + acc1
    repo_interest = q(leg1_amount * repo_rate / 100 * repo_days / 365)
    leg2_amount = leg1_amount + repo_interest
    acc2_days = days_30_360(last_cpn, leg2)
    acc2 = q(fv * coupon / 100 * acc2_days / 360)
    leg2_price = q((leg2_amount - acc2) / fv * 100, "0.0001")

    deal_id = "REPO/MKT/2026/000918"
    header = kv_table([
        ("Deal ID", deal_id),
        ("Deal Type", "REVERSE REPO (Lending Funds) - Market Repo"),
        ("Trade Date", fmt_d(trade)),
        ("Counterparty", "Alpha Co-operative Bank Ltd (MOCK)"),
        ("Platform", "CROMS (CCIL)"),
        ("Collateral Security", "6.99% GS 2031"),
        ("ISIN", isin),
        ("Face Value of Collateral (INR)", inr(fv)),
        ("Repo Rate (% p.a.)", f"{repo_rate}"),
        ("Repo Period (Days)", str(repo_days)),
        ("Day Count (Repo Interest)", "Actual/365"),
    ])
    legs = grid_table([
        ["Leg", "Date", "Direction", "Price", "Accr Days", "Accrued Int.", "Settlement Amount"],
        ["First Leg", fmt_d(leg1), "Pay Funds", f"{leg1_price}", str(acc1_days), inr(acc1), inr(leg1_amount)],
        ["Second Leg", fmt_d(leg2), "Receive Funds", f"{leg2_price}", str(acc2_days), inr(acc2), inr(leg2_amount)],
    ], col_widths=(20 * mm, 24 * mm, 25 * mm, 18 * mm, 17 * mm, 28 * mm, 38 * mm))
    story = [
        Paragraph(OUR_ENTITY, TITLE),
        Paragraph("Money Market Desk  -  Repo Deal Ticket", SUB),
        Spacer(1, 5 * mm), header,
        Spacer(1, 5 * mm), Paragraph("<b>Leg Details</b>", BODY), Spacer(1, 1 * mm), legs,
        Spacer(1, 3 * mm),
        Paragraph(f"Repo Interest (INR): <b>{inr(repo_interest)}</b>", BODY),
        Paragraph("Settlement: DVP-III through CCIL  |  Portfolio: Money Market - Liquidity  |  Dealer: P. Nair", BODY),
        Spacer(1, 6 * mm), signatures(),
    ]
    build_pdf(OUT_DIR / f"{name}.pdf", story)
    write_expected(name, {
        "deal_id": deal_id, "deal_type": "REVERSE_REPO", "instrument_type": "GSEC", "buy_sell": "BUY",
        "platform": "CROMS (CCIL)", "trade_date": trade, "settlement_date": leg1,
        "security_name": "6.99% GS 2031", "isin": isin, "coupon_rate": coupon, "maturity_date": maturity,
        "face_value": fv, "price": leg1_price, "accrued_interest": acc1, "consideration": leg1_amount,
        "currency": "INR", "counterparty": "Alpha Co-operative Bank Ltd (MOCK)", "settlement_mode": "DVP-III",
        "portfolio": "Money Market - Liquidity", "dealer": "P. Nair",
        "repo": {
            "repo_rate": repo_rate, "repo_days": repo_days, "day_count": "ACT/365",
            "leg1_date": leg1, "leg1_price": leg1_price, "leg1_accrued_days": acc1_days,
            "leg1_accrued_interest": acc1, "leg1_amount": leg1_amount,
            "leg2_date": leg2, "leg2_price": leg2_price, "leg2_accrued_days": acc2_days,
            "leg2_accrued_interest": acc2, "leg2_amount": leg2_amount, "repo_interest": repo_interest,
        },
    })


if __name__ == "__main__":
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    for fn in (gsec_outright, ncd_outright, tbill_auction, market_repo):
        fn()
    for p in sorted(OUT_DIR.glob("*.pdf")):
        print("wrote", p.relative_to(ROOT))
