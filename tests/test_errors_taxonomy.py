"""ONE failure taxonomy.

Before this, three vocabularies coexisted: `errors.classify` returned transient/lockout/...,
`runner.handle_failure` wrote a bare `llm_down` string, and `diagnostics` proved granular kinds
(AUTH_REJECTED, FIELD_REQUIRED, ...) that never reached `jobs.fail_class`. The ops report therefore
grouped by whatever string happened to be written, and a new diagnosis could become an unlabelled
bucket without any test noticing. These tests pin the single registry and its two layers.
"""
import json
from pathlib import Path

from apply import diagnostics as dx
from apply import errors

ROOT = Path(__file__).resolve().parents[1]
FAILURES = ROOT / "data" / "failures"
POLICY_KEYS = {"transient", "lockout", "permanent", "captcha", "personal", "logic"}


def test_diagnostics_kinds_and_fail_classes_are_the_same_set():
    """Adding a Problem kind without a fail_class (or the reverse) must fail here, not in ops."""
    assert set(dx.KINDS) == set(errors.KIND_TO_CLASS), (
        set(dx.KINDS) ^ set(errors.KIND_TO_CLASS))


def test_every_fail_class_is_well_formed_and_inherits_a_real_policy():
    for key, entry in errors.CLASSES.items():
        assert entry.key == key
        assert entry.label and entry.description
        if entry.retry_as is not None:
            assert entry.retry_as in errors.CLASSES
            assert entry.retry_as in POLICY_KEYS or entry.retry_as in errors.CLASSES
    # every alias points at a real key and never at another alias
    for alias, key in errors.ALIASES.items():
        assert key in errors.CLASSES and alias not in errors.CLASSES


def test_legacy_stored_strings_still_parse():
    """data/jobhunt.db already holds these; a rename must not orphan them."""
    for old in ("transient", "lockout", "permanent", "captcha", "personal", "logic",
                "llm_down", "LOGIC", "  captcha  "):
        assert errors.fail_class(old) is not None, old
    assert errors.canonical("llm_down") == "llm_unavailable"
    assert errors.canonical("llm_unavailable") == "llm_unavailable"
    assert errors.canonical("transient") == "transient"
    # an unknown stored value is shown as stored, never silently re-bucketed
    assert errors.canonical("some_future_class") == "some_future_class"
    assert errors.fail_class("some_future_class") is None


def test_classify_only_returns_registered_keys():
    """The runner switches on these exact strings; the registry must stay backward compatible."""
    cases = {
        "CAPTCHA on acme.wd5": "captcha",
        "too many attempts, try again later": "lockout",
        "this job is closed": "permanent",
        "TimeoutError: page did not load": "transient",
        "unanswered required: passport number": "personal",
        # a company: a Google-Form application behind accounts.google.com cannot be automated with her account
        "navigator: {'done': 'blocked', 'why': 'Google account challenge page offers no usable element'}": "personal",
        "page stopped changing": "logic",
        # a portal's own maintenance page is not ours and not the job's (General Mills was stamped logic)
        "navigator: {'done': 'blocked', 'why': 'Workday is currently unavailable (service interruption maintenance page)'}": "transient",
        #  a company: a message saying the job is NOT closed was classified `permanent` (-> closed) by the bare word.
        # KB-58 then made the retry real: a picker that fails can succeed in a fresh session, so this is transient - never 'closed'.
        "could not set School or University (still required after 2 attempts); it is not closed, but our picker could not set her real value": "transient",
        # KB-58: a missing Apply control is an observation, not a closure (Naukri signed-out page / Workday API 403)
        "Naukri page is signed out (no Apply button and no signed-in header)": "transient",
        "no Apply button on a signed-in Naukri page (layout change or the listing moved)": "transient",
        "the posting was never closed and it isn't expired": "logic",
        "this job is closed": "permanent",  # the bare word still closes a job
    }
    for detail, expected in cases.items():
        got = errors.classify("failed", detail)
        assert got == expected, (detail, got)
        assert got in errors.CLASSES


def test_granular_classes_inherit_the_retry_policy():
    assert errors.retry_as("timeout") == "transient"
    assert errors.retry_as("llm_unavailable") == "transient"
    assert errors.retry_as("llm_down") == "transient"
    assert errors.retry_as("auth_verify") == "transient"
    assert errors.retry_as("auth_rejected") == "lockout"
    assert errors.retry_as("needs_fact") == "personal"
    assert errors.retry_as("logic") is None
    # next_attempt accepts a granular key, so no caller needs a second table
    assert errors.next_attempt("timeout", 0) is not None
    assert errors.next_attempt("auth_verify", 0) is not None
    assert errors.next_attempt("logic", 0) is None


def test_diagnostics_fail_class_uses_the_one_registry():
    assert dx.fail_class([dx.Problem(dx.FIELD_REQUIRED)]) == "field_required"
    assert dx.fail_class([dx.Problem(dx.COUNTRY_MISMATCH)]) == "wrong_environment"
    assert dx.fail_class([dx.Problem(dx.CONFIRMED)]) is None
    assert dx.fail_class([dx.Problem(dx.CONFIRMED), dx.Problem(dx.UNKNOWN)]) == "transient"


def test_a_real_consent_snapshot_has_a_groupable_fail_class():
    """The saved corpus must produce a key the ops dashboard can count, not a prose bucket."""
    import pytest
    if not (FAILURES / "corpus_01.json").exists():
        pytest.skip("saved failure snapshot not present (private corpus in data/failures)")
    record = json.loads((FAILURES / "corpus_01.json").read_text(encoding="utf-8"))
    problems = dx.diagnose(record)
    key = dx.fail_class(problems)
    assert key == "consent_required"
    assert errors.fail_class(key) is not None
    assert errors.retry_as(key) == "logic"


def test_every_class_maps_to_the_status_the_runner_writes():
    """One class -> one status, as data; an unknown class is our bug ('failed'), never a closure."""
    assert errors.status_for("transient") == "candidate"
    assert errors.status_for("lockout") == "candidate"
    assert errors.status_for("permanent") == "closed"
    assert errors.status_for("captcha") == "needs_you"
    assert errors.status_for("personal") == "needs_you"
    assert errors.status_for("logic") == "failed"
    # granular keys inherit through retry_as
    assert errors.status_for("auth_rejected") == "candidate"
    assert errors.status_for("needs_fact") == "needs_you"
    assert errors.status_for("field_required") == "failed"
    assert errors.status_for("llm_down") == "candidate"
    assert errors.status_for("not_a_class") == "failed"
    for key in errors.CLASSES:
        assert errors.status_for(key) in ("candidate", "closed", "needs_you", "failed")


def test_for_kinds_accepts_bare_strings_too():
    assert errors.for_kinds(["JOB_CLOSED", "FIELD_REQUIRED"]) == "permanent"
    assert errors.for_kinds([]) is None
    assert errors.for_kinds(["NOT_A_KIND"]) is None
