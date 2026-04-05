
#!/usr/bin/env python3
import asyncio
import json
import os
import random
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pymysql
from meshcore import EventType, MeshCore

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
TARGETS_FILE = os.getenv("TARGETS_FILE", "/etc/repeater-status-ingest/targets.json")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "300"))
MESHCLI_BIN = os.getenv("MESHCLI_BIN", "/home/al/.local/bin/meshcli")
STATUS_TIMEOUT_SEC = float(os.getenv("STATUS_TIMEOUT_SEC", "25"))
CONNECT_TIMEOUT_SEC = float(os.getenv("CONNECT_TIMEOUT_SEC", "20"))
CONTACT_REFRESH_SEC = int(os.getenv("CONTACT_REFRESH_SEC", "900"))
CONTACT_SNAPSHOT_INTERVAL_SEC = int(os.getenv("CONTACT_SNAPSHOT_INTERVAL_SEC", "21600"))
CONTACT_PRUNE_MAX_AGE_DAYS = int(os.getenv("CONTACT_PRUNE_MAX_AGE_DAYS", "14"))
CONTACT_SOURCE_ID = os.getenv("CONTACT_SOURCE_ID", SERIAL_PORT)
NEIGHBOURS_INTERVAL_SEC = int(os.getenv("NEIGHBOURS_INTERVAL_SEC", "3600"))
NEIGHBOURS_JITTER_SEC = int(os.getenv("NEIGHBOURS_JITTER_SEC", "900"))
NEIGHBOURS_TIMEOUT_SEC = float(os.getenv("NEIGHBOURS_TIMEOUT_SEC", "25"))
NEIGHBOURS_MAX_ATTEMPTS = int(os.getenv("NEIGHBOURS_MAX_ATTEMPTS", "2"))
NEIGHBOURS_RETRY_MIN_SEC = float(os.getenv("NEIGHBOURS_RETRY_MIN_SEC", "15"))
NEIGHBOURS_RETRY_MAX_SEC = float(os.getenv("NEIGHBOURS_RETRY_MAX_SEC", "45"))
NEIGHBOURS_RETRY_INTERVAL_SEC = int(os.getenv("NEIGHBOURS_RETRY_INTERVAL_SEC", "600"))
PATH_SETTLE_SEC = float(os.getenv("PATH_SETTLE_SEC", "3"))
RELOGIN_INTERVAL_SEC = int(os.getenv("RELOGIN_INTERVAL_SEC", "3600"))
LOGIN_SETTLE_SEC = float(os.getenv("LOGIN_SETTLE_SEC", "2"))
RUN_MODE = os.getenv("RUN_MODE", "oneshot").strip().lower()

DB_HOST = os.getenv("DB_HOST", "10.50.68.87")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "repeater_status")
DB_USER = os.getenv("DB_USER", "repeater_writer")
DB_PASS = os.getenv("DB_PASS", "")

SPOOL_FILE = Path(os.getenv("SPOOL_FILE", "/home/al/repeater_status_spool.jsonl"))
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
DB_RETRY_MIN_SEC = int(os.getenv("DB_RETRY_MIN_SEC", "10"))

META_NODE = os.getenv("NODE", "1385fef9d37e")
SYSTEMD_TIMER_UNIT = os.getenv("SYSTEMD_TIMER_UNIT", "repeater-status-ingest.timer")

_last_db_fail = 0.0


def log(msg: str) -> None:
    print(msg, flush=True)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_ts(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def shorten_error(text: str | None, limit: int = 255) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def to_hex_path(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, list):
        try:
            return "".join(f"{int(x):02x}" for x in value)
        except Exception:
            return str(value)
    return str(value)


def unwrap_contact(contact: Any) -> dict | None:
    if isinstance(contact, dict):
        return contact
    if isinstance(contact, tuple) and contact:
        first = contact[0]
        if isinstance(first, dict):
            return first
    return None


def get_contact_pubkey(contact: dict | None) -> str | None:
    contact = unwrap_contact(contact)
    if not contact:
        return None
    for key in ("public_key", "pubkey", "key"):
        value = contact.get(key)
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, str) and value:
            return value
    return None


def get_contact_prefix(contact: dict | None) -> str:
    pubkey = get_contact_pubkey(contact)
    if pubkey:
        return pubkey[:12]
    return META_NODE


def get_contact_name(contact: dict | None) -> str | None:
    contact = unwrap_contact(contact)
    if not contact:
        return None
    for key in ("adv_name", "name"):
        value = contact.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_contact_type(contact: dict | None) -> int | None:
    contact = unwrap_contact(contact)
    if not contact:
        return None

    raw = contact.get("type")
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def is_repeater_contact(contact: dict | None) -> bool:
    return get_contact_type(contact) == 2


