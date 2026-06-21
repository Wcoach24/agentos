#!/usr/bin/env python3
"""
new_mission.py — EL BRIDGE (lado escritura). Valida una misión y la deja en
missions/inbox/ para que el watcher (o run.sh) la recoja. Es el "botón continúa solo".

FLUJO EN COWORK (modo local, por defecto):
  Álvaro dice "continúalo solo" en un chat -> Claude destila la idea en un dict de
  misión -> llama write_mission() -> aterriza VALIDADO en missions/inbox/ -> el
  watcher lo arranca. Cero git, cero acoplamiento.

FLUJO DESDE CLAUDE.AI SIN ACCESO AL MAC (modo git, opcional):
  Claude commitea el yaml a missions/inbox/ del repo puente -> el watcher hace
  git pull cada ciclo (REPO_SYNC=git + REPO_DIR) y lo copia al inbox local.

La validación es la MISMA que usa el runner: rechaza toda misión cuya DoD no tenga
>=1 check de máquina (el 'done' no falseable es el corazón del sistema).

Uso CLI:  python bin/new_mission.py <ruta_mission.yaml>
"""
from __future__ import annotations
import sys, os
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestrator.runner import validate_mission, ROOT  # noqa: E402

INBOX = os.path.join(ROOT, "missions", "inbox")


def write_mission(mission: dict) -> str:
    """Valida y escribe el yaml en el inbox. Devuelve la ruta. Lanza ValueError si es inválida."""
    validate_mission(mission)
    os.makedirs(INBOX, exist_ok=True)
    path = os.path.join(INBOX, f"{mission['id']}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(mission, f, allow_unicode=True, sort_keys=False)
    return path


def main():
    if len(sys.argv) < 2:
        print("uso: python bin/new_mission.py <mission.yaml>")
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    try:
        p = write_mission(m)
    except ValueError as e:
        print(f"[bridge] MISIÓN RECHAZADA (no cumple el contrato): {e}")
        sys.exit(2)
    print(f"[bridge] misión validada y encolada en inbox: {p}")


if __name__ == "__main__":
    main()
