"""Tests for the deterministic query router."""
import pytest

from ipdr_agent.router import QueryRouter

ROUTER = QueryRouter(provider=None)  # rules-only, no LLM


@pytest.mark.parametrize("query,strategy,qtype", [
    ("Top 10 domains for IP 10.0.0.1", "sql", "aggregation"),
    ("Show the hourly activity trend", "sql", "timeseries"),
    ("Distribution of threat categories", "sql", "aggregation"),
    ("First activity of IP 10.0.0.2", "sql", "lookup"),
    ("Find similar beaconing behaviour", "rag", "pattern"),
    ("List all Banking events", "sql", "filter"),
])
def test_routing(query, strategy, qtype):
    d = ROUTER.route(query)
    assert d.strategy == strategy, d
    assert d.query_type == qtype, d


def test_visualization_defaults():
    assert ROUTER.route("Distribution of threats").visualization == "bar"
    assert ROUTER.route("hourly trend of events").visualization == "line"


def test_explicit_viz_override():
    assert ROUTER.route("threat distribution as a pie").visualization == "pie"


def test_confidence_is_bounded():
    d = ROUTER.route("top most distribution trend hourly")
    assert 0.0 <= d.confidence <= 0.95
    assert d.source == "rules"
