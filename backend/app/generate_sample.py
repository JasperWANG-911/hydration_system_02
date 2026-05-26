"""Generate synthetic DRIP hydration records for testing and demos.

Produces a JSONL file of drink events simulating a realistic ward day:
heavier intake at mealtimes (breakfast, lunch, dinner), light intake
between meals, and a nap pause in the afternoon.

Output format: one JSON record per line, matching the schema written by
:class:`hydration_record.JsonLinesHydrationRecord`.

Usage::

    python backend/app/generate_sample.py
    python backend/app/generate_sample.py --out records/ward-4-bed-7.jsonl
"""

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Meal windows: (start_hour, end_hour, mean_presses, std_presses)
_MEAL_WINDOWS = [
    (8,  9,  5, 1),   # breakfast
    (12, 13, 6, 2),   # lunch
    (17, 18, 6, 2),   # dinner
    (20, 21, 3, 1),   # evening
]
_BETWEEN_MEAL_INTERVAL_MIN = 90
_BETWEEN_MEAL_PRESSES = 2

STEP_ML = 50
BED_ID = "ward-4-bed-7"
DAILY_GOAL_ML = 2000


def _ts(dt: datetime) -> float:
    return dt.timestamp()


def _drink_record(bed_id: str, volume_ml: float, ts: float) -> dict:
    return {
        "record_type": "drink",
        "bed_id": bed_id,
        "timestamp": ts,
        "payload": {"volume_ml": volume_ml},
    }


def _sleep_record(bed_id: str, sleeping: bool, ts: float) -> dict:
    return {
        "record_type": "sleep",
        "bed_id": bed_id,
        "timestamp": ts,
        "payload": {"sleeping": sleeping},
    }


def _summary_record(bed_id: str, total_ml: float, drink_count: int, ts: float) -> dict:
    return {
        "record_type": "session_summary",
        "bed_id": bed_id,
        "timestamp": ts,
        "payload": {
            "session_state": "ended",
            "total_consumed_ml": total_ml,
            "drink_count": drink_count,
            "duration_s": 57600.0,
        },
    }


def generate(bed_id: str = BED_ID, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    records = []

    today = datetime.now(timezone.utc).replace(hour=7, minute=0, second=0, microsecond=0)
    total_ml = 0.0
    drink_count = 0

    for start_h, end_h, mean_p, std_p in _MEAL_WINDOWS:
        window_start = today + timedelta(hours=start_h)
        window_end   = today + timedelta(hours=end_h)
        n_events = rng.randint(2, 4)
        interval = (window_end - window_start) / n_events
        for i in range(n_events):
            ts = _ts(window_start + interval * i + timedelta(minutes=rng.uniform(0, 10)))
            presses = max(1, int(rng.gauss(mean_p, std_p)))
            volume_ml = presses * STEP_ML
            records.append(_drink_record(bed_id, float(volume_ml), ts))
            total_ml += volume_ml
            drink_count += 1

    meal_hour_ranges = {(s, e) for s, e, _, _ in _MEAL_WINDOWS}
    t = today + timedelta(hours=9)
    end_of_day = today + timedelta(hours=22)
    while t < end_of_day:
        h = t.hour
        in_meal = any(s <= h < e for s, e in meal_hour_ranges)
        if not in_meal:
            ts = _ts(t + timedelta(minutes=rng.uniform(-10, 10)))
            volume_ml = _BETWEEN_MEAL_PRESSES * STEP_ML
            records.append(_drink_record(bed_id, float(volume_ml), ts))
            total_ml += volume_ml
            drink_count += 1
        t += timedelta(minutes=_BETWEEN_MEAL_INTERVAL_MIN)

    nap_start = today + timedelta(hours=13, minutes=30)
    nap_end   = today + timedelta(hours=15)
    records.append(_sleep_record(bed_id, sleeping=True,  ts=_ts(nap_start)))
    records.append(_sleep_record(bed_id, sleeping=False, ts=_ts(nap_end)))

    day_end = today + timedelta(hours=23)
    records.append(_summary_record(bed_id, total_ml, drink_count, _ts(day_end)))

    records.sort(key=lambda r: r["timestamp"])
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic DRIP sample data.")
    parser.add_argument("--out", default="records/sample.jsonl", help="Output JSONL path")
    parser.add_argument("--bed", default=BED_ID, help="Bed ID")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records = generate(bed_id=args.bed, seed=args.seed)
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    print(f"wrote {len(records)} records to {out_path}")
    total = sum(
        r["payload"]["volume_ml"]
        for r in records
        if r["record_type"] == "drink"
    )
    print(f"total intake: {total:.0f} ml  (goal: {DAILY_GOAL_ML} ml)")


if __name__ == "__main__":
    main()