def normalize_contact_name(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def find_contact_for_target(meshcore: MeshCore, target: dict) -> dict | None:
    wanted_pubkey = str(target.get("pubkey") or "").strip().lower()
    wanted_name = str(target.get("name") or "").strip()

    contacts = getattr(meshcore, "contacts", None)
    if isinstance(contacts, dict):
        for item in contacts.values():
            contact_pubkey = (get_contact_pubkey(item) or "").strip().lower()
            if wanted_pubkey and contact_pubkey == wanted_pubkey:
                return unwrap_contact(item)

        for item in contacts.values():
            contact_name = get_contact_name(item)
            if wanted_name and contact_name == wanted_name:
                return unwrap_contact(item)

    if wanted_name:
        try:
            return meshcore.get_contact_by_name(wanted_name)
        except Exception:
            return None

    return None


def extract_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in meshcli output")
    return json.loads(text[start:end + 1])


def describe_contact_route(contact: dict | None) -> str:
    contact = unwrap_contact(contact)
    if not contact:
        return "route=unknown"
    parts: list[str] = []
    for key in ("adv_name", "name"):
        if contact.get(key):
            parts.append(f"name={contact[key]}")
            break
    pubkey = get_contact_pubkey(contact)
    if pubkey:
        parts.append(f"pubkey_pre={pubkey[:12]}")
    if "hops" in contact:
        parts.append(f"hops={contact.get('hops')}")
    if "direct" in contact:
        parts.append(f"direct={contact.get('direct')}")
    path_val = contact.get("path")
    path_hex = to_hex_path(path_val)
    if path_hex:
        parts.append(f"path={path_hex}")
    last_mod = contact.get("lastmod") or contact.get("mod_time") or contact.get("mtime")
    if last_mod is not None:
        parts.append(f"lastmod={last_mod}")
    return " ".join(parts) if parts else "route=unknown"


def classify_neighbours_error(text: str) -> str:
    lower = text.lower()
    if "keyerror: \'name\'" in lower or "error: \'name\'" in lower:
        return "cli_keyerror_name"
    if "no_event_received" in lower:
        return "no_event_received"
    if "no json object found" in lower:
        return "invalid_json"
    if "timeout" in lower:
        return "timeout"
    return "other"


def load_targets() -> list[dict]:
    cfg_path = Path(TARGETS_FILE)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))

    if not isinstance(data, list) or not data:
        raise RuntimeError(f"TARGETS_FILE must contain a non-empty JSON list: {cfg_path}")

    targets: list[dict] = []
    seen_nodes: set[str] = set()

    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Target entry #{idx} is not an object")

        name = str(item.get("name") or "").strip()
        pubkey = str(item.get("pubkey") or "").strip().lower()
        node = str(item.get("node") or "").strip().lower()
        password = str(item.get("password") or "")
        login_mode = str(item.get("login_mode") or "always").strip().lower()
        enabled = item.get("enabled", True)

        if not isinstance(enabled, bool):
            raise RuntimeError(f"Target entry #{idx} has non-boolean enabled={enabled!r}")

        if login_mode not in {"always", "never"}:
            raise RuntimeError(
                f"Target entry #{idx} has invalid login_mode={login_mode!r}; expected 'always' or 'never'"
            )

        if not name:
            raise RuntimeError(f"Target entry #{idx} is missing 'name'")
        if not pubkey:
            raise RuntimeError(f"Target entry #{idx} is missing 'pubkey'")
        if not node:
            node = pubkey[:12]

        if not enabled:
            continue

        if node in seen_nodes:
            raise RuntimeError(f"Duplicate target node in TARGETS_FILE: {node}")
        seen_nodes.add(node)

        targets.append(
            {
                "name": name,
                "pubkey": pubkey,
                "node": node,
                "password": password,
                "login_mode": login_mode,
                "enabled": enabled,
            }
        )

    return targets


TARGETS = load_targets()
PRIMARY_TARGET = TARGETS[0]
REPEATER_NAME = PRIMARY_TARGET["name"]
REPEATER_PASSWORD = PRIMARY_TARGET["password"]
META_NODE = PRIMARY_TARGET["node"]


def neighbour_retry_delay() -> float:
    low = max(0.0, NEIGHBOURS_RETRY_MIN_SEC)
    high = max(low, NEIGHBOURS_RETRY_MAX_SEC)
    return random.uniform(low, high)


def build_record_from_status(data: dict, fallback_node: str) -> tuple[str, dict | None, str | None, str]:
    rec = {
        "ts": now_str(),
        "node": data.get("pubkey_pre"),
        "bat_mv": data.get("bat"),
        "noise_floor_dbm": data.get("noise_floor"),
        "last_rssi_dbm": data.get("last_rssi"),
        "last_snr_db": data.get("last_snr"),
        "tx_queue_len": data.get("tx_queue_len"),
        "nb_recv": data.get("nb_recv"),
        "nb_sent": data.get("nb_sent"),
        "airtime_secs": data.get("airtime"),
        "rx_airtime_secs": data.get("rx_airtime"),
        "uptime_secs": data.get("uptime"),
        "sent_flood": data.get("sent_flood"),
        "sent_direct": data.get("sent_direct"),
        "recv_flood": data.get("recv_flood"),
        "recv_direct": data.get("recv_direct"),
        "full_evts": data.get("full_evts"),
        "direct_dups": data.get("direct_dups"),
        "flood_dups": data.get("flood_dups"),
    }

    node = rec.get("node") or fallback_node or META_NODE
    if not rec.get("node"):
        msg = f"status payload missing pubkey_pre: {data!r}"
        return ("invalid", None, shorten_error(msg), node)
    return ("valid", rec, None, node)


def db_connect():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=DB_CONNECT_TIMEOUT,
        read_timeout=DB_CONNECT_TIMEOUT,
        write_timeout=DB_CONNECT_TIMEOUT,
    )


def db_insert_record(rec: dict) -> None:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repeater_status_history (
                    ts, node, bat_mv, noise_floor_dbm, last_rssi_dbm, last_snr_db, tx_queue_len,
                    nb_recv, nb_sent, airtime_secs, rx_airtime_secs, uptime_secs,
                    sent_flood, sent_direct, recv_flood, recv_direct, full_evts,
                    direct_dups, flood_dups
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    rec["ts"],
                    rec["node"],
                    rec["bat_mv"],
                    rec["noise_floor_dbm"],
                    rec["last_rssi_dbm"],
                    rec["last_snr_db"],
                    rec["tx_queue_len"],
                    rec["nb_recv"],
                    rec["nb_sent"],
                    rec["airtime_secs"],
                    rec["rx_airtime_secs"],
                    rec["uptime_secs"],
                    rec["sent_flood"],
                    rec["sent_direct"],
                    rec["recv_flood"],
                    rec["recv_direct"],
                    rec["full_evts"],
                    rec["direct_dups"],
                    rec["flood_dups"],
                ),
            )
    finally:
        conn.close()


