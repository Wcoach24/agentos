#!/usr/bin/env python3
"""
watcher.py — Entry-point relay (pgrep-visible) que delega en bin/watcher.py.

Por qué existe este fichero en la raíz:
  El ejecutable real vive en bin/watcher.py y la invocación launchd usa un Python
  con ruta larga (~141 chars), lo que macOS pgrep trunca y no puede casar con
  'watcher.py'. Este relay se lanza con una ruta corta (.venv/bin/python watcher.py)
  que sí es localizable por `pgrep -f 'watcher.py'`.

Modo standby:
  Si ya hay un watcher real corriendo (detectado por el PID en el heartbeat), este
  proceso duerme en bucle. En cuanto el watcher real muere, toma el relevo sin
  conflictos. Así NUNCA corren dos watchers reales a la vez.
"""
from __future__ import annotations
import os, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
BIN_WATCHER = os.path.join(ROOT, "bin", "watcher.py")
HB_FILE = os.path.join(ROOT, "state", "watcher_heartbeat.txt")


def _real_watcher_running() -> bool:
    """Devuelve True si hay un watcher real vivo (distinto de este proceso)."""
    try:
        line = open(HB_FILE).read().strip()
        pid = int(line.split("pid=")[1].split()[0])
        if pid == os.getpid():
            return False  # somos nosotros mismos
        os.kill(pid, 0)   # signal 0 = sondeo de existencia, sin efecto
        # También comprobamos que el heartbeat sea reciente (< 10 min)
        ts = int(line.split()[0])
        return (time.time() - ts) < 600
    except Exception:
        return False


def _standby_loop() -> None:
    """Bucle de espera: duerme hasta que el watcher real muera."""
    real_pid = None
    try:
        line = open(HB_FILE).read().strip()
        real_pid = int(line.split("pid=")[1].split()[0])
    except Exception:
        pass
    print(f"[watcher-relay] standby (watcher real en pid={real_pid})", flush=True)
    while True:
        if not _real_watcher_running():
            print("[watcher-relay] watcher real caído — tomando el relevo", flush=True)
            return  # salir del standby → ejecutar el watcher real
        time.sleep(30)


def main() -> None:
    if _real_watcher_running():
        _standby_loop()
    # Correr como watcher real: env var AGENTOS_ROOT para que bin/watcher.py
    # calcule ROOT correctamente aunque __file__ sea /agentos/watcher.py
    os.environ["AGENTOS_ROOT"] = ROOT
    sys.argv[0] = BIN_WATCHER
    exec(compile(open(BIN_WATCHER).read(), BIN_WATCHER, "exec"))  # noqa: S102


if __name__ == "__main__":
    main()
