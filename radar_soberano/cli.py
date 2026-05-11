"""Interfaz de línea de comandos para Radar Soberano."""
from __future__ import annotations

import argparse
import logging
import random
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .analyzer import run_radar
from .buffett import BuffettCriteria, fetch_treasury_yield_10y
from .config import (
    DEFAULT_CSV_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_LOG_PATH,
    TradingRules,
)
from .database import initialize
from .history import show_history
from .universe import fetch_sec_universe, invalidate_cache


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar-soberano",
        description="Motor Quantamental de análisis bursátil.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # --- Comando default: scan (también accesible sin nombrarlo) ---
    scan = subparsers.add_parser(
        "scan",
        help="Ejecuta un escaneo del mercado (default si no se especifica comando).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_scan_args(scan)

    # --- Comando history ---
    hist = subparsers.add_parser(
        "history",
        help="Muestra el histórico de un ticker desde la DB.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    hist.add_argument("ticker", help="Símbolo a consultar (ej: NVDA)")
    hist.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                      help="Ruta de la base SQLite")
    hist.add_argument("--limit", type=int, default=30,
                      help="Cantidad máxima de filas a mostrar")
    hist.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH,
                      help="Archivo de log")

    # --- Comando web ---
    web = subparsers.add_parser(
        "web",
        help="Inicia la interfaz web local (http://localhost:8000).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    web.add_argument("--host", default="127.0.0.1", help="Host al que enlazar")
    web.add_argument("--port", type=int, default=8000, help="Puerto del servidor")
    web.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                     help="Ruta de la base SQLite")
    web.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH,
                     help="Ruta del reporte CSV")
    web.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH,
                     help="Archivo de log")

    # Compatibilidad: si no se da comando, los flags de scan se aceptan
    # directamente en el parser raíz.
    _add_scan_args(parser, hidden=True)

    return parser


def _add_scan_args(p: argparse.ArgumentParser, hidden: bool = False) -> None:
    """Añade los argumentos del comando scan al parser dado.

    Si hidden=True no muestra estos flags en el `--help` del parser raíz
    (siguen estando disponibles para compatibilidad con la sintaxis
    sin subcomando).
    """
    helpfn = lambda h: argparse.SUPPRESS if hidden else h

    p.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH,
        help=helpfn("Ruta de la base SQLite"),
    )
    p.add_argument(
        "--csv", type=Path, default=DEFAULT_CSV_PATH,
        help=helpfn("Ruta del reporte CSV"),
    )
    p.add_argument(
        "--log", type=Path, default=DEFAULT_LOG_PATH,
        help=helpfn("Ruta del archivo de log rotativo"),
    )
    p.add_argument(
        "--lote", type=int, default=60,
        help=helpfn("Tamaño del lote aleatorio de exploración"),
    )
    p.add_argument(
        "--seed", type=int, default=None,
        help=helpfn("Semilla del muestreo aleatorio (reproducibilidad)"),
    )
    p.add_argument(
        "--sector", type=str, default=None,
        help=helpfn("Filtrar por sector(es) — coma-separado. "
                    "Ej: --sector Technology,Energy"),
    )
    p.add_argument(
        "--top", type=int, default=10,
        help=helpfn("Mostrar top N resultados en consola al final (0 = ninguno)"),
    )
    p.add_argument(
        "--no-cache", action="store_true",
        help=helpfn("Forzar re-descarga del listado SEC ignorando la cache"),
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help=helpfn("Aumenta verbosidad: -v activa DEBUG en consola y log"),
    )

    # --- Modo Buffett ---
    g = p.add_argument_group("Modo Buffett (value investing)")
    g.add_argument(
        "--buffett", action="store_true",
        help=helpfn("Activa filtros value: E/Y > Treasury, P/B, P/FCF, edad "
                    "mínima, ganancias positivas. Desactiva RSI como gatillo."),
    )
    g.add_argument(
        "--pb-max", type=float, default=3.0,
        help=helpfn("P/B máximo aceptable (solo en --buffett)"),
    )
    g.add_argument(
        "--pfcf-max", type=float, default=20.0,
        help=helpfn("P/FCF máximo aceptable (solo en --buffett)"),
    )
    g.add_argument(
        "--ey-premium", type=float, default=0.0,
        help=helpfn("Puntos porcentuales de E/Y exigidos sobre Treasury 10Y "
                    "(solo en --buffett)"),
    )
    g.add_argument(
        "--min-history", type=int, default=10,
        help=helpfn("Años mínimos de historia operativa (solo en --buffett)"),
    )


