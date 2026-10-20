import json
from datetime import datetime, timezone


class EventLog:
    def __init__(self, path):
        self.file = open(path, "a", encoding="utf-8", buffering=1)

    def record(self, event, **details):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        self.file.write(json.dumps(entry) + "\n")

    def close(self):
        self.file.close()
