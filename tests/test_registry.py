import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from service_assistant.domain.contracts import AssistantReply, ConversationState
from service_assistant.services.discovery import ServiceDiscovery
from service_assistant.services.registry import ServiceRegistry, load_registry
from service_assistant.tools.verify_url import verify_url

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def registry_data(now=NOW):
    # Synthetic fixtures only. These records are never shipped as trusted sources.
    return {
        "version": "test-v1",
        "sources": [
            {
                "id": "fixture-utility",
                "provider": "Fixture Utility",
                "provider_aliases": ["FU"],
                "hostname": "fixture.gov.uk",
                "allowed_paths": ["/bill"],
                "provenance": "Synthetic test fixture, not an official service",
                "reviewed_by": "test-suite",
                "reviewed_at": (now - timedelta(days=1)).isoformat(),
                "review_expires_at": (now + timedelta(days=1)).isoformat(),
            }
        ],
        "services": [
            {
                "id": "fixture-bill",
                "source_id": "fixture-utility",
                "intent": "electricity",
                "label": "Fixture bill information",
                "jurisdiction_aliases": ["Testland", "TL"],
                "official_url": "https://fixture.gov.uk/bill",
            }
        ],
    }


def discovery(data=None):
    return ServiceDiscovery(
        ServiceRegistry.model_validate(data or registry_data()), clock=lambda: NOW
    )


def context(**updates):
    return ConversationState(
        intent="electricity", provider="Fixture Utility", jurisdiction="Testland", **updates
    )


def test_exact_verified_match_has_evidence():
    result = discovery().discover(context())
    assert result.status == "answered"
    assert result.links[0].verification == "registry_verified"
    assert result.links[0].reachability == "not_checked"
    assert result.links[0].source_id == "fixture-utility"
    assert result.links[0].registry_version == "test-v1"


def test_aliases_and_whitespace_normalize():
    result = discovery().discover(
        ConversationState(intent="electricity", provider=" fu ", jurisdiction=" tl ")
    )
    assert result.status == "answered"


def test_missing_location_is_asked_even_for_unique_provider():
    result = discovery().discover(
        ConversationState(intent="electricity", provider="Fixture Utility")
    )
    assert result.clarification.field == "jurisdiction"
    assert not result.links


@pytest.mark.parametrize(
    "field,value",
    [("provider", "Fixture"), ("jurisdiction", "Otherland"), ("service_detail", "unrelated")],
)
def test_inexact_or_conflicting_context_never_matches(field, value):
    state = context().model_copy(update={field: value})
    assert discovery().discover(state).status == "no_verified_match"


@pytest.mark.parametrize(
    "change", ["disabled_source", "disabled_service", "expired", "future_review"]
)
def test_inactive_evidence_is_not_returned(change):
    data = registry_data()
    if change == "disabled_source":
        data["sources"][0]["enabled"] = False
    elif change == "disabled_service":
        data["services"][0]["enabled"] = False
    elif change == "expired":
        data["sources"][0]["review_expires_at"] = NOW.isoformat()
    else:
        data["sources"][0]["reviewed_at"] = (NOW + timedelta(hours=1)).isoformat()
    assert discovery(data).discover(context()).status == "no_verified_match"


def test_ambiguous_matches_do_not_pick_first():
    data = registry_data()
    data["services"].append({**data["services"][0], "id": "another-bill"})
    result = discovery(data).discover(context(service_detail="bill"))
    assert result.status == "no_verified_match"
    result = discovery(data).discover(context())
    assert result.clarification.field == "service_detail"
    assert not result.links


