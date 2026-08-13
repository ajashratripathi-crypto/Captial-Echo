import os
import re
import io
import time
import hashlib
import requests
import pdfplumber
import yfinance as yf

from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL missing")

if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_KEY missing")


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)


HOUSE_BASE = "https://disclosures-clerk.house.gov"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 Capital-Echo/1.0 "
        "(public congressional disclosure research)"
    )
}


# ==========================================================
# MARKET DATA
# ==========================================================

def get_market_price_on_date(ticker, target_date):

    stock = yf.Ticker(ticker)

    start = target_date
    end = target_date + timedelta(days=7)

    history = stock.history(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        auto_adjust=False
    )

    if history.empty:
        return None

    return round(
        float(history.iloc[0]["Close"]),
        4
    )


def get_current_price(ticker):

    stock = yf.Ticker(ticker)

    history = stock.history(
        period="5d",
        interval="1d",
        auto_adjust=False
    )

    if history.empty:
        return None

    return round(
        float(history.iloc[-1]["Close"]),
        4
    )


# ==========================================================
# LAG ENGINE
# ==========================================================

def calculate_signal_status(
    lag_days,
    missed_move_pct
):

    if (
        missed_move_pct is not None
        and missed_move_pct >= 20
    ):
        return "Priced In"

    if lag_days < 14:
        return "Fresh Signal"

    if lag_days < 30:
        return "Moderate Lag"

    return "Late Signal"


