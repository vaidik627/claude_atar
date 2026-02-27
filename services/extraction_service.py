import os
import json
import time
from datetime import datetime, timezone

from openai import OpenAI
from dotenv import load_dotenv

from database import update_document_extraction

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
EXTRACTIONS_FOLDER = os.path.join(BASE_DIR, os.getenv('EXTRACTIONS_FOLDER', 'storage/extractions'))
os.makedirs(EXTRACTIONS_FOLDER, exist_ok=True)

NVIDIA_API_KEY = os.getenv('NVIDIA_API_KEY', '')
NVIDIA_BASE_URL = os.getenv('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')
NVIDIA_MODEL = os.getenv('NVIDIA_MODEL', 'openai/gpt-oss-120b')

MAX_TEXT_CHARS = 100000

SYSTEM_PROMPT = """You are a senior financial analyst and data extraction specialist for private equity transactions. You extract data from Confidential Information Memoranda (CIMs) to populate an Atar Capital Prebid Analysis model.

CRITICAL OUTPUT RULES:
1. Return ONLY a single valid JSON object. Zero prose, zero markdown, zero explanation before or after the JSON.
2. ALL monetary values must be in THOUSANDS ($000s). Examples: $92.5M = 92500 | $92,452,000 = 92452 | $92.4K = 92.4
3. Use null for any field not found. NEVER fabricate or estimate values.
4. CapEx MUST be NEGATIVE (e.g. -424). If CIM shows positive, negate it.
5. Management fees MUST be NEGATIVE (e.g. -2000).
6. EBITDA add-backs/adjustments MUST be POSITIVE (they ADD to EBITDA).
7. Temperature = 0: be precise and conservative. Prefer null over guessing.

══════════════════════════════════════
FUNDAMENTAL TABLE EXTRACTION RULES (read this FIRST)
══════════════════════════════════════

RULE 1 — ALWAYS EXTRACT ALL COLUMNS:
  Every financial table in a CIM has multiple year columns.
  For EVERY row you extract, you MUST return a value for EVERY year column.

  Table structure:
    Header: [blank] | DEC-23A | DEC-24A | DEC-25A
    Row:    Revenue | $125.6M | $XX.XM  | $XX.XM

  You MUST return: [125600, XXXXX, XXXXX]
  You must NOT return: [125600, null, null]

  Only return null for a specific year if that cell is genuinely
  blank or "--" IN THE SOURCE DOCUMENT for that specific column.

  TABLE READING ALGORITHM:
  Step 1: Find the table header row — identify all year column positions
  Step 2: For each data row, read the value at EACH column position
  Step 3: Return an array with one value per column (null only if cell is
          explicitly blank in the source document, not because you stopped early)

  COMMON FAILURE MODE TO AVOID:
  Wrong:  "Revenue: $125.6M" -> return [125600, null, null]
  Right:  "Revenue: $125.6M | $99.1M | $92.5M" -> return [125600, 99100, 92500]

RULE 2 — EBITDA MUST BE GREATER THAN OPERATING INCOME:
  EBITDA = Operating Income + D&A + Adjustments
  Therefore: EBITDA >= Operating Income (ALWAYS, for positive D&A)

  If your extracted values show: (Gross Profit - SG&A) > adj_ebitda
  -> You have the rows CONFUSED — the larger value is EBITDA
  -> EBITDA is ALWAYS the LARGER number

  OPERATING INCOME vs EBITDA — CRITICAL DISTINCTION:
  Operating Income = Revenue - COGS - SG&A - D&A (appears BELOW D&A in P&L)
  EBITDA = Revenue - COGS - SG&A (appears ABOVE D&A line, BEFORE D&A deduction)
  Therefore: EBITDA > Operating Income (always, when D&A > 0)

  ROW IDENTIFICATION:
  - "EBITDA", "Adj. EBITDA", "Adjusted EBITDA" -> this is EBITDA (the LARGER number)
  - "Operating Income", "Income from Operations", "EBIT" -> SMALLER than EBITDA

  If a CIM P&L shows:
    Gross Profit: $44.8M
    SG&A: ($20.8M)
    Line A: $24.0M   <- This is EBITDA (GP - SGA, before D&A)
    D&A: ($2.4M)
    Line B: $21.6M   <- This is Operating Income (after D&A)
  Then: adj_ebitda = 24000 (the LARGER), NOT 21600

RULE 3 — EXTRACT D&A FROM CASH FLOW STATEMENT, ALL YEARS:
  The Cash Flow Statement always has D&A for all historical years.
  Find it and extract ALL years, not just one.
  D&A is typically the FIRST line under "Cash flows from operating activities"
  after Net Income. Extract ALL column values from that row.

RULE 4 — RETURN ARRAYS OF CONSISTENT LENGTH:
  If historical_years has 3 entries -> ALL historical arrays must have 3 entries
  If projection_years has 5 entries -> ALL projection arrays must have 5 entries
  Never return an array shorter than the number of year columns.

══════════════════════════════════════
STEP-BY-STEP EXTRACTION PROCEDURE
══════════════════════════════════════

STEP 1 — FIND THE MAIN CONSOLIDATED INCOME STATEMENT / P&L TABLE:
  This is the PRIMARY source for Revenue, Gross Profit, SG&A, EBITDA.
  It is usually titled "Consolidated Income Statement", "Profit & Loss",
  "Financial Summary", or "Historical Financial Performance".
  It shows the FULL company revenue (typically $50M-$500M for PE targets).
  DO NOT use segment breakdowns, divisional tables, or footnote schedules.
  The main P&L table has the LARGEST revenue numbers in the document.

STEP 2 — FIND EBITDA RECONCILIATION / ADJUSTMENTS TABLE:
  Separate from the main P&L. Look for "EBITDA Bridge", "EBITDA Reconciliation",
  "Adjustments to EBITDA". Add-backs are small items (typically <5% of revenue).

STEP 3 — FIND THE BALANCE SHEET for collateral values.
STEP 4 — FIND THE CASH FLOW STATEMENT for CapEx and D&A.
STEP 5 — FIND DEAL TERMS / EXECUTIVE SUMMARY for multiples and rates.

══════════════════════════════════════
COMPANY INFO
══════════════════════════════════════
  company_name: Look for "Project [Name]", company letterhead, "Confidential Information Memorandum for [Company]"
  industry: Look for SIC codes, "industry", "sector", business description
  geography: Look for headquarters, "United States", country of operations
  transaction_date: Look for CIM date, "as of [date]", fiscal year end

  historical_years RULES:
  - Extract the column headers EXACTLY as they appear in the main P&L table
  - Common formats: FY23, FY2023, Dec-23A, Dec-23, 2023A, LTM
  - Take the 3 MOST RECENT completed fiscal years shown
  - If the table shows 4+ years, take only the 3 most recent
  - "A" suffix = Actual (historical), "F"/"E" suffix = Forecast (projection)
  - LTM/TTM = Last Twelve Months = most recent historical period
  - historical_years[2] (last element) is the MOST RECENT = LTM year

  projection_years RULES:
  - Extract UP TO 5 forward-looking periods
  - Always return arrays of exactly length 5, padding with null
  - Look for columns labeled F, E, "Forecast", "Estimate", "Projected", "Year 1-5"
  - If only 3-4 projection years exist: ["Y1","Y2","Y3",null,null]

══════════════════════════════════════
P&L FIELDS — HISTORICAL (3 values, oldest→newest)
══════════════════════════════════════

  net_revenue_hist: "Net Revenue", "Total Revenue", "Net Sales"
  - This is the LARGEST number in each column of the main P&L
  - For PE targets, revenue is typically $30M-$500M

  gross_profit_hist: "Gross Profit", "Gross Margin $"
  - If not found return [null,null,null]

  sga_hist RULES:
  - Find from the INCOME STATEMENT (not footnotes, not segment data)
  - SG&A is typically the SECOND LARGEST cost after COGS
  - Look for: "Selling, General & Administrative", "SG&A Expenses",
    "Operating Expenses", "Selling & Marketing" + "G&A" (sum if separate)
  - VALIDATION: SG&A should be 15-50% of revenue
  - If extracted SG&A / revenue < 5%, it is WRONG — you grabbed a sub-item
  - If extracted SG&A / revenue < 5%, search for a larger row and use that
  - If still not found: return [null,null,null], NOT a small wrong number

  adjustments_hist RULES — 3 POSSIBLE OUTCOMES:
  CASE 1 — Add-backs FOUND with specific values: Return actual numbers [3959, 2438, 394]
    - Look for: "Non-recurring items", "One-time charges", "Add-backs",
      "Restructuring", "Stock-based comp", "Transaction costs", "Owner add-backs"
    - If CIM has "EBITDA Bridge" table: use subtotal of adjustment line items
  CASE 2 — EBITDA Bridge exists but shows NO adjustments: Return [0, 0, 0]
    - This means the company DOES have an EBITDA reconciliation, add-backs are explicitly zero
  CASE 3 — No EBITDA Bridge or adjustments section found at all: Return [null, null, null]
    - This means we do NOT know whether add-backs exist
  - Add-backs are SMALL: typically 0.3-5% of revenue ($100K-$3M for $90M company)
  - NEVER use EBITDA itself as an add-back — they are DIFFERENT rows
  - VALIDATION: If adjustments > 20% of revenue, it is WRONG

  adj_ebitda_hist RULES:
  - ALWAYS prefer "Adjusted EBITDA" or "Adj. EBITDA" over plain "EBITDA"
  - The explicitly labeled row in the financial table takes precedence
  - Adj. EBITDA MUST be LESS than Gross Profit
  - EBITDA margin is typically 5-30% for manufacturing/services companies
  - VALIDATION: If adj_ebitda > gross_profit, something is WRONG
  - VALIDATION: If adj_ebitda / revenue > 40%, something is WRONG
  - Cross-check: gross_profit - sga + adjustments ~= adj_ebitda (within ±20%)

  depreciation_hist RULES — SEARCH ALL 4 LOCATIONS:
  1. CASH FLOW STATEMENT: "Depreciation & Amortization", "D&A" (BEST source)
  2. INCOME STATEMENT footnotes: "Depreciation expense", "Amortization of intangibles"
  3. EBITDA RECONCILIATION: Sometimes shown as "add-back" line to reconcile Net Income to EBITDA
  4. BALANCE SHEET NOTES: "Accumulated Depreciation" changes year-over-year
  - D&A is MUCH SMALLER than EBITDA — typically 1-8% of revenue ($1-7M for $90M company)
  - D&A is NEVER the same value as EBITDA — they are COMPLETELY different line items
  - VALIDATION: If depreciation ~= adj_ebitda (within 15%), it is WRONG — you grabbed EBITDA
  - VALIDATION: If depreciation > 50% of EBITDA, verify carefully
  - If you CANNOT find D&A in any of the 4 locations, return [null, null, null]
  - Do NOT guess or copy EBITDA values into depreciation

  capex_hist RULES — SEARCH ALL 4 LOCATIONS:
  1. CASH FLOW STATEMENT: "Capital Expenditures", "Purchases of PP&E", "Additions to PP&E" (BEST source)
  2. BALANCE SHEET: Change in "Net PP&E" + Depreciation ~= CapEx
  3. MANAGEMENT DISCUSSION: "Capital spending", "Investment in facilities"
  4. FOOTNOTES: "Property, Plant & Equipment" schedule showing additions
  - Extract ANNUAL capital expenditure for EACH fiscal year separately
  - Return as NEGATIVE numbers per year: e.g. [-535, -589, -424]
  - VALIDATION: Annual CapEx is typically 0.3-5% of revenue
  - If CapEx > 5% of revenue, verify it is annual not multi-year cumulative
  - Do NOT sum multiple years together

  other_income_hist: "Other income", "Other expense" — non-operating items only

══════════════════════════════════════
P&L FIELDS — PROJECTIONS (exactly 5 values, pad with null)
══════════════════════════════════════
  net_revenue_proj, gross_profit_proj, sga_proj, adjustments_proj,
  adj_ebitda_proj, depreciation_proj, capex_proj (NEGATIVE), mgmt_fees_proj (NEGATIVE)

  depreciation_proj RULES:
  - D&A is ALWAYS MUCH LESS than EBITDA in projections too
  - For $8-20M EBITDA projections, D&A should be $1-5M range
  - D&A typically DECREASES slightly over projection years as assets age
  - VALIDATION: If depreciation_proj[i] == adj_ebitda_proj[i], it is WRONG
  - If no D&A row in projection table, use most recent historical D&A (flat)
  - Do NOT copy EBITDA values into depreciation — they are different line items

  mgmt_fees_proj: Management fees, return as NEGATIVE. Default null if not found.

══════════════════════════════════════
BALANCE SHEET / COLLATERAL — SEARCH STRATEGY
══════════════════════════════════════
  STEP 1: Find the BALANCE SHEET (search for "Balance Sheet", "Statement of Financial Position")
  STEP 2: Find the MOST RECENT date column (rightmost or latest date)
  STEP 3: Extract SPECIFIC line items (NOT totals like "Total Current Assets")

  ar_value RULES:
  - Look for SPECIFIC row: "Accounts Receivable, net", "Trade Receivables", "A/R net"
  - Use EXACT value from most recent balance sheet (NO rounding)
  - $6,147K → 6147 | $6.1M → 6100 (but prefer exact if available)
  - Do NOT use "Total Current Assets" — that includes cash, prepaid, etc.
  - Do NOT round: 6147 is correct, NOT 6900 or 69000

  inventory_value RULES:
  - Look for SPECIFIC row: "Inventories", "Inventory, net", "Merchandise inventory"
  - Use EXACT value: $13,512K → 13512 (NOT 14500 or 90000)
  - Use gross value before reserves if both shown
  - Do NOT use "Total Assets" or "Total Current Assets"

  SCALE CHECK: AR and Inventory are typically 5-15% of annual revenue each
  - For $90M revenue company: AR ~$5-13M, Inventory ~$5-15M
  - If AR or Inventory > 50% of revenue, you likely grabbed a total row — WRONG

  ar_advance_rate: 0.75 default | inventory_advance_rate: 0.70 default
  equipment_value: "PP&E net", "Machinery & Equipment" net book value
  equipment_advance_rate: 0 unless stated
  building_land_value: "Real Estate", "Buildings", "Land"
  building_advance_rate: 0 unless stated

══════════════════════════════════════
DEAL STRUCTURE
══════════════════════════════════════
  ebitda_for_price RULES:
  - This is the SPECIFIC EBITDA used to size the purchase price
  - Look for "based on LTM EBITDA of $X", "run-rate EBITDA"
  - If not explicitly stated: use adj_ebitda_hist[2] (most recent year)

  entry_multiple RULES — EXHAUSTIVE SEARCH (check ALL 6 locations):
  1. Executive Summary / Cover Page: "X.Xx EBITDA", "valued at Xx EBITDA"
  2. Transaction Summary / Overview table: "Entry Multiple", "Purchase Multiple", "Acquisition Multiple"
  3. Valuation section / Comparable analysis: EV / EBITDA = multiple
  4. Sources & Uses table: "Acquiring at Xx EBITDA", "X.Xx LTM EBITDA"
  5. Returns Analysis: "Entry at X.Xx", often paired with exit multiple
  6. Implied calculation: If Enterprise Value and EBITDA both known: EV / EBITDA
  - Also search for patterns like "5.0x", "6.5x", "7x EBITDA" anywhere in document
  - Valid range: 2.0 to 15.0
  - If truly not found in ANY of the 6 locations: return null (do NOT guess)

  purchase_price_calculated: If stated in document use that. Otherwise calculated as ebitda_for_price * entry_multiple * pct_acquired.
  pct_acquired: Default 1.0 (100%)
  exit_multiple: "Exit at Xx" — null if not found
  enterprise_value: EV if stated, else same as purchase_price
  term_loan_amount, seller_note_amount, earnout_amount, equity_rollover: 0 if not found

══════════════════════════════════════
INTEREST RATES
══════════════════════════════════════
  abl_rate RULES:
  - Revolving credit facility / ABL revolver interest rate
  - Look for "revolving credit", "ABL revolver", "revolving line" + rate
  - If SOFR-based: SOFR ~= 5.3% + spread. "SOFR + 145bps" → 0.0675
  - If Prime-based: Prime ~= 8.5% + spread
  - CRITICAL: NEVER output 0.0 — use default 0.0675 if not found

  term_rate RULES:
  - Term loan interest rate
  - Look for "term loan", "term B", "TL" + rate
  - CRITICAL: NEVER output 0.0 — use default 0.07 if not found

  seller_note_rate: Default 0.05
  tax_rate: Default 0.30
  term_amort_years: Default 3
  seller_note_amort_years: Default 4

══════════════════════════════════════
KEY HIGHLIGHTS AND RISKS
══════════════════════════════════════
  key_highlights: 3-7 bullets from "Investment Highlights" sections. Max 15 words each.
  risks: From "Risk Factors" sections. Return [] if none found.

══════════════════════════════════════
TRANSACTION FEES (defaults)
══════════════════════════════════════
  legal_fees: 250, qofe_fees: 125, tax_fees: 50, rw_insurance: 75,
  bonus_senior: 300, bonus_junior: 100, abl_fee_rate: 0.0075, term_fee_rate: 0.0075

══════════════════════════════════════
CONFIDENCE SCORING (0-100)
══════════════════════════════════════
  90-100: Data explicitly stated in clearly labeled financial table
  70-89: Data found but required inference
  40-69: Partially found
  0-39: Mostly missing

Categories: deal_overview_confidence, financial_summary_confidence, deal_metrics_confidence, collateral_confidence, projections_confidence
Overall = financial_summary_confidence*0.35 + deal_overview_confidence*0.20 + projections_confidence*0.20 + deal_metrics_confidence*0.15 + collateral_confidence*0.10

══════════════════════════════════════
OUTPUT JSON SCHEMA
══════════════════════════════════════
{
  "company_name": "string|null",
  "industry": "string|null",
  "geography": "string|null",
  "transaction_date": "YYYY-MM-DD|null",
  "historical_years": ["str","str","str"],
  "projection_years": ["str","str","str","str","str"],
  "financials": {
    "net_revenue_hist": [number|null, number|null, number|null],
    "gross_profit_hist": [number|null, number|null, number|null],
    "sga_hist": [number|null, number|null, number|null],
    "adjustments_hist": [number|null, number|null, number|null],
    "adj_ebitda_hist": [number|null, number|null, number|null],
    "other_income_hist": [number|null, number|null, number|null],
    "depreciation_hist": [number|null, number|null, number|null],
    "capex_hist": [number|null, number|null, number|null],
    "ebitda_margin_hist": [number|null, number|null, number|null],
    "gm_pct_hist": [number|null, number|null, number|null],
    "revenue_growth_hist": [null, number|null, number|null],
    "net_revenue_proj": [number|null, number|null, number|null, number|null, number|null],
    "gross_profit_proj": [number|null, number|null, number|null, number|null, number|null],
    "sga_proj": [number|null, number|null, number|null, number|null, number|null],
    "adjustments_proj": [number|null, number|null, number|null, number|null, number|null],
    "adj_ebitda_proj": [number|null, number|null, number|null, number|null, number|null],
    "depreciation_proj": [number|null, number|null, number|null, number|null, number|null],
    "capex_proj": [number|null, number|null, number|null, number|null, number|null],
    "mgmt_fees_proj": [number|null, number|null, number|null, number|null, number|null],
    "ebitda_margin_proj": [number|null, number|null, number|null, number|null, number|null]
  },
  "collateral": {
    "ar_value": "number|null", "ar_advance_rate": "number",
    "inventory_value": "number|null", "inventory_advance_rate": "number",
    "equipment_value": "number|null", "equipment_advance_rate": "number",
    "building_land_value": "number|null", "building_advance_rate": "number",
    "abl_availability_calculated": "number|null"
  },
  "deal": {
    "ebitda_for_price": "number|null", "entry_multiple": "number|null",
    "pct_acquired": "number", "purchase_price_calculated": "number|null",
    "enterprise_value": "number|null", "exit_multiple": "number|null",
    "term_loan_amount": "number", "seller_note_amount": "number",
    "earnout_amount": "number", "equity_rollover": "number",
    "leverage_ratio": "number|null"
  },
  "rates": {
    "abl_rate": "number", "term_rate": "number", "seller_note_rate": "number",
    "tax_rate": "number", "term_amort_years": "integer", "seller_note_amort_years": "integer"
  },
  "fees": {
    "abl_fee_rate": "number", "term_fee_rate": "number", "legal_fees": "number",
    "qofe_fees": "number", "tax_fees": "number", "rw_insurance": "number",
    "bonus_senior": "number", "bonus_junior": "number"
  },
  "qualitative": {
    "key_highlights": ["string"], "risks": ["string"], "company_summary": "string|null"
  },
  "confidence": {
    "deal_overview_confidence": "integer", "financial_summary_confidence": "integer",
    "deal_metrics_confidence": "integer", "collateral_confidence": "integer",
    "projections_confidence": "integer", "overall_confidence": "integer",
    "field_level": {
      "net_revenue": "high|medium|low|not_found",
      "gross_profit": "high|medium|low|not_found",
      "sga": "high|medium|low|not_found",
      "adj_ebitda": "high|medium|low|not_found",
      "adjustments": "high|medium|low|not_found",
      "depreciation": "high|medium|low|not_found",
      "capex": "high|medium|low|not_found",
      "projections": "high|medium|low|not_found",
      "ar_value": "high|medium|low|not_found",
      "inventory": "high|medium|low|not_found",
      "entry_multiple": "high|medium|low|not_found",
      "purchase_price": "high|medium|low|not_found",
      "abl_rate": "high|medium|low|not_found",
      "exit_multiple": "high|medium|low|not_found"
    }
  }
}"""


