import io
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone

import pdfplumber
import requests
import yfinance as yf

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from supabase import create_client


# ============================================================
# CONFIG
# ============================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_KEY is missing")


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)


HOUSE_BASE = "https://disclosures-clerk.house.gov"

CURRENT_YEAR = datetime.now().year

# Number of newest PTR filings inspected each run.
MAX_PTRS_PER_RUN = 40

# Delay between House PDF requests.
REQUEST_DELAY = 0.6


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

retry_strategy = Retry(
    total=4,
    backoff_factor=1,
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

session.headers.update({
    "User-Agent":
        "Capital-Echo/1.0 congressional-disclosure-research"
})


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip()


def parse_date(value):
    if not value:
        return None

    value = value.strip()

    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value,
                fmt
            )
        except ValueError:
            pass

    return None


def iso_date(value):
    dt = parse_date(value)

    if not dt:
        return None

    return dt.strftime(
        "%Y-%m-%d"
    )


def round_number(value):
    if value is None:
        return None

    return round(
        float(value),
        4
    )


# ============================================================
# MARKET DATA CACHE
# ============================================================

historical_price_cache = {}
current_price_cache = {}


# ============================================================
# MARKET DATA
# ============================================================

def get_market_price_on_date(
    ticker,
    date_value
):
    """
    Returns the first available market close on or after
    the requested calendar date.

    This automatically handles weekends and market holidays.
    """

    if isinstance(
        date_value,
        str
    ):
        target_date = datetime.strptime(
            date_value,
            "%Y-%m-%d"
        )
    else:
        target_date = date_value

    cache_key = (
        ticker.upper(),
        target_date.strftime("%Y-%m-%d")
    )

    if cache_key in historical_price_cache:
        return historical_price_cache[
            cache_key
        ]

    try:

        stock = yf.Ticker(
            ticker
        )

        end_date = (
            target_date
            + timedelta(days=8)
        )

        history = stock.history(
            start=target_date.strftime(
                "%Y-%m-%d"
            ),
            end=end_date.strftime(
                "%Y-%m-%d"
            ),
            interval="1d",
            auto_adjust=False
        )

        if history.empty:

            historical_price_cache[
                cache_key
            ] = None

            return None

        price = round_number(
            history.iloc[0]["Close"]
        )

        historical_price_cache[
            cache_key
        ] = price

        return price

    except Exception as error:

        print(
            f"[MARKET] Historical price "
            f"failed for {ticker}: {error}"
        )

        historical_price_cache[
            cache_key
        ] = None

        return None


def get_current_price(ticker):
    """
    Attempts intraday pricing first.

    Falls back to the latest daily close.
    """

    ticker = ticker.upper()

    if ticker in current_price_cache:
        return current_price_cache[
            ticker
        ]

    try:

        stock = yf.Ticker(
            ticker
        )

        intraday = stock.history(
            period="1d",
            interval="1m",
            auto_adjust=False,
            prepost=False
        )

        if not intraday.empty:

            price = round_number(
                intraday.iloc[-1]["Close"]
            )

            current_price_cache[
                ticker
            ] = price

            return price

        daily = stock.history(
            period="5d",
            interval="1d",
            auto_adjust=False
        )

        if not daily.empty:

            price = round_number(
                daily.iloc[-1]["Close"]
            )

            current_price_cache[
                ticker
            ] = price

            return price

    except Exception as error:

        print(
            f"[MARKET] Current price "
            f"failed for {ticker}: {error}"
        )

    current_price_cache[
        ticker
    ] = None

    return None


# ============================================================
# LAG ENGINE
# ============================================================

