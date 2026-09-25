import argparse
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from audit_app.bridge import BridgeClient
from audit_app.config import load_config
from audit_app.job import run_job
from audit_app.web import serve


def load_dotenv():
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": record.levelname,
                "logger": record.name, "event": record.getMessage()}
        for key in ("run_id", "audit_id", "status", "fetched", "new", "selected"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exception"] = record.exc_info[0].__name__
        return json.dumps(data)


def main():
    parser = argparse.ArgumentParser(description="MLS Audit Desk")
    parser.add_argument("command", choices=("inspect-bridge", "run", "serve", "simulate"))
    args = parser.parse_args()
    load_dotenv()
    config = load_config()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    if args.command == "inspect-bridge":
        metadata = BridgeClient(config).inspect_metadata()
        report = {resource: {name: {"field": field, "type": metadata[resource][field]} for name, field in mapping.items()} for resource, mapping in config.field_map.items()}
        print(json.dumps(report, indent=2))
    elif args.command == "run":
        print(json.dumps(run_job(config)))
    elif args.command == "simulate":
        from audit_app.simulation import simulate_cycle
        print(json.dumps(simulate_cycle(config), indent=2))
    else:
        serve(config)


if __name__ == "__main__":
    main()
