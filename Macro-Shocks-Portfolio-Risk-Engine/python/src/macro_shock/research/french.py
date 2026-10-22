"""
research/french.py

Reader for the Kenneth R. French Data Library (Tuck School, Dartmouth):
https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html

Free, keyless, academic-grade daily returns since 1926: industry portfolios
(sector proxies) and the Fama-French factors (market, size, value, momentum).

Each dataset is a zip holding one CSV: free-text notes, then one or more
tables. A table is a title line, a header line starting with a comma
(",NoDur,Durbl,..."), and rows keyed by YYYYMMDD (daily), YYYYMM (monthly) or
YYYY (annual). Values are percent returns; -99.99 and -999 mark missing data.
"""

from __future__ import annotations

import functools
import io
import re
import urllib.request
import zipfile
from typing import List, Tuple

import numpy as np
import pandas as pd

BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"

INDUSTRIES_12 = "12_Industry_Portfolios_daily"
FACTORS_3 = "F-F_Research_Data_Factors_daily"
MOMENTUM = "F-F_Momentum_Factor_daily"

# 12-industry columns -> readable sector and the closest SPDR sector ETF
INDUSTRY_SECTORS = {
    "NoDur": ("Consumer staples", "XLP"),
    "Durbl": ("Consumer durables", "XLY"),
    "Manuf": ("Industrials", "XLI"),
    "Enrgy": ("Energy", "XLE"),
    "Chems": ("Materials", "XLB"),
    "BusEq": ("Technology", "XLK"),
    "Telcm": ("Telecom", "XLC"),
    "Utils": ("Utilities", "XLU"),
    "Shops": ("Retail", "XLY"),
    "Hlth": ("Healthcare", "XLV"),
    "Money": ("Financials", "XLF"),
    "Other": ("Other", "-"),
}

# Long-short style factors
STYLES = {
    "SMB": "Small vs big caps",
    "HML": "Value vs growth",
    "Mom": "Momentum",
}

_MISSING = (-99.99, -999.0)


class FrenchDataError(RuntimeError):
    """The French Data Library could not be downloaded or parsed."""


@functools.lru_cache(maxsize=None)
def download(dataset: str, timeout: float = 60.0) -> str:
    """Download one dataset zip and return the CSV text inside it (cached for the process)."""
    url = f"{BASE_URL}{dataset}_CSV.zip"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            data = zf.read(zf.namelist()[0])
    except Exception as e:
        raise FrenchDataError(f"Could not download {url}: {e}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def parse_tables(text: str) -> List[Tuple[str, pd.DataFrame]]:
    """
    All tables in a French CSV as (title, DataFrame) pairs, in file order.
    Values stay in percent; missing-data sentinels become NaN.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    tables = []
    i = 0
    while i < len(lines):
        if not re.match(r"^\s*,", lines[i]):
            i += 1
            continue
        columns = [c.strip() for c in lines[i].split(",")[1:]]
        title_lines = []
        j = i - 1
        while j >= 0 and lines[j].strip():
            title_lines.insert(0, lines[j].strip())
            j -= 1
        rows = []
        i += 1
        while i < len(lines) and re.match(r"^\s*\d{4,8}\s*,", lines[i]):
            parts = [p.strip() for p in lines[i].split(",")]
            rows.append([parts[0]] + [float(p) if p else np.nan for p in parts[1:len(columns) + 1]])
            i += 1
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["date"] + columns)
        width = len(df["date"].iloc[0])
        fmt = {8: "%Y%m%d", 6: "%Y%m", 4: "%Y"}.get(width)
        if fmt is None:
            continue
        df["date"] = pd.to_datetime(df["date"], format=fmt)
        df = df.set_index("date").replace(list(_MISSING), np.nan)
        tables.append((" ".join(title_lines), df))
    if not tables:
        raise FrenchDataError("No data tables found in the French Data Library file")
    return tables


def daily_returns(dataset: str, text: str | None = None) -> pd.DataFrame:
    """
    First table of a daily dataset (value-weighted returns for industry
    portfolios) as decimal returns. Pass ``text`` to parse already-downloaded data.
    """
    _, df = parse_tables(text if text is not None else download(dataset))[0]
    return df / 100.0


def market_return(factors: pd.DataFrame) -> pd.Series:
    """Total market return (Mkt-RF + RF) from the Fama-French factors table."""
    return (factors["Mkt-RF"] + factors["RF"]).rename("Market")
