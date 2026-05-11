# Changelog

Todas las versiones notables del proyecto se documentan aquí.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
y el versionado [SemVer](https://semver.org/lang/es/).

## [4.5.0] — 2026-05-07

**Portfolio tracking + alertas automáticas**: el ciclo se cierra. Ahora
podés reportar las acciones que compraste y el motor te avisa cuándo
vender por email o Telegram.

### Añadido
- **Earnings calendar** (`earnings.py`): para cada oportunidad detectada,
  Yahoo Finance se consulta para obtener la próxima fecha de reporte
  financiero. Se muestra como banner amarillo en la card si está dentro
  de los próximos 7 días (alta volatilidad esperada).

- **Tracking de posiciones** (`portfolio.py`): tabla SQLite `posiciones`
  con CRUD completo (abrir, cerrar, eliminar). Cada posición tiene su
  propio target de venta (default +15%) y stop loss (default -8%).

- **Detección de señales de venta** con 5 criterios:
  - 🎯 **TAKE PROFIT** — precio alcanzó target (default +15%).
  - 🛑 **STOP LOSS** — precio cayó al stop (default -8%, regla O'Neil).
  - 🔴 **OVERBOUGHT** — RSI > 75 con ganancia > 5%.
  - 📉 **BROKEN TREND** — cierre cae bajo MA200 con pérdida.
  - ⏰ **DEAD MONEY** — posición > 365 días sin moverse ±5%.
  Las señales tienen prioridad: TAKE_PROFIT > STOP_LOSS > resto.

- **Alertas multicanal** (`alerts.py`):
  - **Email vía SMTP** estándar (Gmail App Password, Outlook, etc).
  - **Telegram bot** vía Bot API. Setup self-service desde la web.
  - Configuración persistente en `.env` con permisos restrictivos (0600).
  - Templates HTML + texto plano para email; HTML restringido para Telegram.
  - Botones de prueba "Probar email" / "Probar Telegram" en la web.

- **Pestaña Portfolio en la web**:
  - Lista de posiciones abiertas y cerradas con PnL.
  - Modal "+ Agregar posición" con todos los campos.
  - Botón "Vender" en cada posición abierta para cerrarla con su precio
    de venta (calcula PnL realizado).
  - Botón "🔍 Evaluar ahora" para chequear señales sin esperar al scan.

- **Pestaña Alertas en la web**:
  - Formularios para SMTP y Telegram con guía paso a paso de @BotFather.
  - Las contraseñas/tokens nunca se muestran ya guardados; campo vacío
    significa "no cambiar el actual".

- **Modal "¿Compraste alguna?"** post-scan: si hay oportunidades en
  estado COMPRAR_AHORA o CERCA, se muestra un modal con checkboxes para
  registrar cuáles compraste. Pide precio y cantidad por cada una.

- **Integración automática** en cada scan: tras analizar el universo,
  el motor evalúa todas las posiciones abiertas y manda alertas si hay
  señales activas.

- 36 tests adicionales cubriendo portfolio (CRUD + 5 reglas de venta) y
  alerts (config, plantillas, parseo de .env).

### Endpoints API nuevos
- `GET/POST /api/positions` · CRUD de posiciones.
- `POST /api/positions/{id}/close` · cerrar posición.
- `DELETE /api/positions/{id}` · eliminar posición.
- `GET /api/positions/check` · evaluar señales de venta on-demand.
- `GET/POST /api/alerts/config` · gestionar credenciales.
- `POST /api/alerts/test` · enviar mensaje de prueba.

### Cambiado
- `analyzer.run_radar` ahora devuelve `(snapshots, stats)` y además
  evalúa el portfolio + envía alertas al final (parámetro
  `check_portfolio=True` por default).
- Cards de oportunidades ahora muestran banner de earnings cuando aplica.

## [4.4.0] — 2026-05-07

**Análisis de precio óptimo de entrada** — para cada empresa que pasa los
filtros, el motor calcula un precio objetivo de compra y emite una
recomendación clara: comprar ahora, esperar a $X, o evitar.

### Añadido
- **Módulo `price_targets.py`** con tres métodos combinados:
  - **Pivot lows**: detecta mínimos locales históricos (donde el mercado
    ya rebotó antes). Algoritmo de pivot points con dedupe de niveles
    cercanos (<2% de diferencia).
  - **Retroceso de Fibonacci**: niveles 38.2 / 50 / 61.8 sobre el rango
    anual high-low.
  - **RSI implícito**: cálculo inverso del RSI Wilder — a qué precio el
    indicador caería justo en el umbral de entrada.
- **Recomendación automática** con cuatro niveles según distancia al objetivo:
  - 🟢 **COMPRAR AHORA**: precio actual ≤ objetivo.
  - 🟡 **CERCA**: dentro del 3% del objetivo.
  - ⏳ **ESPERAR a $X**: 3-15% sobre el objetivo.
  - 🔴 **CARO**: más de 15% sobre el objetivo.
- **Gráficos en la web** con Chart.js:
  - Gráfico de precio (1 año) con MA200 superpuesta.
  - Gráfico de RSI debajo con líneas en 35 (sobreventa) y 70 (sobrecompra).
  - **Línea horizontal verde** marcando el precio objetivo de compra.
  - Líneas tenues de soportes adicionales.
- **Pestaña Resultados rediseñada**: cards de oportunidades expandibles,
  ordenadas de "comprar ahora" a "caro". Las dos primeras se auto-expanden.
- Tres columnas nuevas en CSV: `Precio_Objetivo`, `Distancia%`, `Accion`.
- Log del CLI ahora muestra precio actual + objetivo + recomendación
  por cada ticker que pasa: `NRP @ $111.49 → 🟢 COMPRA · 🟡 CERCA`.
- 17 tests adicionales en `test_price_targets.py`.

### Cambiado
- `TickerSnapshot` ahora incluye `price_targets` y `history_data`.
- API web devuelve la versión completa (con history para el chart) vía
  nuevo método `to_full_dict`.

### Por qué
Hasta ahora el motor te decía "esta empresa pasa los filtros" pero no
"a qué precio entrar". Comprar al precio actual cuando hay un soporte
2% más abajo es perder rentabilidad gratis. Esta versión cierra ese loop.

## [4.3.0] — 2026-05-07

Filtros fundamentales **por sector** — el motor ahora aplica umbrales
calibrados según la realidad de cada industria, en vez de una única regla
"talla única" que descartaba injustamente sectores enteros.

### Añadido
- **Módulo `sector_rules.py`** con 11 sectores GICS mapeados:
  - Technology · Communication Services · Healthcare · Consumer Cyclical ·
    Consumer Defensive · Industrials · Energy · Basic Materials ·
    Utilities · Real Estate · Financial Services.
  - Cada sector con `roe_min`, `deuda_max`, `margen_min` y descripción.
  - Sectores no mapeados caen a `FALLBACK_RULES` (ROE 10%, D/C 1.0).
- **Tratamiento especial**:
  - **Financial Services**: `deuda_max=None` (la deuda es su negocio).
  - **Real Estate (REITs)**: `margen_min=None` (se miden con FFO),
    `deuda_max=3.0` (operan apalancados por diseño).
  - **Utilities**: `deuda_max=2.0` (regulado, alto apalancamiento normal).
- **Endpoint `/api/sector-rules`** y tabla en la pestaña Ayuda de la web
  que muestra los umbrales actuales con sus descripciones.
- **Razones de descarte enriquecidas**: el log indica qué umbral falló
  específicamente, p. ej. `"D/C 1.20 > 0.7 (Energy)"`.
- 14 tests adicionales en `test_sector_rules.py` cubriendo cada sector
  y casos edge (sector desconocido, None, valores extremos).

### Cambiado
- `analyzer._passes_fundamentals` ahora devuelve `(bool, reason)` en vez
  de `bool` solo. Los tests viejos fueron actualizados.
- `TradingRules.sector_rules` reemplaza la lógica hardcodeada
  ("if sector == 'Technology'..."). Los campos legacy
  (`margen_min_tech`, `roe_min`, `deuda_max`) se conservan por
  retrocompatibilidad pero ya no se usan.
- El reporte ahora muestra empresas de **todos los sectores**, no solo
  tecnología sobrerrepresentada por filtros de deuda demasiado estrictos.

### Por qué cambió
El filtro anterior aplicaba `deuda ≤ 0.5` a todos los sectores. Pero los
bancos típicamente tienen D/C de 5-10× (su deuda son los depósitos), los
REITs operan con 2-4× (compran propiedades con financiamiento), las
utilities regulan a 1-2×. El resultado era que esos sectores eran
descartados automáticamente, dando la falsa impresión de que el motor
solo analizaba tecnología.

## [4.2.0] — 2026-05-07

### Añadido
- **Interfaz web** (`radar-soberano web`):
  - Servidor FastAPI local en `http://localhost:8000`.
  - Frontend HTML+JS sin frameworks (sin npm, sin build) con estética
    terminal financiera (Bloomberg-inspired).
  - Cuatro pestañas: Escanear, Resultados, Histórico, Ayuda.
  - **Botón para refrescar cache SEC** (descarga inmediata).
  - **Botón para consultar Treasury 10Y** en vivo.
  - **Logs en tiempo real** vía WebSocket — ves cada ticker procesarse
    igual que en consola.
  - Configuración completa del modo Buffett desde la UI (toggle + 4 inputs).
  - Tabla de resultados con código de color por veredicto y descarga CSV.
  - Consulta de histórico de cualquier ticker desde la DB local.
  - Tarjetas de estadísticas con breakdown por veredicto.
  - Sección de Ayuda con glosario completo.
- Extra `[web]` en `pyproject.toml`: `pip install -e ".[web]"`.
- 9 tests adicionales para los endpoints del servidor.

### Cambiado
- Empaque incluye los assets estáticos del frontend (`package-data`).

## [4.1.0] — 2026-05-07

Iteración de UX y observabilidad sobre v4.0.

### Añadido
- **Subcomando `history`**: `radar-soberano history NVDA` muestra evolución
  del ticker desde la DB. Acepta `--limit N`.
- **Filtro por sector** (`--sector Technology,Energy`): escanea solo los
  sectores indicados. El portafolio foco siempre se incluye (ignora filtro).
- **Top-N en consola** (`--top 10`, default): tabla resumen al final de la
  corrida sin necesidad de abrir el CSV. `--top 0` la desactiva.
- **`--no-cache`**: invalida la cache SEC y fuerza re-descarga.
- **Estadísticas detalladas** al final de cada corrida: total escaneados,
  cuántos pasaron, breakdown de descartes por categoría
  (`fundamental`, `buffett`, `historia_insuficiente`, `datos_yfinance`,
  `fetch_precios`, `sector_filtrado`), errores y tickers/segundo.
- **Notas de pase**: la columna `Notas` del CSV ahora también se llena
  cuando un ticker pasa, indicando *por qué* (RSI concreto en modo clásico;
  E/Y vs Treasury, P/B, P/FCF en modo Buffett).
- 13 tests adicionales para `analyzer` y CLI helpers.

### Cambiado
- **Logs ruidosos silenciados**: `yfinance`, `peewee` y `urllib3` ahora
  loggean WARNING+ por defecto, evitando que errores HTTP de yfinance
  contaminen el stdout de la herramienta.
- `analyzer.run_radar` ahora devuelve `(snapshots, stats)` para permitir
  consumo programático de las estadísticas.
- `analyze_ticker` retorna `_AnalysisOutcome` (snapshot + razón de descarte)
  en lugar de `Optional[Snapshot]`, habilitando las estadísticas.

## [4.0.1] — 2026-05-07

### Corregido
- **`UNIQUE constraint failed: sec_cache.ticker`** al refrescar cache SEC.
  El JSON oficial de la SEC contiene símbolos duplicados (típicamente
  distintas clases de acciones que normalizan al mismo ticker tras
  reemplazar `.` por `-`). Ahora `_replace_cache` deduplica preservando
  orden y usa `INSERT OR REPLACE` como red de seguridad.

## [4.0.0] — 2026-05-07

### Añadido
- **Modo Buffett (`--buffett`)** — capa opcional de filtros value investing:
  - Earnings Yield (1/PE) > Treasury 10Y (con premium configurable).
  - P/B ≤ 3.0 (configurable con `--pb-max`).
  - P/FCF ≤ 20 (configurable con `--pfcf-max`).
  - Edad mínima del negocio ≥ 10 años (configurable con `--min-history`).
  - 4 años consecutivos de utilidades positivas (income_stmt de yfinance).
- Fetch dinámico del Treasury 10Y vía ticker `^TNX`, con fallback a 4 %.
- Veredictos nuevos: `⭐ BUFFETT GRADE` y `⚠ FOCO (no-Buffett)`.
- Columnas nuevas en CSV y DB: `PE`, `PB`, `PFCF`, `E/Y%`, `Notas`.
- Migración automática del esquema SQLite (ALTER TABLE para columnas Buffett).
- Módulo `buffett.py` con dataclasses inmutables y función pura `evaluate_buffett`.
- 11 tests adicionales en `test_buffett.py`, todos con datos sintéticos (sin red).

### Cambiado
- `analyzer.py` ahora ramifica entre modo quantamental y modo Buffett.
- En modo Buffett, RSI deja de ser gatillo (sigue informativo en el reporte).
- CSV añade columna `Notas` con razones cuando una empresa no pasa filtros.

## [3.1.0] — 2026-05-07

### Añadido
- `Dockerfile` multi-stage con imagen Python 3.12-slim, usuario no-root y `/data` como volumen.
- `.dockerignore` para reducir el contexto de build.
- GitHub Actions workflow (`.github/workflows/ci.yml`):
  - Matriz de tests en Python 3.10, 3.11 y 3.12.
  - Reporte de cobertura subido como artefacto.
  - Job separado de validación sintáctica.
- Badges de CI y Docker en el README.

## [3.0.0] — 2026-05-07

Refactor completo del motor a arquitectura modular profesional.

### Añadido
- Estructura de paquete (`radar_soberano/`) con módulos especializados.
- CLI con `argparse`: `--db`, `--csv`, `--log`, `--lote`, `--seed`, `-v`, `--version`.
- Console script `radar-soberano` instalado vía `pyproject.toml`.
- Type hints en todos los módulos.
- `RotatingFileHandler` para logs (2 MB × 3 backups).
- PK compuesto `(ticker, fecha)` en `mercado` → mantiene histórico por fecha.
- Índices SQLite en `fecha` y `veredicto`.
- Tests unitarios para indicadores con `pytest`.
- `pyproject.toml` (PEP 621) y `requirements-dev.txt`.
- Manejo graceful de `KeyboardInterrupt` con exit code 130 (POSIX).
- Columnas `Cierre` y `MA200` en el CSV.

### Corregido
- **RSI división por cero**: cuando no hay pérdidas en la ventana, devuelve 100.
- **Logs silenciados**: `logging.debug` antes nunca aparecía con root en INFO.
- **`requests.get` sin timeout**: ahora usa `request_timeout` de TradingRules.
- **Cache SEC**: cambiada de inserts en bucle a `executemany` (≈100× más rápido).
- **Conexiones SQLite**: todas con context manager; rollback automático en excepción.
- **Sort de veredictos**: ahora respeta severidad (BUY → WAIT → SELL), no orden Unicode de emojis.
- **Import sin uso**: eliminado `timedelta`.

### Cambiado
- RSI ahora usa **fórmula de Wilder** (suavizado exponencial), estándar de la industria.
- Configuración centralizada en `TradingRules` (dataclass inmutable).
- Filtro fundamental refactorizado a función pura.

## [2.0.0] — Versión original

- Script monolítico de un solo archivo.
- Logging básico sin rotación.
- Análisis fundamental + técnico con persistencia SQLite y reporte CSV.
