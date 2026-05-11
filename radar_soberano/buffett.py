"""Filtros de inversión value (estilo Warren Buffett).

Aplica los criterios clásicos del value investing:
  - Earnings Yield (1/PE) debe superar al bono Treasury 10Y.
  - P/B (precio/valor contable) razonable.
  - P/FCF (precio/flujo de caja libre) razonable.
  - Empresa con suficiente historia operativa.
  - Beneficios netos positivos en años recientes.

El módulo está aislado del resto del motor: importarlo no introduce
dependencias circulares y testearlo no requiere red.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuffettCriteria:
    """Umbrales del modo Buffett (todos sobrescribibles)."""

    # --- Value ---
    pb_max: float = 3.0
    pfcf_max: float = 20.0
    # Cuántos puntos porcentuales por encima del Treasury 10Y debe estar el E/Y.
    # 0.0 = solo necesita superarlo. 2.0 = necesita superarlo por +2 pp.
    earnings_yield_premium_pp: float = 0.0

    # --- Predictabilidad ---
    min_history_years: int = 10
    require_positive_recent_earnings: bool = True


@dataclass
class BuffettMetrics:
    """Métricas Buffett de un ticker en un momento dado."""

    pe: Optional[float] = None
    pb: Optional[float] = None
    pfcf: Optional[float] = None
    earnings_yield: Optional[float] = None  # como decimal: 0.067 = 6.7%
    years_old: Optional[float] = None


def fetch_treasury_yield_10y(timeout: int = 10) -> float:
    """Obtiene el yield del Treasury 10Y como decimal (0.045 = 4.5 %).

    Usa el ticker ``^TNX`` de Yahoo Finance. Cae a 0.04 si la consulta falla
    o devuelve datos imposibles.
    """
    try:
        tnx = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if hist.empty:
            raise ValueError("Sin datos en ^TNX")

        latest = float(hist["Close"].iloc[-1])

        # Defensa: si Yahoo cambia el formato (a veces lo cuotiza ×10),
        # un yield real nunca pasa de ~30 %.
        if latest > 30.0:
            latest /= 10.0

        return latest / 100.0
    except Exception as exc:
        log.warning(
            "No se pudo obtener Treasury 10Y (%s). Usando 4.0%% como fallback.",
            exc,
        )
        return 0.04


def evaluate_buffett(
    info: dict,
    income_stmt: Optional[pd.DataFrame],
    treasury_10y: float,
    criteria: BuffettCriteria,
) -> tuple[bool, list[str], BuffettMetrics]:
    """Evalúa si una empresa pasa los filtros Buffett.

    Args:
        info: dict con metadata de yfinance (``yf.Ticker.info``).
        income_stmt: DataFrame de ``yf.Ticker.income_stmt`` o None.
        treasury_10y: yield del Treasury 10Y como decimal.
        criteria: umbrales de evaluación.

    Returns:
        (passes, reasons, metrics). ``passes`` es True solo si TODOS los
        criterios se cumplen. ``reasons`` lista los criterios fallados
        (vacía si pasa). ``metrics`` contiene los valores observados.
    """
    reasons: list[str] = []
    metrics = BuffettMetrics()

    # --- 1. Earnings Yield > Treasury + premium ---
    pe = info.get("trailingPE")
    metrics.pe = float(pe) if pe and pe > 0 else None

    if metrics.pe:
        ey = 1.0 / metrics.pe
        metrics.earnings_yield = ey
        threshold = treasury_10y + (criteria.earnings_yield_premium_pp / 100.0)
        if ey < threshold:
            reasons.append(
                f"E/Y {ey * 100:.1f}% < umbral {threshold * 100:.1f}%"
            )
    else:
        reasons.append("PE no disponible o negativo (sin ganancias)")

    # --- 2. P/B razonable ---
    pb = info.get("priceToBook")
    metrics.pb = float(pb) if pb else None
    if metrics.pb is None:
        reasons.append("P/B no disponible")
    elif metrics.pb > criteria.pb_max:
        reasons.append(f"P/B {metrics.pb:.1f} > {criteria.pb_max}")

    # --- 3. P/FCF razonable ---
    market_cap = info.get("marketCap")
    fcf = info.get("freeCashflow")
    if market_cap and fcf and fcf > 0:
        metrics.pfcf = float(market_cap) / float(fcf)
        if metrics.pfcf > criteria.pfcf_max:
            reasons.append(f"P/FCF {metrics.pfcf:.1f} > {criteria.pfcf_max}")
    else:
        reasons.append("FCF negativo o no disponible")

    # --- 4. Edad mínima del negocio ---
    first_trade = info.get("firstTradeDateEpochUtc")
    if first_trade:
        years_old = (datetime.now().timestamp() - float(first_trade)) / (
            365.25 * 24 * 3600
        )
        metrics.years_old = years_old
        if years_old < criteria.min_history_years:
            reasons.append(
                f"Empresa joven: {years_old:.1f} < {criteria.min_history_years} años"
            )

    # --- 5. Beneficios positivos recientes ---
    if criteria.require_positive_recent_earnings:
        verdict = _check_positive_earnings(income_stmt)
        if verdict is not None:
            reasons.append(verdict)

    return len(reasons) == 0, reasons, metrics


def _check_positive_earnings(income_stmt: Optional[pd.DataFrame]) -> Optional[str]:
    """Verifica que todos los años disponibles tengan Net Income > 0.

    Devuelve None si pasa (sin razones que reportar), o un string con
    el motivo del fallo.
    """
    if income_stmt is None or income_stmt.empty:
        return "Sin income statement disponible"

    if "Net Income" not in income_stmt.index:
        return "Income statement sin fila 'Net Income'"

    try:
        net_income = income_stmt.loc["Net Income"].dropna()
        if net_income.empty:
            return "Net Income sin datos"

        negativos = int((net_income < 0).sum())
        if negativos > 0:
            return f"{negativos} de {len(net_income)} años con pérdidas netas"
    except Exception as exc:
        return f"Error leyendo Net Income: {exc.__class__.__name__}"

    return None
