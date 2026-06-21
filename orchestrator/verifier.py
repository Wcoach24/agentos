"""
verifier.py — El antídoto anti-abandono.

El loop NO decide solo que terminó. Este verificador comprueba cada criterio de la
Definition-of-Done contra evidencia OBJETIVA. Sin verde aquí, el grafo no cierra.

Tipos de verificación:
  - file_exists        : ¿existe la ruta?
  - http_status        : ¿la URL devuelve el status esperado?
  - command_exit_zero  : ¿el comando sale con código 0?
  - file_contains      : ¿el fichero contiene el substring esperado?
  - agent_judgment     : juicio de un subagente FRESCO (contexto limpio) contra una rúbrica.
"""
from __future__ import annotations
import os, subprocess, urllib.request
from dataclasses import dataclass
from .engine import run_agent


@dataclass
class CheckResult:
    dod_id: str
    passed: bool
    evidence: str


def _file_exists(target: str, cwd: str) -> CheckResult:
    p = os.path.join(cwd, target) if not os.path.isabs(target) else target
    ok = os.path.isfile(p)
    return CheckResult("", ok, f"file_exists {p} -> {ok}")


def _http_status(target: str, expected: str) -> CheckResult:
    try:
        with urllib.request.urlopen(target, timeout=20) as r:
            code = r.status
        ok = str(code) == str(expected or 200)
        return CheckResult("", ok, f"GET {target} -> {code} (esperado {expected or 200})")
    except Exception as e:
        return CheckResult("", False, f"GET {target} -> ERROR {e}")


def _command_exit_zero(target: str, cwd: str) -> CheckResult:
    try:
        r = subprocess.run(target, shell=True, cwd=cwd, capture_output=True, timeout=120)
        ok = r.returncode == 0
        return CheckResult("", ok, f"$ {target} -> exit {r.returncode}")
    except Exception as e:
        return CheckResult("", False, f"$ {target} -> ERROR {e}")


def _file_contains(target: str, expected: str, cwd: str) -> CheckResult:
    p = os.path.join(cwd, target) if not os.path.isabs(target) else target
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        ok = (expected or "") in content
        return CheckResult("", ok, f"file_contains {p} ~ {expected!r} -> {ok}")
    except Exception as e:
        return CheckResult("", False, f"file_contains {p} -> ERROR {e}")


async def _agent_judgment(rubric: str, cwd: str, objective: str) -> CheckResult:
    """Subagente fresco: lee el workspace y juzga contra la rúbrica. Devuelve PASS/FAIL."""
    prompt = (
        "Eres un VERIFICADOR independiente y estricto. No construyas nada, solo juzga.\n"
        f"Objetivo de la misión: {objective}\n\n"
        f"Rúbrica de aceptación:\n{rubric}\n\n"
        "Inspecciona los ficheros relevantes del directorio de trabajo. Luego responde "
        "EXACTAMENTE con una línea que empiece por 'PASS:' o 'FAIL:' seguida de la razón. "
        "Sé escéptico: si hay cifras sin fuente o afirmaciones vagas, es FAIL."
    )
    res = await run_agent(
        prompt,
        system_prompt="Verificador escéptico e independiente. Evidencia antes que afirmaciones. Solo lectura.",
        cwd=cwd,
        allowed_tools=["Read", "Glob", "Grep"],   # READ-ONLY: el juez NO toca artefactos/tests (anti reward-hacking)
        max_turns=10,
        permission_mode="default",
        model="opus",                              # juez fuerte; contexto independiente del planner
    )
    text = res.text.strip()
    passed = text.upper().lstrip().startswith("PASS")
    return CheckResult("", passed, text[:500])


async def verify_dod(dod: list[dict], cwd: str, objective: str) -> tuple[bool, list[CheckResult]]:
    """Comprueba todos los criterios. Devuelve (todo_ok, detalle)."""
    results: list[CheckResult] = []
    for item in dod:
        v = item["verify"]
        t = v["type"]
        if t == "file_exists":
            r = _file_exists(v["target"], cwd)
        elif t == "http_status":
            r = _http_status(v["target"], v.get("expected", "200"))
        elif t == "command_exit_zero":
            r = _command_exit_zero(v["target"], cwd)
        elif t == "file_contains":
            r = _file_contains(v["target"], v.get("expected", ""), cwd)
        elif t == "agent_judgment":
            r = await _agent_judgment(v.get("rubric", ""), cwd, objective)
        else:
            r = CheckResult("", False, f"tipo de verificación desconocido: {t}")
        r.dod_id = item["id"]
        results.append(r)
    all_ok = all(r.passed for r in results)
    return all_ok, results
