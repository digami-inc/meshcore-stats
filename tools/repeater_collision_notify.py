#!/usr/bin/env python3
import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import List

import pymysql
from meshcore import EventType, MeshCore

DEFAULT_ENV_FILE = os.getenv(
    "ENV_FILE",
    "/etc/repeater-status-ingest/repeater-collision-notify.env",
)

def load_env_file(env_path: str) -> None:
    path = Path(env_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        os.environ.setdefault(key, value)

load_env_file(DEFAULT_ENV_FILE)

DB_HOST = os.getenv("DB_HOST", "10.50.68.87")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "repeater_status")
DB_USER = os.getenv("DB_USER", "repeater_writer")
DB_PASS = os.getenv("DB_PASS", "")

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
CHANNEL_IDX = int(os.getenv("CHANNEL_IDX", "2"))
PRE_SEND_SLEEP_SEC = float(os.getenv("PRE_SEND_SLEEP_SEC", "6"))
SEND_DELAY_SEC = float(os.getenv("SEND_DELAY_SEC", "8"))
HEADER_TEXT = os.getenv("HEADER_TEXT", "First byte collision groups")
FOOTER_TEMPLATE = os.getenv("FOOTER_TEMPLATE", "Transmission end, ({count}) collision groups for today")


def db_connect():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def load_collision_messages(limit: int | None = None) -> List[str]:
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    first_byte,
                    contact_name,
                    current_pubkey_pre,
                    first_seen_ts
                FROM repeater_contact_current
                WHERE first_byte IN (
                    SELECT first_byte
                    FROM repeater_contact_current
                    GROUP BY first_byte
                    HAVING COUNT(*) > 1
                )
                ORDER BY first_byte ASC, first_seen_ts ASC, contact_name ASC
                """
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    grouped: dict[str, list[str]] = {}
    for row in rows:
        first_byte = str(row.get("first_byte") or "").upper()
        contact_name = str(row.get("contact_name") or "").strip()
        if not first_byte or not contact_name:
            continue
        grouped.setdefault(first_byte, []).append(contact_name)

    messages: List[str] = []
    for first_byte, names in grouped.items():
        if len(names) < 2:
            continue
        messages.append(f"({first_byte}) " + " <-> ".join(names))
    if limit is not None:
        return messages[:limit]
    return messages


async def send_messages(messages: List[str]) -> None:
    print(f"Using SERIAL_PORT={SERIAL_PORT}")
    print(f"Using CHANNEL_IDX={CHANNEL_IDX}")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"{HEADER_TEXT} {ts}"
    footer = FOOTER_TEMPLATE.format(count=len(messages))
    payload = [header] + messages + [footer]
    mesh = await MeshCore.create_serial(SERIAL_PORT, debug=True)
    try:
        await asyncio.sleep(PRE_SEND_SLEEP_SEC)
        for idx, message in enumerate(payload, start=1):
            result = await mesh.commands.send_chan_msg(CHANNEL_IDX, message)
            print(f"[{idx}/{len(payload)}] {message}")
            print(f"  event_type={result.type} payload={result.payload}")
            if result.type == EventType.ERROR:
                raise RuntimeError(f"send_chan_msg failed for: {message}")
            if idx < len(payload):
                await asyncio.sleep(SEND_DELAY_SEC)
    finally:
        await mesh.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send repeater first-byte collision report to MeshCore channel")
    parser.add_argument("--send", action="store_true", help="Actually send messages to MeshCore channel")
    parser.add_argument("--limit", type=int, default=None, help="Send only first N collision groups")
    args = parser.parse_args()

    messages = load_collision_messages(limit=args.limit)

    print(f"Collision groups: {len(messages)}")
    for msg in messages:
        print(msg)

    if not args.send:
        return

    asyncio.run(send_messages(messages))


if __name__ == "__main__":
    main()
