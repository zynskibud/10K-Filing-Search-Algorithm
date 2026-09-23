"""About 100 large S&P 500 companies, grouped by GICS sector."""

COMPANIES: dict[str, list[str]] = {
    "Information Technology": [
        "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "AMD", "INTC",
        "IBM", "QCOM", "TXN", "INTU", "AMAT", "MU",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "DIS", "T", "VZ", "CMCSA", "TMUS", "CHTR",
    ],
    "Consumer Discretionary": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TJX", "GM", "F",
    ],
    "Consumer Staples": [
        "WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ", "CL", "KMB",
    ],
    "Health Care": [
        "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR", "BMY",
        "AMGN", "GILD", "CVS",
    ],
    "Financials": [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA",
        "SPGI", "PGR",
    ],
    "Industrials": [
        "CAT", "BA", "HON", "UPS", "GE", "RTX", "LMT", "DE", "UNP", "MMM", "FDX",
    ],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "OXY", "PSX"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP"],
    "Real Estate": ["PLD", "AMT", "EQIX", "SPG", "O"],
    "Materials": ["LIN", "SHW", "APD", "FCX", "NEM", "DOW"],
}

TICKERS: list[str] = [t for group in COMPANIES.values() for t in group]

# Old registrant CIKs for companies that moved to a new holding company.
# The current ticker maps to the new CIK, which lacks the older 10-K filings.
EXTRA_CIKS: dict[str, list[str]] = {
    "XOM": ["0000034088"],   # Exxon Mobil Corp
    "BLK": ["0001364742"],   # BlackRock Finance, Inc.
    "AVGO": ["0001649338"],  # Broadcom Pte. Ltd.
    "DIS": ["0001001039"],   # TWDC Enterprises 18 Corp.
}
