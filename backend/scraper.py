import os
from datetime import datetime, timedelta, timezone

import yfinance as yf
from supabase import create_client, Client


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing")

if not SUPABASE_SERVICE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_KEY is missing")


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY
)


def get_market_price_on_date(ticker, target_date):
    """
    Gets the closing stock price on or immediately after target_date.
    This helps handle weekends and market holidays.
    """

    stock = yf.Ticker(ticker)

    start_date = target_date
    end_date = target_date + timedelta(days=7)

    history = stock.history(
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
        auto_adjust=False
    )

    if history.empty:
        return None

    return float(history.iloc[0]["Close"])


def get_current_price(ticker):
    """
    Gets the latest available market price.
    """

    stock = yf.Ticker(ticker)

    history = stock.history(
        period="5d",
        interval="1d",
        auto_adjust=False
    )

    if history.empty:
        return None

    return float(history.iloc[-1]["Close"])


def calculate_signal_status(lag_days, missed_move_pct):
    """
    Classifies how useful the disclosure is to a retail investor.
    """

    if missed_move_pct is not None and missed_move_pct >= 20:
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
    """
    Runs the Capital-Echo Lag Engine.
    """

    transaction_date = datetime.strptime(
        transaction_date,
        "%Y-%m-%d"
    )

    disclosure_date = datetime.strptime(
        disclosure_date,
        "%Y-%m-%d"
    )

    lag_days = (
        disclosure_date - transaction_date
    ).days

    if lag_days < 0:
        raise ValueError(
            "Disclosure date cannot be before transaction date"
        )

    transaction_price = get_market_price_on_date(
        ticker,
        transaction_date
    )

    disclosure_price = get_market_price_on_date(
        ticker,
        disclosure_date
    )

    current_price = get_current_price(ticker)

    real_return_pct = None
    missed_move_pct = None

    if (
        disclosure_price is not None
        and current_price is not None
        and disclosure_price != 0
    ):
        real_return_pct = (
            (current_price - disclosure_price)
            / disclosure_price
        ) * 100

    if (
        transaction_price is not None
        and disclosure_price is not None
        and transaction_price != 0
    ):
        missed_move_pct = (
            (disclosure_price - transaction_price)
            / transaction_price
        ) * 100

    signal_status = calculate_signal_status(
        lag_days,
        missed_move_pct
    )

    return {
        "lag_days": lag_days,
        "transaction_price": transaction_price,
        "disclosure_price": disclosure_price,
        "current_price": current_price,

        "real_return_pct": (
            round(real_return_pct, 2)
            if real_return_pct is not None
            else None
        ),

        "missed_move_pct": (
            round(missed_move_pct, 2)
            if missed_move_pct is not None
            else None
        ),

        "signal_status": signal_status,

        "last_updated": datetime.now(
            timezone.utc
        ).isoformat()
    }


def process_trade(trade):
    """
    Takes one congressional trade,
    runs the Lag Engine,
    then saves it to Supabase.
    """

    ticker = trade.get("ticker")

    transaction_date = trade.get(
        "transaction_date"
    )

    disclosure_date = trade.get(
        "disclosure_date"
    )

    if not ticker:
        print("Skipping trade: missing ticker")
        return

    if not transaction_date:
        print(
            f"Skipping {ticker}: missing transaction date"
        )
        return

    if not disclosure_date:
        print(
            f"Skipping {ticker}: missing disclosure date"
        )
        return

    print(
        f"Running Lag Engine for {ticker}..."
    )

    try:

        metrics = calculate_lag_engine(
            ticker=ticker,
            transaction_date=transaction_date,
            disclosure_date=disclosure_date
        )

        record = {
            **trade,
            **metrics
        }

        result = (
            supabase
            .table("trades")
            .insert(record)
            .execute()
        )

        print(
            f"Saved {ticker} successfully"
        )

        print(
            f"Lag: {metrics['lag_days']} days"
        )

        print(
            f"Transaction Price: "
            f"{metrics['transaction_price']}"
        )

        print(
            f"Disclosure Price: "
            f"{metrics['disclosure_price']}"
        )

        print(
            f"Current Price: "
            f"{metrics['current_price']}"
        )

        print(
            f"Follower Return: "
            f"{metrics['real_return_pct']}%"
        )

        print(
            f"Missed Move: "
            f"{metrics['missed_move_pct']}%"
        )

        print(
            f"Signal: "
            f"{metrics['signal_status']}"
        )

        return result

    except Exception as error:

        print(
            f"Failed processing {ticker}: {error}"
        )


def main():

    print("")
    print("==============================")
    print(" CAPITAL-ECHO DATA ENGINE")
    print("==============================")
    print("")

    print("Connecting to Supabase...")

    supabase.table(
        "trades"
    ).select(
        "id"
    ).limit(
        1
    ).execute()

    print("Supabase connected.")
    print("")

    #
    # NEXT STEP:
    # congressional disclosure scraper
    # will feed real trades into this function.
    #

    print(
        "Lag Engine ready."
    )

    print(
        "Waiting for congressional disclosure feed."
    )


if __name__ == "__main__":
    main()
