import time
import atexit
import logging
from datetime import datetime

import psutil
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

_prev_disk_io = None
_prev_net_io = None
_prev_ts = None

HWMON_BASE_PATH = "/sys/class/hwmon/hwmon0"
FAN_CHANNELS = ("pwm1", "pwm2", "pwm3")
FAN_MIN_PWM = {"pwm1": 64, "pwm2": 0, "pwm3": 0}
FAN_AUTO_MODE = 5
FAN_MANUAL_MODE = 1
_fan_original_modes = {}


DASHBOARD_HTML = """
<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>System Monitor</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; background: #f5f7fb; color: #222; }
    h1 { margin-bottom: 8px; }
    .subtitle { margin-bottom: 20px; color: #666; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
    .card { background: #fff; border-radius: 10px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
    .label { color: #666; font-size: 14px; }
    .value { font-size: 24px; font-weight: bold; margin: 6px 0; }
    .bar-wrap { height: 10px; background: #e8ebf2; border-radius: 999px; overflow: hidden; }
    .bar { height: 100%; width: 0%; transition: width .4s ease; }
    .green { color: #188038; }
    .yellow { color: #b26a00; }
    .red { color: #c5221f; }
    .bg-green { background: #188038; }
    .bg-yellow { background: #b26a00; }
    .bg-red { background: #c5221f; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; background: #fff; border-radius: 10px; overflow: hidden; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; font-size: 14px; }
    th { background: #fafafa; }
  </style>
</head>
<body>
  <h1>System Monitor</h1>
  <div class="subtitle">Odświeżanie co 2 sekundy</div>

  <div class="grid">
    <div class="card">
      <div class="label">CPU</div>
      <div id="cpuValue" class="value">--%</div>
      <div class="bar-wrap"><div id="cpuBar" class="bar"></div></div>
      <div id="cpuFreq" class="label"></div>
    </div>

    <div class="card">
      <div class="label">RAM</div>
      <div id="ramValue" class="value">--%</div>
      <div class="bar-wrap"><div id="ramBar" class="bar"></div></div>
      <div id="ramDetails" class="label"></div>
    </div>

    <div class="card">
      <div class="label">SWAP</div>
      <div id="swapValue" class="value">--%</div>
      <div class="bar-wrap"><div id="swapBar" class="bar"></div></div>
      <div id="swapDetails" class="label"></div>
    </div>

    <div class="card">
      <div class="label">Temperatury</div>
      <div id="tempsBox" class="label">Brak danych</div>
    </div>

    <div class="card">
      <div class="label">Wentylatory</div>
      <div id="fansBox" class="label">Brak danych</div>
    </div>

    <div class="card">
      <div class="label">Uptime</div>
      <div id="uptime" class="value">--</div>
      <div class="label">Czas od uruchomienia systemu</div>
    </div>

    <div class="card">
      <div class="label">Disk I/O</div>
      <div id="diskIO" class="value" style="font-size:18px">--</div>
    </div>

    <div class="card">
      <div class="label">Net I/O</div>
      <div id="netIO" class="value" style="font-size:18px">--</div>
    </div>
  </div>

  <h2>Dyski</h2>
  <table>
    <thead>
      <tr><th>Mountpoint</th><th>Total (GB)</th><th>Used (GB)</th><th>Usage</th></tr>
    </thead>
    <tbody id="disksBody"></tbody>
  </table>

  <h2>Top 5 procesów (CPU)</h2>
  <table>
    <thead>
      <tr><th>PID</th><th>Nazwa</th><th>CPU %</th><th>RAM (MB)</th></tr>
    </thead>
    <tbody id="procBody"></tbody>
  </table>

  <h2>Sterowanie wentylatorami</h2>
  <div class="grid">
    <div class="card">
      <div class="label">pwm1 <span id="badge-pwm1">--</span></div>
      <input type="range" id="slider-pwm1" min="0" max="100" value="0" />
      <div id="value-pwm1" class="label">--</div>
      <button id="toggle-pwm1" onclick="toggleFanMode('pwm1')">--</button>
    </div>
    <div class="card">
      <div class="label">pwm2 <span id="badge-pwm2">--</span></div>
      <input type="range" id="slider-pwm2" min="0" max="100" value="0" />
      <div id="value-pwm2" class="label">--</div>
      <button id="toggle-pwm2" onclick="toggleFanMode('pwm2')">--</button>
    </div>
    <div class="card">
      <div class="label">pwm3 <span id="badge-pwm3">--</span></div>
      <input type="range" id="slider-pwm3" min="0" max="100" value="0" />
      <div id="value-pwm3" class="label">--</div>
      <button id="toggle-pwm3" onclick="toggleFanMode('pwm3')">--</button>
    </div>
  </div>
  <p><button onclick="resetFansAuto()">Reset wszystkich do AUTO</button></p>
  <div id="fanControlError" class="red"></div>

<script>
function clsByPercent(value, isTemp=false) {
  if (isTemp) {
    if (value > 85) return 'red';
    if (value > 70) return 'yellow';
    return 'green';
  }
  if (value > 85) return 'red';
  if (value >= 60) return 'yellow';
  return 'green';
}

function applyBar(barId, value) {
  const bar = document.getElementById(barId);
  const cls = clsByPercent(value);
  bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
  bar.className = `bar bg-${cls}`;
}

function fmtUptime(seconds) {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${d}d ${h}h ${m}m`;
}

function valHtml(value, cls) {
  return `<span class="${cls}">${value}</span>`;
}

function fanModeLabel(mode) {
  return mode === 'manual' ? 'RĘCZNY' : 'AUTO';
}

function updateFanControls(data) {
  for (const channel of ['pwm1', 'pwm2', 'pwm3']) {
    const state = data[channel];
    const isManual = state.mode === 'manual';
    document.getElementById(`badge-${channel}`).textContent = fanModeLabel(state.mode);
    document.getElementById(`badge-${channel}`).className = isManual ? 'yellow' : 'green';

    const slider = document.getElementById(`slider-${channel}`);
    slider.disabled = !isManual;
    slider.value = state.percent ?? 0;
    document.getElementById(`value-${channel}`).textContent = isManual ? `${state.percent}%` : 'Tryb AUTO';

    const btn = document.getElementById(`toggle-${channel}`);
    btn.textContent = isManual ? 'Przywróć auto' : 'Ustaw ręcznie';
  }
}

async function refreshFanControl() {
  const errorBox = document.getElementById('fanControlError');
  try {
    const res = await fetch('/api/fans/control');
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || 'Nie udało się pobrać stanu');
    updateFanControls(payload);
    errorBox.textContent = '';
  } catch (err) {
    errorBox.textContent = err.message;
  }
}

async function toggleFanMode(channel) {
  const slider = document.getElementById(`slider-${channel}`);
  const manual = !slider.disabled;
  const body = manual
    ? { channel, mode: 'auto' }
    : { channel, mode: 'manual', percent: Number.parseInt(slider.value, 10) };
  await fetch('/api/fans/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  await refreshFanControl();
}

async function resetFansAuto() {
  await fetch('/api/fans/reset', { method: 'POST' });
  await refreshFanControl();
}

async function refresh() {
  const res = await fetch('/api/stats');
  const s = await res.json();

  const cpuCls = clsByPercent(s.cpu_percent.total);
  document.getElementById('cpuValue').innerHTML = valHtml(`${s.cpu_percent.total.toFixed(1)}%`, cpuCls);
  document.getElementById('cpuFreq').textContent = `Częstotliwość: ${s.cpu_freq_mhz.toFixed(1)} MHz | rdzenie: ${s.cpu_percent.per_core.map(v => v.toFixed(1)).join(', ')}`;
  applyBar('cpuBar', s.cpu_percent.total);

  const ramCls = clsByPercent(s.ram_percent);
  document.getElementById('ramValue').innerHTML = valHtml(`${s.ram_percent.toFixed(1)}%`, ramCls);
  document.getElementById('ramDetails').textContent = `${s.ram_used_mb.toFixed(0)} / ${s.ram_total_mb.toFixed(0)} MB`;
  applyBar('ramBar', s.ram_percent);

  const swapCls = clsByPercent(s.swap_percent);
  document.getElementById('swapValue').innerHTML = valHtml(`${s.swap_percent.toFixed(1)}%`, swapCls);
  document.getElementById('swapDetails').textContent = `${s.swap_used_mb.toFixed(0)} / ${s.swap_total_mb.toFixed(0)} MB`;
  applyBar('swapBar', s.swap_percent);

  const tempsBox = document.getElementById('tempsBox');
  if (!s.temperatures.length) {
    tempsBox.textContent = 'Brak danych o temperaturach';
  } else {
    tempsBox.innerHTML = s.temperatures.map(t => {
      const cls = clsByPercent(t.current_c, true);
      return `${t.label || 'sensor'}: <span class="${cls}">${t.current_c.toFixed(1)}°C</span>`;
    }).join('<br/>');
  }

  const fansBox = document.getElementById('fansBox');
  if (!s.fans.length) {
    fansBox.textContent = 'Brak danych o wentylatorach';
  } else {
    fansBox.innerHTML = s.fans.map(f => `${f.label || 'fan'}: ${f.rpm} RPM`).join('<br/>');
  }

  document.getElementById('uptime').textContent = fmtUptime(s.uptime_seconds);
  document.getElementById('diskIO').textContent = `Odczyt: ${s.disk_io.read_mb_s.toFixed(2)} MB/s | Zapis: ${s.disk_io.write_mb_s.toFixed(2)} MB/s`;
  document.getElementById('netIO').textContent = `Wysłane: ${s.net_io.sent_mb_s.toFixed(2)} MB/s | Odebrane: ${s.net_io.recv_mb_s.toFixed(2)} MB/s`;

  const disksBody = document.getElementById('disksBody');
  disksBody.innerHTML = s.disks.map(d => {
    const cls = clsByPercent(d.percent);
    return `<tr><td>${d.mountpoint}</td><td>${d.total_gb.toFixed(1)}</td><td>${d.used_gb.toFixed(1)}</td><td class="${cls}">${d.percent.toFixed(1)}%</td></tr>`;
  }).join('');

  const procBody = document.getElementById('procBody');
  procBody.innerHTML = s.top_processes.map(p =>
    `<tr><td>${p.pid}</td><td>${p.name}</td><td>${p.cpu_percent.toFixed(1)}</td><td>${p.ram_mb.toFixed(1)}</td></tr>`
  ).join('');
}

for (const channel of ['pwm1', 'pwm2', 'pwm3']) {
  document.getElementById(`slider-${channel}`).addEventListener('change', async (event) => {
    const slider = event.target;
    if (slider.disabled) return;
    await fetch('/api/fans/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        channel,
        mode: 'manual',
        percent: Number.parseInt(slider.value, 10),
      }),
    });
    await refreshFanControl();
  });
}

refresh();
refreshFanControl();
setInterval(refresh, 2000);
setInterval(refreshFanControl, 4000);
</script>
</body>
</html>
"""


