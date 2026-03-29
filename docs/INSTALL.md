# meshcore-stats on Linux

## Overview

`meshcore-stats` is a Linux-based telemetry stack for a MeshCore repeater.

It has three main parts:

- **collector** — polls repeater status and neighbour data from a MeshCore companion radio
- **UI** — a Flask web app that reads the database and shows live status and charts
- **SQL** — MariaDB/MySQL schema used by the collector and UI

## Technologies used

- Python 3
- Flask
- PyMySQL
- meshcore
- meshcore-cli / meshcli
- systemd
- Chart.js

## Repository layout

- `collector/` — collector code and collector service files
- `ui/` — UI code and UI service files
- `sql/` — database schema and future migrations
- `docs/` — operational documentation

## How it works

1. The collector connects to the MeshCore companion over serial.
2. It polls repeater status on a regular interval.
3. It also collects neighbour snapshots on a slower schedule.
4. The collector writes telemetry and poll metadata into MariaDB/MySQL.
5. The UI reads the database and serves a local dashboard over HTTP.

## Requirements

- Linux host
- Python 3.10+ recommended
- MariaDB or MySQL
- Access to the MeshCore companion device for the collector host
- systemd recommended

## Install

### 1. Install base packages

sudo apt update
sudo apt install -y git python3 python3-venv

2. Clone the repository
git clone https://github.com/digami-inc/meshcore-stats.git
cd meshcore-stats

3. Create a virtual environment
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

4. Create the database schema
mysql -u <DB_USER> -p <DB_NAME> < sql/001_repeater_status_schema.sql

Example:

mysql -u root -p repeater_status < sql/001_repeater_status_schema.sql

### Collector deployment

Collector code:

collector/app/repeater_status_ingest.py

Sample systemd unit:

collector/systemd/repeater-status-ingest-loop.service

Typical environment variables:

SERIAL_PORT
REPEATER_NAME
REPEATER_PASSWORD
DB_HOST
DB_PORT
DB_NAME
DB_USER
DB_PASS
NODE

Adjust paths and environment file locations for your host before enabling the service.

### UI deployment

UI code:
ui/app/tower_ui_app.py

Sample systemd unit:

ui/systemd/tower-ui-app.service

Typical environment variables:

DB_HOST
DB_PORT
DB_NAME
DB_USER
DB_PASS
NODE

The UI serves on local HTTP and is typically reverse-proxied by nginx or another web server.

### Suggested deployment model

Use two separate Linux services:

one service for the collector on the host that has access to the MeshCore serial device
one service for the UI on the host that has access to the database

They may run on the same machine or on separate machines.

Updating
git pull
. .venv/bin/activate
python3 -m pip install -r requirements.txt

Then restart the relevant service:

sudo systemctl restart repeater-status-ingest-loop.service
sudo systemctl restart tower-ui-app.service


### Notes
Keep passwords and host-specific environment files out of git.
Keep database schema changes in new numbered SQL files after 001_repeater_status_schema.sql.
If you change runtime paths in systemd unit files, document them locally.
