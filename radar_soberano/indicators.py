"""Indicadores técnicos como funciones puras (testeables, sin I/O)."""
from __future__ import annotations

import pandas as pd


def simple_moving_average(prices: pd.Series, period: int) -> pd.Series:
    """Media móvil aritmética de los últimos `period` cierres."""
    return prices.rolling(window=period).mean()


def rsi_wilder(prices: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder (suavizado exponencial con α = 1/period).

    Es la fórmula estándar usada por la mayoría de plataformas
    profesionales (TradingView, Bloomberg). Maneja correctamente
    el caso de cero pérdidas (RSI = 100) y cero ganancias (RSI = 0).

    Args:
        prices: serie de precios de cierre.
        period: ventana del indicador (típico: 14).

    Returns:
        Serie de RSI en [0, 100]. Los primeros `period` valores son NaN.
    """
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    # Si avg_loss == 0 → RS infinito → RSI = 100 (forzado vía fillna)
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(100.0)