def setup_logging(log_path: Path, verbose: int) -> None:
    """Configura logging con archivo rotativo + stdout y silencia ruido externo."""
    level = logging.DEBUG if verbose >= 1 else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8",
    )
    stdout_handler = logging.StreamHandler(sys.stdout)

    # En consola, silenciamos yfinance/urllib3/peewee aplicando un filter
    # selectivo: el archivo recibe todo, stdout no recibe estos loggers.
    class _NoiseFilter(logging.Filter):
        _NOISY = ("yfinance", "peewee", "urllib3")

        def filter(self, record: logging.LogRecord) -> bool:
            return not any(record.name.startswith(n) for n in self._NOISY)

    stdout_handler.addFilter(_NoiseFilter())

    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[file_handler, stdout_handler],
        force=True,
    )

    # Igualmente reducir verbosidad de los ruidosos a WARNING para no llenar
    # el archivo con DEBUG de yfinance.
    for noisy in ("yfinance", "peewee", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _parse_sectors(raw: str | None) -> set[str] | None:
    """Convierte 'Technology,Energy' en {'TECHNOLOGY', 'ENERGY'}."""
    if not raw:
        return None
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _run_scan(args: argparse.Namespace) -> int:
    """Ejecuta el comando scan."""
    setup_logging(args.log, args.verbose)
    log = logging.getLogger("radar_soberano")
    log.info("=== INICIANDO MOTOR RADAR SOBERANO v%s ===", __version__)

    if args.seed is not None:
        random.seed(args.seed)
        log.info("Semilla de aleatoriedad fijada: %d", args.seed)

    rules = TradingRules(tamanio_lote=args.lote)

    buffett_criteria: BuffettCriteria | None = None
    treasury_yield: float | None = None

    if args.buffett:
        buffett_criteria = BuffettCriteria(
            pb_max=args.pb_max,
            pfcf_max=args.pfcf_max,
            earnings_yield_premium_pp=args.ey_premium,
            min_history_years=args.min_history,
        )
        log.info("=== MODO BUFFETT ACTIVADO ===")
        log.info(
            "Criterios: P/B≤%.1f, P/FCF≤%.1f, E/Y > Treasury+%.1fpp, edad≥%d años",
            args.pb_max, args.pfcf_max, args.ey_premium, args.min_history,
        )
        treasury_yield = fetch_treasury_yield_10y()
        log.info("Treasury 10Y: %.2f%%", treasury_yield * 100)

    sectores = _parse_sectors(args.sector)
    if sectores:
        log.info("Filtro por sectores: %s", sorted(sectores))

    try:
        initialize(args.db)
        if args.no_cache:
            log.info("--no-cache: invalidando cache SEC.")
            invalidate_cache(args.db)
        universe = fetch_sec_universe(rules, args.db)
        run_radar(
            universe, rules, args.db, args.csv,
            buffett_criteria=buffett_criteria,
            treasury_yield=treasury_yield,
            sector_filter=sectores,
            top_n=args.top if args.top > 0 else None,
        )
    except KeyboardInterrupt:
        log.warning("Interrumpido por el usuario.")
        return 130
    except Exception as exc:
        log.exception("Error fatal: %s", exc)
        return 1

    log.info("=== OPERACIÓN FINALIZADA ===")
    return 0


def _run_history(args: argparse.Namespace) -> int:
    """Ejecuta el comando history."""
    setup_logging(args.log, verbose=0)
    return show_history(args.db, args.ticker, args.limit)


def _run_web(args: argparse.Namespace) -> int:
    """Ejecuta el servidor web."""
    setup_logging(args.log, verbose=0)
    log = logging.getLogger("radar_soberano")

    try:
        import uvicorn
    except ImportError:
        log.error(
            "El servidor web requiere 'fastapi' y 'uvicorn'. "
            "Instalá con: pip install 'radar-soberano[web]' "
            "o pip install fastapi uvicorn"
        )
        return 1

    from .web.server import create_app
    app = create_app(db_path=args.db, csv_path=args.csv, log_path=args.log)

    log.info("=== INICIANDO SERVIDOR WEB v%s ===", __version__)
    log.info("Abriendo http://%s:%d en el navegador local",
             args.host, args.port)
    log.info("Pulsá Ctrl+C para detener.")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada principal."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "history":
        return _run_history(args)
    if args.command == "web":
        return _run_web(args)
    # Default: scan (con o sin subcomando explícito)
    return _run_scan(args)


if __name__ == "__main__":
    sys.exit(main())
