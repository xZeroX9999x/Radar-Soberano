"""Subcomando `history`: muestra evolución histórica de un ticker."""
from __future__ import annotations

import logging
from pathlib import Path

from .database import open_db

log = logging.getLogger(__name__)


def show_history(db_path: Path, ticker: str, limit: int = 30) -> int:
    """Imprime el histórico de un ticker desde la DB. Devuelve exit code."""
    ticker = ticker.upper()

    if not db_path.exists():
        log.error("Base de datos no existe: %s", db_path)
        log.error("Ejecutá `radar-soberano` al menos una vez para generarla.")
        return 1

    with open_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT fecha, cierre, rsi, ma200, veredicto, pe, pb, pfcf
            FROM mercado
            WHERE ticker = ?
            ORDER BY fecha DESC
            LIMIT ?
            """,
            (ticker, limit),
        ).fetchall()

    if not rows:
        log.warning("Sin historial para %s en %s", ticker, db_path)
        return 1

    log.info("Historial de %s (últimos %d registros):", ticker, len(rows))
    log.info(
        "%-12s %-10s %-7s %-10s %-30s %-7s %-7s %-7s",
        "Fecha", "Cierre", "RSI", "MA200", "Veredicto", "PE", "PB", "PFCF",
    )
    for r in rows:
        pe = f"{r['pe']:.1f}" if r["pe"] is not None else "-"
        pb = f"{r['pb']:.1f}" if r["pb"] is not None else "-"
        pfcf = f"{r['pfcf']:.1f}" if r["pfcf"] is not None else "-"
        veredicto = (r["veredicto"] or "").split(" |")[0]
        log.info(
            "%-12s %-10.2f %-7.1f %-10.2f %-30s %-7s %-7s %-7s",
            r["fecha"], r["cierre"], r["rsi"], r["ma200"],
            veredicto, pe, pb, pfcf,
        )
    return 0
