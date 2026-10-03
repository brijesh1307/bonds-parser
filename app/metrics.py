"""Prometheus metrics (GET /metrics). One registry per app, so tests and apps never share counters.

Labels are low-cardinality on purpose: route templates (not raw paths), statuses, markets,
template ids and auth failure reasons. Nothing from a slip's content is ever a label.
"""

from __future__ import annotations

from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

__all__ = ["CONTENT_TYPE_LATEST", "Metrics"]


class Metrics:
    def __init__(self, active_templates: Callable[[], int]) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "bonds_http_requests_total",
            "HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "bonds_http_request_duration_seconds",
            "HTTP request duration",
            ["route"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0),
            registry=self.registry,
        )
        self.parses = Counter(
            "bonds_parses_total",
            "Parse results by status and market",
            ["status", "market"],
            registry=self.registry,
        )
        self.template_matches = Counter(
            "bonds_template_matches_total",
            "Parses that matched a template",
            ["template_id"],
            registry=self.registry,
        )
        self.auth_failures = Counter(
            "bonds_auth_failures_total",
            "Failed authentications by reason",
            ["reason"],
            registry=self.registry,
        )
        self.templates_active = Gauge("bonds_templates_active", "Active templates", registry=self.registry)
        self._active_templates = active_templates

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(method, route, str(status)).inc()
        self.duration.labels(route).observe(seconds)

    def observe_parse(self, status: str, market: str, template_id: str | None) -> None:
        self.parses.labels(status, market).inc()
        if template_id:
            self.template_matches.labels(template_id).inc()

    def render(self) -> bytes:
        self.templates_active.set(self._active_templates())
        data: bytes = generate_latest(self.registry)
        return data
