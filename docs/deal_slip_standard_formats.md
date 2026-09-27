# Bond Deal Slip – Standard Formats (Reference)

This is the reference the parser is built against. It lists the deal slip / confirmation
formats commonly seen in the Indian fixed-income market, what each one contains, and the
different labels banks and platforms use for the same field.

> These are the *typical* layouts and fields seen across banks, PDs, AMCs and brokers.
> There is no single mandated format. Every institution prints its own slip, so the
> labels differ even when the data is the same. The parser handles this with
> **one canonical output schema** (section 3) plus a **label synonym map** (section 4).

---

## 1. Where slips come from

| Source | Typical document | Format | Layout style |
|---|---|---|---|
| Internal front office (dealer) | Deal slip / deal ticket | PDF, Excel, printed scan | Two-column label/value table |
| NDS-OM (RBI, G-Sec secondary) | Trade confirmation / deal slip | PDF / CSV export | Label/value table |
| CCIL (G-Sec, repo, TREPS) | Trade confirmation, settlement obligation report | PDF / CSV | Grid (one row per trade) |
| RBI E-Kuber (primary auctions) | Auction result / allotment advice | PDF / letter | Memo or letter with a table |
| NSE / BSE RFQ (corporate bonds) | RFQ deal confirmation, NCL / ICCL settlement advice | PDF / Excel | Horizontal grid |
| EBP (primary private placement) | Bid confirmation, allotment advice | PDF / Excel | Grid |
| Brokers | Broker contract note / deal confirmation | PDF / email body | Varies, often in the email body |
| Counterparty bank / AMC | Counterparty confirmation | PDF / email | Varies |
| Custodian / depository (NSDL, CDSL) | Settlement / delivery advice | PDF | Grid |
| International (future) | SWIFT MT515 / MT518, Bloomberg TOMS ticket | Text / PDF | Tagged fields (`:35B:`, `:98A:`) |

---

## 2. Standard formats by deal type

Every format below shares the **common header** (2.0). Only the fields specific to each
deal type are listed under it.

### 2.0 Common header (all slips)

| Field | Example |
|---|---|
| Our entity / desk | Orca Treasury Bank Ltd – Fixed Income Desk |
| Deal / ticket / reference no. | GS/NDSOM/2026/004571 |
| Deal type & direction | OUTRIGHT PURCHASE / SELL / REVERSE REPO |
| Trade date (+ time) | 24-Sep-2026 11:42:17 |
| Settlement / value date | 25-Sep-2026 (T+1) |
| Counterparty | Name, or "Anonymous – CCIL novated" |
| Broker | Name / "Direct" |
| Portfolio / book / category | HTM / AFS / HFT (+ sub-book) |
| Dealer, checker, authoriser | Names / signatures |

### 2.1 G-Sec / SDL outright – secondary (NDS-OM or OTC)
*Mock sample: `samples/mock/01_gsec_outright_purchase.pdf`*

| Field | Notes |
|---|---|
| Security description | `7.10% GS 2034`, `7.25% MH SDL 2033` |
| ISIN | `IN00…` (G-Sec), `IN1…/IN2…` (SDL) |
| Coupon rate, frequency | Semi-annual for G-Sec / SDL |
| Maturity date, last / next coupon date | |
| Face value | Quoted in INR, sometimes in crore (`5.00 Cr`) |
| Clean price, YTM | Price to 4 decimals, yield to 4 decimals |
| Principal amount | FV × price / 100 |
| Accrued days, accrued interest | Day count **30/360** |
| Total consideration | Principal + accrued |
| Settlement mode | DVP-III via CCIL |
| SGL / CSGL account | |

### 2.2 T-Bill / CMB – secondary
Same as 2.1, but a **discount instrument**: no coupon and no accrued interest. The price is
derived from the yield on an **ACT/364** basis. Security looks like `182 DTB 25032027`.

### 2.3 Primary auction – G-Sec / SDL / T-Bill (RBI E-Kuber)
*Mock sample: `samples/mock/03_tbill_primary_auction_allotment.pdf`*

| Field | Notes |
|---|---|
| Auction date, issue / settlement date | |
| Notified amount, tenor | |
| Bid type | Competitive / Non-competitive |
| Bid amount, bid yield / price | |
| Allotted amount | Can be a partial allotment |
| Cut-off yield / price | |
| Discount amount (T-Bill) / coupon (G-Sec) | |
| Amount payable | |
| Debit account, credit SGL | |

Usually a **letter or memo**, with the key facts spread across prose as well as a table.

