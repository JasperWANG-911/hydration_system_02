import json
import time
import os

os.makedirs("sample_records", exist_ok=True)

records = [
    {
        "record_type": "drink",
        "bed_id": "ward-4-bed-7",
        "timestamp": time.time(),
        "payload": {
            "volume_ml": 70.0,
            "confidence": 0.88,
            "raw_net_change_g": -70.0
        }
    },
    {
        "record_type": "observation",
        "bed_id": "ward-4-bed-7",
        "timestamp": time.time(),
        "payload": {
            "note": "Patient declined water",
            "acknowledged": False
        }
    },
    {
        "record_type": "session_summary",
        "bed_id": "ward-4-bed-7",
        "timestamp": time.time(),
        "payload": {
            "session_state": "ended",
            "total_consumed_ml": 70.0,
            "drink_count": 1,
            "refill_count": 0,
            "duration_s": 4.52
        }
    }
]

path = "sample_records/ward-4-bed-7.jsonl"
with open(path, "w") as f:
    for r in records:
        f.write(json.dumps(r) + "\n")

with open(path) as f:
    for line in f:
        print(json.dumps(json.loads(line), indent=2))
        print()