def _bytes_to_mb(value):
    return value / (1024 * 1024)


def _bytes_to_gb(value):
    return value / (1024 * 1024 * 1024)


def _safe_sensor_list(fetcher, map_fn):
    try:
        raw = fetcher()
    except (AttributeError, NotImplementedError):
        return []

    if not raw:
        return []

    result = []
    for entries in raw.values():
        for entry in entries:
            result.append(map_fn(entry))
    return result


def _collect_io_rates():
    global _prev_disk_io, _prev_net_io, _prev_ts

    now = datetime.now().timestamp()
    disk = psutil.disk_io_counters()
    net = psutil.net_io_counters()

    if _prev_ts is None or _prev_disk_io is None or _prev_net_io is None:
        _prev_ts = now
        _prev_disk_io = disk
        _prev_net_io = net
        return {"read_mb_s": 0.0, "write_mb_s": 0.0}, {"sent_mb_s": 0.0, "recv_mb_s": 0.0}

    delta_t = max(now - _prev_ts, 1e-6)

    disk_read = max(0, disk.read_bytes - _prev_disk_io.read_bytes)
    disk_write = max(0, disk.write_bytes - _prev_disk_io.write_bytes)
    net_sent = max(0, net.bytes_sent - _prev_net_io.bytes_sent)
    net_recv = max(0, net.bytes_recv - _prev_net_io.bytes_recv)

    _prev_ts = now
    _prev_disk_io = disk
    _prev_net_io = net

    disk_io = {
        "read_mb_s": _bytes_to_mb(disk_read) / delta_t,
        "write_mb_s": _bytes_to_mb(disk_write) / delta_t,
    }
    net_io = {
        "sent_mb_s": _bytes_to_mb(net_sent) / delta_t,
        "recv_mb_s": _bytes_to_mb(net_recv) / delta_t,
    }
    return disk_io, net_io


