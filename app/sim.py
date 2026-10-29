"""Deterministic incident simulation. Pure Python: no web or database code."""
import random

SLO_TARGET = 99.9
BUDGET_MINUTES = 30 * 24 * 60 * (1 - SLO_TARGET / 100)  # about 43.2 minutes a month
ALERT_ERROR_PCT = 5.0
ALERT_P95_MS = 2000
ALERT_MINUTES = 2  # consecutive bad minutes before the alert fires
BASE_REQUESTS, BASE_ERROR_PCT, BASE_P95_MS = 6000, 0.2, 180

SCENARIOS = {
    "bad_deploy": {
        "title": "Bad deployment",
        "fault": "A new release began failing a share of requests",
        "action": "Rolled back to the previous release",
        "error_pct": 35.0, "latency_x": 1.4, "ramp": 1,
        "follow_ups": ["Add a canary release that rolls back automatically when the error rate rises",
                       "Add a smoke test to the deployment pipeline",
                       "Alert on error rate per release version"],
    },
    "db_pool": {
        "title": "Database connection pool exhaustion",
        "fault": "The service ran out of database connections and requests began to queue",
        "action": "Restarted the service and raised the connection pool limit",
        "error_pct": 40.0, "latency_x": 14.0, "ramp": 5,
        "follow_ups": ["Alert on connection pool usage before it reaches 100 percent",
                       "Set connection timeouts and review pool sizing",
                       "Write a runbook for pool exhaustion"],
    },
    "dependency_timeout": {
        "title": "Dependency timeout",
        "fault": "A downstream service stopped answering within the timeout",
        "action": "Disabled the failing integration with a feature flag",
        "error_pct": 20.0, "latency_x": 16.0, "ramp": 1,
        "follow_ups": ["Add a circuit breaker and sensible timeouts for the dependency",
                       "Serve a degraded response when the dependency is down",
                       "Add a synthetic check that covers the dependency"],
    },
    "memory_leak": {
        "title": "Memory leak",
        "fault": "Memory use grew until the service slowed and began failing requests",
        "action": "Restarted the affected instances",
        "error_pct": 25.0, "latency_x": 12.0, "ramp": 8,
        "follow_ups": ["Alert on the memory growth trend, not only on the limit",
                       "Find and fix the leak, then add a soak test",
                       "Set memory limits and automatic restarts"],
    },
    "cert_expiry": {
        "title": "Expired TLS certificate",
        "fault": "A TLS certificate expired and clients could not connect",
        "action": "Renewed and deployed the certificate",
        "error_pct": 100.0, "latency_x": 1.0, "ramp": 1,
        "follow_ups": ["Alert on certificate expiry 30 days ahead",
                       "Automate certificate renewal",
                       "Keep an inventory of every certificate and its owner"],
    },
}


def clock(minute, start_hour=14):
    """Simulated wall-clock label, for example 14:08."""
    return f"{start_hour + minute // 60:02d}:{minute % 60:02d}"


def validate_params(form):
    errors, clean = {}, {}
    scenario = str(form.get("scenario", ""))
    if scenario not in SCENARIOS:
        errors["scenario"] = "Choose one of the listed scenarios."
    clean["scenario"] = scenario
    limits = {"duration": (20, 120, 40), "fault_at": (1, 100, 8), "mitigate_after": (3, 60, 15), "seed": (0, 9999, 7)}
    for key, (low, high, default) in limits.items():
        raw = str(form.get(key, "")).strip()
        try:
            value = int(raw) if raw else default
        except ValueError:
            errors[key] = "Enter a whole number."
            continue
        if not low <= value <= high:
            errors[key] = f"Enter a number from {low} to {high}."
        clean[key] = value
    if not errors and clean["fault_at"] + clean["mitigate_after"] + 3 > clean["duration"]:
        errors["duration"] = "Make the simulation longer, or start the fault earlier, so recovery fits in the window."
    return clean, errors