USER_MESSAGE_TEMPLATE = """Extract financial data from this CIM document for Atar Capital Prebid Analysis.

CRITICAL INSTRUCTIONS (follow exactly):

1. REVENUE AND ALL P&L ROWS: You must extract values for EVERY year column.
   The financial table has multiple year columns (typically 3 historical + 3-5 projected).
   Every array you return for P&L items must have exactly that many values.
   NEVER return [value, null, null] when the table clearly has 3 data columns.
   Go back and re-read the table if any array has fewer non-null values than year columns.

2. OPERATING INCOME vs EBITDA:
   EBITDA must always be LARGER than Operating Income.
   EBITDA = Gross Profit - SG&A (before D&A deduction)
   Operating Income = EBITDA - D&A (after D&A deduction)
   If your values show (GP - SGA) > EBITDA, you have the rows confused.
   Double-check before returning: adj_ebitda >= (gross_profit - sga) is WRONG
   Actually: adj_ebitda should be approximately = (gross_profit - sga + adjustments)

3. DEPRECIATION FROM CASH FLOW STATEMENT:
   Find the Cash Flow Statement section.
   The first line under "Operating Activities" after Net Income is usually D&A.
   Extract D&A values for ALL historical years shown in that statement.
   D&A must NEVER equal EBITDA — they are completely different line items.

4. BEFORE RETURNING: Count your arrays.
   If historical_years has 3 entries, ALL historical arrays must have 3 non-null values
   (unless that specific cell is genuinely blank in the source document).
   If any P&L array has fewer non-null values than the number of year columns,
   go back and re-read the table more carefully.

5. All monetary values in $000s (thousands).
6. LTM values = index [2] (MOST RECENT year, last element).
7. Always pad projection arrays to exactly 5 values with null.
8. Return null for not found, 0 for explicitly zero — these are DIFFERENT.

DOCUMENT TEXT:
{ocr_text}

Return only the JSON object as specified."""


