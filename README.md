# meshcore-stats

Telemetry, contact tracking, and UI tools for the Inciema tower MeshCore project.

## What this repo does

This project collects repeater telemetry and MeshCore contact information from a companion node, stores the data in MySQL, shows the data in a web UI, and can publish a daily collision summary back into the mesh.

Current scope includes:

- multi-target repeater telemetry polling
- repeater and full contact snapshot storage in MySQL
- web dashboard with repeater switcher
- repeater first-byte collision detection
- contact prefix lookup utility
- daily MeshCore channel notification for collision groups

## High-level flow

```text
MeshCore companion
  -> collector/app/repeater_status_ingest_multi.py
  -> MySQL (repeater_status)
  -> ui/app/tower_ui_app.py
  -> optional daily notifier: tools/repeater_collision_notify.py
```

## Repository layout

- `collector/` - multi-target ingest and collector-side examples
- `ui/` - Flask UI application
- `sql/` - schema and migration files
- `tools/` - operational helper scripts
- `docs/` - notes and deployment docs

## Main components

### Collector

Main collector:

- `collector/app/repeater_status_ingest_multi.py`

Responsibilities:

- connects to a MeshCore companion
- refreshes contacts from the companion
- polls multiple repeater targets from one collector instance
- stores repeater telemetry into MySQL
- stores repeater-only contact snapshots
- stores full MeshCore contact snapshots
- prunes stale contact records after the configured retention period

Important env values:

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASS`
- `SERIAL_PORT`
- `SERIAL_BAUD`
- `TARGETS_FILE`
- `CONTACT_SNAPSHOT_INTERVAL_SEC`
- `CONTACT_PRUNE_MAX_AGE_DAYS`
- `CONTACT_SOURCE_ID`

### UI

Main UI app:

- `ui/app/tower_ui_app.py`

Current UI features:

- repeater switcher
- KPI cards and charts
- repeater first-byte collision panel
- collapsible collisions and neighbours sections
- contact prefix lookup utility under the collisions section

Prefix lookup rules:

- accepts 2 or 4 hex characters
- searches all stored contacts, not only repeaters
- returns matching contact names and pubkey prefixes

### Daily MeshCore notifier

Main script:

- `tools/repeater_collision_notify.py`

Responsibilities:

- reads current collision groups from MySQL
- formats one message per collision group
- sends a header, all collision messages, and a footer to a MeshCore channel
- uses conservative send timing for reliable delivery

Expected runtime config is provided by a dedicated env file, for example:

- `/etc/repeater-status-ingest/repeater-collision-notify.env`

Typical values include:

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASS`
- `SERIAL_PORT=/dev/ttyACM0`
- `CHANNEL_IDX=2`
- `PRE_SEND_SLEEP_SEC=6`
- `SEND_DELAY_SEC=8`

## Database objects

Main telemetry tables are used by the collector and UI.

Contact-related tables added for collision tracking and lookup:

- `repeater_contact_current`
  - current repeater-only contact snapshot
  - used for first-byte collision grouping
  - keeps `first_seen_ts` and `last_seen_ts`
  - stale records are removed automatically after the configured retention window

- `meshcore_contact_current`
  - current snapshot of all MeshCore contacts
  - used by the UI contact prefix lookup tool
  - keeps `first_seen_ts` and `last_seen_ts`
  - stale records are removed automatically after the configured retention window

Migrations currently include:

- `sql/002_repeater_contact_current.sql`
- `sql/003_meshcore_contact_current.sql`

## Collision logic

### Repeater first-byte collisions

The UI collision panel groups current repeater contacts by the first byte of the public key.

Example:

```text
(8A) NG26 Vecais Bebrs <-> StraupesPils
```

Rules:

- repeater collision tracking uses repeater contacts only
- ordering inside a collision group is based on `first_seen_ts`, then name
- if the same repeater name stays the same but the pubkey changes, the current record is updated
- stale records are removed automatically after the retention threshold

### Contact prefix lookup

The prefix lookup utility searches all current contacts using the beginning of `current_pubkey`.

Examples:

- `8A`
- `8AEE`

This is intended as an operator aid when trying to identify names behind a one-byte or two-byte pubkey prefix.

## Services and timers

Host-specific service names can vary, but the typical layout is:

- collector service:
  - `repeater-status-ingest-multi-lab.service`
- UI service:
  - `tower-ui-app.service`
- daily collision notifier:
  - `repeater-collision-notify.service`
  - `repeater-collision-notify.timer`

Example timer behavior:

- runs daily at `08:00`
- triggers the notifier service
- sends collision messages into the configured MeshCore channel

## Deployment notes

Typical change flow:

1. apply SQL migration on the MySQL host
2. grant DB permissions for any new tables
3. deploy collector changes and restart collector service
4. verify new rows appear in MySQL
5. deploy UI changes and restart UI service
6. verify API output with `curl`
7. deploy notifier changes and test manual send
8. enable or restart the notifier timer

## Secrets

Do not commit:

- passwords
- private keys
- host-specific env files
- local SSH keys
- temporary `.bak` files

Use example env files or deployment-specific files outside the repo.
