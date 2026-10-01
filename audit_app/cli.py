"""Command-line operations; run through main.py or python -m audit_app."""
import argparse
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from .bridge import BridgeClient
from .config import load_config
from .job import run_job
from .runtime import serve


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
    parser.add_argument("command", choices=("inspect-bridge", "backfill-listing-boards", "backfill-office-addresses", "run", "serve", "simulate", "backup", "restore"))
    parser.add_argument("--output", help="New backup destination")
    parser.add_argument("--input", help="Backup to restore while the service is stopped")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    load_dotenv()
    config = load_config()
    if config.env == "development" and args.command in {"backup", "restore", "backfill-listing-boards", "backfill-office-addresses", "inspect-bridge"}:
        parser.error("Development uses disposable synthetic data; live intake and backup/restore commands are unavailable")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    if args.command == "backup":
        from .backup import backup
        if not args.output:
            parser.error("backup requires --output")
        print(backup(config, args.output))
    elif args.command == "restore":
        from .backup import restore
        if not args.input:
            parser.error("restore requires --input")
        print(restore(config, args.input, args.confirm))
    elif args.command == "inspect-bridge":
        metadata = BridgeClient(config).inspect_metadata()
        report = {resource: {name: {"field": field, "type": metadata[resource][field]} for name, field in mapping.items()} for resource, mapping in config.field_map.items()}
        print(json.dumps(report, indent=2))
    elif args.command == 'backfill-listing-boards':
        from .board_scope import backfill_listing_boards
        print(json.dumps(backfill_listing_boards(config)))
    elif args.command == "backfill-office-addresses":
        from .office_backfill import backfill_office_addresses
        print(json.dumps(backfill_office_addresses(config)))
    elif args.command == "run":
        print(json.dumps(run_job(config)))
    elif args.command == "simulate":
        from .simulation import simulate_cycle
        print(json.dumps(simulate_cycle(config), indent=2))
    else:
        serve(config)

