"""
research/stooq.py

Daily prices from Stooq (stooq.com): free CSV downloads, no key. Used for the
S&P 500 sector ETFs and for gold, silver and copper, which FRED stopped
publishing. Stooq limits how many files one address can download per day;
the report fetches each symbol once.
"""

from __future__ import annotations

import io
import urllib.request
from typing import Callable, Optional

import pandas as pd

URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


def http_text(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "macro-shock-engine/1.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def daily_close(symbol: str, get: Optional[Callable] = None) -> pd.Series:
    """Close prices for a Stooq symbol (e.g. 'xlk.us', 'xauusd', 'hg.f')."""
    text = (get or http_text)(URL.format(symbol=symbol))
    if not text.lstrip().lower().startswith("date"):
        raise ValueError(f"Stooq {symbol}: {text.strip()[:80] or 'empty answer'}")
    df = pd.read_csv(io.StringIO(text))
    s = pd.Series(df["Close"].astype(float).to_numpy(), index=pd.to_datetime(df["Date"]), name=symbol)
    s = s.dropna().sort_index()
    if s.empty:
        raise ValueError(f"Stooq {symbol}: no data")
    return s
