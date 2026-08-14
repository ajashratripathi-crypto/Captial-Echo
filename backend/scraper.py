import io
import os
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import pdfplumber
import requests
import yfinance as yf

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from supabase import create_client


# ============================================================
# CAPITAL-ECHO CONFIGURATION
# ============================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("Missing SUPABASE_URL environment variable.")

if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("Missing SUPABASE_SERVICE_KEY environment variable.")


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)


HOUSE_BASE = (
    "https://disclosures-clerk.house.gov"
)

CURRENT_YEAR = datetime.now().year


# Number of newest congressional PTR filings processed
MAX_PTRS_PER_RUN = 40


# Pause between House PDF downloads
REQUEST_DELAY = 0.6


# Market data
MARKET_HISTORY_DAYS = 35

MAX_MARKET_TICKERS_PER_RUN = 150

MARKET_REQUEST_DELAY = 0.30


# ============================================================
# REQUEST SESSION
# ============================================================

session = requests.Session()

retry_strategy = Retry(
    total=4,
    backoff_factor=0.8,
    status_forcelist=[
        429,
        500,
        502,
        503,
        504
    ],
    allowed_methods=["GET"]
)

adapter = HTTPAdapter(
    max_retries=retry_strategy
)

session.mount(
    "https://",
    adapter
)

session.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 Capital-Echo/1.0"
    }
)


# ============================================================
# CACHES
# ============================================================

historical_price_cache = {}

current_price_cache = {}


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value)

    value = value.replace("\x00", " ")

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def parse_date(value):
    if not value:
        return None

    value = clean_text(value)

    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d"
    ]

    for date_format in formats:

        try:

            return datetime.strptime(
                value,
                date_format
            ).date()

        except ValueError:
            pass

    return None


def iso_date(value):
    if value is None:
        return None

    if hasattr(
        value,
        "isoformat"
    ):
        return value.isoformat()

    return str(value)


def round_number(
    value,
    digits=4
):

    if value is None:
        return None

    try:
        return round(
            float(value),
            digits
        )

    except Exception:
        return None


def safe_float(value):
    try:

        value = float(value)

        if value != value:
            return None

        return value

    except Exception:
        return None


# ============================================================
# MARKET PRICE FUNCTIONS
# ============================================================

def normalize_yahoo_ticker(ticker):

    if not ticker:
        return None

    ticker = (
        str(ticker)
        .strip()
        .upper()
    )

    # Yahoo commonly represents share classes with "-"
    #
    # BRK.B -> BRK-B

    ticker = ticker.replace(
        ".",
        "-"
    )

    return ticker


def valid_market_ticker(ticker):

    if not ticker:
        return False

    ticker = (
        str(ticker)
        .strip()
        .upper()
    )

    if len(ticker) > 12:
        return False

    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789.-"
    )

    return all(
        char in allowed
        for char in ticker
    )


def get_market_price_on_date(
    ticker,
    target_date
):

    if not ticker:
        return None

    if not target_date:
        return None


    cache_key = (
        ticker,
        str(target_date)
    )


    if cache_key in historical_price_cache:

        return historical_price_cache[
            cache_key
        ]


    yahoo_ticker = (
        normalize_yahoo_ticker(
            ticker
        )
    )


    try:

        start_date = target_date

        end_date = (
            target_date
            +
            timedelta(days=8)
        )


        stock = yf.Ticker(
            yahoo_ticker
        )


        history = stock.history(
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False
        )


        if (
            history is None
            or history.empty
        ):

            historical_price_cache[
                cache_key
            ] = None

            return None


        close_series = (
            history["Close"]
            .dropna()
        )


        if close_series.empty:

            historical_price_cache[
                cache_key
            ] = None

            return None


        value = float(
            close_series.iloc[0]
        )


        value = round_number(
            value,
            4
        )


        historical_price_cache[
            cache_key
        ] = value


        return value


    except Exception as exc:

        print(
            f"[PRICE] {ticker} "
            f"{target_date}: {exc}"
        )

        historical_price_cache[
            cache_key
        ] = None

        return None