def _top_processes(limit=5):
    processes = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            cpu = proc.cpu_percent(interval=None)
            memory = proc.info.get("memory_info")
            ram_mb = _bytes_to_mb(memory.rss) if memory else 0.0
            processes.append(
                {
                    "pid": proc.info["pid"],
                    "name": proc.info.get("name") or "unknown",
                    "cpu_percent": cpu,
                    "ram_mb": ram_mb,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    processes.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return processes[:limit]


def _sysfs_file(channel, suffix=""):
    return f"{HWMON_BASE_PATH}/{channel}{suffix}"


def _read_sysfs_int(path):
    with open(path, "r", encoding="utf-8") as f:
        return int(f.read().strip())


def _write_sysfs_int(path, value):
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))


def _validate_channel(channel):
    return channel in FAN_CHANNELS


def _percent_to_pwm(percent):
    return int((percent * 255) / 100)


def _pwm_to_percent(pwm_value):
    return int(round((pwm_value * 100) / 255))


def _read_fan_control_state(channel):
    mode_value = _read_sysfs_int(_sysfs_file(channel, "_enable"))
    if mode_value == FAN_MANUAL_MODE:
        pwm_value = _read_sysfs_int(_sysfs_file(channel))
        return {"mode": "manual", "pwm_value": pwm_value, "percent": _pwm_to_percent(pwm_value)}
    return {"mode": "auto", "pwm_value": None, "percent": None}


