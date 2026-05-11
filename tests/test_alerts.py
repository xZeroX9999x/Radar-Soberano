"""Tests para el módulo de alertas.

No tocamos red — testeamos solo el formateo, parseo de config y
plantillas.
"""
from __future__ import annotations

import os

import pytest

from radar_soberano.alerts import (
    AlertConfig,
    format_sell_alert,
    load_alert_config,
    save_alert_config,
)
from radar_soberano.portfolio import Position, SellSignal


# ---------------------------------------------------------------------------
# AlertConfig
# ---------------------------------------------------------------------------

def test_email_disabled_by_default():
    config = AlertConfig()
    assert config.email_enabled is False
    assert config.telegram_enabled is False
    assert config.any_enabled is False


def test_email_enabled_when_all_fields_set():
    config = AlertConfig(
        smtp_host="smtp.gmail.com",
        smtp_user="user@gmail.com",
        smtp_password="pwd",
        smtp_to="to@gmail.com",
    )
    assert config.email_enabled is True


def test_email_disabled_when_one_field_missing():
    config = AlertConfig(
        smtp_host="smtp.gmail.com",
        smtp_user="user@gmail.com",
        # smtp_password missing
        smtp_to="to@gmail.com",
    )
    assert config.email_enabled is False


def test_telegram_enabled_when_token_and_chat_id():
    config = AlertConfig(
        telegram_token="123:abc",
        telegram_chat_id="456",
    )
    assert config.telegram_enabled is True


# ---------------------------------------------------------------------------
# load_alert_config
# ---------------------------------------------------------------------------

def test_load_from_env_file(tmp_path, monkeypatch):
    """Limpia env vars conocidas y carga desde archivo."""
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_TO",
              "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SMTP_HOST=smtp.example.com\n"
        "SMTP_USER=user@example.com\n"
        'SMTP_PASSWORD="my password"\n'
        "TELEGRAM_BOT_TOKEN=abc:123\n"
        "TELEGRAM_CHAT_ID=999\n"
        "# comentario ignorado\n"
    )
    config = load_alert_config(env_path=env_file)
    assert config.smtp_host == "smtp.example.com"
    assert config.smtp_user == "user@example.com"
    assert config.smtp_password == "my password"
    assert config.telegram_token == "abc:123"
    assert config.telegram_chat_id == "999"


def test_load_env_var_overrides_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SMTP_HOST=from-file\n")
    monkeypatch.setenv("SMTP_HOST", "from-env")
    config = load_alert_config(env_path=env_file)
    assert config.smtp_host == "from-env"


def test_load_nonexistent_file_returns_empty(tmp_path, monkeypatch):
    for k in ("SMTP_HOST", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    config = load_alert_config(env_path=tmp_path / "nope.env")
    assert config.smtp_host is None
    assert config.telegram_token is None


# ---------------------------------------------------------------------------
# save_alert_config
# ---------------------------------------------------------------------------

def test_save_writes_only_provided_fields(tmp_path):
    env_file = tmp_path / ".env"
    save_alert_config(
        {"smtp_host": "smtp.example.com", "telegram_token": "abc:123"},
        env_path=env_file,
    )
    content = env_file.read_text()
    assert "SMTP_HOST=smtp.example.com" in content
    assert "TELEGRAM_BOT_TOKEN=abc:123" in content
    # Campos vacíos no deberían aparecer
    assert "SMTP_PASSWORD=" not in content


def test_save_quotes_values_with_spaces(tmp_path):
    env_file = tmp_path / ".env"
    save_alert_config({"smtp_password": "my secret"}, env_path=env_file)
    content = env_file.read_text()
    assert 'SMTP_PASSWORD="my secret"' in content


def test_save_creates_file_with_restricted_permissions(tmp_path):
    """En sistemas POSIX, .env debe crearse con permisos 0600."""
    if os.name != "posix":
        pytest.skip("solo aplica a POSIX")
    env_file = tmp_path / ".env"
    save_alert_config({"smtp_host": "x"}, env_path=env_file)
    mode = env_file.stat().st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------------------
# format_sell_alert
# ---------------------------------------------------------------------------

def _mk_signal(ticker="NVDA", reason_code="TAKE_PROFIT", pnl_pct=15.0):
    pos = Position(
        id=1,
        ticker=ticker,
        fecha_compra="2026-01-15",
        precio_compra=200.0,
        cantidad=10,
        target_venta_pct=15.0,
        stop_loss_pct=8.0,
        estado="abierta",
        fecha_venta=None,
        precio_venta=None,
        pnl_realizado=None,
        notas=None,
    )
    return SellSignal(
        position=pos,
        current_price=230.0,
        pnl_pct=pnl_pct,
        pnl_dollars=300.0,
        reason_code=reason_code,
        reason_text=f"🎯 Test {reason_code}",
        severity="positive",
    )


def test_format_alert_includes_ticker_and_prices():
    signals = [_mk_signal(ticker="NVDA")]
    subject, body, html, tg = format_sell_alert(signals)
    assert "1 señal" in subject
    assert "NVDA" in body
    assert "200.00" in body  # precio compra
    assert "230.00" in body  # precio actual
    assert "NVDA" in html
    assert "NVDA" in tg


def test_format_multiple_signals():
    signals = [
        _mk_signal(ticker="NVDA"),
        _mk_signal(ticker="AAPL"),
        _mk_signal(ticker="GOOG"),
    ]
    subject, body, html, tg = format_sell_alert(signals)
    assert "3 señales" in subject
    for t in ("NVDA", "AAPL", "GOOG"):
        assert t in body
        assert t in html
        assert t in tg


def test_format_empty_signals_returns_empty():
    subject, body, html, tg = format_sell_alert([])
    assert subject == ""
    assert body == ""
