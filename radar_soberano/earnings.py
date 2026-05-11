"""Earnings calendar — fechas de próximos reportes financieros.

Yahoo Finance expone esto vía `yf.Ticker(...).calendar` (un dict con
'Earnings Date' como lista de timestamps) y `Ticker.earnings_dates`
(DataFrame con histórico).

Las fechas de earnings importan porque:
  - La volatilidad se dispara los días previos y posteriores al reporte.
  - Comprar justo antes de un earnings es apostar a sorpresa (riesgoso).
  - Comprar justo después permite operar con información ya pricing-ed.
  - Una posición abierta cerca de earnings tiene riesgo elevado.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class EarningsInfo:
    """Información sobre el próximo reporte de earnings."""

    next_date: Optional[str]  # ISO yyyy-mm-dd
    days_until: Optional[int]
    is_imminent: bool  # True si quedan ≤ 7 días
    warning_text: Optional[str]


def fetch_earnings_info(yf_ticker) -> EarningsInfo:
    """Obtiene la próxima fecha de earnings desde un Ticker de yfinance.

    Maneja múltiples formatos que devuelve Yahoo (a veces dict, a veces
    DataFrame, a veces vacío) y errores de red sin propagar.

    Args:
        yf_ticker: instancia ya creada de yfinance.Ticker.

    Returns:
        EarningsInfo con next_date None si no hay datos disponibles.
    """
    next_dt: Optional[datetime] = None

    # Intento 1: el atributo .calendar (más confiable)
    try:
        calendar = yf_ticker.calendar
        if isinstance(calendar, dict):
            dates = calendar.get("Earnings Date") or []
            if isinstance(dates, list) and dates:
                first = dates[0]
                next_dt = _to_datetime(first)
    except Exception as exc:
        log.debug("calendar fail: %s", exc.__class__.__name__)

    # Intento 2: earnings_dates (DataFrame con futuras fechas)
    if next_dt is None:
        try:
            df = yf_ticker.earnings_dates
            if df is not None and not df.empty:
                # earnings_dates trae fechas futuras y pasadas; queremos
                # la primera futura (índice ordenado descendente normalmente)
                now = datetime.now(timezone.utc)
                future = df.index[df.index > now]
                if len(future) > 0:
                    next_dt = future.min().to_pydatetime()
        except Exception as exc:
            log.debug("earnings_dates fail: %s", exc.__class__.__name__)

    if next_dt is None:
        return EarningsInfo(
            next_date=None, days_until=None,
            is_imminent=False, warning_text=None,
        )

    # Normalizar a UTC-naive para comparar con datetime.now() naive
    if next_dt.tzinfo is not None:
        next_dt_naive = next_dt.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        next_dt_naive = next_dt

    delta_days = (next_dt_naive.date() - datetime.now().date()).days

    is_imminent = 0 <= delta_days <= 7
    warning = None

    if delta_days < 0:
        # Fecha pasada — el reporte ya ocurrió y no hay nuevo schedule todavía
        warning = f"Último reporte hace {abs(delta_days)} días"
    elif delta_days == 0:
        warning = "⚠️ REPORTE HOY — alta volatilidad esperada"
    elif delta_days <= 3:
        warning = f"⚠️ Reporte en {delta_days} días — evitar entrar ahora"
    elif delta_days <= 7:
        warning = f"⚠️ Reporte en {delta_days} días"

    return EarningsInfo(
        next_date=next_dt_naive.strftime("%Y-%m-%d"),
        days_until=delta_days,
        is_imminent=is_imminent,
        warning_text=warning,
    )


def _to_datetime(value) -> Optional[datetime]:
    """Acepta str, datetime, date, pd.Timestamp y devuelve datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # pandas.Timestamp también es datetime, pero por si acaso:
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if hasattr(value, "year"):  # date
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
