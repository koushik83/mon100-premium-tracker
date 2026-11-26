#!/usr/bin/env python3
"""
MON100 Premium Tracker - Data Fetcher
Fetches 2 years of historical data and calculates premium for MON100.NS ETF

Data Sources:
- MON100.NS market prices: Yahoo Finance (yfinance)
- Official NAV: mfapi.in API (scheme code 114984)
- USDINR forex rates: Yahoo Finance

Premium Calculation:
The AMFI NAV dated D is struck on the US close of D, which happens ~02:30 IST on
D+1 -- after MON100 has finished trading in India on D. So the price on D must
be compared with the latest NAV dated strictly BEFORE D (what the market
actually knew), adjusted for the rupee's move since then:
- Adjusted iNAV = NAV(prev) x (USDINR on D / USDINR on NAV date)
- Premium % = ((Market Price - Adjusted iNAV) / Adjusted iNAV) x 100

Alerts (Telegram, optional): BUY_BELOW / RICH_ABOVE premium zones and stale
data. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID; skipped if unset.
"""

import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any
import os
import sys

BUY_BELOW = float(os.environ.get("BUY_BELOW", "5"))     # premium % at/below = buy window
RICH_ABOVE = float(os.environ.get("RICH_ABOVE", "30"))  # premium % at/above = very rich
STALE_BDAYS = 2  # tolerate one holiday / one missing Yahoo day
STATE_FILE = "alert_state.json"
DASHBOARD = "https://koushik83.github.io/mon100-premium-tracker/"


def _closes(symbol: str, start_date: str, end_date: str) -> pd.Series:
    try:
        df = yf.Ticker(symbol).history(start=start_date, end=end_date, auto_adjust=True)
    except Exception as e:
        print(f"  {symbol}: fetch failed ({e})")
        return pd.Series(dtype=float)
    if df.empty:
        return pd.Series(dtype=float)
    s = df['Close'].copy()
    s.index = s.index.tz_localize(None).normalize()
    return s[~s.index.duplicated(keep='last')]


def fetch_mon100_prices(start_date: str, end_date: str) -> pd.DataFrame:
    """MON100 daily closes: NSE from Yahoo, with BSE filling any days NSE is missing."""
    print(f"Fetching MON100 prices from {start_date} to {end_date}...")
    nse = _closes("MON100.NS", start_date, end_date)
    bse = _closes("MON100.BO", start_date, end_date)
    if nse.empty and bse.empty:
        raise ValueError("No price data returned for MON100.NS or MON100.BO")
    filled = bse[~bse.index.isin(nse.index)]
    if len(filled):
        print(f"  BSE filled {len(filled)} day(s) missing on NSE: "
              f"{', '.join(d.strftime('%Y-%m-%d') for d in filled.index[-5:])}")
    df = pd.concat([nse, filled]).sort_index().to_frame('price')
    # Drop an in-progress session: its "close" is just the latest intraday tick.
    df = df[df.index <= last_closed_session()]
    print(f"  Retrieved {len(df)} price records")
    return df


def fetch_nav_data(scheme_code: int = 114984) -> pd.DataFrame:
    """Fetch NAV data from mfapi.in API."""
    url = f"https://api.mfapi.in/mf/{scheme_code}"
    print(f"Fetching NAV data from {url}...")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        if 'data' not in data:
            raise ValueError("Invalid API response - 'data' field missing")

        nav_records = []
        for record in data['data']:
            try:
                date = datetime.strptime(record['date'], '%d-%m-%Y')
                nav = float(record['nav'])
                nav_records.append({'date': date, 'nav': nav})
            except (ValueError, KeyError):
                continue

        df = pd.DataFrame(nav_records)
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)

        print(f"  Retrieved {len(df)} NAV records")
        return df

    except requests.RequestException as e:
        print(f"Error fetching NAV data: {e}")
        raise