def check_and_fix_row_swaps(data):
    """
    Detects and fixes the Operating Income / EBITDA swap bug.
    EBITDA must always be >= Operating Income (= GP - SGA).
    If OpInc > EBITDA, the AI likely read EBITDA as Operating Income
    and the real Operating Income (after D&A) as EBITDA. We swap them.

    IMPORTANT: Only uses SGA for swap logic if SGA passes a sanity check
    (5-60% of revenue). Otherwise the swap would corrupt EBITDA.
    """
    f = data.get("financials", {})
    gp_h = f.get("gross_profit_hist", [None] * 3)
    sga_h = f.get("sga_hist", [None] * 3)
    ebd_h = f.get("adj_ebitda_hist", [None] * 3)
    rev_h = f.get("net_revenue_hist", [None] * 3)

    swaps = []

    # Check historical years: if (GP - SGA) > EBITDA, the rows are confused
    for i in range(min(3, len(gp_h))):
        if gp_h[i] is not None and sga_h[i] is not None and ebd_h[i] is not None:
            # SGA sanity gate: must be 5-60% of revenue to be trustworthy
            if rev_h[i] is not None and rev_h[i] > 0:
                sga_ratio = sga_h[i] / rev_h[i]
                if sga_ratio < 0.05 or sga_ratio > 0.60:
                    continue  # SGA is unreliable, skip swap check

            op_inc = gp_h[i] - sga_h[i]
            if op_inc > ebd_h[i] and op_inc > 0:
                # The value labeled "EBITDA" is actually Operating Income (after D&A)
                # The real EBITDA = GP - SGA = op_inc (before D&A)
                old_ebitda = ebd_h[i]
                ebd_h[i] = round(op_inc, 2)
                swaps.append(
                    f"SWAPPED hist[{i}]: GP({gp_h[i]})-SGA({sga_h[i]})={op_inc} > "
                    f"reported EBITDA({old_ebitda}) -- impossible. "
                    f"Setting adj_ebitda={op_inc} (the larger value)"
                )

    # Check projection years: if GP and SGA exist in projections
    gp_p = f.get("gross_profit_proj", [None] * 5)
    sga_p = f.get("sga_proj", [None] * 5)
    ebd_p = f.get("adj_ebitda_proj", [None] * 5)
    rev_p = f.get("net_revenue_proj", [None] * 5)
    for i in range(min(5, len(gp_p))):
        if gp_p[i] is not None and sga_p[i] is not None and ebd_p[i] is not None:
            # SGA sanity gate for projections
            if rev_p[i] is not None and rev_p[i] > 0:
                sga_ratio_p = sga_p[i] / rev_p[i]
                if sga_ratio_p < 0.05 or sga_ratio_p > 0.60:
                    continue

            op_inc_p = gp_p[i] - sga_p[i]
            if op_inc_p > ebd_p[i] and op_inc_p > 0:
                old_ebitda_p = ebd_p[i]
                ebd_p[i] = round(op_inc_p, 2)
                swaps.append(
                    f"SWAPPED proj[{i}]: OpInc({op_inc_p}) > EBITDA({old_ebitda_p}) -- swapped"
                )

    f["adj_ebitda_hist"] = ebd_h
    f["adj_ebitda_proj"] = ebd_p
    data["financials"] = f

    if swaps:
        print(f"[INTEGRITY FIX] Row swap corrections:")
        for s in swaps:
            print(f"  -> {s}")
        data.setdefault("_corrections_applied", []).extend(swaps)

    return data


