"""
research/report.py

Executive HTML report for the pre-trading routine: every study of the engine
in one self-contained file (no internet needed to open it, light and dark
themes), with trend charts, an objective summary built from the numbers,
historical analogs and an out-of-sample-tested predictive model.

Each section is gathered independently: if one data source fails, the
report still renders and says which section is missing and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

from macro_shock.research import analogs as an
from macro_shock.research import charts, cpi_nowcast, growth, macro, macro_scenario as ms, sector_study
from macro_shock.research import carry as cy, crypto as cr, data_health, french, liquidity as lq
from macro_shock.research import track_record as tr
from macro_shock.research import metals as mt, sectors as sc, view as vw
from macro_shock.research import agenda as ag, positioning as po, valuation as va

logger = structlog.get_logger(__name__)

SCEN_ES = {"reacceleration": "Reaceleración con presión de costos", "disinflation": "Desinflación / aterrizaje ordenado",
           "supply_shock": "Shock de oferta / inflación persistente", "growth_scare": "Freno de crecimiento / liquidación"}
STATUS_ES = {"CONFIRMED": "confirmado", "partial": "parcial", "invalidated": "invalidado", "no signal": "sin señal"}
REGIME_ES = {"risk_on_expansion": "Expansión con apetito por riesgo", "fragile_risk_on": "Apetito por riesgo frágil",
             "risk_off_correction": "Corrección / aversión al riesgo", "crisis": "Crisis"}
QUAD_ES = {"reacceleration": "Reaceleración (crecimiento ↑, inflación ↑)",
           "disinflation": "Desinflación (crecimiento ↑, inflación ↓)",
           "supply_shock": "Estanflación / shock de oferta (crecimiento ↓, inflación ↑)",
           "growth_scare": "Desaceleración (crecimiento ↓, inflación ↓)"}
COMPONENT_ES = {"volatility": "volatilidad", "credit": "crédito", "curve": "curva", "liquidity": "liquidez",
                "funding": "financiación"}
LEADING_ES = {"wei": "Índice económico semanal (Fed NY, % PIB)", "gdpnow": "GDPNow (Fed Atlanta, % PIB)",
              "sahm": "Regla de Sahm (≥ 0.5 = recesión)", "philly_activity": "Fed Filadelfia: actividad",
              "philly_orders": "Fed Filadelfia: nuevos pedidos", "philly_prices": "Fed Filadelfia: precios pagados",
              "empire_activity": "Fed Nueva York: actividad", "empire_orders": "Fed Nueva York: nuevos pedidos",
              "empire_prices": "Fed Nueva York: precios pagados"}
SIGNAL_ES = {"claims": "Peticiones de subsidio", "indpro": "Producción industrial", "payrolls": "Empleo (nóminas)",
             "cpi": "IPC general", "core": "IPC subyacente"}
LABEL_ES = {"headline": "IPC general", "core": "Subyacente (tendencia)", "food": "Alimentos (tendencia)",
            "energy": "Energía", "gasoline": "Gasolina", "fuel_oil": "Fuel oil", "electricity": "Electricidad",
            "utility_gas": "Gas por tubería"}
MOVE_ES = {"y2": "Bono 2 años", "y10": "Bono 10 años", "real10": "Tasa real 10 años (TIPS)",
           "breakeven10": "Breakeven 10 años", "dollar": "Dólar (índice amplio)", "oil": "WTI", "equities": "S&P 500",
           "nasdaq": "Nasdaq", "credit": "Spread HY", "vix": "VIX", "curve": "Curva 2s10s"}


@dataclass
class Context:
    today: date
    errors: Dict[str, str] = field(default_factory=dict)
    data_through: Optional[date] = None
    regime: Optional[str] = None
    vulnerability: Optional[float] = None
    components: Dict[str, float] = field(default_factory=dict)
    snap: object = None
    market_series: Dict[str, pd.Series] = field(default_factory=dict)
    market: Optional[ms.MarketScenario] = None
    score_hist: Optional[pd.DataFrame] = None
    growth_series: Dict[str, pd.Series] = field(default_factory=dict)
    sig: Optional[growth.Signals] = None
    axes: Optional[growth.Axes] = None
    axes_hist: Optional[pd.DataFrame] = None
    leading: Optional[growth.Leading] = None
    nowcast: Optional[cpi_nowcast.CpiNowcast] = None
    nowcast_axis: Optional[float] = None
    btc: Optional[float] = None
    regime_study: Optional[sector_study.SectorStudy] = None
    quadrant_study: Optional[sector_study.SectorStudy] = None
    analogs: Optional[an.Analogs] = None
    model: Optional[an.Model] = None
    crypto: Optional[cr.CryptoView] = None
    liq_series: Dict[str, pd.Series] = field(default_factory=dict)
    liquidity: Optional[lq.Liquidity] = None
    health: Optional[pd.DataFrame] = None
    carry_series: Dict[str, pd.Series] = field(default_factory=dict)
    carry: Optional[cy.Carry] = None
    track_log: Optional[pd.DataFrame] = None
    track_scored: Optional[pd.DataFrame] = None
    sectors: Optional[sc.SectorView] = None
    metals: Optional[mt.MetalsView] = None
    vix_term: Optional[pd.DataFrame] = None
    asset_view: Optional[pd.DataFrame] = None
    valuation_series: Dict[str, pd.Series] = field(default_factory=dict)
    valuation: Optional[va.Valuation] = None
    positioning: Optional[po.Positioning] = None
    agenda: Optional[pd.DataFrame] = None


def _try(ctx: Context, name: str, fn):
    try:
        return fn()
    except Exception as e:                          # each section is optional
        logger.warning("report_section_unavailable", section=name, error=str(e))
        ctx.errors[name] = str(e)
        return None


def gather(fred, today: Optional[date] = None, quick: bool = False, track_path=None) -> Context:
    from macro_shock.data.feed_provider import FREDFeedProvider
    from macro_shock.event_detection.calendar import MarketCalendar
    from macro_shock.market_context.vulnerability_scorer import MarketVulnerabilityScorer

    ctx = Context(today or date.today())

    def risk():
        snap = FREDFeedProvider(MarketCalendar()).get_snapshot()
        v = MarketVulnerabilityScorer().score(snap, log=False)
        found = re.search(r"through (\d{4}-\d{2}-\d{2})", snap.gap_description or "")
        ctx.data_through = date.fromisoformat(found.group(1)) if found else ctx.today
        ctx.snap, ctx.regime, ctx.vulnerability = snap, v.regime.value, v.composite
        comps = {"volatility": v.volatility_vulnerability, "credit": v.credit_vulnerability,
                 "curve": v.yield_curve_vulnerability, "liquidity": v.liquidity_vulnerability,
                 "funding": v.funding_vulnerability}
        named = {"curve": "yield_curve"}
        ctx.components = {k: x for k, x in comps.items() if named.get(k, k) not in v.missing_components}
    _try(ctx, "riesgo", risk)

    def market():
        ctx.market_series = ms.fetch(fred, ctx.today)
        ctx.market = ms.assess(ctx.market_series)
        ctx.score_hist = ms.score_history(ctx.market_series)
    _try(ctx, "mercado", market)

    def macro_data():
        ctx.growth_series = growth.fetch(fred, growth.FULL_HISTORY_START, ctx.today)
        ctx.sig = growth.signals(ctx.growth_series)
        ctx.axes = growth.axes_at(ctx.sig, ctx.today, lagged=False)
        ctx.leading = growth.leading(ctx.growth_series)
        ends = pd.date_range(pd.Timestamp(ctx.today) - pd.DateOffset(years=15), ctx.today, freq="MS") - pd.Timedelta(days=1)
        rows = [growth.axes_at(ctx.sig, d, lagged=True) for d in ends]
        ctx.axes_hist = pd.DataFrame({"Crecimiento": [a.growth for a in rows],
                                      "Inflación": [a.inflation for a in rows]}, index=ends).astype(float)
    _try(ctx, "macro", macro_data)

    def nowcast():
        ctx.nowcast = cpi_nowcast.run(fred, ctx.today)
        if ctx.sig is not None:
            ctx.nowcast_axis, _ = macro.nowcast_axis(ctx.growth_series, ctx.sig, ctx.nowcast)
    _try(ctx, "ipc", nowcast)

    def bitcoin():
        s = pd.Series(fred.get_series("CBBTCUSD", observation_start=str(ctx.today - timedelta(days=14)),
                                      observation_end=str(ctx.today))).dropna()
        ctx.btc = float(s.iloc[-1]) if len(s) else None
    _try(ctx, "bitcoin", bitcoin)

    def crypto_axis():
        data = cr.fetch()
        quadrants = None
        if ctx.sig is not None:
            ends = pd.date_range("2017-01-01", ctx.today, freq="MS") - pd.Timedelta(days=1)
            quadrants = growth.quadrant_history(ctx.sig, ends)
        ctx.crypto = cr.analyze(data, ctx.market_series.get("nasdaq"), quadrants)
    _try(ctx, "cripto", crypto_axis)

    def fed_liquidity():
        ctx.liq_series = lq.fetch(fred, ctx.today)
        assets = {"Nasdaq": ctx.market_series.get("nasdaq")}
        if ctx.crypto is not None:
            assets["BTC"] = ctx.crypto.data.prices["BTC"].dropna()
        ctx.liquidity = lq.assess(ctx.liq_series, assets)
    _try(ctx, "liquidez", fed_liquidity)

    def yen_carry():
        ctx.carry_series = cy.fetch(fred, ctx.today)
        assets = {"Nasdaq": ctx.market_series.get("nasdaq")}
        if ctx.crypto is not None:
            assets["BTC"] = ctx.crypto.data.prices["BTC"].dropna()
        ctx.carry = cy.assess(ctx.carry_series, assets)
    _try(ctx, "carry del yen", yen_carry)

    def sp_sectors():
        prices, errors, source = sc.fetch()
        factors = ms.levels(ctx.market_series) if ctx.market_series else None
        moves = ctx.market.moves["change"] if ctx.market is not None else None
        ctx.sectors = sc.analyze(prices, factors, moves, errors, source)
    _try(ctx, "sectores S&P 500", sp_sectors)

    def metals_axis():
        ctx.metals = mt.analyze(mt.fetch(fred), ctx.market_series.get("real10"), ctx.market_series.get("y10"))
    _try(ctx, "metales", metals_axis)
    ctx.vix_term = _try(ctx, "estructura del VIX", lambda: vw.vix_term(fred, ctx.today))

    def valuation():
        ctx.valuation_series = va.fetch(fred, ctx.today)
        ctx.valuation = va.assess(ctx.valuation_series, ctx.market_series.get("equities"), with_returns=not quick)
    _try(ctx, "valoración", valuation)
    ctx.positioning = _try(ctx, "posicionamiento CFTC", po.fetch)
    ctx.agenda = _try(ctx, "calendario", lambda: ag.upcoming(ctx.today))
    _try(ctx, "visión por activo", lambda: _asset_view(ctx))

    if quick:
        return finish(ctx, track_path)
    ctx.regime_study = _try(ctx, "sectores por régimen", lambda: sector_study.run(fred, date(1990, 1, 1)))
    if ctx.sig is not None:
        ctx.quadrant_study = _try(ctx, "sectores por cuadrante",
                                  lambda: macro.quadrant_study(ctx.sig, ctx.axes.quadrant if ctx.axes else None))

        def history():
            mkt = an.fetch(fred, ctx.today)
            df = an.panel(mkt, ctx.sig)
            today_state = an.today_state(mkt, ctx.axes)
            ctx.analogs = an.analogs(df, today_state, mkt)
            ctx.model = an.model(df, today_state)
        _try(ctx, "análogos y modelo", history)
    return finish(ctx, track_path)


def _asset_view(ctx: Context) -> None:
    s = ctx.market_series
    metal = ctx.metals.data.prices if ctx.metals is not None else {}
    oil = ctx.nowcast.prices.get("wti") if ctx.nowcast is not None else s.get("oil")
    series = {"equities": s.get("equities"), "bonds": s.get("y10"), "credit": s.get("credit"),
              "gold": metal.get("gold"), "copper": metal.get("copper"), "oil": oil, "dollar": s.get("dollar"),
              "bitcoin": ctx.crypto.data.prices["BTC"].dropna() if ctx.crypto is not None else None}
    m = ctx.market
    best = m.best if m is not None else None
    extra = {}
    v = ctx.valuation
    if v is not None and "erp" in v.percentiles:
        p = v.percentiles["erp"]                   # low risk premium = expensive equities
        extra["valuation"] = {"equities": -1 if p <= 0.2 else 1 if p >= 0.8 else 0}
    if ctx.positioning is not None:
        extra["positioning"] = ctx.positioning.signals()
    ctx.asset_view = vw.asset_view(series, best, m.status(best) if best else None,
                                   ctx.axes.quadrant if ctx.axes else None, extra)


def sector_picks(ctx: Context):
    """(top 3, bottom 3) sectors: average rank of 12-1 momentum and expected return in the scenario priced."""
    v = ctx.sectors
    if v is None or v.table.empty:
        return [], []
    t = v.table.copy()
    rank = t["momentum"].astype(float).rank(ascending=False)
    m = ctx.market
    if m is not None and m.best and m.status(m.best) in ("CONFIRMED", "partial") and m.best in v.scenarios:
        rank = (rank + v.scenarios[m.best].reindex(t.index).rank(ascending=False)) / 2
    order = rank.sort_values().index
    return list(order[:3]), list(order[-3:][::-1])


def finish(ctx: Context, track_path) -> Context:
    """Log today's forecasts, score the past ones, and check the data's freshness."""
    if track_path is not None:
        def track():
            log = tr.record(track_path, tr.forecasts(ctx.today, ctx.nowcast, ctx.analogs, ctx.model))
            ctx.track_log = log
            ctx.track_scored = tr.score(log, ctx.growth_series.get("cpi"), ctx.market_series.get("equities"))
        _try(ctx, "historial de aciertos", track)
    ctx.health = health(ctx)
    return ctx


def health(ctx: Context) -> pd.DataFrame:
    """Freshness of every series gathered (no extra downloads)."""
    sources = {"FRED mercado": ctx.market_series, "FRED crecimiento": ctx.growth_series,
               "FRED liquidez": ctx.liq_series, "FRED carry del yen": ctx.carry_series}
    if ctx.sectors is not None:
        name = f"Sectores ({ctx.sectors.source})"
        sources[name] = {k: ctx.sectors.prices[k] for k in ctx.sectors.prices}
        sources[name].update({k: pd.Series(dtype=float) for k in ctx.sectors.errors if k not in ctx.sectors.prices})
    if ctx.valuation_series:
        sources["FRED valoración"] = ctx.valuation_series
    if ctx.positioning is not None:
        sources["CFTC posicionamiento"] = {po.CONTRACTS[k][1]: v for k, v in ctx.positioning.history.items()}
        sources["CFTC posicionamiento"].update({po.CONTRACTS[k][1]: pd.Series(dtype=float) for k in ctx.positioning.errors})
    if ctx.metals is not None:
        sources["Metales"] = {f"{k} ({ctx.metals.data.source[k]})": v for k, v in ctx.metals.data.prices.items()}
    if ctx.nowcast is not None:
        sources["FRED energía"] = ctx.nowcast.prices
    if ctx.crypto is not None:
        d = ctx.crypto.data
        sources["Cripto: precios Binance"] = {k: d.prices[k] for k in d.prices}
        other = {"Fear & Greed": d.fear_greed, "Stablecoins (DefiLlama)": d.stablecoins}
        other.update({f"Funding {k}": v for k, v in d.funding.items()})
        other.update({k: pd.Series(dtype=float) for k in d.errors})          # failed sources
        sources["Cripto: otras fuentes"] = other
    if ctx.errors:                                  # whole sections that could not be built
        sources["Secciones del informe"] = {name: pd.Series(dtype=float) for name in ctx.errors}
    return data_health.check(sources, ctx.today)


# ── Objective summary ────────────────────────────────────────────────────────

def _pct(v, spec="+.1f", unit="%"):
    return "n/d" if v is None or pd.isna(v) else f"{format(v, spec)}{unit}"


def energy_alerts_es(prices: Dict[str, pd.Series]) -> List[str]:
    out = []
    for key, limit in cpi_nowcast.ALERT_PRICES.items():
        m = cpi_nowcast.move(prices.get(key, pd.Series(dtype=float)), 30)
        if m and abs(m[2]) >= limit:
            effect = "presión sobre el IPC energético" if m[2] > 0 else "alivio para el IPC energético"
            out.append(f"{cpi_nowcast.PRICES[key][1]} {m[2]:+.1f}% en 30 días ({m[0]:.2f} → {m[1]:.2f}): {effect}")
    return out


def pmi_hints_es(lead: Optional[growth.Leading]) -> List[str]:
    if lead is None:
        return []
    out = []
    if lead.survey_activity_change is not None:
        out.append(f"Pista del PMI: la actividad manufacturera regional "
                   f"{'mejora' if lead.survey_activity_change > 0 else 'empeora'} ({lead.survey_activity_change:+.1f} pts; "
                   f"{'en expansión' if (lead.survey_activity or 0) > 0 else 'en contracción'} en promedio).")
    if lead.survey_prices_change is not None:
        out.append(f"Pista de precios del PMI: los precios pagados "
                   f"{'suben' if lead.survey_prices_change > 0 else 'bajan'} ({lead.survey_prices_change:+.1f} pts).")
    if out:
        out.append("Solo dirección: los índices de S&P Global e ISM no son públicos.")
    return out


def evidence_rows(study: Optional[sector_study.SectorStudy], label: Optional[str]) -> pd.DataFrame:
    if study is None or label is None:
        return pd.DataFrame()
    rows = []
    for table, kind in ((study.sectors, "sector"), (study.styles, "estilo")):
        t = table[(table["regime"] == label) & table["evidence"].isin(["robust", "consistent"])]
        for _, r in t.iterrows():
            name = sector_study.sector_label(r["asset"]) if kind == "sector" else french.STYLES.get(r["asset"], r["asset"])
            rows.append({"name": name, "kind": kind, "mean": r["mean_pct"], "hit": r["hit_rate"],
                         "lo": r["ci_low"], "hi": r["ci_high"], "h1": r.get("mean_pct_h1"), "h2": r.get("mean_pct_h2"),
                         "mark": sector_study.MARKERS[r["evidence"]], "n": r["n"]})
    return pd.DataFrame(rows).sort_values("mean", ascending=False) if rows else pd.DataFrame()


def convergence(ctx: Context) -> List[str]:
    a = evidence_rows(ctx.regime_study, ctx.regime)
    b = evidence_rows(ctx.quadrant_study, ctx.axes.quadrant if ctx.axes else None)
    if a.empty or b.empty:
        return []
    out = []
    for _, r in a.iterrows():
        m = b[b["name"] == r["name"]]
        if m.empty:
            continue
        same = np.sign(r["mean"]) == np.sign(m.iloc[0]["mean"])
        side = "favorecido" if r["mean"] > 0 else "rezagado"
        out.append(f"{r['name']}: {side} en el régimen y en el cuadrante (convergencia)" if same else
                   f"{r['name']}: señales opuestas entre régimen y cuadrante (no actuar)")
    return out


def executive(ctx: Context) -> List[str]:
    out = []
    av = ctx.asset_view
    if av is not None and len(av):
        ow = av[av["view"] == "Sobreponderar"]["asset"].tolist()
        uw = av[av["view"] == "Infraponderar"]["asset"].tolist()
        out.append("<b>Visión sistemática:</b> sobreponderar " + (", ".join(ow) or "nada")
                   + "; infraponderar " + (", ".join(uw) or "nada") + ".")
    top, bottom = sector_picks(ctx)
    if top:
        name = lambda k: ctx.sectors.table.at[k, "name"]
        out.append(f"<b>Sectores S&P 500:</b> preferidos {', '.join(f'{name(k)} ({k})' for k in top)}; "
                   f"evitar {', '.join(f'{name(k)} ({k})' for k in bottom)}. Amplitud: "
                   f"{ctx.sectors.breadth:.0%} de los sectores en tendencia alcista; cíclicos vs defensivos "
                   f"{ctx.sectors.rotation_3m:+.1f}% en 3 meses.")
    mv = ctx.metals
    if mv is not None and mv.gold_vs_real:
        g = mv.gold_vs_real
        out.append(f"<b>Oro:</b> {g['gap_pct']:+.0f}% frente a lo que justificaría la tasa real ({g['gap_sd']:+.1f} sd; "
                   f"{'caro' if g['gap_sd'] > 1 else 'barato' if g['gap_sd'] < -1 else 'en línea'} respecto a esa relación)"
                   + (f"; ratio oro/plata {mv.gold_silver.iloc[-1]:.0f}" if mv.gold_silver is not None else "") + ".")
    v = ctx.valuation
    if v is not None:
        n = v.now
        txt = (f"<b>Valoración:</b> PER aproximado {n['pe']:.1f} (percentil {v.percentiles['pe']:.0%} desde 1951), "
               f"indicador Buffett {n['buffett']:.0f}% del PIB (percentil {v.percentiles['buffett']:.0%})")
        if "erp" in n:
            txt += f", prima de riesgo {n['erp']:+.1f} pp (percentil {v.percentiles['erp']:.0%} desde 2003)"
        f5 = v.next5y.get("pe")
        if f5:
            txt += (f". Con valoraciones en este quinto de la historia, la bolsa rindió {f5['same_fifth']:+.1f}% anual "
                    f"los 5 años siguientes (media {f5['all']:+.1f}%)")
        out.append(txt + ".")
    if ctx.positioning is not None and ctx.positioning.alerts():
        out.append("<b>Posicionamiento:</b> " + "; ".join(escape(a) for a in ctx.positioning.alerts()) + ".")
    for a in vw.vix_alert(ctx.vix_term):
        out.append(f"<b>Volatilidad:</b> {escape(a)}.")
    if ctx.regime:
        top = max(ctx.components.items(), key=lambda kv: kv[1]) if ctx.components else None
        out.append(f"<b>Riesgo:</b> régimen <b>{REGIME_ES.get(ctx.regime, ctx.regime)}</b> con vulnerabilidad "
                   f"{ctx.vulnerability:.2f} (0 calma, 1 estrés)"
                   + (f"; el componente más tenso es {COMPONENT_ES.get(top[0], top[0])} ({top[1]:.2f})." if top else "."))
    m = ctx.market
    if m is not None and m.best is not None:
        match, against = m.conditions(m.best)
        out.append(f"<b>Mercado:</b> el escenario que mejor encaja es <b>{SCEN_ES[m.best]}</b> "
                   f"(puntaje {m.scores[m.best]:+.2f}, {STATUS_ES[m.status(m.best)]}). "
                   f"Lo confirman {len(match)} de {len(match) + len(against)} movimientos.")
        d = m.decomposition
        if d:
            driver = "la tasa real" if abs(d["real10"]) >= abs(d["breakeven10"]) else "la inflación esperada"
            out.append(f"<b>Tasas:</b> el 10 años se movió {d['y10']:+.0f} pb en {m.window} días, sobre todo por "
                       f"{driver} (real {d['real10']:+.0f} pb, breakeven {d['breakeven10']:+.0f} pb).")
    if ctx.axes is not None and ctx.axes.quadrant:
        agree = ""
        if m is not None and m.best is not None and m.status(m.best) in ("CONFIRMED", "partial"):
            agree = (" Coincide con lo que descuenta el mercado: lectura más sólida." if m.best == ctx.axes.quadrant else
                     f" <b>No coincide</b> con el mercado ({SCEN_ES[m.best]}): o los datos están por girar o el mercado exagera.")
        out.append(f"<b>Datos macro:</b> cuadrante <b>{QUAD_ES[ctx.axes.quadrant]}</b> "
                   f"(crecimiento {ctx.axes.growth:+.2f}, inflación {ctx.axes.inflation:+.2f}).{agree}")
    nc = ctx.nowcast
    if nc is not None and nc.months:
        mo = nc.months[0]
        t = nc.table[nc.table["month"] == mo].set_index("item")
        head = t.loc["headline"]
        items = t[t["kind"] == "nowcast"].drop(index=["energy", "headline"], errors="ignore")
        driver = items["contribution"].abs().idxmax() if len(items) else None
        ann = cpi_nowcast.annualized(head["mm"])
        out.append(f"<b>Inflación:</b> IPC estimado de {mo.strftime('%m/%Y')} {head['mm']:+.2f}% m/m "
                   f"(rango {head['low']:+.2f} a {head['high']:+.2f}; {ann:+.1f}% anualizado frente al 2% de la Fed)"
                   + (f"; la subpartida energética que más aporta es {LABEL_ES.get(driver, driver).lower()} "
                      f"({t.loc[driver, 'contribution']:+.2f} pts)." if driver else "."))
    lead = ctx.leading
    if lead is not None:
        bits = []
        if lead.sahm is not None:
            bits.append(f"regla de Sahm {lead.sahm:.2f} ({'señal de recesión' if lead.sahm >= growth.SAHM_RECESSION else 'sin señal de recesión'})")
        if lead.survey_activity_change is not None:
            bits.append(f"encuestas manufactureras regionales {'mejoran' if lead.survey_activity_change > 0 else 'empeoran'} "
                        f"({lead.survey_activity_change:+.1f} pts)")
        if lead.survey_prices_change is not None:
            bits.append(f"precios pagados {'al alza' if lead.survey_prices_change > 0 else 'a la baja'}")
        if bits:
            out.append("<b>Crecimiento adelantado:</b> " + "; ".join(bits) + ".")
    if nc is not None:
        alerts = energy_alerts_es(nc.prices)
        wti = nc.prices.get("wti", pd.Series(dtype=float))
        yr = cpi_nowcast.move(wti, 365)
        out.append(f"<b>Energía:</b> WTI {wti.iloc[-1]:.2f} $/bbl" + (f" ({yr[2]:+.0f}% en un año)" if yr else "")
                   + (". Alertas: " + "; ".join(alerts) if alerts else "; sin movimientos bruscos en 30 días."))
    a = ctx.analogs
    if a is not None and len(a.table):
        med6 = a.table["mkt_6m"].median()
        out.append(f"<b>Análogos históricos:</b> los {len(a.table)} meses más parecidos son "
                   f"{', '.join(a.table['month'])}. Seis meses después, la bolsa rindió en mediana {_pct(med6)} "
                   f"(media de todos los meses: {_pct(a.baseline.get('mkt_6m'))}); caída máxima media "
                   f"{_pct(a.table['dd_6m'].mean())}.")
    mdl = ctx.model
    if mdl is not None:
        if mdl.useful:
            out.append(f"<b>Modelo predictivo:</b> R² fuera de muestra {mdl.oos_r2:+.3f} (modesto, pero mejor que la media). "
                       f"Estima {mdl.forecast:+.1f}% de exceso de rentabilidad de la bolsa a 3 meses (media histórica "
                       f"{mdl.baseline:+.1f}%; error típico ±{mdl.error_band:.1f}%).")
        else:
            out.append(f"<b>Modelo predictivo:</b> R² fuera de muestra {mdl.oos_r2:+.3f}: <b>no supera a la media "
                       f"histórica, así que su previsión ({mdl.forecast:+.1f}% a 3 meses) no debe usarse</b>.")
    for line in convergence(ctx):
        out.append(f"<b>Sectores:</b> {escape(line)}.")
    c = ctx.crypto
    if c is not None and "BTC" in c.table.index:
        b = c.table.loc["BTC"]
        out.append(f"<b>Cripto:</b> régimen <b>{cr.REGIME_ES.get(c.regime, 'n/d')}</b> desde {c.regime_since}; BTC "
                   f"{b['r30']:+.1f}% en 30 días, {b['dd_1y']:+.0f}% desde su máximo de 12 meses"
                   + (f", correlación con el Nasdaq {b['corr_nasdaq']:+.2f}" if pd.notna(b["corr_nasdaq"]) else "")
                   + (f". {len(c.alerts)} alerta(s): " + "; ".join(escape(a) for a in c.alerts[:3]) if c.alerts else
                      ". Sin alertas de apalancamiento ni sentimiento") + ".")
    lq_ = ctx.liquidity
    if lq_ is not None:
        exp = lq_.expect.iloc[-1] if len(lq_.expect) else None
        out.append(f"<b>Liquidez de la Fed:</b> {lq_.net.iloc[-1] / 1000:,.2f} billones de dólares netos, "
                   f"{_pct(lq_.change_13w, '+,.0f', ' mil millones')} en 13 semanas"
                   + (f"; el mercado descuenta {'subidas' if exp > 15 else 'bajadas' if exp < -15 else 'tipos estables'} "
                      f"(2 años − fed funds {exp:+.0f} pb)" if exp is not None else "") + ".")
    cy_ = ctx.carry
    if cy_ is not None:
        msg = (f"<b>Carry del yen:</b> USDJPY {cy_.usdjpy.iloc[-1]:.2f}, {cy_.change_10d:+.1f}% en 10 días, "
               f"volatilidad en el percentil {cy_.vol_pct:.0%} de 3 años")
        out.append(msg + (". <b>Señal de desmontaje del carry</b>: riesgo para Nasdaq y cripto." if cy_.unwind_now else "."))
    h = ctx.health
    if h is not None and len(h):
        bad = h[h["status"] != "ok"]
        out.append(f"<b>Datos:</b> {int((h['status'] == 'ok').sum())} de {len(h)} series al día"
                   + (f"; revisar: {escape(', '.join(bad['series'].astype(str).head(6)))}" if len(bad) else "") + ".")
    return out


def watch_list(ctx: Context) -> List[str]:
    m = ctx.market
    if m is None or m.best is None:
        return []
    out = []
    for var, sign in ms.SCENARIOS[m.best][1].items():
        if var not in m.moves.index or pd.isna(m.moves.at[var, "z"]):
            continue
        z = float(m.moves.at[var, "z"])
        need = "subiendo" if sign > 0 else "bajando"
        state = "✔" if sign * z > 0 else "✘"
        out.append(f"{state} {MOVE_ES.get(var, var)}: el escenario necesita que siga {need} (hoy {z:+.1f} sd)")
    if len(m.scores) > 1:
        alt = m.scores.index[1]
        out.append(f"Alternativa más cercana: {SCEN_ES[alt]} (puntaje {m.scores[alt]:+.2f}).")
    return out


# ── HTML ─────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1c1e;--muted:#6b6b70;--line:#e3e3e0;--accent:#2563eb;--good:#059669;--bad:#dc2626;--band:rgba(220,38,38,.07)}
@media (prefers-color-scheme:dark){:root{--bg:#121214;--card:#1c1c1f;--ink:#ececee;--muted:#9a9aa1;--line:#2e2e33;--accent:#60a5fa;--good:#34d399;--bad:#f87171;--band:rgba(248,113,113,.10)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:18px 0 8px}.muted{color:var(--muted)}.sub{color:var(--muted);margin:0 0 20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}.kpi .l{font-size:12px;color:var(--muted)}.kpi .v{font-size:18px;font-weight:600;margin-top:2px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
ul.exec{margin:0;padding-left:18px}ul.exec li{margin:6px 0}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:12px}
figure.chart{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:0;padding:10px 10px 4px}figure.chart figcaption{font-weight:600;font-size:14px;margin:0 4px 4px}
svg{width:100%;height:auto;display:block}svg .grid{stroke:var(--line);stroke-width:1}svg .zero{stroke:var(--muted);stroke-dasharray:4 3}
svg .tick,svg .legend{fill:var(--muted);font-size:11px}svg .legend{fill:var(--ink)}svg .band{fill:var(--band)}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:500}.tw{overflow-x:auto}
.pos{color:var(--good)}.neg{color:var(--bad)}.tag{display:inline-block;border-radius:6px;padding:1px 7px;font-size:12px;border:1px solid var(--line)}
.tag.ok{border-color:var(--good);color:var(--good)}.tag.no{border-color:var(--bad);color:var(--bad)}.err{color:var(--bad)}
footer{margin-top:40px;font-size:12.5px;color:var(--muted)}
"""