def db_insert_poll_log(
    ts_started: str,
    ts_finished: str,
    node: str,
    is_valid: bool,
    status: str,
    error_text: str | None,
) -> None:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repeater_status_poll_log (
                    ts_started,
                    ts_finished,
                    node,
                    is_valid,
                    status,
                    error_text
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    ts_started,
                    ts_finished,
                    node,
                    1 if is_valid else 0,
                    status,
                    shorten_error(error_text),
                ),
            )
    finally:
        conn.close()



def db_insert_neighbour_records(collected_ts: str, repeater_node: str, rows: list[dict]) -> None:
    if not rows:
        return

    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO repeater_neighbors_history (
                    collected_ts,
                    repeater_node,
                    neighbor_pubkey_pre,
                    neighbor_name,
                    neighbor_seen_ts,
                    snr_x4,
                    snr_db
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        collected_ts,
                        repeater_node,
                        row["neighbor_pubkey_pre"],
                        row.get("neighbor_name"),
                        row["neighbor_seen_ts"],
                        row["snr_x4"],
                        row["snr_db"],
                    )
                    for row in rows
                ],
            )
    finally:
        conn.close()



def db_get_last_repeater_contact_snapshot_ts(source_id: str) -> datetime | None:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(last_seen_ts)
                FROM repeater_contact_current
                WHERE source_id = %s
                """,
                (source_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return row[0]
    finally:
        conn.close()


def repeater_contact_snapshot_due(source_id: str, min_interval_sec: int) -> bool:
    last_seen_ts = db_get_last_repeater_contact_snapshot_ts(source_id)
    if last_seen_ts is None:
        return True
    age_sec = (datetime.now() - last_seen_ts).total_seconds()
    return age_sec >= max(0, min_interval_sec)


def db_upsert_repeater_contacts(source_id: str, collected_ts: str, rows: list[dict]) -> None:
    if not rows:
        return

    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO repeater_contact_current (
                    source_id,
                    contact_name,
                    contact_name_norm,
                    current_pubkey,
                    current_pubkey_pre,
                    first_byte,
                    first_seen_ts,
                    last_seen_ts
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    contact_name = VALUES(contact_name),
                    current_pubkey = VALUES(current_pubkey),
                    current_pubkey_pre = VALUES(current_pubkey_pre),
                    first_byte = VALUES(first_byte),
                    last_seen_ts = VALUES(last_seen_ts)
                """,
                [
                    (
                        source_id,
                        row["contact_name"],
                        row["contact_name_norm"],
                        row["current_pubkey"],
                        row["current_pubkey_pre"],
                        row["first_byte"],
                        collected_ts,
                        collected_ts,
                    )
                    for row in rows
                ],
            )
    finally:
        conn.close()


def db_prune_stale_repeater_contacts(source_id: str, cutoff_ts: str) -> None:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM repeater_contact_current
                WHERE source_id = %s
                  AND last_seen_ts < %s
                """,
                (source_id, cutoff_ts),
            )
    finally:
        conn.close()

def db_upsert_meta(
    node: str,
    poll_state: str,
    last_poll_started_ts: str | None = None,
    last_poll_finished_ts: str | None = None,
    next_poll_at: str | None = None,
) -> None:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repeater_status_meta (
                    node,
                    last_poll_started_ts,
                    last_poll_finished_ts,
                    next_poll_at,
                    poll_state
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    last_poll_started_ts = COALESCE(VALUES(last_poll_started_ts), last_poll_started_ts),
                    last_poll_finished_ts = COALESCE(VALUES(last_poll_finished_ts), last_poll_finished_ts),
                    next_poll_at = VALUES(next_poll_at),
                    poll_state = VALUES(poll_state)
                """,
                (
                    node,
                    last_poll_started_ts,
                    last_poll_finished_ts,
                    next_poll_at,
                    poll_state,
                ),
            )
    finally:
        conn.close()


def safe_update_meta(
    node: str,
    poll_state: str,
    last_poll_started_ts: str | None = None,
    last_poll_finished_ts: str | None = None,
    next_poll_at: str | None = None,
) -> None:
    try:
        db_upsert_meta(
            node=node,
            poll_state=poll_state,
            last_poll_started_ts=last_poll_started_ts,
            last_poll_finished_ts=last_poll_finished_ts,
            next_poll_at=next_poll_at,
        )
    except Exception as e:
        log(f"Meta update failed: {e}")


def safe_insert_poll_log(
    ts_started: str,
    ts_finished: str,
    node: str,
    is_valid: bool,
    status: str,
    error_text: str | None,
) -> None:
    try:
        db_insert_poll_log(
            ts_started=ts_started,
            ts_finished=ts_finished,
            node=node,
            is_valid=is_valid,
            status=status,
            error_text=error_text,
        )
    except Exception as e:
        log(f"Poll log insert failed: {e}")


