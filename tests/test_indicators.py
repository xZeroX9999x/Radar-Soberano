"""Tests unitarios para los indicadores técnicos."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from radar_soberano.indicators import rsi_wilder, simple_moving_average


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------

def test_sma_basic_window_3():
    serie = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    sma = simple_moving_average(serie, 3)

    assert math.isnan(sma.iloc[0])
    assert math.isnan(sma.iloc[1])
    assert sma.iloc[2] == pytest.approx(2.0)
    assert sma.iloc[3] == pytest.approx(3.0)
    assert sma.iloc[4] == pytest.approx(4.0)


def test_sma_constant_series_returns_constant():
    serie = pd.Series([7.0] * 50)
    sma = simple_moving_average(serie, 14)

    assert sma.iloc[-1] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# RSI Wilder
# ---------------------------------------------------------------------------

def test_rsi_strictly_increasing_returns_100():
    """Sin pérdidas → RSI = 100 (manejo de div-por-cero)."""
    serie = pd.Series(range(1, 60), dtype=float)
    rsi = rsi_wilder(serie, 14)

    assert rsi.iloc[-1] == pytest.approx(100.0)


def test_rsi_strictly_decreasing_approaches_zero():
    """Sin ganancias → RSI cerca de 0."""
    serie = pd.Series(range(60, 0, -1), dtype=float)
    rsi = rsi_wilder(serie, 14)

    assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_always_in_range_0_100():
    """Para cualquier serie, el RSI debe estar en [0, 100]."""
    rng = np.random.default_rng(seed=42)
    serie = pd.Series(rng.standard_normal(200).cumsum() + 100)
    rsi = rsi_wilder(serie, 14).dropna()

    assert (rsi >= 0).all()
    assert (rsi <= 100).all()


def test_rsi_handles_constant_series_without_error():
    """Serie constante → todos los deltas son 0, no debe romper."""
    serie = pd.Series([100.0] * 50)
    rsi = rsi_wilder(serie, 14)

    # No debe producir excepciones; el valor concreto es de menor importancia
    # (con 0 ganancias y 0 pérdidas, el rs es 0/0 → manejado por fillna a 100).
    assert not math.isinf(rsi.iloc[-1])


def test_rsi_returns_same_length_as_input():
    serie = pd.Series(np.linspace(100, 200, 50))
    rsi = rsi_wilder(serie, 14)

    assert len(rsi) == len(serie)
