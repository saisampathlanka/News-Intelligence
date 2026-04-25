"""
Stock market data service using yfinance (Yahoo Finance).
Free, no API key required.
Covers global + regional indices and tracks news-correlated movers.
"""
import logging
from datetime import datetime, timezone
from typing import Optional
import json

logger = logging.getLogger("news_intel.stocks")

# ── Regional Index Registry ───────────────────────────────────────────────────
# Ticker : (display_name, region, currency)
REGIONAL_INDICES = {
    "global": [
        ("^GSPC",  "S&P 500",         "global",        "USD"),
        ("^DJI",   "Dow Jones",        "global",        "USD"),
        ("^IXIC",  "NASDAQ",          "global",        "USD"),
        ("^VIX",   "Volatility (VIX)","global",        "USD"),
        ("GC=F",   "Gold Futures",     "global",        "USD"),
        ("CL=F",   "Crude Oil WTI",    "global",        "USD"),
        ("EURUSD=X","EUR/USD",         "global",        "USD"),
    ],
    "west": [
        ("^GSPC",  "S&P 500",         "west",          "USD"),
        ("^DJI",   "Dow Jones",        "west",          "USD"),
        ("^IXIC",  "NASDAQ",          "west",          "USD"),
        ("^FTSE",  "FTSE 100",         "west",          "GBP"),
        ("^GDAXI", "DAX (Germany)",    "west",          "EUR"),
    ],
    "europe": [
        ("^FTSE",  "FTSE 100",         "europe",        "GBP"),
        ("^GDAXI", "DAX (Germany)",    "europe",        "EUR"),
        ("^FCHI",  "CAC 40 (France)",  "europe",        "EUR"),
        ("^STOXX50E","Euro Stoxx 50",  "europe",        "EUR"),
        ("^AEX",   "AEX (Netherlands)","europe",        "EUR"),
        ("EURUSD=X","EUR/USD",         "europe",        "USD"),
    ],
    "middle_east": [
        ("^TASI",  "Saudi Tadawul",    "middle_east",   "SAR"),
        ("^ADI",   "Abu Dhabi Index",  "middle_east",   "AED"),
        ("^DFMGI", "Dubai Index",      "middle_east",   "AED"),
        ("GC=F",   "Gold Futures",     "middle_east",   "USD"),
        ("CL=F",   "Crude Oil WTI",    "middle_east",   "USD"),
        ("BZ=F",   "Brent Crude",      "middle_east",   "USD"),
    ],
    "india": [
        ("^BSESN", "BSE Sensex",       "india",         "INR"),
        ("^NSEI",  "NSE Nifty 50",     "india",         "INR"),
        ("^NSEBANK","Nifty Bank",       "india",         "INR"),
        ("USDINR=X","USD/INR",          "india",         "INR"),
    ],
    "southeast_asia": [
        ("^STI",   "Singapore STI",    "southeast_asia","SGD"),
        ("^JKSE",  "Jakarta IDX",      "southeast_asia","IDR"),
        ("^KLSE",  "Malaysia KLCI",    "southeast_asia","MYR"),
        ("^SET",   "Thailand SET",     "southeast_asia","THB"),
        ("^PSI",   "Philippines PSEi", "southeast_asia","PHP"),
        ("USDSGD=X","USD/SGD",         "southeast_asia","SGD"),
    ],
    "east_asia": [
        ("^N225",  "Nikkei 225",       "east_asia",     "JPY"),
        ("^HSI",   "Hang Seng",        "east_asia",     "HKD"),
        ("000001.SS","Shanghai Comp.", "east_asia",     "CNY"),
        ("^KS11",  "KOSPI (Korea)",    "east_asia",     "KRW"),
        ("^TWII",  "Taiwan TAIEX",     "east_asia",     "TWD"),
        ("USDJPY=X","USD/JPY",         "east_asia",     "JPY"),
    ],
    "africa": [
        ("^JN0U.JO","JSE All Share",   "africa",        "ZAR"),
        ("USDZAR=X","USD/ZAR",         "africa",        "ZAR"),
        ("GC=F",   "Gold Futures",     "africa",        "USD"),
    ],
    "latin_america": [
        ("^BVSP",  "Bovespa (Brazil)", "latin_america", "BRL"),
        ("^MXX",   "IPC (Mexico)",     "latin_america", "MXN"),
        ("USDBRL=X","USD/BRL",         "latin_america", "BRL"),
        ("CL=F",   "Crude Oil WTI",    "latin_america", "USD"),
    ],
}


def _fmt_change(change: float) -> str:
    """Format price change with sign."""
    return f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"


def fetch_indices(region: str = "global") -> list[dict]:
    """
    Fetch stock index data for a region.
    Returns list of index dicts with price, change, direction.
    Falls back to empty list on error (market may be closed).
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — stock data unavailable")
        return []

    indices = REGIONAL_INDICES.get(region, REGIONAL_INDICES["global"])
    results = []

    tickers = [t for t, *_ in indices]
    try:
        # Batch download for efficiency (1 API call)
        data = yf.download(
            tickers,
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=True,
        )

        for ticker, name, reg, currency in indices:
            try:
                if len(tickers) == 1:
                    close_series = data["Close"]
                else:
                    close_series = data["Close"][ticker]

                closes = close_series.dropna()
                if len(closes) < 1:
                    continue

                current = float(closes.iloc[-1])
                previous = float(closes.iloc[-2]) if len(closes) >= 2 else current
                change_pct = ((current - previous) / previous * 100) if previous else 0

                results.append({
                    "ticker": ticker,
                    "name": name,
                    "region": reg,
                    "currency": currency,
                    "price": round(current, 2),
                    "change_pct": round(change_pct, 2),
                    "change_fmt": _fmt_change(change_pct),
                    "direction": "up" if change_pct > 0 else ("down" if change_pct < 0 else "flat"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                logger.debug("Skipping %s: %s", ticker, e)

    except Exception as e:
        logger.error("Stock fetch failed for region %s: %s", region, e)

    logger.info("Fetched %d indices for region=%s", len(results), region)
    return results


def fetch_multi_region(regions: list[str]) -> dict[str, list[dict]]:
    """Fetch indices for multiple regions. Used for global dashboard."""
    return {region: fetch_indices(region) for region in regions}


def get_market_summary(region: str = "global") -> dict:
    """
    Get a concise market summary: overall direction, movers.
    """
    indices = fetch_indices(region)
    if not indices:
        return {"region": region, "status": "unavailable", "indices": []}

    up = sum(1 for i in indices if i["direction"] == "up")
    down = sum(1 for i in indices if i["direction"] == "down")

    # Top movers
    sorted_by_move = sorted(indices, key=lambda x: abs(x["change_pct"]), reverse=True)

    return {
        "region": region,
        "status": "ok",
        "market_mood": "bullish" if up > down else ("bearish" if down > up else "mixed"),
        "up_count": up,
        "down_count": down,
        "top_movers": sorted_by_move[:3],
        "indices": indices,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
