"""Tests para los endpoints del servidor FastAPI."""
from __future__ import annotations

import pytest

# Saltamos todo el módulo si fastapi no está instalado.
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from radar_soberano import __version__
from radar_soberano.web.server import create_app


@pytest.fixture
def client(tmp_path):
    """Cliente FastAPI con DB y CSV en directorio temporal."""
    app = create_app(
        db_path=tmp_path / "test.db",
        csv_path=tmp_path / "test.csv",
        log_path=tmp_path / "test.log",
    )
    return TestClient(app)


def test_index_serves_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "RADAR" in response.text
    assert "text/html" in response.headers["content-type"]


def test_status_endpoint_returns_version_and_empty_cache(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()

    assert data["version"] == __version__
    assert data["cache_size"] == 0
    assert data["cache_updated"] is None
    assert "defaults" in data
    assert data["defaults"]["lote"] == 60


def test_history_returns_empty_for_unknown_ticker(client):
    response = client.get("/api/history/UNKNOWN")
    assert response.status_code == 200
    data = response.json()

    assert data["ticker"] == "UNKNOWN"
    assert data["count"] == 0
    assert data["records"] == []


def test_csv_returns_404_when_no_scan(client):
    response = client.get("/api/csv")
    assert response.status_code == 404


def test_sectors_endpoint_returns_empty_initially(client):
    response = client.get("/api/sectors")
    assert response.status_code == 200
    assert response.json() == {"sectors": []}


def test_scan_validation_rejects_invalid_lote(client):
    response = client.post("/api/scan", json={"lote": -5})
    assert response.status_code == 422  # validation error


def test_scan_returns_job_id(client):
    """Al enviar un scan válido, devuelve un job_id (corre async pero el POST
    debe responder de inmediato)."""
    response = client.post("/api/scan", json={"lote": 0, "buffett": False})
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert len(data["job_id"]) > 0


def test_scan_status_404_for_unknown_job(client):
    response = client.get("/api/scan/nonexistent")
    assert response.status_code == 404


def test_jobs_listing_works_when_empty(client):
    response = client.get("/api/scan")
    assert response.status_code == 200
    assert response.json() == {"jobs": []}