def get_current_price(
    ticker
):

    if not ticker:
        return None


    ticker = (
        str(ticker)
        .strip()
        .upper()
    )


    if ticker in current_price_cache:

        return current_price_cache[
            ticker
        ]


    yahoo_ticker = (
        normalize_yahoo_ticker(
            ticker
        )
    )


    try:

        stock = yf.Ticker(
            yahoo_ticker
        )


        # Try recent intraday price first

        history = stock.history(
            period="1d",
            interval="1m",
            auto_adjust=False,
            actions=False
        )


        if (
            history is not None
            and not history.empty
        ):

            closes = (
                history["Close"]
                .dropna()
            )

            if not closes.empty:

                value = float(
                    closes.iloc[-1]
                )

                value = round_number(
                    value,
                    4
                )

                current_price_cache[
                    ticker
                ] = value

                return value


        # Fallback to daily data

        history = stock.history(
            period="5d",
            interval="1d",
            auto_adjust=False,
            actions=False
        )


        if (
            history is not None
            and not history.empty
        ):

            closes = (
                history["Close"]
                .dropna()
            )

            if not closes.empty:

                value = float(
                    closes.iloc[-1]
                )

                value = round_number(
                    value,
                    4
                )

                current_price_cache[
                    ticker
                ] = value

                return value


    except Exception as exc:

        print(
            f"[PRICE] Current "
            f"{ticker}: {exc}"
        )


    current_price_cache[
        ticker
    ] = None

    return None


# ============================================================
# CAPITAL-ECHO LAG ENGINE
# ============================================================

def calculate_lag_days(
    transaction_date,
    disclosure_date
):

    if (
        transaction_date is None
        or disclosure_date is None
    ):
        return None


    return (
        disclosure_date
        -
        transaction_date
    ).days


def calculate_percentage(
    start_value,
    end_value
):

    if (
        start_value is None
        or end_value is None
    ):
        return None


    if start_value == 0:
        return None


    return (
        (
            end_value
            -
            start_value
        )
        /
        start_value
    ) * 100


def determine_signal_status(
    lag_days,
    missed_move_pct
):

    if missed_move_pct is not None:

        if missed_move_pct >= 20:

            return "Priced In"

        if missed_move_pct <= -20:

            return "Price Fell During Lag"


    if lag_days is None:

        return "Unknown"


    if lag_days < 14:

        return "Fresh Signal"


    if lag_days < 30:

        return "Moderate Lag"


    return "Late Signal"


def run_lag_engine(
    ticker,
    transaction_date,
    disclosure_date
):

    lag_days = calculate_lag_days(
        transaction_date,
        disclosure_date
    )


    transaction_price = (
        get_market_price_on_date(
            ticker,
            transaction_date
        )
    )


    disclosure_price = (
        get_market_price_on_date(
            ticker,
            disclosure_date
        )
    )


    current_price = (
        get_current_price(
            ticker
        )
    )


    missed_move_pct = (
        calculate_percentage(
            transaction_price,
            disclosure_price
        )
    )


    follower_roi = (
        calculate_percentage(
            disclosure_price,
            current_price
        )
    )


    signal_status = (
        determine_signal_status(
            lag_days,
            missed_move_pct
        )
    )


    return {

        "lag_days":
            lag_days,

        "transaction_price":
            round_number(
                transaction_price,
                4
            ),

        "disclosure_price":
            round_number(
                disclosure_price,
                4
            ),

        "current_price":
            round_number(
                current_price,
                4
            ),

        "real_return_pct":
            round_number(
                follower_roi,
                2
            ),

        "missed_move_pct":
            round_number(
                missed_move_pct,
                2
            ),

        "signal_status":
            signal_status
    }


# ============================================================
# HOUSE FILING INDEX
# ============================================================

def download_house_filing_index(
    year
):

    url = (
        f"{HOUSE_BASE}"
        f"/public_disc/"
        f"financial-pdfs/"
        f"{year}FD.zip"
    )


    print(
        f"[HOUSE] Downloading "
        f"{year} filing index..."
    )


    response = session.get(
        url,
        timeout=60
    )

    response.raise_for_status()


    with zipfile.ZipFile(
        io.BytesIO(
            response.content
        )
    ) as archive:

        xml_files = [
            name
            for name in archive.namelist()
            if name.lower().endswith(
                ".xml"
            )
        ]


        if not xml_files:

            raise RuntimeError(
                f"No XML file found "
                f"in {year} House archive."
            )


        xml_bytes = archive.read(
            xml_files[0]
        )


    return xml_bytes


