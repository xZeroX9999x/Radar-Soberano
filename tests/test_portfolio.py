"""Tests para tracking de posiciones y señales de venta."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from radar_soberano.portfolio import (
    Position,
    add_position,
    close_position,
    delete_position,
    evaluate_sell_signals,
    initialize_portfolio_schema,
    list_positions,
)


@pytest.fixture
def db(tmp_path):
    """DB temporal con schema inicializado."""
    p = tmp_path / "portfolio.db"
    initialize_portfolio_schema(p)
    return p


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_add_and_list_position(db):
    pid = add_position(db, ticker="NVDA", precio_compra=215.50, cantidad=10)
    assert pid > 0
    positions = list_positions(db)
    assert len(positions) == 1
    assert positions[0].ticker == "NVDA"
    assert positions[0].precio_compra == 215.50
    assert positions[0].cantidad == 10
    assert positions[0].estado == "abierta"


def test_add_position_normalizes_ticker_uppercase(db):
    pid = add_position(db, ticker="nvda", precio_compra=200.0)
    p = list_positions(db)[0]
    assert p.ticker == "NVDA"


def test_add_position_rejects_invalid_price(db):
    with pytest.raises(ValueError):
        add_position(db, ticker="NVDA", precio_compra=0)
    with pytest.raises(ValueError):
        add_position(db, ticker="NVDA", precio_compra=-10)


def test_add_position_rejects_invalid_quantity(db):
    with pytest.raises(ValueError):
        add_position(db, ticker="NVDA", precio_compra=200, cantidad=0)


def test_close_position_calculates_pnl(db):
    pid = add_position(db, ticker="NVDA", precio_compra=200.0, cantidad=10)
    closed = close_position(db, pid, precio_venta=230.0)
    assert closed.estado == "cerrada"
    assert closed.precio_venta == 230.0
    # PnL = (230 - 200) * 10 = 300
    assert closed.pnl_realizado == 300.0


def test_close_position_with_loss(db):
    pid = add_position(db, ticker="NVDA", precio_compra=200.0, cantidad=5)
    closed = close_position(db, pid, precio_venta=180.0)
    # PnL = (180 - 200) * 5 = -100
    assert closed.pnl_realizado == -100.0


def test_close_already_closed_position_fails(db):
    pid = add_position(db, ticker="NVDA", precio_compra=200.0)
    close_position(db, pid, precio_venta=210.0)
    with pytest.raises(ValueError):
        close_position(db, pid, precio_venta=215.0)


def test_close_nonexistent_position_fails(db):
    with pytest.raises(ValueError):
        close_position(db, 999, precio_venta=100.0)


def test_delete_position(db):
    pid = add_position(db, ticker="NVDA", precio_compra=200.0)
    assert delete_position(db, pid) is True
    assert delete_position(db, pid) is False
    assert list_positions(db) == []


def test_list_filters_by_estado(db):
    pid1 = add_position(db, ticker="NVDA", precio_compra=200.0)
    pid2 = add_position(db, ticker="AAPL", precio_compra=150.0)
    close_position(db, pid1, precio_venta=210.0)

    abiertas = list_positions(db, estado="abierta")
    cerradas = list_positions(db, estado="cerrada")
    assert len(abiertas) == 1 and abiertas[0].ticker == "AAPL"
    assert len(cerradas) == 1 and cerradas[0].ticker == "NVDA"


# ---------------------------------------------------------------------------
# evaluate_sell_signals
# ---------------------------------------------------------------------------

def _mk_position(
    ticker="NVDA",
    precio_compra=200.0,
    cantidad=10,
    target_venta_pct=15.0,
    stop_loss_pct=8.0,
    fecha_compra=None,
    estado="abierta",
):
    return Position(
        id=1,
        ticker=ticker,
        fecha_compra=fecha_compra or datetime.now().strftime("%Y-%m-%d"),
        precio_compra=precio_compra,
        cantidad=cantidad,
        target_venta_pct=target_venta_pct,
        stop_loss_pct=stop_loss_pct,
        estado=estado,
        fecha_venta=None,
        precio_venta=None,
        pnl_realizado=None,
        notas=None,
    )


def test_take_profit_signal_when_target_reached():
    pos = _mk_position(precio_compra=100.0, target_venta_pct=15.0)
    signal = evaluate_sell_signals(pos, current_price=115.0)
    assert signal is not None
    assert signal.reason_code == "TAKE_PROFIT"
    assert signal.severity == "positive"
    assert signal.pnl_pct == pytest.approx(15.0)


def test_take_profit_above_target():
    pos = _mk_position(precio_compra=100.0, target_venta_pct=15.0)
    signal = evaluate_sell_signals(pos, current_price=120.0)
    assert signal.reason_code == "TAKE_PROFIT"
    assert signal.pnl_pct == pytest.approx(20.0)


def test_stop_loss_signal_when_breached():
    pos = _mk_position(precio_compra=100.0, stop_loss_pct=8.0, cantidad=5)
    signal = evaluate_sell_signals(pos, current_price=92.0)
    assert signal is not None
    assert signal.reason_code == "STOP_LOSS"
    assert signal.severity == "negative"
    assert signal.pnl_pct == pytest.approx(-8.0)
    assert signal.pnl_dollars == pytest.approx(-40.0)


def test_overbought_signal_with_gain():
    pos = _mk_position(precio_compra=100.0)
    # +10% pero RSI muy alto
    signal = evaluate_sell_signals(pos, current_price=110.0, rsi=78.0)
    assert signal is not None
    assert signal.reason_code == "OVERBOUGHT"


def test_no_overbought_signal_without_decent_gain():
    """RSI alto pero ganancia <5% no debería gatillarse."""
    pos = _mk_position(precio_compra=100.0)
    signal = evaluate_sell_signals(pos, current_price=103.0, rsi=80.0)
    assert signal is None


def test_broken_trend_signal():
    pos = _mk_position(precio_compra=100.0)
    # Bajó pero no llegó al stop loss; cierre bajo MA200
    signal = evaluate_sell_signals(pos, current_price=95.0, ma200=98.0)
    assert signal is not None
    assert signal.reason_code == "BROKEN_TREND"


def test_dead_money_signal():
    """Posición vieja sin movimiento significativo."""
    old_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    pos = _mk_position(precio_compra=100.0, fecha_compra=old_date)
    signal = evaluate_sell_signals(pos, current_price=102.0)
    assert signal is not None
    assert signal.reason_code == "DEAD_MONEY"


def test_no_signal_for_normal_position():
    pos = _mk_position(precio_compra=100.0)
    signal = evaluate_sell_signals(pos, current_price=105.0, rsi=55, ma200=98)
    assert signal is None


def test_no_signal_for_closed_position():
    pos = _mk_position(precio_compra=100.0, estado="cerrada")
    signal = evaluate_sell_signals(pos, current_price=120.0)
    assert signal is None


def test_take_profit_priority_over_overbought():
    """Si gatilla take profit Y sobrecompra, prevalece take profit."""
    pos = _mk_position(precio_compra=100.0, target_venta_pct=10.0)
    signal = evaluate_sell_signals(pos, current_price=115.0, rsi=80.0)
    assert signal.reason_code == "TAKE_PROFIT"


def test_stop_loss_priority_over_broken_trend():
    """Si gatilla stop Y rotura de tendencia, prevalece stop."""
    pos = _mk_position(precio_compra=100.0, stop_loss_pct=5.0)
    signal = evaluate_sell_signals(pos, current_price=92.0, ma200=98.0)
    assert signal.reason_code == "STOP_LOSS"
