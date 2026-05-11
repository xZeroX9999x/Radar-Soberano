"""Configuración del motor — paths por defecto y reglas duras."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .sector_rules import SectorRulesConfig

# ============================================================
# Rutas por defecto (sobrescribibles por CLI)
# ============================================================
DEFAULT_DB_PATH = Path("infraestructura_mercado.db")
DEFAULT_CSV_PATH = Path("radar_oportunidades_globales.csv")
DEFAULT_LOG_PATH = Path("radar_sistema.log")

# URL del registro oficial de la SEC
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass(frozen=True)
class TradingRules:
    """Reglas duras del motor quantamental.

    Todos los campos tienen valores por defecto; sobrescribir solo los que
    haga falta. Inmutable (`frozen=True`) para evitar mutaciones accidentales
    en tiempo de ejecución.
    """

    # --- Filtros fundamentales por sector ---
    # Cada sector (Technology, Energy, Financial Services, etc.) tiene
    # umbrales propios calibrados según benchmarks de su industria.
    # Ver sector_rules.DEFAULT_SECTOR_RULES.
    sector_rules: SectorRulesConfig = field(default_factory=SectorRulesConfig)

    # --- Filtros legacy (deprecados, conservados por compatibilidad) ---
    # Usados solo si sector_rules es None. NO modificar para casos nuevos.
    margen_min_tech: float = 50.0
    roe_min: float = 15.0
    deuda_max: float = 0.5

    # --- Filtro técnico ---
    rsi_period: int = 14
    rsi_entrada: float = 35.0
    rsi_sobrecompra: float = 75.0
    ma_period: int = 200

    # --- Universo de análisis ---
    portafolio_foco: tuple[str, ...] = (
        "NVDA", "TTWO", "IBIT", "PLTR", "BBAI",
    )
    tamanio_lote: int = 60
    cache_dias: int = 30

    # --- Networking ---
    sec_user_agent: str = "RadarSoberano/3.0 (contact@example.com)"
    request_timeout: int = 15
    request_delay: float = 0.4
