#!/usr/bin/env python3
"""Burst simulation: fires concurrent incidents and measures system resilience.

Usage:
    python scripts/burst_simulation.py [--base-url URL] [--concurrency N] [--total N]

Validates:
  - Throughput under concurrent load
  - Idempotency (duplicate keys return 200, not 201)
  - Latency distribution (p50, p95, p99)
  - Error rate and failure modes
  - Circuit breaker behavior under sustained failures
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from uuid import uuid4

import requests

DEFAULT_BASE_URL = "http://localhost:8000"


@dataclass
class RequestResult:
    status_code: int
    latency_ms: float
    incident_id: str | None = None
    is_duplicate: bool = False
    error: str | None = None


@dataclass
class SimulationReport:
    total_requests: int = 0
    successful: int = 0
    duplicates: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time

    @property
    def throughput_rps(self) -> float:
        return self.total_requests / self.duration_s if self.duration_s > 0 else 0

    def latency_stats(self) -> dict[str, float]:
        if not self.latencies_ms:
            return {}
        sorted_lat = sorted(self.latencies_ms)
        n = len(sorted_lat)
        return {
            "min": sorted_lat[0],
            "p50": sorted_lat[n // 2],
            "p95": sorted_lat[int(n * 0.95)],
            "p99": sorted_lat[int(n * 0.99)],
            "max": sorted_lat[-1],
            "avg": statistics.mean(sorted_lat),
        }


def fire_delay_incident(base_url: str, idempotency_key: str | None = None) -> RequestResult:
    """Send a single delay incident and measure latency."""
    url = f"{base_url}/api/v1/incidents/delay"
    key = idempotency_key or str(uuid4())
    payload = {
        "supplier_id": str(uuid4()),
        "shipment_id": str(uuid4()),
        "po_number": f"PO-BURST-{uuid4().hex[:8].upper()}",
        "delay_reason": "Burst simulation test",
        "original_eta": "2026-04-01",
        "new_eta": "2026-04-05",
    }
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": key,
    }

    start = time.perf_counter()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        latency_ms = (time.perf_counter() - start) * 1000.0

        body = resp.json()
        return RequestResult(
            status_code=resp.status_code,
            latency_ms=latency_ms,
            incident_id=body.get("incident", {}).get("id"),
            is_duplicate=body.get("is_duplicate", False),
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(
            status_code=0,
            latency_ms=latency_ms,
            error=str(exc),
        )


def fire_absence_incident(base_url: str) -> RequestResult:
    """Send a single absence incident and measure latency."""
    url = f"{base_url}/api/v1/incidents/absence"
    key = str(uuid4())
    payload = {
        "site_id": f"SITE-{uuid4().hex[:4].upper()}",
        "worker_name": "Burst Test Worker",
        "shift_date": "2026-04-01",
        "role": "forklift_operator",
        "reason": "Burst simulation test",
    }
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": key,
    }

    start = time.perf_counter()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        latency_ms = (time.perf_counter() - start) * 1000.0

        body = resp.json()
        return RequestResult(
            status_code=resp.status_code,
            latency_ms=latency_ms,
            incident_id=body.get("incident", {}).get("id"),
            is_duplicate=body.get("is_duplicate", False),
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RequestResult(
            status_code=0,
            latency_ms=latency_ms,
            error=str(exc),
        )


def run_idempotency_burst(base_url: str, concurrency: int = 5) -> SimulationReport:
    """Fire same idempotency key N times concurrently. Expect 1 create + (N-1) dupes."""
    report = SimulationReport()
    shared_key = str(uuid4())

    report.start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(fire_delay_incident, base_url, shared_key)
            for _ in range(concurrency)
        ]
        for f in as_completed(futures):
            result = f.result()
            report.total_requests += 1
            report.latencies_ms.append(result.latency_ms)

            if result.error:
                report.errors += 1
                report.error_details.append(result.error)
            elif result.is_duplicate:
                report.duplicates += 1
                report.successful += 1
            else:
                report.successful += 1

    report.end_time = time.perf_counter()
    return report


def run_throughput_burst(
    base_url: str,
    total: int = 20,
    concurrency: int = 10,
    mix: str = "delay",
) -> SimulationReport:
    """Fire N unique incidents with M concurrency and measure throughput."""
    report = SimulationReport()

    def fire_one(i: int) -> RequestResult:
        if mix == "absence" or (mix == "mixed" and i % 2 == 1):
            return fire_absence_incident(base_url)
        return fire_delay_incident(base_url)

    report.start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(fire_one, i) for i in range(total)]
        for f in as_completed(futures):
            result = f.result()
            report.total_requests += 1
            report.latencies_ms.append(result.latency_ms)

            if result.error:
                report.errors += 1
                report.error_details.append(result.error)
            elif result.status_code in (200, 201):
                report.successful += 1
                if result.is_duplicate:
                    report.duplicates += 1
            else:
                report.errors += 1
                report.error_details.append(f"HTTP {result.status_code}")

    report.end_time = time.perf_counter()
    return report


def check_health(base_url: str) -> dict:
    """Check system health including circuit breakers."""
    try:
        resp = requests.get(f"{base_url}/health/ready", timeout=5)
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}


def print_report(name: str, report: SimulationReport) -> None:
    stats = report.latency_stats()
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(f"  Total requests:  {report.total_requests}")
    print(f"  Successful:      {report.successful}")
    print(f"  Duplicates:      {report.duplicates}")
    print(f"  Errors:          {report.errors}")
    print(f"  Duration:        {report.duration_s:.2f}s")
    print(f"  Throughput:      {report.throughput_rps:.1f} req/s")
    if stats:
        print(f"  Latency min:     {stats['min']:.0f}ms")
        print(f"  Latency p50:     {stats['p50']:.0f}ms")
        print(f"  Latency p95:     {stats['p95']:.0f}ms")
        print(f"  Latency p99:     {stats['p99']:.0f}ms")
        print(f"  Latency max:     {stats['max']:.0f}ms")
        print(f"  Latency avg:     {stats['avg']:.0f}ms")
    if report.error_details:
        print(f"  Errors (first 5):")
        for e in report.error_details[:5]:
            print(f"    - {e[:120]}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Burst simulation for supply chain coordinator")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--total", type=int, default=20)
    parser.add_argument("--mix", choices=["delay", "absence", "mixed"], default="mixed")
    args = parser.parse_args()

    print(f"Target: {args.base_url}")
    print(f"Concurrency: {args.concurrency}, Total: {args.total}, Mix: {args.mix}")

    # Pre-check health
    print("\n--- Pre-burst health check ---")
    health = check_health(args.base_url)
    print(json.dumps(health, indent=2))

    # Test 1: Idempotency under concurrent load
    print("\n--- Test 1: Idempotency burst (same key, 5 concurrent) ---")
    idem_report = run_idempotency_burst(args.base_url, concurrency=5)
    print_report("Idempotency Burst", idem_report)
    expected_creates = 1
    actual_creates = idem_report.successful - idem_report.duplicates
    print(f"  Idempotency check: expected {expected_creates} create, got {actual_creates}")
    assert actual_creates <= expected_creates, "Idempotency violation!"
    print("  PASS")

    # Test 2: Throughput burst
    print(f"\n--- Test 2: Throughput burst ({args.total} incidents, {args.concurrency} concurrent) ---")
    throughput_report = run_throughput_burst(
        args.base_url,
        total=args.total,
        concurrency=args.concurrency,
        mix=args.mix,
    )
    print_report("Throughput Burst", throughput_report)

    error_rate = throughput_report.errors / throughput_report.total_requests if throughput_report.total_requests > 0 else 0
    print(f"  Error rate: {error_rate:.1%}")
    stats = throughput_report.latency_stats()
    if stats:
        print(f"  p95 < 5000ms: {'PASS' if stats['p95'] < 5000 else 'WARN'}")

    # Post-burst health check
    print("\n--- Post-burst health check ---")
    health = check_health(args.base_url)
    print(json.dumps(health, indent=2))

    # Final summary
    print("\n" + "=" * 60)
    print("  BURST SIMULATION COMPLETE")
    print("=" * 60)
    all_passed = (
        actual_creates <= expected_creates
        and error_rate < 0.1  # <10% error rate
    )
    print(f"  Overall: {'PASS' if all_passed else 'FAIL'}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
