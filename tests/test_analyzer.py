"""Tests unitarios para el módulo analyzer y CLI helpers."""
from __future__ import annotations

import pytest

from radar_soberano.analyzer import (
    DROP_BUFFETT,
    DROP_FUNDAMENTAL,
    DROP_HISTORY,
    ScanStats,
    _passes_fundamentals,
    _verdict_quantamental,
    V_BUY,
    V_SELL,
    V_WAIT,
)
from radar_soberano.cli import _parse_sectors
from radar_soberano.config import TradingRules


# ---------------------------------------------------------------------------
# _passes_fundamentals (ahora devuelve (bool, reason))
# ---------------------------------------------------------------------------

def test_passes_fundamentals_strong_tech_company():
    rules = TradingRules()
    passes, reason = _passes_fundamentals(
        sector="Technology", margen=70.0, roe=25.0, deuda=0.2, rules=rules
    )
    assert passes is True
    assert reason is None


def test_passes_fundamentals_low_margin_tech_fails():
    """Tech con margen bajo debe fallar."""
    rules = TradingRules()
    passes, reason = _passes_fundamentals(
        sector="Technology", margen=30.0, roe=25.0, deuda=0.2, rules=rules
    )
    assert passes is False
    assert "Margen" in reason


def test_passes_fundamentals_low_margin_non_tech_passes():
    """Sectores no-tech con menor exigencia de margen pasan."""
    rules = TradingRules()
    # Energy: roe_min 10, deuda_max 0.7, margen_min 15
    passes, _ = _passes_fundamentals(
        sector="Energy", margen=20.0, roe=15.0, deuda=0.5, rules=rules
    )
    assert passes is True


def test_passes_fundamentals_high_debt_fails():
    rules = TradingRules()
    passes, reason = _passes_fundamentals(
        sector="Technology", margen=70.0, roe=25.0, deuda=2.0, rules=rules
    )
    assert passes is False
    assert "D/C" in reason


def test_passes_fundamentals_low_roe_fails():
    rules = TradingRules()
    passes, reason = _passes_fundamentals(
        sector="Energy", margen=50.0, roe=5.0, deuda=0.2, rules=rules
    )
    assert passes is False
    assert "ROE" in reason


def test_passes_fundamentals_financial_services_no_debt_filter():
    """Bancos no deben fallar por D/C alto (su negocio es deuda)."""
    rules = TradingRules()
    passes, _ = _passes_fundamentals(
        sector="Financial Services", margen=0.0, roe=15.0, deuda=8.0, rules=rules
    )
    assert passes is True


def test_passes_fundamentals_reit_no_margin_filter():
    """REITs no deben fallar por margen (no es métrica relevante)."""
    rules = TradingRules()
    passes, _ = _passes_fundamentals(
        sector="Real Estate", margen=0.0, roe=8.0, deuda=2.5, rules=rules
    )
    assert passes is True


def test_passes_fundamentals_utility_high_debt_ok():
    """Utilities aceptan deuda mayor (regulado, modelo de negocio)."""
    rules = TradingRules()
    passes, _ = _passes_fundamentals(
        sector="Utilities", margen=25.0, roe=10.0, deuda=1.8, rules=rules
    )
    assert passes is True


def test_passes_fundamentals_unknown_sector_uses_fallback():
    """Sectores no mapeados usan reglas fallback."""
    rules = TradingRules()
    passes, _ = _passes_fundamentals(
        sector="Quantum Cryptocurrency Metaverse",
        margen=0.0, roe=15.0, deuda=0.5, rules=rules,
    )
    assert passes is True


# ---------------------------------------------------------------------------
# _verdict_quantamental
# ---------------------------------------------------------------------------

def test_verdict_buy_when_oversold_and_uptrend():
    rules = TradingRules()
    veredicto, nota = _verdict_quantamental(
        cierre=100.0, ma200=90.0, rsi=30.0, rules=rules,
    )
    assert veredicto == V_BUY
    assert "RSI 30.0" in nota
    assert "alcista" in nota.lower()


def test_verdict_sell_when_overbought():
    rules = TradingRules()
    veredicto, nota = _verdict_quantamental(
        cierre=100.0, ma200=90.0, rsi=80.0, rules=rules,
    )
    assert veredicto == V_SELL
    assert "RSI 80.0" in nota


def test_verdict_wait_otherwise():
    rules = TradingRules()
    veredicto, nota = _verdict_quantamental(
        cierre=100.0, ma200=90.0, rsi=50.0, rules=rules,
    )
    assert veredicto == V_WAIT
    assert nota == ""


def test_verdict_no_buy_in_downtrend_even_if_oversold():
    """RSI bajo en tendencia bajista no es señal de compra."""
    rules = TradingRules()
    veredicto, _ = _verdict_quantamental(
        cierre=80.0, ma200=100.0, rsi=20.0, rules=rules,
    )
    assert veredicto == V_WAIT


# ---------------------------------------------------------------------------
# ScanStats
# ---------------------------------------------------------------------------

def test_scan_stats_aggregates_drops():
    stats = ScanStats(total=10)
    stats.add_drop(DROP_FUNDAMENTAL)
    stats.add_drop(DROP_FUNDAMENTAL)
    stats.add_drop(DROP_BUFFETT)
    stats.passed = 7

    assert stats.drops_by_reason[DROP_FUNDAMENTAL] == 2
    assert stats.drops_by_reason[DROP_BUFFETT] == 1


def test_scan_stats_summary_includes_throughput():
    stats = ScanStats(total=100, passed=20, elapsed_seconds=10.0)
    summary = "\n".join(stats.summary_lines())

    assert "Total escaneados: 100" in summary
    assert "Pasaron filtros:  20" in summary
    assert "tickers/s" in summary


def test_scan_stats_no_throughput_when_zero_elapsed():
    """Sin tiempo medido, no debería intentar dividir."""
    stats = ScanStats(total=10, passed=5)
    summary = "\n".join(stats.summary_lines())

    assert "tickers/s" not in summary


# ---------------------------------------------------------------------------
# _parse_sectors
# ---------------------------------------------------------------------------

def test_parse_sectors_simple():
    assert _parse_sectors("Technology") == {"TECHNOLOGY"}


def test_parse_sectors_multiple_normalizes():
    result = _parse_sectors("Technology,energy , Healthcare")
    assert result == {"TECHNOLOGY", "ENERGY", "HEALTHCARE"}


def test_parse_sectors_empty_returns_none():
    assert _parse_sectors(None) is None
    assert _parse_sectors("") is None


def test_parse_sectors_strips_empty_segments():
    """'Tech,,Energy,' no debería dejar strings vacíos en el set."""
    result = _parse_sectors("Tech,,Energy,")
    assert "" not in result
    assert result == {"TECH", "ENERGY"}
