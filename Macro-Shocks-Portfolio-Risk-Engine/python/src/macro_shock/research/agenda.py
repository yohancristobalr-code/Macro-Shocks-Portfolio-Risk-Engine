"""
research/agenda.py

The next two weeks' market-moving releases: FRED's release calendar (same
key as the rest of the engine) filtered to the US data that move markets,
plus the Fed's meeting dates. The S&P Global PMI is not in FRED's calendar.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Callable, List, Optional

import pandas as pd

# (keyword in FRED's release name, label, impact)
KEY_RELEASES = [
    ("consumer price index", "IPC (CPI)", "alto"), ("employment situation", "Empleo (nóminas)", "alto"),
    ("gross domestic product", "PIB", "alto"), ("personal income and outlays", "Ingresos y gasto personal (PCE)", "alto"),
    ("producer price index", "Precios al productor (PPI)", "medio"), ("retail", "Ventas minoristas", "medio"),
    ("industrial production", "Producción industrial", "medio"), ("unemployment insurance weekly claims", "Peticiones de subsidio", "medio"),
    ("job openings", "Vacantes (JOLTS)", "medio"), ("university of michigan", "Confianza del consumidor (Michigan)", "medio"),
    ("new residential construction", "Construcción de viviendas", "bajo"), ("durable goods", "Bienes duraderos", "medio"),
]
# FOMC statement days (second day of each meeting); verify at federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
URL = "https://api.stlouisfed.org/fred/releases/dates"


def _get(url: str, timeout: float = 20.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "macro-shock-engine"}), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def upcoming(today: date, days: int = 14, get: Optional[Callable] = None, api_key: Optional[str] = None) -> pd.DataFrame:
    key = api_key or os.getenv("FRED_API_KEY", "")
    q = urllib.parse.urlencode({"api_key": key, "file_type": "json", "realtime_start": today.isoformat(),
                                "realtime_end": (today + timedelta(days=days)).isoformat(),
                                "include_release_dates_with_no_data": "true", "limit": "1000"})
    data = (get or _get)(f"{URL}?{q}")
    rows = []
    for r in data.get("release_dates", []):
        name = r.get("release_name", "").lower()
        for kw, label, impact in KEY_RELEASES:
            if kw in name:
                rows.append({"date": r["date"], "event": label, "impact": impact})
                break
    for d in FOMC_2026:
        if today.isoformat() <= d <= (today + timedelta(days=days)).isoformat():
            rows.append({"date": d, "event": "Decisión de la Fed (FOMC), 14:00 ET", "impact": "alto"})
    df = pd.DataFrame(rows, columns=["date", "event", "impact"]).drop_duplicates()
    return df.sort_values(["date", "impact"]).reset_index(drop=True)
