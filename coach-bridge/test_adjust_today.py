"""Tages-Anpassung: „nur reduzieren" setzt die Bridge selbst durch (0.13 S11a).

Bis 0.13 stand die Regel nur im Prompt; eine Antwort mit mehr Sätzen, mehr Last oder einer neuen
Übung ging unverändert an die App. Jetzt vergleicht die Bridge die Antwort mit der Session aus der
Anfrage und antwortet bei jeder Steigerung mit 502 und einem festen Satz.
"""
import copy
import json
import unittest
from unittest.mock import patch

import bridge
from test_runner_stream_json import TEST_TOKEN, hermes_process, reply_stream

SESSION_ID = "123e4567-e89b-42d3-a456-426614174000"
BENCH_ID = "123e4567-e89b-42d3-a456-426614174001"
ROW_ID = "123e4567-e89b-42d3-a456-426614174002"
OTHER_ID = "123e4567-e89b-42d3-a456-426614174003"

ORIGINAL = {
    "session_id": SESSION_ID,
    "name": "Push A",
    "exercises": [
        {
            "exercise_id": BENCH_ID,
            "name": "Bankdrücken",
            "note": "",
            "sets": [
                {"weight_kg": 80, "reps": 8},
                {"weight_kg": 80, "reps": 8},
                {"weight_kg": 85, "reps": 6},
            ],
        },
        {
            "exercise_id": ROW_ID,
            "name": "Rudern",
            "note": "",
            "sets": [
                {"weight_kg": 60, "reps": 8, "reps_max": 12},
                {"weight_kg": 60, "reps": 8, "reps_max": 12},
            ],
        },
    ],
}


def adjusted(change=None):
    session = copy.deepcopy(ORIGINAL)
    if change is not None:
        change(session)
    return session


def bench(session):
    return session["exercises"][0]


def row(session):
    return session["exercises"][1]


REDUCTIONS = {
    "unverändert": lambda s: None,
    "ein Satz weniger": lambda s: bench(s)["sets"].pop(),
    "leichter": lambda s: bench(s)["sets"][0].update(weight_kg=70),
    "weniger Wiederholungen": lambda s: bench(s)["sets"][1].update(reps=6),
    "Bereich bleibt": lambda s: row(s)["sets"].pop(),
    "Bereich enger": lambda s: row(s)["sets"][0].update(reps_max=10),
    "Bereich als feste Wiederholungen": lambda s: row(s)["sets"][0].pop("reps_max"),
    "Übung gestrichen": lambda s: s["exercises"].pop(),
    "Notiz geändert": lambda s: bench(s).update(note="heute ruhiger"),
    "Sessionname geändert": lambda s: s.update(name="Push A (leicht)"),
    "IDs weggelassen": lambda s: (s.pop("session_id"), bench(s).pop("exercise_id")),
    "Namensschreibweise": lambda s: bench(s).update(name=" bankdrücken "),
    "Gleitkomma-Last": lambda s: bench(s)["sets"][0].update(weight_kg=77.5),
}

ESCALATIONS = {
    "mehr Sätze": lambda s: bench(s)["sets"].append({"weight_kg": 80, "reps": 8}),
    "schwerer": lambda s: bench(s)["sets"][0].update(weight_kg=82.5),
    "schwerer im späteren Satz": lambda s: bench(s)["sets"][1].update(weight_kg=85.5),
    "mehr Wiederholungen": lambda s: bench(s)["sets"][2].update(reps=7),
    "reps_max höher": lambda s: row(s)["sets"][0].update(reps_max=15),
    "reps im Bereich höher": lambda s: row(s)["sets"][0].update(reps=9),
    "neuer Bereich": lambda s: bench(s)["sets"][0].update(reps_max=12),
    "neue Übung": lambda s: s["exercises"].append(
        {"name": "Dips", "note": "", "sets": [{"weight_kg": 0, "reps": 10}]}
    ),
    "neue Übung mit fremder ID": lambda s: s["exercises"].append(
        {"exercise_id": OTHER_ID, "name": "Dips", "note": "", "sets": [{"weight_kg": 0, "reps": 10}]}
    ),
    "Übung umbenannt": lambda s: bench(s).update(name="Schrägbankdrücken"),
    "exercise_id umbenannt": lambda s: bench(s).update(exercise_id=OTHER_ID),
    "exercise_id vertauscht": lambda s: (bench(s).update(exercise_id=ROW_ID),
                                          row(s).update(exercise_id=BENCH_ID)),
    "session_id umbenannt": lambda s: s.update(session_id=OTHER_ID),
    "neues Satzfeld": lambda s: bench(s)["sets"][0].update(duration_s=600),
    "neues Übungsfeld": lambda s: bench(s).update(rest_seconds=30),
}