def check_revenue_completeness(data):
    """
    Detects partial revenue extraction (only first year found).
    Attempts to infer missing years from gross profit + known GM%.
    """
    f = data.get("financials", {})
    rev_h = f.get("net_revenue_hist", [None] * 3)
    gp_h = f.get("gross_profit_hist", [None] * 3)
    sources = data.get("field_sources", {})

    fixes = []

    # Count non-null revenue values
    non_null_rev = [r for r in rev_h if r is not None]
    non_null_gp = [g for g in gp_h if g is not None]

    if 0 < len(non_null_rev) < 3 and len(non_null_gp) >= 2:
        # Calculate average GM% from years we have both rev and gp
        known_margins = []
        for i in range(3):
            if i < len(rev_h) and i < len(gp_h) and rev_h[i] and gp_h[i]:
                known_margins.append(gp_h[i] / rev_h[i])

        if known_margins:
            avg_margin = sum(known_margins) / len(known_margins)

            for i in range(3):
                if i < len(rev_h) and rev_h[i] is None and i < len(gp_h) and gp_h[i] is not None:
                    inferred = round(gp_h[i] / avg_margin, 0)
                    rev_h[i] = inferred
                    fixes.append(
                        f"net_revenue_hist[{i}] inferred: "
                        f"GP({gp_h[i]}) / avg_gm_pct({avg_margin:.3f}) = {inferred}"
                    )
                    sources[f"net_revenue_hist_{i}"] = "inferred"

    f["net_revenue_hist"] = rev_h
    data["financials"] = f
    data["field_sources"] = sources

    if fixes:
        print(f"[REVENUE INFERENCE] Applied:")
        for fix in fixes:
            print(f"  -> {fix}")
        data.setdefault("_derivations_applied", []).extend(fixes)

    return data