### 2.4 Corporate bond / NCD / PSU bond – secondary (OTC, RFQ)
*Mock sample: `samples/mock/02_corporate_ncd_outright_sale.pdf`*

| Field | Notes |
|---|---|
| ISIN | `INE…` |
| Security name, issuer | |
| Credit rating + agency | CRISIL / ICRA / CARE / India Ratings |
| Secured / unsecured, put / call dates | |
| Coupon, frequency | Often annual |
| No. of bonds × FV per bond | FV per bond: 1,00,000 / 10,00,000 |
| Total face value | |
| Clean price, YTM (or YTC / YTP) | |
| Interest days, accrued interest | Day count **ACT/ACT** or ACT/365 |
| Net consideration | |
| Settlement | NCL (NSE) / ICCL (BSE), DVP-I, T+0 / T+1 |
| Depository | NSDL / CDSL, DP ID, client ID |
| Stamp duty | Sometimes shown separately |

Often laid out as **horizontal grids** (headers across, values below).

### 2.5 Corporate bond – primary / private placement (EBP)
Issue name, EBP bid ID, bid / allotment amount, coupon or yield bid, cut-off, pay-in date,
allotment date, deemed date of allotment, ISIN (sometimes allotted later).

### 2.6 Money market – CP / CD
Discount instruments. Issuer, rating, ISIN, issue date, maturity date, face value,
discount rate / yield, price, amount payable, IPA (issuing & paying agent), depository.
Day count ACT/365.

### 2.7 Market repo (G-Sec or corporate bond collateral)
*Mock sample: `samples/mock/04_market_repo_reverse_repo.pdf`*

| Field | Notes |
|---|---|
| Repo vs reverse repo | Borrowing vs lending funds |
| Collateral security, ISIN, FV of collateral | |
| Repo rate, repo period (days) | Repo interest ACT/365 |
| Haircut / margin | Common for corporate bond repo |
| **First leg**: date, price, accrued days / interest, amount | |
| **Second leg**: date, price, accrued days / interest, amount | |
| Repo interest | Leg 2 amount − leg 1 amount |
| Platform | CROMS (CCIL) / OTC |

A **two-leg grid** is typical.

### 2.8 TREPS (tri-party repo, CCIL)
There is no single collateral ISIN; it is a **basket**. Borrow / lend, amount, rate, tenor
(O/N, T/N, term), start and end date, maturity amount, interest.

### 2.9 RBI LAF / SDF / MSF
The counterparty is RBI. Operation type (VRR / VRRR / SDF / MSF), amount, rate, tenor,
start / reversal date, interest.

---

## 3. Canonical output schema

This is what the parser returns for **every** slip, whatever the source layout. Fields that
don't apply are `null`. Amounts and rates are **decimal strings** (no float rounding), and
dates are ISO `YYYY-MM-DD`. This matches `samples/mock/expected/*.json`.

| Group | Field | Type | Values / format |
|---|---|---|---|
| **Identity** | `deal_id` | str | |
| | `deal_type` | enum | `OUTRIGHT`, `PRIMARY_AUCTION`, `PRIMARY_PLACEMENT`, `REPO`, `REVERSE_REPO`, `TREPS_BORROW`, `TREPS_LEND`, `LAF` |
| | `instrument_type` | enum | `GSEC`, `SDL`, `TBILL`, `CMB`, `CORPORATE_BOND`, `PSU_BOND`, `CP`, `CD` |
| | `buy_sell` | enum | `BUY`, `SELL` (reverse repo = BUY leg 1) |
| | `platform` | str | NDS-OM, OTC / NSE RFQ, RBI E-Kuber, CROMS (CCIL), … |
| **Dates** | `trade_date`, `settlement_date` | date | |
| **Security** | `security_name`, `isin` | str | ISIN is check-digit validated |
| | `issuer`, `credit_rating` | str | Corporate / CP / CD only |
| | `coupon_rate`, `coupon_frequency` | dec, int | |
| | `maturity_date`, `last_coupon_date` | date | |
| | `day_count` | enum | `30/360`, `ACT/ACT`, `ACT/365`, `ACT/364` |
| | `tenor_days` | int | T-Bill / CP / CD |
| **Economics** | `face_value`, `face_value_per_unit`, `quantity` | dec, dec, int | |
| | `price`, `yield` | dec | Clean price per 100 |
| | `principal_amount`, `accrued_days`, `accrued_interest` | dec, int, dec | |
| | `discount_amount` | dec | Discount instruments |
| | `consideration` | dec | Net settlement amount |
| | `currency` | str | `INR` |
| **Parties / settlement** | `counterparty`, `broker` | str | |
| | `settlement_mode` | str | `DVP-I`, `DVP-III`, … |
| | `portfolio`, `dealer` | str | |
| **Auction** (when present) | `bid_type`, `bid_amount` | enum, dec | |
| **Repo** (nested `repo` object) | `repo_rate`, `repo_days`, `day_count`, `haircut` | | |
| | `leg1_*` / `leg2_*`: `date`, `price`, `accrued_days`, `accrued_interest`, `amount` | | |
| | `repo_interest` | dec | |
| **Parser metadata** | `_source` | obj | file name, detected template, page count |
| | `_validation` | list | failed checks (see section 5) |
| | `_unmapped` | obj | labels found on the slip but not mapped, kept for review |

