"""Reglas de filtrado fundamental específicas por sector.

Cada sector tiene su propia 'tarjeta de evaluación' con métricas realistas:
- Tech, Healthcare, Consumer: ROE + deuda + margen bruto.
- Energy, Materials, Industrials: ROE + deuda más permisiva, margen bajo.
- Utilities: ROE bajo aceptable, deuda alta aceptable (negocio regulado).
- REITs (Real Estate): se miden con FFO, no margen — solo ROE mínimo.
- Financial Services (bancos, aseguradoras): la deuda *es* su negocio,
  se ignora ese filtro. Solo se exige ROE.

Los umbrales son ajustables vía dataclass `SectorRules` que vive en
`config.TradingRules.sector_rules`. Se aplica fallback a `default_rules`
para sectores no mapeados o "N/A".
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SectorThresholds:
    """Umbrales fundamentales para un sector específico.

    Atributos:
        roe_min: ROE mínimo en % (15.0 = 15%).
        deuda_max: ratio Deuda/Capital máximo. None = sin límite (financieros).
        margen_min: margen bruto mínimo en %. None = no se evalúa (REITs).
        descripcion: explicación corta del sector.
    """

    roe_min: float
    deuda_max: float | None
    margen_min: float | None
    descripcion: str = ""


# ============================================================
# Tabla de sectores GICS (los que Yahoo Finance reporta)
# ============================================================

# Reglas calibradas con benchmarks típicos de cada industria.
# Comentarios al final indican el rationale del umbral.

DEFAULT_SECTOR_RULES: dict[str, SectorThresholds] = {
    "Technology": SectorThresholds(
        roe_min=15.0, deuda_max=0.5, margen_min=50.0,
        descripcion="Software, semis, hardware. Alto margen y baja deuda esperados.",
    ),
    "Communication Services": SectorThresholds(
        roe_min=12.0, deuda_max=1.0, margen_min=35.0,
        descripcion="Telecom, media, gaming. Apalancamiento medio aceptable.",
    ),
    "Healthcare": SectorThresholds(
        roe_min=12.0, deuda_max=0.6, margen_min=40.0,
        descripcion="Farma, biotech, equipos médicos. Márgenes altos por IP.",
    ),
    "Consumer Cyclical": SectorThresholds(
        roe_min=12.0, deuda_max=1.0, margen_min=25.0,
        descripcion="Retail, autos, viajes. Más apalancados por inventario y ciclo.",
    ),
    "Consumer Defensive": SectorThresholds(
        roe_min=12.0, deuda_max=1.0, margen_min=20.0,
        descripcion="Alimentos, bebidas, household. Márgenes finos pero estables.",
    ),
    "Industrials": SectorThresholds(
        roe_min=12.0, deuda_max=1.5, margen_min=20.0,
        descripcion="Manufactura, transporte, defensa. Capital intensivo.",
    ),
    "Energy": SectorThresholds(
        roe_min=10.0, deuda_max=0.7, margen_min=15.0,
        descripcion="Oil & gas, energía. Cíclico, capital intensivo.",
    ),
    "Basic Materials": SectorThresholds(
        roe_min=10.0, deuda_max=1.0, margen_min=15.0,
        descripcion="Mining, químicos, papel. Commodities con márgenes finos.",
    ),
    "Utilities": SectorThresholds(
        roe_min=8.0, deuda_max=2.0, margen_min=20.0,
        descripcion="Eléctricas, gas, agua. Reguladas, alto apalancamiento normal.",
    ),
    "Real Estate": SectorThresholds(
        roe_min=5.0, deuda_max=3.0, margen_min=None,
        descripcion="REITs. Operan endeudados por diseño; se miden con FFO no margen.",
    ),
    "Financial Services": SectorThresholds(
        roe_min=10.0, deuda_max=None, margen_min=None,
        descripcion="Bancos, aseguradoras, brokers. La deuda es su modelo de negocio.",
    ),
}

# Fallback para sectores no clasificados (incluye "N/A")
FALLBACK_RULES = SectorThresholds(
    roe_min=10.0, deuda_max=1.0, margen_min=None,
    descripcion="Sector desconocido o no clasificado.",
)


@dataclass(frozen=True)
class SectorRulesConfig:
    """Configuración completa de reglas por sector.

    Permite override de sectores específicos sin tocar el resto.
    """

    rules: dict[str, SectorThresholds] = field(
        default_factory=lambda: dict(DEFAULT_SECTOR_RULES)
    )
    fallback: SectorThresholds = FALLBACK_RULES

    def for_sector(self, sector: str | None) -> SectorThresholds:
        """Devuelve los umbrales aplicables al sector dado."""
        if not sector or sector == "N/A":
            return self.fallback
        return self.rules.get(sector, self.fallback)


def passes_sector_filter(
    sector: str | None,
    margen: float,
    roe: float,
    deuda: float,
    config: SectorRulesConfig,
) -> tuple[bool, str | None]:
    """Evalúa si la empresa pasa el filtro fundamental de su sector.

    Args:
        sector: nombre del sector según Yahoo Finance.
        margen: margen bruto en % (no decimal).
        roe: ROE en % (no decimal).
        deuda: ratio Deuda/Capital (decimal, p.ej. 0.5 = 50%).
        config: configuración de sectores.

    Returns:
        (passes, reason). Si passes=False, reason indica qué umbral falló.
    """
    thresholds = config.for_sector(sector)

    if roe < thresholds.roe_min:
        return False, f"ROE {roe:.1f}% < {thresholds.roe_min:.1f}% ({sector})"

    if thresholds.deuda_max is not None and deuda > thresholds.deuda_max:
        return False, (
            f"D/C {deuda:.2f} > {thresholds.deuda_max} ({sector})"
        )

    if thresholds.margen_min is not None and margen < thresholds.margen_min:
        return False, (
            f"Margen {margen:.1f}% < {thresholds.margen_min:.1f}% ({sector})"
        )

    return True, None
