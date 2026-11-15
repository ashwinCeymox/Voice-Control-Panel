"""
Unit tests for Task M2-METRICS: Prometheus /metrics endpoint.
Tests metric collection, counter increments, gauge changes, latency histogram buckets,
and standard Prometheus text rendering without external dependencies.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.metrics import metrics_collector


def test_metrics_endpoint_returns_prometheus_format():
    """GET /metrics returns HTTP 200 with text/plain Prometheus format."""
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text

    assert "# HELP voice_agent_requests_total" in body
    assert "# TYPE voice_agent_requests_total counter" in body
    assert "# HELP voice_agent_active_streams" in body
    assert "# TYPE voice_agent_active_streams gauge" in body
    assert "# HELP voice_agent_turn_latency_seconds" in body
    assert "# TYPE voice_agent_turn_latency_seconds histogram" in body


def test_metrics_records_request_counter():
    """Recording requests increments Prometheus request counter."""
    metrics_collector.inc_request("/v1/chat/completions", 200)
    metrics_collector.inc_request("/v1/chat/completions", 200)
    metrics_collector.inc_request("/v1/chat/completions", 429)

    body = metrics_collector.render_prometheus()
    assert 'voice_agent_requests_total{endpoint="/v1/chat/completions",status="200"}' in body
    assert 'voice_agent_requests_total{endpoint="/v1/chat/completions",status="429"}' in body


def test_metrics_active_streams_gauge():
    """Active stream gauge tracks concurrent connections accurately."""
    metrics_collector.inc_active_streams("voice")
    metrics_collector.inc_active_streams("voice")
    body = metrics_collector.render_prometheus()
    assert 'voice_agent_active_streams{type="voice"} 2' in body

    metrics_collector.dec_active_streams("voice")
    body = metrics_collector.render_prometheus()
    assert 'voice_agent_active_streams{type="voice"} 1' in body


def test_metrics_turn_latency_histogram():
    """Recording turn latency populates histogram buckets, sum, and count."""
    metrics_collector.observe_turn_latency(0.18)  # fits in <= 0.25, 0.5, etc.
    metrics_collector.observe_turn_latency(0.75)  # fits in <= 1.0, 2.5, etc.

    body = metrics_collector.render_prometheus()
    assert 'voice_agent_turn_latency_seconds_bucket{le="0.1"}' in body
    assert 'voice_agent_turn_latency_seconds_bucket{le="0.25"}' in body
    assert 'voice_agent_turn_latency_seconds_bucket{le="+Inf"}' in body
    assert "voice_agent_turn_latency_seconds_count" in body
    assert "voice_agent_turn_latency_seconds_sum" in body
