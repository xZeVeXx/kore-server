"""Optionaler Anzeigename des Coaches (KORE_COACH_NAME): nur ein gueltiger Wert erscheint in der API."""
import unittest
from unittest import mock

import bridge


class CoachNameValidationTests(unittest.TestCase):
    def test_accepts_plain_names(self):
        for raw in ("Peter", "Coach Lena", "Zoë", "Jean-Luc", "O'Brien", "Dr. Fit", "Coach 2", "a", "A" * 24):
            with self.subTest(raw=raw):
                self.assertEqual(bridge.valid_coach_name(raw), raw)

    def test_decomposed_accents_are_composed(self):
        self.assertEqual(bridge.valid_coach_name("Zoë"), "Zoë")

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(bridge.valid_coach_name("  Peter \n"), "Peter")

    def test_rejects_everything_else(self):
        for raw in (
            "", "   ", "<script>", "a" * 25, "http://x", "Peter\nPause", "Peter\tX", "Co/ach",
            "Peter!", "a_b", "Name; rm", "😀", "Peter@home", '"Peter"', "a​b",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(bridge.valid_coach_name(raw))

    def test_none_is_not_a_name(self):
        self.assertIsNone(bridge.valid_coach_name(None))


class CoachNamePayloadTests(unittest.TestCase):
    def test_valid_name_is_in_capabilities_and_health_but_not_in_the_capability_list(self):
        with mock.patch.object(bridge, "COACH_NAME", "Coach Lena"):
            caps = bridge.capabilities_payload()
            health = bridge.health_payload()
        self.assertEqual(caps["coach_name"], "Coach Lena")
        self.assertEqual(health["coach_name"], "Coach Lena")
        self.assertFalse([c for c in caps["capabilities"] if "coach_name" in c])

    def test_without_name_the_field_is_omitted(self):
        with mock.patch.object(bridge, "COACH_NAME", None):
            self.assertNotIn("coach_name", bridge.capabilities_payload())
            self.assertNotIn("coach_name", bridge.health_payload())

    def test_capability_list_is_identical_with_and_without_name(self):
        with mock.patch.object(bridge, "COACH_NAME", None):
            without = bridge.capabilities_payload()["capabilities"]
        with mock.patch.object(bridge, "COACH_NAME", "Peter"):
            with_name = bridge.capabilities_payload()["capabilities"]
        self.assertEqual(without, with_name)


class CoachNameStartupTests(unittest.TestCase):
    def test_unset_is_silent(self):
        self.assertEqual(bridge.coach_name_from_env({}), (None, None))

    def test_valid_value_is_used_without_warning(self):
        self.assertEqual(bridge.coach_name_from_env({"KORE_COACH_NAME": "Peter"}), ("Peter", None))

    def test_invalid_value_is_ignored_with_one_warning_that_does_not_echo_it(self):
        name, warning = bridge.coach_name_from_env({"KORE_COACH_NAME": "<script>"})
        self.assertIsNone(name)
        self.assertIn("KORE_COACH_NAME", warning)
        self.assertNotIn("<script>", warning)
        self.assertNotIn("\n", warning)

    def test_empty_value_is_treated_as_unset(self):
        # `KORE_COACH_NAME=` in an env file means "not set"; no warning for that.
        self.assertEqual(bridge.coach_name_from_env({"KORE_COACH_NAME": ""}), (None, None))
