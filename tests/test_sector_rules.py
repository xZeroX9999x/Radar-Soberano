"""Tests para el módulo de reglas por sector."""
from __future__ import annotations

import pytest

from radar_soberano.sector_rules import (
    DEFAULT_SECTOR_RULES,
    FALLBACK_RULES,
    SectorRulesConfig,
    SectorThresholds,
    passes_sector_filter,
)


# ---------------------------------------------------------------------------
# Cobertura del mapeo
# ---------------------------------------------------------------------------

def test_all_gics_sectors_present():
    """Los 11 sectores GICS de Yahoo Finance deben estar mapeados."""
    expected = {
        "Technology",
        "Communication Services",
        "Healthcare",
        "Consumer Cyclical",
        "Consumer Defensive",
        "Industrials",
        "Energy",
        "Basic Materials",
        "Utilities",
        "Real Estate",
        "Financial Services",
    }
    assert set(DEFAULT_SECTOR_RULES.keys()) == expected


def test_all_sectors_have_descriptions():
    """Cada sector debe tener una descripción no vacía."""
    for name, rules in DEFAULT_SECTOR_RULES.items():
        assert rules.descripcion, f"Sector {name} sin descripción"


# ---------------------------------------------------------------------------
# SectorRulesConfig.for_sector
# ---------------------------------------------------------------------------

def test_for_sector_returns_specific_rules():
    config = SectorRulesConfig()
    tech = config.for_sector("Technology")
    assert tech.roe_min == 15.0
    assert tech.margen_min == 50.0


def test_for_sector_unknown_returns_fallback():
    config = SectorRulesConfig()
    assert config.for_sector("Cryptozoology") is FALLBACK_RULES


def test_for_sector_none_returns_fallback():
    config = SectorRulesConfig()
    assert config.for_sector(None) is FALLBACK_RULES


def test_for_sector_na_returns_fallback():
    config = SectorRulesConfig()
    assert config.for_sector("N/A") is FALLBACK_RULES


# ---------------------------------------------------------------------------
# passes_sector_filter — casos por sector
# ---------------------------------------------------------------------------

def test_tech_strong_passes():
    config = SectorRulesConfig()
    passes, reason = passes_sector_filter(
        "Technology", margen=70.0, roe=25.0, deuda=0.2, config=config,
    )
    assert passes
    assert reason is None


def test_tech_low_margin_fails():
    config = SectorRulesConfig()
    passes, reason = passes_sector_filter(
        "Technology", margen=30.0, roe=25.0, deuda=0.2, config=config,
    )
    assert not passes
    assert "Margen" in reason


def test_financial_services_ignores_debt():
    """Bancos: deuda altísima no debe ser problema."""
    config = SectorRulesConfig()
    passes, _ = passes_sector_filter(
        "Financial Services", margen=0.0, roe=12.0, deuda=10.0, config=config,
    )
    assert passes


def test_financial_services_still_requires_roe():
    """Bancos sí necesitan ROE decente."""
    config = SectorRulesConfig()
    passes, reason = passes_sector_filter(
        "Financial Services", margen=0.0, roe=3.0, deuda=10.0, config=config,
    )
    assert not passes
    assert "ROE" in reason


def test_real_estate_ignores_margin():
    """REITs no se evalúan por margen."""
    config = SectorRulesConfig()
    passes, _ = passes_sector_filter(
        "Real Estate", margen=0.0, roe=8.0, deuda=2.5, config=config,
    )
    assert passes


def test_real_estate_high_debt_acceptable():
    """REITs aceptan deuda hasta 3x."""
    config = SectorRulesConfig()
    passes, _ = passes_sector_filter(
        "Real Estate", margen=0.0, roe=8.0, deuda=2.9, config=config,
    )
    assert passes


def test_real_estate_extreme_debt_fails():
    """Deuda absurdamente alta sí debe fallar."""
    config = SectorRulesConfig()
    passes, reason = passes_sector_filter(
        "Real Estate", margen=0.0, roe=8.0, deuda=5.0, config=config,
    )
    assert not passes
    assert "D/C" in reason


def test_utility_high_debt_ok():
    config = SectorRulesConfig()
    passes, _ = passes_sector_filter(
        "Utilities", margen=25.0, roe=10.0, deuda=1.5, config=config,
    )
    assert passes


def test_industrial_thresholds_apply():
    """Industrials aceptan deuda ≤ 1.5 y margen ≥ 20."""
    config = SectorRulesConfig()
    # Acepta
    p1, _ = passes_sector_filter("Industrials", 22.0, 13.0, 1.2, config)
    assert p1
    # Rechaza por margen
    p2, r2 = passes_sector_filter("Industrials", 15.0, 13.0, 1.2, config)
    assert not p2 and "Margen" in r2


def test_energy_low_margin_acceptable():
    """Energy acepta margen ≥ 15% (ROE ≥ 10%, D/C ≤ 0.7)."""
    config = SectorRulesConfig()
    passes, _ = passes_sector_filter(
        "Energy", margen=18.0, roe=12.0, deuda=0.5, config=config,
    )
    assert passes


# ---------------------------------------------------------------------------
# Override de reglas
# ---------------------------------------------------------------------------

def test_custom_rules_override_defaults():
    """Permitir reemplazar la regla de un sector específico."""
    custom = dict(DEFAULT_SECTOR_RULES)
    custom["Technology"] = SectorThresholds(
        roe_min=5.0, deuda_max=2.0, margen_min=10.0,
        descripcion="Reglas relajadas para test",
    )
    config = SectorRulesConfig(rules=custom)
    # Tech con margen 15% pasaría con custom (default exige 50%)
    passes, _ = passes_sector_filter(
        "Technology", margen=15.0, roe=8.0, deuda=1.0, config=config,
    )
    assert passes
