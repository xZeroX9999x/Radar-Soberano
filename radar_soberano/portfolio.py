"""Tracking de posiciones — compras del usuario y señales de venta.

Almacena las posiciones que el usuario reporta haber comprado. En cada
escaneo, evalúa si alguna posición abierta cumple criterios de venta:

  - 🎯 TAKE PROFIT: precio actual ≥ target (default +15%)
  - 🛑 STOP LOSS:   precio actual ≤ stop (default -8%, regla de O'Neil)
  - 🔴 SOBRECOMPRA: RSI > 75 con ganancia > 5%
  - 📉 ROTURA TENDENCIA: cierre cae bajo MA200
  - ⏰ TIEMPO MUERTO: posición > 365 días sin moverse ±5%

Cuando alguna se gatilla, se notifica vía las alertas configuradas.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .database import open_db

log = logging.getLogger(__name__)

# Reglas por defecto (configurables por posición individual)
DEFAULT_TAKE_PROFIT_PCT = 15.0   # +15% sobre precio compra
DEFAULT_STOP_LOSS_PCT = 8.0      # -8% bajo precio compra (Bill O'Neil)
RSI_OVERBOUGHT_EXIT = 75.0
DEAD_MONEY_DAYS = 365            # >1 año sin moverse → "tiempo muerto"
DEAD_MONEY_PCT = 5.0             # ±5% define "sin moverse"


@dataclass
class Position:
    """Posición de portfolio (abierta o cerrada)."""

    id: int
    ticker: str
    fecha_compra: str
    precio_compra: float
    cantidad: float
    target_venta_pct: float
    stop_loss_pct: float
    estado: str  # 'abierta' | 'cerrada'
    fecha_venta: Optional[str]
    precio_venta: Optional[float]
    pnl_realizado: Optional[float]
    notas: Optional[str]


@dataclass
class SellSignal:
    """Señal de venta detectada para una posición abierta."""

    position: Position
    current_price: float
    pnl_pct: float          # ganancia/pérdida no realizada en %
    pnl_dollars: float      # ganancia/pérdida en $ (sin impuestos)
    reason_code: str        # 'TAKE_PROFIT' | 'STOP_LOSS' | 'OVERBOUGHT' | ...
    reason_text: str        # mensaje legible para el usuario
    severity: str           # 'positive' | 'negative' | 'warning'


# ============================================================
# Schema y migración
# ============================================================

POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS posiciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    fecha_compra    TEXT NOT NULL,
    precio_compra   REAL NOT NULL,
    cantidad        REAL NOT NULL DEFAULT 1,
    target_venta_pct REAL NOT NULL DEFAULT 15.0,
    stop_loss_pct   REAL NOT NULL DEFAULT 8.0,
    estado          TEXT NOT NULL DEFAULT 'abierta',
    fecha_venta     TEXT,
    precio_venta    REAL,
    pnl_realizado   REAL,
    notas           TEXT
)
"""

POSITIONS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_posiciones_ticker ON posiciones(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_posiciones_estado ON posiciones(estado)",
)


def initialize_portfolio_schema(db_path: Path) -> None:
    """Crea la tabla de posiciones si no existe."""
    with open_db(db_path) as conn:
        conn.execute(POSITIONS_SCHEMA)
        for stmt in POSITIONS_INDEXES:
            conn.execute(stmt)


# ============================================================
# CRUD
# ============================================================

def add_position(
    db_path: Path,
    ticker: str,
    precio_compra: float,
    cantidad: float = 1.0,
    fecha_compra: Optional[str] = None,
    target_venta_pct: float = DEFAULT_TAKE_PROFIT_PCT,
    stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
    notas: Optional[str] = None,
) -> int:
    """Registra una nueva posición. Devuelve el id asignado."""
    if precio_compra <= 0:
        raise ValueError("precio_compra debe ser > 0")
    if cantidad <= 0:
        raise ValueError("cantidad debe ser > 0")
    if not (0 < target_venta_pct < 1000):
        raise ValueError("target_venta_pct debe estar entre 0 y 1000")
    if not (0 < stop_loss_pct < 100):
        raise ValueError("stop_loss_pct debe estar entre 0 y 100")

    fecha = fecha_compra or datetime.now().strftime("%Y-%m-%d")
    initialize_portfolio_schema(db_path)

    with open_db(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO posiciones (
                ticker, fecha_compra, precio_compra, cantidad,
                target_venta_pct, stop_loss_pct, notas
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker.upper(), fecha, precio_compra, cantidad,
                target_venta_pct, stop_loss_pct, notas,
            ),
        )
        return cursor.lastrowid


def list_positions(
    db_path: Path,
    estado: Optional[str] = None,
) -> list[Position]:
    """Lista posiciones, opcionalmente filtradas por estado."""
    initialize_portfolio_schema(db_path)
    query = "SELECT * FROM posiciones"
    params: tuple = ()
    if estado:
        query += " WHERE estado = ?"
        params = (estado,)
    query += " ORDER BY fecha_compra DESC, id DESC"

    with open_db(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [_row_to_position(r) for r in rows]


def close_position(
    db_path: Path,
    position_id: int,
    precio_venta: float,
    fecha_venta: Optional[str] = None,
) -> Position:
    """Marca una posición como cerrada y calcula PnL realizado."""
    initialize_portfolio_schema(db_path)
    fecha = fecha_venta or datetime.now().strftime("%Y-%m-%d")

    with open_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM posiciones WHERE id = ?", (position_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Posición {position_id} no existe")
        if row["estado"] == "cerrada":
            raise ValueError(f"Posición {position_id} ya está cerrada")

        pnl = (precio_venta - row["precio_compra"]) * row["cantidad"]

        conn.execute(
            """
            UPDATE posiciones
            SET estado = 'cerrada',
                precio_venta = ?,
                fecha_venta = ?,
                pnl_realizado = ?
            WHERE id = ?
            """,
            (precio_venta, fecha, pnl, position_id),
        )

        updated = conn.execute(
            "SELECT * FROM posiciones WHERE id = ?", (position_id,)
        ).fetchone()

    return _row_to_position(updated)


