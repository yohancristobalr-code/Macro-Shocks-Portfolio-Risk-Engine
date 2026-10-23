"""
research/prices.py

Daily prices for ETFs and futures from public sources, tried in order until
one answers (each one blocks automated downloads from time to time):

  1. Yahoo Finance chart API   adjusted close (dividends included), 10 years
  2. Nasdaq.com API            ETFs and stocks, close
  3. Stooq                     CSV download (now often behind an anti-bot page)

No key is needed for any of them.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date, timedelta
from typing import Callable, Optional, Tuple

import pandas as pd

from macro_shock.research import stooq

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def http_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def yahoo(symbol: str, get: Optional[Callable] = None, years: int = 10) -> pd.Series:
    data = (get or http_json)(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                              f"?range={years}y&interval=1d&includeAdjustedClose=true")
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        raise ValueError(f"Yahoo {symbol}: {(data.get('chart') or {}).get('error') or 'no data'}")
    r = res[0]
    ind = r["indicators"]
    values = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    s = pd.Series(values, index=pd.to_datetime(r["timestamp"], unit="s").normalize(), dtype=float).dropna()
    if s.empty:
        raise ValueError(f"Yahoo {symbol}: empty")
    return s[~s.index.duplicated(keep="last")].sort_index()


def nasdaq(symbol: str, get: Optional[Callable] = None, years: int = 10, assetclass: str = "etf") -> pd.Series:
    start = (date.today() - timedelta(days=365 * years)).isoformat()
    data = (get or http_json)(f"https://api.nasdaq.com/api/quote/{symbol}/historical?assetclass={assetclass}"
                              f"&fromdate={start}&todate={date.today().isoformat()}&limit=9999")
    rows = (((data or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    if not rows:
        raise ValueError(f"Nasdaq {symbol}: no data")
    s = pd.Series([float(str(r["close"]).replace("$", "").replace(",", "")) for r in rows],
                  index=pd.to_datetime([r["date"] for r in rows], format="%m/%d/%Y"), dtype=float)
    return s.sort_index()


def etf_close(symbol: str, get_json: Optional[Callable] = None, get_text: Optional[Callable] = None) -> Tuple[pd.Series, str]:
    """(prices, source) for a US ETF, from the first source that answers."""
    errors = []
    for name, fn in (("Yahoo", lambda: yahoo(symbol, get_json)), ("Nasdaq", lambda: nasdaq(symbol, get_json)),
                     ("Stooq", lambda: stooq.daily_close(f"{symbol.lower()}.us", get_text))):
        try:
            return fn(), name
        except Exception as e:
            errors.append(f"{name}: {str(e)[:60]}")
    raise ValueError(f"{symbol} unavailable ({'; '.join(errors)})")
