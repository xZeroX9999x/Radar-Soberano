# syntax=docker/dockerfile:1.7
# ============================================================
# Stage 1: builder — compila el wheel del paquete
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build

# Solo lo necesario para construir el wheel
RUN pip install --no-cache-dir --upgrade pip build

# Copiar metadata primero (mejor caché de capas)
COPY pyproject.toml README.md ./
COPY radar_soberano/ ./radar_soberano/

RUN python -m build --wheel --outdir /wheels

# ============================================================
# Stage 2: runtime — imagen final mínima
# ============================================================
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Radar Soberano" \
      org.opencontainers.image.description="Motor Quantamental de análisis bursátil" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/your-username/radar-soberano"

# Usuario no-root por seguridad
RUN useradd --create-home --shell /bin/bash radar

# Instalar el wheel + dependencias en una sola capa
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm -f /tmp/*.whl

# /data es el volumen donde viven DB, CSV y logs
WORKDIR /data
RUN chown radar:radar /data

USER radar

# Variable de entorno para que Python no buffere stdout (mejor para logs Docker)
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["radar-soberano"]
CMD ["--help"]