def parse_house_index(
    xml_bytes,
    year
):

    root = ET.fromstring(
        xml_bytes
    )


    filings = []


    for member in root:

        values = {}


        for child in member:

            key = (
                child.tag
                .split("}")[-1]
            )

            values[key] = (
                clean_text(
                    child.text
                )
            )


        filing_type = (
            values.get(
                "FilingType"
            )
            or ""
        ).upper()


        # P = Periodic Transaction Report

        if filing_type != "P":
            continue


        doc_id = (
            values.get("DocID")
            or values.get("DocumentID")
        )


        if not doc_id:
            continue


        filing_date_text = (
            values.get(
                "FilingDate"
            )
        )


        filing_date = (
            parse_date(
                filing_date_text
            )
        )


        first_name = (
            values.get(
                "First"
            )
            or ""
        )


        last_name = (
            values.get(
                "Last"
            )
            or ""
        )


        prefix = (
            values.get(
                "Prefix"
            )
            or ""
        )


        suffix = (
            values.get(
                "Suffix"
            )
            or ""
        )


        politician = clean_text(
            " ".join(
                part
                for part in [
                    prefix,
                    first_name,
                    last_name,
                    suffix
                ]
                if part
            )
        )


        filing_url = (
            f"{HOUSE_BASE}"
            f"/public_disc/"
            f"ptr-pdfs/"
            f"{year}/"
            f"{doc_id}.pdf"
        )


        filings.append(
            {

                "doc_id":
                    doc_id,

                "politician":
                    politician,

                "filing_date":
                    filing_date,

                "filing_url":
                    filing_url,

                "year":
                    year
            }
        )


    filings.sort(
        key=lambda row: (
            row["filing_date"]
            or datetime.min.date()
        ),
        reverse=True
    )


    print(
        f"[HOUSE] Found "
        f"{len(filings)} "
        f"PTR filings in {year}."
    )


    return filings


def get_recent_ptr_filings():

    all_filings = []


    for year in [
        CURRENT_YEAR,
        CURRENT_YEAR - 1
    ]:

        try:

            xml_bytes = (
                download_house_filing_index(
                    year
                )
            )


            filings = (
                parse_house_index(
                    xml_bytes,
                    year
                )
            )


            all_filings.extend(
                filings
            )


        except Exception as exc:

            print(
                f"[HOUSE] Could not "
                f"load {year}: {exc}"
            )


    all_filings.sort(
        key=lambda row: (
            row["filing_date"]
            or datetime.min.date()
        ),
        reverse=True
    )


    filings = all_filings[
        :MAX_PTRS_PER_RUN
    ]


    print(
        f"[HOUSE] Processing "
        f"{len(filings)} "
        f"newest PTRs."
    )


    return filings


# ============================================================
# PDF EXTRACTION
# ============================================================

def download_pdf(
    url
):

    response = session.get(
        url,
        timeout=60
    )

    response.raise_for_status()

    return response.content


def extract_pdf_text(
    pdf_bytes
):

    text_parts = []


    try:

        with pdfplumber.open(
            io.BytesIO(
                pdf_bytes
            )
        ) as pdf:

            for page in pdf.pages:

                # layout=True helps preserve
                # House disclosure columns better.

                text = page.extract_text(
                    layout=True
                )

                if text:
                    text_parts.append(
                        text
                    )


    except Exception as exc:

        print(
            f"[PTR] PDF parse "
            f"error: {exc}"
        )

        return None


    if not text_parts:
        return None


    return "\n".join(
        text_parts
    )


# ============================================================
# HOUSE TRANSACTION PARSER
# ============================================================

TICKER_MARKER = re.compile(
    r"\("
    r"(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})"
    r"\)"
    r"\s*"
    r"\["
    r"(?P<asset_type>[A-Z0-9]+)"
    r"\]",
    re.MULTILINE
)