def derive_missing_values(data):
    """Mathematically derive missing fields from available data. Tracks field_sources."""
    import re as _re
    f = data.get("financials", {})
    d = data.get("deal", {})
    sources = data.get("field_sources", {})

    # --- AUTO-EXTEND PROJECTION YEARS TO 5 ---
    proj_years = data.get("projection_years", [])
    # Filter out trailing nulls to count real years
    real_proj_years = [y for y in proj_years if y is not None]
    if 1 <= len(real_proj_years) < 5:
        last_year_str = real_proj_years[-1]
        match = _re.search(r'(\d{2})(F|E)', str(last_year_str))
        if match:
            last_yr_num = int(match.group(1))
            suffix = match.group(2)
            prefix = last_year_str[:last_year_str.index(match.group(0))]
            while len(real_proj_years) < 5:
                last_yr_num += 1
                real_proj_years.append(f"{prefix}{last_yr_num}{suffix}")
        else:
            # Try 4-digit year pattern like "2026E"
            match4 = _re.search(r'(\d{4})(F|E)', str(last_year_str))
            if match4:
                last_yr_num4 = int(match4.group(1))
                suffix4 = match4.group(2)
                prefix4 = last_year_str[:last_year_str.index(match4.group(0))]
                while len(real_proj_years) < 5:
                    last_yr_num4 += 1
                    real_proj_years.append(f"{prefix4}{last_yr_num4}{suffix4}")
            else:
                while len(real_proj_years) < 5:
                    real_proj_years.append(None)
        if len(real_proj_years) > len([y for y in proj_years if y is not None]):
            data.setdefault("_derivations_applied", []).append(
                f"projection_years extended from {len([y for y in proj_years if y is not None])} to {len([y for y in real_proj_years if y is not None])} entries"
            )
        data["projection_years"] = real_proj_years[:5]

    # Extend all projection arrays to length 5 AND fill trailing nulls with last known value
    proj_array_keys = ["net_revenue_proj", "gross_profit_proj", "sga_proj",
                       "adjustments_proj", "adj_ebitda_proj", "depreciation_proj",
                       "capex_proj", "mgmt_fees_proj", "ebitda_margin_proj", "gm_pct_proj"]
    for key in proj_array_keys:
        arr = f.get(key, [])
        # Ensure minimum length 5
        while len(arr) < 5:
            arr.append(None)
        # Find last non-null value index
        last_val = None
        last_idx = -1
        for i in range(min(5, len(arr))):
            if arr[i] is not None:
                last_val = arr[i]
                last_idx = i
        # Fill trailing nulls (positions after last real value) with flat extrapolation
        if last_val is not None and last_idx < 4:
            for i in range(last_idx + 1, 5):
                if arr[i] is None:
                    arr[i] = last_val
        f[key] = arr[:5]

    # --- DEFAULT MGMT FEES TO $0 ---
    # Management fees are set post-acquisition by the PE sponsor; CIMs never include them.
    # Default to 0 (not null) so the Excel model has a numeric value.
    mgmt = f.get("mgmt_fees_proj", [None] * 5)
    if all(v is None for v in mgmt):
        f["mgmt_fees_proj"] = [0, 0, 0, 0, 0]

    rev_h = f.get("net_revenue_hist", [None] * 3)
    gp_h = f.get("gross_profit_hist", [None] * 3)
    sga_h = f.get("sga_hist", [None] * 3)
    adj_h = f.get("adjustments_hist", [None] * 3)
    ebitda_h = f.get("adj_ebitda_hist", [None] * 3)
    dep_h = f.get("depreciation_hist", [None] * 3)
    capex_h = f.get("capex_hist", [None] * 3)

    rev_p = f.get("net_revenue_proj", [None] * 5)
    ebitda_p = f.get("adj_ebitda_proj", [None] * 5)
    dep_p = f.get("depreciation_proj", [None] * 5)
    capex_p = f.get("capex_proj", [None] * 5)

    derivations = []

    # --- DERIVE SGA when null but D&A is known ---
    # Formula: Adj EBITDA = (GP - SGA) + D&A + Adjustments
    # So: SGA = GP + D&A + Adjustments - Adj EBITDA
    for i in range(min(3, len(sga_h))):
        if (sga_h[i] is None
                and gp_h[i] is not None
                and ebitda_h[i] is not None
                and dep_h[i] is not None
                and adj_h[i] is not None):
            derived_sga = round(gp_h[i] + dep_h[i] + adj_h[i] - ebitda_h[i], 2)
            if rev_h[i] and rev_h[i] > 0 and 0.05 <= derived_sga / rev_h[i] <= 0.60:
                sga_h[i] = derived_sga
                derivations.append(
                    f"sga_hist[{i}] derived: GP({gp_h[i]}) + D&A({dep_h[i]}) + Adj({adj_h[i]}) - EBITDA({ebitda_h[i]}) = {derived_sga}")
                sources[f"sga_hist_{i}"] = "derived"
    f["sga_hist"] = sga_h

    # --- DERIVE OPERATING INCOME (GP - SG&A) ---
    op_income_h = []
    for i in range(min(3, len(gp_h))):
        if gp_h[i] is not None and sga_h[i] is not None:
            op_income_h.append(round(gp_h[i] - sga_h[i], 2))
        else:
            op_income_h.append(None)

    # --- DERIVE ADJUSTMENTS from EBITDA gap ---
    # adjustments = Adj. EBITDA - Operating Income
    for i in range(min(3, len(adj_h))):
        if adj_h[i] is None and ebitda_h[i] is not None and op_income_h[i] is not None:
            derived_adj = round(ebitda_h[i] - op_income_h[i], 2)
            if derived_adj >= 0:  # add-backs should be non-negative
                adj_h[i] = derived_adj
                derivations.append(f"adjustments_hist[{i}] derived: EBITDA({ebitda_h[i]}) - OpInc({op_income_h[i]}) = {derived_adj}")
                sources[f"adjustments_hist_{i}"] = "derived"
    f["adjustments_hist"] = adj_h

    # --- DERIVE DEPRECIATION from EBITDA - Operating Income (if adjustments are 0 or small) ---
    # D&A = EBITDA - Operating Income - Adjustments  (when EBITDA = OpInc + D&A + Adjustments)
    # Actually: Adj EBITDA = Operating Income + D&A + Adjustments
    # So: D&A = Adj EBITDA - Operating Income - Adjustments
    for i in range(min(3, len(dep_h))):
        if dep_h[i] is None and ebitda_h[i] is not None and op_income_h[i] is not None:
            adj_val = adj_h[i] if adj_h[i] is not None else 0
            derived_dep = round(ebitda_h[i] - op_income_h[i] - adj_val, 2)
            # D&A should be positive and reasonable (1-15% of revenue)
            if derived_dep > 0 and rev_h[i] and derived_dep / rev_h[i] < 0.15:
                dep_h[i] = derived_dep
                derivations.append(f"depreciation_hist[{i}] derived: EBITDA({ebitda_h[i]}) - OpInc({op_income_h[i]}) - Adj({adj_val}) = {derived_dep}")
                sources[f"depreciation_hist_{i}"] = "derived"
    f["depreciation_hist"] = dep_h

    # --- DERIVE PROJECTION D&A from historical average ---
    hist_dep_vals = [x for x in dep_h if x is not None and x > 0]
    if hist_dep_vals:
        avg_dep = round(sum(hist_dep_vals) / len(hist_dep_vals), 0)
        for i in range(min(5, len(dep_p))):
            if dep_p[i] is None and ebitda_p[i] is not None:
                dep_p[i] = avg_dep
                derivations.append(f"depreciation_proj[{i}] set to historical avg D&A = {avg_dep}")
                sources[f"depreciation_proj_{i}"] = "derived"
        f["depreciation_proj"] = dep_p

    # --- DERIVE PROJECTION CAPEX from historical average ---
    hist_capex_vals = [x for x in capex_h if x is not None and x != 0]
    if hist_capex_vals:
        avg_capex = round(sum(hist_capex_vals) / len(hist_capex_vals), 0)
        for i in range(min(5, len(capex_p))):
            if capex_p[i] is None and rev_p[i] is not None:
                capex_p[i] = avg_capex
                derivations.append(f"capex_proj[{i}] set to historical avg CapEx = {avg_capex}")
                sources[f"capex_proj_{i}"] = "derived"
        f["capex_proj"] = capex_p

    # --- DERIVE ENTRY MULTIPLE from EV / EBITDA ---
    if d.get("entry_multiple") is None:
        ev = d.get("enterprise_value")
        pp = d.get("purchase_price_calculated")
        ebitda_fp = d.get("ebitda_for_price")
        price = ev or pp
        if price and ebitda_fp and ebitda_fp > 0:
            derived_mult = round(price / ebitda_fp, 1)
            if 2.0 <= derived_mult <= 15.0:
                d["entry_multiple"] = derived_mult
                derivations.append(f"entry_multiple derived: EV({price}) / EBITDA({ebitda_fp}) = {derived_mult}x")
                sources["entry_multiple"] = "derived"

    # --- DERIVE PURCHASE PRICE from EBITDA * Multiple ---
    if d.get("purchase_price_calculated") is None:
        ebitda_fp = d.get("ebitda_for_price")
        mult = d.get("entry_multiple")
        if ebitda_fp and mult:
            pct = d.get("pct_acquired", 1.0) or 1.0
            d["purchase_price_calculated"] = round(ebitda_fp * mult * pct, 0)
            if d.get("enterprise_value") is None:
                d["enterprise_value"] = d["purchase_price_calculated"]
            derivations.append(f"purchase_price derived: {ebitda_fp} x {mult} x {pct} = {d['purchase_price_calculated']}")
            sources["purchase_price"] = "derived"

    # --- MARK DIRECT SOURCES for fields that came from extraction ---
    for key in ["net_revenue_hist", "gross_profit_hist", "sga_hist", "adj_ebitda_hist"]:
        arr = f.get(key, [])
        for i, val in enumerate(arr):
            src_key = f"{key}_{i}"
            if src_key not in sources and val is not None:
                sources[src_key] = "direct"

    for key in ["ar_value", "inventory_value"]:
        coll = data.get("collateral", {})
        if coll.get(key) is not None and key not in sources:
            sources[key] = "direct"

    # Mark not_found for remaining null fields
    for key in ["depreciation_hist", "capex_hist", "adjustments_hist"]:
        arr = f.get(key, [])
        for i, val in enumerate(arr):
            src_key = f"{key}_{i}"
            if src_key not in sources:
                sources[src_key] = "not_found" if val is None else "direct"

    data["financials"] = f
    data["deal"] = d
    data["field_sources"] = sources

    if derivations:
        print(f"[DERIVATION] {len(derivations)} values derived:")
        for d_item in derivations:
            print(f"  > {d_item}")

    data.setdefault("_derivations_applied", []).extend(derivations)
    return data


