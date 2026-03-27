# System Monitor (Flask + psutil)

## Instalacja zależności

```bash
pip install flask psutil
```

## Uruchomienie manualne

```bash
python app.py
```

Aplikacja domyślnie startuje na porcie `5001`.

## Instalacja jako serwis systemd

Uruchom w katalogu `system_monitor` (i wcześniej przygotuj plik `system-monitor.service`):

```bash
sudo cp system-monitor.service /etc/systemd/system/system-monitor.service
sudo systemctl enable system-monitor
sudo systemctl start system-monitor
```

## Logi serwisu

```bash
journalctl -u system-monitor -f
```
