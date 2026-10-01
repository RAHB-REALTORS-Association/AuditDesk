from .security import event
from filelock import FileLock
import json
import logging
import math
import random
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .bridge import BridgeClient
from .database import connect, init_db
from .emailer import EmailError, resolve_recipients, send_email, utcnow
from .settings import brokerage_cooldown_days, selection_percent, workflow_config


LOG = logging.getLogger("audit_app")
LISTING_COLUMNS = (
    "bridge_listing_id", "mls_number", "status", "entry_timestamp", "address", "agent_id",
    "agent_name", "agent_email", "brokerage_id", "brokerage_name", "brokerage_email", "brokerage_address",
    "broker_id", "broker_name", "broker_first_name", "broker_email", "first_processed_at", "processing_status", "originating_system_name",
)


@contextmanager
def job_lock(path):
    lock_path = str(Path(path).resolve()) + ".lock"
    with FileLock(lock_path):
        yield


def choose_fairly(candidates, history, config, rng=random, rate=None, office_cooldown_days=None):
    """Binomial rate target, then weighted brokerage lottery with cooldowns."""
    candidates = [row for row in candidates if row.get('originating_system_name') == 'Cornerstone']
    rate = config.rate if rate is None else rate
    office_cooldown_days = config.brokerage_cooldown_days if office_cooldown_days is None else office_cooldown_days
    target = sum(rng.random() < rate for _ in candidates)
    recent_offices = {r["brokerage_id"] for r in history if r["brokerage_id"] and r["office_recent"]}
    recent_brokers = {r["broker_id"] for r in history if r["broker_id"] and r["broker_recent"]}
    groups = {}
    for listing in candidates:
        office = listing["brokerage_id"] or "unknown:" + listing["bridge_listing_id"]
        if listing["brokerage_id"] in recent_offices or listing["broker_id"] in recent_brokers:
            continue
        groups.setdefault(office, []).append(listing)
    selected = []
    while groups and len(selected) < target:
        offices = list(groups)
        weights = [math.sqrt(len(groups[office])) for office in offices]
        office = rng.choices(offices, weights=weights, k=1)[0]
        listing = rng.choice(groups.pop(office))
        selected.append((listing, {
            "method": "binomial_target_weighted_brokerage_lottery",
            "rate": rate,
            "target": target,
            "eligible_brokerages_at_draw": len(offices),
            "brokerage_weight": "sqrt(new listings)",
            "brokerage_cooldown_days": office_cooldown_days,
            "broker_cooldown_days": config.broker_cooldown_days,
        }))
        if listing["broker_id"]:
            groups = {key: [item for item in rows if item["broker_id"] != listing["broker_id"]] for key, rows in groups.items()}
            groups = {key: rows for key, rows in groups.items() if rows}
    return selected


def _recent_history(db, config, now, office_cooldown_days=None):
    office_cooldown_days = config.brokerage_cooldown_days if office_cooldown_days is None else office_cooldown_days
    office_cutoff = (now - timedelta(days=office_cooldown_days)).isoformat(timespec="seconds")
    broker_cutoff = (now - timedelta(days=config.broker_cooldown_days)).isoformat(timespec="seconds")
    rows = db.execute("SELECT a.brokerage_id, a.broker_id, a.selected_at FROM audits a JOIN listings l ON l.id=a.listing_id WHERE l.originating_system_name='Cornerstone' AND (a.selected_at >= ? OR a.selected_at >= ?)", (office_cutoff, broker_cutoff)).fetchall()
    return [{"brokerage_id": row["brokerage_id"], "broker_id": row["broker_id"], "office_recent": row["selected_at"] >= office_cutoff, "broker_recent": row["selected_at"] >= broker_cutoff} for row in rows]


def _listing_dict(row):
    return dict(row)


