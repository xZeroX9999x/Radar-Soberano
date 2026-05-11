"""Capa de persistencia SQLite — conexiones gestionadas con `with`."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


# Esquema versionado: ticker + fecha como PK compuesto permite tracking histórico.
_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS mercado (
        ticker          TEXT NOT NULL,
        sector          TEXT,
        margen          REAL,
        roe             REAL,
        deuda           REAL,
        rsi             REAL,
        ma200           REAL,
        cierre          REAL,
        veredicto       TEXT,
        fecha           TEXT NOT NULL,
        pe              REAL,
        pb              REAL,
        pfcf            REAL,
        earnings_yield  REAL,
        PRIMARY KEY (ticker, fecha)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sec_cache (
        ticker               TEXT PRIMARY KEY,
        fecha_actualizacion  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mercado_fecha ON mercado(fecha)",
    "CREATE INDEX IF NOT EXISTS idx_mercado_veredicto ON mercado(veredicto)",
    "CREATE INDEX IF NOT EXISTS idx_sec_cache_fecha ON sec_cache(fecha_actualizacion)",
)

# Columnas que pueden faltar en bases creadas con versiones anteriores.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("pe", "REAL"),
    ("pb", "REAL"),
    ("pfcf", "REAL"),
    ("earnings_yield", "REAL"),
)


@contextmanager
def open_db(path: Path) -> Iterator[sqlite3.Connection]:
    """Abre una conexión SQLite que se commitea/cierra automáticamente.

    En caso de excepción dentro del bloque, hace rollback antes de cerrar.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(path: Path) -> None:
    """Crea tablas e índices y aplica migraciones si hace falta. Idempotente."""
    with open_db(path) as conn:
        for statement in _SCHEMA:
            conn.execute(statement)
        _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Añade columnas que no existan en la tabla `mercado`.

    SQLite anterior a 3.38 no soporta `ADD COLUMN IF NOT EXISTS`, así que
    inspeccionamos el esquema con PRAGMA y agregamos solo lo que falte.
    """
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(mercado)")
    }
    for col, col_type in _MIGRATIONS:
        if col not in existing:
            conn.execute(f"ALTER TABLE mercado ADD COLUMN {col} {col_type}")
