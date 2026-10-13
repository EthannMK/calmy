"""Train the delay model that sits behind the forecast.

Data: nycflights13 (CC0), 336,776 real departures from New York in 2013 with the weather at the
airport for each hour. Target: the flight left 30 or more minutes late. The model learns how time of
day, day of week, trip distance and weather change that chance.

The split is by date, not random: it trains on January to September and is tested on October to
December, so the score reflects flights the model has never seen from a later period.

Run:  pip download nycflights13 --no-deps -d /tmp/nyc && tar xzf /tmp/nyc/nycflights13-*.tar.gz -C /tmp/nyc
      python tools/train_model.py /tmp/nyc/nycflights13-0.0.3/nycflights13/data
Writes app/model/delay_model.joblib and app/model/model_card.json.
"""
import json
import pathlib
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "model"
# Month is left out on purpose: the data covers one year, so the model would learn that year's seasons, not a pattern.
FEATURES = ["hour", "weekday", "distance_km", "wind_kmh", "visibility_km", "precip_mm", "temp_c"]


def build(src: pathlib.Path) -> pd.DataFrame:
    flights = pd.read_csv(src / "flights.csv.zip")
    weather = pd.read_csv(src / "weather.csv")
    f = flights.dropna(subset=["dep_delay", "hour"]).copy()
    w = weather[["origin", "year", "month", "day", "hour", "temp", "wind_speed", "visib", "precip"]]
    f = f.merge(w, on=["origin", "year", "month", "day", "hour"], how="left")
    f["date"] = pd.to_datetime(f[["year", "month", "day"]])
    f["weekday"] = f["date"].dt.weekday
    f["distance_km"] = f["distance"] * 1.609
    f["wind_kmh"] = f["wind_speed"] * 1.609          # source is in miles per hour
    f["visibility_km"] = f["visib"] * 1.609          # source is in miles
    f["precip_mm"] = f["precip"] * 25.4              # source is in inches per hour
    f["temp_c"] = (f["temp"] - 32) * 5 / 9
    f["y"] = (f["dep_delay"] >= 30).astype(int)
    return f


def main() -> None:
    d = build(pathlib.Path(sys.argv[1]))
    train, test = d[d.month <= 9], d[d.month >= 10]
    m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=400, l2_regularization=5.0, random_state=7)
    m.fit(train[FEATURES], train["y"])
    p = m.predict_proba(test[FEATURES])[:, 1]
    base = float(train["y"].mean())
    # Reliability by band: when the model says X%, how often was the flight really late.
    bands = [(0, .05), (.05, .1), (.1, .2), (.2, .3), (.3, .5), (.5, 1.01)]
    rel = []
    for lo, hi in bands:
        s = (p >= lo) & (p < hi)
        if s.sum():
            rel.append({"band": f"{int(lo*100)}-{min(100,int(hi*100))}%", "flights": int(s.sum()),
                        "predicted": round(float(p[s].mean()) * 100, 1), "actual": round(float(test["y"][s].mean()) * 100, 1)})
    # Share of all late flights found among the 20% of flights with the highest score.
    top = np.argsort(-p)[: len(p) // 5]
    card = {
        "model": "Gradient boosted trees (scikit-learn HistGradientBoosting)",
        "data": "nycflights13, 336,776 real departures from New York in 2013 (CC0 licence), joined to hourly airport weather",
        "target": "Departure delayed 30 minutes or more",
        "features": FEATURES,
        "train_rows": int(len(train)), "test_rows": int(len(test)),
        "split": "Trained on January to September, tested on October to December",
        "base_rate_pct": round(base * 100, 1),
        "auc": round(float(roc_auc_score(test["y"], p)), 3),
        "brier": round(float(brier_score_loss(test["y"], p)), 4),
        "brier_always_base_rate": round(float(brier_score_loss(test["y"], np.full(len(p), base))), 4),
        "late_flights_caught_in_top_20pct": round(float(test["y"].to_numpy()[top].sum() / test["y"].sum()) * 100, 1),
        "reliability": rel,
        "limits": ["Only one year of data, so it has no season feature.","Trained on one US airport region in 2013, not on Singapore or Southeast Asian flights.",
                   "It does not know the airline, the route or the aircraft. Those come from our own saved flight history.",
                   "Live delay, live weather and insurer airspace alerts are added after the model, as separate readable drivers.",
                   "The model informs. It never decides a payout."],
    }
    OUT.mkdir(exist_ok=True)
    joblib.dump({"model": m, "features": FEATURES}, OUT / "delay_model.joblib", compress=3)
    (OUT / "model_card.json").write_text(json.dumps(card, indent=1))
    print(json.dumps({k: v for k, v in card.items() if k not in ("limits",)}, indent=1))


if __name__ == "__main__":
    main()