def calculate_lag_engine(
    ticker,
    transaction_date,
    disclosure_date
):

    transaction_dt = datetime.strptime(
        transaction_date,
        "%Y-%m-%d"
    )

    disclosure_dt = datetime.strptime(
        disclosure_date,
        "%Y-%m-%d"
    )

    lag_days = (
        disclosure_dt - transaction_dt
    ).days

    if lag_days < 0:
        raise ValueError(
            "Disclosure date is earlier than transaction date"
        )

    transaction_price = get_market_price_on_date(
        ticker,
        transaction_dt
    )

    disclosure_price = get_market_price_on_date(
        ticker,
        disclosure_dt
    )

    current_price = get_current_price(
        ticker
    )

    real_return_pct = None
    missed_move_pct = None

    if (
        disclosure_price is not None
        and current_price is not None
        and disclosure_price != 0
    ):

        real_return_pct = (
            (
                current_price
                - disclosure_price
            )
            / disclosure_price
        ) * 100

    if (
        transaction_price is not None
        and disclosure_price is not None
        and transaction_price != 0
    ):

        missed_move_pct = (
            (
                disclosure_price
                - transaction_price
            )
            / transaction_price
        ) * 100

    return {

        "lag_days": lag_days,

        "transaction_price":
            transaction_price,

        "disclosure_price":
            disclosure_price,

        "current_price":
            current_price,

        "real_return_pct":
            round(real_return_pct, 2)
            if real_return_pct is not None
            else None,

        "missed_move_pct":
            round(missed_move_pct, 2)
            if missed_move_pct is not None
            else None,

        "signal_status":
            calculate_signal_status(
                lag_days,
                missed_move_pct
            ),

        "last_updated":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


# ==========================================================
# HOUSE DISCLOSURE SCRAPER
# ==========================================================

def get_house_search_page():

    url = (
        "https://disclosures-clerk.house.gov/"
        "FinancialDisclosure/ViewSearch"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    return response.text


def find_ptr_links(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    links = []

    for tag in soup.find_all(
        "a",
        href=True
    ):

        href = tag["href"]

        if "ptr-pdfs" not in href.lower():
            continue

        if href.startswith("http"):
            url = href

        else:
            url = (
                HOUSE_BASE
                + "/"
                + href.lstrip("/")
            )

        links.append(url)

    return list(
        dict.fromkeys(links)
    )


# ==========================================================
# PDF EXTRACTION
# ==========================================================

def download_pdf(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=45
    )

    response.raise_for_status()

    return response.content


def extract_pdf_text(pdf_bytes):

    text = []

    with pdfplumber.open(
        io.BytesIO(pdf_bytes)
    ) as pdf:

        for page in pdf.pages:

            page_text = (
                page.extract_text()
                or ""
            )

            text.append(
                page_text
            )

    return "\n".join(text)


# ==========================================================
# PTR PARSER
# ==========================================================

def extract_member_name(text):

    patterns = [

        r"Name:\s*([A-Za-z ,.'\-]+)",

        r"Filer Name:\s*([A-Za-z ,.'\-]+)"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            return (
                match.group(1)
                .strip()
            )

    return "Unknown"


def extract_filing_date(text):

    patterns = [

        r"Filing Date:\s*(\d{1,2}/\d{1,2}/\d{4})",

        r"Date Filed:\s*(\d{1,2}/\d{1,2}/\d{4})"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            date = datetime.strptime(
                match.group(1),
                "%m/%d/%Y"
            )

            return date.strftime(
                "%Y-%m-%d"
            )

    return None


def normalize_date(value):

    formats = [
        "%m/%d/%Y",
        "%m/%d/%y"
    ]

    for format_string in formats:

        try:

            date = datetime.strptime(
                value,
                format_string
            )

            return date.strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            pass

    return None


def find_ticker(text):

    patterns = [

        r"\(([A-Z]{1,5})\)",

        r"\[([A-Z]{1,5})\]"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text
        )

        if match:

            ticker = match.group(1)

            ignored = {
                "JT",
                "SP",
                "DC",
                "US",
                "IRA",
                "NA"
            }

            if ticker not in ignored:
                return ticker

    return None


def parse_transaction_lines(
    text,
    filing_url
):

    politician = extract_member_name(
        text
    )

    disclosure_date = (
        extract_filing_date(
            text
        )
    )

    if not disclosure_date:

        print(
            "Could not determine filing date."
        )

        return []

    trades = []

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    date_pattern = re.compile(
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    )

    amount_pattern = re.compile(
        r"\$[\d,]+"
        r"(?:\s*-\s*\$[\d,]+)?"
    )

    for line in lines:

        ticker = find_ticker(
            line
        )

        if not ticker:
            continue

        date_match = date_pattern.search(
            line
        )

        if not date_match:
            continue

        transaction_date = normalize_date(
            date_match.group(0)
        )

        if not transaction_date:
            continue

        upper = line.upper()

        if (
            "PURCHASE" in upper
            or " P " in f" {upper} "
        ):

            transaction_type = "Purchase"

        elif (
            "SALE" in upper
            or " S " in f" {upper} "
        ):

            transaction_type = "Sale"

        else:

            transaction_type = "Unknown"

        amount_match = amount_pattern.search(
            line
        )

        amount = (
            amount_match.group(0)
            if amount_match
            else None
        )

        asset_name = line

        trade = {

            "politician":
                politician,

            "party":
                None,

            "ticker":
                ticker,

            "asset_name":
                asset_name[:500],

            "transaction_type":
                transaction_type,

            "transaction_date":
                transaction_date,

            "disclosure_date":
                disclosure_date,

            "amount":
                amount,

            "filing_url":
                filing_url
        }

        trades.append(
            trade
        )

    return trades


# ==========================================================
# DUPLICATE CHECK
# ==========================================================

def trade_exists(
    trade
):

    response = (
        supabase
        .table("trades")
        .select("id")
        .eq(
            "politician",
            trade["politician"]
        )
        .eq(
            "ticker",
            trade["ticker"]
        )
        .eq(
            "transaction_date",
            trade["transaction_date"]
        )
        .eq(
            "filing_url",
            trade["filing_url"]
        )
        .limit(1)
        .execute()
    )

    return bool(
        response.data
    )


# ==========================================================
# PROCESS ONE TRADE
# ==========================================================

def process_trade(
    trade
):

    ticker = trade["ticker"]

    print("")
    print(
        f"Processing {ticker}"
    )

    if trade_exists(trade):

        print(
            "Trade already exists. Skipping."
        )

        return

    try:

        metrics = calculate_lag_engine(

            ticker=
                ticker,

            transaction_date=
                trade["transaction_date"],

            disclosure_date=
                trade["disclosure_date"]
        )

    except Exception as error:

        print(
            f"Lag Engine failed for "
            f"{ticker}: {error}"
        )

        return

    record = {
        **trade,
        **metrics
    }

    supabase.table(
        "trades"
    ).insert(
        record
    ).execute()

    print(
        f"Saved {ticker}"
    )

    print(
        f"Politician: "
        f"{trade['politician']}"
    )

    print(
        f"Lag: "
        f"{metrics['lag_days']} days"
    )

    print(
        f"Transaction price: "
        f"{metrics['transaction_price']}"
    )

    print(
        f"Disclosure price: "
        f"{metrics['disclosure_price']}"
    )

    print(
        f"Current price: "
        f"{metrics['current_price']}"
    )

    print(
        f"Follower ROI: "
        f"{metrics['real_return_pct']}%"
    )

    print(
        f"Missed move: "
        f"{metrics['missed_move_pct']}%"
    )

    print(
        f"Signal: "
        f"{metrics['signal_status']}"
    )


# ==========================================================
# PROCESS PTR
# ==========================================================

def process_ptr(
    url
):

    print("")
    print(
        "--------------------------------"
    )

    print(
        f"Reading PTR:"
    )

    print(
        url
    )

    try:

        pdf_bytes = download_pdf(
            url
        )

        text = extract_pdf_text(
            pdf_bytes
        )

        trades = parse_transaction_lines(
            text,
            url
        )

        print(
            f"Found {len(trades)} "
            f"possible stock trades."
        )

        for trade in trades:

            process_trade(
                trade
            )

            time.sleep(
                0.75
            )

    except Exception as error:

        print(
            f"PTR failed: {error}"
        )


# ==========================================================
# MAIN
# ==========================================================

def main():

    print("")
    print(
        "================================"
    )

    print(
        " CAPITAL-ECHO DATA ENGINE"
    )

    print(
        "================================"
    )

    print("")

    print(
        "Checking Supabase..."
    )

    supabase.table(
        "trades"
    ).select(
        "id"
    ).limit(
        1
    ).execute()

    print(
        "Supabase connected."
    )

    print("")
    print(
        "Searching House disclosures..."
    )

    html = get_house_search_page()

    ptr_links = find_ptr_links(
        html
    )

    print(
        f"Found {len(ptr_links)} "
        f"PTR filings."
    )

    # Don't hammer the government site.
    # First version processes newest 20.
    for url in ptr_links[:20]:

        process_ptr(
            url
        )

        time.sleep(
            1
        )

    print("")
    print(
        "================================"
    )

    print(
        " CAPITAL-ECHO RUN COMPLETE"
    )

    print(
        "================================"
    )


if __name__ == "__main__":
    main()
