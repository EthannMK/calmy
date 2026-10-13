"""The forecast uses the trained model, reacts to its inputs, and survives without it."""
import os
import tempfile

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "m.db")
os.environ["KODA_NO_WEATHER"] = "1"

from app import forecast  # noqa: E402

F = {"code": "TG 100", "origin": "BKK", "dest": "HKT", "scheduled": "14:00", "flight_date": "2026-10-14"}


def test_model_loads_and_card_has_honest_limits():
    fc = forecast.compute(F)
    assert fc["source"].startswith("Trained model")
    card = forecast.card()
    assert card["auc"] > 0.6 and card["test_rows"] > 50000 and card["limits"]


def test_chance_moves_with_the_hour():
    a = forecast.compute({**F, "scheduled": "07:00"})["probability"]
    b = forecast.compute({**F, "scheduled": "20:00"})["probability"]
    assert b > a


def test_live_delay_adds_a_driver():
    base = forecast.compute(F)["probability"]
    late = forecast.compute({**F, "delay_minutes": 90})
    assert late["probability"] > base and any("Already delayed" in d[0] for d in late["drivers"])


def test_falls_back_to_a_rule_without_the_model(monkeypatch):
    monkeypatch.setitem(forecast._m, "m", None)
    forecast._base.cache_clear()
    fc = forecast.compute(F)
    assert fc["source"] == "Rule fallback" and 3 <= fc["probability"] <= 97
    forecast._m.pop("m")
    forecast._base.cache_clear()
