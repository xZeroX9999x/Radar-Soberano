"""Servidor web FastAPI que expone el motor Radar Soberano.

Endpoints:
  GET  /                  → frontend HTML
  GET  /api/status        → cache info, treasury yield, defaults
  POST /api/scan          → inicia scan en background, devuelve job_id
  GET  /api/scan/{job_id} → estado del job + resultados si finalizó
  POST /api/refresh-sec   → fuerza re-descarga del listado SEC
  GET  /api/history/:tk   → histórico de un ticker desde DB
  GET  /api/csv           → descarga el CSV más reciente
  GET  /api/sectors       → lista de sectores presentes en la DB
  WS   /ws/logs/{job_id}  → stream de logs del scan en vivo
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..analyzer import run_radar
from ..buffett import BuffettCriteria, fetch_treasury_yield_10y
from ..config import (
    DEFAULT_CSV_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_LOG_PATH,
    TradingRules,
)
from ..database import initialize, open_db
from ..universe import fetch_sec_universe, invalidate_cache

log = logging.getLogger(__name__)

# Carpeta de archivos estáticos (frontend)
STATIC_DIR = Path(__file__).parent / "static"

# ============================================================
# Estado del servidor (vive en app.state, aislable por instancia)
# ============================================================


class JobState:
    """Estado de un job de escaneo."""

    def __init__(self, job_id: str, params: dict[str, Any]):
        self.job_id = job_id
        self.params = params
        self.status = "pending"  # pending | running | done | error
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.error: str | None = None
        self.snapshots: list[dict] = []
        self.stats: dict = {}

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "params": self.params,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "snapshots": self.snapshots,
            "stats": self.stats,
        }


class _QueueLogHandler(logging.Handler):
    """Logging handler que pushea cada record a una asyncio.Queue."""

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.queue = queue
        self.loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            # call_soon_threadsafe porque el log viene de otro thread
            self.loop.call_soon_threadsafe(self.queue.put_nowait, msg)
        except Exception:
            pass  # nunca dejar que un fallo de log rompa el scan


# ============================================================
# Pydantic models para los endpoints
# ============================================================

class ScanRequest(BaseModel):
    lote: int = Field(default=60, ge=0, le=500)
    seed: int | None = None
    sector: str | None = None
    buffett: bool = False
    pb_max: float = 3.0
    pfcf_max: float = 20.0
    ey_premium: float = 0.0
    min_history: int = 10


class StatusResponse(BaseModel):
    version: str
    cache_size: int
    cache_updated: str | None
    treasury_yield: float | None
    defaults: dict


# ============================================================
# Lógica de negocio (envoltorios al motor)
# ============================================================

def _run_scan_blocking(job: JobState, db_path: Path, csv_path: Path) -> None:
    """Corre el scan en un thread (bloqueante). Llamado vía run_in_executor."""
    p = job.params
    rules = TradingRules(tamanio_lote=p["lote"])

    buffett_criteria = None
    treasury_yield = None
    if p["buffett"]:
        buffett_criteria = BuffettCriteria(
            pb_max=p["pb_max"],
            pfcf_max=p["pfcf_max"],
            earnings_yield_premium_pp=p["ey_premium"],
            min_history_years=p["min_history"],
        )
        treasury_yield = fetch_treasury_yield_10y()

    if p["seed"] is not None:
        random.seed(p["seed"])

    sectores = None
    if p["sector"]:
        sectores = {s.strip().upper() for s in p["sector"].split(",") if s.strip()}

    initialize(db_path)
    universe = fetch_sec_universe(rules, db_path)

    snapshots, stats = run_radar(
        universe, rules, db_path, csv_path,
        buffett_criteria=buffett_criteria,
        treasury_yield=treasury_yield,
        sector_filter=sectores,
        top_n=None,  # no imprimir top en consola desde web
    )

    job.snapshots = [s.to_full_dict() for s in snapshots]
    job.stats = {
        "total": stats.total,
        "passed": stats.passed,
        "drops": stats.drops_by_reason,
        "errors": stats.errors,
        "elapsed_seconds": round(stats.elapsed_seconds, 1),
        "treasury_yield": treasury_yield,
    }


async def _execute_job(
    job: JobState,
    db_path: Path,
    csv_path: Path,
    job_log_queues: dict[str, asyncio.Queue],
) -> None:
    """Wrapper async: monta handler de logs, dispara el scan, limpia."""
    queue = job_log_queues.get(job.job_id)
    handler: _QueueLogHandler | None = None

    if queue is not None:
        loop = asyncio.get_running_loop()
        handler = _QueueLogHandler(queue, loop)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                              datefmt="%H:%M:%S")
        )
        # Solo enganchamos al logger del paquete, no al root, para no
        # capturar yfinance/urllib3.
        logging.getLogger("radar_soberano").addHandler(handler)

    job.status = "running"
    job.started_at = datetime.now()

    try:
        await asyncio.get_running_loop().run_in_executor(
            None, _run_scan_blocking, job, db_path, csv_path
        )
        job.status = "done"
    except Exception as exc:
        log.exception("Job %s falló", job.job_id)
        job.status = "error"
        job.error = f"{exc.__class__.__name__}: {exc}"
    finally:
        job.finished_at = datetime.now()
        if handler is not None:
            logging.getLogger("radar_soberano").removeHandler(handler)
        # Empujar sentinel al queue para cerrar WebSocket
        if queue is not None:
            await queue.put(None)


# ============================================================
# App FastAPI
# ============================================================

def create_app(
    db_path: Path = DEFAULT_DB_PATH,
    csv_path: Path = DEFAULT_CSV_PATH,
    log_path: Path = DEFAULT_LOG_PATH,
) -> FastAPI:
    """Construye la app FastAPI. Inyecta paths para tests."""
    app = FastAPI(
        title="Radar Soberano",
        version=__version__,
        description="Interfaz web para el motor de análisis bursátil.",
    )

    # Estado por instancia (no global) — permite tests aislados
    jobs: dict[str, JobState] = {}
    job_log_queues: dict[str, asyncio.Queue] = {}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ----- Frontend -----
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(500, "index.html no encontrado")
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    # ----- Status / Health -----
    @app.get("/api/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        initialize(db_path)
        cache_size = 0
        cache_updated = None
        with open_db(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*), MAX(fecha_actualizacion) FROM sec_cache"
            ).fetchone()
            cache_size = row[0] or 0
            cache_updated = row[1]

        defaults = {
            "lote": 60,
            "pb_max": 3.0,
            "pfcf_max": 20.0,
            "ey_premium": 0.0,
            "min_history": 10,
        }
        return StatusResponse(
            version=__version__,
            cache_size=cache_size,
            cache_updated=cache_updated,
            treasury_yield=None,  # no hacemos fetch automático en cada status
            defaults=defaults,
        )

    @app.get("/api/treasury")
    async def treasury_now() -> dict:
        """Fetch en vivo del Treasury 10Y (cuando el usuario lo pide)."""
        loop = asyncio.get_running_loop()
        yield_value = await loop.run_in_executor(None, fetch_treasury_yield_10y)
        return {"treasury_yield": yield_value, "as_percent": yield_value * 100}

    # ----- Scan -----
    @app.post("/api/scan")
    async def start_scan(req: ScanRequest) -> dict:
        job_id = uuid.uuid4().hex[:12]
        job = JobState(job_id, req.model_dump())
        jobs[job_id] = job
        job_log_queues[job_id] = asyncio.Queue()

        # Lanzar en background
        asyncio.create_task(_execute_job(job, db_path, csv_path, job_log_queues))

        return {"job_id": job_id}

    @app.get("/api/scan/{job_id}")
    async def scan_status(job_id: str) -> dict:
        if job_id not in jobs:
            raise HTTPException(404, "Job no encontrado")
        return jobs[job_id].to_dict()

    @app.get("/api/scan")
    async def list_jobs() -> dict:
        """Lista jobs ordenados del más reciente al más viejo."""
        items = sorted(
            jobs.values(),
            key=lambda j: j.started_at or datetime.min,
            reverse=True,
        )
        return {"jobs": [j.to_dict() for j in items[:20]]}

    # ----- WebSocket de logs -----
    @app.websocket("/ws/logs/{job_id}")
    async def logs_stream(ws: WebSocket, job_id: str) -> None:
        await ws.accept()
        queue = job_log_queues.get(job_id)
        if queue is None:
            await ws.send_json({"type": "error", "message": "job no existe"})
            await ws.close()
            return

        try:
            while True:
                msg = await queue.get()
                if msg is None:  # sentinel: job terminó
                    await ws.send_json({"type": "done"})
                    break
                await ws.send_json({"type": "log", "message": msg})
        except WebSocketDisconnect:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    # ----- Cache SEC -----
    @app.post("/api/refresh-sec")
    async def refresh_sec() -> dict:
        """Invalida la cache SEC. La próxima corrida re-descargará el listado."""
        initialize(db_path)
        invalidate_cache(db_path)
        return {"ok": True, "message": "Cache invalidada. Re-descarga en próximo scan."}

    @app.post("/api/refresh-sec-now")
    async def refresh_sec_now() -> dict:
        """Invalida la cache Y la repuebla inmediatamente."""
        initialize(db_path)
        invalidate_cache(db_path)
        rules = TradingRules()
        loop = asyncio.get_running_loop()
        # fetch_sec_universe descarga si la cache está vacía
        await loop.run_in_executor(None, fetch_sec_universe, rules, db_path)

        with open_db(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*), MAX(fecha_actualizacion) FROM sec_cache"
            ).fetchone()

        return {
            "ok": True,
            "cache_size": row[0],
            "cache_updated": row[1],
        }

    # ----- Histórico -----
    @app.get("/api/history/{ticker}")
    async def history(ticker: str, limit: int = 60) -> dict:
        ticker = ticker.upper()
        initialize(db_path)
        with open_db(db_path) as conn:
            rows = conn.execute(
                """
                SELECT fecha, cierre, rsi, ma200, veredicto, pe, pb, pfcf, sector
                FROM mercado
                WHERE ticker = ?
                ORDER BY fecha DESC
                LIMIT ?
                """,
                (ticker, limit),
            ).fetchall()

        records = [dict(r) for r in rows]
        return {"ticker": ticker, "count": len(records), "records": records}

    # ----- CSV download -----
    @app.get("/api/csv")
    async def download_csv() -> FileResponse:
        if not csv_path.exists():
            raise HTTPException(404, "CSV no existe — corré un scan primero.")
        return FileResponse(
            path=str(csv_path),
            media_type="text/csv",
            filename=csv_path.name,
        )

    # ----- Sectores ya presentes en la DB -----
    @app.get("/api/sectors")
    async def sectors() -> dict:
        initialize(db_path)
        with open_db(db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT sector FROM mercado WHERE sector IS NOT NULL "
                "AND sector != 'N/A' ORDER BY sector"
            ).fetchall()
        return {"sectors": [r["sector"] for r in rows]}

    # ----- Reglas por sector (consulta-only) -----
    @app.get("/api/sector-rules")
    async def sector_rules() -> dict:
        """Devuelve los umbrales actuales por sector — para mostrar en UI."""
        from ..sector_rules import DEFAULT_SECTOR_RULES, FALLBACK_RULES

        rules_dict = {}
        for sector, t in DEFAULT_SECTOR_RULES.items():
            rules_dict[sector] = {
                "roe_min": t.roe_min,
                "deuda_max": t.deuda_max,
                "margen_min": t.margen_min,
                "descripcion": t.descripcion,
            }
        rules_dict["_fallback"] = {
            "roe_min": FALLBACK_RULES.roe_min,
            "deuda_max": FALLBACK_RULES.deuda_max,
            "margen_min": FALLBACK_RULES.margen_min,
            "descripcion": FALLBACK_RULES.descripcion,
        }
        return {"rules": rules_dict}

    # ============================================================
    # Portfolio: posiciones del usuario
    # ============================================================

    @app.get("/api/positions")
    async def list_user_positions(estado: str | None = None) -> dict:
        """Lista posiciones, opcionalmente filtradas por estado."""
        from ..portfolio import list_positions
        positions = list_positions(db_path, estado=estado)
        return {
            "positions": [_position_to_dict(p) for p in positions],
            "count": len(positions),
        }

    @app.post("/api/positions")
    async def create_position(req: dict) -> dict:
        """Registra una nueva posición de compra."""
        from ..portfolio import add_position

        ticker = (req.get("ticker") or "").strip().upper()
        if not ticker:
            raise HTTPException(400, "ticker requerido")

        try:
            position_id = add_position(
                db_path,
                ticker=ticker,
                precio_compra=float(req.get("precio_compra", 0)),
                cantidad=float(req.get("cantidad", 1)),
                fecha_compra=req.get("fecha_compra") or None,
                target_venta_pct=float(req.get("target_venta_pct", 15.0)),
                stop_loss_pct=float(req.get("stop_loss_pct", 8.0)),
                notas=req.get("notas") or None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        return {"id": position_id, "ok": True}

    @app.post("/api/positions/{position_id}/close")
    async def close_user_position(position_id: int, req: dict) -> dict:
        """Cierra una posición indicando precio de venta."""
        from ..portfolio import close_position

        try:
            position = close_position(
                db_path,
                position_id=position_id,
                precio_venta=float(req.get("precio_venta", 0)),
                fecha_venta=req.get("fecha_venta") or None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return _position_to_dict(position)

    @app.delete("/api/positions/{position_id}")
    async def delete_user_position(position_id: int) -> dict:
        """Elimina una posición (uso para corrección de errores)."""
        from ..portfolio import delete_position
        ok = delete_position(db_path, position_id)
        if not ok:
            raise HTTPException(404, "Posición no existe")
        return {"ok": True}

    @app.get("/api/positions/check")
    async def check_positions_now() -> dict:
        """Evalúa posiciones abiertas en este momento y devuelve señales."""
        from ..analyzer import _check_open_positions
        loop = asyncio.get_running_loop()
        signals = await loop.run_in_executor(
            None, _check_open_positions, db_path,
        )
        return {
            "signals": [
                {
                    "position": _position_to_dict(s.position),
                    "current_price": s.current_price,
                    "pnl_pct": s.pnl_pct,
                    "pnl_dollars": s.pnl_dollars,
                    "reason_code": s.reason_code,
                    "reason_text": s.reason_text,
                    "severity": s.severity,
                }
                for s in signals
            ],
            "count": len(signals),
        }

    # ============================================================
    # Alertas: email + telegram
    # ============================================================

    @app.get("/api/alerts/config")
    async def get_alert_config() -> dict:
        """Devuelve config (sin exponer credenciales completas)."""
        from ..alerts import load_alert_config
        config = load_alert_config()
        # Nunca devolvemos las contraseñas — solo flag de "configurado"
        return {
            "email_enabled": config.email_enabled,
            "telegram_enabled": config.telegram_enabled,
            "smtp_host": config.smtp_host or "",
            "smtp_port": config.smtp_port,
            "smtp_user": config.smtp_user or "",
            "smtp_to": config.smtp_to or "",
            "smtp_password_set": bool(config.smtp_password),
            "telegram_chat_id": config.telegram_chat_id or "",
            "telegram_token_set": bool(config.telegram_token),
        }

    @app.post("/api/alerts/config")
    async def save_alert_config_endpoint(req: dict) -> dict:
        """Guarda config en .env. No reemplaza valores vacíos para passwords."""
        from ..alerts import load_alert_config, save_alert_config

        # Para campos sensibles, si vienen vacíos preservamos lo existente
        existing = load_alert_config()
        if not req.get("smtp_password"):
            req["smtp_password"] = existing.smtp_password or ""
        if not req.get("telegram_token"):
            req["telegram_token"] = existing.telegram_token or ""

        save_alert_config(req)
        return {"ok": True}

    @app.post("/api/alerts/test")
    async def test_alert(req: dict) -> dict:
        """Envía un mensaje de prueba al canal indicado."""
        from ..alerts import load_alert_config, send_test_message

        channel = req.get("channel", "")
        if channel not in ("email", "telegram"):
            raise HTTPException(400, "channel debe ser 'email' o 'telegram'")

        config = load_alert_config()
        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(
            None, send_test_message, config, channel,
        )
        return {"ok": ok, "message": msg}

    return app


def _position_to_dict(p) -> dict:
    """Serializa Position para respuesta JSON."""
    return {
        "id": p.id,
        "ticker": p.ticker,
        "fecha_compra": p.fecha_compra,
        "precio_compra": p.precio_compra,
        "cantidad": p.cantidad,
        "target_venta_pct": p.target_venta_pct,
        "stop_loss_pct": p.stop_loss_pct,
        "estado": p.estado,
        "fecha_venta": p.fecha_venta,
        "precio_venta": p.precio_venta,
        "pnl_realizado": p.pnl_realizado,
        "notas": p.notas,
    }


# Entry point default
app = create_app()
