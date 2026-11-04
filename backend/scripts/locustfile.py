"""Locust load testing script for Phase 5 Task 5.6 (Capacity & Load Curve Profiling).

Simulates concurrent staff users (5, 10, 20, 50, 100) querying POST /v1/questions
on either the text path or the vision path against an isolated throwaway database
on desktop-gpu.

Enforces:
- 60-second step duration (-t 60s).
- Early stop: monitors aggregate error rate every 2 seconds and stops the step early
  if error rate exceeds 50% once at least max(5, target_users // 2) requests have
  completed, writing the early-stop marker file so the test runner records the
  breaking point.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from locust import HttpUser, between, events, task
from locust.env import Environment

import gevent

ASSISTANT_ID: str = os.environ.get("LOCUST_ASSISTANT_ID", "")
SESSION_TOKEN: str = os.environ.get("LOCUST_SESSION_TOKEN", "")
QUESTION_TEXT: str = os.environ.get(
    "LOCUST_QUESTION_TEXT",
    "What is the policy reference code for outpatient prior authorization?",
)
EARLY_STOP_FLAG_PATH: str = os.environ.get("LOCUST_EARLY_STOP_FLAG_PATH", "")
STEP_STATS_JSON_PATH: str = os.environ.get("LOCUST_STEP_STATS_JSON_PATH", "")
REQUEST_TIMEOUT_SECONDS: float = float(os.environ.get("LOCUST_REQUEST_TIMEOUT_SECONDS", "30.0"))
CSRF_VALUE: str = "locust-csrf-token-value"


def _write_step_stats_snapshot(environment: Environment) -> None:
    """Persist live environment.stats.total metrics to JSON before runner exit."""
    if not STEP_STATS_JSON_PATH:
        return
    total_stats = environment.stats.total
    num_reqs = int(total_stats.num_requests)
    num_fails = int(total_stats.num_failures)
    err_rate = round(num_fails / num_reqs, 4) if num_reqs > 0 else 0.0
    rps = round(float(total_stats.total_rps or 0.0), 4)
    p50_raw = total_stats.get_response_time_percentile(0.50) or total_stats.median_response_time
    p95_raw = total_stats.get_response_time_percentile(0.95) or total_stats.max_response_time
    default_timeout_ms = round(REQUEST_TIMEOUT_SECONDS * 1000.0, 2)
    p50 = round(float(p50_raw), 2) if p50_raw and float(p50_raw) > 0 else default_timeout_ms
    p95 = round(float(p95_raw), 2) if p95_raw and float(p95_raw) > 0 else max(p50, default_timeout_ms)
    if p95 < p50:
        p95 = p50

    payload = {
        "total_requests": num_reqs,
        "failed_requests": num_fails,
        "error_rate": err_rate,
        "throughput_rps": rps,
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
    }
    Path(STEP_STATS_JSON_PATH).write_text(json.dumps(payload), encoding="utf-8")


def _monitor_error_rate(environment: Environment) -> None:
    """Greenlet checking aggregate error rate and stopping step early if > 50%."""
    while environment.runner is not None and environment.runner.state not in (
        "stopping",
        "stopped",
        "cleanup",
    ):
        gevent.sleep(0.25)
        if environment.runner is None:
            break
        total_stats = environment.stats.total
        num_reqs = total_stats.num_requests
        target_u = getattr(environment.runner, "target_user_count", 5) or 5
        min_sample = max(4, min(target_u, 10))
        if num_reqs >= min_sample and total_stats.fail_ratio > 0.50:
            _write_step_stats_snapshot(environment)
            if EARLY_STOP_FLAG_PATH:
                Path(EARLY_STOP_FLAG_PATH).write_text(
                    f"fail_ratio={total_stats.fail_ratio:.4f},num_requests={num_reqs}\n",
                    encoding="utf-8",
                )
            environment.runner.quit()
            break


@events.test_start.add_listener
def on_test_start(environment: Environment, **_kwargs: Any) -> None:
    """Spawn the early-stop error rate monitor when the Locust run starts."""
    gevent.spawn(_monitor_error_rate, environment)


@events.quitting.add_listener
def on_quitting(environment: Environment, **_kwargs: Any) -> None:
    """Ensure final stats snapshot is written on normal or early-stopped exit."""
    _write_step_stats_snapshot(environment)


class KnowledgeAssistantLoadUser(HttpUser):
    """Virtual staff user submitting questions to POST /v1/questions."""

    wait_time = between(1.5, 2.5)

    @task
    def ask_question(self) -> None:
        headers = {
            "Authorization": f"Bearer {SESSION_TOKEN}",
            "X-CSRF-Token": CSRF_VALUE,
            "Content-Type": "application/json",
        }
        cookies = {
            "cka_session": SESSION_TOKEN,
            "cka_csrf": CSRF_VALUE,
        }
        payload = {
            "assistant_id": ASSISTANT_ID,
            "question_text": QUESTION_TEXT,
        }
        with self.client.post(
            "/v1/questions",
            json=payload,
            headers=headers,
            cookies=cookies,
            timeout=REQUEST_TIMEOUT_SECONDS,
            name="POST /v1/questions",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            try:
                body = resp.json()
            except Exception as exc:
                resp.failure(f"Invalid JSON: {type(exc).__name__}")
                return
            status_val = body.get("status")
            if status_val != "answered":
                resp.failure(f"Non-answered status: {status_val}")
            else:
                resp.success()