def deliver_audit(config, audit_id, retry=False, sender=None):
    if config.env == "development" or not config.email_enabled or not config.test_window_open():
        LOG.info("test_window_closed", extra={"audit_id": audit_id})
        return False
    with connect(config.database_path) as db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        audit = db.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone()
        if audit:
            board = db.execute('SELECT originating_system_name FROM listings WHERE id=?', (audit['listing_id'],)).fetchone()
            if not board or board[0] != 'Cornerstone':
                LOG.warning('audit_delivery_blocked_by_board_scope audit_id=%s', audit_id)
                return False
        if not audit or audit["email_status"] != ("email_failed" if retry else "email_pending"):
            db.rollback()
            return False
        if audit["test_mode"] and not config.test_mode:
            db.execute("UPDATE audits SET email_status='email_blocked',last_error=? WHERE id=?",
                       ("A test audit cannot send a production request.", audit_id))
            db.execute("UPDATE listings SET processing_status='email_blocked' WHERE id=?", (audit["listing_id"],))
            event(db, "email.blocked", audit_id, "test-to-production")
            db.commit()
            return False
        listing = _listing_dict(db.execute("SELECT * FROM listings WHERE id=?", (audit["listing_id"],)).fetchone())
        intended_to = json.loads(audit["intended_to"])
        intended_cc = json.loads(audit["intended_cc"])
        actual = json.loads(audit["actual_recipients"])
        # Re-evaluate the current environment at send time. A mode change cannot reuse old recipients.
        try:
            intended_to, intended_cc, actual = resolve_recipients(listing, config)
        except EmailError as exc:
            db.execute("UPDATE audits SET email_status='email_failed', last_error=? WHERE id=?", (str(exc), audit_id))
            db.execute("UPDATE listings SET processing_status='email_failed' WHERE id=?", (audit["listing_id"],))
            db.commit()
            return False
        db.execute("UPDATE audits SET email_status='email_sending', intended_to=?, intended_cc=?, actual_recipients=?, test_mode=?, last_error=NULL WHERE id=?",
                   (json.dumps(intended_to), json.dumps(intended_cc), json.dumps(actual), int(config.test_mode), audit_id))
        db.execute("UPDATE listings SET processing_status='email_pending' WHERE id=?", (audit["listing_id"],))
        cursor = db.execute("INSERT INTO email_attempts(audit_id,attempted_at,status,actual_recipients) VALUES(?,?,?,?)",
                            (audit_id, utcnow(), "started", json.dumps(actual)))
        attempt_id = cursor.lastrowid
        event(db, "request_email.started", audit_id)
        db.commit()
    try:
        message_id = (sender or send_email)(config, listing, audit_id, intended_to, intended_cc, actual)
    except EmailError as exc:
        state = "email_unknown" if exc.uncertain else "email_failed"
        with connect(config.database_path) as db:
            db.execute("UPDATE audits SET email_status=?, last_error=? WHERE id=?", (state, str(exc), audit_id))
            db.execute("UPDATE listings SET processing_status=? WHERE id=?", (state, audit["listing_id"]))
            db.execute("UPDATE email_attempts SET completed_at=?, status=?, error=? WHERE id=?", (utcnow(), state, str(exc), attempt_id))
            db.commit()
        LOG.warning("email_attempt_failed", extra={"audit_id": audit_id, "status": state})
        return False
    with connect(config.database_path) as db:
        db.execute("UPDATE audits SET email_status='email_sent', email_sent_at=?, sendgrid_message_id=? WHERE id=?", (utcnow(), message_id, audit_id))
        db.execute("UPDATE listings SET processing_status='email_sent' WHERE id=?", (audit["listing_id"],))
        db.execute("UPDATE email_attempts SET completed_at=?, status='email_sent', sendgrid_message_id=? WHERE id=?", (utcnow(), message_id, attempt_id))
        db.commit()
    LOG.info("email_sent", extra={"audit_id": audit_id})
    return True


