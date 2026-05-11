"""Tests unitarios para el módulo Buffett.

No tocan la red — todos los tests usan datos sintéticos.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from radar_soberano.buffett import (
    BuffettCriteria,
    evaluate_buffett,
)


# ---------------------------------------------------------------------------
# Helpers para construir datos de test
# ---------------------------------------------------------------------------

def _years_ago_epoch(years: float) -> float:
    """Devuelve el timestamp epoch de hace `years` años."""
    return datetime.now().timestamp() - (years * 365.25 * 24 * 3600)


def _make_income_stmt(net_incomes: list[float]) -> pd.DataFrame:
    """Construye un income_stmt como el que devuelve yfinance."""
    cols = [pd.Timestamp(f"202{i}-12-31") for i in range(len(net_incomes))]
    return pd.DataFrame([net_incomes], index=["Net Income"], columns=cols)


def _great_company_info() -> dict:
    """Empresa que cumple todos los criterios Buffett."""
    return {
        "trailingPE": 15.0,
        "priceToBook": 2.5,
        "marketCap": 100_000_000_000,
        "freeCashflow": 8_000_000_000,
        "firstTradeDateEpochUtc": _years_ago_epoch(20),
    }


# ---------------------------------------------------------------------------
# Casos: empresa de calidad
# ---------------------------------------------------------------------------

def test_great_company_passes_all_filters():
    info = _great_company_info()
    income = _make_income_stmt([5e9, 4.5e9, 4e9, 3.5e9])

    passes, reasons, metrics = evaluate_buffett(
        info, income, treasury_10y=0.04, criteria=BuffettCriteria()
    )

    assert passes is True
    assert reasons == []
    assert metrics.pe == pytest.approx(15.0)
    assert metrics.pb == pytest.approx(2.5)
    assert metrics.pfcf == pytest.approx(12.5)


def test_metrics_are_populated_even_when_failing():
    info = _great_company_info()
    info["priceToBook"] = 10.0  # falla P/B
    income = _make_income_stmt([5e9, 4.5e9, 4e9, 3.5e9])

    passes, reasons, metrics = evaluate_buffett(
        info, income, treasury_10y=0.04, criteria=BuffettCriteria()
    )

    assert passes is False
    assert any("P/B" in r for r in reasons)
    # Las métricas se calculan igual, para que el reporte tenga datos
    assert metrics.pe == pytest.approx(15.0)
    assert metrics.pb == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Casos: cada filtro falla por separado
# ---------------------------------------------------------------------------

def test_high_pe_fails_earnings_yield():
    """PE muy alto → E/Y muy bajo → no supera Treasury."""
    info = _great_company_info()
    info["trailingPE"] = 50.0  # E/Y = 2 % < Treasury 4 %
    income = _make_income_stmt([5e9, 4.5e9])

    passes, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04, criteria=BuffettCriteria()
    )

    assert passes is False
    assert any("E/Y" in r for r in reasons)


def test_negative_pe_fails():
    """PE no disponible (empresa con pérdidas) debe fallar."""
    info = _great_company_info()
    info["trailingPE"] = None
    income = _make_income_stmt([5e9, 4.5e9])

    passes, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04, criteria=BuffettCriteria()
    )

    assert passes is False
    assert any("PE" in r for r in reasons)


def test_high_pb_fails():
    info = _great_company_info()
    info["priceToBook"] = 5.0
    income = _make_income_stmt([5e9, 4.5e9])

    passes, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04, criteria=BuffettCriteria(pb_max=3.0)
    )

    assert passes is False
    assert any("P/B" in r for r in reasons)


def test_negative_fcf_fails():
    info = _great_company_info()
    info["freeCashflow"] = -1_000_000_000
    income = _make_income_stmt([5e9, 4.5e9])

    passes, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04, criteria=BuffettCriteria()
    )

    assert passes is False
    assert any("FCF" in r for r in reasons)


def test_young_company_fails():
    info = _great_company_info()
    info["firstTradeDateEpochUtc"] = _years_ago_epoch(3)  # solo 3 años
    income = _make_income_stmt([5e9, 4.5e9])

    passes, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04,
        criteria=BuffettCriteria(min_history_years=10),
    )

    assert passes is False
    assert any("joven" in r.lower() or "años" in r.lower() for r in reasons)


def test_recent_losses_fail():
    info = _great_company_info()
    income = _make_income_stmt([5e9, -2e9, 4e9, 3.5e9])  # un año en pérdidas

    passes, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04, criteria=BuffettCriteria()
    )

    assert passes is False
    assert any("pérdidas" in r.lower() for r in reasons)


def test_no_income_statement_fails():
    info = _great_company_info()

    passes, reasons, _ = evaluate_buffett(
        info, None, treasury_10y=0.04, criteria=BuffettCriteria()
    )

    assert passes is False
    assert any("income" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# Casos: configurabilidad de los criterios
# ---------------------------------------------------------------------------

def test_premium_over_treasury_is_respected():
    """E/Y de 5 % debe pasar Treasury 4 %, pero no Treasury 4 % + premium 2pp."""
    info = _great_company_info()
    info["trailingPE"] = 20.0  # E/Y = 5 %
    income = _make_income_stmt([5e9, 4.5e9])

    # Sin premium: pasa
    passes_no_premium, _, _ = evaluate_buffett(
        info, income, treasury_10y=0.04,
        criteria=BuffettCriteria(earnings_yield_premium_pp=0.0),
    )
    assert passes_no_premium is True

    # Con premium 2pp: el umbral es 6 %, pero E/Y es 5 % → falla
    passes_with_premium, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04,
        criteria=BuffettCriteria(earnings_yield_premium_pp=2.0),
    )
    assert passes_with_premium is False
    assert any("E/Y" in r for r in reasons)


def test_disabled_earnings_check_passes_with_losses():
    """Si require_positive_recent_earnings=False, las pérdidas no descalifican."""
    info = _great_company_info()
    income = _make_income_stmt([5e9, -2e9, 4e9, 3.5e9])

    passes, reasons, _ = evaluate_buffett(
        info, income, treasury_10y=0.04,
        criteria=BuffettCriteria(require_positive_recent_earnings=False),
    )

    assert passes is True
    assert reasons == []
