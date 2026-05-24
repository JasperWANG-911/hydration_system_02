import sys
sys.path.insert(0, '.')

import tempfile
import json
from config import SystemConfig
from patient_profile import BedProfile
from sensor_reader import MockSensorReader
from led_controller import MockLedController
from observation_button import MockObservationButton
from hydration_record import JsonLinesHydrationRecord
from pipeline import Pipeline

config = SystemConfig()
config.alert.quiet_hours_start = None
config.alert.quiet_hours_end = None

bed = BedProfile(bed_id="ward-4-bed-7", daily_goal_ml=2000.0)

record = JsonLinesHydrationRecord("sample_records/")

pipeline = Pipeline(
    config=config,
    bed=bed,
    sensor=MockSensorReader(config),
    led=MockLedController(config),
    button=MockObservationButton(config),
    record=record,
)

pipeline.sensor.push([450.0]*40 + [0.0]*10 + [380.0]*40)
pipeline.button.press(note="Patient declined water")
pipeline.run(max_ticks=90)

# Pretty print the contents
with open("sample_records/ward-4-bed-7.jsonl") as f:
    for line in f:
        print(json.dumps(json.loads(line), indent=2))