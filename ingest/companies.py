"""About 100 large S&P 500 companies, grouped by GICS sector."""

COMPANIES: dict[str, list[str]] = {
    "Information Technology": [
        "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO", "AMD", "INTC",
        "IBM", "QCOM", "TXN", "INTU", "AMAT", "MU",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "DIS", "T", "VZ", "CMCSA", "TMUS", "EA",
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