def calculate_signal_status(
    lag_days,
    missed_move_pct
):

    if (
        missed_move_pct is not None
        and missed_move_pct >= 20
    ):
        return "Priced In"

    if (
        missed_move_pct is not None
        and missed_move_pct <= -20
    ):
        return "Price Fell During Lag"

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
        disclosure_dt
        - transaction_dt
    ).days

    if lag_days < 0:
        raise ValueError(
            "Disclosure date is before transaction date"
        )

    transaction_price = (
        get_market_price_on_date(
            ticker,
            transaction_dt
        )
    )

    disclosure_price = (
        get_market_price_on_date(
            ticker,
            disclosure_dt
        )
    )

    current_price = (
        get_current_price(
            ticker
        )
    )

    real_return_pct = None
    missed_move_pct = None

    if (
        disclosure_price is not None
        and disclosure_price != 0
        and current_price is not None
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
        and transaction_price != 0
        and disclosure_price is not None
    ):

        missed_move_pct = (
            (
                disclosure_price
                - transaction_price
            )
            / transaction_price
        ) * 100

    return {

        "lag_days":
            lag_days,

        "transaction_price":
            transaction_price,

        "disclosure_price":
            disclosure_price,

        "current_price":
            current_price,

        "real_return_pct":
            round(
                real_return_pct,
                2
            )
            if real_return_pct is not None
            else None,

        "missed_move_pct":
            round(
                missed_move_pct,
                2
            )
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


# ============================================================
# HOUSE YEARLY INDEX
# ============================================================

def strip_namespace(tag):

    if "}" in tag:
        return tag.split(
            "}",
            1
        )[1]

    return tag


def direct_child_map(node):

    result = {}

    for child in list(node):

        tag = strip_namespace(
            child.tag
        )

        result[
            tag.lower()
        ] = clean_text(
            child.text
        )

    return result


def download_house_index(year):

    url = (
        f"{HOUSE_BASE}/public_disc/"
        f"financial-pdfs/{year}FD.zip"
    )

    print(
        f"[HOUSE] Downloading {year} filing index..."
    )

    response = session.get(
        url,
        timeout=45
    )

    response.raise_for_status()

    return response.content


def parse_house_index(
    zip_bytes,
    year
):

    with zipfile.ZipFile(
        io.BytesIO(zip_bytes)
    ) as archive:

        files = archive.namelist()

        xml_files = [
            name
            for name in files
            if name.lower().endswith(
                ".xml"
            )
        ]

        if not xml_files:
            raise RuntimeError(
                f"No XML file found inside "
                f"{year}FD.zip"
            )

        xml_name = xml_files[0]

        xml_data = archive.read(
            xml_name
        )

    root = ET.fromstring(
        xml_data
    )

    filings = []

    for node in root.iter():

        fields = direct_child_map(
            node
        )

        if (
            "docid" not in fields
            or "filingtype" not in fields
        ):
            continue

        filing_type = (
            fields.get(
                "filingtype",
                ""
            )
            .strip()
            .upper()
        )

        if filing_type != "P":
            continue

        doc_id = fields.get(
            "docid"
        )

        filing_date = (
            fields.get(
                "filingdate"
            )
            or ""
        )

        filing_dt = parse_date(
            filing_date
        )

        first = fields.get(
            "first",
            ""
        )

        last = fields.get(
            "last",
            ""
        )

        suffix = fields.get(
            "suffix",
            ""
        )

        prefix = fields.get(
            "prefix",
            ""
        )

        name = clean_text(
            " ".join(
                part
                for part in [
                    prefix,
                    first,
                    last,
                    suffix
                ]
                if part
            )
        )

        filings.append({

            "year":
                year,

            "doc_id":
                doc_id,

            "politician":
                name,

            "filing_date":
                filing_date,

            "filing_dt":
                filing_dt,

            "state_district":
                (
                    fields.get(
                        "statedst"
                    )
                    or fields.get(
                        "statedistrict"
                    )
                    or ""
                )
        })

    filings.sort(
        key=lambda row:
            row["filing_dt"]
            or datetime.min,
        reverse=True
    )

    return filings


def get_ptr_filings(year):

    zip_bytes = download_house_index(
        year
    )

    filings = parse_house_index(
        zip_bytes,
        year
    )

    print(
        f"[HOUSE] Found "
        f"{len(filings)} PTR filings "
        f"in {year}."
    )

    return filings


# ============================================================
# PTR PDF
# ============================================================

def build_ptr_url(
    year,
    doc_id
):

    return (
        f"{HOUSE_BASE}/public_disc/"
        f"ptr-pdfs/{year}/{doc_id}.pdf"
    )


def download_ptr_pdf(url):

    response = session.get(
        url,
        timeout=60
    )

    response.raise_for_status()

    return response.content


def extract_pdf_text(
    pdf_bytes
):

    pages = []

    with pdfplumber.open(
        io.BytesIO(pdf_bytes)
    ) as pdf:

        for page in pdf.pages:

            text = (
                page.extract_text(
                    x_tolerance=2,
                    y_tolerance=3
                )
                or ""
            )

            pages.append(
                text
            )

    return "\n".join(
        pages
    )


# ============================================================
# PTR TEXT PARSER
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
    r"\d{1,2}/\d{1,2}/\d{4}"
    r")"
    r"(?:\s+"
    r"(?P<notification>"
    r"\d{1,2}/\d{1,2}/\d{4}"
    r"))?"
    r"\s+"
    r"(?P<amount>"
    r"\$[\d,]+"
    r"\s*-\s*"
    r"\$[\d,]+"
    r"|Over\s+\$[\d,]+"
    r")",
    re.IGNORECASE
)


