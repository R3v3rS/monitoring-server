import threading
import time
from collections import deque
from datetime import datetime, timezone

RAPL_ENERGY_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"
SAMPLE_INTERVAL_SECONDS = 1.0
ROLLING_WINDOW_SIZE = 5

_lock = threading.Lock()
_latest_watts = 0.0
_latest_timestamp = datetime.now(timezone.utc)
_recent_samples = deque(maxlen=ROLLING_WINDOW_SIZE)
_min_watts = None
_max_watts = None
_monitor_started = False


def read_energy(path=RAPL_ENERGY_PATH):
    with open(path, "r", encoding="utf-8") as file_handle:
        return int(file_handle.read().strip())


def calculate_power(energy_start_uj, energy_end_uj):
    delta_uj = max(0, energy_end_uj - energy_start_uj)
    return delta_uj / 1_000_000


def _update_metrics(power_watts):
    global _latest_watts, _latest_timestamp, _min_watts, _max_watts

    now = datetime.now(timezone.utc)
    with _lock:
        _latest_watts = float(power_watts)
        _latest_timestamp = now
        _recent_samples.append(_latest_watts)
        _min_watts = _latest_watts if _min_watts is None else min(_min_watts, _latest_watts)
        _max_watts = _latest_watts if _max_watts is None else max(_max_watts, _latest_watts)


def _power_monitor_loop(path):
    global _latest_timestamp
    previous_energy = None
    while True:
        try:
            current_energy = read_energy(path=path)
            if previous_energy is not None:
                _update_metrics(calculate_power(previous_energy, current_energy))
            previous_energy = current_energy
        except (FileNotFoundError, PermissionError, ValueError, OSError):
            with _lock:
                _latest_timestamp = datetime.now(timezone.utc)
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
        }
