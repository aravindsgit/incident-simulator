"""Builds the postmortem draft from simulation facts and the responder's notes."""
from .sim import ALERT_ERROR_PCT, ALERT_MINUTES, ALERT_P95_MS, BUDGET_MINUTES, SCENARIOS, clock

NOTE_FIELDS = ("cause", "actions_taken", "went_well", "went_poorly")


def severity(summary):
    if summary["peak_error_pct"] >= 50 or summary["budget_used_pct"] >= 25:
        return "SEV1"
    if summary["peak_error_pct"] >= 20 or summary["budget_used_pct"] >= 10:
        return "SEV2"
    return "SEV3"


def _mins(n):
    return f"{n} minute" + ("" if n == 1 else "s")


def _todo(value, hint):
    return value.strip() if value and value.strip() else f"TODO: {hint}"


def observations(s):
    found = []
    if s["time_to_detect"] is None:
        found.append("No alert fired, so detection depends on someone noticing. The alert rule needs work.")
    elif s["time_to_detect"] >= 3:
        found.append(f"Detection took {_mins(s['time_to_detect'])}; consider a faster or more sensitive alert.")
    if s["time_to_mitigate"] is not None and s["time_to_mitigate"] >= 10:
        found.append(f"Mitigation started {_mins(s['time_to_mitigate'])} after the alert; "
                     "review runbooks and how quickly the fix can be applied.")
    if s["budget_used_pct"] >= 10:
        found.append(f"The incident used {s['budget_used_pct']}% of the monthly error budget.")
    return found


def draft(scenario, summary, events, notes):
    spec, s, notes = SCENARIOS[scenario], summary, notes or {}
    resolved = clock(s["resolved_at"]) if s["resolved_at"] is not None else "the end of the window (not resolved)"
    lines = [
        f"# Postmortem: {spec['title']}", "",
        f"**Status:** Draft | **Severity:** {severity(s)} | **Format:** blameless (describe systems and decisions, not people)", "",
        "## Summary",
        f"Between {clock(s['fault_at'])} and {resolved} (simulated time), {spec['fault'][0].lower()}{spec['fault'][1:]}. "
        f"Peak error rate reached {s['peak_error_pct']}% and p95 latency {s['peak_p95_ms']} ms. "
        f"{s['failed_requests']:,} of {s['total_requests']:,} requests failed during the incident.", "",
        "## Impact",
        f"- Availability during the incident: {s['availability_pct']}%",
        f"- Error budget: about {s['bad_minutes']} minutes of the {BUDGET_MINUTES:.1f}-minute monthly budget "
        f"for a 99.9% SLO ({s['budget_used_pct']}%)", "",
        "## Detection",
        (f"Detected at {clock(s['alert_at'])}, {_mins(s['time_to_detect'])} after the fault began, by the rule: "
         f"error rate >= {ALERT_ERROR_PCT:g}% or p95 >= {ALERT_P95_MS} ms for {ALERT_MINUTES} minutes.")
        if s["alert_at"] is not None else "No alert fired during the window.", "",
        "## Timeline (simulated time)",
    ]
    lines += [f"- {clock(e['minute'])} {e['text']}" for e in events]
    lines += ["", "## Root cause",
              _todo(notes.get("cause"), "what changed or failed, and why it was not caught earlier"), "",
              "## Resolution",
              notes.get("actions_taken", "").strip()
              or f"{spec['action']} (simulator default: replace with what you actually did)", "",
              "## What went well", _todo(notes.get("went_well"), "what helped, such as alerts, runbooks or teamwork"), "",
              "## What went poorly"]
    lines += [f"- {o}" for o in observations(s)]
    poorly = (notes.get("went_poorly") or "").strip()
    lines += [f"- {poorly}" if poorly else "- TODO: what slowed you down or made the impact larger", "", "## Action items"]
    lines += [f"- [ ] {item} (owner: TBD, priority: {'P1' if i == 0 else 'P2'})" for i, item in enumerate(spec["follow_ups"])]
    lines += ["", "## Lessons learned", "TODO: two or three sentences a new team member should remember.", ""]
    return "\n".join(lines)