def test_service_detail_selects_exact_service():
    data = registry_data()
    data["services"][0].update(intent="government_form", detail_aliases=["passport"])
    assert (
        discovery(data)
        .discover(
            ConversationState(
                intent="government_form", jurisdiction="Testland", service_detail="passport"
            )
        )
        .status
        == "answered"
    )
    assert (
        discovery(data)
        .discover(
            ConversationState(
                intent="appointment", jurisdiction="Testland", service_detail="passport"
            )
        )
        .status
        == "no_verified_match"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://fixture.gov.uk/bill",
        "https://fixture.gov.uk.attacker.com/bill",
        "https://evilfixture.gov.uk/bill",
        "https://sub.fixture.gov.uk/bill",
        "https://fixture.gov.uk@attacker.com/bill",
        "https://attacker.com@fixture.gov.uk/bill",
        "https://fixture.gov.uk:443/bill",
        "https://fixture.gov.uk./bill",
        "https://fixture.gov.uk/bill/evil",
        "https://fixture.gov.uk/bill?next=https://attacker.com",
        "https://fixture.gov.uk/bill#evil",
        "https://fixture.gov.uk/%62ill",
        "https://fixture.gov.uk/a/../bill",
        "https://fixture.gov.uk//bill",
        "https://fixture.gov.uk/bill\n",
        "https://fixture.gov.uk\\@attacker.com/bill",
        "https://127.0.0.1/bill",
        "https://169.254.169.254/bill",
        "https://[::1]/bill",
        "https://fıxture.gov.uk/bill",
        "file:///bill",
        "javascript:alert(1)",
    ],
)
def test_unsafe_urls_rejected(url):
    assert verify_url(url, "fixture.gov.uk", ("/bill",)) is not None
    data = registry_data()
    data["services"][0]["official_url"] = url
    assert discovery(data).discover(context()).status == "no_verified_match"


def test_exact_url_accepted():
    assert verify_url("https://fixture.gov.uk/bill", "fixture.gov.uk", ("/bill",)) is None


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "169.254.169.254",
        "[::1]",
        "host.local",
        "host.internal",
        "*.gov.uk",
        "xn--fixture.gov.uk",
    ],
)
def test_unsafe_trust_hosts_cannot_be_registered(host):
    data = registry_data()
    data["sources"][0]["hostname"] = host
    with pytest.raises(ValidationError):
        ServiceRegistry.model_validate(data)


@pytest.mark.parametrize(
    "change", ["duplicate", "missing_source", "unknown_field", "missing_provenance"]
)
def test_registry_integrity(change):
    data = registry_data()
    if change == "duplicate":
        data["sources"].append(data["sources"][0])
    elif change == "missing_source":
        data["services"][0]["source_id"] = "absent"
    elif change == "unknown_field":
        data["sources"][0]["trust_any_subdomain"] = True
    else:
        del data["sources"][0]["provenance"]
    with pytest.raises(ValidationError):
        ServiceRegistry.model_validate(data)


def test_registry_loader(tmp_path):
    assert not load_registry(None).services
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry_data()))
    assert load_registry(path).version == "test-v1"
    path.write_text("not-json")
    with pytest.raises(ValueError):
        load_registry(path)


def test_cached_links_revalidated_on_expiry_and_revision():
    current = discovery()
    links = current.discover(context()).links
    assert current.links_current(links)
    expired = ServiceDiscovery(current.registry, clock=lambda: NOW + timedelta(days=2))
    assert not expired.links_current(links)
    data = registry_data()
    data["version"] = "test-v2"
    assert not discovery(data).links_current(links)
    data["sources"][0]["enabled"] = False
    assert not discovery(data).links_current(links)


@pytest.mark.parametrize("status", ["answered", "needs_clarification"])
def test_response_requires_evidence_or_question(status):
    with pytest.raises(ValidationError):
        AssistantReply(status=status, message="unsubstantiated")


def test_authoring_example_is_valid_but_disabled():
    from pathlib import Path

    registry = load_registry(Path("examples/registry.example.json"))
    assert not any(source.enabled for source in registry.sources)
    assert not any(service.enabled for service in registry.services)


def test_plugin_requirements_are_replaceable():
    from service_assistant.orchestration.engine import FakeIntentProvider, Orchestrator
    from service_assistant.services.plugins import ServicePlugin, plugin_map

    plugin = ServicePlugin("electricity", ("jurisdiction",))
    engine = Orchestrator(FakeIntentProvider(), plugins=(plugin,))
    _, reply = engine.reply("electricity", ConversationState())
    assert reply.clarification.field == "jurisdiction"
    with pytest.raises(ValueError, match="Duplicate service plugins"):
        plugin_map((plugin, plugin))
