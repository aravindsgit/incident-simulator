from app.postmortem import draft, severity
from app.sim import ALERT_MINUTES, BUDGET_MINUTES, SCENARIOS, run, validate_params


def test_same_seed_gives_same_result():
    assert run("bad_deploy", seed=3) == run("bad_deploy", seed=3)
    assert run("bad_deploy", seed=3)["metrics"] != run("bad_deploy", seed=4)["metrics"]


def test_every_scenario_alerts_mitigates_and_resolves_in_order():
    for key in SCENARIOS:
        s = run(key)["summary"]
        assert s["alert_at"] is not None, key
        assert s["fault_at"] < s["alert_at"] <= s["mitigated_at"] < s["resolved_at"], key
        assert s["time_to_detect"] >= ALERT_MINUTES - 1, key


def test_bad_deploy_numbers_are_sensible():
    s = run("bad_deploy")["summary"]
    assert s["time_to_detect"] == 1 and s["time_to_resolve"] == 17
    assert 30 < s["peak_error_pct"] < 40
    assert s["failed_requests"] > 0 and s["availability_pct"] < 80
    assert abs(s["budget_used_pct"] - s["bad_minutes"] / BUDGET_MINUTES * 100) < 0.1


def test_cert_expiry_hits_one_hundred_percent_errors_but_not_more():
    assert max(r["error_pct"] for r in run("cert_expiry")["metrics"]) == 100.0


def test_quiet_before_the_fault():
    rows = run("db_pool", fault_at=10)["metrics"][:10]
    assert all(r["error_pct"] < 1 and r["p95_ms"] < 300 for r in rows)


def test_validation_accepts_defaults_and_rejects_bad_input():
    clean, errors = validate_params({"scenario": "bad_deploy"})
    assert errors == {} and clean["duration"] == 40 and clean["seed"] == 7
    assert "scenario" in validate_params({"scenario": "nope"})[1]
    assert "duration" in validate_params({"scenario": "bad_deploy", "duration": "10"})[1]
    assert "fault_at" in validate_params({"scenario": "bad_deploy", "fault_at": "abc"})[1]
    assert "duration" in validate_params({"scenario": "bad_deploy", "duration": "30", "fault_at": "20",
                                          "mitigate_after": "15"})[1]


def test_severity_levels():
    base = {"peak_error_pct": 10, "budget_used_pct": 1}
    assert severity(base) == "SEV3"
    assert severity({**base, "peak_error_pct": 25}) == "SEV2"
    assert severity({**base, "budget_used_pct": 30}) == "SEV1"


def test_draft_uses_notes_and_marks_missing_parts_as_todo():
    result = run("bad_deploy")
    empty = draft("bad_deploy", result["summary"], result["events"], {})
    assert "# Postmortem: Bad deployment" in empty and "TODO: what changed" in empty
    assert "Rolled back to the previous release" in empty and "- [ ] Add a canary release" in empty
    filled = draft("bad_deploy", result["summary"], result["events"], {"cause": "A config change removed a header."})
    assert "A config change removed a header." in filled and "TODO: what changed" not in filled