OWNER_ASSET = re.compile(
    r"(?:^|\n)"
    r"(?P<owner>SP|JT|DC)"
    r"\s+"
    r"(?P<asset>[^\n]{1,250})$",
    re.MULTILINE
)


def extract_pdf_member_name(
    text,
    fallback
):

    match = re.search(
        r"Name:\s*"
        r"([^\n]+)",
        text,
        re.IGNORECASE
    )

    if match:

        name = clean_text(
            match.group(1)
        )

        name = re.sub(
            r"\s+Status:.*$",
            "",
            name,
            flags=re.IGNORECASE
        )

        return name

    return fallback


def transaction_type_name(code):

    value = (
        code.upper()
        .strip()
    )

    if value == "P":
        return "Purchase"

    if value.startswith("S"):
        return "Sale"

    if value == "E":
        return "Exchange"

    return value


def find_asset_and_owner(
    before_text
):

    window = before_text[
        -400:
    ]

    matches = list(
        OWNER_ASSET.finditer(
            window
        )
    )

    if matches:

        latest = matches[-1]

        owner = latest.group(
            "owner"
        )

        asset = clean_text(
            latest.group(
                "asset"
            )
        )

        return owner, asset

    lines = [
        clean_text(line)
        for line
        in window.splitlines()
        if clean_text(line)
    ]

    if lines:

        asset = lines[-1]

        asset = re.sub(
            r"^(SP|JT|DC)\s+",
            "",
            asset
        )

        return None, asset

    return None, ""


def parse_ptr_transactions(
    text,
    filing
):

    markers = list(
        TICKER_MARKER.finditer(
            text
        )
    )

    if not markers:

        return []

    politician = extract_pdf_member_name(
        text,
        filing["politician"]
    )

    disclosure_date = iso_date(
        filing["filing_date"]
    )

    if not disclosure_date:

        print(
            f"[PTR] Missing filing date "
            f"for {filing['doc_id']}"
        )

        return []

    trades = []

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

        # Only market securities we can reasonably
        # price through ticker-based market data.
        allowed_asset_types = {
            "ST",
            "EF"
        }

        if asset_type not in allowed_asset_types:
            continue

        segment_end = (
            markers[index + 1].start()
            if index + 1 < len(markers)
            else min(
                len(text),
                marker.end() + 700
            )
        )

        after = text[
            marker.end():
            segment_end
        ]

        transaction_match = (
            TRANSACTION_INFO.search(
                after
            )
        )

        if not transaction_match:
            continue

        transaction_date = iso_date(
            transaction_match.group(
                "date"
            )
        )

        if not transaction_date:
            continue

        tx_type = transaction_type_name(
            transaction_match.group(
                "type"
            )
        )

        amount = clean_text(
            transaction_match.group(
                "amount"
            )
        )

        before = text[
            max(
                0,
                marker.start() - 400
            ):
            marker.start()
        ]

        owner, asset_name = (
            find_asset_and_owner(
                before
            )
        )

        if not asset_name:

            asset_name = ticker

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
                tx_type,

            "transaction_date":
                transaction_date,

            "disclosure_date":
                disclosure_date,

            "amount":
                amount,

            "filing_url":
                build_ptr_url(
                    filing["year"],
                    filing["doc_id"]
                )
        }

        trades.append(
            trade
        )

    return trades


# ============================================================
# SUPABASE
# ============================================================

def find_existing_trade(
    trade
):

    query = (
        supabase
        .table("trades")
        .select("id")
        .eq(
            "ticker",
            trade["ticker"]
        )
        .eq(
            "transaction_date",
            trade["transaction_date"]
        )
        .eq(
            "transaction_type",
            trade["transaction_type"]
        )
        .eq(
            "filing_url",
            trade["filing_url"]
        )
    )

    if trade.get(
        "politician"
    ):

        query = query.eq(
            "politician",
            trade["politician"]
        )

    result = (
        query
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]["id"]

    return None