TRANSACTION_INFO = re.compile(
    r"\b"
    r"(?P<type>"
    r"P"
    r"|S(?:\s*\(partial\))?"
    r"|E"
    r")"
    r"\s+"
    r"(?P<date>"
    r"\d{1,2}/"
    r"\d{1,2}/"
    r"\d{4}"
    r")"
    r"(?:\s+"
    r"(?P<notification>"
    r"\d{1,2}/"
    r"\d{1,2}/"
    r"\d{4}"
    r")"
    r")?"
    r"\s+"
    r"(?P<amount>"
    r"\$[\d,]+\s*-\s*\$[\d,]+"
    r"|Over\s+\$[\d,]+"
    r")",
    re.IGNORECASE
)


ALLOWED_ASSET_TYPES = {
    "ST",
    "EF"
}


def transaction_type_name(
    value
):

    if not value:
        return "Unknown"


    value = (
        value
        .strip()
        .upper()
    )


    if value.startswith("P"):
        return "Purchase"


    if value.startswith("S"):
        return "Sale"


    if value.startswith("E"):
        return "Exchange"


    return value


def extract_asset_name(
    text,
    marker_start
):

    before = text[
        max(
            0,
            marker_start - 300
        ):
        marker_start
    ]


    lines = [
        clean_text(line)
        for line in before.splitlines()
        if clean_text(line)
    ]


    if not lines:
        return None


    candidate = lines[-1]


    # Remove likely row-number prefixes

    candidate = re.sub(
        r"^\d+\.\s*",
        "",
        candidate
    )


    # Remove owner prefixes like SP / JT / DC

    candidate = re.sub(
        r"^(SP|JT|DC)\s+",
        "",
        candidate,
        flags=re.IGNORECASE
    )


    return clean_text(
        candidate
    )


def parse_market_transactions(
    text
):

    if not text:
        return []


    transactions = []


    markers = list(
        TICKER_MARKER.finditer(
            text
        )
    )


    for index, marker in enumerate(
        markers
    ):

        ticker = (
            marker.group(
                "ticker"
            )
            .upper()
        )


        asset_type = (
            marker.group(
                "asset_type"
            )
            .upper()
        )


        if asset_type not in ALLOWED_ASSET_TYPES:
            continue


        segment_start = (
            marker.end()
        )


        if index + 1 < len(markers):

            segment_end = (
                markers[
                    index + 1
                ].start()
            )

        else:

            segment_end = min(
                len(text),
                segment_start + 1000
            )


        segment = text[
            segment_start:
            segment_end
        ]


        transaction_match = (
            TRANSACTION_INFO.search(
                segment
            )
        )


        if not transaction_match:
            continue


        transaction_date = (
            parse_date(
                transaction_match.group(
                    "date"
                )
            )
        )


        notification_date = (
            parse_date(
                transaction_match.group(
                    "notification"
                )
            )
        )


        if transaction_date is None:
            continue


        transaction_type = (
            transaction_type_name(
                transaction_match.group(
                    "type"
                )
            )
        )


        amount = clean_text(
            transaction_match.group(
                "amount"
            )
        )


        asset_name = (
            extract_asset_name(
                text,
                marker.start()
            )
        )


        transactions.append(
            {

                "ticker":
                    ticker,

                "asset_name":
                    asset_name,

                "asset_type":
                    asset_type,

                "transaction_type":
                    transaction_type,

                "transaction_date":
                    transaction_date,

                "notification_date":
                    notification_date,

                "amount":
                    amount
            }
        )


    # Remove exact parser duplicates

    unique = []

    seen = set()


    for trade in transactions:

        key = (
            trade["ticker"],
            trade["transaction_type"],
            trade["transaction_date"],
            trade["amount"]
        )


        if key in seen:
            continue


        seen.add(
            key
        )

        unique.append(
            trade
        )


    return unique


# ============================================================
# TRADE DATABASE FUNCTIONS
# ============================================================

def trade_exists(
    politician,
    ticker,
    transaction_date,
    transaction_type,
    filing_url
):

    try:

        response = (
            supabase
            .table("trades")
            .select("id")
            .eq(
                "politician",
                politician
            )
            .eq(
                "ticker",
                ticker
            )
            .eq(
                "transaction_date",
                iso_date(
                    transaction_date
                )
            )
            .eq(
                "transaction_type",
                transaction_type
            )
            .eq(
                "filing_url",
                filing_url
            )
            .limit(1)
            .execute()
        )


        return bool(
            response.data
        )


    except Exception as exc:

        print(
            f"[DB] Duplicate "
            f"check failed: {exc}"
        )

        return False


