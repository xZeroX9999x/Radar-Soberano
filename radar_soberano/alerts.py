"""Notificaciones por email y Telegram.

Configuración via:
  - Variables de entorno (SMTP_*, TELEGRAM_*)
  - Archivo `.env` en el cwd
  - API web (formulario que escribe `.env`)

Mantiene `LOCAL` por default — no se manda nada hasta que el usuario
configure al menos un canal. Manda solo si hay señales reales (no spam).
"""
from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import requests

from .portfolio import SellSignal

log = logging.getLogger(__name__)

DEFAULT_ENV_PATH = Path(".env")


@dataclass
class AlertConfig:
    """Configuración cargada de variables de entorno o .env."""

    # Email (SMTP)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_to: Optional[str] = None
    smtp_use_tls: bool = True

    # Telegram
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @property
    def email_enabled(self) -> bool:
        return all([
            self.smtp_host, self.smtp_user, self.smtp_password, self.smtp_to,
        ])

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    @property
    def any_enabled(self) -> bool:
        return self.email_enabled or self.telegram_enabled


def load_alert_config(env_path: Path = DEFAULT_ENV_PATH) -> AlertConfig:
    """Carga config desde variables de entorno + archivo .env (si existe)."""
    env: dict[str, str] = dict(os.environ)

    # Parsear .env si existe (formato KEY=VALUE, ignora # comentarios)
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    # Las variables de entorno tienen precedencia sobre .env
                    env.setdefault(k, v)
        except Exception as exc:
            log.warning("No pude leer %s: %s", env_path, exc)

    return AlertConfig(
        smtp_host=env.get("SMTP_HOST"),
        smtp_port=int(env.get("SMTP_PORT", "587")),
        smtp_user=env.get("SMTP_USER"),
        smtp_password=env.get("SMTP_PASSWORD"),
        smtp_to=env.get("SMTP_TO") or env.get("SMTP_USER"),
        smtp_use_tls=env.get("SMTP_USE_TLS", "true").lower() == "true",
        telegram_token=env.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID"),
    )