---

## 4. Label synonym map (starter set)

Different slips use different labels for the same field. The parser normalises a label
(lower-case, strip punctuation, `(INR)`, `(%)`) and looks it up here. This grows with every
real slip we onboard.

| Canonical field | Labels seen |
|---|---|
| `deal_id` | Deal Reference No., Deal ID, Ticket No, Ref, Deal No., Trade ID, Order No., Confirmation No. |
| `trade_date` | Trade Date, Deal Date, Deal Date / Time, Auction Date, Transaction Date, Date of Deal |
| `settlement_date` | Settlement Date, Value Date, Date of Issue / Settlement, Pay-in Date, First Leg Date |
| `security_name` | Security Description, Security Name, Security, Collateral Security, Scrip Name, Instrument |
| `isin` | ISIN, ISIN Code, Security ISIN |
| `coupon_rate` | Coupon Rate, Coupon, Interest Rate, Rate of Interest |
| `maturity_date` | Maturity Date, Date of Maturity, Redemption Date |
| `last_coupon_date` | Last Coupon Date, Last Interest Paid On, Previous Coupon Date |
| `face_value` | Face Value, Total Face Value, Amount Allotted (FV), FV Amount, Nominal, Face Value of Collateral |
| `quantity` | No. of Bonds, Quantity, Units, No. of Securities |
| `price` | Clean Price, Price, Deal Price, Cut-off Price, Rate (per 100) |
| `yield` | YTM, Yield to Maturity, Yield, Cut-off Yield, YTM % |
| `principal_amount` | Principal Amount, Clean Consideration, Deal Amount |
| `accrued_days` | Accrued Interest Days, Interest Days, Accr Days, Broken Period Days |
| `accrued_interest` | Accrued Interest, Accrued Int., Broken Period Interest, Interest Amount |
| `consideration` | Total Consideration, Net Consideration, Amount Payable, Settlement Amount, Net Amount |
| `counterparty` | Counterparty, Counter Party, Party Name, Client, Buyer / Seller |
| `settlement_mode` | Settlement Mode, Settlement, Clearing |
| `portfolio` | Portfolio, Book, Portfolio / Book, Category, Investment Category |
| `dealer` | Dealer, Trader, Dealt By |

Values are normalised too:

- **Amounts**: Indian grouping `5,00,00,000.00`, western grouping `50,000,000.00`, `5.00 Cr`, `50 Lakh` → `"50000000"`
- **Dates**: `24-Sep-2026`, `24/09/2026`, `24.09.2026`, `2026-09-24`, `24 September 2026` → `2026-09-24`. Day-first by default, because this is an Indian market.
- **Direction**: PURCHASE / BUY / BOUGHT → `BUY`; SALE / SELL / SOLD → `SELL`
- **Day count**: Actual/Actual → `ACT/ACT`, Actual/365 → `ACT/365`, 30/360 stays `30/360`

---

## 5. Validation rules (run on every parsed slip)

| Check | Rule |
|---|---|
| ISIN | 12 characters, ISO 6166 check digit valid |
| Dates | settlement ≥ trade; maturity > settlement; no weekend settlement |
| Principal | `face_value × price / 100` ≈ `principal_amount` (±0.01) |
| Consideration | `principal + accrued` ≈ `consideration` (outright) |
| Discount instrument | `face_value − consideration` ≈ `discount_amount` |
| Quantity | `quantity × face_value_per_unit` = `face_value` |
| Repo | `leg1_amount + repo_interest` = `leg2_amount`; repo_days = leg2 − leg1 dates |
| Required fields | deal_id, deal_type, trade_date, settlement_date, isin / security, face_value, consideration |

A failed check doesn't stop the parse. It is recorded in `_validation`, so the output can be
reviewed rather than silently trusted.