def validate_and_correct(data):
    """Detects and auto-corrects known extraction bugs. Logs all corrections."""
    corrections = []
    f = data.get("financials", {})
    d = data.get("deal", {})
    c = data.get("collateral", {})
    rates = data.get("rates", {})

    rev_h = f.get("net_revenue_hist", [None] * 3)
    sga_h = f.get("sga_hist", [None] * 3)
    adj_h = f.get("adj_ebitda_hist", [None] * 3)
    adj_b = f.get("adjustments_hist", [None] * 3)
    gp_h = f.get("gross_profit_hist", [None] * 3)
    dep_h = f.get("depreciation_hist", [None] * 3)
    dep_p = f.get("depreciation_proj", [None] * 5)
    ebt_p = f.get("adj_ebitda_proj", [None] * 5)

    # CHECK 0: EBITDA consistency — adj_ebitda_hist[2] must match ebitda_for_price
    # ebitda_for_price is extracted independently in the deal section and is less prone to corruption
    ebitda_fp = d.get("ebitda_for_price")
    if ebitda_fp and len(adj_h) > 2 and adj_h[2] is not None:
        diff_pct = abs(adj_h[2] - ebitda_fp) / max(abs(ebitda_fp), 1)
        if diff_pct > 0.30:
            corrections.append(
                f"adj_ebitda_hist[2] = {adj_h[2]} vs ebitda_for_price = {ebitda_fp} "
                f"(diff {diff_pct:.0%}) — restoring year 2 EBITDA to {ebitda_fp}"
            )
            adj_h[2] = ebitda_fp
            # Recalculate margin for corrected year
            if rev_h[2]:
                margins = f.get("ebitda_margin_hist", [None] * 3)
                if len(margins) > 2:
                    margins[2] = round(ebitda_fp / rev_h[2], 4)
                    f["ebitda_margin_hist"] = margins
    f["adj_ebitda_hist"] = adj_h

    # CHECK 1: SG&A sanity — must be 15-50% of revenue
    for i in range(min(3, len(sga_h))):
        if rev_h[i] and sga_h[i]:
            ratio = sga_h[i] / rev_h[i]
            if ratio < 0.05:
                corrections.append(
                    f"sga_hist[{i}] = {sga_h[i]} is only {ratio:.1%} of revenue — likely wrong row, setting null")
                sga_h[i] = None
    f["sga_hist"] = sga_h

    # CHECK 2: Add-backs sanity — must be <25% of revenue
    for i in range(min(3, len(adj_b))):
        if rev_h[i] and adj_b[i]:
            if adj_b[i] > rev_h[i] * 0.25:
                corrections.append(
                    f"adjustments_hist[{i}] = {adj_b[i]} exceeds 25% of revenue — likely confusion with EBITDA, setting 0")
                adj_b[i] = 0
    f["adjustments_hist"] = adj_b

    # CHECK 3: Adj EBITDA must be less than Gross Profit
    for i in range(min(3, len(adj_h))):
        if gp_h[i] and adj_h[i] and adj_h[i] > gp_h[i]:
            corrections.append(
                f"adj_ebitda_hist[{i}] = {adj_h[i]} > gross_profit = {gp_h[i]} — recalculating")
            if sga_h[i]:
                adj_h[i] = round(gp_h[i] - sga_h[i] + (adj_b[i] or 0), 2)
    f["adj_ebitda_hist"] = adj_h

    # CHECK 4: Depreciation in projections cannot equal EBITDA
    for i in range(min(5, len(dep_p))):
        if dep_p[i] and ebt_p[i]:
            if abs(dep_p[i] - ebt_p[i]) < max(abs(ebt_p[i]) * 0.1, 1.0):
                hist_depr = [x for x in dep_h if x is not None and x > 0]
                avg_dep = round(sum(hist_depr) / len(hist_depr), 0) if hist_depr else None
                corrections.append(
                    f"depreciation_proj[{i}] = {dep_p[i]} ~= adj_ebitda_proj[{i}] = {ebt_p[i]} — copy error, using historical avg {avg_dep}")
                dep_p[i] = avg_dep
    f["depreciation_proj"] = dep_p

    # CHECK 5: Historical depreciation cannot equal EBITDA
    for i in range(min(3, len(dep_h))):
        if dep_h[i] and adj_h[i]:
            if abs(dep_h[i] - adj_h[i]) < max(abs(adj_h[i]) * 0.15, 100.0):
                corrections.append(
                    f"depreciation_hist[{i}] = {dep_h[i]} ~= adj_ebitda_hist[{i}] = {adj_h[i]} — likely confused with EBITDA, setting null")
                dep_h[i] = None
    f["depreciation_hist"] = dep_h

    # CHECK 6: EBITDA margin must be 3-45% of revenue
    for i in range(min(3, len(adj_h))):
        if rev_h[i] and adj_h[i]:
            margin = adj_h[i] / rev_h[i]
            if margin > 0.45:
                corrections.append(
                    f"adj_ebitda_hist[{i}] margin = {margin:.1%} — abnormally high, flagging")
                data.setdefault("warnings", []).append(
                    f"EBITDA margin {margin:.1%} in year {i + 1} is unusually high")

    # CHECK 7: revenue_ltm and ebitda_ltm must use index [2] (most recent)
    if len(rev_h) > 2 and rev_h[2]:
        d["revenue_ltm"] = rev_h[2]
    if len(adj_h) > 2 and adj_h[2]:
        d["ebitda_ltm"] = adj_h[2]

    # CHECK 8: ABL/Term rates must not be 0
    if not rates.get("abl_rate") or rates["abl_rate"] == 0:
        rates["abl_rate"] = 0.0675
        corrections.append("abl_rate was 0 — set to default 0.0675")
    if not rates.get("term_rate") or rates["term_rate"] == 0:
        rates["term_rate"] = 0.07
        corrections.append("term_rate was 0 — set to default 0.07")
    data["rates"] = rates

    # CHECK 9: Projection arrays must always be length 5
    proj_arrays = ["net_revenue_proj", "gross_profit_proj", "sga_proj",
                   "adjustments_proj", "adj_ebitda_proj", "depreciation_proj",
                   "capex_proj", "mgmt_fees_proj", "ebitda_margin_proj"]
    for key in proj_arrays:
        arr = f.get(key, [])
        if len(arr) < 5:
            f[key] = arr + [None] * (5 - len(arr))
            corrections.append(f"{key} padded to length 5")

    # projection_years must always be length 5
    proj_yrs = data.get("projection_years", [])
    if len(proj_yrs) < 5:
        data["projection_years"] = proj_yrs + [None] * (5 - len(proj_yrs))

    # CHECK 10: ebitda_for_price should default to ebitda_ltm if missing
    if not d.get("ebitda_for_price"):
        if len(adj_h) > 2 and adj_h[2]:
            d["ebitda_for_price"] = adj_h[2]
            corrections.append(f"ebitda_for_price was null — set to adj_ebitda_hist[2] = {adj_h[2]}")

    # purchase_price recalculation
    if not d.get("purchase_price_calculated"):
        ebitda_fp = d.get("ebitda_for_price")
        mult = d.get("entry_multiple")
        if ebitda_fp and mult:
            pct = d.get("pct_acquired", 1.0) or 1.0
            d["purchase_price_calculated"] = round(ebitda_fp * mult * pct, 0)
            d["enterprise_value"] = d["purchase_price_calculated"]
            corrections.append(
                f"purchase_price calculated: {ebitda_fp} x {mult} = {d['purchase_price_calculated']}")

    # CHECK 11: CapEx sanity — annual CapEx typically 0.3-5% of revenue
    capex_h = f.get("capex_hist", [None] * 3)
    for i in range(min(3, len(capex_h))):
        if rev_h[i] and capex_h[i] and capex_h[i] != 0:
            ratio = abs(capex_h[i]) / rev_h[i]
            if ratio > 0.10:
                corrections.append(
                    f"capex_hist[{i}] = {capex_h[i]} is {ratio:.1%} of revenue — may be cumulative")

    # CHECK 12: Projection SG&A sanity — same < 5% threshold as historical
    sga_p = f.get("sga_proj", [None] * 5)
    rev_p = f.get("net_revenue_proj", [None] * 5)
    for i in range(min(5, len(sga_p))):
        if rev_p[i] and sga_p[i]:
            ratio_p = sga_p[i] / rev_p[i]
            if ratio_p < 0.05:
                corrections.append(
                    f"sga_proj[{i}] = {sga_p[i]} is only {ratio_p:.1%} of proj revenue — likely wrong row, setting null")
                sga_p[i] = None
    f["sga_proj"] = sga_p

    data["financials"] = f
    data["deal"] = d
    # Preserve corrections from earlier pipeline steps (e.g. check_and_fix_row_swaps)
    data.setdefault("_corrections_applied", []).extend(corrections)

    if corrections:
        print(f"[VALIDATION] {len(corrections)} corrections applied:")
        for c_item in corrections:
            print(f"  > {c_item}")

    return data