def _set_manual_pwm(channel, percent):
    pwm_value = _percent_to_pwm(percent)
    pwm_value = max(FAN_MIN_PWM[channel], min(255, pwm_value))
    _write_sysfs_int(_sysfs_file(channel, "_enable"), FAN_MANUAL_MODE)
    _write_sysfs_int(_sysfs_file(channel), pwm_value)


def _restore_fan_modes(use_original=False):
    errors = []
    for channel in FAN_CHANNELS:
        try:
            target_mode = _fan_original_modes.get(channel, FAN_AUTO_MODE) if use_original else FAN_AUTO_MODE
            _write_sysfs_int(_sysfs_file(channel, "_enable"), target_mode)
        except Exception as exc:  # noqa: BLE001
            app.logger.error("Fan reset error for %s: %s", channel, exc)
            errors.append({"channel": channel, "error": str(exc)})
    return errors


def _cache_original_fan_modes():
    for channel in FAN_CHANNELS:
        try:
            _fan_original_modes[channel] = _read_sysfs_int(_sysfs_file(channel, "_enable"))
        except Exception as exc:  # noqa: BLE001
            app.logger.error("Cannot read original mode for %s: %s", channel, exc)
            _fan_original_modes[channel] = FAN_AUTO_MODE


@atexit.register
def _restore_fans_on_exit():
    _restore_fan_modes(use_original=True)