def run_job(config, client=None, rng=random, now=None, sender=None, only_if_needed=False):
    if config.env == "development":
        from .simulation import simulate_cycle
        return simulate_cycle(config)
    init_db(config.database_path)
    with job_lock(config.database_path):
        config = workflow_config(config)
        now = now or datetime.now(timezone.utc)
        if only_if_needed:
            local_now = now.astimezone(ZoneInfo(config.timezone))
            today_eight = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
            if local_now < today_eight:
                return {"fetched": 0, "new": 0, "selected": 0, "skipped": "before_daily_time"}
            tomorrow_eight = today_eight + timedelta(days=1)
            start_utc = today_eight.astimezone(timezone.utc).isoformat(timespec="seconds")
            end_utc = tomorrow_eight.astimezone(timezone.utc).isoformat(timespec="seconds")
            with connect(config.database_path) as db:
                already = db.execute("""SELECT 1 FROM runs WHERE started_at>=? AND started_at<?
                    AND status IN ('completed','completed_with_email_errors') LIMIT 1""", (start_utc, end_utc)).fetchone()
            if already:
                return {"fetched": 0, "new": 0, "selected": 0, "skipped": "already_ran_today"}
        if not config.test_window_open(now):
            with connect(config.database_path) as db:
                db.execute("INSERT INTO runs(started_at,finished_at,status) VALUES(?,?,?)",
                           (now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), "test_window_closed"))
                db.commit()
            LOG.info("test_window_closed")
            return {"fetched": 0, "new": 0, "selected": 0, "skipped": "test_window_closed"}
        start = now - timedelta(hours=config.window_hours)
        with connect(config.database_path) as db:
            last = db.execute("SELECT max(started_at) FROM runs WHERE status IN ('completed','completed_with_email_errors')").fetchone()[0]
        if last:
            start = min(start, datetime.fromisoformat(last))
        with connect(config.database_path) as db:
            cursor = db.execute("INSERT INTO runs(started_at,status) VALUES(?,?)", (now.isoformat(timespec="seconds"), "running"))
            run_id = cursor.lastrowid
            db.commit()
        try:
            client = client or BridgeClient(config)
            fetched = list(client.active_new_listings(start, now))
            with connect(config.database_path) as db:
                if not db.in_transaction:
                    db.execute("BEGIN IMMEDIATE")
                fresh = []
                for listing in fetched:
                    if listing.get('originating_system_name') != 'Cornerstone':
                        continue
                    values = {
                        "bridge_listing_id": str(listing["listing_id"]),
                        "mls_number": listing["mls_number"],
                        "status": listing["status"],
                        "entry_timestamp": listing["entry_timestamp"],
                        "address": listing["address"],
                        "agent_id": listing.get("agent_id"),
                        "agent_name": listing.get("agent_name"),
                        "agent_email": listing.get("agent_email"),
                        "brokerage_id": listing.get("brokerage_id"),
                        "brokerage_name": listing.get("brokerage_name"),
                        "brokerage_email": listing.get("brokerage_email"),
                        "brokerage_address": listing.get("brokerage_address"),
                        "broker_id": listing.get("broker_id"),
                        "broker_name": listing.get("broker_name"),
                        "broker_first_name": listing.get("broker_first_name"),
                        "broker_email": listing.get("broker_email"),
                        "first_processed_at": now.isoformat(timespec="seconds"),
                        "processing_status": "processed_not_selected",
                        "originating_system_name": listing['originating_system_name'],
                    }
                    placeholders = ",".join("?" for _ in LISTING_COLUMNS)
                    cursor = db.execute(f"INSERT OR IGNORE INTO listings({','.join(LISTING_COLUMNS)}) VALUES({placeholders})", tuple(values[key] for key in LISTING_COLUMNS))
                    if cursor.rowcount:
                        values["id"] = cursor.lastrowid
                        fresh.append(values)
                rate = float(selection_percent(config, db) / 100)
                office_cooldown_days = brokerage_cooldown_days(config, db)
                selected = choose_fairly(fresh, _recent_history(db, config, now, office_cooldown_days),
                                         config, rng, rate=rate, office_cooldown_days=office_cooldown_days)
                audit_ids = []
                routing_failures = 0
                for listing, metadata in selected:
                    try:
                        intended_to, intended_cc, actual = resolve_recipients(listing, config)
                        status, error = "email_pending", None
                    except EmailError as exc:
                        routing_failures += 1
                        intended_to = [listing["broker_email"]] if listing.get("broker_email") else []
                        intended_cc = [address for address in (listing.get("brokerage_email"), listing.get("agent_email")) if address]
                        actual = [config.admin_email] if config.test_mode and config.admin_email else intended_to + intended_cc
                        status, error = "email_failed", str(exc)
                    cursor = db.execute("""INSERT INTO audits(listing_id,selected_at,brokerage_id,brokerage_name,broker_id,broker_name,agent_name,intended_to,intended_cc,actual_recipients,test_mode,email_status,selection_metadata,last_error)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        listing["id"], now.isoformat(timespec="seconds"), listing["brokerage_id"], listing["brokerage_name"],
                        listing["broker_id"], listing["broker_name"], listing["agent_name"], json.dumps(intended_to),
                        json.dumps(intended_cc), json.dumps(actual), int(config.test_mode), status, json.dumps(metadata), error,
                    ))
                    db.execute("UPDATE listings SET processing_status=? WHERE id=?", ("selected_for_audit" if status == "email_pending" else status, listing["id"]))
                    if status == "email_pending":
                        audit_ids.append(cursor.lastrowid)
                db.execute("UPDATE runs SET status='completed', finished_at=?, fetched_count=?, new_count=?, selected_count=? WHERE id=?",
                           (utcnow(), len(fetched), len(fresh), len(selected), run_id))
                db.commit()
            # Resume committed requests that never began sending, including after downtime.
            with connect(config.database_path) as db:
                audit_ids = [row[0] for row in db.execute("SELECT a.id FROM audits a JOIN listings l ON l.id=a.listing_id WHERE a.email_status='email_pending' AND l.originating_system_name='Cornerstone'")]
            failed_deliveries = routing_failures
            for audit_id in audit_ids:
                if not deliver_audit(config, audit_id, sender=sender):
                    with connect(config.database_path) as db:
                        state = db.execute("SELECT email_status FROM audits WHERE id=?", (audit_id,)).fetchone()[0]
                    if state != "email_pending":
                        failed_deliveries += 1
            if failed_deliveries:
                with connect(config.database_path) as db:
                    db.execute("UPDATE runs SET status='completed_with_email_errors', error=? WHERE id=?",
                               (f"{failed_deliveries} email(s) were not accepted; see audit history", run_id))
                    db.commit()
            LOG.info("run_completed", extra={"run_id": run_id, "fetched": len(fetched), "new": len(fresh), "selected": len(selected)})
            return {"fetched": len(fetched), "new": len(fresh), "selected": len(selected)}
        except Exception as exc:
            with connect(config.database_path) as db:
                db.execute("UPDATE runs SET status='failed', finished_at=?, error=? WHERE id=?", (utcnow(), str(exc), run_id))
                db.commit()
            LOG.exception("run_failed", extra={"run_id": run_id})
            raise
