#!/usr/bin/env python3
"""
MON100 Premium Tracker - Data Fetcher
Fetches 2 years of historical data and calculates premium for MON100.NS ETF

Data Sources:
- MON100.NS market prices: Yahoo Finance (yfinance)
- Official NAV: mfapi.in API (scheme code 114984)
- USDINR forex rates: Yahoo Finance

Premium Calculation:
- Adjusted iNAV = Official NAV × (Current day USDINR / NAV day USDINR)
- Premium % = ((Market Price - Adjusted iNAV) / Adjusted iNAV) × 100
"""

import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import sys


def fetch_mon100_prices(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch MON100.NS daily closing prices from Yahoo Finance.

    Args:
        start_date: Start date in 'YYYY-MM-DD' format
        end_date: End date in 'YYYY-MM-DD' format

    Returns:
        DataFrame with Date index and Close price column
    """
    print(f"Fetching MON100.NS prices from {start_date} to {end_date}...")

    try:
        ticker = yf.Ticker("MON100.NS")
        df = ticker.history(start=start_date, end=end_date, auto_adjust=True)

        if df.empty:
            raise ValueError("No price data returned for MON100.NS")

        # Keep only Close price and reset index
        df = df[['Close']].copy()
        df.index = df.index.tz_localize(None)  # Remove timezone info
        df.index = df.index.normalize()  # Normalize to date only
        df.columns = ['price']

        print(f"  Retrieved {len(df)} price records")
        return df

    except Exception as e:
        print(f"Error fetching MON100.NS prices: {e}")
        raise


def fetch_nav_data(scheme_code: int = 114984) -> pd.DataFrame:
    """
    Fetch NAV data from mfapi.in API.

    Args:
        scheme_code: Mutual fund scheme code (default: 114984 for Motilal NASDAQ 100)

    Returns:
        DataFrame with Date index and NAV column
    """
    url = f"https://api.mfapi.in/mf/{scheme_code}"
    print(f"Fetching NAV data from {url}...")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        if 'data' not in data:
            raise ValueError("Invalid API response - 'data' field missing")

        # Parse NAV data - format is: {"date": "25-12-2024", "nav": "123.45"}
        nav_records = []
        for record in data['data']:
            try:
                date = datetime.strptime(record['date'], '%d-%m-%Y')
                nav = float(record['nav'])
                nav_records.append({'date': date, 'nav': nav})
            except (ValueError, KeyError) as e:
                continue  # Skip malformed records

        df = pd.DataFrame(nav_records)
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)

        print(f"  Retrieved {len(df)} NAV records")
        return df

    except requests.RequestException as e:
        print(f"Error fetching NAV data: {e}")
        raise


def fetch_usdinr_rates(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch USDINR forex rates from Yahoo Finance.

    Args:
        start_date: Start date in 'YYYY-MM-DD' format
        end_date: End date in 'YYYY-MM-DD' format

    Returns:
        DataFrame with Date index and forex rate column
    """
    print(f"Fetching USDINR rates from {start_date} to {end_date}...")

    try:
        ticker = yf.Ticker("USDINR=X")
        df = ticker.history(start=start_date, end=end_date, auto_adjust=True)

        if df.empty:
            raise ValueError("No forex data returned for USDINR=X")

        # Keep only Close price
        df = df[['Close']].copy()
        df.index = df.index.tz_localize(None)
        df.index = df.index.normalize()
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

    Premium calculation:
    1. Adjusted iNAV = Official NAV × (Current day USDINR / NAV day USDINR)
    2. Premium % = ((Market Price - Adjusted iNAV) / Adjusted iNAV) × 100

    Args:
        prices_df: DataFrame with 'price' column
        nav_df: DataFrame with 'nav' column
        forex_df: DataFrame with 'usdinr' column

    Returns:
        DataFrame with all data merged and premium calculated
    """
    print("Calculating premium...")

    # Create a copy of prices as the base
    result = prices_df.copy()

    # Forward-fill NAV to get the latest available NAV for each price date
    # (NAV is published with a delay, so we use the most recent available)
    nav_reindexed = nav_df.reindex(result.index, method='ffill')
    result['nav'] = nav_reindexed['nav']

    # Get USDINR for each date (forward-fill for missing dates)
    forex_reindexed = forex_df.reindex(result.index, method='ffill')
    result['usdinr'] = forex_reindexed['usdinr']

    # Get the USDINR rate on the day the NAV was calculated
    # We need to find which NAV date corresponds to each row
    nav_dates = nav_df.index.to_series()

    # For each price date, find the corresponding NAV date
    result['nav_date'] = pd.NaT
    for idx in result.index:
        # Find the most recent NAV date on or before the price date
        nav_mask = nav_dates.index <= idx
        if nav_mask.any():
            result.loc[idx, 'nav_date'] = nav_dates[nav_mask].index[-1]

    # Get USDINR on NAV date (forward-fill to handle weekends/holidays)
    forex_all = forex_df.reindex(
        pd.date_range(start=forex_df.index.min(), end=forex_df.index.max()),
        method='ffill'
    )

    result['usdinr_nav_day'] = result['nav_date'].apply(
        lambda x: forex_all.loc[x, 'usdinr'] if pd.notna(x) and x in forex_all.index else np.nan
    )

    # Drop rows with missing data
    result = result.dropna(subset=['price', 'nav', 'usdinr', 'usdinr_nav_day'])

    # Calculate adjusted iNAV
    # Adjusted iNAV = NAV × (Current USDINR / NAV day USDINR)
    result['adjusted_inav'] = result['nav'] * (result['usdinr'] / result['usdinr_nav_day'])

    # Calculate premium percentage
    result['premium'] = ((result['price'] - result['adjusted_inav']) / result['adjusted_inav']) * 100

    print(f"  Calculated premium for {len(result)} trading days")

    return result


def calculate_statistics(premiums: pd.Series) -> Dict[str, float]:
    """
    Calculate statistical measures for the premium series.

    Args:
        premiums: Series of premium percentages

    Returns:
        Dictionary with statistical measures
    """
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
    """
    Save the data to a JSON file for the web frontend.

    Args:
        df: DataFrame with all calculated data
        stats: Dictionary with statistical measures
        output_path: Path to save the JSON file
    """
    print(f"Saving data to {output_path}...")

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
    start_date = end_date - timedelta(days=730)  # ~2 years

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
        print()

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