def _severity(minute, fault_at, mitigated_at, ramp):
    if minute < fault_at:
        return 0.0
    peak = min(1.0, (mitigated_at - fault_at) / ramp)
    if minute < mitigated_at:
        return min(1.0, (minute - fault_at + 1) / ramp)
    return peak * max(0.0, 0.4 - 0.2 * (minute - mitigated_at))  # two-minute recovery tail


def simulate(spec, duration, fault_at, mitigated_at, seed):
    rng = random.Random(seed)
    rows = []
    for minute in range(duration):
        requests = BASE_REQUESTS * (1 + rng.uniform(-0.05, 0.05))
        error = BASE_ERROR_PCT + rng.uniform(0, 0.15)
        p95 = BASE_P95_MS + rng.uniform(-15, 15)
        severity = _severity(minute, fault_at, mitigated_at, spec["ramp"])
        error = min(100.0, error + spec["error_pct"] * severity)
        p95 = p95 * (1 + (spec["latency_x"] - 1) * severity)
        rows.append({"minute": minute, "requests": int(requests), "error_pct": round(error, 2),
                     "p95_ms": int(p95), "failed": int(round(requests * error / 100))})
    return rows


def _breach(row):
    return row["error_pct"] >= ALERT_ERROR_PCT or row["p95_ms"] >= ALERT_P95_MS


def analyse(metrics, fault_at, mitigated_at):
    alert_at, streak = None, 0
    for row in metrics:
        streak = streak + 1 if _breach(row) else 0
        if streak >= ALERT_MINUTES:
            alert_at = row["minute"]
            break
    resolved_at = next((r["minute"] for r in metrics
                        if r["minute"] >= mitigated_at and r["error_pct"] < 1 and r["p95_ms"] < 300), None)
    end = resolved_at if resolved_at is not None else len(metrics)
    window = [r for r in metrics if fault_at <= r["minute"] < end]
    failed = sum(r["failed"] for r in window)
    total = sum(r["requests"] for r in window)
    bad_minutes = sum(r["error_pct"] / 100 for r in window)
    return {
        "fault_at": fault_at, "alert_at": alert_at, "mitigated_at": mitigated_at, "resolved_at": resolved_at,
        "time_to_detect": None if alert_at is None else alert_at - fault_at,
        "time_to_mitigate": mitigated_at - alert_at if alert_at is not None and alert_at <= mitigated_at else None,
        "time_to_resolve": None if resolved_at is None else resolved_at - fault_at,
        "peak_error_pct": round(max(r["error_pct"] for r in metrics), 1),
        "peak_p95_ms": max(r["p95_ms"] for r in metrics),
        "failed_requests": failed, "total_requests": total,
        "availability_pct": round(100 * (1 - failed / total), 2) if total else 100.0,
        "bad_minutes": round(bad_minutes, 1),
        "budget_used_pct": round(bad_minutes / BUDGET_MINUTES * 100, 1),
    }


def build_events(spec, summary):
    events = [{"minute": summary["fault_at"], "kind": "fault", "text": f"Simulator: {spec['fault']}"}]
    if summary["alert_at"] is not None:
        events.append({"minute": summary["alert_at"], "kind": "alert",
                       "text": f"Alert fired: error rate >= {ALERT_ERROR_PCT:g}% or p95 >= {ALERT_P95_MS} ms "
                               f"for {ALERT_MINUTES} minutes"})
    events.append({"minute": summary["mitigated_at"], "kind": "action", "text": f"Responder action: {spec['action']}"})
    if summary["resolved_at"] is not None:
        events.append({"minute": summary["resolved_at"], "kind": "resolved",
                       "text": "Service healthy again: error rate under 1% and p95 under 300 ms"})
    return sorted(events, key=lambda e: e["minute"])


def run(scenario, duration=40, fault_at=8, mitigate_after=15, seed=7):
    spec = SCENARIOS[scenario]
    mitigated_at = fault_at + mitigate_after
    metrics = simulate(spec, duration, fault_at, mitigated_at, seed)
    summary = analyse(metrics, fault_at, mitigated_at)
    return {"metrics": metrics, "summary": summary, "events": build_events(spec, summary)}