def post_process_extraction(raw_json):
    """Fill in calculated fields and validate key relationships."""
    f = raw_json.get("financials", {})
    d = raw_json.get("deal", {})
    c = raw_json.get("collateral", {})

    # Ensure arrays exist with correct lengths
    for key in ["net_revenue_hist", "gross_profit_hist", "sga_hist",
                "adjustments_hist", "adj_ebitda_hist", "other_income_hist",
                "depreciation_hist", "capex_hist"]:
        arr = f.get(key)
        if not isinstance(arr, list) or len(arr) < 3:
            f[key] = (arr or []) + [None] * (3 - len(arr or []))

    for key in ["net_revenue_proj", "gross_profit_proj", "sga_proj",
                "adjustments_proj", "adj_ebitda_proj", "depreciation_proj",
                "capex_proj", "mgmt_fees_proj"]:
        arr = f.get(key)
        if not isinstance(arr, list) or len(arr) < 5:
            f[key] = (arr or []) + [None] * (5 - len(arr or []))

    # 1. Calculate purchase price if not present
    if d.get("purchase_price_calculated") is None:
        ebitda = d.get("ebitda_for_price")
        mult = d.get("entry_multiple")
        pct = d.get("pct_acquired", 1.0) or 1.0
        if ebitda and mult:
            d["purchase_price_calculated"] = round(ebitda * mult * pct, 2)
            if d.get("enterprise_value") is None:
                d["enterprise_value"] = d["purchase_price_calculated"]

    # 2. Backfill adj_ebitda_hist from components if null
    rev = f.get("net_revenue_hist", [None]*3)
    gp = f.get("gross_profit_hist", [None]*3)
    sga = f.get("sga_hist", [None]*3)
    adj = f.get("adjustments_hist", [None]*3)
    ebitda_hist = f.get("adj_ebitda_hist", [None]*3)

    for i in range(min(3, len(ebitda_hist))):
        if ebitda_hist[i] is None and gp[i] is not None and sga[i] is not None:
            adj_val = adj[i] or 0
            ebitda_hist[i] = round(gp[i] - sga[i] + adj_val, 2)
    f["adj_ebitda_hist"] = ebitda_hist

    # 3. Calculate margins
    ebitda_margins = []
    gm_pcts = []
    for i in range(3):
        r = rev[i] if i < len(rev) else None
        e = ebitda_hist[i] if i < len(ebitda_hist) else None
        g = gp[i] if i < len(gp) else None
        ebitda_margins.append(round(e / r, 4) if r and e else None)
        gm_pcts.append(round(g / r, 4) if r and g else None)
    f["ebitda_margin_hist"] = ebitda_margins
    f["gm_pct_hist"] = gm_pcts

    # 4. Revenue growth
    growth = [None]
    for i in range(1, 3):
        prev = rev[i - 1] if i - 1 < len(rev) else None
        curr = rev[i] if i < len(rev) else None
        if prev and curr and prev != 0:
            growth.append(round((curr - prev) / prev, 4))
        else:
            growth.append(None)
    f["revenue_growth_hist"] = growth

    # 5. Projection margins
    rev_p = f.get("net_revenue_proj", [None]*5)
    ebitda_p = f.get("adj_ebitda_proj", [None]*5)
    gp_p = f.get("gross_profit_proj", [None]*5)
    margin_p = []
    gm_p = []
    for i in range(5):
        rp = rev_p[i] if i < len(rev_p) else None
        ep = ebitda_p[i] if i < len(ebitda_p) else None
        gpp = gp_p[i] if i < len(gp_p) else None
        margin_p.append(round(ep / rp, 4) if rp and ep else None)
        gm_p.append(round(gpp / rp, 4) if rp and gpp else None)
    f["ebitda_margin_proj"] = margin_p
    f["gm_pct_proj"] = gm_p

    # 6. ABL availability
    ar_val = c.get("ar_value")
    ar_rate = c.get("ar_advance_rate", 0.75) or 0.75
    inv_val = c.get("inventory_value")
    inv_rate = c.get("inventory_advance_rate", 0.70) or 0.70
    avail = 0
    if ar_val:
        avail += ar_val * ar_rate
    if inv_val:
        avail += inv_val * inv_rate
    c["abl_availability_calculated"] = round(avail, 2) if avail > 0 else None

    # 7. Leverage ratio
    term = d.get("term_loan_amount", 0) or 0
    sn = d.get("seller_note_amount", 0) or 0
    ebitda_for_price = d.get("ebitda_for_price")
    if ebitda_for_price and ebitda_for_price > 0:
        d["leverage_ratio"] = round((term + sn) / ebitda_for_price, 2)

    # 8. LTM convenience fields
    d["revenue_ltm"] = rev[2] if len(rev) > 2 and rev[2] else None
    d["ebitda_ltm"] = ebitda_hist[2] if len(ebitda_hist) > 2 and ebitda_hist[2] else None

    raw_json["financials"] = f
    raw_json["deal"] = d
    raw_json["collateral"] = c
    return raw_json


