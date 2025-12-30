#!/usr/bin/env python3
"""
MON100 Premium Tracker - Data Fetcher
Fetches 2 years of historical data and calculates premium for MON100.NS ETF

Data Sources:
- MON100.NS market prices: Yahoo Finance (yfinance)
- Official NAV: mfapi.in API (scheme code 114984)
- USDINR forex rates: Yahoo Finance

Premium Calculation:
For international ETFs, NAV is typically calculated using previous day's US market close.
So we adjust the NAV for forex movement:
- Adjusted iNAV = NAV × (Current day USDINR / Previous day USDINR)
- Premium % = ((Market Price - Adjusted iNAV) / Adjusted iNAV) × 100
"""

import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any
import sys


def fetch_mon100_prices(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch MON100.NS daily closing prices from Yahoo Finance."""
    print(f"Fetching MON100.NS prices from {start_date} to {end_date}...")

    try:
        ticker = yf.Ticker("MON100.NS")
        df = ticker.history(start=start_date, end=end_date, auto_adjust=True)

        if df.empty:
            raise ValueError("No price data returned for MON100.NS")

        df = df[['Close']].copy()
        df.index = df.index.tz_localize(None).normalize()
        df.columns = ['price']

        print(f"  Retrieved {len(df)} price records")
        return df

    except Exception as e:
        print(f"Error fetching MON100.NS prices: {e}")
        raise


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

        # Find the most recent NAV on or before this date
        nav_dates_before = nav_df.index[nav_df.index <= price_date]
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
        'stats': stats,
        'last_updated': datetime.now().isoformat(),
        'data_points': len(df)
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)

    print(f"  Saved {len(df)} data points")


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
    end_str = end_date.strftime('%Y-%m-%d')

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
