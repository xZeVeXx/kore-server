"""Dependency-free cross-field validation for the recovery snapshot HRV contract."""

from __future__ import annotations

from typing import Any


HRV_VALUE_KEYS = ("hrv_rmssd_ms", "hrv_sdnn_ms", "hrv_ms")
# Apples Erholungs-HRV: eigene Skala, nie mit Kores rMSSD/SDNN gemischt, nur im neutralen hrv_ms.
APPLE_RMSSD = "appleRMSSD"


def validate_recovery_hrv_contract(value: Any) -> Any:
    """Reject ambiguous HRV semantics without normalizing or inventing fields."""
    if not isinstance(value, dict):
        return value

    present_value_keys = [key for key in HRV_VALUE_KEYS if key in value]
    if len(present_value_keys) > 1:
        raise ValueError("recovery must contain at most one HRV value field")

    value_key = present_value_keys[0] if present_value_keys else None
    kind = value.get("hrv_kind")
    method = value.get("hrv_method_effective")
    source = value.get("hrv_source")

    if value_key == "hrv_rmssd_ms":
        if kind not in (None, "rmssd") or method not in (None, "rmssd"):
            raise ValueError("hrv_rmssd_ms conflicts with HRV provenance")
    elif value_key == "hrv_sdnn_ms":
        if kind not in (None, "sdnn") or method not in (
            None,
            "sdnn",
            "sdnnFallbackFromRMSSD",
        ):
            raise ValueError("hrv_sdnn_ms conflicts with HRV provenance")

    if method == "rmssd" and kind not in (None, "rmssd"):
        raise ValueError("rMSSD method conflicts with HRV kind")
    if method in ("sdnn", "sdnnFallbackFromRMSSD") and kind not in (None, "sdnn"):
        raise ValueError("SDNN method conflicts with HRV kind")
    if method == APPLE_RMSSD and kind not in (None, APPLE_RMSSD):
        raise ValueError("Apple recovery HRV method conflicts with HRV kind")

    if source == "nightHeartbeatSeriesRMSSD":
        if value_key == "hrv_sdnn_ms" or kind in ("sdnn", APPLE_RMSSD) or method in (
            "sdnn",
            "sdnnFallbackFromRMSSD",
            APPLE_RMSSD,
        ):
            raise ValueError("rMSSD source conflicts with HRV semantics")
    elif source == "sleepHealthKitSDNN":
        if value_key == "hrv_rmssd_ms" or kind in ("rmssd", APPLE_RMSSD) or method in (
            "rmssd",
            APPLE_RMSSD,
        ):
            raise ValueError("SDNN source conflicts with HRV semantics")
    elif source == "appleRecoveryRMSSD":
        if value_key not in (None, "hrv_ms") or kind not in (None, APPLE_RMSSD) or method not in (
            None,
            APPLE_RMSSD,
        ):
            raise ValueError("Apple recovery HRV source conflicts with HRV semantics")
    elif source == "metric_tile_fallback" and value_key != "hrv_ms":
        raise ValueError("metric fallback requires the method-neutral hrv_ms field")

    if value.get("hrv_contributed_to_recovery") is True and (
        value_key is None or value.get(value_key) is None
    ):
        raise ValueError("contributing HRV provenance requires a current HRV value")

    return value
