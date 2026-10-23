"""
research/positioning.py

Speculative positioning from the CFTC Commitments of Traders (legacy report,
futures only), via the CFTC's public API: no key, updated every Friday with
Tuesday's positions.

For each contract: non-commercial (speculative) longs minus shorts, as % of
open interest, and the COT index: where that net position sits in its own
3-year range (0 = most short, 100 = most long). Extremes (>= 90 or <= 10)
tend to be contrarian: when everyone is already long, few buyers are left.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from macro_shock.research import crypto

API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
# key: (CFTC contract code, label, asset key used by the page-one view or None)
CONTRACTS = {
    "sp500": ("13874A", "S&P 500 (E-mini)", "equities"),
    "t10": ("043602", "Bono 10 años (T-Note)", "bonds"),
    "yen": ("097741", "Yen japonés", None),
    "wti": ("067651", "Petróleo WTI", "oil"),
    "gold": ("088691", "Oro", "gold"),
    "copper": ("085692", "Cobre", "copper"),
    "bitcoin": ("133741", "Bitcoin (CME)", "bitcoin"),
}
WEEKS = 156
HIGH, LOW = 90.0, 10.0


def fetch_contract(code: str, get: Optional[Callable] = None) -> pd.DataFrame:
    query = urllib.parse.urlencode({"$where": f"cftc_contract_market_code='{code}'",
                                    "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": str(WEEKS + 10)})
    rows = (get or crypto.http_json)(f"{API}?{query}")
    df = pd.DataFrame([{"date": pd.Timestamp(r["report_date_as_yyyy_mm_dd"][:10]),
                        "long": float(r["noncomm_positions_long_all"]), "short": float(r["noncomm_positions_short_all"]),
                        "oi": float(r["open_interest_all"])} for r in rows])
    if df.empty:
        raise ValueError(f"CFTC {code}: no data")
    return df.set_index("date").sort_index()


@dataclass
class Positioning:
    table: pd.DataFrame           # per contract: net % OI, COT index, weekly change, date
    history: Dict[str, pd.Series]
    errors: Dict[str, str]

    def signals(self) -> Dict[str, int]:
        """Contrarian signal per page-one asset: -1 crowded long, +1 crowded short."""
        out = {}
        for key, r in self.table.iterrows():
            asset = CONTRACTS[key][2]
            if asset and pd.notna(r["cot_index"]):
                out[asset] = -1 if r["cot_index"] >= HIGH else 1 if r["cot_index"] <= LOW else 0
        return out

    def alerts(self) -> List[str]:
        out = []
        for key, r in self.table.iterrows():
            if r["cot_index"] >= HIGH:
                out.append(f"{CONTRACTS[key][1]}: especuladores en máximos de posición larga de 3 años (índice {r['cot_index']:.0f})")
            elif r["cot_index"] <= LOW:
                out.append(f"{CONTRACTS[key][1]}: especuladores en máximos de posición corta de 3 años (índice {r['cot_index']:.0f})")
        return out


def fetch(get: Optional[Callable] = None) -> Positioning:
    rows, hist, errors = {}, {}, {}
    for key, (code, label, _) in CONTRACTS.items():
        try:
            df = fetch_contract(code, get)
        except Exception as e:
            errors[key] = str(e)
            continue
        net = (df["long"] - df["short"]) / df["oi"] * 100
        window = net.iloc[-WEEKS:]
        rng = window.max() - window.min()
        idx = (net.iloc[-1] - window.min()) / rng * 100 if rng > 0 else np.nan
        rows[key] = {"label": label, "net_pct_oi": float(net.iloc[-1]), "cot_index": float(idx),
                     "change_1w": float(net.iloc[-1] - net.iloc[-2]) if len(net) > 1 else np.nan,
                     "date": net.index[-1].date()}
        hist[key] = net
    if not rows:
        raise ValueError("CFTC API unavailable: " + "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:2]))
    return Positioning(pd.DataFrame(rows).T, hist, errors)
