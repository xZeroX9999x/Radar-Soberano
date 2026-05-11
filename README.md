# 📡 Radar Soberano

[![CI](https://github.com/your-username/radar-soberano/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/radar-soberano/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker)](#-docker)

**Motor Quantamental** de análisis bursátil que combina filtros fundamentales y técnicos sobre el universo completo de empresas registradas en la SEC, generando veredictos de inversión basados en reglas duras.

---

## 🎯 Qué hace

1. Descarga el registro oficial de la SEC (`company_tickers.json`) y lo cachea localmente por 30 días.
2. Combina un **portafolio foco** fijo con un **lote aleatorio** de exploración.
3. Aplica un **filtro fundamental** (margen bruto, ROE, deuda/capital) diferenciado por sector.
4. Sobre los activos que pasan el filtro, calcula **MA200** y **RSI de Wilder (14)**.
5. Emite un veredicto: 🟢 *COMPRA ESTRATÉGICA* / ⏳ *ESPERAR* / 🔴 *SOBRECOMPRA*.
6. Persiste todo en SQLite (con histórico por fecha) y exporta CSV consumible por Excel.

---

## 📋 Reglas duras

| Regla | Valor | Aplicación |
|---|---|---|
| Margen bruto mínimo (Tech) | ≥ 50 % | Solo sector Technology |
| ROE mínimo | ≥ 15 % | Todos los sectores |
| Deuda / Capital máximo | ≤ 0.5 | Todos los sectores |
| RSI de entrada | < 35 | Sobreventa con tendencia alcista |
| RSI de sobrecompra | > 75 | Señal de salida |
| Confirmación tendencia | Cierre > MA200 | Necesaria para COMPRA |

**Portafolio Foco** (analizado siempre, omite filtro fundamental): `NVDA`, `TTWO`, `IBIT`, `PLTR`, `BBAI`.

---

## ⭐ Modo Buffett (opcional)

Activa con el flag `--buffett` una capa adicional de filtros **value investing** clásicos. Pensada para complementar el filtro fundamental con criterios de valuación al estilo Warren Buffett.

### Filtros adicionales

| Filtro | Default | Flag | Lógica |
|---|---|---|---|
| Earnings Yield (1/PE) | > Treasury 10Y | `--ey-premium` | Las ganancias deben rendir más que el bono libre de riesgo |
| P/B (Precio/Valor Contable) | ≤ 3.0 | `--pb-max` | No pagar de más sobre el valor en libros |
| P/FCF (Precio/Flujo Caja Libre) | ≤ 20 | `--pfcf-max` | El cash flow debe justificar la valuación |
| Edad mínima | ≥ 10 años | `--min-history` | Solo negocios con historia probada |
| Utilidades recientes | 4 años seguidos positivos | — | Descarta empresas en pérdidas |

El **Treasury 10Y** se obtiene dinámicamente del ticker `^TNX` (con fallback a 4 % si la consulta falla).

### Comportamiento

- **RSI deja de ser gatillo**: en modo Buffett la valuación pesa más que el timing técnico. RSI/MA200 quedan como información en el reporte pero no filtran.
- **Tickers no-foco que fallan los filtros Buffett quedan fuera** del reporte.
- **Tickers del portafolio foco siempre aparecen**: si pasan, marcados como `⭐ BUFFETT GRADE`; si no, como `⚠ FOCO (no-Buffett)` con las razones del fallo en la columna `Notas`.

### Ejemplos

```bash
# Modo Buffett con defaults
radar-soberano --buffett

# Más estricto: requerir 2 puntos porcentuales sobre Treasury y P/B ≤ 2
radar-soberano --buffett --ey-premium 2.0 --pb-max 2.0

# Menos estricto en empresas jóvenes (5 años en vez de 10)
radar-soberano --buffett --min-history 5
```

---

## 🚀 Instalación

```bash
git clone https://github.com/<tu-usuario>/radar-soberano.git
cd radar-soberano

python -m venv venv
source venv/bin/activate          # Linux / macOS
# venv\Scripts\activate           # Windows

pip install -e .                  # instala en modo editable + console script
```

> Requiere **Python 3.10+**.

---

## ▶️ Uso

### 🖥️ Interfaz web (recomendado para no-developers)

```bash
pip install -e ".[web]"
radar-soberano web
```

Abrí `http://localhost:8000` en el navegador. Tenés:

- Botón único para iniciar escaneos con todas las opciones (lote, seed, sector, modo Buffett).
- Botón para refrescar el listado SEC inmediatamente.
- Botón para consultar el Treasury 10Y en vivo.
- **Logs en tiempo real** — ves cada ticker procesarse igual que en consola.
- Tabla de resultados con código de color por veredicto + descarga CSV.
- Consulta de histórico de cualquier ticker.
- Sección de Ayuda con glosario.

Configurar puerto/host: `radar-soberano web --host 0.0.0.0 --port 8080`.

### 💻 Línea de comandos

### Invocación básica

```bash
radar-soberano                            # console script (recomendado)
python -m radar_soberano                  # equivalente
```

### Con opciones

```bash
# Lote de exploración más grande, modo verbose
radar-soberano --lote 200 -v

# Reproducible (misma muestra aleatoria cada vez)
radar-soberano --seed 42

# Filtrar por sector(es)
radar-soberano --sector Technology,Energy

# Top-N resultados en consola al final (default 10, 0 lo desactiva)
radar-soberano --top 20

# Forzar re-descarga del listado SEC ignorando la cache
radar-soberano --no-cache

# Rutas personalizadas
radar-soberano --db data/mercado.db --csv reports/oportunidades.csv
```

### Subcomando `history`

Inspeccionar la evolución de un ticker desde la base local:

```bash
radar-soberano history NVDA
radar-soberano history NVDA --limit 60
```

### Estadísticas de cada corrida

Al final del scan, el motor imprime un breakdown:

```
--- Estadísticas de la corrida ---
Total escaneados: 35
Pasaron filtros:  7
Descartados por:
  · fundamental: 23
  · historia_insuficiente: 3
  · datos_yfinance: 2
Tiempo: 22.7s (1.5 tickers/s)
```

### Ayuda completa

```bash
radar-soberano --help
```

| Flag | Descripción | Default |
|---|---|---|
| `--db PATH` | Base SQLite | `infraestructura_mercado.db` |
| `--csv PATH` | Reporte CSV | `radar_oportunidades_globales.csv` |
| `--log PATH` | Archivo de log rotativo | `radar_sistema.log` |
| `--lote N` | Tamaño del muestreo aleatorio SEC | `60` |
| `--seed N` | Semilla del muestreo (reproducibilidad) | `None` |
| `--sector LIST` | Filtrar por sector(es), coma-separado | sin filtro |
| `--top N` | Mostrar top N en consola (0 = no mostrar) | `10` |
| `--no-cache` | Forzar re-descarga del listado SEC | off |
| `-v` | Activa logs DEBUG | off |
| `--version` | Muestra versión | — |

| Subcomando | Descripción |
|---|---|
| `history TICKER [--limit N]` | Muestra la evolución histórica del ticker desde la DB |

---

## 📤 Salidas generadas

| Archivo | Descripción |
|---|---|
| `infraestructura_mercado.db` | Tablas `mercado` (PK `ticker+fecha`, mantiene histórico) y `sec_cache`. |
| `radar_oportunidades_globales.csv` | Reporte ordenado por severidad, separador `;`, UTF-8 BOM (Excel-friendly). |
| `radar_sistema.log` | Bitácora rotativa: 2 MB × 3 backups. |

Las tres rutas están en `.gitignore`.

---

## 🐳 Docker

El proyecto incluye un `Dockerfile` multi-stage con imagen final mínima (Python 3.12 slim, usuario no-root).

### Build

```bash
docker build -t radar-soberano:latest .
```

### Ejecución

El contenedor escribe DB, CSV y logs en `/data`. Montar un volumen local para persistirlos:

```bash
mkdir -p data

# Corrida estándar
docker run --rm -v "$(pwd)/data:/data" radar-soberano:latest

# Con flags propios
docker run --rm -v "$(pwd)/data:/data" radar-soberano:latest --seed 42 -v --lote 100

# Ver versión
docker run --rm radar-soberano:latest --version
```

Después de la ejecución, los archivos `infraestructura_mercado.db`, `radar_oportunidades_globales.csv` y `radar_sistema.log` quedan en `./data/` del host.

---

## 🤖 Integración Continua

Cada push y pull request a `main` dispara el workflow `.github/workflows/ci.yml`:

- Tests con `pytest --cov` en Python 3.10, 3.11 y 3.12.
- Job de validación sintáctica (`python -m compileall`).
- Reporte de cobertura subido como artefacto.

Para correr el equivalente en local antes de hacer push:

```bash
pip install -e ".[dev]"
pytest --cov=radar_soberano --cov-report=term-missing
python -m compileall -q radar_soberano tests
```

---

## 🗂️ Estructura del proyecto

```
radar-soberano/
├── .github/
│   └── workflows/
│       └── ci.yml               # GitHub Actions: tests + lint
├── radar_soberano/              # Paquete principal
│   ├── __init__.py              # Versión
│   ├── __main__.py              # python -m radar_soberano
│   ├── cli.py                   # Argparse + setup logging
│   ├── config.py                # TradingRules (dataclass inmutable)
│   ├── database.py              # Persistencia SQLite con context managers
│   ├── indicators.py            # SMA, RSI Wilder (puros, testeables)
│   ├── universe.py              # Fetch SEC + cache
│   ├── analyzer.py              # Pipeline fundamental + técnico
│   ├── buffett.py               # Filtros value investing
│   └── history.py               # Subcomando history
├── tests/
│   ├── __init__.py
│   ├── test_indicators.py       # Tests RSI/SMA
│   ├── test_buffett.py          # Tests filtros value
│   └── test_analyzer.py         # Tests pipeline + helpers
├── Dockerfile                   # Imagen multi-stage producción
├── .dockerignore
├── pyproject.toml               # Empaque moderno (PEP 621)
├── requirements.txt             # Runtime
├── requirements-dev.txt         # Dev (pytest + cov)
├── README.md
├── CHANGELOG.md
├── LICENSE                      # MIT
└── .gitignore
```

---

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest                           # corrida estándar
pytest --cov=radar_soberano      # con cobertura
```

---

## 🔧 Personalización

Las reglas viven en `radar_soberano/config.py` dentro de `TradingRules`. Para una corrida puntual con reglas distintas, modifica `cli.py` o instancia `TradingRules(...)` con los overrides en código propio:

```python
from radar_soberano.config import TradingRules
from radar_soberano.universe import fetch_sec_universe
from radar_soberano.analyzer import run_radar
from radar_soberano.database import initialize
from pathlib import Path

rules = TradingRules(
    margen_min_tech=60.0,        # Más exigente con tech
    roe_min=20.0,                # ROE más alto
    rsi_entrada=30.0,            # Solo sobreventa profunda
)

db = Path("custom.db")
initialize(db)
universe = fetch_sec_universe(rules, db)
run_radar(universe, rules, db, Path("custom.csv"))
```

---

## 🔬 Notas técnicas

- **RSI**: implementación de **Wilder** (suavizado exponencial con α = 1/14). Es la fórmula estándar de la industria — distinta del RSI con SMA simple, especialmente en mercados volátiles.
- **División por cero en RSI**: tratada explícitamente. Si no hay pérdidas en la ventana, el RSI se fuerza a 100.
- **Cache SEC**: insertada con `executemany` (≈100× más rápido que loop de inserts).
- **Conexiones SQLite**: todas pasan por context manager con rollback automático en excepción.
- **Logs**: archivo rotativo (no crece sin límite). `-v` activa DEBUG en consola y archivo.

---

## ⚠️ Aviso legal

Software de **análisis cuantitativo educativo**. No constituye asesoría financiera ni recomendación de inversión. Las decisiones operativas son responsabilidad exclusiva del usuario. Datos provenientes de fuentes públicas (SEC, Yahoo Finance) que pueden contener errores o retrasos.

---

## 📜 Licencia

MIT — ver [LICENSE](LICENSE).
