"""Obtención del universo de tickers desde la SEC con cache local."""
from __future__ import annotations

import logging
import random
from datetime import datetime
from pathlib import Path

import requests

from .config import SEC_TICKERS_URL, TradingRules
from .database import open_db

log = logging.getLogger(__name__)


def fetch_sec_universe(rules: TradingRules, db_path: Path) -> list[str]:
    """Devuelve el universo de análisis: portafolio foco + muestra aleatoria.

    Si la cache local tiene menos de `rules.cache_dias` días y al menos
    1.000 entradas, la usa. Si no, descarga de la SEC. Si la SEC falla,
    cae a modo emergencia (solo portafolio foco).
    """
    cached = _load_cache_if_fresh(db_path, rules.cache_dias)

    if cached:
        log.info("Cache válida — %d tickers de la SEC en local.", len(cached))
        all_tickers = cached
    else:
        log.info("Cache caducada — descargando registro oficial de la SEC...")
        try:
            all_tickers = _download_sec_tickers(rules)
        except requests.RequestException as exc:
            log.error("Falla SEC (%s): %s", exc.__class__.__name__, exc)
            log.warning("Modo emergencia: solo Portafolio Foco.")
            return list(rules.portafolio_foco)

        _replace_cache(db_path, all_tickers)
        log.info("Cache SEC actualizada con %d empresas.", len(all_tickers))

    sample_size = min(rules.tamanio_lote, len(all_tickers))
    sample = random.sample(all_tickers, sample_size)

    # Deduplica pero preserva orden: foco primero, luego exploración.
    universe: list[str] = []
    seen: set[str] = set()
    for ticker in (*rules.portafolio_foco, *sample):
        if ticker not in seen:
            universe.append(ticker)
            seen.add(ticker)

    return universe


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def invalidate_cache(db_path: Path) -> None:
    """Borra la cache SEC, forzando re-descarga en la siguiente corrida."""
    with open_db(db_path) as conn:
        conn.execute("DELETE FROM sec_cache")


def _load_cache_if_fresh(db_path: Path, max_age_days: int) -> list[str]:
    """Lee la cache si tiene menos de `max_age_days` y >1.000 tickers."""
    with open_db(db_path) as conn:
        rows = conn.execute(
            "SELECT ticker FROM sec_cache "
            "WHERE fecha_actualizacion >= date('now', ?)",
            (f"-{max_age_days} days",),
        ).fetchall()

    return [row["ticker"] for row in rows] if len(rows) > 1000 else []


def _download_sec_tickers(rules: TradingRules) -> list[str]:
    """Descarga el JSON oficial de la SEC y normaliza los símbolos."""
    response = requests.get(
        SEC_TICKERS_URL,
        headers={"User-Agent": rules.sec_user_agent},
        timeout=rules.request_timeout,
    )
    response.raise_for_status()
    data = response.json()

    # SEC usa "BRK.B"; Yahoo Finance usa "BRK-B".
    return [company["ticker"].replace(".", "-") for company in data.values()]


def _replace_cache(db_path: Path, tickers: list[str]) -> None:
    """Reemplaza la cache completa con `executemany` (batch insert).

    Deduplica preservando el orden de aparición — la SEC a veces lista
    múltiples clases de acciones que normalizan al mismo símbolo
    (p.ej. dos filas que ambas resuelven a "BRK-B"). Sin deduplicación,
    el insert violaría el PK.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    seen: set[str] = set()
    unique_tickers: list[str] = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)

    duplicates = len(tickers) - len(unique_tickers)
    if duplicates:
        log.info("SEC: %d tickers duplicados eliminados.", duplicates)

    with open_db(db_path) as conn:
        conn.execute("DELETE FROM sec_cache")
        # INSERT OR REPLACE como red de seguridad por si hay otra
        # fuente de duplicación que no detectamos.
        conn.executemany(
            "INSERT OR REPLACE INTO sec_cache (ticker, fecha_actualizacion) "
            "VALUES (?, ?)",
            [(t, today) for t in unique_tickers],
        )
