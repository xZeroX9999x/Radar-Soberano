"""Pipeline principal: análisis fundamental + técnico → veredicto.

Soporta dos modos:
  - Quantamental (default): filtros fundamentales + RSI/MA como gatillo.
  - Buffett (--buffett): añade filtros value y desactiva RSI como gatillo.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from .alerts import load_alert_config, send_sell_alerts
from .buffett import (
    BuffettCriteria,
    BuffettMetrics,
    evaluate_buffett,
)
from .config import TradingRules
from .database import open_db
from .earnings import EarningsInfo, fetch_earnings_info
from .indicators import rsi_wilder, simple_moving_average
from .portfolio import (
    SellSignal,
    evaluate_sell_signals,
    list_positions,
)
from .price_targets import PriceTargets, compute_price_targets
from .sector_rules import passes_sector_filter

log = logging.getLogger(__name__)

# --- Etiquetas de veredicto ---
V_BUY = "🟢 COMPRA ESTRATÉGICA"
V_WAIT = "⏳ ESPERAR"
V_SELL = "🔴 SOBRECOMPRA"
V_BUFFETT = "⭐ BUFFETT GRADE"
V_FOCO_NO_BUFFETT = "⚠ FOCO (no-Buffett)"

# Orden de severidad/preferencia para sort en el CSV
_VERDICT_ORDER = {
    V_BUFFETT: 0,
    V_BUY: 1,
    V_WAIT: 2,
    V_FOCO_NO_BUFFETT: 3,
    V_SELL: 4,
}

# --- Razones de descarte (para estadísticas) ---
DROP_FUNDAMENTAL = "fundamental"
DROP_BUFFETT = "buffett"
DROP_HISTORY = "historia_insuficiente"
DROP_INFO = "datos_yfinance"
DROP_HISTORY_FETCH = "fetch_precios"
DROP_SECTOR = "sector_filtrado"


@dataclass
class ScanStats:
    """Métricas agregadas de una corrida."""

    total: int = 0
    passed: int = 0
    drops_by_reason: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    elapsed_seconds: float = 0.0

    def add_drop(self, reason: str) -> None:
        self.drops_by_reason[reason] = self.drops_by_reason.get(reason, 0) + 1

    def summary_lines(self) -> list[str]:
        lines = [
            f"Total escaneados: {self.total}",
            f"Pasaron filtros:  {self.passed}",
        ]
        if self.drops_by_reason:
            lines.append("Descartados por:")
            for reason, n in sorted(
                self.drops_by_reason.items(), key=lambda x: -x[1]
            ):
                lines.append(f"  · {reason}: {n}")
        if self.errors:
            lines.append(f"Errores: {self.errors}")
        if self.total and self.elapsed_seconds:
            tps = self.total / self.elapsed_seconds
            lines.append(
                f"Tiempo: {self.elapsed_seconds:.1f}s ({tps:.1f} tickers/s)"
            )
        return lines


@dataclass
class TickerSnapshot:
    """Resultado de análisis de un ticker en una fecha dada."""

    ticker: str
    sector: str
    margen: float
    roe: float
    deuda: float
    rsi: float
    ma200: float
    cierre: float
    veredicto: str
    fecha: str

    # Métricas Buffett (None fuera del modo Buffett)
    pe: Optional[float] = None
    pb: Optional[float] = None
    pfcf: Optional[float] = None
    earnings_yield: Optional[float] = None
    notas: list[str] = field(default_factory=list)

    # Datos para gráfico y precio objetivo (solo para los que pasan filtros)
    price_targets: Optional[PriceTargets] = None
    history_data: Optional[list[dict]] = None  # [{date, close, ma200, rsi}]
    earnings: Optional[EarningsInfo] = None

    def for_csv(self) -> dict:
        """Vista plana del snapshot para serialización CSV."""
        # Price targets son opcionales — formateamos solo si existen.
        if self.price_targets is not None:
            target_price = self.price_targets.suggested_buy_price
            distance = self.price_targets.distance_pct
            recommendation = self.price_targets.recommendation
        else:
            target_price = ""
            distance = ""
            recommendation = ""

        return {
            "Activo": self.ticker,
            "Sector": self.sector,
            "M.Bruto%": round(self.margen, 1),
            "ROE%": round(self.roe, 1),
            "D/C": round(self.deuda, 2),
            "PE": _round_or_blank(self.pe, 1),
            "PB": _round_or_blank(self.pb, 2),
            "PFCF": _round_or_blank(self.pfcf, 1),
            "E/Y%": _round_or_blank(
                self.earnings_yield * 100 if self.earnings_yield else None, 2
            ),
            "RSI": round(self.rsi, 1),
            "Cierre": round(self.cierre, 2),
            "MA200": round(self.ma200, 2),
            "Precio_Objetivo": target_price,
            "Distancia%": distance,
            "Accion": recommendation,
            "Tendencia": "ALCISTA" if self.cierre > self.ma200 else "BAJISTA",
            "VEREDICTO": self.veredicto,
            "Notas": "; ".join(self.notas) if self.notas else "",
        }

    def to_full_dict(self) -> dict:
        """Versión completa para la API web (con targets + history)."""
        base = self.for_csv()

        if self.price_targets is not None:
            t = self.price_targets
            base["targets"] = {
                "current_price": t.current_price,
                "suggested_buy_price": t.suggested_buy_price,
                "distance_pct": t.distance_pct,
                "supports": t.supports,
                "fibonacci_levels": t.fibonacci_levels,
                "rsi_implied_price": t.rsi_implied_price,
                "high_52w": t.high_52w,
                "low_52w": t.low_52w,
                "pct_from_high": t.pct_from_high,
                "method_used": t.method_used,
                "recommendation": t.recommendation,
                "recommendation_reason": t.recommendation_reason,
            }
        else:
            base["targets"] = None

        base["history"] = self.history_data or []

        if self.earnings is not None:
            base["earnings"] = {
                "next_date": self.earnings.next_date,
                "days_until": self.earnings.days_until,
                "is_imminent": self.earnings.is_imminent,
                "warning_text": self.earnings.warning_text,
            }
        else:
            base["earnings"] = None

        return base


def _round_or_blank(value: Optional[float], digits: int) -> str | float:
    if value is None:
        return ""
    return round(value, digits)


def _build_history_payload(
    closes: pd.Series,
    ma200: pd.Series,
    rsi: pd.Series,
    max_points: int = 250,
) -> list[dict]:
    """Serializa la serie histórica para el frontend.

    Reduce a `max_points` puntos máximo (downsample si excede), e incluye
    cierre, MA200 y RSI para el chart de doble panel.
    """
    n = len(closes)
    if n == 0:
        return []

    # Downsample si la serie es muy larga (típicamente 252 días, no hay caso)
    step = max(1, n // max_points)

    payload: list[dict] = []
    for i in range(0, n, step):
        idx = closes.index[i]
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)

        close_val = float(closes.iloc[i])
        ma_val = ma200.iloc[i]
        rsi_val = rsi.iloc[i]

        payload.append({
            "date": date_str,
            "close": round(close_val, 2),
            "ma200": round(float(ma_val), 2) if pd.notna(ma_val) else None,
            "rsi": round(float(rsi_val), 2) if pd.notna(rsi_val) else None,
        })

    # Asegurar que el último punto siempre esté
    last_idx = closes.index[-1]
    last_date = (
        last_idx.strftime("%Y-%m-%d")
        if hasattr(last_idx, "strftime") else str(last_idx)
    )
    if not payload or payload[-1]["date"] != last_date:
        payload.append({
            "date": last_date,
            "close": round(float(closes.iloc[-1]), 2),
            "ma200": (
                round(float(ma200.iloc[-1]), 2)
                if pd.notna(ma200.iloc[-1]) else None
            ),
            "rsi": (
                round(float(rsi.iloc[-1]), 2)
                if pd.notna(rsi.iloc[-1]) else None
            ),
        })

    return payload


@dataclass
class _AnalysisOutcome:
    """Resultado interno del análisis de un ticker individual."""

    snapshot: Optional[TickerSnapshot] = None
    drop_reason: Optional[str] = None


def analyze_ticker(
    ticker: str,
    rules: TradingRules,
    buffett_criteria: Optional[BuffettCriteria] = None,
    treasury_yield: Optional[float] = None,
    sector_filter: Optional[set[str]] = None,
) -> _AnalysisOutcome:
    """Analiza un ticker. Devuelve snapshot o razón de descarte."""
    is_focus = ticker in rules.portafolio_foco
    is_buffett_mode = buffett_criteria is not None

    if is_buffett_mode and treasury_yield is None:
        raise ValueError("treasury_yield obligatorio en modo Buffett")

    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as exc:
        log.debug("%s: fallo .info — %s", ticker, exc.__class__.__name__)
        return _AnalysisOutcome(drop_reason=DROP_INFO)

    sector = info.get("sector") or "N/A"
    margen = (info.get("grossMargins") or 0.0) * 100.0
    roe = (info.get("returnOnEquity") or 0.0) * 100.0
    deuda = (info.get("debtToEquity") or 0.0) / 100.0

    # --- Filtro por sector (no aplica al portafolio foco) ---
    if sector_filter and not is_focus:
        if sector.upper() not in sector_filter:
            return _AnalysisOutcome(drop_reason=DROP_SECTOR)

    # --- Filtro fundamental por sector ---
    fundamental_reason: str | None = None
    if not is_focus:
        passes_fund, fundamental_reason = _passes_fundamentals(
            sector, margen, roe, deuda, rules,
        )
        if not passes_fund:
            log.debug("%s: %s", ticker, fundamental_reason)
            return _AnalysisOutcome(drop_reason=DROP_FUNDAMENTAL)

    # --- Filtro Buffett ---
    buffett_passes = True
    buffett_reasons: list[str] = []
    metrics = BuffettMetrics()

    if is_buffett_mode:
        try:
            income_stmt = tk.income_stmt
        except Exception:
            income_stmt = None

        buffett_passes, buffett_reasons, metrics = evaluate_buffett(
            info, income_stmt, treasury_yield, buffett_criteria
        )

        if not buffett_passes and not is_focus:
            return _AnalysisOutcome(drop_reason=DROP_BUFFETT)

    # --- Análisis técnico ---
    try:
        hist = tk.history(period="1y", auto_adjust=False)
    except Exception as exc:
        log.debug("%s: fallo .history — %s", ticker, exc.__class__.__name__)
        return _AnalysisOutcome(drop_reason=DROP_HISTORY_FETCH)

    if len(hist) < rules.ma_period:
        return _AnalysisOutcome(drop_reason=DROP_HISTORY)

    closes = hist["Close"]
    cierre = float(closes.iloc[-1])
    ma200_series = simple_moving_average(closes, rules.ma_period)
    rsi_series = rsi_wilder(closes, rules.rsi_period)
    ma200 = float(ma200_series.iloc[-1])
    rsi = float(rsi_series.iloc[-1])

    # --- Veredicto + notas explicativas ---
    notas: list[str] = []
    if is_buffett_mode:
        if buffett_passes:
            veredicto = V_BUFFETT
            notas = _buffett_pass_notes(metrics, treasury_yield)
        else:
            veredicto = V_FOCO_NO_BUFFETT
            notas = list(buffett_reasons)
    else:
        veredicto, nota_tecnica = _verdict_quantamental(cierre, ma200, rsi, rules)
        if nota_tecnica:
            notas.append(nota_tecnica)

    if ticker == "TTWO":
        veredicto = f"{veredicto} | Target: 19 Nov 2026 (GTA VI)"

    # --- Cálculo de precios objetivo y datos del gráfico ---
    targets = compute_price_targets(closes, rsi_target=rules.rsi_entrada)

    # Serializar histórico para el chart (ratio reducido si la serie es muy
    # larga: tomamos 1 cada N puntos para no inflar el JSON innecesariamente).
    history_data = _build_history_payload(closes, ma200_series, rsi_series)

    # Próximos earnings (puede fallar silenciosamente)
    try:
        earnings_info = fetch_earnings_info(tk)
    except Exception as exc:
        log.debug("%s: earnings fetch fail — %s", ticker, exc.__class__.__name__)
        earnings_info = None

    return _AnalysisOutcome(snapshot=TickerSnapshot(
        ticker=ticker,
        sector=sector,
        margen=margen,
        roe=roe,
        deuda=deuda,
        rsi=rsi,
        ma200=ma200,
        cierre=cierre,
        veredicto=veredicto,
        fecha=datetime.now().strftime("%Y-%m-%d"),
        pe=metrics.pe,
        pb=metrics.pb,
        pfcf=metrics.pfcf,
        earnings_yield=metrics.earnings_yield,
        notas=notas,
        price_targets=targets,
        history_data=history_data,
        earnings=earnings_info,
    ))


def run_radar(
    universe: list[str],
    rules: TradingRules,
    db_path: Path,
    csv_path: Path,
    buffett_criteria: Optional[BuffettCriteria] = None,
    treasury_yield: Optional[float] = None,
    sector_filter: Optional[set[str]] = None,
    top_n: Optional[int] = None,
    check_portfolio: bool = True,
) -> tuple[list[TickerSnapshot], ScanStats]:
    """Recorre el universo, persiste resultados y escribe el CSV.

    Si `check_portfolio` es True (default), tras el scan principal evalúa
    posiciones abiertas en busca de señales de venta y envía alertas.
    """
    log.info(
        "Analizando %d activos%s%s...",
        len(universe),
        " (modo Buffett)" if buffett_criteria else "",
        f" sectores={sorted(sector_filter)}" if sector_filter else "",
    )

    snapshots: list[TickerSnapshot] = []
    stats = ScanStats(total=len(universe))
    start = time.monotonic()

    for i, ticker in enumerate(universe, start=1):
        try:
            outcome = analyze_ticker(
                ticker, rules,
                buffett_criteria=buffett_criteria,
                treasury_yield=treasury_yield,
                sector_filter=sector_filter,
            )
        except KeyboardInterrupt:
            log.warning("Interrupción del usuario — terminando suavemente.")
            break
        except Exception as exc:
            stats.errors += 1
            log.warning(
                "[%d/%d] %s: error — %s: %s",
                i, len(universe), ticker, exc.__class__.__name__, exc,
            )
            continue

        if outcome.snapshot is not None:
            _persist(outcome.snapshot, db_path)
            snapshots.append(outcome.snapshot)
            stats.passed += 1
            # Log del veredicto con info de target si existe
            snap = outcome.snapshot
            if snap.price_targets is not None:
                t = snap.price_targets
                rec_short = {
                    "COMPRAR_AHORA": "🟢 COMPRAR AHORA",
                    "CERCA": "🟡 CERCA",
                    "ESPERAR": "⏳ esperar a $%.2f" % t.suggested_buy_price,
                    "CARO": "🔴 caro (objetivo $%.2f)" % t.suggested_buy_price,
                }.get(t.recommendation, t.recommendation)
                log.info(
                    "[%d/%d] %s @ $%.2f → %s · %s",
                    i, len(universe), ticker, snap.cierre,
                    snap.veredicto.split(" |")[0], rec_short,
                )
            else:
                log.info("[%d/%d] %s → %s",
                         i, len(universe), ticker, snap.veredicto)
        else:
            stats.add_drop(outcome.drop_reason or "desconocido")
            log.debug("[%d/%d] %s: descartado (%s)",
                      i, len(universe), ticker, outcome.drop_reason)

        time.sleep(rules.request_delay)

    stats.elapsed_seconds = time.monotonic() - start

    if snapshots:
        _write_csv(snapshots, csv_path)
        log.info("Reporte CSV: %s", csv_path.resolve())
        if top_n:
            _print_top(snapshots, top_n)
    else:
        log.info("Sin oportunidades — radar completado.")

    log.info("--- Estadísticas de la corrida ---")
    for line in stats.summary_lines():
        log.info(line)

    # ---- Evaluación de posiciones abiertas + alertas ----
    if check_portfolio:
        try:
            sell_signals = _check_open_positions(db_path)
            if sell_signals:
                log.warning(
                    "🚨 %d posición(es) con señal de venta — enviando alertas.",
                    len(sell_signals),
                )
                for sig in sell_signals:
                    log.warning("  %s: %s", sig.position.ticker, sig.reason_text)

                _try_send_alerts(sell_signals)
            else:
                positions_count = len(list_positions(db_path, estado="abierta"))
                if positions_count > 0:
                    log.info("Portfolio: %d posición(es) abierta(s), "
                             "ninguna cumple criterio de venta.",
                             positions_count)
        except Exception as exc:
            log.warning("Error evaluando portfolio: %s: %s",
                        exc.__class__.__name__, exc)

    return snapshots, stats


def _check_open_positions(db_path: Path) -> list[SellSignal]:
    """Evalúa posiciones abiertas; devuelve las que tienen señal de venta.

    Para cada posición abierta consulta yfinance con un solo `.history()`
    de 1 año (necesitamos MA200 y RSI) y aplica el evaluador.
    """
    import yfinance as yf

    positions = list_positions(db_path, estado="abierta")
    if not positions:
        return []

    signals: list[SellSignal] = []
    for pos in positions:
        try:
            tk = yf.Ticker(pos.ticker)
            hist = tk.history(period="1y", auto_adjust=False)
            if hist.empty:
                log.debug("Portfolio %s: sin historia, salteando.", pos.ticker)
                continue

            closes = hist["Close"]
            current_price = float(closes.iloc[-1])

            ma200_val = None
            rsi_val = None
            if len(closes) >= 200:
                ma200_val = float(simple_moving_average(closes, 200).iloc[-1])
            if len(closes) >= 15:
                rsi_val = float(rsi_wilder(closes, 14).iloc[-1])

            sig = evaluate_sell_signals(
                pos, current_price, rsi=rsi_val, ma200=ma200_val,
            )
            if sig is not None:
                signals.append(sig)

        except Exception as exc:
            log.warning(
                "Portfolio %s: error evaluando — %s: %s",
                pos.ticker, exc.__class__.__name__, exc,
            )

    return signals


def _try_send_alerts(signals: list[SellSignal]) -> None:
    """Carga config y envía alertas. Errores se loggean pero no propagan."""
    try:
        config = load_alert_config()
        if not config.any_enabled:
            log.info("Alertas no configuradas — solo log local.")
            return
        result = send_sell_alerts(config, signals)
        for channel, (ok, msg) in result.items():
            if ok is True:
                log.info("Alerta %s: %s", channel, msg)
            elif ok is False:
                log.warning("Alerta %s falló: %s", channel, msg)
    except Exception as exc:
        log.warning("Error enviando alertas: %s: %s",
                    exc.__class__.__name__, exc)


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _passes_fundamentals(
    sector: str,
    margen: float,
    roe: float,
    deuda: float,
    rules: TradingRules,
) -> tuple[bool, str | None]:
    """Reglas duras de calidad fundamental por sector.

    Devuelve (passes, reason). Si passes=False, reason indica qué umbral
    falló (útil para logs y notas en el reporte).
    """
    return passes_sector_filter(
        sector, margen, roe, deuda, rules.sector_rules,
    )


def _verdict_quantamental(
    cierre: float, ma200: float, rsi: float, rules: TradingRules,
) -> tuple[str, str]:
    """Genera veredicto + nota explicativa en modo quantamental."""
    if cierre > ma200 and rsi < rules.rsi_entrada:
        return V_BUY, f"RSI {rsi:.1f} < {rules.rsi_entrada} con tendencia alcista"
    if rsi > rules.rsi_sobrecompra:
        return V_SELL, f"RSI {rsi:.1f} > {rules.rsi_sobrecompra}"
    return V_WAIT, ""


def _buffett_pass_notes(
    metrics: BuffettMetrics, treasury_yield: Optional[float],
) -> list[str]:
    """Construye notas explicativas cuando un ticker pasa Buffett."""
    notas: list[str] = []
    if metrics.earnings_yield and treasury_yield:
        notas.append(
            f"E/Y {metrics.earnings_yield * 100:.1f}% vs Treasury "
            f"{treasury_yield * 100:.1f}%"
        )
    if metrics.pb:
        notas.append(f"P/B {metrics.pb:.1f}")
    if metrics.pfcf:
        notas.append(f"P/FCF {metrics.pfcf:.1f}")
    return notas


def _persist(snapshot: TickerSnapshot, db_path: Path) -> None:
    with open_db(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO mercado (
                ticker, sector, margen, roe, deuda,
                rsi, ma200, cierre, veredicto, fecha,
                pe, pb, pfcf, earnings_yield
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.ticker,
                snapshot.sector,
                round(snapshot.margen, 2),
                round(snapshot.roe, 2),
                round(snapshot.deuda, 2),
                round(snapshot.rsi, 2),
                round(snapshot.ma200, 2),
                round(snapshot.cierre, 2),
                snapshot.veredicto,
                snapshot.fecha,
                round(snapshot.pe, 2) if snapshot.pe else None,
                round(snapshot.pb, 2) if snapshot.pb else None,
                round(snapshot.pfcf, 2) if snapshot.pfcf else None,
                round(snapshot.earnings_yield, 4) if snapshot.earnings_yield else None,
            ),
        )


def _write_csv(snapshots: list[TickerSnapshot], csv_path: Path) -> None:
    rows = [snap.for_csv() for snap in snapshots]
    df = pd.DataFrame(rows)

    df["_orden"] = df["VEREDICTO"].map(
        lambda v: _VERDICT_ORDER.get(v.split(" |")[0], 99)
    )
    df = df.sort_values(by=["_orden", "Activo"]).drop(columns=["_orden"])
    df.to_csv(csv_path, index=False, sep=";", encoding="utf-8-sig")


def _print_top(snapshots: list[TickerSnapshot], top_n: int) -> None:
    """Imprime un top-N en formato tabla en consola."""
    ordered = sorted(
        snapshots,
        key=lambda s: _VERDICT_ORDER.get(s.veredicto.split(" |")[0], 99),
    )[:top_n]

    log.info("--- Top %d resultados ---", min(top_n, len(ordered)))
    log.info("%-7s %-26s %-10s %-10s %-12s %s",
             "Ticker", "Veredicto", "Cierre", "Objetivo", "Distancia", "Acción")
    for s in ordered:
        v = s.veredicto.split(" |")[0]
        if s.price_targets is not None:
            t = s.price_targets
            target = f"${t.suggested_buy_price:.2f}"
            dist = f"{t.distance_pct:+.1f}%"
            rec = {
                "COMPRAR_AHORA": "🟢 COMPRAR YA",
                "CERCA": "🟡 cerca",
                "ESPERAR": "⏳ esperar",
                "CARO": "🔴 caro",
            }.get(t.recommendation, t.recommendation)
        else:
            target = "—"
            dist = "—"
            rec = "—"

        log.info(
            "%-7s %-26s $%-9.2f %-10s %-12s %s",
            s.ticker, v, s.cierre, target, dist, rec,
        )