def delete_position(db_path: Path, position_id: int) -> bool:
    """Elimina una posición (typo del usuario, etc). Devuelve True si existía."""
    initialize_portfolio_schema(db_path)
    with open_db(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM posiciones WHERE id = ?", (position_id,)
        )
        return cursor.rowcount > 0


# ============================================================
# Detección de señales de venta
# ============================================================

def evaluate_sell_signals(
    position: Position,
    current_price: float,
    rsi: Optional[float] = None,
    ma200: Optional[float] = None,
) -> Optional[SellSignal]:
    """Evalúa si una posición debe venderse.

    Devuelve la primera señal aplicable (TAKE_PROFIT > STOP_LOSS >
    OVERBOUGHT > BROKEN_TREND > DEAD_MONEY) o None si nada se gatilla.
    """
    if position.estado != "abierta":
        return None

    pnl_pct = ((current_price - position.precio_compra) / position.precio_compra) * 100
    pnl_dollars = (current_price - position.precio_compra) * position.cantidad

    target_price = position.precio_compra * (1 + position.target_venta_pct / 100)
    stop_price = position.precio_compra * (1 - position.stop_loss_pct / 100)

    # 1. TAKE PROFIT — el más prioritario
    if current_price >= target_price:
        return SellSignal(
            position=position,
            current_price=current_price,
            pnl_pct=pnl_pct,
            pnl_dollars=pnl_dollars,
            reason_code="TAKE_PROFIT",
            reason_text=(
                f"🎯 TAKE PROFIT alcanzado: ${current_price:.2f} ≥ "
                f"${target_price:.2f} (+{pnl_pct:.1f}%, "
                f"${pnl_dollars:+.2f})"
            ),
            severity="positive",
        )

    # 2. STOP LOSS — proteger capital
    if current_price <= stop_price:
        return SellSignal(
            position=position,
            current_price=current_price,
            pnl_pct=pnl_pct,
            pnl_dollars=pnl_dollars,
            reason_code="STOP_LOSS",
            reason_text=(
                f"🛑 STOP LOSS gatillado: ${current_price:.2f} ≤ "
                f"${stop_price:.2f} ({pnl_pct:+.1f}%, ${pnl_dollars:+.2f}). "
                "Cortar la pérdida."
            ),
            severity="negative",
        )

    # 3. SOBRECOMPRA fuerte con ganancia decente
    if rsi is not None and rsi > RSI_OVERBOUGHT_EXIT and pnl_pct > 5.0:
        return SellSignal(
            position=position,
            current_price=current_price,
            pnl_pct=pnl_pct,
            pnl_dollars=pnl_dollars,
            reason_code="OVERBOUGHT",
            reason_text=(
                f"🔴 RSI sobrecompra ({rsi:.1f} > 75) con ganancia "
                f"{pnl_pct:+.1f}%. Considerar tomar ganancias."
            ),
            severity="warning",
        )

    # 4. Rotura de tendencia: cierre bajo MA200
    if ma200 is not None and current_price < ma200 and pnl_pct < 0:
        return SellSignal(
            position=position,
            current_price=current_price,
            pnl_pct=pnl_pct,
            pnl_dollars=pnl_dollars,
            reason_code="BROKEN_TREND",
            reason_text=(
                f"📉 Tendencia rota: cierre ${current_price:.2f} < "
                f"MA200 ${ma200:.2f}. Reevaluar tesis."
            ),
            severity="warning",
        )

    # 5. Tiempo muerto
    try:
        compra_date = datetime.strptime(position.fecha_compra, "%Y-%m-%d")
        days_held = (datetime.now() - compra_date).days
        if days_held > DEAD_MONEY_DAYS and abs(pnl_pct) < DEAD_MONEY_PCT:
            return SellSignal(
                position=position,
                current_price=current_price,
                pnl_pct=pnl_pct,
                pnl_dollars=pnl_dollars,
                reason_code="DEAD_MONEY",
                reason_text=(
                    f"⏰ {days_held} días sin movimiento "
                    f"({pnl_pct:+.1f}%). Capital muerto — "
                    "considerar rotar a otra oportunidad."
                ),
                severity="warning",
            )
    except ValueError:
        pass

    return None


# ============================================================
# Helpers
# ============================================================

def _row_to_position(row) -> Position:
    return Position(
        id=row["id"],
        ticker=row["ticker"],
        fecha_compra=row["fecha_compra"],
        precio_compra=float(row["precio_compra"]),
        cantidad=float(row["cantidad"]),
        target_venta_pct=float(row["target_venta_pct"]),
        stop_loss_pct=float(row["stop_loss_pct"]),
        estado=row["estado"],
        fecha_venta=row["fecha_venta"],
        precio_venta=float(row["precio_venta"]) if row["precio_venta"] else None,
        pnl_realizado=float(row["pnl_realizado"]) if row["pnl_realizado"] else None,
        notas=row["notas"],
    )