_cache_original_fan_modes()


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/stats")
def stats():
    cpu_total = psutil.cpu_percent(interval=None)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
    freq = psutil.cpu_freq()
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()

    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append(
                {
                    "mountpoint": part.mountpoint,
                    "total_gb": _bytes_to_gb(usage.total),
                    "used_gb": _bytes_to_gb(usage.used),
                    "percent": usage.percent,
                }
            )
        except PermissionError:
            continue

    disk_io, net_io = _collect_io_rates()

    temperatures = _safe_sensor_list(
        psutil.sensors_temperatures,
        lambda entry: {
            "label": entry.label,
            "current_c": entry.current,
            "high_c": entry.high,
            "critical_c": entry.critical,
        },
    )

    fans = _safe_sensor_list(
        psutil.sensors_fans,
        lambda entry: {"label": entry.label, "rpm": entry.current},
    )

    uptime_seconds = int(time.time() - psutil.boot_time())

    return jsonify(
        {
            "cpu_percent": {"total": cpu_total, "per_core": cpu_per_core},
            "cpu_freq_mhz": (freq.current if freq else 0.0),
            "ram_total_mb": _bytes_to_mb(vm.total),
            "ram_used_mb": _bytes_to_mb(vm.used),
            "ram_percent": vm.percent,
            "swap_total_mb": _bytes_to_mb(swap.total),
            "swap_used_mb": _bytes_to_mb(swap.used),
            "swap_percent": swap.percent,
            "disks": disks,
            "disk_io": disk_io,
            "net_io": net_io,
            "temperatures": temperatures,
            "fans": fans,
            "uptime_seconds": uptime_seconds,
            "top_processes": _top_processes(limit=5),
        }
    )


@app.route("/api/fans/control", methods=["GET"])
def get_fans_control():
    try:
        payload = {channel: _read_fan_control_state(channel) for channel in FAN_CHANNELS}
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        app.logger.error("Error reading fans control: %s", exc)
        return jsonify({"error": "Błąd odczytu sterowania wentylatorami"}), 500


@app.route("/api/fans/control", methods=["POST"])
def set_fans_control():
    payload = request.get_json(silent=True) or {}
    channel = payload.get("channel")
    mode = payload.get("mode")
    percent = payload.get("percent")

    if not _validate_channel(channel):
        return jsonify({"error": "Nieprawidłowy channel"}), 400
    if mode not in ("manual", "auto"):
        return jsonify({"error": "Nieprawidłowy mode"}), 400
    if mode == "manual" and (not isinstance(percent, int) or percent < 0 or percent > 100):
        return jsonify({"error": "percent musi być liczbą całkowitą 0-100"}), 400

    try:
        if mode == "manual":
            _set_manual_pwm(channel, percent)
        else:
            _write_sysfs_int(_sysfs_file(channel, "_enable"), FAN_AUTO_MODE)
        return jsonify(_read_fan_control_state(channel))
    except PermissionError:
        return jsonify({"error": "Brak uprawnień do zapisu PWM — sprawdź reguły udev"}), 403
    except Exception as exc:  # noqa: BLE001
        app.logger.error("Error setting fan control (%s): %s", channel, exc)
        return jsonify({"error": "Błąd zapisu sterowania wentylatorami"}), 500


@app.route("/api/fans/reset", methods=["POST"])
def reset_fans():
    errors = _restore_fan_modes(use_original=False)
    status = 500 if errors else 200
    return jsonify({"status": "ok" if not errors else "partial_error", "errors": errors}), status


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
