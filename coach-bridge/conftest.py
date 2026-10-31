"""Gemeinsame pytest-Einstellung fuer die Bridge-Tests.

Nach jedem erfolgreichen Chat-Turn liest die Bridge den Hermes-Titel ueber `sessions export`, also
ueber denselben Prozess-Runner, den die Tests mit Fakes ersetzen. Damit diese Fakes nur den
Chat-Lauf sehen, ist die Titelsuche hier standardmaessig aus; die Titel-Tests
(test_journal_title.py) schalten sie gezielt wieder ein.
"""
import pytest

import bridge
import pairing


@pytest.fixture(autouse=True)
def no_session_title_lookup(monkeypatch):
    monkeypatch.setattr(bridge, "lookup_session_title", lambda hermes_session_id: None)


@pytest.fixture(autouse=True)
def isolated_pairing_state(monkeypatch, tmp_path):
    """Never touch a real pairing state dir; this one does not exist unless a test creates it."""
    monkeypatch.setattr(bridge, "PAIRING", pairing.PairingStore(str(tmp_path / "pairing-state")))


@pytest.fixture(autouse=True)
def isolated_bridge_state(monkeypatch, tmp_path):
    """Sitzungsbuch und gelernte Hermes-Optionen nie im echten Zustandsordner. `hermes --version` laeuft ueber
    denselben Prozess-Runner wie die Chat-Fakes; standardmaessig gibt es keine Version (also nichts zu merken), die
    Tests dafuer (test_session_ledger.py) setzen sie selbst."""
    state_dir = str(tmp_path / "bridge-state")
    monkeypatch.setattr(bridge, "BRIDGE_STATE_DIR", state_dir)
    monkeypatch.setattr(bridge, "SESSION_LEDGER", bridge.SessionLedger(state_dir))
    monkeypatch.setattr(bridge, "hermes_version", lambda: None)


@pytest.fixture(autouse=True)
def isolated_hermes_home(monkeypatch, tmp_path):
    """Die Bridge prueft vor jedem Hermes-Lauf die config.yaml des Profils unter HOME; nie die des
    Entwicklers. Tests, die ein Profil brauchen, setzen HOME selbst."""
    monkeypatch.setattr(bridge, "HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setattr(bridge, "HERMES_DATA_DIR", "")
