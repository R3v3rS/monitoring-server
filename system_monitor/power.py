import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

RAPL_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"
SAMPLE_INTERVAL_SECONDS = 1.0
ROLLING_WINDOW_SIZE = 5
ERROR_LOG_THROTTLE_SECONDS = 30

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_latest_watts = 0.0
_latest_timestamp = datetime.now(timezone.utc)
_recent_samples = deque(maxlen=ROLLING_WINDOW_SIZE)
_min_watts = None
_max_watts = None
_monitor_started = False
_last_error = None
_last_error_timestamp = None
_source_available = False
_last_error_log_ts = 0.0


def read_energy(path=RAPL_ENERGY_PATH):
    with open(path, "r", encoding="utf-8") as file_handle:
        return int(file_handle.read().strip())


def _read_max_energy_range(path=RAPL_ENERGY_PATH):
    max_range_path = path.replace("/energy_uj", "/max_energy_range_uj")
    with open(max_range_path, "r", encoding="utf-8") as file_handle:
        return int(file_handle.read().strip())


def calculate_power(energy_start_uj, energy_end_uj, elapsed_seconds=1.0, max_energy_range_uj=None):
    if elapsed_seconds <= 0:
        return 0.0

    if energy_end_uj >= energy_start_uj:
        delta_uj = energy_end_uj - energy_start_uj
    elif max_energy_range_uj:
        # RAPL counters roll over at max_energy_range_uj.
        delta_uj = (max_energy_range_uj - energy_start_uj) + energy_end_uj
    else:
        delta_uj = 0

    return max(0.0, delta_uj / 1_000_000 / elapsed_seconds)


def _update_metrics(power_watts):
    global _latest_watts, _latest_timestamp, _min_watts, _max_watts, _source_available

    now = datetime.now(timezone.utc)
    with _lock:
        _latest_watts = float(power_watts)
        _latest_timestamp = now
        _source_available = True
        _recent_samples.append(_latest_watts)
        _min_watts = _latest_watts if _min_watts is None else min(_min_watts, _latest_watts)
        _max_watts = _latest_watts if _max_watts is None else max(_max_watts, _latest_watts)


def _mark_error(exc):
    global _latest_timestamp, _last_error, _last_error_timestamp, _source_available, _last_error_log_ts

    now = datetime.now(timezone.utc)
    with _lock:
        _latest_timestamp = now
        _last_error = f"{type(exc).__name__}: {exc}"
        _last_error_timestamp = now
        _source_available = False

    now_ts = time.time()
    if now_ts - _last_error_log_ts >= ERROR_LOG_THROTTLE_SECONDS:
        logger.warning("CPU power monitor read failed (%s)", _last_error)
        _last_error_log_ts = now_ts


def _clear_error():
    global _last_error, _last_error_timestamp
    with _lock:
        _last_error = None
        _last_error_timestamp = None


def _power_monitor_loop(path):
    previous_energy = None
    previous_ts = None
    max_energy_range_uj = None

    try:
        max_energy_range_uj = _read_max_energy_range(path)
    except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
        logger.info("CPU power max range unavailable, rollover compensation disabled (%s)", exc)

    while True:
        try:
            current_energy = read_energy(path=path)
            now_ts = time.time()
            if previous_energy is not None and previous_ts is not None:
                elapsed = max(now_ts - previous_ts, 1e-6)
                watts = calculate_power(
                    previous_energy,
                    current_energy,
                    elapsed_seconds=elapsed,
                    max_energy_range_uj=max_energy_range_uj,
                )
                _update_metrics(watts)
                _clear_error()
            previous_energy = current_energy
            previous_ts = now_ts
        except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
            _mark_error(exc)
            previous_energy = None
            previous_ts = None

        time.sleep(SAMPLE_INTERVAL_SECONDS)


def start_power_monitor(path=RAPL_ENERGY_PATH):
    global _monitor_started
    with _lock:
        if _monitor_started:
            return
        _monitor_started = True

    worker = threading.Thread(target=_power_monitor_loop, args=(path,), daemon=True, name="cpu-power-monitor")
    worker.start()


def get_power_snapshot():
    with _lock:
        rolling_avg = sum(_recent_samples) / len(_recent_samples) if _recent_samples else _latest_watts
        return {
            "cpu_watts": _latest_watts,
            "timestamp": _latest_timestamp.isoformat(),
            "rolling_avg_watts": rolling_avg,
            "min_watts": _min_watts if _min_watts is not None else _latest_watts,
            "max_watts": _max_watts if _max_watts is not None else _latest_watts,
            "source_available": _source_available,
            "last_error": _last_error,
            "last_error_timestamp": _last_error_timestamp.isoformat() if _last_error_timestamp else None,
        }