def extract_document_financials(doc_id, ocr_text_path):
    """Main extraction function. Runs in a background thread after OCR completes."""
    try:
        update_document_extraction(doc_id, extraction_status='processing', extraction_error=None)
        print(f"[EXTRACTION] doc_id={doc_id}: Started AI extraction via NVIDIA NIM")

        # Read OCR text
        full_path = ocr_text_path if os.path.isabs(ocr_text_path) else os.path.join(BASE_DIR, ocr_text_path)
        with open(full_path, 'r', encoding='utf-8') as f:
            ocr_text = f.read()

        # Truncate if too long
        if len(ocr_text) > MAX_TEXT_CHARS:
            first_chunk = ocr_text[:30000]
            last_chunk = ocr_text[-20000:]
            ocr_text = (
                first_chunk
                + "\n\n[... MIDDLE SECTION TRUNCATED FOR LENGTH ...]\n\n"
                + last_chunk
            )
            print(f"[EXTRACTION] doc_id={doc_id}: Text truncated to {len(ocr_text)} chars")

        # Call NVIDIA NIM API with retry
        extraction_json = _call_nvidia_with_retry(ocr_text)

        # Parse and validate
        extraction_data = json.loads(extraction_json)

        # 1. INTEGRITY: fix row swaps (EBITDA vs Operating Income confusion)
        extraction_data = check_and_fix_row_swaps(extraction_data)

        # 2. INTEGRITY: infer missing revenue from GP + known GM%
        extraction_data = check_revenue_completeness(extraction_data)

        # 3. Post-process: fill calculated fields (margins, growth, ABL, etc.)
        extraction_data = post_process_extraction(extraction_data)

        # 4. Validate and auto-correct known extraction bugs
        extraction_data = validate_and_correct(extraction_data)

        # 5. Derive missing values from available data
        extraction_data = derive_missing_values(extraction_data)

        # Save extraction JSON
        extraction_path = os.path.join(EXTRACTIONS_FOLDER, f'{doc_id}.json')
        with open(extraction_path, 'w', encoding='utf-8') as f:
            json.dump(extraction_data, f, indent=2, ensure_ascii=False)

        # Extract key fields for DB columns
        deal = extraction_data.get('deal', {})
        confidence = extraction_data.get('confidence', {})
        hist_years = extraction_data.get('historical_years', [])

        overall_conf = confidence.get('overall_confidence', 0)
        # Normalize: if 0-100 scale, convert to 0-1 for DB
        if isinstance(overall_conf, (int, float)) and overall_conf > 1:
            overall_conf = overall_conf / 100.0

        update_document_extraction(
            doc_id,
            extraction_status='completed',
            extraction_completed_at=datetime.now(timezone.utc).isoformat(),
            extraction_path=os.path.join('storage', 'extractions', f'{doc_id}.json'),
            company_name=extraction_data.get('company_name'),
            fiscal_year_1=hist_years[0] if len(hist_years) > 0 else None,
            fiscal_year_2=hist_years[1] if len(hist_years) > 1 else None,
            fiscal_year_3=hist_years[2] if len(hist_years) > 2 else None,
            ebitda_ltm=deal.get('ebitda_ltm'),
            revenue_ltm=deal.get('revenue_ltm'),
            entry_multiple=deal.get('entry_multiple'),
            purchase_price=deal.get('purchase_price_calculated'),
            confidence_score=overall_conf,
        )

        print(f"[EXTRACTION] doc_id={doc_id}: Completed — company={extraction_data.get('company_name')}, confidence={overall_conf:.0%}")

    except Exception as e:
        print(f"[EXTRACTION ERROR] doc_id={doc_id}: {e}")
        import traceback
        traceback.print_exc()
        update_document_extraction(
            doc_id,
            extraction_status='failed',
            extraction_error=str(e),
        )


def _call_nvidia_with_retry(ocr_text, max_retries=3):
    """Call NVIDIA NIM API (OpenAI-compatible) with exponential backoff retry."""
    client = OpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=NVIDIA_API_KEY,
    )

    last_exception = None
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=NVIDIA_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_MESSAGE_TEMPLATE.format(ocr_text=ocr_text)}
                ],
                temperature=0.1,
                top_p=0.9,
                max_tokens=8192,
                stream=True,
            )

            response_text = ""
            for chunk in completion:
                if not getattr(chunk, "choices", None):
                    continue
                if chunk.choices and chunk.choices[0].delta.content is not None:
                    response_text += chunk.choices[0].delta.content

            print(f"[EXTRACTION] NVIDIA response length: {len(response_text)} chars")

            # Strip markdown code fences if present
            response_text = response_text.strip()
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                lines = lines[1:]
                if lines and lines[-1].strip() == '```':
                    lines = lines[:-1]
                response_text = '\n'.join(lines)

            # Validate it's valid JSON
            json.loads(response_text)
            return response_text

        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = 2 ** (attempt + 1)
                print(f"[EXTRACTION RETRY] attempt {attempt + 1}/{max_retries}, retrying in {delay}s: {e}")
                time.sleep(delay)

    raise last_exception
