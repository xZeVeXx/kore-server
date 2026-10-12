from __future__ import annotations

import sys
import unittest
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from snapshot_contract import validate_recovery_hrv_contract  # noqa: E402


class RecoveryHRVContractTests(unittest.TestCase):
    def test_valid_method_specific_and_legacy_payloads_are_not_normalized(self) -> None:
        payloads = [
            {
                "hrv_rmssd_ms": 54.0,
                "hrv_baseline_ms": 49.0,
                "hrv_method_effective": "rmssd",
                "hrv_kind": "rmssd",
                "hrv_source": "nightHeartbeatSeriesRMSSD",
                "hrv_quality": "good",
                "hrv_sample_count": 18,
                "hrv_contributed_to_recovery": True,
            },
            {
                "hrv_sdnn_ms": 47.0,
                "hrv_method_effective": "sdnnFallbackFromRMSSD",
                "hrv_kind": "sdnn",
                "hrv_source": "sleepHealthKitSDNN",
                "hrv_quality": "fallback",
                "hrv_sample_count": 0,
                "hrv_contributed_to_recovery": False,
                "hrv_fallback_reason": (
                    "Keine gültigen rMSSD-Beat-Serien im Schlafzeitraum — "
                    "Apple-SDNN aus dem Schlaf genutzt."
                ),
            },
            {
                "hrv_ms": 42.0,
                "hrv_source": "metric_tile_fallback",
            },
            # 0.13.1: Apples Erholungs-HRV, eigene Skala im neutralen hrv_ms.
            {
                "hrv_ms": 61.0,
                "hrv_method_effective": "appleRMSSD",
                "hrv_kind": "appleRMSSD",
                "hrv_source": "appleRecoveryRMSSD",
                "hrv_contributed_to_recovery": True,
            },
            {
                "hrv_ms": 60.0,
                "hrv_method_effective": "appleRMSSD",
                "hrv_kind": "appleRMSSD",
                "hrv_source": "metric_tile_fallback",
            },
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertIs(validate_recovery_hrv_contract(payload), payload)

    def test_multiple_value_fields_are_rejected_even_when_equal(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most one"):
            validate_recovery_hrv_contract(
                {"hrv_rmssd_ms": 42.0, "hrv_sdnn_ms": 42.0}
            )

    def test_conflicting_key_kind_method_and_source_are_rejected(self) -> None:
        invalid = [
            {"hrv_rmssd_ms": 42.0, "hrv_kind": "sdnn"},
            {"hrv_sdnn_ms": 42.0, "hrv_method_effective": "rmssd"},
            {"hrv_ms": 42.0, "hrv_kind": "rmssd", "hrv_method_effective": "sdnn"},
            {"hrv_rmssd_ms": 42.0, "hrv_source": "sleepHealthKitSDNN"},
            {"hrv_sdnn_ms": 42.0, "hrv_source": "nightHeartbeatSeriesRMSSD"},
            {"hrv_sdnn_ms": 42.0, "hrv_source": "metric_tile_fallback"},
            # Apples Skala nie in Kores rMSSD-/SDNN-Feldern oder mit deren Herkunft gemischt.
            {"hrv_rmssd_ms": 42.0, "hrv_kind": "appleRMSSD"},
            {"hrv_sdnn_ms": 42.0, "hrv_method_effective": "appleRMSSD"},
            {"hrv_rmssd_ms": 42.0, "hrv_source": "appleRecoveryRMSSD"},
            {"hrv_ms": 42.0, "hrv_kind": "rmssd", "hrv_source": "appleRecoveryRMSSD"},
            {"hrv_ms": 42.0, "hrv_kind": "sdnn", "hrv_method_effective": "appleRMSSD"},
            {"hrv_ms": 42.0, "hrv_kind": "appleRMSSD", "hrv_method_effective": "rmssd"},
            {"hrv_ms": 42.0, "hrv_kind": "appleRMSSD", "hrv_source": "nightHeartbeatSeriesRMSSD"},
            {"hrv_ms": 42.0, "hrv_method_effective": "appleRMSSD", "hrv_source": "sleepHealthKitSDNN"},
        ]

        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_recovery_hrv_contract(payload)

    def test_true_contribution_without_current_value_is_rejected(self) -> None:
        for payload in (
            {"hrv_kind": "rmssd", "hrv_contributed_to_recovery": True},
            {
                "hrv_rmssd_ms": None,
                "hrv_kind": "rmssd",
                "hrv_contributed_to_recovery": True,
            },
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                ValueError,
                "current HRV value",
            ):
                validate_recovery_hrv_contract(payload)


if __name__ == "__main__":
    unittest.main()
