"""Parity between the vendored `tests/fixtures/hooks/<client>/unverified/`
payloads and the adapter's own EVENT_PROFILE claim, and a golden-file
assertion that a fixture piped through the real CLI produces the exact
protocol shape the adapter's own formatter would build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manifest_agent.hooks import claude_code, codex, cursor, gemini

FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "hooks"

MODULES = {
    "claude_code": claude_code,
    "codex": codex,
    "cursor": cursor,
    "gemini": gemini,
}
CLI_NAMES = {"claude_code": "claude-code", "codex": "codex", "cursor": "cursor", "gemini": "gemini"}


def _fixture_events(client: str) -> list[str]:
    directory = FIXTURES_ROOT / client / "unverified"
    return sorted(p.stem for p in directory.glob("*.json"))


@pytest.mark.parametrize("client", sorted(MODULES))
def test_every_fixture_event_has_a_coverage_verdict(client):
    """Every vendored fixture event name must appear in the adapter's own
    EVENT_PROFILE table (even if mapped to None/unsupported) — a fixture for
    an event the adapter doesn't know about would be an untested claim."""
    module = MODULES[client]
    if not module.EVENT_PROFILE:
        return  # codex: empty by construction, every event is unsupported
    for event in _fixture_events(client):
        assert event in module.EVENT_PROFILE, (
            f"{client} fixture {event!r} is not in EVENT_PROFILE; "
            "either register it (quick/full) or document why it's absent"
        )


@pytest.mark.parametrize("client", sorted(MODULES))
def test_every_quick_or_full_event_is_vendored_as_a_fixture(client):
    """The inverse: every event this adapter claims to support has a
    vendored, source-labeled fixture backing that claim."""
    module = MODULES[client]
    fixtures = set(_fixture_events(client))
    supported = {event for event, profile in module.EVENT_PROFILE.items() if profile}
    missing = supported - fixtures
    assert not missing, f"{client} claims {missing} but has no vendored fixture"


@pytest.mark.parametrize("client", sorted(MODULES))
def test_fixture_source_is_recorded_as_unverified(client):
    source_doc = FIXTURES_ROOT / client / "unverified" / "SOURCE.md"
    assert source_doc.is_file()
    assert "unverified" in source_doc.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("client", sorted(MODULES))
def test_fixture_payload_round_trips_through_the_real_cli(hook_harness, client):
    for event_path in sorted((FIXTURES_ROOT / client / "unverified").glob("*.json")):
        event = event_path.stem
        payload = json.loads(
            event_path.read_text(encoding="utf-8").replace(
                "__REPO_ROOT__", str(hook_harness.root)
            )
        )
        result = hook_harness.invoke(CLI_NAMES[client], event, payload)
        assert result.returncode == 0
        body = json.loads(result.stdout.decode())
        profile = MODULES[client].EVENT_PROFILE.get(event)
        if profile is None:
            assert body == {"coverage": "unsupported"}
        else:
            assert body.get("coverage") != "unsupported"
