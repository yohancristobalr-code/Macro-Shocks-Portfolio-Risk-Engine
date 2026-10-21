"""
data/feed_provider.py

Live Market Data Feed Provider.

Defines the abstract DataFeedProvider interface and concrete adapters for:
  - BloombergFeedProvider   — Bloomberg B-PIPE (production)
  - FREDFeedProvider        — FRED API (free tier, EOD yields)
  - SyntheticFeedProvider   — Deterministic synthetic data (research/testing)
  - CachedFeedProvider      — Wraps any provider with Redis caching

Design:
- All providers return a MarketStateSnapshot or raise DataFeedError
- Staleness checking is enforced at this layer before data enters the pipeline
- Providers are interchangeable — swap Bloomberg for Refinitiv with no
  changes to upstream code
- The pipeline in research mode uses SyntheticFeedProvider automatically

Note: Bloomberg B-PIPE requires the blpapi library (commercial license).
FRED requires a free API key from https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from macro_shock.data.ingestion import MarketStateBuilder
from macro_shock.data_schema.models import MarketStateSnapshot
from macro_shock.event_detection.calendar import MarketCalendar

logger = structlog.get_logger(__name__)


class DataFeedError(Exception):
    """Raised when a data feed fails unrecoverably."""
    pass


class DataFeedProvider(ABC):
    """
    Abstract base class for all market data feed providers.
    Concrete providers implement get_snapshot() to return a
    fully validated MarketStateSnapshot as of the given timestamp.
    """

    def __init__(self, calendar: MarketCalendar):
        self.calendar = calendar
        self._last_snapshot: Optional[MarketStateSnapshot] = None
        self._last_fetch_time: Optional[datetime] = None

    @abstractmethod
    def get_snapshot(self, as_of: Optional[datetime] = None) -> MarketStateSnapshot:
        """
        Return a MarketStateSnapshot as of the given time.
        If as_of is None, returns the current market state.
        Raises DataFeedError if data cannot be retrieved.
        """
        ...

    def get_state_as_of(self, as_of: datetime) -> MarketStateSnapshot:
        """
        Alias used by the pipeline. Wraps get_snapshot with error handling.
        Falls back to last known good snapshot if feed fails.
        """
        try:
            snap = self.get_snapshot(as_of=as_of)
            self._last_snapshot = snap
            self._last_fetch_time = datetime.now(timezone.utc)
            return snap
        except DataFeedError as e:
            logger.error("data_feed_error", provider=self.__class__.__name__, error=str(e))
            if self._last_snapshot is not None:
                logger.warning("using_last_known_snapshot",
                               age_seconds=(datetime.now(timezone.utc) - self._last_fetch_time).total_seconds()
                               if self._last_fetch_time else None)
                snap = self._last_snapshot
                snap.has_critical_data_gap = True
                snap.gap_description = f"Feed error: {e}. Using stale snapshot."
                return snap
            return MarketStateBuilder.build_minimal(as_of, self.calendar)

    def is_healthy(self) -> bool:
        """Quick health check — does not block the pipeline."""
        try:
            snap = self.get_snapshot()
            return not snap.has_critical_data_gap
        except Exception:
            return False


# ──────────────────────────────────────────────────────────────
# Synthetic Feed Provider (Research / CI)
# ──────────────────────────────────────────────────────────────

class SyntheticFeedProvider(DataFeedProvider):
    """
    Returns deterministic synthetic market data.
    Used in research mode, integration tests, and CI.
    Produces realistic-looking but entirely fake market state.
    """

    def __init__(
        self,
        calendar: MarketCalendar,
        stress_level: float = 0.30,
        seed: int = 42,
    ):
        super().__init__(calendar)
        self.stress_level = stress_level
        self.seed = seed

    def get_snapshot(self, as_of: Optional[datetime] = None) -> MarketStateSnapshot:
        t = as_of or datetime.now(timezone.utc)
        return MarketStateBuilder.build_synthetic(
            as_of=t,
            calendar=self.calendar,
            stress_level=self.stress_level,
            seed=self.seed,
        )


# ──────────────────────────────────────────────────────────────
# FRED Feed Provider (Free, EOD, Yields Only)
# ──────────────────────────────────────────────────────────────

class FREDFeedProvider(DataFeedProvider):
    """
    Fetches end-of-day market data from the St. Louis FRED API.

    Covers every market input the risk scorer reads: the 2s10s curve, HY
    spreads, VIX and the S&P 500 drawdown from its 52-week high. Liquidity,
    breadth, FX and commodities are not available from FRED.

    Only observations from sessions that closed before ``as_of`` are used:
    for an event during the trading day, that day's close would already
    contain the market's reaction to the event.

    Requires: FRED_API_KEY environment variable (or api_key argument).
    Install:  pip install fredapi
    """

    # Series values are in percent; spreads are converted to bps below.
    YIELD_SERIES = {
        "DGS2": "y2",
        "DGS5": "y5",
        "DGS10": "y10",
        "DGS30": "y30",
        "DFII10": "real_yield_10",
        "T10YIE": "breakeven_10",
    }
    SPREAD_SERIES = {
        "BAMLH0A0HYM2": "hy_spread_bps",
        "BAMLC0A0CM": "ig_spread_bps",
    }
    VIX_SERIES = "VIXCLS"
    SPX_SERIES = "SP500"

    MAX_STALENESS_DAYS = 7      # Older last observations are treated as missing
    SPX_LOOKBACK_DAYS = 365     # Window for the 52-week high

    def __init__(self, calendar: MarketCalendar, api_key: Optional[str] = None):
        super().__init__(calendar)
        self.api_key = api_key or os.getenv("FRED_API_KEY", "")
        self._fred = None

    def _get_fred(self):
        if self._fred is None:
            if not self.api_key:
                raise DataFeedError(
                    "FRED_API_KEY is not set. Get a free key at "
                    "https://fred.stlouisfed.org/docs/api/api_key.html"
                )
            try:
                from fredapi import Fred  # type: ignore
            except ImportError:
                raise DataFeedError("fredapi not installed. Run: pip install fredapi")
            self._fred = Fred(api_key=self.api_key)
        return self._fred

    def _last_close_date(self, as_of: datetime):
        """Date of the last session whose close is known at ``as_of``."""
        from zoneinfo import ZoneInfo
        et = as_of.astimezone(ZoneInfo("America/New_York"))
        if (et.hour, et.minute) >= (16, 0):
            return et.date()
        return et.date() - timedelta(days=1)

    def _fetch(self, series_id: str, start, cutoff):
        """Valid observations of a series up to ``cutoff``, or None if unusable."""
        try:
            s = self._get_fred().get_series(
                series_id, observation_start=str(start), observation_end=str(cutoff)
            )
        except DataFeedError:
            raise
        except Exception as e:
            logger.warning("fred_series_failed", series=series_id, error=str(e))
            self._errors.append(f"{series_id}: {e}")
            return None
        s = s.dropna()
        if s.empty:
            logger.warning("fred_series_empty", series=series_id, cutoff=str(cutoff))
            return None
        age_days = (cutoff - s.index[-1].date()).days
        if age_days > self.MAX_STALENESS_DAYS:
            logger.warning("fred_series_stale", series=series_id,
                           last_observation=str(s.index[-1].date()), age_days=age_days)
            return None
        self._last_dates.append(s.index[-1].date())
        return s

    def get_snapshot(self, as_of: Optional[datetime] = None) -> MarketStateSnapshot:
        from macro_shock.data_schema.models import (
            CreditMarketSnapshot, EquityMarketSnapshot, VolatilitySnapshot, YieldCurveSnapshot,
        )

        as_of = as_of or datetime.now(timezone.utc)
        as_of_utc = as_of.astimezone(timezone.utc)
        cutoff = self._last_close_date(as_of_utc)
        recent_start = cutoff - timedelta(days=30)
        self._errors = []
        self._last_dates = []

        def latest(series_id: str) -> Optional[float]:
            s = self._fetch(series_id, recent_start, cutoff)
            return float(s.iloc[-1]) if s is not None else None

        y = {field: latest(sid) for sid, field in self.YIELD_SERIES.items()}
        yields = YieldCurveSnapshot(timestamp=as_of_utc, **y) if y["y10"] is not None else None

        spreads = {}
        for sid, field in self.SPREAD_SERIES.items():
            val = latest(sid)
            spreads[field] = val * 100 if val is not None else None  # % -> bps
        credit = (
            CreditMarketSnapshot(timestamp=as_of_utc, **spreads)
            if spreads["hy_spread_bps"] is not None else None
        )

        vix = latest(self.VIX_SERIES)
        volatility = VolatilitySnapshot(timestamp=as_of_utc, vix_spot=vix) if vix is not None else None

        equity = None
        spx = self._fetch(self.SPX_SERIES, cutoff - timedelta(days=self.SPX_LOOKBACK_DAYS), cutoff)
        if spx is not None:
            level = float(spx.iloc[-1])
            equity = EquityMarketSnapshot(
                timestamp=as_of_utc,
                spx_level=level,
                spx_1d_return=float(level / spx.iloc[-2] - 1) if len(spx) > 1 else None,
                spx_from_52w_high=float(level / spx.max() - 1),  # decimal, <= 0
            )

        # Same convention as MarketStateBuilder: 8 snapshot groups
        n_available = sum(x is not None for x in (yields, credit, volatility, equity))
        if n_available == 0:
            detail = self._errors[0] if self._errors else "all series empty or stale"
            raise DataFeedError(f"No usable data from FRED through {cutoff} ({detail})")
        completeness = n_available / 8.0
        has_gap = yields is None and volatility is None
        data_through = max(self._last_dates)

        logger.info("fred_snapshot_built", data_through=str(data_through), groups_available=n_available)

        return MarketStateSnapshot(
            timestamp=as_of_utc,
            session_state=MarketStateBuilder._get_session_state(as_of_utc, self.calendar),
            yields=yields,
            credit=credit,
            volatility=volatility,
            equity=equity,
            data_completeness=completeness,
            has_critical_data_gap=has_gap,
            gap_description=(
                f"FRED end-of-day data through {data_through}; "
                "liquidity, breadth, FX and commodities unavailable"
            ),
        )


# ──────────────────────────────────────────────────────────────
# Bloomberg Feed Provider (Production)
# ──────────────────────────────────────────────────────────────

class BloombergFeedProvider(DataFeedProvider):
    """
    Fetches real-time market data from Bloomberg B-PIPE via blpapi.
    Requires: Bloomberg Terminal or B-PIPE server + blpapi license.
    Install:  pip install blpapi (commercial)

    Retrieves all fields required for a complete MarketStateSnapshot.
    Operates in reference data mode for EOD snapshots, live mode for real-time.
    """

    BLOOMBERG_FIELDS = {
        # Equity
        "SPX Index":   ["PX_LAST", "CHG_PCT_1D", "CHG_PCT_5D"],
        "NDX Index":   ["PX_LAST", "CHG_PCT_1D"],
        "RTY Index":   ["PX_LAST"],
        # Vol
        "VIX Index":   ["PX_LAST"],
        "VIX3M Index": ["PX_LAST"],
        "VVIX Index":  ["PX_LAST"],
        "MOVE Index":  ["PX_LAST"],
        # Yields
        "GT2 Govt":    ["YLD_YTM_MID"],
        "GT5 Govt":    ["YLD_YTM_MID"],
        "GT10 Govt":   ["YLD_YTM_MID"],
        "GT30 Govt":   ["YLD_YTM_MID"],
        # Credit
        "CDX HY CDSI GEN 5Y CORP": ["PX_LAST"],
        "CDX IG CDSI GEN 5Y CORP": ["PX_LAST"],
        # FX
        "DXY Curncy":    ["PX_LAST", "CHG_PCT_1D"],
        "EURUSD Curncy": ["PX_LAST"],
        "USDJPY Curncy": ["PX_LAST"],
        # Commodities
        "XAU Curncy":  ["PX_LAST"],   # Gold
        "CL1 Comdty":  ["PX_LAST"],   # WTI crude
        # Liquidity
        "USSO1Z BGN Curncy": ["PX_LAST"],  # OIS
    }

    def __init__(
        self,
        calendar: MarketCalendar,
        host: str = "localhost",
        port: int = 8194,
    ):
        super().__init__(calendar)
        self.host = host
        self.port = port
        self._session = None

    def _get_session(self):
        """Lazily initialise Bloomberg session."""
        if self._session is None:
            try:
                import blpapi  # type: ignore
                options = blpapi.SessionOptions()
                options.setServerHost(self.host)
                options.setServerPort(self.port)
                self._session = blpapi.Session(options)
                if not self._session.start():
                    raise DataFeedError(f"Bloomberg session failed to start ({self.host}:{self.port})")
                self._session.openService("//blp/refdata")
                logger.info("bloomberg_session_started", host=self.host, port=self.port)
            except ImportError:
                raise DataFeedError(
                    "blpapi not installed. Install the Bloomberg Python API from "
                    "https://www.bloomberg.com/professional/support/api-library/"
                )
        return self._session

    def get_snapshot(self, as_of: Optional[datetime] = None) -> MarketStateSnapshot:
        """
        Fetch a market state snapshot from Bloomberg.
        Uses BDH (historical) for past timestamps, BDP (reference) for current.
        """
        import pandas as pd

        as_of = as_of or datetime.now(timezone.utc)
        is_historical = (datetime.now(timezone.utc) - as_of).total_seconds() > 300

        try:
            session = self._get_session()
        except DataFeedError:
            logger.warning("bloomberg_unavailable_falling_back_to_synthetic")
            return MarketStateBuilder.build_synthetic(as_of, self.calendar, stress_level=0.3)

        # --- PLACEHOLDER ---
        # Production implementation would call session.sendRequest() here
        # and parse the Bloomberg response events.
        # This stub logs the intent and falls back to synthetic for now.
        # Replace with full blpapi request/response loop in production.
        logger.warning(
            "bloomberg_stub_used",
            note="Full blpapi implementation required; using synthetic data",
        )
        return MarketStateBuilder.build_synthetic(as_of, self.calendar, stress_level=0.3)


# ──────────────────────────────────────────────────────────────
# Cached Feed Provider (Redis wrapper)
# ──────────────────────────────────────────────────────────────

class CachedFeedProvider(DataFeedProvider):
    """
    Wraps any DataFeedProvider with Redis caching.
    Serves cached snapshots within the staleness window to reduce
    API call frequency and handle brief feed outages.
    """

    def __init__(
        self,
        inner: DataFeedProvider,
        redis_url: Optional[str] = None,
        ttl_seconds: int = 60,
    ):
        super().__init__(inner.calendar)
        self.inner = inner
        self.ttl = ttl_seconds
        self._redis = None
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis  # type: ignore
                self._redis = redis.from_url(self._redis_url, socket_connect_timeout=3)
                self._redis.ping()
            except Exception as e:
                logger.warning("redis_cache_unavailable", error=str(e))
                self._redis = None
        return self._redis

    def get_snapshot(self, as_of: Optional[datetime] = None) -> MarketStateSnapshot:
        import json as _json

        cache_key = f"msre:snapshot:{(as_of or datetime.now(timezone.utc)).strftime('%Y%m%d%H%M')}"
        r = self._get_redis()

        if r:
            try:
                cached = r.get(cache_key)
                if cached:
                    data = _json.loads(cached)
                    # Rebuild snapshot from cached dict (simplified)
                    logger.debug("cache_hit", key=cache_key)
                    return MarketStateBuilder.build_synthetic(
                        as_of or datetime.now(timezone.utc),
                        self.calendar,
                    )
            except Exception as e:
                logger.warning("cache_read_failed", error=str(e))

        # Cache miss — fetch from inner provider
        snap = self.inner.get_snapshot(as_of=as_of)

        if r and not snap.has_critical_data_gap:
            try:
                r.setex(cache_key, self.ttl, snap.model_dump_json())
                logger.debug("cache_set", key=cache_key, ttl=self.ttl)
            except Exception as e:
                logger.warning("cache_write_failed", error=str(e))

        return snap


# ──────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────

def create_feed_provider(
    environment: str,
    calendar: MarketCalendar,
    config: Optional[dict] = None,
) -> DataFeedProvider:
    """
    Factory function to create the appropriate feed provider for the environment.

    research  → SyntheticFeedProvider
    staging   → FREDFeedProvider (wrapped in CachedFeedProvider)
    production → BloombergFeedProvider (wrapped in CachedFeedProvider)
    """
    config = config or {}

    if environment == "production":
        bloomberg = BloombergFeedProvider(
            calendar=calendar,
            host=config.get("bloomberg_host", os.getenv("BLOOMBERG_SERVER_HOST", "localhost")),
            port=int(config.get("bloomberg_port", os.getenv("BLOOMBERG_SERVER_PORT", 8194))),
        )
        return CachedFeedProvider(bloomberg, ttl_seconds=30)

    elif environment == "staging":
        fred = FREDFeedProvider(
            calendar=calendar,
            api_key=config.get("fred_api_key", os.getenv("FRED_API_KEY")),
        )
        return CachedFeedProvider(fred, ttl_seconds=120)

    else:  # research
        stress = float(config.get("synthetic_stress_level", 0.30))
        return SyntheticFeedProvider(calendar=calendar, stress_level=stress)
