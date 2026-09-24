"""Capability-Vertrag 0.10 (Spec E S5, Spec C §4.11/§5.10).

`contract/app-required-capabilities.json` ist genau die Liste, die die App ab 0.10 verlangt
(`CoachClient.requiredPrivacyCapabilities`); das App-Repo haelt eine bytegleiche Kopie. Eine
vollstaendig eingerichtete Bridge muss jeden Wert melden.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge

CONTRACT = Path(__file__).resolve().parent.parent / "contract" / "app-required-capabilities.json"


def contract_values() -> list:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


class CapabilityContractTests(unittest.TestCase):
    def test_contract_is_a_sorted_unique_list_of_the_twelve_0_10_values(self):
        values = contract_values()
        self.assertEqual(values, sorted(set(values)))
        self.assertEqual(len(values), 12)
        for required in (
            "detached_turns_v1",
            "conversation_sessions_v2",
            "session_forget_v1",
            "app_chat_toolsets_v1",
            "stdin_prompt_transport_v1",
        ):
            self.assertIn(required, values)
        # Die Bridge nutzt kein --safe-mode mehr; ein Schein-Flag gibt es nicht (Spec C A7).
        self.assertNotIn("safe_mode_without_health_v1", values)

    def test_contract_file_is_stable_json_with_a_trailing_newline(self):
        raw = CONTRACT.read_text(encoding="utf-8")
        self.assertEqual(raw, json.dumps(contract_values(), indent=2) + "\n")

    def test_a_configured_bridge_reports_every_contract_value(self):
        with (
            patch.object(bridge, "PLAN_ORIGIN_SECRET", "s" * 48),
            patch.object(bridge, "PLAN_ORIGIN_PRINCIPAL", "kore"),
            patch.object(bridge, "ALLOW_ARGV_PROMPTS", False),
            patch.object(bridge, "PROMPT_TRANSPORT", "stdin"),
        ):
            capabilities = set(bridge.capabilities_payload()["capabilities"])
        missing = sorted(set(contract_values()) - capabilities)
        self.assertEqual(missing, [])

    def test_without_origin_secret_the_bridge_does_not_claim_plan_origin(self):
        with (
            patch.object(bridge, "PLAN_ORIGIN_SECRET", ""),
            patch.object(bridge, "PLAN_ORIGIN_PRINCIPAL", ""),
        ):
            capabilities = set(bridge.capabilities_payload()["capabilities"])
        self.assertNotIn("plan_origin_v1", capabilities)

    def test_health_reports_session_retention_days(self):
        payload = bridge.health_payload()
        self.assertEqual(payload["session_retention_days"], bridge.SESSION_RETENTION_DAYS)
        self.assertEqual(payload["session_retention_days"], 30)


class RetentionConfigurationTests(unittest.TestCase):
    def test_default_and_upper_limit_are_30_days(self):
        self.assertEqual(bridge.session_retention_days({}), 30)
        self.assertEqual(bridge.session_retention_days({"KORE_BRIDGE_SESSION_RETENTION_DAYS": ""}), 30)
        self.assertEqual(bridge.session_retention_days({"KORE_BRIDGE_SESSION_RETENTION_DAYS": "7"}), 7)
        self.assertEqual(bridge.session_retention_days({"KORE_BRIDGE_SESSION_RETENTION_DAYS": "90"}), 30)

    def test_invalid_retention_is_a_startup_error(self):
        for raw in ("0", "-3", "dreissig", "1.5"):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                bridge.session_retention_days({"KORE_BRIDGE_SESSION_RETENTION_DAYS": raw})


if __name__ == "__main__":
    unittest.main()
