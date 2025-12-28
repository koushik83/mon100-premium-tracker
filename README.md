# MON100 Premium Tracker

A web-based dashboard to track the premium/discount of Motilal Oswal NASDAQ 100 ETF (MON100.NS) relative to its indicative NAV (iNAV).

## What is Premium?

When you buy an ETF on the stock exchange, the market price may differ from the actual value of its underlying holdings (NAV). This difference is called **premium** (when price > NAV) or **discount** (when price < NAV).

**Premium % = ((Market Price - iNAV) / iNAV) × 100**

For international ETFs like MON100, the iNAV is adjusted for forex movements:

**Adjusted iNAV = Official NAV × (Current USDINR / NAV day USDINR)**

## Premium Zones

| Zone | Premium | Recommendation |
|------|---------|----------------|
| Green | < 2% | Good time to buy |
| Yellow | 2% - 4% | Neutral - consider waiting |
| Red | > 4% | Avoid buying |

## Features

- 2 years of historical premium data
- Interactive chart with zoom and pan
- Date range selector (1M, 3M, 6M, 1Y, 2Y, ALL)
- Statistics: Min, Max, Average, Median, Percentiles
- Dark/Light mode toggle
- Mobile responsive design
- Auto-updates daily via GitHub Actions

## Data Sources

- **MON100.NS Prices**: Yahoo Finance (yfinance)
- **Official NAV**: [mfapi.in](https://api.mfapi.in/mf/114984) (Scheme Code: 114984)
- **USDINR Rates**: Yahoo Finance (USDINR=X)

## Local Setup

### Prerequisites

```bash
pip install yfinance requests pandas numpy
```

### Generate Data

```bash
python fetch_premium_data.py
```

This creates `premium_data.json` with all historical data.

### View Locally

Open `index.html` in a browser, or use a local server:

```bash
# Python 3
python -m http.server 8000

# Then open http://localhost:8000
```

## Deploy to GitHub Pages

1. **Create a new GitHub repository**

2. **Push the code**:
   ```bash
   cd premium-tracker
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/premium-tracker.git
   git push -u origin main
   ```

3. **Enable GitHub Pages**:
   - Go to repository Settings → Pages
   - Source: Deploy from a branch
   - Branch: `main` / `/ (root)`
   - Click Save

4. **Wait 1-2 minutes**, then visit:
   `https://YOUR_USERNAME.github.io/premium-tracker/`

## GitHub Actions (Auto-Update)

The included workflow (`.github/workflows/update-data.yml`) automatically:

- Runs daily at 4:30 PM IST (after market close)
- Fetches latest data
- Commits and pushes updated `premium_data.json`

The first run is triggered automatically when you push to `main`.

You can also trigger manually:
- Go to Actions → "Update Premium Data" → Run workflow

## Project Structure

```
premium-tracker/
├── index.html              # Interactive dashboard
├── fetch_premium_data.py   # Data fetching script
├── premium_data.json       # Generated data file
├── README.md               # This file
└── .github/
    └── workflows/
        └── update-data.yml # GitHub Actions workflow
```

## Output Format (premium_data.json)

```json
{
  "dates": ["2023-12-25", "2023-12-26", ...],
  "premiums": [2.3, 4.1, 1.8, ...],
  "prices": [234.50, 236.20, ...],
  "navs": [221.50, 222.10, ...],
  "adjusted_inavs": [225.30, 226.50, ...],
  "usdinr": [83.25, 83.30, ...],
  "stats": {
    "min": -1.5,
    "max": 8.7,
    "average": 3.2,
    "median": 2.8,
    "p25": 1.2,
    "p75": 4.5,
    "std": 1.8,
    "current": 4.4
  },
  "last_updated": "2025-12-25T16:00:00",
  "data_points": 500
}
```

## Troubleshooting

### "No data returned" Error

This usually means Yahoo Finance is temporarily unavailable. Wait a few minutes and try again.

### Chart not loading

Make sure `premium_data.json` exists in the same directory as `index.html`. Run `fetch_premium_data.py` to generate it.

### GitHub Actions failing

Check that the workflow has write permissions:
- Settings → Actions → General → Workflow permissions → Read and write permissions

## License

MIT License - Feel free to use and modify.