def fetch_usdinr_rates(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch USDINR forex rates from Yahoo Finance."""
    print(f"Fetching USDINR rates from {start_date} to {end_date}...")

    try:
        ticker = yf.Ticker("USDINR=X")
        df = ticker.history(start=start_date, end=end_date, auto_adjust=True)

        if df.empty:
            raise ValueError("No forex data returned for USDINR=X")

        df = df[['Close']].copy()
        df.index = df.index.tz_localize(None).normalize()
        df.columns = ['usdinr']

        print(f"  Retrieved {len(df)} forex records")
        return df

    except Exception as e:
        print(f"Error fetching USDINR rates: {e}")
        raise


def calculate_premium(
    prices_df: pd.DataFrame,
    nav_df: pd.DataFrame,
    forex_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Calculate premium for each trading day.

    The NAV for international funds like MON100 is typically:
    - Published after Indian market close
    - Based on previous US trading day's prices
    - Converted to INR using that day's forex rate

    For accurate premium calculation, we need to adjust NAV for
    forex movement between NAV date and current trading date.
    """
    print("Calculating premium...")

    # Create complete date range and forward-fill forex rates
    all_dates = pd.date_range(
        start=min(prices_df.index.min(), nav_df.index.min(), forex_df.index.min()),
        end=max(prices_df.index.max(), nav_df.index.max(), forex_df.index.max())
    )
    forex_complete = forex_df.reindex(all_dates, method='ffill')

    # Build result dataframe
    results = []

    for price_date in prices_df.index:
        price = prices_df.loc[price_date, 'price']

        # Latest NAV dated strictly before this trading day: NAV(D) uses the
        # US close of D, which happens after Indian trading on D is over.
        nav_dates_before = nav_df.index[nav_df.index < price_date]
        if len(nav_dates_before) == 0:
            continue

        nav_date = nav_dates_before[-1]
        nav = nav_df.loc[nav_date, 'nav']

        # Get forex rates
        if price_date not in forex_complete.index or nav_date not in forex_complete.index:
            continue

        usdinr_today = forex_complete.loc[price_date, 'usdinr']
        usdinr_nav_day = forex_complete.loc[nav_date, 'usdinr']

        if pd.isna(usdinr_today) or pd.isna(usdinr_nav_day) or usdinr_nav_day == 0:
            continue

        # Calculate adjusted iNAV
        # If forex went up since NAV date, the underlying is worth more in INR
        forex_adjustment = usdinr_today / usdinr_nav_day
        adjusted_inav = nav * forex_adjustment

        # Calculate premium
        premium = ((price - adjusted_inav) / adjusted_inav) * 100

        results.append({
            'date': price_date,
            'price': price,
            'nav': nav,
            'nav_date': nav_date,
            'usdinr': usdinr_today,
            'usdinr_nav_day': usdinr_nav_day,
            'forex_adj': forex_adjustment,
            'adjusted_inav': adjusted_inav,
            'premium': premium
        })

    result_df = pd.DataFrame(results)
    result_df.set_index('date', inplace=True)

    print(f"  Calculated premium for {len(result_df)} trading days")

    # Debug: show some forex adjustments
    if len(result_df) > 0:
        print("\n  Sample forex adjustments (last 5 days):")
        for i in range(-min(5, len(result_df)), 0):
            row = result_df.iloc[i]
            print(f"    {result_df.index[i].strftime('%Y-%m-%d')}: "
                  f"NAV date={row['nav_date'].strftime('%Y-%m-%d')}, "
                  f"FX adj={row['forex_adj']:.4f}, "
                  f"Premium={row['premium']:.2f}%")

    return result_df


def calculate_statistics(premiums: pd.Series) -> Dict[str, float]:
    """Calculate statistical measures for the premium series."""
    return {
        'min': round(premiums.min(), 2),
        'max': round(premiums.max(), 2),
        'average': round(premiums.mean(), 2),
        'median': round(premiums.median(), 2),
        'p25': round(premiums.quantile(0.25), 2),
        'p75': round(premiums.quantile(0.75), 2),
        'std': round(premiums.std(), 2),
        'current': round(premiums.iloc[-1], 2) if len(premiums) > 0 else None
    }


def save_to_json(df: pd.DataFrame, stats: Dict[str, float], output_path: str) -> None:
    """Save the data to a JSON file for the web frontend."""
    print(f"\nSaving data to {output_path}...")

    output = {
        'dates': [d.strftime('%Y-%m-%d') for d in df.index],
        'premiums': [round(p, 2) for p in df['premium'].tolist()],
        'prices': [round(p, 2) for p in df['price'].tolist()],
        'navs': [round(n, 2) for n in df['nav'].tolist()],
        'adjusted_inavs': [round(i, 2) for i in df['adjusted_inav'].tolist()],
        'usdinr': [round(u, 4) for u in df['usdinr'].tolist()],
        'nav_dates': [d.strftime('%Y-%m-%d') for d in df['nav_date']],
        'stats': stats,
        'latest_date': df.index[-1].strftime('%Y-%m-%d'),
        'stale': is_stale(df.index[-1]),
        'last_updated': datetime.now().isoformat(),
        'data_points': len(df)
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)

    print(f"  Saved {len(df)} data points")


def now_ist() -> pd.Timestamp:
    return pd.Timestamp.utcnow().tz_localize(None) + pd.Timedelta(hours=5, minutes=30)


def last_closed_session() -> pd.Timestamp:
    """Most recent weekday whose NSE session has closed (15:30 IST + buffer)."""
    n = now_ist()
    day = n.normalize()
    if n < day + pd.Timedelta(hours=15, minutes=45):
        day -= pd.Timedelta(days=1)
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return day


def is_stale(latest: pd.Timestamp) -> bool:
    """True if more than STALE_BDAYS weekday sessions after `latest` are missing.
    Holidays count as missing, hence the tolerance."""
    missing = np.busday_count((latest + pd.Timedelta(days=1)).date(),
                              (last_closed_session() + pd.Timedelta(days=1)).date())
    return int(missing) > STALE_BDAYS


def send_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("---- Telegram (not sent, secrets unset) ----\n" + text)
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": "true"})
        print("Telegram sent." if r.ok else f"Telegram error {r.status_code}")
    except requests.RequestException as e:  # never echo the URL: it contains the token
        print(f"Telegram send failed: {type(e).__name__}")


def zone_of(premium: float) -> str:
    if premium <= BUY_BELOW:
        return "buy"
    if premium >= RICH_ABOVE:
        return "rich"
    return "normal"


def notify(df: pd.DataFrame, stats: Dict[str, float]) -> None:
    """Alert on premium-zone changes and on stale data. State lives in alert_state.json."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}

    last = df.iloc[-1]
    date = df.index[-1].strftime('%d %b %Y')
    prem = float(last['premium'])
    zone = zone_of(prem)
    detail = (f"Price ₹{last['price']:.2f} vs iNAV ₹{last['adjusted_inav']:.2f} "
              f"(NAV of {last['nav_date']:%d %b})\n"
              f"2y median {stats['median']:.1f}% · p25 {stats['p25']:.1f}% · p75 {stats['p75']:.1f}%\n"
              f"{DASHBOARD}")

    prev_zone = state.get("zone")
    if prev_zone is None:
        print(f"Alert state initialised: zone={zone} ({prem:.2f}%)")
    elif zone != prev_zone:
        head = {"buy": f"📉 <b>MON100 premium in BUY zone: {prem:.1f}%</b> (≤ {BUY_BELOW:g}%)",
                "rich": f"📈 <b>MON100 premium very RICH: {prem:.1f}%</b> (≥ {RICH_ABOVE:g}%)",
                "normal": f"↔️ <b>MON100 premium back to normal: {prem:.1f}%</b>"}[zone]
        send_telegram(f"{head}\n{date}\n\n{detail}")
    state["zone"] = zone

    latest = df.index[-1].strftime('%Y-%m-%d')
    if is_stale(df.index[-1]):
        if state.get("stale_alerted_for") != latest:
            send_telegram(f"⚠️ <b>MON100 tracker data is stale</b>\nLatest price is from {date}. "
                          f"Yahoo may be missing days.\n"
                          f"https://github.com/koushik83/mon100-premium-tracker/actions")
            state["stale_alerted_for"] = latest
    else:
        state.pop("stale_alerted_for", None)

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def main():
    """Main function to fetch data and generate JSON output."""

    print("=" * 60)
    print("MON100 Premium Tracker - Data Fetcher")
    print("=" * 60)
    print()

    # Calculate date range (2 years from today)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=730)

    start_str = start_date.strftime('%Y-%m-%d')
    # yfinance's `end` is exclusive: +1 day so today's close is included. (The
    # Actions run lands ~20:00 UTC, so `end=today` silently dropped that day.)
    end_str = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"Date Range: {start_str} to {end_str}")
    print()

    try:
        # Fetch all data sources
        prices_df = fetch_mon100_prices(start_str, end_str)
        nav_df = fetch_nav_data()
        forex_df = fetch_usdinr_rates(start_str, end_str)

        # Filter NAV data to our date range
        nav_df = nav_df[(nav_df.index >= start_date) & (nav_df.index <= end_date)]
        print(f"  Filtered NAV to {len(nav_df)} records in date range")
        print()

        # Calculate premium
        result_df = calculate_premium(prices_df, nav_df, forex_df)

        # Calculate statistics
        stats = calculate_statistics(result_df['premium'])

        # Print summary
        print()
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Total data points: {len(result_df)}")
        print(f"Date range: {result_df.index.min().strftime('%Y-%m-%d')} to {result_df.index.max().strftime('%Y-%m-%d')}")
        print()
        print("Premium Statistics:")
        print(f"  Current:  {stats['current']:.2f}%")
        print(f"  Minimum:  {stats['min']:.2f}%")
        print(f"  Maximum:  {stats['max']:.2f}%")
        print(f"  Average:  {stats['average']:.2f}%")
        print(f"  Median:   {stats['median']:.2f}%")
        print(f"  25th %:   {stats['p25']:.2f}%")
        print(f"  75th %:   {stats['p75']:.2f}%")
        print(f"  Std Dev:  {stats['std']:.2f}%")

        # Save to JSON
        save_to_json(result_df, stats, 'premium_data.json')
        notify(result_df, stats)

        print()
        print("Data fetch complete!")
        print("=" * 60)

        return 0

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
