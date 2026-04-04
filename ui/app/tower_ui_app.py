#!/usr/bin/env python3
import os
from datetime import datetime, timedelta
from statistics import median

import pymysql
from flask import Flask, jsonify, render_template_string, request

DB_HOST = os.getenv("DB_HOST", "10.50.68.87")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "repeater_status")
DB_USER = os.getenv("DB_USER", "repeater_reader")
DB_PASS = os.getenv("DB_PASS", "")
DEFAULT_NODE = os.getenv("NODE", "1385fef9d37e")
REPEATER_OPTIONS = os.getenv(
    "REPEATER_OPTIONS",
    "1385fef9d37e:Inciema Tornis:repeater_status,8aee80843dec:Straupes Pils:repeater_status",
)

app = Flask(__name__)


def parse_repeater_options(raw: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue

        parts = [part.strip() for part in chunk.split(":", 2)]
        node = (parts[0] if len(parts) >= 1 else "").lower()
        name = (parts[1] if len(parts) >= 2 and parts[1] else node)
        db_name = (parts[2] if len(parts) >= 3 and parts[2] else DB_NAME)

        if not node or node in seen:
            continue

        seen.add(node)
        out.append({"node": node, "name": name, "db_name": db_name})

    if not out:
        out.append({"node": DEFAULT_NODE.lower(), "name": DEFAULT_NODE.lower(), "db_name": DB_NAME})
    return out

REPEATERS = parse_repeater_options(REPEATER_OPTIONS)
REPEATER_MAP = {item["node"]: item for item in REPEATERS}


def get_requested_node() -> str:
    requested = (request.args.get("node") or DEFAULT_NODE).strip().lower()
    if requested in REPEATER_MAP:
        return requested
    return DEFAULT_NODE.lower()


def get_requested_repeater() -> dict:
    node = get_requested_node()
    return REPEATER_MAP.get(node) or REPEATERS[0]
HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Repeater Telemetry Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg-1: #0b1220;
      --bg-2: #0f172a;
      --panel: #172235;
      --panel-2: #1e293b;
      --line: rgba(148, 163, 184, 0.16);
      --text: #e2e8f0;
      --muted: #94a3b8;
      --good: #22c55e;
      --warn: #eab308;
      --bad: #ef4444;
      --gray: #64748b;
      --accent: #60a5fa;
    }

    * { box-sizing: border-box; }

    body {
      font-family: Verdana, sans-serif;
      margin: 0;
      padding: 16px;
      background:
        radial-gradient(circle at top, rgba(96,165,250,.10), transparent 30%),
        linear-gradient(180deg, var(--bg-1), var(--bg-2));
      color: var(--text);
    }

    .wrap {
      max-width: 1180px;
      margin: 0 auto;
    }

    .page-head {
      text-align: center;
      margin-bottom: 16px;
    }

    h1 {
      margin: 0 0 6px 0;
      font-size: 34px;
      line-height: 1.15;
      letter-spacing: .01em;
    }

    .lead {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }

    .repeater-switcher {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin: 0 auto 14px auto;
      max-width: 760px;
    }

    .repeater-tile {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-radius: 14px;
      background: linear-gradient(180deg, rgba(30,41,59,.78), rgba(23,34,53,.86));
      border: 1px solid rgba(148,163,184,.12);
      box-shadow: 0 8px 22px rgba(0,0,0,.16);
      cursor: pointer;
      transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease, background .16s ease;
    }

    .repeater-tile:hover {
      transform: translateY(-1px);
      border-color: rgba(96,165,250,.35);
      box-shadow: 0 10px 24px rgba(0,0,0,.22);
    }

    .repeater-tile.active {
      border-color: rgba(96,165,250,.72);
      background: linear-gradient(180deg, rgba(37,99,235,.22), rgba(23,34,53,.96));
      box-shadow: 0 12px 28px rgba(37,99,235,.18);
    }

    .repeater-tile-main {
      min-width: 0;
    }

    .repeater-tile-name {
      font-size: 15px;
      font-weight: 700;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .repeater-tile-sub {
      margin-top: 3px;
      font-size: 10px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .repeater-tile-sub:empty {
      display: none;
    }

    .repeater-battery-dot {
      width: 11px;
      height: 11px;
      border-radius: 50%;
      background: var(--gray);
      box-shadow: 0 0 10px rgba(255,255,255,.10);
      flex: 0 0 auto;
    }

    .repeater-battery-dot.green { background: var(--good); }
    .repeater-battery-dot.yellow { background: var(--warn); }
    .repeater-battery-dot.red { background: var(--bad); }
    .repeater-battery-dot.gray { background: var(--gray); }

    .cards {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 12px;
      align-items: stretch;
    }

    .card {
      background: linear-gradient(180deg, rgba(30,41,59,.95), rgba(23,34,53,.96));
      border-radius: 14px;
      padding: 14px 14px 12px 14px;
      box-shadow: 0 8px 28px rgba(0,0,0,.20);
      border: 1px solid rgba(148,163,184,.10);
      border-top: 3px solid rgba(148,163,184,.35);
      text-align: center;
      min-height: 126px;
      transition: border-color .2s ease, box-shadow .2s ease;
    }

    .card.status-green {
      border-top-color: rgba(34,197,94,.95);
      box-shadow: 0 10px 28px rgba(34,197,94,.08);
    }

    .card.status-yellow {
      border-top-color: rgba(234,179,8,.95);
      box-shadow: 0 10px 28px rgba(234,179,8,.08);
    }

    .card.status-red {
      border-top-color: rgba(239,68,68,.95);
      box-shadow: 0 10px 28px rgba(239,68,68,.10);
    }

    .card.status-gray {
      border-top-color: rgba(100,116,139,.85);
    }

    .label {
      font-size: 11px;
      color: var(--muted);
      margin-bottom: 8px;
      letter-spacing: .02em;
      text-transform: uppercase;
    }

    .value {
      font-size: 22px;
      font-weight: 700;
      line-height: 1.15;
    }

    .value-row {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }

    .trend-arrow {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      color: var(--muted);
      position: relative;
      top: 1px;
      flex: 0 0 auto;
    }

    .trend-arrow svg {
      display: block;
      width: 16px;
      height: 16px;
    }

    .trend-arrow.up {
      color: var(--good);
    }

    .trend-arrow.down {
      color: var(--bad);
    }

    .trend-arrow.neutral {
      color: var(--muted);
      opacity: .45;
    }

    .trend-arrow.hidden {
      display: none;
    }

    .meta {
      font-size: 11px;
      color: var(--muted);
      margin-top: 8px;
      min-height: 15px;
      line-height: 1.35;
    }

    .statusline {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      margin-top: 8px;
      color: #cbd5e1;
      flex-wrap: wrap;
    }

    .dot {
      width: 11px;
      height: 11px;
      border-radius: 50%;
      display: inline-block;
      box-shadow: 0 0 10px rgba(255,255,255,.10);
      background: var(--gray);
    }

    .dot.green { background: var(--good); }
    .dot.yellow { background: var(--warn); }
    .dot.red { background: var(--bad); }
    .dot.gray { background: var(--gray); }

    .pill {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: .02em;
      background: rgba(51,65,85,.95);
      color: var(--text);
      border: 1px solid rgba(148,163,184,.12);
      white-space: nowrap;
    }

    .pill.green { background: rgba(34,197,94,.18); color: #86efac; }
    .pill.yellow { background: rgba(234,179,8,.18); color: #fde047; }
    .pill.red { background: rgba(239,68,68,.18); color: #fca5a5; }
    .pill.gray { background: rgba(100,116,139,.25); color: #cbd5e1; }

    .mode-inline {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-left: 6px;
      color: var(--muted);
      font-size: 9px;
    }

    .switch {
      position: relative;
      display: inline-block;
      width: 34px;
      height: 18px;
    }

    .switch input {
      opacity: 0;
      width: 0;
      height: 0;
    }

    .slider {
      position: absolute;
      inset: 0;
      cursor: pointer;
      background: #334155;
      transition: .2s;
      border-radius: 999px;
      border: 1px solid #475569;
    }

    .slider:before {
      content: "";
      position: absolute;
      height: 12px;
      width: 12px;
      left: 2px;
      top: 2px;
      background: var(--text);
      transition: .2s;
      border-radius: 50%;
    }

    .switch input:checked + .slider:before {
      transform: translateX(16px);
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 12px;
      color: #cbd5e1;
    }

    .toolbar-item {
      font-size: 11px;
      color: var(--muted);
      background: rgba(30,41,59,.55);
      border: 1px solid rgba(148,163,184,.10);
      border-radius: 999px;
      padding: 5px 9px;
    }

    .neighbors-panel {
      background: linear-gradient(180deg, rgba(26,38,58,.96), rgba(18,28,44,.98));
      border-radius: 14px;
      padding: 10px 12px;
      box-shadow: 0 8px 28px rgba(0,0,0,.20);
      border: 1px solid rgba(148,163,184,.10);
      margin-bottom: 14px;
    }

    .neighbors-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 8px;
    }

    .neighbors-title {
      margin: 0;
      font-size: 13px;
      font-weight: 700;
    }

    .neighbors-meta {
      font-size: 10px;
      color: var(--muted);
    }

    .neighbors-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
    }

    .neighbor-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      background: rgba(30,41,59,.50);
      border: 1px solid rgba(148,163,184,.08);
      border-radius: 9px;
      padding: 7px 10px;
    }

    .neighbor-main {
      min-width: 0;
    }

    .neighbor-name {
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .neighbor-id {
      margin-top: 2px;
      font-size: 10px;
      color: var(--muted);
    }

    .neighbor-snr, .neighbor-seen {
      font-size: 11px;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }

    .neighbor-snr {
      font-weight: 700;
    }

    .neighbors-empty {
      background: rgba(30,41,59,.50);
      border: 1px solid rgba(148,163,184,.08);
      border-radius: 9px;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 11px;
    }

    .range-row {
      display: flex;
      justify-content: center;
      gap: 14px;
      margin-bottom: 14px;
      color: #cbd5e1;
      font-size: 12px;
      flex-wrap: wrap;
    }

    .range-row label {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      cursor: pointer;
    }

    .chartbox {
      background: linear-gradient(180deg, rgba(26,38,58,.96), rgba(18,28,44,.98));
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 8px 28px rgba(0,0,0,.20);
      border: 1px solid rgba(148,163,184,.10);
      margin-bottom: 14px;
    }

    .chart-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 8px;
    }

    h2 {
      margin: 0;
      font-size: 14px;
    }

    .chart-meta {
      font-size: 11px;
      color: var(--muted);
    }

    canvas {
      max-height: 260px;
    }

    .page-footer {
      margin-top: 18px;
      text-align: center;
      font-size: 11px;
      color: var(--muted);
      opacity: .85;
    }

    @media (max-width: 980px) {
      .cards { grid-template-columns: repeat(2, 1fr); }
      .neighbors-list { grid-template-columns: 1fr; }
    }

    @media (max-width: 760px) {
      .repeater-switcher { grid-template-columns: 1fr; }
    }

    @media (max-width: 620px) {
      body { padding: 10px; }
      h1 { font-size: 26px; }
      .cards { grid-template-columns: 1fr; }
      .card { min-height: 118px; }
      .neighbor-row { grid-template-columns: 1fr auto; }
      .neighbor-seen { grid-column: 1 / -1; color: var(--muted); }
    }
  </style>
</head>
<body>
<div class="wrap">
  <div class="page-head">
    <h1>Repeater Telemetry Dashboard</h1>
    <p class="lead">Live repeater health, RF environment, and link quality.</p>
  </div>

  <div class="repeater-switcher" id="repeater-switcher">
    <div class="repeater-tile active">
      <div class="repeater-tile-main">
        <div class="repeater-tile-name">Loading…</div>
        <div class="repeater-tile-sub">Please wait</div>
      </div>
      <span class="repeater-battery-dot gray"></span>
    </div>
  </div>

  <div class="cards">
    <div class="card status-gray" id="battery-card">
      <div class="label" id="battery-label">Battery</div>
      <div class="value-row">
        <div class="value" id="battery">--</div>
        <div class="trend-arrow hidden neutral" id="battery-trend-arrow">↑</div>
      </div>
      <div class="meta" id="battery-meta">24h trend: --</div>
      <div class="statusline">
        <span id="battery-dot" class="dot gray"></span>
        <span id="battery-state" class="pill gray">no data</span>
        <div class="mode-inline">
          <span>V</span>
          <label class="switch">
            <input type="checkbox" id="battery-mode-toggle">
            <span class="slider"></span>
          </label>
          <span>%</span>
        </div>
      </div>
    </div>

    <div class="card status-gray" id="noise-card">
      <div class="label">Noise Floor</div>
      <div class="value" id="noise-floor">--</div>
      <div class="meta" id="noise-meta">baseline: --</div>
      <div class="statusline">
        <span id="noise-state" class="pill gray">no data</span>
      </div>
    </div>

    <div class="card status-gray" id="margin-card">
      <div class="label">Poll Link</div>
      <div class="value" id="link-margin">--</div>
      <div class="meta" id="link-meta">RSSI -- | SNR --</div>
      <div class="statusline">
        <span id="margin-state" class="pill gray">no data</span>
      </div>
    </div>

    <div class="card status-gray" id="uptime-card">
      <div class="label">Repeater Uptime</div>
      <div class="value" id="uptime">--</div>
      <div class="meta" id="uptime-meta">last reboot: --</div>
      <div class="statusline">
        <span id="uptime-state" class="pill gray">no data</span>
      </div>
    </div>
  </div>


  <div class="toolbar">
    <span id="freshness-pill" class="pill gray">no data</span>
    <span class="toolbar-item" id="poll-summary">Polls 24h: --</span>
  </div>

  <div class="neighbors-panel">
    <div class="neighbors-head">
      <div class="neighbors-title">Neighbours</div>
      <div class="neighbors-meta" id="neighbors-meta">snapshot: -- • count: --</div>
    </div>
    <div class="neighbors-list" id="neighbors-list">
      <div class="neighbors-empty">No neighbour data</div>
    </div>
  </div>

  <div class="range-row">
    <label><input type="radio" name="chart-range" value="day"> Day</label>
    <label><input type="radio" name="chart-range" value="week" checked> Week</label>
    <label><input type="radio" name="chart-range" value="month"> Month</label>
    <label><input type="radio" name="chart-range" value="year"> Year</label>
  </div>

  <div class="chartbox">
    <div class="chart-head">
      <h2 id="battery-chart-title">Battery — week</h2>
      <div class="chart-meta" id="battery-chart-meta">--</div>
    </div>
    <canvas id="batteryChart" height="120"></canvas>
  </div>

  <div class="chartbox">
    <div class="chart-head">
      <h2 id="noise-chart-title">Noise Floor — week</h2>
      <div class="chart-meta" id="noise-chart-meta">--</div>
    </div>
    <canvas id="noiseChart" height="120"></canvas>
  </div>

  <div class="chartbox">
    <div class="chart-head">
      <h2 id="margin-chart-title">Poll Link — week</h2>
      <div class="chart-meta" id="margin-chart-meta">--</div>
    </div>
    <canvas id="marginChart" height="120"></canvas>
  </div>

  <div class="chartbox">
    <div class="chart-head">
      <h2 id="snr-chart-title">Last SNR — week</h2>
      <div class="chart-meta" id="snr-chart-meta">--</div>
    </div>
    <canvas id="snrChart" height="120"></canvas>
  </div>
  <div class="page-footer">&copy; Digami 2026</div>
</div>

<script>
let batteryChart;
let noiseChart;
let marginChart;
let snrChart;

let repeaters = [];
let currentNode = localStorage.getItem('selectedRepeaterNode') || null;

let serverTimeMs = null;
let lastRecordTs = null;
let lastRecordText = null;

let latestBatteryMv = null;
let latestUptimeSecs = null;
let latestNoiseFloorDbm = null;
let latestRssiDbm = null;
let latestSnrDb = null;
let latestLinkMarginDb = null;
let latestNextPollAt = null;
let latestPollState = null;
let latestLastPollStartedTs = null;
let latestLastPollFinishedTs = null;
let latestLastPollStatus = null;
let latestLastPollIsValid = null;
let latestPollTotal24h = null;
let latestPollValid24h = null;
let latestPollSuccessRate24h = null;
let latestNeighbours = [];
let latestNeighboursCollectedTs = null;

let battery24hMinV = null;
let battery24hMaxV = null;
let latestBatteryTrendDirection = null;
let noiseFloorBaselineDbm = null;
let linkMarginBaselineDb = null;
let snrBaselineDb = null;
let lastRebootText = null;

let batteryDisplayMode = localStorage.getItem('batteryDisplayMode') || 'v';
let currentRange = localStorage.getItem('chartRange') || 'week';
let nextLatestPollSec = null;

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const thresholdBandsPlugin = {
  id: 'thresholdBands',
  beforeDraw(chart, args, pluginOptions) {
    const bands = pluginOptions && Array.isArray(pluginOptions.bands) ? pluginOptions.bands : [];
    if (!bands.length) return;

    const { ctx, chartArea, scales } = chart;
    const y = scales.y;
    if (!chartArea || !y) return;

    ctx.save();
    for (const band of bands) {
      const fromPx = y.getPixelForValue(band.from);
      const toPx = y.getPixelForValue(band.to);
      const top = Math.min(fromPx, toPx);
      const bottom = Math.max(fromPx, toPx);
      ctx.fillStyle = band.color;
      ctx.fillRect(chartArea.left, top, chartArea.right - chartArea.left, bottom - top);
    }
    ctx.restore();
  }
};

Chart.register(thresholdBandsPlugin);

function fmtDateTime(s) {
  return s || '--';
}

function apiUrl(path, extraParams = {}, includeNode = true) {
  const url = new URL(path, window.location.origin);
  if (includeNode && currentNode) {
    url.searchParams.set('node', currentNode);
  }
  for (const [key, value] of Object.entries(extraParams || {})) {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value);
    }
  }
  return `${url.pathname}${url.search}`;
}

function setCurrentNode(node, persist = true) {
  if (!node) return;
  currentNode = node;
  if (persist) {
    localStorage.setItem('selectedRepeaterNode', currentNode);
  }
  renderRepeaterSwitcher();
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

function parseLocalTs(s) {
  if (!s) return null;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
  if (!m) return null;
  return new Date(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
    Number(m[6])
  );
}

function formatDateObj(dt, withSeconds = false) {
  if (!dt) return '--';
  const base = `${dt.getFullYear()}-${pad2(dt.getMonth() + 1)}-${pad2(dt.getDate())} ${pad2(dt.getHours())}:${pad2(dt.getMinutes())}`;
  return withSeconds ? `${base}:${pad2(dt.getSeconds())}` : base;
}

function formatAxisLabel(s) {
  const dt = parseLocalTs(s);
  if (!dt) return '';
  if (currentRange === 'day') {
    return `${pad2(dt.getHours())}:${pad2(dt.getMinutes())}`;
  }
  if (currentRange === 'year') {
    return `${MONTH_NAMES[dt.getMonth()]} ${dt.getFullYear()}`;
  }
  return `${dt.getDate()} ${MONTH_NAMES[dt.getMonth()]}`;
}

function batteryMvToVolts(mv) {
  if (mv == null || isNaN(mv)) return null;
  return Number(mv) / 1000.0;
}

function batteryVoltsToPercent(voltage) {
  if (voltage == null || isNaN(voltage)) return null;
  const vMin = 3.40;
  const vMax = 4.17;
  let pct = ((voltage - vMin) / (vMax - vMin)) * 100;
  pct = Math.max(0, Math.min(100, pct));
  return Math.round(pct);
}

function computeLinkMargin(rssi, noise) {
  if (rssi == null || noise == null || isNaN(rssi) || isNaN(noise)) return null;
  return Number(rssi) - Number(noise);
}

function formatUptime(totalSec) {
  if (totalSec == null || isNaN(totalSec)) return '--';
  totalSec = Math.max(0, Math.floor(Number(totalSec)));

  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);

  if (days > 0) {
    return `${days}d ${pad2(hours)}h ${pad2(minutes)}m`;
  }
  return `${pad2(hours)}h ${pad2(minutes)}m`;
}

function formatMinutesSeconds(totalSec) {
  if (totalSec == null || isNaN(totalSec)) return '--';
  totalSec = Math.max(0, Math.floor(Number(totalSec)));
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  return `${minutes}m ${pad2(seconds)}s`;
}

function fmtSigned(value, decimals = 1, suffix = '') {
  if (value == null || isNaN(value)) return '--';
  const n = Number(value);
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(decimals)}${suffix}`;
}

function fmtMaybe(value, decimals = 0, suffix = '') {
  if (value == null || isNaN(value)) return '--';
  return `${Number(value).toFixed(decimals)}${suffix}`;
}

function movingAverage(values, windowSize = 5) {
  const out = [];
  for (let i = 0; i < values.length; i++) {
    const start = Math.max(0, i - windowSize + 1);
    const slice = values.slice(start, i + 1).filter(v => v != null && !isNaN(v));
    if (!slice.length) {
      out.push(null);
      continue;
    }
    const avg = slice.reduce((a, b) => a + Number(b), 0) / slice.length;
    out.push(Number(avg.toFixed(3)));
  }
  return out;
}

function constantArray(labels, value) {
  if (value == null || isNaN(value)) return [];
  return labels.map(() => Number(value));
}

function minMax(values) {
  const clean = values.filter(v => v != null && !isNaN(v)).map(Number);
  if (!clean.length) return null;
  return {
    min: Math.min(...clean),
    max: Math.max(...clean),
  };
}

function setPillState(id, level, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'pill';
  el.classList.add(level || 'gray');
  el.textContent = text;
}

function setCardState(id, level) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('status-green', 'status-yellow', 'status-red', 'status-gray');
  el.classList.add(`status-${level || 'gray'}`);
}

function setDotState(id, level) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'dot';
  el.classList.add(level || 'gray');
}


function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatAgoShort(totalSec) {
  if (totalSec == null || isNaN(totalSec)) return '--';
  totalSec = Math.max(0, Math.floor(Number(totalSec)));
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const minutes = Math.floor((totalSec % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function renderRepeaterSwitcher() {
  const el = document.getElementById('repeater-switcher');
  if (!el) return;

  if (!Array.isArray(repeaters) || !repeaters.length) {
    el.innerHTML = `
      <div class="repeater-tile active">
        <div class="repeater-tile-main">
          <div class="repeater-tile-name">Loading…</div>
        </div>
        <span class="repeater-battery-dot gray"></span>
      </div>
    `;
    return;
  }

  el.innerHTML = repeaters.map((item) => {
    const active = item.node === currentNode ? ' active' : '';
    const dot = item.battery_state || 'gray';
    return `
      <div class="repeater-tile${active}" data-node="${escapeHtml(item.node)}">
        <div class="repeater-tile-main">
          <div class="repeater-tile-name">${escapeHtml(item.name || item.node)}</div>
          <div class="repeater-tile-sub"></div>
        </div>
        <span class="repeater-battery-dot ${escapeHtml(dot)}"></span>
      </div>
    `;
  }).join('');

  el.querySelectorAll('.repeater-tile[data-node]').forEach((tile) => {
    tile.addEventListener('click', async () => {
      const node = tile.dataset.node;
      if (!node || node === currentNode) return;
      setCurrentNode(node);
      await loadLatest();
      await refreshHistory();
      await syncServerTime();
      await loadRepeaterSummaries();
    });
  });
}

async function loadRepeaterSummaries() {
  const r = await fetch('/api/repeaters_summary');
  const d = await r.json();

  repeaters = Array.isArray(d.repeaters) ? d.repeaters : [];
  const validNodes = new Set(repeaters.map((x) => x.node));
  const defaultNode = d.default_node || (repeaters[0] && repeaters[0].node) || null;

  if (!currentNode || !validNodes.has(currentNode)) {
    currentNode = validNodes.has(defaultNode) ? defaultNode : (repeaters[0] ? repeaters[0].node : null);
    if (currentNode) {
      localStorage.setItem('selectedRepeaterNode', currentNode);
    }
  }

  renderRepeaterSwitcher();
}

function renderNeighboursPanel() {
  const metaEl = document.getElementById('neighbors-meta');
  const listEl = document.getElementById('neighbors-list');
  if (!metaEl || !listEl) return;

  const rows = Array.isArray(latestNeighbours) ? latestNeighbours : [];
  metaEl.textContent = `snapshot: ${fmtDateTime(latestNeighboursCollectedTs)} • count: ${rows.length}`;

  if (!rows.length) {
    listEl.innerHTML = '<div class="neighbors-empty">No neighbour data</div>';
    return;
  }

  listEl.innerHTML = rows.map((row) => {
    const name = escapeHtml(row.neighbor_name || row.neighbor_pubkey_pre || 'unknown');
    const id = escapeHtml(row.neighbor_pubkey_pre || '--');
    const snr = row.snr_db == null || isNaN(Number(row.snr_db)) ? '--' : `${Number(row.snr_db).toFixed(2)} dB`;
    const seen = row.secs_ago == null || isNaN(Number(row.secs_ago)) ? '--' : `${formatAgoShort(row.secs_ago)} ago`;
    return `
      <div class="neighbor-row">
        <div class="neighbor-main">
          <div class="neighbor-name">${name}</div>
          <div class="neighbor-id">${id}</div>
        </div>
        <div class="neighbor-snr">${snr}</div>
        <div class="neighbor-seen">${seen}</div>
      </div>`;
  }).join('');
}

function renderBatteryDisplay() {
  const el = document.getElementById('battery');
  const labelEl = document.getElementById('battery-label');
  if (!el || !labelEl) return;

  labelEl.textContent = (batteryDisplayMode === 'pct') ? 'Charge Level' : 'Battery';

  const volts = batteryMvToVolts(latestBatteryMv);
  if (volts == null || isNaN(volts)) {
    el.textContent = '--';
    renderBatteryTrendArrow();
    return;
  }

  if (batteryDisplayMode === 'pct') {
    const pct = batteryVoltsToPercent(volts);
    el.textContent = `${pct} %`;
  } else {
    el.textContent = `${volts.toFixed(3)} V`;
  }

  renderBatteryTrendArrow();
}

function renderBatteryTrendArrow() {
  const el = document.getElementById('battery-trend-arrow');
  if (!el) return;

  const svgUp = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2 L13 7 H10 V14 H6 V7 H3 Z" fill="currentColor"/></svg>';
  const svgDown = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2 H10 V9 H13 L8 14 L3 9 H6 Z" fill="currentColor"/></svg>';

  el.className = 'trend-arrow';
  el.innerHTML = svgUp;

  if (batteryDisplayMode !== 'v') {
    el.classList.add('hidden', 'neutral');
    return;
  }

  if (latestBatteryTrendDirection === 'up') {
    el.classList.add('up');
    el.innerHTML = svgUp;
    return;
  }

  if (latestBatteryTrendDirection === 'down') {
    el.classList.add('down');
    el.innerHTML = svgDown;
    return;
  }

  el.classList.add('hidden', 'neutral');
}

function renderBatteryMeta() {
  const el = document.getElementById('battery-meta');
  if (!el) return;
  if (
    battery24hMinV == null || isNaN(battery24hMinV) ||
    battery24hMaxV == null || isNaN(battery24hMaxV)
  ) {
    el.textContent = '24h range: --';
    return;
  }
  el.textContent = `24h range: ${Number(battery24hMinV).toFixed(3)}–${Number(battery24hMaxV).toFixed(3)} V`;
}

function renderNoiseCard() {
  const valueEl = document.getElementById('noise-floor');
  const metaEl = document.getElementById('noise-meta');
  if (valueEl) {
    valueEl.textContent = latestNoiseFloorDbm == null ? '--' : `${Number(latestNoiseFloorDbm).toFixed(0)} dBm`;
  }
  if (metaEl) {
    if (latestNoiseFloorDbm == null || noiseFloorBaselineDbm == null) {
      metaEl.textContent = 'baseline: --';
    } else {
      const delta = Number(latestNoiseFloorDbm) - Number(noiseFloorBaselineDbm);
      metaEl.textContent = `baseline: ${Number(noiseFloorBaselineDbm).toFixed(1)} dBm | ${fmtSigned(delta, 1, ' dB')}`;
    }
  }
}

function renderLinkCard() {
  const valueEl = document.getElementById('link-margin');
  const metaEl = document.getElementById('link-meta');

  if (valueEl) {
    valueEl.textContent = latestLinkMarginDb == null ? '--' : `${Number(latestLinkMarginDb).toFixed(0)} dB`;
  }

  if (metaEl) {
    const rssiText = latestRssiDbm == null ? '--' : `${Number(latestRssiDbm).toFixed(0)} dBm`;
    const snrText = latestSnrDb == null ? '--' : `${Number(latestSnrDb).toFixed(1)} dB`;
    metaEl.textContent = `RSSI ${rssiText} | SNR ${snrText}`;
  }
}

function renderUptimeDisplay() {
  const el = document.getElementById('uptime');
  if (!el) return;
  el.textContent = formatUptime(latestUptimeSecs);
  renderUptimeMeta();
  updateUptimeState();
}

function renderUptimeMeta() {
  const el = document.getElementById('uptime-meta');
  if (!el) return;
  el.textContent = `last reboot: ${lastRebootText || '--'}`;
}

function updateBatteryState(stale) {
  const volts = batteryMvToVolts(latestBatteryMv);

  if (stale || volts == null) {
    setDotState('battery-dot', 'gray');
    setPillState('battery-state', 'gray', stale ? 'stale' : 'no data');
    setCardState('battery-card', 'gray');
    return;
  }

  if (volts >= 3.70) {
    setDotState('battery-dot', 'green');
    setPillState('battery-state', 'green', 'good');
    setCardState('battery-card', 'green');
  } else if (volts >= 3.40) {
    setDotState('battery-dot', 'yellow');
    setPillState('battery-state', 'yellow', 'watch');
    setCardState('battery-card', 'yellow');
  } else {
    setDotState('battery-dot', 'red');
    setPillState('battery-state', 'red', 'low');
    setCardState('battery-card', 'red');
  }
}

function updateNoiseState(stale) {
  if (stale || latestNoiseFloorDbm == null) {
    setPillState('noise-state', 'gray', stale ? 'stale' : 'no data');
    setCardState('noise-card', 'gray');
    return;
  }

  if (noiseFloorBaselineDbm != null) {
    const delta = Number(latestNoiseFloorDbm) - Number(noiseFloorBaselineDbm);
    if (delta <= 2) {
      setPillState('noise-state', 'green', 'quiet');
      setCardState('noise-card', 'green');
    } else if (delta <= 5) {
      setPillState('noise-state', 'yellow', 'raised');
      setCardState('noise-card', 'yellow');
    } else {
      setPillState('noise-state', 'red', 'noisy');
      setCardState('noise-card', 'red');
    }
    return;
  }

  if (Number(latestNoiseFloorDbm) <= -105) {
    setPillState('noise-state', 'green', 'quiet');
    setCardState('noise-card', 'green');
  } else if (Number(latestNoiseFloorDbm) <= -100) {
    setPillState('noise-state', 'yellow', 'fair');
    setCardState('noise-card', 'yellow');
  } else {
    setPillState('noise-state', 'red', 'high');
    setCardState('noise-card', 'red');
  }
}

function updateMarginState(stale) {
  if (stale || latestLinkMarginDb == null) {
    setPillState('margin-state', 'gray', stale ? 'stale' : 'no data');
    setCardState('margin-card', 'gray');
    return;
  }

  const m = Number(latestLinkMarginDb);
  if (m >= 15) {
    setPillState('margin-state', 'green', 'good');
    setCardState('margin-card', 'green');
  } else if (m >= 10) {
    setPillState('margin-state', 'yellow', 'usable');
    setCardState('margin-card', 'yellow');
  } else if (m >= 6) {
    setPillState('margin-state', 'yellow', 'weak');
    setCardState('margin-card', 'yellow');
  } else {
    setPillState('margin-state', 'red', 'poor');
    setCardState('margin-card', 'red');
  }
}

function updateUptimeState() {
  if (latestUptimeSecs == null || isNaN(latestUptimeSecs)) {
    setPillState('uptime-state', 'gray', 'no data');
    setCardState('uptime-card', 'gray');
    return;
  }

  const s = Number(latestUptimeSecs);
  if (s >= 86400) {
    setPillState('uptime-state', 'green', 'stable');
    setCardState('uptime-card', 'green');
  } else if (s >= 3600) {
    setPillState('uptime-state', 'yellow', 'recent reboot');
    setCardState('uptime-card', 'yellow');
  } else {
    setPillState('uptime-state', 'red', 'fresh reboot');
    setCardState('uptime-card', 'red');
  }
}

function updateFreshnessOnly() {
  const freshnessPill = document.getElementById('freshness-pill');
  const STALE_SEC = 1920;
  const FALLBACK_POLL_INTERVAL_SEC = 600;

  let isStale = true;

  if (latestLastPollStartedTs && serverTimeMs !== null) {
    const ageSec = Math.max(0, Math.floor((serverTimeMs - latestLastPollStartedTs.getTime()) / 1000));
    const ageText = formatMinutesSeconds(ageSec);
    isStale = ageSec > STALE_SEC;

    let nextPollText = '--';
    if (latestPollState === 'running') {
      nextPollText = 'polling now';
    } else {
      let targetPollMs = null;

      if (latestNextPollAt) {
        targetPollMs = latestNextPollAt.getTime();
      }

      if ((targetPollMs == null || targetPollMs <= serverTimeMs) && latestLastPollStartedTs) {
        targetPollMs = latestLastPollStartedTs.getTime() + (FALLBACK_POLL_INTERVAL_SEC * 1000);
      }

      if (targetPollMs != null) {
        const nextPollSec = Math.max(0, Math.floor((targetPollMs - serverTimeMs) / 1000));
        nextPollText = formatMinutesSeconds(nextPollSec);
      }
    }

    freshnessPill.className = 'pill';
    if (isStale) {
      freshnessPill.classList.add('red');
      freshnessPill.textContent = `stale • poll age: ${ageText} • next poll in: ${nextPollText}`;
    } else {
      freshnessPill.classList.add('green');
      freshnessPill.textContent = `fresh • poll age: ${ageText} • next poll in: ${nextPollText}`;
    }
  } else {
    freshnessPill.className = 'pill gray';
    freshnessPill.textContent = 'no poll data';
  }

  updateBatteryState(isStale);
  updateNoiseState(isStale);
  updateMarginState(isStale);
}

function updateServerClock() {
  if (serverTimeMs === null) return;
  serverTimeMs += 1000;
  if (latestUptimeSecs != null && !isNaN(latestUptimeSecs)) {
    latestUptimeSecs += 1;
    renderUptimeDisplay();
  }
  if (nextLatestPollSec != null && nextLatestPollSec > 0) {
    nextLatestPollSec -= 1;
  }
  updateFreshnessOnly();
}

async function syncServerTime() {
  const r = await fetch('/api/now');
  const d = await r.json();
  const dt = parseLocalTs(d.now);
  if (dt) {
    serverTimeMs = dt.getTime();
  }
  updateFreshnessOnly();
}

function initBatteryModeToggle() {
  const toggle = document.getElementById('battery-mode-toggle');
  if (!toggle) return;

  toggle.checked = (batteryDisplayMode === 'pct');

  toggle.addEventListener('change', () => {
    batteryDisplayMode = toggle.checked ? 'pct' : 'v';
    localStorage.setItem('batteryDisplayMode', batteryDisplayMode);
    renderBatteryDisplay();
  });
}

function rangeLabel(v) {
  if (v === 'day') return 'day';
  if (v === 'month') return 'month';
  if (v === 'year') return 'year';
  return 'week';
}

function updateRangeTitles() {
  const label = rangeLabel(currentRange);
  document.getElementById('battery-chart-title').textContent = `Battery — ${label}`;
  document.getElementById('noise-chart-title').textContent = `Noise Floor — ${label}`;
  document.getElementById('margin-chart-title').textContent = `Poll Link — ${label}`;
  document.getElementById('snr-chart-title').textContent = `Last SNR — ${label}`;
}

function initRangeSelector() {
  const radios = document.querySelectorAll('input[name="chart-range"]');
  radios.forEach(r => {
    r.checked = (r.value === currentRange);
    r.addEventListener('change', async () => {
      if (!r.checked) return;
      currentRange = r.value;
      localStorage.setItem('chartRange', currentRange);
      updateRangeTitles();
      await refreshHistory();
    });
  });
  updateRangeTitles();
}

function chartOptions() {
  return {
    animation: {
      duration: 500,
      easing: 'easeOutCubic'
    },
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: 'rgba(15,23,42,.96)',
        borderColor: 'rgba(148,163,184,.18)',
        borderWidth: 1,
        titleColor: '#e2e8f0',
        bodyColor: '#e2e8f0',
        displayColors: true
      }
    },
    scales: {
      x: {
        ticks: {
          color: '#94a3b8',
          autoSkip: true,
          maxTicksLimit: 8,
          callback: function(value) {
            return formatAxisLabel(this.getLabelForValue(value));
          }
        },
        grid: {
          display: false
        }
      },
      y: {
        ticks: {
          color: '#94a3b8'
        },
        grid: {
          color: 'rgba(148,163,184,.08)'
        }
      }
    }
  };
}

function lineDataset(label, data, borderColor, borderWidth = 2, extra = {}) {
  return {
    label,
    data,
    borderColor,
    borderWidth,
    pointRadius: 0,
    pointHoverRadius: extra.pointHoverRadius ?? 3,
    pointHitRadius: extra.pointHitRadius ?? 8,
    tension: extra.tension ?? 0.25,
    spanGaps: true,
    borderDash: extra.borderDash ?? [],
    fill: false,
    ...extra,
  };
}

function createOrUpdateChart(existingChart, canvasId, labels, datasets, thresholdBands = null, yConfig = null) {
  if (!existingChart) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    const options = chartOptions();

    if (thresholdBands) {
      options.plugins.thresholdBands = { bands: thresholdBands };
    }
    if (yConfig) {
      Object.assign(options.scales.y, yConfig);
    }

    return new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options
    });
  }

  existingChart.data.labels = labels;
  existingChart.data.datasets = datasets;

  if (thresholdBands) {
    existingChart.options.plugins.thresholdBands = { bands: thresholdBands };
  } else {
    delete existingChart.options.plugins.thresholdBands;
  }

  if (yConfig) {
    Object.assign(existingChart.options.scales.y, yConfig);
  }

  existingChart.update('none');
  return existingChart;
}

function updateBatteryChartMeta(batterySeries) {
  const el = document.getElementById('battery-chart-meta');
  if (!el) return;

  const mm = minMax(batterySeries);
  if (!mm) {
    el.textContent = '--';
    return;
  }

  const now = batterySeries[batterySeries.length - 1];
  const dayRangeText = (
    battery24hMinV == null || isNaN(battery24hMinV) ||
    battery24hMaxV == null || isNaN(battery24hMaxV)
  )
    ? '--'
    : `${Number(battery24hMinV).toFixed(3)}–${Number(battery24hMaxV).toFixed(3)} V`;

  el.textContent = `now ${fmtMaybe(now, 3, ' V')} | chart range ${fmtMaybe(mm.min, 3)}–${fmtMaybe(mm.max, 3)} V | 24h range ${dayRangeText}`;
}

function updateNoiseChartMeta(noiseSeries) {
  const el = document.getElementById('noise-chart-meta');
  if (!el) return;

  const clean = noiseSeries.filter(v => v != null && !isNaN(v));
  if (!clean.length) {
    el.textContent = '--';
    return;
  }

  const now = clean[clean.length - 1];
  if (noiseFloorBaselineDbm == null) {
    el.textContent = `now ${fmtMaybe(now, 0, ' dBm')} | baseline --`;
    return;
  }

  const warn = Number(noiseFloorBaselineDbm) + 2;
  const alert = Number(noiseFloorBaselineDbm) + 5;
  el.textContent = `now ${fmtMaybe(now, 0, ' dBm')} | base ${fmtMaybe(noiseFloorBaselineDbm, 1, ' dBm')} | warn ${fmtMaybe(warn, 1)} | alert ${fmtMaybe(alert, 1)}`;
}

function updateMarginChartMeta(marginSeries) {
  const el = document.getElementById('margin-chart-meta');
  if (!el) return;

  const clean = marginSeries.filter(v => v != null && !isNaN(v));
  if (!clean.length) {
    el.textContent = '--';
    return;
  }

  const now = clean[clean.length - 1];
  const baselineText = linkMarginBaselineDb == null ? '--' : fmtMaybe(linkMarginBaselineDb, 1, ' dB');
  el.textContent = `now ${fmtMaybe(now, 0, ' dB')} | 30d median ${baselineText} | RSSI ${fmtMaybe(latestRssiDbm, 0, ' dBm')} / NF ${fmtMaybe(latestNoiseFloorDbm, 0, ' dBm')}`;
}

function updateSnrChartMeta(snrSeries) {
  const el = document.getElementById('snr-chart-meta');
  if (!el) return;

  const clean = snrSeries.filter(v => v != null && !isNaN(v));
  if (!clean.length) {
    el.textContent = '--';
    return;
  }

  const now = clean[clean.length - 1];
  const baselineText = snrBaselineDb == null ? '--' : fmtMaybe(snrBaselineDb, 1, ' dB');
  el.textContent = `now ${fmtMaybe(now, 1, ' dB')} | 30d median ${baselineText} | zones: poor <3, good ≥10`;
}

async function refreshHistory() {
  const r = await fetch(apiUrl('/api/history', { range: currentRange }));
  const data = await r.json();

  const labels = data.map(x => x.ts);

  const battery = data.map(x => x.bat_v);
  const noise = data.map(x => x.noise_floor_dbm);
  const margin = data.map(x => x.link_margin_db);
  const snr = data.map(x => x.last_snr_db);

  const batterySmoothed = movingAverage(battery, 5);
  const noiseSmoothed = movingAverage(noise, 5);
  const marginSmoothed = movingAverage(margin, 5);
  const snrSmoothed = movingAverage(snr, 5);

  updateBatteryChartMeta(battery);
  updateNoiseChartMeta(noise);
  updateMarginChartMeta(margin);
  updateSnrChartMeta(snr);

  batteryChart = createOrUpdateChart(
    batteryChart,
    'batteryChart',
    labels,
    [
      lineDataset('Battery raw', battery, 'rgba(96,165,250,.22)', 1, { pointHoverRadius: 0 }),
      lineDataset('Battery', batterySmoothed, '#60a5fa', 2),
    ]
  );

  const noiseDatasets = [
    lineDataset('Noise raw', noise, 'rgba(34,197,94,.20)', 1, { pointHoverRadius: 0 }),
    lineDataset('Noise', noiseSmoothed, '#4ade80', 2),
  ];

  if (noiseFloorBaselineDbm != null) {
    noiseDatasets.push(
      lineDataset('Baseline', constantArray(labels, noiseFloorBaselineDbm), 'rgba(148,163,184,.65)', 1, { tension: 0, borderDash: [6, 6], pointHoverRadius: 0 }),
      lineDataset('Baseline +2 dB', constantArray(labels, Number(noiseFloorBaselineDbm) + 2), 'rgba(234,179,8,.55)', 1, { tension: 0, borderDash: [4, 5], pointHoverRadius: 0 }),
      lineDataset('Baseline +5 dB', constantArray(labels, Number(noiseFloorBaselineDbm) + 5), 'rgba(239,68,68,.55)', 1, { tension: 0, borderDash: [4, 5], pointHoverRadius: 0 }),
    );
  }

  noiseChart = createOrUpdateChart(
    noiseChart,
    'noiseChart',
    labels,
    noiseDatasets
  );

  const marginBands = [
    { from: -5, to: 6, color: 'rgba(239,68,68,.05)' },
    { from: 6, to: 10, color: 'rgba(249,115,22,.05)' },
    { from: 10, to: 15, color: 'rgba(234,179,8,.05)' },
    { from: 15, to: 40, color: 'rgba(34,197,94,.05)' },
  ];

  marginChart = createOrUpdateChart(
    marginChart,
    'marginChart',
    labels,
    [
      lineDataset('Margin raw', margin, 'rgba(56,189,248,.20)', 1, { pointHoverRadius: 0 }),
      lineDataset('Margin', marginSmoothed, '#38bdf8', 2),
      lineDataset('Good (15 dB)', constantArray(labels, 15), 'rgba(34,197,94,.55)', 1, { tension: 0, borderDash: [5, 5], pointHoverRadius: 0 }),
      lineDataset('Usable (10 dB)', constantArray(labels, 10), 'rgba(234,179,8,.55)', 1, { tension: 0, borderDash: [5, 5], pointHoverRadius: 0 }),
      lineDataset('Weak (6 dB)', constantArray(labels, 6), 'rgba(239,68,68,.55)', 1, { tension: 0, borderDash: [5, 5], pointHoverRadius: 0 }),
    ],
    marginBands,
    { suggestedMin: 0, suggestedMax: 30 }
  );

  const snrBands = [
    { from: -5, to: 0, color: 'rgba(239,68,68,.05)' },
    { from: 0, to: 3, color: 'rgba(249,115,22,.05)' },
    { from: 3, to: 10, color: 'rgba(234,179,8,.05)' },
    { from: 10, to: 25, color: 'rgba(34,197,94,.05)' },
  ];

  snrChart = createOrUpdateChart(
    snrChart,
    'snrChart',
    labels,
    [
      lineDataset('SNR raw', snr, 'rgba(167,139,250,.20)', 1, { pointHoverRadius: 0 }),
      lineDataset('SNR', snrSmoothed, '#a78bfa', 2),
      lineDataset('Good (10 dB)', constantArray(labels, 10), 'rgba(34,197,94,.55)', 1, { tension: 0, borderDash: [5, 5], pointHoverRadius: 0 }),
      lineDataset('OK (3 dB)', constantArray(labels, 3), 'rgba(234,179,8,.55)', 1, { tension: 0, borderDash: [5, 5], pointHoverRadius: 0 }),
      lineDataset('Bad (0 dB)', constantArray(labels, 0), 'rgba(239,68,68,.55)', 1, { tension: 0, borderDash: [5, 5], pointHoverRadius: 0 }),
    ],
    snrBands,
    { suggestedMin: -2, suggestedMax: 20 }
  );
}

async function loadLatest() {
  const r = await fetch(apiUrl('/api/latest'));
  const d = await r.json();

  const fetchedRecordTs = parseLocalTs(d.ts);
  const fetchedBatteryMv = d.bat_mv == null ? null : Number(d.bat_mv);
  const fetchedUptimeSecs = d.uptime_secs == null ? null : Number(d.uptime_secs);

  latestBatteryMv = fetchedBatteryMv;
  latestBatteryTrendDirection = d.battery_trend_direction || null;
  latestNoiseFloorDbm = d.noise_floor_dbm == null ? null : Number(d.noise_floor_dbm);
  latestRssiDbm = d.last_rssi_dbm == null ? null : Number(d.last_rssi_dbm);
  latestSnrDb = d.last_snr_db == null ? null : Number(d.last_snr_db);
  latestLinkMarginDb = d.link_margin_db == null ? computeLinkMargin(latestRssiDbm, latestNoiseFloorDbm) : Number(d.link_margin_db);
  latestNextPollAt = parseLocalTs(d.next_poll_at);
  latestPollState = d.poll_state || null;
  latestLastPollStartedTs = parseLocalTs(d.last_poll_started_ts || d.ts_started);
  latestLastPollFinishedTs = parseLocalTs(d.last_poll_finished_ts || d.ts_finished);
  latestLastPollStatus = d.status || null;
  latestLastPollIsValid = d.is_valid == null ? null : Number(d.is_valid);
  latestPollTotal24h = d.poll_total_24h == null ? null : Number(d.poll_total_24h);
  latestPollValid24h = d.poll_valid_24h == null ? null : Number(d.poll_valid_24h);
  latestPollSuccessRate24h = d.poll_success_rate_24h == null ? null : Number(d.poll_success_rate_24h);
  latestNeighboursCollectedTs = d.neighbours_collected_ts || null;
  latestNeighbours = Array.isArray(d.neighbours) ? d.neighbours : [];

  if (fetchedUptimeSecs != null && !isNaN(fetchedUptimeSecs)) {
    if (latestUptimeSecs == null || isNaN(latestUptimeSecs)) {
      latestUptimeSecs = fetchedUptimeSecs;
    } else if (fetchedRecordTs && lastRecordTs) {
      if (fetchedRecordTs.getTime() > lastRecordTs.getTime()) {
        latestUptimeSecs = fetchedUptimeSecs;
      } else if (fetchedUptimeSecs < latestUptimeSecs) {
        latestUptimeSecs = fetchedUptimeSecs;
      }
    } else if (fetchedRecordTs && !lastRecordTs) {
      latestUptimeSecs = fetchedUptimeSecs;
    }
  }

  battery24hMinV = d.battery_24h_min_v == null ? null : Number(d.battery_24h_min_v);
  battery24hMaxV = d.battery_24h_max_v == null ? null : Number(d.battery_24h_max_v);
  noiseFloorBaselineDbm = d.noise_floor_baseline_dbm == null ? null : Number(d.noise_floor_baseline_dbm);
  linkMarginBaselineDb = d.link_margin_baseline_db == null ? null : Number(d.link_margin_baseline_db);
  snrBaselineDb = d.snr_baseline_db == null ? null : Number(d.snr_baseline_db);

  if (fetchedRecordTs && latestUptimeSecs != null && !isNaN(latestUptimeSecs)) {
    const rebootDt = new Date(fetchedRecordTs.getTime() - (latestUptimeSecs * 1000));
    lastRebootText = formatDateObj(rebootDt, false);
  } else {
    lastRebootText = null;
  }

  if (fetchedRecordTs) {
    lastRecordTs = fetchedRecordTs;
    lastRecordText = d.ts || null;
  }

  renderBatteryDisplay();
  renderBatteryMeta();
  renderNoiseCard();
  renderLinkCard();
  renderUptimeDisplay();
  renderNeighboursPanel();

  const pollSummaryEl = document.getElementById('poll-summary');
  if (pollSummaryEl) {
    const rateText = latestPollSuccessRate24h == null ? '--' : `${latestPollSuccessRate24h.toFixed(1)}%`;
    const validText = latestPollValid24h == null ? '--' : `${latestPollValid24h}`;
    const totalText = latestPollTotal24h == null ? '--' : `${latestPollTotal24h}`;
    pollSummaryEl.textContent = `Polls 24h: ${rateText} (${validText}/${totalText}) • last valid: ${fmtDateTime(lastRecordText)}`;
  }

  nextLatestPollSec = 5;
  updateFreshnessOnly();
}

(async function init() {
  initBatteryModeToggle();
  initRangeSelector();
  await loadRepeaterSummaries();
  await loadLatest();
  await refreshHistory();
  await syncServerTime();
  setInterval(updateServerClock, 1000);
  setInterval(syncServerTime, 60000);
  setInterval(loadRepeaterSummaries, 30000);
  setInterval(loadLatest, 5000);
  setInterval(refreshHistory, 60000);
})();
</script>
</body>
</html>
"""

def db(database_name: str | None = None):
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=database_name or DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/now")
def api_now():
    return jsonify({"now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@app.route("/api/repeaters")
def api_repeaters():
    return jsonify(
        {
            "default_node": DEFAULT_NODE.lower(),
            "repeaters": REPEATERS,
        }
    )


def battery_state_from_mv(bat_mv: int | None) -> str:
    if bat_mv is None:
        return "gray"
    volts = float(bat_mv) / 1000.0
    if volts >= 3.70:
        return "green"
    if volts >= 3.40:
        return "yellow"
    return "red"


@app.route("/api/repeaters_summary")
def api_repeaters_summary():
    out = []

    for repeater in REPEATERS:
        conn = db(repeater["db_name"])
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ts,
                        bat_mv
                    FROM repeater_status_history
                    WHERE node = %s
                    ORDER BY ts DESC, id DESC
                    LIMIT 1
                    """,
                    (repeater["node"],),
                )
                row = cur.fetchone() or {}
        finally:
            conn.close()

        ts = row.get("ts")
        bat_mv = row.get("bat_mv")

        out.append(
            {
                "node": repeater["node"],
                "name": repeater["name"],
                "db_name": repeater["db_name"],
                "ts": ts.strftime("%Y-%m-%d %H:%M:%S") if ts else None,
                "bat_mv": int(bat_mv) if bat_mv is not None else None,
                "battery_state": battery_state_from_mv(int(bat_mv)) if bat_mv is not None else "gray",
            }
        )

    return jsonify(
        {
            "default_node": DEFAULT_NODE.lower(),
            "repeaters": out,
        }
    )


@app.route("/api/latest")
def api_latest():
    repeater = get_requested_repeater()
    node = repeater["node"]
    now = datetime.now()
    since_24h = now - timedelta(hours=24)
    since_30d = now - timedelta(days=30)

    conn = db(repeater["db_name"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ts,
                    node,
                    bat_mv,
                    noise_floor_dbm,
                    last_rssi_dbm,
                    last_snr_db,
                    tx_queue_len,
                    nb_recv,
                    nb_sent,
                    airtime_secs,
                    rx_airtime_secs,
                    uptime_secs
                FROM repeater_status_history
                WHERE node = %s
                ORDER BY ts DESC, id DESC
                LIMIT 1
                """,
                (node,),
            )
            row = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    node,
                    last_poll_started_ts,
                    last_poll_finished_ts,
                    next_poll_at,
                    poll_state,
                    updated_at
                FROM repeater_status_meta
                WHERE node = %s
                LIMIT 1
                """,
                (node,),
            )
            meta_row = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    ts_started,
                    ts_finished,
                    node,
                    is_valid,
                    status,
                    error_text
                FROM repeater_status_poll_log
                WHERE node = %s
                ORDER BY ts_started DESC, id DESC
                LIMIT 1
                """,
                (node,),
            )
            last_poll_row = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    COUNT(*) AS poll_total_24h,
                    COALESCE(SUM(CASE WHEN is_valid = 1 THEN 1 ELSE 0 END), 0) AS poll_valid_24h
                FROM repeater_status_poll_log
                WHERE node = %s
                  AND ts_started >= %s
                """,
                (node, since_24h.strftime("%Y-%m-%d %H:%M:%S")),
            )
            poll_stats_row = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    ts,
                    bat_mv
                FROM repeater_status_history
                WHERE node = %s
                  AND ts >= %s
                  AND bat_mv IS NOT NULL
                ORDER BY ts ASC, id ASC
                """,
                (node, since_24h.strftime("%Y-%m-%d %H:%M:%S")),
            )
            battery_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    ts,
                    bat_mv
                FROM repeater_status_history
                WHERE node = %s
                  AND bat_mv IS NOT NULL
                ORDER BY ts DESC, id DESC
                LIMIT 3
                """,
                (node,),
            )
            battery_recent_rows = cur.fetchall()

            cur.execute(
                """
                SELECT
                    noise_floor_dbm,
                    last_rssi_dbm,
                    last_snr_db
                FROM repeater_status_history
                WHERE node = %s
                  AND ts >= %s
                ORDER BY ts ASC, id ASC
                """,
                (node, since_30d.strftime("%Y-%m-%d %H:%M:%S")),
            )
            stats_rows = cur.fetchall()

            neighbours_collected_ts = None
            neighbour_rows = []

            try:
                node_for_neighbours = row.get("node") or node

                cur.execute(
                    """
                    SELECT MAX(collected_ts) AS neighbours_collected_ts
                    FROM repeater_neighbors_history
                    WHERE repeater_node = %s
                    """,
                    (node_for_neighbours,),
                )
                neighbours_meta_row = cur.fetchone() or {}
                neighbours_collected_ts = neighbours_meta_row.get("neighbours_collected_ts")

                if neighbours_collected_ts:
                    cur.execute(
                        """
                        SELECT
                            neighbor_pubkey_pre,
                            neighbor_name,
                            ROUND(snr_db, 2) AS snr_db,
                            GREATEST(0, UNIX_TIMESTAMP() - neighbor_seen_ts) AS secs_ago
                        FROM repeater_neighbors_history
                        WHERE repeater_node = %s
                          AND collected_ts = %s
                        ORDER BY snr_db DESC, neighbor_name ASC, neighbor_pubkey_pre ASC
                        """,
                        (node_for_neighbours, neighbours_collected_ts),
                    )
                    neighbour_rows = cur.fetchall() or []
            except Exception as e:
                app.logger.warning("Neighbour query failed: %s", e)
    finally:
        conn.close()

    row.update(meta_row)
    row.update(last_poll_row)
    row.update(poll_stats_row)

    row["neighbours_collected_ts"] = neighbours_collected_ts
    for n in neighbour_rows:
        if n.get("snr_db") is not None:
            n["snr_db"] = round(float(n["snr_db"]), 2)
        if n.get("secs_ago") is not None:
            n["secs_ago"] = int(n["secs_ago"])
    row["neighbours"] = neighbour_rows

    if row.get("poll_total_24h"):
        row["poll_success_rate_24h"] = round(
            (float(row["poll_valid_24h"]) / float(row["poll_total_24h"])) * 100.0,
            1,
        )
    else:
        row["poll_success_rate_24h"] = None

    for key in (
        "ts",
        "next_poll_at",
        "last_poll_started_ts",
        "last_poll_finished_ts",
        "neighbours_collected_ts",
        "updated_at",
        "ts_started",
        "ts_finished",
    ):
        if row.get(key):
            row[key] = row[key].strftime("%Y-%m-%d %H:%M:%S")

    if row.get("bat_mv") is not None:
        row["bat_mv"] = int(row["bat_mv"])
    if row.get("noise_floor_dbm") is not None:
        row["noise_floor_dbm"] = int(row["noise_floor_dbm"])
    if row.get("last_rssi_dbm") is not None:
        row["last_rssi_dbm"] = int(row["last_rssi_dbm"])
    if row.get("last_snr_db") is not None:
        row["last_snr_db"] = round(float(row["last_snr_db"]), 2)
    if row.get("uptime_secs") is not None:
        row["uptime_secs"] = int(row["uptime_secs"])
    if row.get("is_valid") is not None:
        row["is_valid"] = int(row["is_valid"])
    if row.get("poll_total_24h") is not None:
        row["poll_total_24h"] = int(row["poll_total_24h"])
    if row.get("poll_valid_24h") is not None:
        row["poll_valid_24h"] = int(row["poll_valid_24h"])

    battery_vals = [
        float(r["bat_mv"]) / 1000.0
        for r in battery_rows
        if r.get("bat_mv") is not None
    ]
    row["battery_24h_min_v"] = round(min(battery_vals), 3) if battery_vals else None
    row["battery_24h_max_v"] = round(max(battery_vals), 3) if battery_vals else None

    row["battery_trend_direction"] = None
    if len(battery_recent_rows) >= 2:
        current_mv = battery_recent_rows[0].get("bat_mv")
        prev1_mv = battery_recent_rows[1].get("bat_mv")

        if current_mv is not None and prev1_mv is not None:
            current_mv = int(current_mv)
            prev1_mv = int(prev1_mv)

            if current_mv > prev1_mv:
                row["battery_trend_direction"] = "up"
            elif current_mv < prev1_mv:
                row["battery_trend_direction"] = "down"

    noise_vals = []
    margin_vals = []
    snr_vals = []

    for r in stats_rows:
        nf = r.get("noise_floor_dbm")
        rr = r.get("last_rssi_dbm")
        snr = r.get("last_snr_db")

        if nf is not None:
            noise_vals.append(float(nf))
        if nf is not None and rr is not None:
            margin_vals.append(float(rr) - float(nf))
        if snr is not None:
            snr_vals.append(float(snr))

    row["noise_floor_baseline_dbm"] = round(float(median(noise_vals)), 1) if noise_vals else None
    row["link_margin_baseline_db"] = round(float(median(margin_vals)), 1) if margin_vals else None
    row["snr_baseline_db"] = round(float(median(snr_vals)), 1) if snr_vals else None

    if row.get("last_rssi_dbm") is not None and row.get("noise_floor_dbm") is not None:
        row["link_margin_db"] = int(row["last_rssi_dbm"]) - int(row["noise_floor_dbm"])
    else:
        row["link_margin_db"] = None

    return jsonify(row)

@app.route("/api/history")
def api_history():
    repeater = get_requested_repeater()
    node = repeater["node"]
    range_name = request.args.get("range", "week")
    days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(range_name, 7)
    since = datetime.now() - timedelta(days=days)

    conn = db(repeater["db_name"])
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ts,
                    bat_mv,
                    noise_floor_dbm,
                    last_rssi_dbm,
                    last_snr_db
                FROM repeater_status_history
                WHERE node = %s
                  AND ts >= %s
                ORDER BY ts ASC, id ASC
                """,
                (node, since.strftime("%Y-%m-%d %H:%M:%S")),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        bat_v = (float(r["bat_mv"]) / 1000.0) if r["bat_mv"] is not None else None
        noise_floor = int(r["noise_floor_dbm"]) if r["noise_floor_dbm"] is not None else None
        last_rssi = int(r["last_rssi_dbm"]) if r["last_rssi_dbm"] is not None else None
        link_margin = (last_rssi - noise_floor) if (last_rssi is not None and noise_floor is not None) else None

        out.append({
            "ts": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
            "bat_v": round(bat_v, 3) if bat_v is not None else None,
            "noise_floor_dbm": noise_floor,
            "last_rssi_dbm": last_rssi,
            "last_snr_db": float(r["last_snr_db"]) if r["last_snr_db"] is not None else None,
            "link_margin_db": link_margin,
        })
    return jsonify(out)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8010, debug=False)