class ReductionCheckTests(unittest.TestCase):
    def test_reductions_pass(self):
        for name, change in REDUCTIONS.items():
            with self.subTest(name):
                session = adjusted(change)
                self.assertTrue(bridge.validate_session(session), name)
                self.assertFalse(bridge.adjustment_escalates(ORIGINAL, session))

    def test_every_escalation_is_detected(self):
        for name, change in ESCALATIONS.items():
            with self.subTest(name):
                session = adjusted(change)
                self.assertTrue(bridge.adjustment_escalates(ORIGINAL, session))

    def test_durations_and_distances_may_only_sink(self):
        original = {"name": "Lauf", "exercises": [{"name": "Intervalle", "sets": [
            {"weight_kg": 0, "reps": 1, "duration_s": 600, "distance_m": 2000},
        ]}]}
        for values, escalates in (
            ({"duration_s": 600, "distance_m": 2000}, False),
            ({"duration_s": 480, "distance_m": 1500}, False),
            ({"duration_s": 660, "distance_m": 2000}, True),
            ({"duration_s": 600, "distance_m": 2400}, True),
            ({"duration_s": "600", "distance_m": 2000}, True),
            ({"duration_s": True, "distance_m": 2000}, True),
        ):
            with self.subTest(values=values):
                session = copy.deepcopy(original)
                session["exercises"][0]["sets"][0].update(values)
                self.assertEqual(bridge.adjustment_escalates(original, session), escalates)


class AdjustTodayEndpointTests(unittest.TestCase):
    def setUp(self):
        for name, value in (
            ("ALLOW_ARGV_PROMPTS", False),
            ("PROMPT_TRANSPORT", "stdin"),
            ("PROFILE", "kore-coach"),
            ("PLAN_ORIGIN_SECRET", ""),
            ("PLAN_ORIGIN_PRINCIPAL", ""),
            ("TOKEN", TEST_TOKEN),
            ("HERMES", "/opt/hermes/bin/hermes"),
        ):
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("bridge._run_process_bounded")
        self.run_hermes = patcher.start()
        self.addCleanup(patcher.stop)

    def adjust(self, answer):
        self.run_hermes.side_effect = hermes_process(reply_stream(json.dumps(answer)))
        return bridge.adjust_today({
            "session": ORIGINAL,
            "health_context_allowed": True,
            "consent_scope_version": bridge.CONSENT_SCOPE_VERSION,
            "context": {"readiness": 40},
        })

    def test_a_reduction_reaches_the_app(self):
        session = adjusted(REDUCTIONS["ein Satz weniger"])
        code, payload = self.adjust({"session": session, "rationale": "kurz", "changes": ["−1 Satz"]})
        self.assertEqual(code, 200)
        self.assertEqual(payload["session"], session)

    def test_every_escalation_attempt_is_a_502_with_a_fixed_sentence(self):
        for name, change in ESCALATIONS.items():
            with self.subTest(name):
                code, payload = self.adjust({"session": adjusted(change), "rationale": "mehr geht",
                                             "changes": []})
                self.assertEqual(code, 502)
                self.assertEqual(payload, {"error": bridge.ADJUST_ESCALATION_ERROR})
                self.assertNotIn("session", payload)


if __name__ == "__main__":
    unittest.main()