def save_alert_config(config: dict, env_path: Path = DEFAULT_ENV_PATH) -> None:
    """Guarda configuración en .env (sobrescribe). Usado por el endpoint web.

    Nunca loggea los valores — son credenciales.
    """
    keys = {
        "smtp_host": "SMTP_HOST",
        "smtp_port": "SMTP_PORT",
        "smtp_user": "SMTP_USER",
        "smtp_password": "SMTP_PASSWORD",
        "smtp_to": "SMTP_TO",
        "smtp_use_tls": "SMTP_USE_TLS",
        "telegram_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
    }

    lines = ["# Radar Soberano — credenciales de alertas (autogenerado)"]
    for src, target in keys.items():
        value = config.get(src)
        if value is None or value == "":
            continue
        # Escapar valores con espacios
        if " " in str(value):
            lines.append(f'{target}="{value}"')
        else:
            lines.append(f"{target}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Permisos restrictivos: solo el dueño puede leer (0600)
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass  # Windows / sistemas que no soportan chmod tradicional


# ============================================================
# Senders
# ============================================================

def send_email(
    config: AlertConfig,
    subject: str,
    body: str,
    body_html: Optional[str] = None,
) -> tuple[bool, str]:
    """Envía un email. Devuelve (ok, mensaje)."""
    if not config.email_enabled:
        return False, "Email no configurado (faltan SMTP_*)"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.smtp_user
    msg["To"] = config.smtp_to
    msg.set_content(body)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=15) as s:
                s.login(config.smtp_user, config.smtp_password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as s:
                if config.smtp_use_tls:
                    s.starttls()
                s.login(config.smtp_user, config.smtp_password)
                s.send_message(msg)
        return True, "Email enviado"
    except Exception as exc:
        log.warning("Fallo email: %s: %s", exc.__class__.__name__, exc)
        return False, f"{exc.__class__.__name__}: {exc}"


def send_telegram(
    config: AlertConfig,
    text: str,
) -> tuple[bool, str]:
    """Envía un mensaje por Telegram bot. Devuelve (ok, mensaje)."""
    if not config.telegram_enabled:
        return False, "Telegram no configurado"

    url = f"https://api.telegram.org/bot{config.telegram_token}/sendMessage"
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            return True, "Telegram enviado"
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        log.warning("Fallo telegram: %s: %s", exc.__class__.__name__, exc)
        return False, f"{exc.__class__.__name__}: {exc}"


# ============================================================
# Plantillas para señales de venta
# ============================================================

def format_sell_alert(signals: list[SellSignal]) -> tuple[str, str, str, str]:
    """Construye (asunto, body texto, body html, body telegram) para n señales."""
    if not signals:
        return "", "", "", ""

    n = len(signals)
    subject = (
        f"🚨 Radar Soberano: {n} señal{'es' if n > 1 else ''} de venta"
    )

    # ---- Texto plano ----
    lines = [
        f"Tenés {n} posición{'es' if n > 1 else ''} con señal de venta:",
        "",
    ]
    for s in signals:
        p = s.position
        lines.extend([
            f"━━━ {p.ticker} ━━━",
            f"Compra:  {p.fecha_compra} @ ${p.precio_compra:.2f}",
            f"Actual:  ${s.current_price:.2f} ({s.pnl_pct:+.1f}%, "
            f"${s.pnl_dollars:+.2f})",
            f"Razón:   {s.reason_text}",
            "",
        ])
    lines.append("Generado por Radar Soberano.")
    body_text = "\n".join(lines)

    # ---- HTML ----
    rows = []
    for s in signals:
        p = s.position
        color = {
            "positive": "#00d97e", "negative": "#ff5757", "warning": "#f0b400",
        }.get(s.severity, "#5fa8ff")
        rows.append(f"""
            <div style="border-left:3px solid {color};padding:10px 14px;
                        background:#11171a;margin-bottom:10px;border-radius:3px;">
              <div style="font-size:18px;color:#c4f000;font-weight:700;">
                {p.ticker}
              </div>
              <div style="color:#d8e0e8;margin-top:4px;">
                Compra: {p.fecha_compra} @ ${p.precio_compra:.2f}<br>
                Actual: ${s.current_price:.2f}
                <span style="color:{color};font-weight:600;">
                  ({s.pnl_pct:+.1f}%, ${s.pnl_dollars:+.2f})
                </span>
              </div>
              <div style="margin-top:8px;color:#a8c0d4;">
                {s.reason_text}
              </div>
            </div>
        """)
    body_html = f"""
        <div style="font-family:monospace;background:#0a0e0d;color:#d8e0e8;
                    padding:24px;max-width:600px;">
          <h2 style="color:#c4f000;border-bottom:1px solid #2a3540;
                     padding-bottom:8px;">📡 RADAR SOBERANO</h2>
          <p>Tenés <strong>{n}</strong> señal{'es' if n > 1 else ''} de venta:</p>
          {''.join(rows)}
          <p style="font-size:11px;color:#7a8896;margin-top:20px;">
            Estas son señales algorítmicas — la decisión final de vender
            es tuya. Recordá impuestos y comisiones.
          </p>
        </div>
    """

    # ---- Telegram (HTML restringido pero soporta <b>, <code>) ----
    tg_lines = [f"🚨 <b>Radar Soberano — {n} señal(es) de venta</b>", ""]
    for s in signals:
        p = s.position
        tg_lines.extend([
            f"<b>{p.ticker}</b>",
            f"Compra: {p.fecha_compra} @ ${p.precio_compra:.2f}",
            f"Actual: ${s.current_price:.2f} "
            f"({s.pnl_pct:+.1f}%, ${s.pnl_dollars:+.2f})",
            f"<i>{s.reason_text}</i>",
            "",
        ])
    body_telegram = "\n".join(tg_lines)

    return subject, body_text, body_html, body_telegram


def send_sell_alerts(
    config: AlertConfig,
    signals: list[SellSignal],
) -> dict:
    """Envía alertas por todos los canales configurados.

    Devuelve dict con resultado por canal: {"email": (ok, msg), "telegram": ...}.
    """
    if not signals:
        return {"email": (None, "sin señales"), "telegram": (None, "sin señales")}

    if not config.any_enabled:
        log.info("Alertas no configuradas; omitiendo envío.")
        return {
            "email": (None, "no configurado"),
            "telegram": (None, "no configurado"),
        }

    subject, body_text, body_html, body_tg = format_sell_alert(signals)
    result: dict = {}

    if config.email_enabled:
        result["email"] = send_email(config, subject, body_text, body_html)
    else:
        result["email"] = (None, "no configurado")

    if config.telegram_enabled:
        result["telegram"] = send_telegram(config, body_tg)
    else:
        result["telegram"] = (None, "no configurado")

    return result


def send_test_message(
    config: AlertConfig,
    channel: str,
) -> tuple[bool, str]:
    """Envía un mensaje de prueba al canal indicado ('email' | 'telegram')."""
    if channel == "email":
        return send_email(
            config,
            "✅ Radar Soberano — test de alertas",
            "Este es un mensaje de prueba de Radar Soberano.\n\n"
            "Si lo recibiste, las alertas por email están funcionando.",
            "<p>✅ <strong>Radar Soberano</strong>: alertas por email funcionando.</p>",
        )
    if channel == "telegram":
        return send_telegram(
            config,
            "✅ <b>Radar Soberano</b>\n\nTest de alertas: funcionando.",
        )
    return False, f"Canal desconocido: {channel}"