def insert_trade(
    trade
):

    existing_id = (
        find_existing_trade(
            trade
        )
    )

    if existing_id:

        print(
            f"[DB] Already exists: "
            f"{trade['politician']} "
            f"{trade['ticker']} "
            f"{trade['transaction_date']}"
        )

        return False

    try:

        metrics = calculate_lag_engine(

            trade["ticker"],

            trade[
                "transaction_date"
            ],

            trade[
                "disclosure_date"
            ]
        )

    except Exception as error:

        print(
            f"[LAG] Failed "
            f"{trade['ticker']}: "
            f"{error}"
        )

        return False

    record = {
        **trade,
        **metrics
    }

    supabase.table(
        "trades"
    ).insert(
        record
    ).execute()

    print("")
    print(
        f"✅ SAVED "
        f"{trade['ticker']}"
    )

    print(
        f"   Politician: "
        f"{trade['politician']}"
    )

    print(
        f"   Type: "
        f"{trade['transaction_type']}"
    )

    print(
        f"   Transaction: "
        f"{trade['transaction_date']}"
    )

    print(
        f"   Disclosure: "
        f"{trade['disclosure_date']}"
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


# ============================================================
# PROCESS PTR
# ============================================================

def process_ptr(
    filing
):

    url = build_ptr_url(
        filing["year"],
        filing["doc_id"]
    )

    print("")
    print(
        "----------------------------------------"
    )

    print(
        f"[PTR] {filing['politician']}"
    )

    print(
        f"[PTR] Filing date: "
        f"{filing['filing_date']}"
    )

    print(
        f"[PTR] {url}"
    )

    try:

        pdf_bytes = (
            download_ptr_pdf(
                url
            )
        )

        text = extract_pdf_text(
            pdf_bytes
        )

        if not text.strip():

            print(
                "[PTR] PDF contains no "
                "extractable text."
            )

            return 0

        trades = (
            parse_ptr_transactions(
                text,
                filing
            )
        )

        print(
            f"[PTR] Parsed "
            f"{len(trades)} "
            f"market transactions."
        )

        saved = 0

        for trade in trades:

            if insert_trade(
                trade
            ):
                saved += 1

        return saved

    except Exception as error:

        print(
            f"[PTR] Failed: {error}"
        )

        return 0


# ============================================================
# REFRESH EXISTING FOLLOWER ROI
# ============================================================

def refresh_existing_trades():

    print("")
    print(
        "[REFRESH] Updating existing "
        "Follower ROI..."
    )

    response = (
        supabase
        .table("trades")
        .select(
            "id,"
            "ticker,"
            "disclosure_price"
        )
        .execute()
    )

    rows = response.data or []

    if not rows:

        print(
            "[REFRESH] No existing trades."
        )

        return

    updated = 0

    for row in rows:

        ticker = row.get(
            "ticker"
        )

        disclosure_price = row.get(
            "disclosure_price"
        )

        if (
            not ticker
            or disclosure_price is None
        ):
            continue

        current_price = (
            get_current_price(
                ticker
            )
        )

        if current_price is None:
            continue

        disclosure_price = float(
            disclosure_price
        )

        if disclosure_price == 0:
            continue

        roi = (
            (
                current_price
                - disclosure_price
            )
            / disclosure_price
        ) * 100

        (
            supabase
            .table("trades")
            .update({

                "current_price":
                    current_price,

                "real_return_pct":
                    round(
                        roi,
                        2
                    ),

                "last_updated":
                    datetime.now(
                        timezone.utc
                    ).isoformat()
            })
            .eq(
                "id",
                row["id"]
            )
            .execute()
        )

        updated += 1

    print(
        f"[REFRESH] Updated "
        f"{updated} trades."
    )


# ============================================================
# MAIN
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

    print("")
    print(
        "[DB] Checking Supabase..."
    )

    (
        supabase
        .table("trades")
        .select("id")
        .limit(1)
        .execute()
    )

    print(
        "[DB] Supabase connected."
    )

    all_filings = []

    # Current year is normally enough,
    # but checking previous year protects
    # against early-January edge cases.
    years = [
        CURRENT_YEAR,
        CURRENT_YEAR - 1
    ]

    for year in years:

        try:

            filings = (
                get_ptr_filings(
                    year
                )
            )

            all_filings.extend(
                filings
            )

        except Exception as error:

            print(
                f"[HOUSE] Failed index "
                f"{year}: {error}"
            )

    all_filings.sort(
        key=lambda row:
            row["filing_dt"]
            or datetime.min,
        reverse=True
    )

    newest = all_filings[
        :MAX_PTRS_PER_RUN
    ]

    print("")
    print(
        f"[HOUSE] Processing "
        f"{len(newest)} newest PTRs."
    )

    total_saved = 0

    for filing in newest:

        total_saved += (
            process_ptr(
                filing
            )
        )

        time.sleep(
            REQUEST_DELAY
        )

    # Clear current-price cache so the
    # refresh gets the latest quote set.
    current_price_cache.clear()

    refresh_existing_trades()

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
        f"{total_saved}"
    )


if __name__ == "__main__":
    main()