def get_next_poll_at_from_systemd() -> str | None:
    import subprocess

    result = subprocess.run(
        [
            "systemctl",
            "list-timers",
            "--all",
            "--no-pager",
            "--no-legend",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if result.returncode != 0:
        log(
            f"Could not read timer list: rc={result.returncode}, "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        return None

    for line in result.stdout.splitlines():
        if SYSTEMD_TIMER_UNIT not in line:
            continue

        fields = line.split()
        if len(fields) < 4:
            log(f"Unexpected list-timers format for {SYSTEMD_TIMER_UNIT!r}: {line!r}")
            return None

        next_ts_text = " ".join(fields[:4])

        conv = subprocess.run(
            ["date", "-d", next_ts_text, "+%Y-%m-%d %H:%M:%S"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        if conv.returncode != 0:
            log(
                f"Could not parse timer timestamp {next_ts_text!r}: "
                f"stdout={conv.stdout!r}, stderr={conv.stderr!r}"
            )
            return None

        return conv.stdout.strip()

    log(f"Timer {SYSTEMD_TIMER_UNIT!r} not found in systemctl list-timers output")
    return None


def spool_append(rec: dict) -> None:
    SPOOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SPOOL_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def spool_flush() -> None:
    global _last_db_fail

    if not SPOOL_FILE.exists() or SPOOL_FILE.stat().st_size == 0:
        return

    now = time.time()
    if now - _last_db_fail < DB_RETRY_MIN_SEC:
        return

    lines = SPOOL_FILE.read_text(encoding="utf-8").splitlines()
    if not lines:
        return

    remaining = []

    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        rec = json.loads(line)
        try:
            db_insert_record(rec)
        except Exception as e:
            _last_db_fail = time.time()
            remaining = lines[idx:]
            log(f"DB still unavailable during flush: {e}")
            break

    tmp = SPOOL_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for line in remaining:
            if line.strip():
                f.write(line + "\n")
    tmp.replace(SPOOL_FILE)

    flushed = len([x for x in lines if x.strip()]) - len([x for x in remaining if x.strip()])
    if flushed > 0:
        log(f"Flushed {flushed} queued records to DB")


def save_record(rec: dict) -> None:
    global _last_db_fail

    try:
        spool_flush()
        db_insert_record(rec)
        log(
            f"Inserted node={rec['node']} bat_mv={rec['bat_mv']} "
            f"noise_floor_dbm={rec['noise_floor_dbm']} last_rssi_dbm={rec['last_rssi_dbm']} "
            f"last_snr_db={rec['last_snr_db']}"
        )
    except Exception as e:
        _last_db_fail = time.time()
        spool_append(rec)
        log(f"DB unavailable, queued locally: {e}")


def save_neighbour_records(repeater_node: str, rows: list[dict]) -> None:
    if not rows:
        return
    try:
        db_insert_neighbour_records(
            collected_ts=now_str(),
            repeater_node=repeater_node,
            rows=rows,
        )
        log(f"Inserted {len(rows)} neighbour rows for node={repeater_node}")
    except Exception as e:
        log(f"Neighbour insert failed: {e}")




def save_repeater_contacts(source_id: str, contacts_payload: dict) -> None:
    if not isinstance(contacts_payload, dict) or not contacts_payload:
        return

    try:
        if not repeater_contact_snapshot_due(source_id, CONTACT_SNAPSHOT_INTERVAL_SEC):
            return

        collected_ts = now_str()
        cutoff_ts = fmt_ts(datetime.now() - timedelta(days=max(1, CONTACT_PRUNE_MAX_AGE_DAYS)))
        rows: list[dict] = []

        for item in contacts_payload.values():
            contact = unwrap_contact(item)
            if not contact or not is_repeater_contact(contact):
                continue

            contact_name = get_contact_name(contact)
            contact_pubkey = get_contact_pubkey(contact)
            contact_name_norm = normalize_contact_name(contact_name)
            if not contact_name_norm or not contact_pubkey:
                continue

            contact_pubkey = contact_pubkey.lower()
            rows.append(
                {
                    "contact_name": contact_name or contact_name_norm,
                    "contact_name_norm": contact_name_norm,
                    "current_pubkey": contact_pubkey,
                    "current_pubkey_pre": contact_pubkey[:12],
                    "first_byte": contact_pubkey[:2],
                }
            )

        db_upsert_repeater_contacts(source_id, collected_ts, rows)
        db_prune_stale_repeater_contacts(source_id, cutoff_ts)
        log(f"Updated repeater contact snapshot: {len(rows)} repeaters for source={source_id}")
    except Exception as e:
        log(f"Repeater contact snapshot update failed: {e}")


class MeshStatusSession:
    def __init__(self, target: dict) -> None:
        self.target = target
        self.target_name = str(target.get("name") or "").strip()
        self.target_pubkey = str(target.get("pubkey") or "").strip().lower()
        self.target_node = str(target.get("node") or "").strip().lower() or self.target_pubkey[:12]
        self.target_password = str(target.get("password") or "")
        self.target_login_mode = str(target.get("login_mode") or "always").strip().lower()
        self.meshcore: MeshCore | None = None
        self.contact: dict | None = None
        self.contact_prefix_map: list[tuple[str, str]] = []
        self.last_contact_refresh = 0.0
        self.last_login_ts = 0.0
        self.consecutive_failures = 0
        self.login_events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        self.contacts_payload: dict[str, dict] = {}
        self.next_neighbours_due_ts = 0.0
        self.login_success_sub = None
        self.login_failed_sub = None

    async def close(self) -> None:
        if self.meshcore is not None:
            try:
                await self.meshcore.disconnect()
            except Exception as e:
                log(f"Disconnect failed: {e}")
            self.meshcore = None
        self.contact = None
        self.contact_prefix_map = []
        self.contacts_payload = {}
        self.last_contact_refresh = 0.0
        self.login_success_sub = None
        self.login_failed_sub = None

    async def _on_login_success(self, event) -> None:
        await self.login_events.put(("success", event.payload))

    async def _on_login_failed(self, event) -> None:
        await self.login_events.put(("failed", event.payload))

    async def connect(self, force_reconnect: bool = False) -> None:
        if force_reconnect:
            await self.close()

        if self.meshcore is not None and self.meshcore.is_connected:
            return

        log(f"Connecting to MeshCore companion on {SERIAL_PORT}")
        self.meshcore = await asyncio.wait_for(
            MeshCore.create_serial(SERIAL_PORT, debug=True),
            timeout=CONNECT_TIMEOUT_SEC,
        )

        self.login_success_sub = self.meshcore.subscribe(
            EventType.LOGIN_SUCCESS,
            self._on_login_success,
        )
        self.login_failed_sub = self.meshcore.subscribe(
            EventType.LOGIN_FAILED,
            self._on_login_failed,
        )

        await self.refresh_contact(force=True)
        if self.next_neighbours_due_ts <= 0:
            self.next_neighbours_due_ts = time.time() + random.uniform(60, max(60, NEIGHBOURS_JITTER_SEC))
        self.consecutive_failures = 0

    async def refresh_contact(self, force: bool = False) -> dict | None:
        if self.meshcore is None:
            raise RuntimeError("MeshCore is not connected")

        now = time.time()
        if force or self.contact is None or now - self.last_contact_refresh >= CONTACT_REFRESH_SEC:
            result = await self.meshcore.commands.get_contacts()
            if result.type == EventType.ERROR:
                raise RuntimeError(f"get_contacts failed: {result.payload}")
            payload = getattr(result, "payload", {}) or {}
            self.contacts_payload = payload if isinstance(payload, dict) else {}
            self.contact_prefix_map = []
            for item in self.contacts_payload.values():
                pubkey = get_contact_pubkey(item)
                name = get_contact_name(item)
                if pubkey and name:
                    self.contact_prefix_map.append((pubkey.lower(), name))
            save_repeater_contacts(CONTACT_SOURCE_ID, self.contacts_payload)
            self.last_contact_refresh = now

        self.contact = find_contact_for_target(self.meshcore, self.target)
        if self.contact is not None:
            log(f"Target contact {self.target_name}: {describe_contact_route(self.contact)}")
        else:
            log(f"Target contact {self.target_name!r} not found in contacts")
        return self.contact

    async def ensure_contact(self, force_refresh: bool = False) -> dict:
        await self.connect()
        contact = await self.refresh_contact(force=force_refresh)
        if contact is None:
            raise RuntimeError(f"Target contact {self.target_name!r} not found")
        return contact

    async def drain_login_events(self) -> None:
        while True:
            try:
                self.login_events.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def login_if_needed(self, force: bool = False) -> None:
        if self.target_login_mode == "never":
            return

        if not self.target_password:
            return

        now = time.time()
        if not force and self.last_login_ts and now - self.last_login_ts < RELOGIN_INTERVAL_SEC:
            return

        contact = await self.ensure_contact(force_refresh=force)
        prefix = get_contact_prefix(contact)

        if self.last_login_ts:
            try:
                await self.meshcore.commands.send_logout(contact)
                log(f"Sent logout to {self.target_name} ({prefix})")
                await asyncio.sleep(LOGIN_SETTLE_SEC)
            except Exception as e:
                log(f"Logout failed: {e}")

        await self.drain_login_events()
        sent = await self.meshcore.commands.send_login(contact, self.target_password)
        if sent.type == EventType.ERROR:
            raise RuntimeError(f"login send failed: {sent.payload}")

        log(f"Sent login to {self.target_name} ({prefix})")

        try:
            status, payload = await asyncio.wait_for(self.login_events.get(), timeout=STATUS_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log("No explicit login result received, continuing with current session")
            self.last_login_ts = now
            return

        payload_prefix = str(payload.get("pubkey_prefix") or payload.get("pubkey_pre") or "")
        if payload_prefix and not prefix.startswith(payload_prefix) and not payload_prefix.startswith(prefix):
            log(f"Ignoring login event for another node: {payload!r}")
            self.last_login_ts = now
            return

        if status != "success":
            raise RuntimeError(f"login failed: {payload!r}")

        self.last_login_ts = now
        log(f"Login ok for {self.target_name} ({prefix})")

    async def recover_path(self) -> None:
        contact = await self.ensure_contact(force_refresh=True)
        prefix = get_contact_prefix(contact)
        pubkey = get_contact_pubkey(contact)
        if not pubkey:
            return

        try:
            result = await self.meshcore.commands.reset_path(pubkey)
            if result.type == EventType.ERROR:
                log(f"reset_path failed for {self.target_name}: {result.payload}")
            else:
                log(f"reset_path sent for {self.target_name} ({prefix})")
        except Exception as e:
            log(f"reset_path exception: {e}")

        await asyncio.sleep(1)

        try:
            result = await self.meshcore.commands.send_path_discovery(contact)
            if result.type == EventType.ERROR:
                log(f"path discovery failed for {self.target_name}: {result.payload}")
            else:
                log(f"path discovery sent for {self.target_name} ({prefix})")
        except Exception as e:
            log(f"path discovery exception: {e}")

        await asyncio.sleep(PATH_SETTLE_SEC)
        await self.refresh_contact(force=True)


    def neighbours_due(self) -> bool:
        return time.time() >= self.next_neighbours_due_ts

    def schedule_next_neighbours(self) -> None:
        jitter = random.uniform(0, max(0, NEIGHBOURS_JITTER_SEC))
        self.next_neighbours_due_ts = time.time() + NEIGHBOURS_INTERVAL_SEC + jitter

    def resolve_contact_name_by_prefix(self, pubkey_prefix: str) -> str | None:
        prefix = pubkey_prefix.strip().lower()
        if not prefix:
            return None
        matches = sorted({name for full_pubkey, name in self.contact_prefix_map if full_pubkey.startswith(prefix)})
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            log(f"Neighbour name ambiguous for prefix {pubkey_prefix}: {matches}")
        return None

    async def run_meshcli_json(self, *args: str, timeout: float) -> dict:
        cmd = [MESHCLI_BIN, "-j", "-s", SERIAL_PORT, *args]
        log(f"Running meshcli JSON command for {self.target_name}: {' '.join(repr(x) for x in cmd[4:])}")

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        result = await asyncio.to_thread(_run)
        if result.returncode != 0:
            raise RuntimeError(
                f"meshcli failed rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
            )

        try:
            return extract_json(result.stdout)
        except Exception as e:
            raise RuntimeError(
                f"meshcli returned no valid JSON: {e}; stdout={result.stdout!r}, stderr={result.stderr!r}"
            ) from e

    async def collect_neighbours(self) -> tuple[str, list[dict] | None, str | None, str]:
        await self.connect()
        contact = await self.ensure_contact(force_refresh=False)
        fallback_node = get_contact_prefix(contact)
        last_error_text = None

        max_attempts = max(1, NEIGHBOURS_MAX_ATTEMPTS)

        for attempt in range(1, max_attempts + 1):
            try:
                payload = await self.run_meshcli_json(
                    "req_neighbours",
                    self.target_name,
                    timeout=NEIGHBOURS_TIMEOUT_SEC,
                )

                neighbours = payload.get("neighbours") or []
                repeater_node = str(
                    payload.get("pubkey_prefix")
                    or payload.get("pubkey_pre")
                    or fallback_node
                )

                rows: list[dict] = []
                now_ts = int(time.time())
                for item in neighbours:
                    pubkey_pre = str(item.get("pubkey") or item.get("pubkey_pre") or "").strip()
                    if not pubkey_pre:
                        continue
                    secs_ago = int(item.get("secs_ago", 0))
                    snr_db = float(item.get("snr"))
                    rows.append(
                        {
                            "neighbor_pubkey_pre": pubkey_pre,
                            "neighbor_name": self.resolve_contact_name_by_prefix(pubkey_pre),
                            "neighbor_seen_ts": now_ts - secs_ago,
                            "snr_x4": int(round(snr_db * 4)),
                            "snr_db": snr_db,
                        }
                    )

                self.schedule_next_neighbours()
                log(
                    f"Collected {len(rows)} neighbours via meshcli for {REPEATER_NAME} ({repeater_node}) "
                    f"on attempt {attempt}/{max_attempts}"
                )
                return ("valid", rows, None, repeater_node)

            except Exception as e:
                err_text = str(e)
                err_kind = classify_neighbours_error(err_text)
                last_error_text = shorten_error(err_text)
                log(
                    f"collect_neighbours attempt {attempt}/{max_attempts} failed "
                    f"[{err_kind}]: {err_text}"
                )

                if attempt >= max_attempts:
                    break

                delay = neighbour_retry_delay()
                log(f"Retrying neighbour collection in {delay:.1f}s")
                await asyncio.sleep(delay)

        self.schedule_next_neighbours()
        return ("error", None, last_error_text, fallback_node)

    async def collect_status(self) -> tuple[str, dict | None, str | None, str]:
        try:
            await self.connect()
            contact = await self.ensure_contact(force_refresh=False)
            await self.login_if_needed(force=False)

            log(f"Status route snapshot: {describe_contact_route(contact)}")

            dst = get_contact_pubkey(contact) or contact
            sent = await self.meshcore.commands.send_statusreq(dst)
            if getattr(sent, "type", None) == EventType.ERROR:
                raise RuntimeError(f"send_statusreq failed: {getattr(sent, 'payload', None)}")

            result = await self.meshcore.wait_for_event(
                EventType.STATUS_RESPONSE,
                timeout=STATUS_TIMEOUT_SEC,
            )

            if result is None:
                raise RuntimeError("wait_for_event STATUS_RESPONSE timed out")

            if getattr(result, "type", None) == EventType.ERROR:
                raise RuntimeError(f"status wait failed: {getattr(result, 'payload', None)}")

            data = getattr(result, "payload", None)
            if data is None:
                raise RuntimeError(f"STATUS_RESPONSE returned no payload: {result!r}")
            fallback_node = get_contact_prefix(contact)
            status, rec, error_text, node = build_record_from_status(data, fallback_node)
            if status == "valid":
                self.consecutive_failures = 0
            else:
                self.consecutive_failures += 1
            return (status, rec, error_text, node)

        except Exception as e:
            self.consecutive_failures += 1
            msg = shorten_error(str(e))
            fallback_node = get_contact_prefix(self.contact)
            log(f"collect_status failed: {e}")

            if self.consecutive_failures >= 2:
                try:
                    await self.recover_path()
                    if self.target_login_mode != "never" and self.target_password:
                        await self.login_if_needed(force=True)
                except Exception as recover_error:
                    log(f"Recovery failed: {recover_error}")

            if self.consecutive_failures >= 4:
                try:
                    await self.connect(force_reconnect=True)
                except Exception as reconnect_error:
                    log(f"Reconnect failed: {reconnect_error}")

            return ("error", None, msg, (get_contact_prefix(self.contact) if self.contact else self.target_node))



# --- neighbour stability monkey patch ---
def _parse_expected_count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_neighbour_payload(payload: Any) -> tuple[list[dict], dict[str, Any]]:
    if isinstance(payload, list):
        return payload, {}

    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected neighbour payload type: {type(payload).__name__}")

    neighbours = payload.get("neighbours")
    if neighbours is None:
        neighbours = payload.get("results")

    if neighbours is None and isinstance(payload.get("payload"), dict):
        nested_payload = payload["payload"]
        neighbours = nested_payload.get("neighbours") or nested_payload.get("results")
        if isinstance(neighbours, list):
            merged_meta = dict(payload)
            merged_meta.update(nested_payload)
            return neighbours, merged_meta

    if neighbours is None:
        raise RuntimeError(f"Neighbour payload missing neighbours list: {payload!r}")

    if not isinstance(neighbours, list):
        raise RuntimeError(f"Neighbour payload has non-list neighbours: {type(neighbours).__name__}")

    return neighbours, payload


def _patched_build_neighbour_rows(self, payload: Any, fallback_node: str) -> tuple[list[dict], str]:
    neighbours, meta = _extract_neighbour_payload(payload)
    repeater_node = str(
        meta.get("pubkey_prefix")
        or meta.get("pubkey_pre")
        or meta.get("pubkey")
        or fallback_node
    )

    results_count = _parse_expected_count(meta.get("results_count"))
    neighbours_count = _parse_expected_count(meta.get("neighbours_count"))

    if results_count is not None and len(neighbours) != results_count:
        raise RuntimeError(
            f"Neighbour payload incomplete: len(neighbours)={len(neighbours)} results_count={results_count}"
        )

    if (
        results_count is not None
        and neighbours_count is not None
        and results_count < neighbours_count
    ):
        raise RuntimeError(
            f"Neighbour payload truncated: results_count={results_count} neighbours_count={neighbours_count}"
        )

    rows: list[dict] = []
    now_ts = int(time.time())
    for item in neighbours:
        if not isinstance(item, dict):
            continue

        pubkey_pre = str(item.get("pubkey") or item.get("pubkey_pre") or "").strip()
        if not pubkey_pre:
            continue

        secs_ago_raw = item.get("secs_ago", 0)
        snr_raw = item.get("snr")

        try:
            secs_ago = int(secs_ago_raw)
        except (TypeError, ValueError):
            secs_ago = 0

        try:
            snr_db = float(snr_raw)
        except (TypeError, ValueError):
            continue

        rows.append(
            {
                "neighbor_pubkey_pre": pubkey_pre,
                "neighbor_name": self.resolve_contact_name_by_prefix(pubkey_pre),
                "neighbor_seen_ts": now_ts - max(0, secs_ago),
                "snr_x4": int(round(snr_db * 4)),
                "snr_db": snr_db,
            }
        )

    return rows, repeater_node



async def _patched_collect_neighbours_via_meshcore(self, contact: dict) -> tuple[list[dict], str]:
    if self.meshcore is None:
        raise RuntimeError("MeshCore is not connected")

    fetch_all_neighbours = getattr(self.meshcore.commands, "fetch_all_neighbours", None)
    if not callable(fetch_all_neighbours):
        raise RuntimeError("meshcore.commands.fetch_all_neighbours is not available")

    try:
        result = await fetch_all_neighbours(contact, timeout=NEIGHBOURS_TIMEOUT_SEC)
    except TypeError:
        result = await fetch_all_neighbours(contact)

    if getattr(result, "type", None) == EventType.ERROR:
        raise RuntimeError(f"fetch_all_neighbours failed: {getattr(result, 'payload', None)}")

    payload = getattr(result, "payload", result)
    if payload is None:
        raise RuntimeError("fetch_all_neighbours returned no payload")

    fallback_node = get_contact_prefix(contact)
    return self.build_neighbour_rows(payload, fallback_node)


async def _patched_run_meshcli_json(self, *args: str, timeout: float) -> dict:
    cmd = [MESHCLI_BIN, "-j", "-s", SERIAL_PORT, *args]
    log(f"Running meshcli JSON command: {' '.join(repr(x) for x in cmd[4:])}")

    def _run() -> subprocess.CompletedProcess[str]:
        fallback_home = Path(os.getenv("MESHCLI_FALLBACK_HOME", "/tmp/meshcli-fallback"))
        fallback_home.mkdir(parents=True, exist_ok=True)
        xdg_config_home = fallback_home / ".config"
        xdg_config_home.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["HOME"] = str(fallback_home)
        env["XDG_CONFIG_HOME"] = str(xdg_config_home)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )

    result = await asyncio.to_thread(_run)
    if result.returncode != 0:
        raise RuntimeError(
            f"meshcli failed rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )

    try:
        return extract_json(result.stdout)
    except Exception as e:
        raise RuntimeError(
            f"meshcli returned no valid JSON: {e}; stdout={result.stdout!r}, stderr={result.stderr!r}"
        ) from e


async def _patched_run_meshcli_json_exclusive(self, *args: str, timeout: float) -> dict:
    had_session = self.meshcore is not None and self.meshcore.is_connected
    if had_session:
        log("Closing persistent MeshCore session before meshcli fallback to avoid serial-port contention")
        await self.close()

    try:
        return await self.run_meshcli_json(*args, timeout=timeout)
    finally:
        if had_session:
            try:
                await self.connect(force_reconnect=True)
            except Exception as e:
                log(f"Reconnect after meshcli fallback failed: {e}")



async def _patched_collect_neighbours(self) -> tuple[str, list[dict] | None, str | None, str]:
    await self.connect()
    contact = await self.ensure_contact(force_refresh=False)
    fallback_node = get_contact_prefix(contact)
    last_error_text = None

    max_attempts = max(1, NEIGHBOURS_MAX_ATTEMPTS)

    for attempt in range(1, max_attempts + 1):
        direct_error = None

        try:
            contact = await self.ensure_contact(force_refresh=(attempt > 1))
            rows, repeater_node = await self.collect_neighbours_via_meshcore(contact)
            self.schedule_next_neighbours()
            log(
                f"Collected {len(rows)} neighbours via meshcore session for {self.target_name} ({repeater_node}) "
                f"on attempt {attempt}/{max_attempts}"
            )
            return ("valid", rows, None, repeater_node)

        except Exception as e:
            direct_error = e
            log(
                f"Direct MeshCore neighbour collection failed on attempt {attempt}/{max_attempts}: "
                f"{e}"
            )

        try:
            for fallback_poll in range(1, 6):
                payload = await self.run_meshcli_json_exclusive(
                    "req_neighbours",
                    REPEATER_NAME,
                    timeout=NEIGHBOURS_TIMEOUT_SEC,
                )

                if (
                    isinstance(payload, dict)
                    and str(payload.get("error") or "").strip().lower() == "getting data"
                ):
                    if fallback_poll < 5:
                        log(
                            f"meshcli fallback returned 'Getting data' for {self.target_name}; "
                            f"poll {fallback_poll}/5, waiting 3s"
                        )
                        await asyncio.sleep(3)
                        continue
                    raise RuntimeError("meshcli still reports 'Getting data' after 5 polls")

                rows, repeater_node = self.build_neighbour_rows(payload, fallback_node)
                self.schedule_next_neighbours()
                log(
                    f"Collected {len(rows)} neighbours via meshcli fallback for {self.target_name} ({repeater_node}) "
                    f"on attempt {attempt}/{max_attempts}"
                )
                return ("valid", rows, None, repeater_node)

        except Exception as fallback_error:
            err_text = (
                f"direct meshcore failed: {direct_error}; "
                f"meshcli fallback failed: {fallback_error}"
            )
            err_kind = classify_neighbours_error(err_text)
            last_error_text = shorten_error(err_text)
            log(
                f"collect_neighbours attempt {attempt}/{max_attempts} failed "
                f"[{err_kind}]: {err_text}"
            )

        if attempt >= max_attempts:
            break

        delay = neighbour_retry_delay()
        log(f"Retrying neighbour collection in {delay:.1f}s")
        await asyncio.sleep(delay)

    self.schedule_retry_neighbours()
    return ("error", None, last_error_text, fallback_node)


def _patched_schedule_retry_neighbours(self) -> None:
    self.next_neighbours_due_ts = time.time() + max(60, NEIGHBOURS_RETRY_INTERVAL_SEC)


def _apply_neighbour_stability_patch() -> None:
    MeshStatusSession.build_neighbour_rows = _patched_build_neighbour_rows
    MeshStatusSession.collect_neighbours_via_meshcore = _patched_collect_neighbours_via_meshcore
    MeshStatusSession.run_meshcli_json = _patched_run_meshcli_json
    MeshStatusSession.run_meshcli_json_exclusive = _patched_run_meshcli_json_exclusive
    MeshStatusSession.collect_neighbours = _patched_collect_neighbours
    MeshStatusSession.schedule_retry_neighbours = _patched_schedule_retry_neighbours


_apply_neighbour_stability_patch()

async def run_poll(session: MeshStatusSession, next_poll_at: str | None) -> None:
    started_ts = now_str()
    safe_update_meta(
        node=session.target_node,
        poll_state="running",
        last_poll_started_ts=started_ts,
        next_poll_at=None,
    )

    status, rec, error_text, node = await session.collect_status()
    finished_ts = now_str()

    safe_insert_poll_log(
        ts_started=started_ts,
        ts_finished=finished_ts,
        node=node,
        is_valid=(status == "valid"),
        status=status,
        error_text=error_text,
    )

    if status == "valid" and rec is not None:
        save_record(rec)
    else:
        log(f"No valid repeater status collected in this run (status={status})")

    if session.neighbours_due():
        n_status, n_rows, n_error_text, n_node = await session.collect_neighbours()
        if n_status == "valid" and n_rows is not None:
            save_neighbour_records(n_node, n_rows)
        else:
            log(f"No valid neighbour snapshot collected in this run (status={n_status}, error={n_error_text})")

    safe_update_meta(
        node=node,
        poll_state="idle",
        last_poll_finished_ts=finished_ts,
        next_poll_at=next_poll_at,
    )


async def run_oneshot() -> None:
    next_poll_at = get_next_poll_at_from_systemd()
    sessions = [MeshStatusSession(target) for target in TARGETS]

    try:
        for session in sessions:
            await run_poll(session, next_poll_at=next_poll_at)
            await session.close()
    finally:
        for session in sessions:
            await session.close()


async def run_loop() -> None:
    sessions = [MeshStatusSession(target) for target in TARGETS]
    deadline = time.monotonic()

    try:
        while True:
            deadline += POLL_INTERVAL_SEC
            next_poll_at_dt = datetime.now() + timedelta(seconds=max(0, deadline - time.monotonic()))
            next_poll_at = fmt_ts(next_poll_at_dt)

            for session in sessions:
                await run_poll(session, next_poll_at=next_poll_at)
                await session.close()

            sleep_for = max(0.0, deadline - time.monotonic())
            await asyncio.sleep(sleep_for)
    finally:
        for session in sessions:
            await session.close()


def main() -> None:
    if RUN_MODE == "oneshot":
        asyncio.run(run_oneshot())
    else:
        asyncio.run(run_loop())


if __name__ == "__main__":
    main()
