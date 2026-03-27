import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

CPU_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0:0/energy_uj"
PACKAGE_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"
SAMPLE_INTERVAL_SECONDS = 1.0
ROLLING_WINDOW_SIZE = 5
ERROR_LOG_THROTTLE_SECONDS = 30

logger = logging.getLogger(__name__)


_METRIC_PATHS = {
    "cpu": CPU_ENERGY_PATH,
    "package": PACKAGE_ENERGY_PATH,
}


def _new_metric_state():
    now = datetime.now(timezone.utc)
    return {
        "latest_watts": 0.0,
        "latest_timestamp": now,
        "recent_samples": deque(maxlen=ROLLING_WINDOW_SIZE),
        "min_watts": None,
        "max_watts": None,
        "last_error": None,
        "last_error_timestamp": None,
        "source_available": False,
        "last_error_log_ts": 0.0,
    }


_lock = threading.Lock()
_states = {metric: _new_metric_state() for metric in _METRIC_PATHS}
_monitor_started = False


def read_energy(path):
    with open(path, "r", encoding="utf-8") as file_handle:
        return int(file_handle.read().strip())


def _read_max_energy_range(path):
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


def _update_metrics(metric, power_watts):
    now = datetime.now(timezone.utc)
    with _lock:
        state = _states[metric]
        state["latest_watts"] = float(power_watts)
        state["latest_timestamp"] = now
        state["source_available"] = True
        state["recent_samples"].append(state["latest_watts"])
        state["min_watts"] = (
            state["latest_watts"] if state["min_watts"] is None else min(state["min_watts"], state["latest_watts"])
        )
        state["max_watts"] = (
            state["latest_watts"] if state["max_watts"] is None else max(state["max_watts"], state["latest_watts"])
        )


def _mark_error(metric, exc):
    now = datetime.now(timezone.utc)
    with _lock:
        state = _states[metric]
        state["latest_timestamp"] = now
        state["last_error"] = f"{type(exc).__name__}: {exc}"
        state["last_error_timestamp"] = now
        state["source_available"] = False

        now_ts = time.time()
        if now_ts - state["last_error_log_ts"] >= ERROR_LOG_THROTTLE_SECONDS:
            logger.warning("%s power monitor read failed (%s)", metric.upper(), state["last_error"])
            state["last_error_log_ts"] = now_ts


def _clear_error(metric):
    with _lock:
        state = _states[metric]
        state["last_error"] = None
        state["last_error_timestamp"] = None


def _power_monitor_loop(metric, path):
    previous_energy = None
    previous_ts = None
    max_energy_range_uj = None

    try:
        max_energy_range_uj = _read_max_energy_range(path)
    except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
        logger.info("%s power max range unavailable, rollover compensation disabled (%s)", metric.upper(), exc)

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
                _update_metrics(metric, watts)
                _clear_error(metric)
            previous_energy = current_energy
            previous_ts = now_ts
        except (FileNotFoundError, PermissionError, ValueError, OSError) as exc:
            _mark_error(metric, exc)
            previous_energy = None
            previous_ts = None

        time.sleep(SAMPLE_INTERVAL_SECONDS)


def _resolve_metric_paths(cpu_path=CPU_ENERGY_PATH, package_path=PACKAGE_ENERGY_PATH):
    metric_paths = {
        "cpu": cpu_path,
        "package": package_path,
    }

    try:
        read_energy(cpu_path)
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        metric_paths["cpu"] = package_path
        logger.info("CPU cores RAPL path unavailable, falling back to package path")

    return metric_paths


def start_power_monitor(cpu_path=CPU_ENERGY_PATH, package_path=PACKAGE_ENERGY_PATH):
    global _monitor_started
    with _lock:
        if _monitor_started:
            return
        _monitor_started = True

    for metric, path in _resolve_metric_paths(cpu_path=cpu_path, package_path=package_path).items():
        worker = threading.Thread(
            target=_power_monitor_loop,
            args=(metric, path),
            daemon=True,
            name=f"{metric}-power-monitor",
        )
        worker.start()


def _build_snapshot(state):
    rolling_avg = sum(state["recent_samples"]) / len(state["recent_samples"]) if state["recent_samples"] else state["latest_watts"]
    return {
        "watts": state["latest_watts"],
        "timestamp": state["latest_timestamp"].isoformat(),
        "rolling_avg_watts": rolling_avg,
        "min_watts": state["min_watts"] if state["min_watts"] is not None else state["latest_watts"],
        "max_watts": state["max_watts"] if state["max_watts"] is not None else state["latest_watts"],
        "source_available": state["source_available"],
        "last_error": state["last_error"],
        "last_error_timestamp": state["last_error_timestamp"].isoformat() if state["last_error_timestamp"] else None,
    }


def get_power_snapshot():
    with _lock:
        return {metric: _build_snapshot(state) for metric, state in _states.items()}