def _cls(v) -> str:
    return "" if v is None or pd.isna(v) else ("pos" if v > 0 else "neg" if v < 0 else "")


def _num(v, spec="+.2f", unit="") -> str:
    return '<span class="muted">n/d</span>' if v is None or pd.isna(v) else f'<span class="{_cls(v)}">{format(v, spec)}{unit}</span>'


def _table(headers: List[str], rows: List[List[str]]) -> str:
    h = "".join(f"<th>{escape(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>'


def _kpi(label: str, value: str) -> str:
    return f'<div class="kpi"><div class="l">{escape(label)}</div><div class="v">{value}</div></div>'


def _missing(ctx: Context, name: str) -> str:
    return f'<p class="err">Sección no disponible: {escape(ctx.errors.get(name, "sin datos"))}</p>'


def _last_years(s: pd.Series, years: int) -> pd.Series:
    return s[s.index >= s.index[-1] - pd.DateOffset(years=years)] if len(s) else s


def kpis(ctx: Context) -> str:
    snap = ctx.snap
    y10 = snap.yields.y10 if snap is not None and snap.yields else None
    vix = snap.volatility.vix_spot if snap is not None and snap.volatility else None
    hy = snap.credit.hy_spread_bps if snap is not None and snap.credit else None
    m = ctx.market
    nc = ctx.nowcast
    head = nc.value(nc.months[0], "headline") if nc is not None and nc.months else None
    wti = nc.prices.get("wti") if nc is not None else None
    cards = [
        _kpi("Régimen de riesgo", escape(REGIME_ES.get(ctx.regime, "n/d"))),
        _kpi("Vulnerabilidad (0–1)", _num(ctx.vulnerability, ".2f")),
        _kpi("Escenario de mercado", escape(SCEN_ES.get(m.best, "n/d")) + f'<br><span class="tag">{STATUS_ES[m.status(m.best)]} {m.scores[m.best]:+.2f}</span>'
             if m is not None and m.best else "n/d"),
        _kpi("Cuadrante macro", escape(QUAD_ES.get(ctx.axes.quadrant, "n/d")) if ctx.axes else "n/d"),
        _kpi(f"IPC estimado {nc.months[0].strftime('%m/%Y')}" if head is not None else "IPC estimado", _num(head, "+.2f", "% m/m")),
        _kpi("Bono 10 años", _num(y10, ".2f", "%")),
        _kpi("VIX", _num(vix, ".1f")),
        _kpi("Spread HY", _num(hy, ".0f", " pb")),
        _kpi("WTI", _num(float(wti.iloc[-1]) if wti is not None and len(wti) else None, ".2f", " $")),
        _kpi("Bitcoin", _num(ctx.btc, ",.0f", " $")),
    ]
    return f'<div class="kpis">{"".join(cards)}</div>'


def section_market(ctx: Context) -> str:
    m = ctx.market
    if m is None:
        return _missing(ctx, "mercado")
    rows = [[escape(MOVE_ES.get(k, r["label"])), _num(r["change"], "+.0f" if r["unit"] == "bps" else "+.1f",
                                                       " pb" if r["unit"] == "bps" else "%" if r["unit"] == "%" else " pts"),
             _num(r["z"], "+.1f", " sd")] for k, r in m.moves.iterrows()]
    scen = [[escape(SCEN_ES[k]), _num(m.scores[k]),
             f'<span class="tag {"ok" if m.status(k) == "CONFIRMED" else "no" if m.status(k) == "invalidated" else ""}">'
             f'{STATUS_ES[m.status(k)]}</span>'] for k in m.scores.index]
    hist = ""
    if ctx.score_hist is not None and len(ctx.score_hist):
        hist = charts.line_chart({SCEN_ES[c].split(" /")[0].split(" con")[0]: ctx.score_hist[c] for c in ctx.score_hist},
                                 "Puntaje de cada escenario, último año", zero=True, note="−1 a +1")
    s = ctx.market_series
    rates = charts.line_chart({"10 años": _last_years(s.get("y10", pd.Series(dtype=float)), 2),
                               "Real (TIPS)": _last_years(s.get("real10", pd.Series(dtype=float)), 2),
                               "Breakeven": _last_years(s.get("breakeven10", pd.Series(dtype=float)), 2)},
                              "Tasas: nominal = real + inflación esperada", unit="%")
    watch = "".join(f"<li>{escape(w)}</li>" for w in watch_list(ctx))
    return (f'<div class="grid2"><div class="card"><h3>Movimientos de {m.window} días</h3>'
            f'{_table(["Variable", "Cambio", "vs normal"], rows)}</div>'
            f'<div class="card"><h3>Escenarios</h3>{_table(["Escenario", "Puntaje", "Estado"], scen)}'
            f'<h3>Qué vigilar</h3><ul>{watch}</ul></div></div>'
            f'<div class="grid2">{hist}{rates}</div>')


def section_macro(ctx: Context) -> str:
    if ctx.axes is None:
        return _missing(ctx, "macro")
    gs = ctx.growth_series
    axes_chart = charts.line_chart(dict(ctx.axes_hist.items()), "Ejes macro (15 años, con datos publicados en cada fecha)",
                                   zero=True, note="> 0: acelera") if ctx.axes_hist is not None else ""
    yoy = lambda s: _last_years((s / s.shift(12) - 1) * 100, 10) if len(s) else s
    cpi_chart = charts.line_chart({"IPC general": yoy(growth._monthly(gs["cpi"]).dropna()),
                                   "Subyacente": yoy(growth._monthly(gs["core"]).dropna())},
                                  "IPC interanual (10 años)", unit="%")
    claims = gs.get("claims", pd.Series(dtype=float))
    claims_chart = charts.line_chart({"Media 4 semanas": _last_years(claims.rolling(4).mean() / 1000, 5)},
                                     "Peticiones de subsidio por desempleo (miles)", unit="k")
    rows = []
    for key in ("wei", "gdpnow", "sahm", "philly_activity", "philly_orders", "philly_prices",
                "empire_activity", "empire_orders", "empire_prices"):
        s = gs.get(key, pd.Series(dtype=float))
        if len(s) >= 2:
            rows.append([escape(LEADING_ES[key]), _num(float(s.iloc[-1]), "+.2f"),
                         _num(float(s.iloc[-1] - s.iloc[-2]), "+.2f"), s.index[-1].strftime("%d/%m/%Y")])
    hints = "".join(f"<li>{escape(h)}</li>" for h in pmi_hints_es(ctx.leading))
    ax = ctx.axes
    parts = [[escape(SIGNAL_ES[k]), _num(ax.parts[k])] for k in (*growth.GROWTH, *growth.INFLATION)]
    nca = (f"<p>Eje de inflación si el próximo IPC sale como estima el nowcast: {_num(ctx.nowcast_axis)}</p>"
           if ctx.nowcast_axis is not None else "")
    return (f'<div class="grid2"><div class="card"><h3>Cuadrante: {escape(QUAD_ES.get(ax.quadrant, "n/d"))}</h3>'
            f'<p>Crecimiento {_num(ax.growth)} · Inflación {_num(ax.inflation)}</p>{nca}'
            f'{_table(["Señal (ritmo reciente vs año)", "Valor"], parts)}</div>'
            f'<div class="card"><h3>Indicadores adelantados</h3>{_table(["Indicador", "Último", "Cambio", "Fecha"], rows)}'
            f'<ul>{hints}</ul></div></div>'
            f'<div class="grid2">{axes_chart}{cpi_chart}{claims_chart}</div>')


def section_energy(ctx: Context) -> str:
    nc = ctx.nowcast
    if nc is None:
        return _missing(ctx, "ipc")
    out = ""
    if nc.months:
        mo = nc.months[0]
        t = nc.table[nc.table["month"] == mo]
        rows = [[("&nbsp;&nbsp;" if r["item"] in cpi_nowcast.ITEMS else "") + escape(LABEL_ES.get(r["item"], r["label"])),
                 "" if pd.isna(r["weight"]) else f"{r['weight']:.0%}", _num(r["mm"], "+.2f", "%"),
                 "3m" if r["kind"] == "trend" else (f"{r['low']:+.2f} a {r['high']:+.2f}" if pd.notna(r["low"]) else ""),
                 _num(r["contribution"], "+.2f") if pd.notna(r["contribution"]) else "",
                 _num(r["yoy"], "+.1f", "%") if r["yoy"] is not None and pd.notna(r["yoy"]) else "n/d"]
                for _, r in t.iterrows()]
        out += (f'<div class="card"><h3>IPC estimado de {mo.strftime("%m/%Y")} '
                f'({nc.observed_share.get(mo, 1):.0%} del mes observado)</h3>'
                f'{_table(["Partida", "Peso", "m/m", "Rango 90%", "Aporte (pts)", "a/a"], rows)}</div>')
    stats = nc.stats
    if len(stats):
        rows = [[escape(LABEL_ES.get(k, k)), f"{stats.loc[k, 'mae']:.2f}%", f"{stats.loc[k, 'naive_mae']:.2f}%",
                 f'<span class="tag {"ok" if stats.loc[k, "mae"] < stats.loc[k, "naive_mae"] else "no"}">'
                 f'{"sí" if stats.loc[k, "mae"] < stats.loc[k, "naive_mae"] else "no"}</span>']
                for k in (*cpi_nowcast.ITEMS, "energy", "headline") if k in stats.index]
        out += f'<div class="card"><h3>Validación desde 2010</h3>{_table(["Partida", "Error modelo", "Error ingenuo", "¿Mejor?"], rows)}</div>'
    p = nc.prices
    two = lambda k: _last_years(p.get(k, pd.Series(dtype=float)), 2)
    out += '<div class="grid2">' + charts.line_chart({"WTI": two("wti"), "Brent": two("brent")}, "Crudo ($/bbl)", unit=" $") \
        + charts.line_chart({"Gasolina surtidor": two("gasoline_retail"), "Diésel surtidor": two("diesel_retail")},
                            "Precio al consumidor ($/galón)", unit=" $") \
        + charts.line_chart({"Margen 3-2-1": _last_years(cpi_nowcast.crack_321(p), 2)}, "Margen de refino ($/bbl)", unit=" $") \
        + charts.line_chart({"Henry Hub": two("henry_hub")}, "Gas natural ($/MMBtu)", unit=" $") + "</div>"
    return out


def section_risk(ctx: Context) -> str:
    if ctx.regime is None:
        return _missing(ctx, "riesgo")
    s = ctx.market_series
    comps = "".join(_kpi(COMPONENT_ES.get(k, k).capitalize(), _num(v, ".2f")) for k, v in ctx.components.items())
    vt = ctx.vix_term
    if vt is not None and len(vt):
        comps += _kpi("VIX / VIX 3 meses", f"{vt['ratio'].iloc[-1]:.2f} " + ('<span class="tag no">invertida</span>'
                      if vt["ratio"].iloc[-1] >= 1 else '<span class="tag ok">normal</span>'))
    hy = s.get("credit", pd.Series(dtype=float)) * 100
    risk = charts.line_chart({"VIX": _last_years(s.get("vix", pd.Series(dtype=float)), 2),
                              "Spread HY (pb/10)": _last_years(hy / 10, 2)}, "Volatilidad y crédito")
    norm = lambda x: _last_years(x, 2) / _last_years(x, 2).iloc[0] * 100 if len(x) else x
    eq = charts.line_chart({"S&P 500": norm(s.get("equities", pd.Series(dtype=float))),
                            "Nasdaq": norm(s.get("nasdaq", pd.Series(dtype=float)))}, "Bolsa (base 100 hace 2 años)")
    term = charts.line_chart({"VIX / VIX 3m": _last_years(vt["ratio"], 2)}, "Estructura temporal del VIX",
                             note="> 1: estrés inmediato") if vt is not None and len(vt) else ""
    return f'<div class="kpis">{comps}</div><div class="grid2">{risk}{eq}{term}</div>'


def section_evidence(ctx: Context) -> str:
    def block(study, label, title):
        rows = evidence_rows(study, label)
        if study is None:
            return f'<div class="card"><h3>{escape(title)}</h3><p class="muted">No disponible.</p></div>'
        if rows.empty:
            return f'<div class="card"><h3>{escape(title)}</h3><p>Ningún resultado robusto: la evidencia no apoya inclinaciones.</p></div>'
        body = [[escape(f"{r['mark']} {r['name']}"), _num(r["mean"], "+.2f", "%"), f"{r['hit']:.0%}",
                 f"{r['lo']:+.2f} a {r['hi']:+.2f}", f"{r['h1']:+.2f} / {r['h2']:+.2f}"] for _, r in rows.iterrows()]
        return (f'<div class="card"><h3>{escape(title)}</h3>'
                f'{_table(["Activo (vs mercado)", "Media mes sig.", "Acierto", "IC 95%", "Mitades"], body)}</div>')
    conv = "".join(f"<li>{escape(c)}</li>" for c in convergence(ctx))
    return ('<div class="grid2">' + block(ctx.regime_study, ctx.regime, f"Régimen: {REGIME_ES.get(ctx.regime, 'n/d')} (desde 1990)")
            + block(ctx.quadrant_study, ctx.axes.quadrant if ctx.axes else None,
                    f"Cuadrante: {QUAD_ES.get(ctx.axes.quadrant, 'n/d') if ctx.axes else 'n/d'} (desde 1972)") + "</div>"
            + (f'<div class="card"><h3>Convergencia régimen × cuadrante</h3><ul>{conv}</ul></div>' if conv else "")
            + '<p class="muted">** robusto (claro en toda la muestra y en cada mitad) · * coherente (claro en total, '
              'mismo signo en ambas mitades). Asociación histórica, no previsión.</p>')


def section_history(ctx: Context) -> str:
    a, mdl = ctx.analogs, ctx.model
    if a is None:
        return _missing(ctx, "análogos y modelo")
    feats = list(an.FEATURES)
    head = ["Mes", "Distancia"] + [an.FEATURES[f] for f in feats] + ["Bolsa 3m", "Bolsa 6m", "Bolsa 12m", "Caída máx. 6m", "Δ10a 6m (pb)", "WTI 6m"]
    fmt = {"vix": ".1f", "curve": "+.0f", "baa": ".2f", "y10_12m": "+.0f", "oil_12m": "+.0f", "growth": "+.2f", "inflation": "+.2f"}
    today_row = ["<b>Hoy</b>", ""] + [_num(a.today.get(f), fmt[f]) for f in feats] + [""] * 6
    rows = [today_row] + [[r["month"], f"{r['distance']:.2f}"] + [_num(r[f], fmt[f]) for f in feats]
                          + [_num(r["mkt_3m"], "+.1f", "%"), _num(r["mkt_6m"], "+.1f", "%"), _num(r["mkt_12m"], "+.1f", "%"),
                             _num(r["dd_6m"], "+.1f", "%"), _num(r["y10_6m"], "+.0f"), _num(r["oil_6m"], "+.0f", "%")]
                          for _, r in a.table.iterrows()]
    b = a.baseline
    rows.append(["<i>Mediana análogos</i>", ""] + [""] * len(feats)
                + [_num(a.table[c].median(), "+.1f" if c != "y10_6m" else "+.0f", "%" if c != "y10_6m" else "")
                   for c in ("mkt_3m", "mkt_6m", "mkt_12m", "dd_6m", "y10_6m", "oil_6m")])
    rows.append(["<i>Todos los meses</i>", ""] + [""] * len(feats)
                + [_num(b.get(c), "+.1f" if c != "y10_6m" else "+.0f", "%" if c != "y10_6m" else "")
                   for c in ("mkt_3m", "mkt_6m", "mkt_12m", "dd_6m", "y10_6m", "oil_6m")])
    sect = ("<p>Sectores tras los análogos (6 meses, vs mercado): mejores "
            + ", ".join(f"{escape(n)} {_num(v, '+.1f', '%')}" for n, v in a.sectors_best)
            + "; peores " + ", ".join(f"{escape(n)} {_num(v, '+.1f', '%')}" for n, v in a.sectors_worst) + ".</p>")
    out = f'<div class="card"><h3>Meses más parecidos a hoy (desde 1990)</h3>{_table(head, rows)}{sect}</div>'
    if mdl is not None:
        verdict = ('<span class="tag ok">supera a la media</span>' if mdl.useful else
                   '<span class="tag no">no supera a la media: no usar</span>')
        coef = [[escape(an.FEATURES.get(k, k)), _num(v, "+.2f")] for k, v in sorted(mdl.coefficients.items(), key=lambda kv: -abs(kv[1]))]
        out += (f'<div class="grid2"><div class="card"><h3>Modelo predictivo (ridge, bolsa a 3 meses) {verdict}</h3>'
                f'<p>R² fuera de muestra <b>{mdl.oos_r2:+.3f}</b> en {mdl.n_oos} meses desde 2005 · acierto de signo '
                f'{mdl.hit_rate:.0%} · error medio ±{mdl.error_band:.1f}%</p>'
                f'<p>Previsión: exceso de rentabilidad {_num(mdl.forecast, "+.1f", "%")} a 3 meses '
                f'(media histórica {_num(mdl.baseline, "+.1f", "%")}).</p>'
                f'<p class="muted">Cada mes se reestima solo con datos anteriores. Un R² ≤ 0 significa que la media '
                f'histórica predice mejor que el modelo.</p></div>'
                f'<div class="card"><h3>Peso de cada variable (estandarizado)</h3>{_table(["Variable", "Coeficiente"], coef)}'
                f'<p class="muted">Positivo: valores altos se asocian a mayor rentabilidad posterior.</p></div></div>')
    return out


def section_crypto(ctx: Context) -> str:
    c = ctx.crypto
    if c is None:
        return _missing(ctx, "cripto")
    d, t = c.data, c.table
    cards = [_kpi("Régimen cripto", escape(cr.REGIME_ES.get(c.regime, "n/d")) + f'<br><span class="muted">desde {c.regime_since}</span>')]
    if d.dominance:
        cards.append(_kpi("Dominancia BTC", f"{d.dominance['btc']:.1f}%"))
    if len(d.fear_greed):
        cards.append(_kpi("Fear & Greed (0–100)", f"{d.fear_greed.iloc[-1]:.0f}"))
    if len(d.stablecoins):
        cards.append(_kpi("Stablecoins", f"{d.stablecoins.iloc[-1] / 1e9:,.0f} mil M$ "
                          f"<span class='{_cls(cr._ret(d.stablecoins, 30))}'>{cr._ret(d.stablecoins, 30):+.1f}% 30d</span>"))
    rows = [[f"<b>{escape(sym)}</b> <span class='muted'>{escape(r['segment'])}</span>",
             cr.fmt_price(r["price"]), _num(r["r7"], "+.1f", "%"), _num(r["r30"], "+.1f", "%"), _num(r["r90"], "+.1f", "%"),
             _num(r["r365"], "+.0f", "%"), f"{r['vol30']:.0f}%", _num(r["dd_1y"], "+.0f", "%"),
             "" if pd.isna(r["beta_btc"]) else f"{r['beta_btc']:.2f}", _num(r["corr_nasdaq"], "+.2f"),
             _num(r["funding"], "+.0f", "%"), _num(r["oi_30d"], "+.0f", "%")] for sym, r in t.iterrows()]
    table = _table(["Moneda", "Precio $", "7d", "30d", "90d", "1 año", "Vol. 30d", "vs máx. 1a", "Beta BTC",
                    "Corr. Nasdaq", "Funding anual", "Interés abierto 30d"], rows)
    al = "".join(f"<li>{escape(a)}</li>" for a in c.alerts) or "<li>Sin alertas.</li>"

    def ev(tab, label, title):
        s = cr.supported(tab, label)
        if s.empty:
            return f'<div class="card"><h3>{escape(title)}</h3><p>Ningún resultado robusto: la evidencia no apoya inclinaciones.</p></div>'
        body = [[escape(f"{'**' if r['evidence'] == 'robust' else '*'} {r['asset']}"), _num(r["mean_pct"], "+.2f", "%"),
                 f"{r['hit_rate']:.0%}", f"{r['ci_low']:+.2f} a {r['ci_high']:+.2f}", str(int(r["n"]))] for _, r in s.iterrows()]
        return f'<div class="card"><h3>{escape(title)}</h3>{_table(["Moneda", "Media 30d", "Acierto", "IC 95%", "Ventanas"], body)}</div>'
    q = ctx.axes.quadrant if ctx.axes else None
    p = d.prices[d.prices.index > d.prices.index[-1] - pd.Timedelta(days=365)]
    base = lambda x: x.dropna() / x.dropna().iloc[0] * 100 if len(x.dropna()) else x
    perf = charts.line_chart({k: base(p[k]) for k in ("BTC", "ETH", "SOL", "AAVE", "ONDO") if k in p},
                             "Rendimiento 1 año (base 100)")
    fg = charts.line_chart({"Fear & Greed": _last_years(d.fear_greed, 1)}, "Sentimiento (0 miedo – 100 codicia)") if len(d.fear_greed) else ""
    st = charts.line_chart({"Stablecoins": _last_years(d.stablecoins / 1e9, 2)}, "Oferta de stablecoins (miles de millones $)") if len(d.stablecoins) else ""
    fund = ""
    if "BTC" in d.funding:
        f = d.funding["BTC"]
        fund = charts.line_chart({"BTC": f.groupby(f.index.normalize()).mean() * 3 * 365 * 100},
                                 "Funding de BTC (% anualizado)", zero=True, note="> 30%: largos en exceso")
    errs = (f'<p class="muted">Fuentes no disponibles: {escape("; ".join(sorted(d.errors)))}</p>' if d.errors else "")
    return (f'<div class="kpis">{"".join(cards)}</div><div class="card">{table}{errs}</div>'
            f'<div class="card"><h3>Alertas cripto</h3><ul>{al}</ul></div>'
            f'<div class="grid2">{ev(c.by_regime, c.regime, f"Frente a BTC, 30 días, en régimen {cr.REGIME_ES.get(c.regime, chr(45))}")}'
            f'{ev(c.by_quadrant, q, f"BTC y ETH, 30 días, en cuadrante {QUAD_ES.get(q, chr(45))}")}</div>'
            f'<div class="grid2">{perf}{fg}{st}{fund}</div>')


def section_liquidity(ctx: Context) -> str:
    l = ctx.liquidity
    if l is None:
        return _missing(ctx, "liquidez")
    exp = l.expect.iloc[-1] if len(l.expect) else None
    cards = [_kpi("Liquidez neta Fed", f"{l.net.iloc[-1] / 1000:,.2f} billones $"),
             _kpi("Cambio 4 semanas", _num(l.change_4w, "+,.0f", " mil M$")),
             _kpi("Cambio 13 semanas", _num(l.change_13w, "+,.0f", " mil M$")),
             _kpi("2 años − fed funds", _num(exp, "+.0f", " pb"))]
    links = "".join(f"<li>{escape(k)}: correlación {v:+.2f}</li>" for k, v in l.links.items())
    return (f'<div class="kpis">{"".join(cards)}</div>'
            + (f'<div class="card"><h3>Relación con la liquidez (cambios de 13 semanas, 6 años)</h3><ul>{links}</ul>'
               '<p class="muted">Cerca de 0: la liquidez apenas explica ese activo en este periodo.</p></div>' if links else "")
            + '<div class="grid2">' + charts.line_chart({"Liquidez neta": l.net / 1000}, "Liquidez neta de la Fed (billones $)",
                                                        note="balance − cuenta del Tesoro − repos inversos")
            + charts.line_chart({"2 años − fed funds": _last_years(l.expect, 3)}, "Expectativas de tipos (pb)", zero=True,
                                note="> 0 subidas, < 0 bajadas") + "</div>")


def section_health(ctx: Context) -> str:
    h = ctx.health
    if h is None or h.empty:
        return '<p class="muted">Sin información.</p>'
    status = {"ok": '<span class="tag ok">al día</span>', "stale": '<span class="tag no">desactualizada</span>',
              "failed": '<span class="tag no">falló</span>'}
    rows = [[escape(str(r["source"])), escape(str(r["series"])), str(r["last"] or "—"),
             "" if r["age"] is None or pd.isna(r["age"]) else str(int(r["age"])), escape(r["freq"]), status[r["status"]]]
            for _, r in h.iterrows()]
    bad = int((h["status"] != "ok").sum())
    head = (f"<p><b>{len(h) - bad} de {len(h)}</b> series al día" + (f"; <b>{bad}</b> con problemas (arriba en la tabla)." if bad else ".")
            + "</p>")
    return (f'<div class="card">{head}<details{" open" if bad else ""}><summary>Ver todas las series</summary>'
            f'{_table(["Fuente", "Serie", "Último dato", "Días", "Frecuencia", "Estado"], rows)}</details></div>')


def section_carry(ctx: Context) -> str:
    c = ctx.carry
    if c is None:
        return _missing(ctx, "carry del yen")
    cards = [_kpi("USDJPY", f"{c.usdjpy.iloc[-1]:.2f}"), _kpi("Cambio 10 días", _num(c.change_10d, "+.1f", "%")),
             _kpi("Volatilidad 20 días", f"{c.vol:.1f}% <span class='muted'>p{c.vol_pct * 100:.0f}</span>"),
             _kpi("Diferencial 3m EE. UU. − Japón", _num(c.differential, "+.2f", " pp")),
             _kpi("Carry / riesgo", _num(c.carry_to_risk, ".2f")),
             _kpi("Señal de desmontaje", '<span class="tag no">activa</span>' if c.unwind_now else '<span class="tag ok">no</span>')]
    rows = [[escape(k), str(int(h["n"])), _num(h["mean_after"], "+.1f", "%"), _num(h["median_after"], "+.1f", "%"),
             f"{h['hit_down']:.0%}", _num(h["mean_all"], "+.1f", "%")] for k, h in c.history.items()]
    hist = (f'<div class="card"><h3>Qué pasó tras subidas bruscas del yen (≥ 3% en 10 días) desde 2000</h3>'
            f'{_table(["Activo", "Episodios", "Media 30d", "Mediana 30d", "Cayó", "Media de cualquier 30d"], rows)}'
            f'<p class="muted">Últimos episodios: {", ".join(str(d.date()) for d in c.last_unwinds)}</p></div>') if rows else ""
    return (f'<div class="kpis">{"".join(cards)}</div>{hist}<div class="grid2">'
            + charts.line_chart({"USDJPY": _last_years(c.usdjpy, 3)}, "Dólar/yen (3 años)", note="bajadas = yen más fuerte")
            + "</div>")


def section_track(ctx: Context) -> str:
    if ctx.track_log is None:
        return '<p class="muted">Se activa al generar el informe con el comando `report`.</p>'
    lines = "".join(f"<li>{escape(x)}</li>" for x in tr.summary(ctx.track_scored, ctx.track_log))
    sc = ctx.track_scored
    table = ""
    if sc is not None and len(sc):
        names = {"cpi": "IPC m/m", "analogs": "Bolsa 6m (análogos)", "model": "Bolsa 3m (modelo)"}
        rows = [[escape(r["run_date"]), escape(names.get(r["kind"], r["kind"])), escape(str(r["target"])),
                 _num(r["value"], "+.2f"), _num(r["actual"], "+.2f"), _num(r["error"], "+.2f"),
                 "" if pd.isna(r["inside"]) else ("sí" if r["inside"] else "no")]
                for _, r in sc.sort_values("run_date", ascending=False).head(15).iterrows()]
        table = _table(["Fecha", "Previsión", "Objetivo", "Estimado", "Real", "Error", "En rango"], rows)
    return (f'<div class="card"><ul>{lines}</ul>{table}<p class="muted">Registro: reports\\predicciones.csv. '
            'El IPC se evalúa al publicarse; los análogos a los 6 meses y el modelo a los 3 meses (S&P 500 sin dividendos).</p></div>')


def section_view(ctx: Context) -> str:
    av = ctx.asset_view
    if av is None or av.empty:
        return ""
    sig = lambda v: "" if v is None or pd.isna(v) else ('<span class="pos">▲</span>' if v > 0 else
                                                           '<span class="neg">▼</span>' if v < 0 else "·")
    tag = {"Sobreponderar": "ok", "Infraponderar": "no", "Neutral": ""}
    rows = [[escape(r["asset"]), sig(r["trend"]), sig(r["momentum"]), sig(r["scenario"]), sig(r["quadrant"]),
             sig(r.get("valuation")), sig(r.get("positioning")), _num(r["score"], "+.1f"), f'<span class="tag {tag[r["view"]]}">{r["view"]}</span>', f"{r['conviction']:.0%}"]
            for _, r in av.iterrows()]
    top, bottom = sector_picks(ctx)
    picks = ""
    if top:
        n = lambda k: f"{escape(ctx.sectors.table.at[k, 'name'])} ({k})"
        picks = (f"<p><b>Sectores preferidos:</b> {', '.join(n(k) for k in top)} · <b>Evitar:</b> "
                 f"{', '.join(n(k) for k in bottom)}</p>")
    return (f'<div class="card"><h3>Visión sistemática por activo</h3>'
            f'{_table(["Activo", "Tendencia", "Momentum 3m", "Escenario", "Cuadrante", "Valoración", "Posicionamiento", "Puntos", "Visión", "Convicción"], rows)}'
            f'{picks}<p class="muted">Reglas fijas: ▲/▼ = señal a favor/en contra. Escenario y cuadrante según el manual '
            f'estándar de cada escenario (escenario ×0.5 si es parcial). Valoración: prima de riesgo de la bolsa en su '
            f'20% más bajo (▼) o más alto (▲). Posicionamiento (contrario): especuladores en extremo largo (▼) o corto (▲). '
            f'≥ +2 sobreponderar, ≤ −2 infraponderar. Señal sistemática, no recomendación.</p></div>' + section_agenda(ctx))


def section_agenda(ctx: Context) -> str:
    a = ctx.agenda
    if a is None:
        return ""
    if a.empty:
        return '<div class="card"><h3>Próximos 14 días</h3><p>Sin publicaciones clave.</p></div>'
    tag = {"alto": "no", "medio": "", "bajo": ""}
    rows = [[escape(r["date"]), escape(r["event"]), f'<span class="tag {tag[r["impact"]]}">{r["impact"]}</span>']
            for _, r in a.iterrows()]
    return (f'<div class="card"><h3>Próximos 14 días</h3>{_table(["Fecha", "Publicación", "Impacto"], rows)}'
            '<p class="muted">Calendario de FRED y reuniones de la Fed. El PMI de S&amp;P Global no figura en FRED '
            '(se publica hacia el día 20–23).</p></div>')


def section_valuation(ctx: Context) -> str:
    v = ctx.valuation
    if v is None:
        return _missing(ctx, "valoración")
    n, p = v.now, v.percentiles
    cards = [_kpi("PER aproximado", f"{n['pe']:.1f} <span class='muted'>p{p['pe'] * 100:.0f}</span>"),
             _kpi("Indicador Buffett", f"{n['buffett']:.0f}% PIB <span class='muted'>p{p['buffett'] * 100:.0f}</span>")]
    if "erp" in n:
        cards.append(_kpi("Prima de riesgo", f"{n['erp']:+.1f} pp <span class='muted'>p{p['erp'] * 100:.0f}</span>"))
    rows = [[{"pe": "PER aproximado", "buffett": "Indicador Buffett"}[k], _num(x["same_fifth"], "+.1f", "%"),
             _num(x["all"], "+.1f", "%"), str(int(x["n"]))] for k, x in v.next5y.items()]
    link = (f'<div class="card"><h3>Rentabilidad anual de la bolsa los 5 años siguientes</h3>'
            f'{_table(["Con valoración en el quinto actual de", "Media de esos trimestres", "Media de todos", "Trimestres"], rows)}'
            '<p class="muted">Relación histórica medida desde 1951; no es un objetivo de precio.</p></div>') if rows else ""
    h = v.history
    ch = charts.line_chart({"PER aproximado": h["pe"]}, "PER aproximado desde 1951", note="valor de mercado / beneficios")
    ch += charts.line_chart({"Prima de riesgo (pp)": h["erp"].dropna()}, "Prima de riesgo: rentabilidad de beneficios − tasa real",
                            zero=True, note="baja = bolsa cara frente a bonos")
    return (f'<div class="kpis">{"".join(cards)}</div>{link}<div class="grid2">{ch}</div>'
            f'<p class="muted">Datos trimestrales de la Reserva Federal (cuentas Z.1) y beneficios del BEA hasta {n["quarter"]}, '
            f'llevados a hoy con el S&amp;P 500 ({n["growth_since_quarter"]:+.1f}% desde ese trimestre). Los niveles difieren '
            f'del PER del S&amp;P 500 publicado; lo útil es el percentil.</p>')


def section_positioning(ctx: Context) -> str:
    po_ = ctx.positioning
    if po_ is None:
        return _missing(ctx, "posicionamiento CFTC")
    rows = []
    for k, r in po_.table.iterrows():
        idx = r["cot_index"]
        tag = '<span class="tag no">extremo</span>' if idx >= po.HIGH or idx <= po.LOW else ""
        rows.append([escape(r["label"]), _num(r["net_pct_oi"], "+.1f", "%"), f"{idx:.0f} {tag}",
                     _num(r["change_1w"], "+.1f", " pp"), str(r["date"])])
    al = "".join(f"<li>{escape(a)}</li>" for a in po_.alerts()) or "<li>Ningún extremo.</li>"
    err = f'<p class="muted">Sin datos: {escape(", ".join(sorted(po_.errors)))}</p>' if po_.errors else ""
    return (f'<div class="card">{_table(["Contrato", "Especuladores netos (% interés abierto)", "Índice COT 3 años (0–100)", "Cambio semanal", "Fecha"], rows)}'
            f'<ul>{al}</ul><p class="muted">CFTC, informe de compromisos de operadores (no comerciales), publicado los '
            f'viernes con datos del martes. Extremos (≥ 90 o ≤ 10): señal contraria.</p>{err}</div>')


def section_sectors(ctx: Context) -> str:
    v = ctx.sectors
    if v is None:
        return _missing(ctx, "sectores S&P 500")
    t = v.table
    cards = [_kpi("Amplitud (sectores en tendencia)", f"{v.breadth:.0%}"),
             _kpi("Cíclicos − defensivos (3m)", _num(v.rotation_3m, "+.1f", "%"))]
    has_beta = "beta_y10" in t
    head = ["Sector", "1m", "3m", "6m", "12m", "Tendencia", "Rank momentum"] + (
        ["β 10 años (por 10 pb)", "β WTI (por 10%)", "β dólar (por 1%)", "β HY (por 10 pb)"] if has_beta else [])
    rows = []
    for k, r in t.sort_values("momentum_rank").iterrows():
        row = [f"<b>{k}</b> {escape(r['name'])}", _num(r["r1m"], "+.1f", "%"), _num(r["r3m"], "+.1f", "%"),
               _num(r["r6m"], "+.1f", "%"), _num(r["r12m"], "+.1f", "%"),
               ('<span class="pos">▲</span>' if r["trend_up"] else '<span class="neg">▼</span>')
               if r["trend_up"] is not None else "", f"{r['momentum_rank']:.0f}"]
        if has_beta:
            row += [_num(r["beta_y10"] * 10, "+.2f", "%"), _num(r["beta_oil"] * 10, "+.2f", "%"),
                    _num(r["beta_dollar"], "+.2f", "%"), _num(r["beta_credit"] * 10, "+.2f", "%")]
        rows.append(row)
    out = (f'<div class="kpis">{"".join(cards)}</div><div class="card"><h3>Frente al S&P 500 (SPY)</h3>'
           f'{_table(head, rows)}<p class="muted">Rentabilidad relativa al SPY. β: cuánto rinde el sector frente al '
           f'SPY por cada movimiento del factor, medido en periodos de 20 días durante 5 años.</p></div>')
    if len(v.scenarios):
        names = {**{k: SCEN_ES[k].split(" /")[0] for k in ms.SCENARIOS}, "last_20d": "Últimos 20 días"}
        srows = [[f"<b>{k}</b> {escape(t.at[k, 'name'])}"] + [_num(v.scenarios.at[k, c], "+.1f", "%") for c in v.scenarios]
                 for k in v.scenarios.sort_values(v.scenarios.columns[0], ascending=False).index]
        out += (f'<div class="card"><h3>Mapa de escenarios: rentabilidad esperada frente al SPY</h3>'
                f'{_table(["Sector"] + [names.get(c, c) for c in v.scenarios.columns], srows)}'
                f'<p class="muted">Si el escenario se da con un movimiento típico (1 desviación) de sus variables. '
                f'"Últimos 20 días": con los movimientos reales del último mes.</p></div>')
    p = v.prices
    yr = p[p.index > p.index[-1] - pd.Timedelta(days=365)]
    rel = lambda k: (yr[k] / yr[k].iloc[0]) / (yr["SPY"] / yr["SPY"].iloc[0]) * 100
    top, bottom = sector_picks(ctx)
    cyc = [c for c in sc.CYCLICAL if c in p]
    dfn = [c for c in sc.DEFENSIVE if c in p]
    p2 = p[p.index > p.index[-1] - pd.DateOffset(years=2)]
    norm = lambda cols: (p2[cols] / p2[cols].iloc[0]).mean(axis=1)
    out += '<div class="grid2">' + charts.line_chart({k: rel(k) for k in (top + bottom) if k in yr},
                                                      "Fuerza relativa frente al SPY, 1 año (base 100)", note="preferidos y a evitar")
    if cyc and dfn:
        out += charts.line_chart({"Cíclicos / defensivos": norm(cyc) / norm(dfn) * 100}, "Rotación: cíclicos frente a defensivos (base 100)",
                                 note="sube = apetito por riesgo")
    out += "</div>"
    out += f'<p class="muted">Fuente de precios: {escape(v.source)}.</p>'
    missing = [k for k in v.errors if k not in v.prices]
    if missing:
        out += f'<p class="muted">Sin datos: {escape(", ".join(sorted(missing)))}</p>'
    return out


def section_metals(ctx: Context) -> str:
    mv = ctx.metals
    if mv is None:
        return _missing(ctx, "metales")
    t = mv.table
    rows = [[escape(r["label"]), f"{r['price']:,.2f}", _num(r["r1m"], "+.1f", "%"), _num(r["r3m"], "+.1f", "%"),
             _num(r["r1y"], "+.1f", "%"), _num(r["vs_high"], "+.1f", "%"),
             _num(r["funding"], "+.0f", "%") if pd.notna(r["funding"]) else "", escape(r["source"])]
            for _, r in t.iterrows()]
    cards = []
    g = mv.gold_vs_real
    if g:
        cards.append(_kpi("Oro vs tasa real", f"{g['gap_pct']:+.0f}% <span class='muted'>{g['gap_sd']:+.1f} sd</span>"))
    if mv.gold_silver is not None:
        r10 = mv.gold_silver[mv.gold_silver.index > mv.gold_silver.index[-1] - pd.DateOffset(years=10)]
        cards.append(_kpi("Ratio oro/plata", f"{mv.gold_silver.iloc[-1]:.0f} <span class='muted'>p{(r10 <= r10.iloc[-1]).mean() * 100:.0f} 10a</span>"))
    if mv.copper_gold is not None:
        cards.append(_kpi("Cobre/oro vs bono 10a", _num(mv.copper_gold_vs_10y, "+.2f") + " <span class='muted'>corr. 3a</span>"))
    one = lambda s: s[s.index > s.index[-1] - pd.Timedelta(days=365)]
    base = lambda s: one(s) / one(s).iloc[0] * 100
    prices = mv.data.prices
    ch = charts.line_chart({LABELS_SHORT[k]: base(v) for k, v in prices.items()}, "Metales, 1 año (base 100)")
    if mv.copper_gold is not None:
        ch += charts.line_chart({"Cobre/oro ×1000": mv.copper_gold[mv.copper_gold.index > mv.copper_gold.index[-1] - pd.DateOffset(years=3)]},
                                "Ratio cobre/oro (3 años)", note="sube = expectativas de crecimiento")
    expl = ("<p class='muted'>Oro vs tasa real: distancia del oro a su relación de 3 años con el TIPS a 10 años "
            "(> +1 sd: caro respecto a esa relación). Funding: posicionamiento apalancado en los perpetuos de Binance.</p>")
    return (f'<div class="kpis">{"".join(cards)}</div><div class="card">'
            f'{_table(["Metal", "Precio", "1m", "3m", "1 año", "vs máx. 1a", "Funding anual", "Fuente"], rows)}{expl}</div>'
            f'<div class="grid2">{ch}</div>')


LABELS_SHORT = {"gold": "Oro", "silver": "Plata", "copper": "Cobre"}


def render(ctx: Context) -> str:
    gen = datetime.now().strftime("%d/%m/%Y %H:%M")
    bullets = "".join(f"<li>{b}</li>" for b in executive(ctx))
    errs = "".join(f"<li>{escape(k)}: {escape(v)}</li>" for k, v in ctx.errors.items())
    sections = [
        ("Qué descuenta el mercado", section_market(ctx)),
        ("Crecimiento, inflación y cuadrante macro", section_macro(ctx)),
        ("Energía e IPC", section_energy(ctx)),
        ("Régimen de riesgo", section_risk(ctx)),
        ("Valoración de la bolsa", section_valuation(ctx)),
        ("Posicionamiento (CFTC)", section_positioning(ctx)),
        ("Evidencia sectorial", section_evidence(ctx) if ctx.regime_study or ctx.quadrant_study
         else '<p class="muted">Omitida (--quick).</p>'),
        ("Sectores del S&P 500", section_sectors(ctx)),
        ("Metales: oro, plata y cobre", section_metals(ctx)),
        ("Análogos históricos y modelo predictivo", section_history(ctx) if "análogos y modelo" in ctx.errors or ctx.analogs
         else '<p class="muted">Omitida (--quick).</p>'),
        ("Cripto", section_crypto(ctx)),
        ("Liquidez de la Fed", section_liquidity(ctx)),
        ("Carry del yen", section_carry(ctx)),
        ("Historial de aciertos del motor", section_track(ctx)),
        ("Salud de los datos", section_health(ctx)),
    ]
    sections = [(f"{i}. {t}", h) for i, (t, h) in enumerate(sections, 1)]
    body = "".join(f"<h2>{escape(t)}</h2>{s}" for t, s in sections)
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Informe macro {ctx.today.isoformat()}</title><style>{CSS}</style></head>
<body><main>
<h1>Informe macro y de riesgo</h1>
<p class="sub">Datos de FRED hasta {ctx.data_through or "n/d"} · generado {gen} · Macro Shock Risk Engine</p>
{kpis(ctx)}
{section_view(ctx)}
<div class="card"><h3>Lectura ejecutiva</h3><ul class="exec">{bullets}</ul></div>
{body}
{f'<div class="card"><h3>Secciones con datos incompletos</h3><ul class="err">{errs}</ul></div>' if errs else ""}
<footer>
<p><b>Método.</b> Escenario de mercado: movimientos de {ms.WINDOW} días en desviaciones típicas de los últimos 3 años.
Cuadrante: ritmo de 3 meses frente a 12 de peticiones de subsidio, producción industrial, empleo e IPC, con los retrasos de
publicación de cada dato. Nowcast del IPC: regresiones por subpartida validadas fuera de muestra desde 2010. Sectores: French
Data Library, robustez por mitades. Análogos: distancia euclídea en variables estandarizadas desde 1990. Modelo: ridge con
reestimación mensual y R² fuera de muestra.</p>
<p><b>Límites.</b> Asociaciones históricas, no previsiones garantizadas. El escenario de mercado describe lo que los precios ya
descuentan. El PMI y el ISM no son públicos; el oro y la plata no están en FRED. Datos, no consejo: decide con tu propio plan y
tu límite de caída.</p>
</footer></main></body></html>"""


def write(ctx: Context, path) -> None:
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(ctx), encoding="utf-8")
