#!/usr/bin/env python3
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import repeater_status_ingest as base

MESHCLI_FALLBACK_HOME = Path(os.getenv("MESHCLI_FALLBACK_HOME", "/tmp/meshcli-fallback"))


def extract_neighbour_payload(payload: Any) -> tuple[list[dict], dict[str, Any]]:
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


def parse_expected_count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def patched_close(self) -> None:
    if self.meshcore is not None:
        try:
            await self.meshcore.disconnect()
        except Exception as e:
            base.log(f"Disconnect failed: {e}")
        self.meshcore = None
    self.contact = None
    self.contact_prefix_map = []
    self.last_contact_refresh = 0.0
    self.last_login_ts = 0.0
    self.login_success_sub = None
    self.login_failed_sub = None


def build_neighbour_rows(self, payload: Any, fallback_node: str) -> tuple[list[dict], str]:
    neighbours, meta = extract_neighbour_payload(payload)
    repeater_node = str(
        meta.get("pubkey_prefix")
        or meta.get("pubkey_pre")
        or meta.get("pubkey")
        or fallback_node
    )

    results_count = parse_expected_count(meta.get("results_count"))
    neighbours_count = parse_expected_count(meta.get("neighbours_count"))

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


async def collect_neighbours_via_meshcore(self, contact: dict) -> tuple[list[dict], str]:
    if self.meshcore is None:
        raise RuntimeError("MeshCore is not connected")

    fetch_all_neighbours = getattr(self.meshcore.commands, "fetch_all_neighbours", None)
    if not callable(fetch_all_neighbours):
        raise RuntimeError("meshcore.commands.fetch_all_neighbours is not available")

    try:
        result = await fetch_all_neighbours(contact, timeout=base.NEIGHBOURS_TIMEOUT_SEC)
    except TypeError:
        result = await fetch_all_neighbours(contact)

    if getattr(result, "type", None) == base.EventType.ERROR:
        raise RuntimeError(f"fetch_all_neighbours failed: {getattr(result, 'payload', None)}")

    payload = getattr(result, "payload", result)
    fallback_node = base.get_contact_prefix(contact)
    return self.build_neighbour_rows(payload, fallback_node)


async def run_meshcli_json(self, *args: str, timeout: float) -> dict:
    cmd = [base.MESHCLI_BIN, "-j", "-s", base.SERIAL_PORT, *args]
    base.log(f"Running meshcli JSON command: {' '.join(repr(x) for x in cmd[4:])}")

    def _run() -> subprocess.CompletedProcess[str]:
        MESHCLI_FALLBACK_HOME.mkdir(parents=True, exist_ok=True)
        xdg_config_home = MESHCLI_FALLBACK_HOME / ".config"
        xdg_config_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(MESHCLI_FALLBACK_HOME)
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
        return base.extract_json(result.stdout)
    except Exception as e:
        raise RuntimeError(
            f"meshcli returned no valid JSON: {e}; stdout={result.stdout!r}, stderr={result.stderr!r}"
        ) from e


async def run_meshcli_json_exclusive(self, *args: str, timeout: float) -> dict:
    had_session = self.meshcore is not None and self.meshcore.is_connected
    if had_session:
        base.log("Closing persistent MeshCore session before meshcli fallback to avoid serial-port contention")
        await self.close()

    try:
        return await self.run_meshcli_json(*args, timeout=timeout)
    finally:
        if had_session:
            try:
                await self.connect(force_reconnect=True)
            except Exception as e:
                base.log(f"Reconnect after meshcli fallback failed: {e}")


async def collect_neighbours(self) -> tuple[str, list[dict] | None, str | None, str]:
    await self.connect()
    contact = await self.ensure_contact(force_refresh=False)
    fallback_node = base.get_contact_prefix(contact)
    last_error_text = None

    max_attempts = max(1, base.NEIGHBOURS_MAX_ATTEMPTS)

    for attempt in range(1, max_attempts + 1):
        try:
            contact = await self.ensure_contact(force_refresh=(attempt > 1))
            rows, repeater_node = await self.collect_neighbours_via_meshcore(contact)
            self.schedule_next_neighbours()
            base.log(
                f"Collected {len(rows)} neighbours via meshcore session for {base.REPEATER_NAME} ({repeater_node}) "
                f"on attempt {attempt}/{max_attempts}"
            )
            return ("valid", rows, None, repeater_node)

        except Exception as direct_error:
            base.log(
                f"Direct MeshCore neighbour collection failed on attempt {attempt}/{max_attempts}: "
                f"{direct_error}"
            )

            try:
                payload = await self.run_meshcli_json_exclusive(
                    "req_neighbours",
                    base.REPEATER_NAME,
                    timeout=base.NEIGHBOURS_TIMEOUT_SEC,
                )
                rows, repeater_node = self.build_neighbour_rows(payload, fallback_node)
                self.schedule_next_neighbours()
                base.log(
                    f"Collected {len(rows)} neighbours via meshcli fallback for {base.REPEATER_NAME} ({repeater_node}) "
                    f"on attempt {attempt}/{max_attempts}"
                )
                return ("valid", rows, None, repeater_node)
            except Exception as fallback_error:
                err_text = (
                    f"direct meshcore failed: {direct_error}; "
                    f"meshcli fallback failed: {fallback_error}"
                )
                err_kind = base.classify_neighbours_error(err_text)
                last_error_text = base.shorten_error(err_text)
                base.log(
                    f"collect_neighbours attempt {attempt}/{max_attempts} failed "
                    f"[{err_kind}]: {err_text}"
                )

            if attempt >= max_attempts:
                break

            delay = base.neighbour_retry_delay()
            base.log(f"Retrying neighbour collection in {delay:.1f}s")
            await asyncio.sleep(delay)

    self.schedule_next_neighbours()
    return ("error", None, last_error_text, fallback_node)


def apply_patches() -> None:
    base.MeshStatusSession.close = patched_close
    base.MeshStatusSession.build_neighbour_rows = build_neighbour_rows
    base.MeshStatusSession.collect_neighbours_via_meshcore = collect_neighbours_via_meshcore
    base.MeshStatusSession.run_meshcli_json = run_meshcli_json
    base.MeshStatusSession.run_meshcli_json_exclusive = run_meshcli_json_exclusive
    base.MeshStatusSession.collect_neighbours = collect_neighbours


if __name__ == "__main__":
    apply_patches()
    base.main()