def save_trade(
    filing,
    trade
):

    politician = (
        filing["politician"]
    )


    ticker = (
        trade["ticker"]
    )


    transaction_date = (
        trade["transaction_date"]
    )


    transaction_type = (
        trade["transaction_type"]
    )


    filing_url = (
        filing["filing_url"]
    )


    if trade_exists(
        politician,
        ticker,
        transaction_date,
        transaction_type,
        filing_url
    ):

        print(
            f"[DB] Already exists: "
            f"{politician} "
            f"{ticker} "
            f"{transaction_date}"
        )

        return False


    disclosure_date = (
        filing["filing_date"]
    )


    metrics = (
        run_lag_engine(
            ticker,
            transaction_date,
            disclosure_date
        )
    )


    row = {

        "politician":
            politician,

        # Party enrichment will come later.
        # NULL is preferable to fake party data.

        "party":
            None,

        "ticker":
            ticker,

        "asset_name":
            trade.get(
                "asset_name"
            ),

        "transaction_type":
            transaction_type,

        "transaction_date":
            iso_date(
                transaction_date
            ),

        "disclosure_date":
            iso_date(
                disclosure_date
            ),

        "amount":
            trade.get(
                "amount"
            ),

        "filing_url":
            filing_url,

        "lag_days":
            metrics[
                "lag_days"
            ],

        "transaction_price":
            metrics[
                "transaction_price"
            ],

        "disclosure_price":
            metrics[
                "disclosure_price"
            ],

        "current_price":
            metrics[
                "current_price"
            ],

        "real_return_pct":
            metrics[
                "real_return_pct"
            ],

        "missed_move_pct":
            metrics[
                "missed_move_pct"
            ],

        "signal_status":
            metrics[
                "signal_status"
            ],

        "last_updated":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


    try:

        (
            supabase
            .table("trades")
            .insert(row)
            .execute()
        )


        print(
            f"✅ SAVED {ticker}"
        )

        print(
            f"   Politician: "
            f"{politician}"
        )

        print(
            f"   Type: "
            f"{transaction_type}"
        )

        print(
            f"   Transaction: "
            f"{transaction_date}"
        )

        print(
            f"   Disclosure: "
            f"{disclosure_date}"
        )

        print(
            f"   Lag: "
            f"{metrics['lag_days']} days"
        )

        print(
            f"   Trade price: "
            f"${metrics['transaction_price']}"
        )

        print(
            f"   Disclosure price: "
            f"${metrics['disclosure_price']}"
        )

        print(
            f"   Current price: "
            f"${metrics['current_price']}"
        )

        print(
            f"   Follower ROI: "
            f"{metrics['real_return_pct']}%"
        )

        print(
            f"   Missed move: "
            f"{metrics['missed_move_pct']}%"
        )

        print(
            f"   Signal: "
            f"{metrics['signal_status']}"
        )


        return True


    except Exception as exc:

        print(
            f"[DB] ❌ Insert "
            f"failed for "
            f"{politician} "
            f"{ticker}: {exc}"
        )

        return False


# ============================================================
# PROCESS HOUSE PTR FILINGS
# ============================================================

def process_house_ptrs():

    filings = (
        get_recent_ptr_filings()
    )


    saved_count = 0


    for filing in filings:

        print(
            "----------------------------------------"
        )

        print(
            f"[PTR] "
            f"{filing['politician']}"
        )

        print(
            f"[PTR] Filing date: "
            f"{filing['filing_date']}"
        )

        print(
            f"[PTR] "
            f"{filing['filing_url']}"
        )


        try:

            pdf_bytes = download_pdf(
                filing[
                    "filing_url"
                ]
            )


            text = extract_pdf_text(
                pdf_bytes
            )


            if not text:

                print(
                    "[PTR] PDF contains "
                    "no extractable text."
                )

                time.sleep(
                    REQUEST_DELAY
                )

                continue


            transactions = (
                parse_market_transactions(
                    text
                )
            )


            print(
                f"[PTR] Parsed "
                f"{len(transactions)} "
                f"market transactions."
            )


            for trade in transactions:

                if save_trade(
                    filing,
                    trade
                ):

                    saved_count += 1


        except Exception as exc:

            print(
                f"[PTR] Error: {exc}"
            )


        time.sleep(
            REQUEST_DELAY
        )


    return saved_count


# ============================================================
# REFRESH EXISTING FOLLOWER ROI
# ============================================================

def refresh_existing_trades():

    print(
        "[REFRESH] Updating "
        "existing Follower ROI..."
    )


    try:

        response = (
            supabase
            .table("trades")
            .select(
                "id,"
                "ticker,"
                "disclosure_price,"
                "lag_days,"
                "missed_move_pct"
            )
            .execute()
        )


        rows = (
            response.data
            or []
        )


    except Exception as exc:

        print(
            f"[REFRESH] Could not "
            f"load trades: {exc}"
        )

        return 0


    if not rows:

        print(
            "[REFRESH] No existing trades."
        )

        return 0


    updated = 0


    for row in rows:

        ticker = row.get(
            "ticker"
        )


        disclosure_price = (
            safe_float(
                row.get(
                    "disclosure_price"
                )
            )
        )


        current_price = (
            get_current_price(
                ticker
            )
        )


        follower_roi = (
            calculate_percentage(
                disclosure_price,
                current_price
            )
        )


        signal_status = (
            determine_signal_status(
                row.get(
                    "lag_days"
                ),
                safe_float(
                    row.get(
                        "missed_move_pct"
                    )
                )
            )
        )


        payload = {

            "current_price":
                round_number(
                    current_price,
                    4
                ),

            "real_return_pct":
                round_number(
                    follower_roi,
                    2
                ),

            "signal_status":
                signal_status,

            "last_updated":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }


        try:

            (
                supabase
                .table("trades")
                .update(payload)
                .eq(
                    "id",
                    row["id"]
                )
                .execute()
            )


            updated += 1


        except Exception as exc:

            print(
                f"[REFRESH] "
                f"{ticker}: {exc}"
            )


    print(
        f"[REFRESH] Updated "
        f"{updated} trades."
    )


    return updated


# ============================================================
# REAL OHLCV MARKET DATA
# ============================================================

def get_tracked_tickers():

    try:

        response = (
            supabase
            .table("trades")
            .select("ticker")
            .execute()
        )


        rows = (
            response.data
            or []
        )


    except Exception as exc:

        print(
            f"[MARKET] Could not "
            f"load tickers: {exc}"
        )

        return []


    tickers = set()


    for row in rows:

        ticker = (
            row.get(
                "ticker"
            )
        )


        if not ticker:
            continue


        ticker = (
            str(ticker)
            .strip()
            .upper()
        )


        if valid_market_ticker(
            ticker
        ):
            tickers.add(
                ticker
            )


    tickers = sorted(
        tickers
    )


    print(
        f"[MARKET] Found "
        f"{len(tickers)} "
        f"unique tracked tickers."
    )


    return tickers[
        :MAX_MARKET_TICKERS_PER_RUN
    ]


def download_market_candles(
    ticker
):

    yahoo_ticker = (
        normalize_yahoo_ticker(
            ticker
        )
    )


    end_date = (
        datetime.now(
            timezone.utc
        ).date()
        +
        timedelta(days=1)
    )


    start_date = (
        end_date
        -
        timedelta(
            days=MARKET_HISTORY_DAYS
        )
    )


    try:

        stock = yf.Ticker(
            yahoo_ticker
        )


        history = stock.history(
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False
        )


    except Exception as exc:

        print(
            f"[MARKET] "
            f"{ticker}: "
            f"download error: {exc}"
        )

        return []


    if (
        history is None
        or history.empty
    ):

        print(
            f"[MARKET] "
            f"{ticker}: "
            f"no OHLC data."
        )

        return []


    candles = []


    for index, row in (
        history.iterrows()
    ):

        try:

            open_price = (
                safe_float(
                    row.get(
                        "Open"
                    )
                )
            )

            high_price = (
                safe_float(
                    row.get(
                        "High"
                    )
                )
            )

            low_price = (
                safe_float(
                    row.get(
                        "Low"
                    )
                )
            )

            close_price = (
                safe_float(
                    row.get(
                        "Close"
                    )
                )
            )


            volume_value = (
                safe_float(
                    row.get(
                        "Volume"
                    )
                )
            )


            if (
                open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
            ):
                continue


            if (
                open_price <= 0
                or high_price <= 0
                or low_price <= 0
                or close_price <= 0
            ):
                continue


            candle_date = (
                index.date()
                .isoformat()
            )


            volume = (
                int(volume_value)
                if volume_value is not None
                else 0
            )


            candles.append(
                {

                    "ticker":
                        ticker,

                    "candle_date":
                        candle_date,

                    "open":
                        round(
                            open_price,
                            4
                        ),

                    "high":
                        round(
                            high_price,
                            4
                        ),

                    "low":
                        round(
                            low_price,
                            4
                        ),

                    "close":
                        round(
                            close_price,
                            4
                        ),

                    "volume":
                        volume,

                    "last_updated":
                        datetime.now(
                            timezone.utc
                        ).isoformat()
                }
            )


        except Exception as exc:

            print(
                f"[MARKET] "
                f"{ticker}: "
                f"bad candle row: {exc}"
            )


    return candles


def save_market_candles(
    ticker,
    candles
):

    if not candles:
        return 0


    try:

        (
            supabase
            .table(
                "market_candles"
            )
            .upsert(
                candles,
                on_conflict=(
                    "ticker,candle_date"
                )
            )
            .execute()
        )


        print(
            f"[MARKET] ✅ "
            f"{ticker}: "
            f"{len(candles)} "
            f"candles stored."
        )


        return len(
            candles
        )


    except Exception as exc:

        print(
            f"[MARKET] ❌ "
            f"{ticker}: "
            f"{exc}"
        )

        return 0


def refresh_market_history():

    print("")
    print(
        "=========================================="
    )

    print(
        "     CAPITAL-ECHO MARKET DATA ENGINE"
    )

    print(
        "=========================================="
    )


    tickers = (
        get_tracked_tickers()
    )


    if not tickers:

        print(
            "[MARKET] No tickers "
            "available."
        )

        return


    successful = 0

    total_candles = 0


    for index, ticker in enumerate(
        tickers,
        start=1
    ):

        print("")
        print(
            f"[MARKET] "
            f"{index}/"
            f"{len(tickers)} "
            f"{ticker}"
        )


        candles = (
            download_market_candles(
                ticker
            )
        )


        saved = (
            save_market_candles(
                ticker,
                candles
            )
        )


        if saved > 0:

            successful += 1

            total_candles += (
                saved
            )


        time.sleep(
            MARKET_REQUEST_DELAY
        )


    print("")
    print(
        "=========================================="
    )

    print(
        "      MARKET DATA REFRESH COMPLETE"
    )

    print(
        "=========================================="
    )

    print(
        f"Tickers updated: "
        f"{successful}"
    )

    print(
        f"Candles processed: "
        f"{total_candles}"
    )


# ============================================================
# DATABASE TEST
# ============================================================

def test_database():

    print(
        "[DB] Checking Supabase..."
    )


    try:

        (
            supabase
            .table("trades")
            .select(
                "id",
                count="exact"
            )
            .limit(1)
            .execute()
        )


        print(
            "[DB] Supabase connected."
        )


        return True


    except Exception as exc:

        print(
            f"[DB] Connection failed: "
            f"{exc}"
        )


        return False


# ============================================================
# MAIN CAPITAL-ECHO ENGINE
# ============================================================

def main():

    print("")
    print(
        "=========================================="
    )

    print(
        "       CAPITAL-ECHO DATA ENGINE"
    )

    print(
        "=========================================="
    )


    if not test_database():

        raise RuntimeError(
            "Supabase database "
            "connection failed."
        )


    # --------------------------------------------------------
    # 1. SCRAPE NEW HOUSE DISCLOSURES
    # --------------------------------------------------------

    new_trades = (
        process_house_ptrs()
    )


    # --------------------------------------------------------
    # 2. UPDATE CURRENT PRICES + FOLLOWER ROI
    # --------------------------------------------------------

    refresh_existing_trades()


    # --------------------------------------------------------
    # 3. DOWNLOAD REAL OHLCV STOCK HISTORY
    # --------------------------------------------------------

    refresh_market_history()


    print("")
    print(
        "=========================================="
    )

    print(
        "        CAPITAL-ECHO RUN COMPLETE"
    )

    print(
        "=========================================="
    )

    print(
        f"New trades saved: "
        f"{new_trades}"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
