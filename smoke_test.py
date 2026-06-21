#!/usr/bin/env python3
"""
smoke_test.py — Valida la fontanería SIN gastar crédito ni llamar al SDK.

Comprueba:
  1) el mission.yaml de prueba parsea y cumple el schema mínimo.
  2) el verifier evalúa bien los tipos objetivos (file_exists, file_contains).
  3) el router toma las decisiones correctas en casos límite.

Ejecuta en el Mac Mini DESPUÉS de instalar deps:
    python smoke_test.py
"""
import os, sys, json, yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def test_mission_parses():
    p = os.path.join(ROOT, "missions", "inbox", "2026-06-16-geo-es-dossier.yaml")
    m = yaml.safe_load(open(p, encoding="utf-8"))
    assert m["id"] and m["objective"] and m["definition_of_done"], "campos obligatorios"
    assert m["budget"]["credit_usd"] > 0, "budget de crédito"
    for d in m["definition_of_done"]:
        assert d["verify"]["type"] in {
            "file_exists", "http_status", "command_exit_zero", "file_contains", "agent_judgment"
        }, f"tipo verify inválido: {d}"
    print("  OK  mission.yaml parsea y respeta el schema")


def test_schema_present():
    s = json.load(open(os.path.join(ROOT, "schemas", "mission.schema.json"), encoding="utf-8"))
    assert s["required"], "schema con required"
    print("  OK  schema presente")


def test_verifier_objective_checks():
    # No importa engine (que requiere el SDK); probamos solo las funciones puras.
    from orchestrator import verifier as V
    tmp = os.path.join(ROOT, "state", "_smoke")
    os.makedirs(tmp, exist_ok=True)
    open(os.path.join(tmp, "x.md"), "w").write("hola ## Competidores mundo")
    r1 = V._file_exists("x.md", tmp); assert r1.passed, r1.evidence
    r2 = V._file_contains("x.md", "## Competidores", tmp); assert r2.passed, r2.evidence
    r3 = V._file_contains("x.md", "## NoExiste", tmp); assert not r3.passed
    print("  OK  verifier: file_exists / file_contains")


def test_router_logic():
    from orchestrator.graph import route, node_bookkeep
    base = {"mission": {"budget": {"max_iterations": 20, "credit_usd": 5, "no_progress_limit": 4}}}
    # route() es decisión PURA
    assert route({**base, "done": True}) == "end"
    assert route({**base, "aborted": True}) == "end"
    assert route({**base, "pending_gate": {"subject": "x"}}) == "gate"
    assert route({**base, "iteration": 1}) == "plan"
    # los TOPES anti-atasco viven en node_bookkeep; el GASTO ya NO es stopper
    assert node_bookkeep({**base, "iteration": 20}).get("aborted"), "tope iteraciones"
    # SDK por plan de suscripción: mucho gasto NO debe abortar (coste = solo telemetría)
    assert not node_bookkeep({**base, "iteration": 1, "spend_usd": 99.0, "last_cost_usd": 0.5}).get("aborted"), "gasto NO aborta"
    r = node_bookkeep({**base, "iteration": 1, "spend_usd": 1.0, "last_cost_usd": 0.25})
    assert abs(r["spend_usd"] - 1.25) < 1e-9 and not r.get("aborted"), "acumula coste real (telemetría)"
    print("  OK  router puro + bookkeep (anti-atasco; gasto solo telemetría)")


def test_mission_validation():
    from orchestrator.runner import validate_mission
    base = {"id": "x", "title": "t", "objective": "o",
            "budget": {"max_iterations": 1, "credit_usd": 1},
            "gates": {"payment": True, "irreversible": True}}
    # válida: tiene un check de máquina
    validate_mission({**base, "definition_of_done": [
        {"id": "d", "check": "c", "verify": {"type": "file_exists", "target": "x"}}]})
    # inválida: solo agent_judgment -> el 'done' sería falseable -> debe rechazarse
    try:
        validate_mission({**base, "definition_of_done": [
            {"id": "d", "check": "c", "verify": {"type": "agent_judgment", "rubric": "r"}}]})
        raise AssertionError("debería haber rechazado una DoD sin check de máquina")
    except ValueError:
        pass
    print("  OK  validación: exige >=1 check de máquina (verificador no falseable)")


if __name__ == "__main__":
    print("AgentOS smoke test (offline, sin crédito):")
    test_schema_present()
    test_mission_parses()
    test_verifier_objective_checks()
    test_router_logic()
    test_mission_validation()
    print("\nTODO VERDE. La fontanería está bien. Listo para la primera misión real.")
