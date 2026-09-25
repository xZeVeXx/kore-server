"""Gemeinsame pytest-Einstellung fuer die Bridge-Tests.

Nach jedem erfolgreichen Chat-Turn liest die Bridge den Hermes-Titel ueber `sessions export`, also
ueber denselben Prozess-Runner, den die Tests mit Fakes ersetzen. Damit diese Fakes nur den
Chat-Lauf sehen, ist die Titelsuche hier standardmaessig aus; die Titel-Tests
(test_journal_title.py) schalten sie gezielt wieder ein.
"""
import pytest

import bridge


@pytest.fixture(autouse=True)
def no_session_title_lookup(monkeypatch):
    monkeypatch.setattr(bridge, "lookup_session_title", lambda hermes_session_id: None)
