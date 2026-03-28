
#!/usr/bin/env python3
import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pymysql
from meshcore import EventType, MeshCore

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
REPEATER_NAME = os.getenv("REPEATER_NAME", "Inciema Tornis")
REPEATER_PASSWORD = os.getenv("REPEATER_PASSWORD", "")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "300"))
STATUS_TIMEOUT_SEC = float(os.getenv("STATUS_TIMEOUT_SEC", "25"))
CONNECT_TIMEOUT_SEC = float(os.getenv("CONNECT_TIMEOUT_SEC", "20"))
CONTACT_REFRESH_SEC = int(os.getenv("CONTACT_REFRESH_SEC", "900"))
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


def get_contact_pubkey(contact: dict | None) -> str | None:
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


def describe_contact_route(contact: dict | None) -> str:
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


class MeshStatusSession:
    def __init__(self) -> None:
        self.meshcore: MeshCore | None = None
        self.contact: dict | None = None
        self.last_contact_refresh = 0.0
        self.last_login_ts = 0.0
        self.consecutive_failures = 0
        self.login_events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
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
        self.last_contact_refresh = 0.0
        self.last_login_ts = 0.0

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
            MeshCore.create_serial(SERIAL_PORT),
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
        self.consecutive_failures = 0

    async def refresh_contact(self, force: bool = False) -> dict | None:
        if self.meshcore is None:
            raise RuntimeError("MeshCore is not connected")

        now = time.time()
        if force or self.contact is None or now - self.last_contact_refresh >= CONTACT_REFRESH_SEC:
            result = await self.meshcore.commands.get_contacts()
            if result.type == EventType.ERROR:
                raise RuntimeError(f"get_contacts failed: {result.payload}")
            self.last_contact_refresh = now

        self.contact = self.meshcore.get_contact_by_name(REPEATER_NAME)
        if self.contact is not None:
            log(f"Target contact: {describe_contact_route(self.contact)}")
        else:
            log(f"Target contact {REPEATER_NAME!r} not found in contacts")
        return self.contact

    async def ensure_contact(self, force_refresh: bool = False) -> dict:
        await self.connect()
        contact = await self.refresh_contact(force=force_refresh)
        if contact is None:
            raise RuntimeError(f"Target contact {REPEATER_NAME!r} not found")
        return contact

    async def drain_login_events(self) -> None:
        while True:
            try:
                self.login_events.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def login_if_needed(self, force: bool = False) -> None:
        if not REPEATER_PASSWORD:
            return

        now = time.time()
        if not force and self.last_login_ts and now - self.last_login_ts < RELOGIN_INTERVAL_SEC:
            return

        contact = await self.ensure_contact(force_refresh=force)
        prefix = get_contact_prefix(contact)

        if self.last_login_ts:
            try:
                await self.meshcore.commands.send_logout(contact)
                log(f"Sent logout to {REPEATER_NAME} ({prefix})")
                await asyncio.sleep(LOGIN_SETTLE_SEC)
            except Exception as e:
                log(f"Logout failed: {e}")

        await self.drain_login_events()
        sent = await self.meshcore.commands.send_login(contact, REPEATER_PASSWORD)
        if sent.type == EventType.ERROR:
            raise RuntimeError(f"login send failed: {sent.payload}")

        log(f"Sent login to {REPEATER_NAME} ({prefix})")

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
        log(f"Login ok for {REPEATER_NAME} ({prefix})")

    async def recover_path(self) -> None:
        contact = await self.ensure_contact(force_refresh=True)
        prefix = get_contact_prefix(contact)
        pubkey = get_contact_pubkey(contact)
        if not pubkey:
            return

        try:
            result = await self.meshcore.commands.reset_path(pubkey)
            if result.type == EventType.ERROR:
                log(f"reset_path failed for {REPEATER_NAME}: {result.payload}")
            else:
                log(f"reset_path sent for {REPEATER_NAME} ({prefix})")
        except Exception as e:
            log(f"reset_path exception: {e}")

        await asyncio.sleep(1)

        try:
            result = await self.meshcore.commands.send_path_discovery(contact)
            if result.type == EventType.ERROR:
                log(f"path discovery failed for {REPEATER_NAME}: {result.payload}")
            else:
                log(f"path discovery sent for {REPEATER_NAME} ({prefix})")
        except Exception as e:
            log(f"path discovery exception: {e}")

        await asyncio.sleep(PATH_SETTLE_SEC)
        await self.refresh_contact(force=True)

    async def collect_status(self) -> tuple[str, dict | None, str | None, str]:
        try:
            await self.connect()
            contact = await self.ensure_contact(force_refresh=False)
            await self.login_if_needed(force=False)

            result = await self.meshcore.commands.req_status(contact, timeout=STATUS_TIMEOUT_SEC)
            if result.type == EventType.ERROR:
                raise RuntimeError(f"req_status failed: {result.payload}")

            data = result.payload
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
                    if REPEATER_PASSWORD:
                        await self.login_if_needed(force=True)
                except Exception as recover_error:
                    log(f"Recovery failed: {recover_error}")

            if self.consecutive_failures >= 4:
                try:
                    await self.connect(force_reconnect=True)
                except Exception as reconnect_error:
                    log(f"Reconnect failed: {reconnect_error}")

            return ("error", None, msg, fallback_node)

async def run_poll(session: MeshStatusSession, next_poll_at: str | None) -> None:
    started_ts = now_str()
    safe_update_meta(
        node=META_NODE,
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

    safe_update_meta(
        node=node,
        poll_state="idle",
        last_poll_finished_ts=finished_ts,
        next_poll_at=next_poll_at,
    )


async def run_oneshot() -> None:
    session = MeshStatusSession()
    try:
        next_poll_at = get_next_poll_at_from_systemd()
        await run_poll(session, next_poll_at=next_poll_at)
    finally:
        await session.close()


async def run_loop() -> None:
    session = MeshStatusSession()
    deadline = time.monotonic()

    try:
        while True:
            deadline += POLL_INTERVAL_SEC
            next_poll_at_dt = datetime.now() + timedelta(seconds=max(0, deadline - time.monotonic()))
            await run_poll(session, next_poll_at=fmt_ts(next_poll_at_dt))
            sleep_for = max(0.0, deadline - time.monotonic())
            await asyncio.sleep(sleep_for)
    finally:
        await session.close()


def main() -> None:
    if RUN_MODE == "oneshot":
        asyncio.run(run_oneshot())
    else:
        asyncio.run(run_loop())


if __name__ == "__main__":
    main()
