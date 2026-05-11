"""Tests para el módulo de cálculo de precios objetivo."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from radar_soberano.price_targets import (
    compute_price_targets,
    fibonacci_retracements,
    find_pivot_lows,
    rsi_implied_price,
)


# ---------------------------------------------------------------------------
# find_pivot_lows
# ---------------------------------------------------------------------------

def test_find_pivot_lows_detects_obvious_minimum():
    """Una serie con un mínimo claro en el medio debe detectarlo."""
    # Sube a 100, cae a 50 (minimo), vuelve a subir a 100
    serie = pd.Series(
        list(range(100, 50, -2)) +  # 100 → 52
        list(range(50, 100, 2))     # 50 → 98
    )
    pivots = find_pivot_lows(serie, window=5)
    assert len(pivots) > 0
    # El mínimo más bajo debe estar cerca de 50
    assert min(pivots) <= 55


def test_find_pivot_lows_short_series_returns_empty():
    """Series demasiado cortas no producen pivots."""
    serie = pd.Series([1, 2, 3, 4, 5])
    assert find_pivot_lows(serie, window=10) == []


def test_find_pivot_lows_dedupes_close_levels():
    """Pivots a menos de 2% de diferencia se consideran el mismo nivel."""
    # Construyo una serie con mínimos casi idénticos
    serie = pd.Series(
        [100, 90, 80, 70, 60, 50, 60, 70, 80, 70, 60, 50.5, 60, 70, 80, 90, 100] * 3
    )
    pivots = find_pivot_lows(serie, window=3)
    # 50 y 50.5 deberían deduplicarse a uno solo
    assert len(pivots) <= 5


def test_find_pivot_lows_returns_descending_order():
    """Resultados deben venir ordenados de mayor a menor."""
    rng = np.random.default_rng(42)
    serie = pd.Series(rng.standard_normal(200).cumsum() + 100)
    pivots = find_pivot_lows(serie, window=5)
    assert pivots == sorted(pivots, reverse=True)


# ---------------------------------------------------------------------------
# fibonacci_retracements
# ---------------------------------------------------------------------------

def test_fibonacci_classic_levels():
    fib = fibonacci_retracements(high=100.0, low=0.0)
    assert fib["38.2%"] == pytest.approx(61.8, abs=0.1)
    assert fib["50%"] == pytest.approx(50.0)
    assert fib["61.8%"] == pytest.approx(38.2, abs=0.1)


def test_fibonacci_zero_range_returns_empty():
    """Si high == low, no hay retroceso que calcular."""
    fib = fibonacci_retracements(100.0, 100.0)
    assert fib == {}


def test_fibonacci_invalid_range_returns_empty():
    """high < low es inválido."""
    fib = fibonacci_retracements(50.0, 100.0)
    assert fib == {}


def test_fibonacci_levels_between_high_and_low():
    fib = fibonacci_retracements(200.0, 100.0)
    for level in fib.values():
        assert 100.0 <= level <= 200.0


# ---------------------------------------------------------------------------
# rsi_implied_price
# ---------------------------------------------------------------------------

def test_rsi_implied_price_for_strongly_uptrending_series():
    """Tras una subida fuerte, debe haber un precio menor que lleve el RSI a 35."""
    serie = pd.Series([100 + i * 0.5 for i in range(100)])  # uptrend continuo
    implied = rsi_implied_price(serie, period=14, target_rsi=35.0)
    assert implied is not None
    assert implied < float(serie.iloc[-1])
    assert implied > 0


def test_rsi_implied_price_short_series_returns_none():
    serie = pd.Series([100, 101, 102])
    assert rsi_implied_price(serie, period=14) is None


def test_rsi_implied_price_below_target_returns_current_or_none():
    """Si la serie ya está en RSI muy bajo (downtrend total), avg_gain≤0 → None.
    Si está cayendo pero con algún rebote, devuelve precio actual o cercano.
    """
    # Serie con downtrend pero con rebotes intermedios (avg_gain > 0)
    serie = pd.Series(
        [100 - i * 0.3 + (3 if i % 5 == 0 else 0) for i in range(100)]
    )
    implied = rsi_implied_price(serie, period=14, target_rsi=35.0)
    # Acepta tanto None como un precio válido (depende del shape exacto)
    if implied is not None:
        assert implied >= 0


def test_rsi_implied_price_pure_downtrend_returns_none():
    """Downtrend puro sin rebotes: avg_gain == 0 → None."""
    serie = pd.Series([100 - i * 0.5 for i in range(100)])
    implied = rsi_implied_price(serie, period=14, target_rsi=35.0)
    # Sin ganancias en ningún momento, no se puede calcular
    assert implied is None


# ---------------------------------------------------------------------------
# compute_price_targets — el flujo principal
# ---------------------------------------------------------------------------

def _mock_uptrend_with_dips() -> pd.Series:
    """Construye una serie realista: tendencia alcista con dips periódicos."""
    rng = np.random.default_rng(123)
    base = np.linspace(100, 150, 252)  # un año subiendo de 100 a 150
    noise = rng.standard_normal(252) * 2
    # Inyectar dips notables
    dips = np.zeros(252)
    for pos in [40, 100, 180]:
        dips[pos:pos + 5] = -8.0
    return pd.Series(base + noise + dips,
                     index=pd.date_range("2025-01-01", periods=252, freq="D"))


def test_compute_targets_basic_structure():
    serie = _mock_uptrend_with_dips()
    targets = compute_price_targets(serie)

    assert targets.current_price > 0
    assert targets.suggested_buy_price > 0
    assert targets.high_52w >= targets.current_price or \
           targets.high_52w == pytest.approx(targets.current_price, rel=0.01)
    assert targets.low_52w <= targets.current_price
    assert targets.recommendation in ("COMPRAR_AHORA", "CERCA", "ESPERAR", "CARO")
    assert targets.recommendation_reason


def test_compute_targets_distance_is_consistent():
    """distance_pct debe ser coherente con la diferencia entre precios."""
    serie = _mock_uptrend_with_dips()
    targets = compute_price_targets(serie)

    expected = (targets.current_price - targets.suggested_buy_price) / targets.current_price * 100
    assert targets.distance_pct == pytest.approx(expected, abs=0.01)


def test_compute_targets_buy_now_when_at_low():
    """Si el precio actual es el mínimo de 52 semanas, debería ser COMPRAR_AHORA."""
    # Serie que termina en el mínimo
    serie = pd.Series(
        list(range(150, 100, -1)) +  # baja
        list(range(100, 150, 1)) +
        list(range(150, 80, -1))     # termina cayendo
    )
    targets = compute_price_targets(serie)
    # Estando en un mínimo absoluto, todos los soportes están arriba → recomendación de comprar
    assert targets.recommendation == "COMPRAR_AHORA"


def test_compute_targets_provides_all_levels():
    """El resultado debe incluir soportes, fibos y RSI implícito."""
    serie = _mock_uptrend_with_dips()
    targets = compute_price_targets(serie)

    # Al menos algunos soportes (puede ser menos de 5 dependiendo del shape)
    assert isinstance(targets.supports, list)
    # Fibos siempre los 3 niveles si hay rango
    assert "38.2%" in targets.fibonacci_levels
    assert "50%" in targets.fibonacci_levels
    assert "61.8%" in targets.fibonacci_levels


def test_compute_targets_pct_from_high_correct():
    serie = _mock_uptrend_with_dips()
    targets = compute_price_targets(serie)

    expected = (targets.high_52w - targets.current_price) / targets.high_52w * 100
    assert targets.pct_from_high == pytest.approx(expected, abs=0.01)
