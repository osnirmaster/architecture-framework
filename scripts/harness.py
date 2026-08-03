#!/usr/bin/env python3
"""Harness determinístico file-based para validar e publicar o corporate graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fitness_engine import evaluate_fitness

ROOT = Path(__file__).resolve().parents[1]
NODE_DIR = ROOT / "workspace/proposals/nodes"
REL_DIR = ROOT / "workspace/proposals/relationships"
HARNESS_DIR = ROOT / "workspace/harness"
DIST_DIR = ROOT / "dist/graph"
ID_RE = re.compile(r"^urn:corp-arch:[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]+$")
CANONICAL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Diagnostic:
    severity: str
    code: str
    artifact: str
    message: str


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def list_json(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.json") if p.is_file())


def independent_evidence_count(provenance: list[dict[str, Any]]) -> int:
    keys = {
        (str(p.get("repository", "")), str(p.get("commit", "")), str(p.get("path", "")), p.get("lineStart"), p.get("lineEnd"))
        for p in provenance
    }
    return len(keys)


def classify_inference(item: dict[str, Any], artifact: str, diagnostics: list[Diagnostic]) -> str:
    inf = item.get("inference") or {}
    status = inf.get("epistemicStatus")
    confidence = inf.get("confidence")
    provenance = item.get("provenance") or []

    if status not in {"EXPLICIT", "INFERRED", "AMBIGUOUS", "CONFLICTED"}:
        diagnostics.append(Diagnostic("BLOCK", "INVALID_EPISTEMIC_STATUS", artifact, f"Status epistêmico inválido: {status!r}"))
        return "rejected"
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        diagnostics.append(Diagnostic("BLOCK", "INVALID_CONFIDENCE", artifact, "confidence deve estar entre 0 e 1"))
        return "rejected"
    if status == "CONFLICTED":
        diagnostics.append(Diagnostic("BLOCK", "CONFLICTED_CLAIM", artifact, "Claims conflitantes não podem ser publicados"))
        return "rejected"
    if status == "AMBIGUOUS":
        diagnostics.append(Diagnostic("QUARANTINE", "AMBIGUOUS_CLAIM", artifact, "Classificação explicitamente ambígua"))
        return "quarantine"
    if status == "INFERRED":
        if confidence < 0.85:
            diagnostics.append(Diagnostic("QUARANTINE", "LOW_CONFIDENCE", artifact, f"Inferência com confidence={confidence:.2f}"))
            return "quarantine"
        if independent_evidence_count(provenance) < 2:
            diagnostics.append(Diagnostic("QUARANTINE", "INSUFFICIENT_INDEPENDENT_EVIDENCE", artifact, "Inferência requer duas evidências independentes"))
            return "quarantine"
    return "accepted"


def validate_node(node: dict[str, Any], artifact: str, metamodel: dict[str, Any], factsheet_ids: set[str], diagnostics: list[Diagnostic]) -> str:
    required = ["id", "type", "name", "canonicalName", "description", "metamodelOrigin", "lifecycle", "architectureState", "provenance", "inference", "attributes"]
    missing = [field for field in required if field not in node]
    if missing:
        diagnostics.append(Diagnostic("BLOCK", "MISSING_FIELDS", artifact, f"Campos ausentes: {', '.join(missing)}"))
        return "rejected"

    node_type = node.get("type")
    type_def = metamodel["nodeTypes"].get(node_type)
    if not type_def:
        diagnostics.append(Diagnostic("BLOCK", "UNKNOWN_NODE_TYPE", artifact, f"Tipo não permitido: {node_type}"))
        return "rejected"
    if not ID_RE.match(str(node.get("id", ""))):
        diagnostics.append(Diagnostic("BLOCK", "INVALID_NODE_ID", artifact, "ID fora do padrão urn:corp-arch:<type>:<namespace>:<name>"))
        return "rejected"
    if not CANONICAL_RE.match(str(node.get("canonicalName", ""))):
        diagnostics.append(Diagnostic("BLOCK", "INVALID_CANONICAL_NAME", artifact, "canonicalName deve estar em kebab-case"))
        return "rejected"
    if len(str(node.get("description", "")).strip()) < 30:
        diagnostics.append(Diagnostic("QUARANTINE", "WEAK_DESCRIPTION", artifact, "Descrição deve explicar responsabilidade e fronteira"))
        decision = "quarantine"
    else:
        decision = classify_inference(node, artifact, diagnostics)

    if node.get("architectureState") not in metamodel["architectureStates"]:
        diagnostics.append(Diagnostic("BLOCK", "INVALID_ARCHITECTURE_STATE", artifact, f"Estado inválido: {node.get('architectureState')}"))
        return "rejected"
    if node.get("lifecycle") not in metamodel["lifecycles"]:
        diagnostics.append(Diagnostic("BLOCK", "INVALID_LIFECYCLE", artifact, f"Lifecycle inválido: {node.get('lifecycle')}"))
        return "rejected"
    if not isinstance(node.get("provenance"), list) or not node["provenance"]:
        diagnostics.append(Diagnostic("BLOCK", "MISSING_PROVENANCE", artifact, "Nó sem provenance"))
        return "rejected"

    origin = node.get("metamodelOrigin")
    if origin != type_def.get("origin"):
        diagnostics.append(Diagnostic("BLOCK", "METAMODEL_ORIGIN_MISMATCH", artifact, f"Origem esperada para {node_type}: {type_def.get('origin')}"))
        return "rejected"

    leanix = node.get("leanix") or {"matchStatus": "UNMATCHED"}
    match_status = leanix.get("matchStatus", "UNMATCHED")
    fact_id = leanix.get("factSheetId")
    if match_status == "MATCHED" and (not fact_id or fact_id not in factsheet_ids):
        diagnostics.append(Diagnostic("BLOCK", "UNKNOWN_LEANIX_FACTSHEET", artifact, "MATCHED requer factSheetId existente no catálogo"))
        return "rejected"
    if node_type in {"Capability", "BusinessService", "Product", "ValueStream"} and match_status != "MATCHED":
        diagnostics.append(Diagnostic("QUARANTINE", "GOVERNED_TYPE_NOT_MATCHED", artifact, f"{node_type} requer factsheet corporativo confirmado no MVP"))
        decision = "quarantine" if decision != "rejected" else decision

    if not node.get("owners"):
        diagnostics.append(Diagnostic("WARN", "MISSING_OWNER", artifact, "Nó sem owner"))

    return decision


def relation_allowed(rel_type: str, source_type: str, target_type: str, policy: dict[str, Any]) -> bool:
    variants = policy.get("relationships", {}).get(rel_type, [])
    for variant in variants:
        sources = variant.get("source", [])
        targets = variant.get("target", [])
        if ("*" in sources or source_type in sources) and ("*" in targets or target_type in targets):
            return True
    return False


def validate_relationship(rel: dict[str, Any], artifact: str, nodes: dict[str, dict[str, Any]], policy: dict[str, Any], diagnostics: list[Diagnostic]) -> str:
    required = ["id", "type", "source", "target", "description", "provenance", "inference", "attributes"]
    missing = [field for field in required if field not in rel]
    if missing:
        diagnostics.append(Diagnostic("BLOCK", "MISSING_FIELDS", artifact, f"Campos ausentes: {', '.join(missing)}"))
        return "rejected"
    source = rel.get("source")
    target = rel.get("target")
    if source not in nodes or target not in nodes:
        diagnostics.append(Diagnostic("BLOCK", "MISSING_ENDPOINT", artifact, f"Endpoint inexistente: source={source!r}, target={target!r}"))
        return "rejected"
    if source == target and rel.get("type") not in {"DEPENDS_ON", "CALLS"}:
        diagnostics.append(Diagnostic("BLOCK", "INVALID_SELF_LOOP", artifact, "Self-loop não permitido para este tipo"))
        return "rejected"
    if not relation_allowed(rel.get("type"), nodes[source]["type"], nodes[target]["type"], policy):
        diagnostics.append(Diagnostic("BLOCK", "RELATIONSHIP_NOT_ALLOWED", artifact, f"{nodes[source]['type']} -[{rel.get('type')}]-> {nodes[target]['type']} não é permitido"))
        return "rejected"
    if not rel.get("provenance"):
        diagnostics.append(Diagnostic("BLOCK", "MISSING_PROVENANCE", artifact, "Relação sem provenance"))
        return "rejected"
    return classify_inference(rel, artifact, diagnostics)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_fitness_enforcement(
    fitness: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    node_decisions: dict[str, str],
    diagnostics: list[Diagnostic],
    policy: dict[str, Any],
) -> None:
    for item in fitness.get("diagnostics", []):
        diagnostics.append(Diagnostic(item.get("severity", "BLOCK"), item.get("code", "FITNESS_DIAGNOSTIC"), item.get("artifact", "fitness"), item.get("message", "")))

    default_unknown = policy.get("defaultUnknownEnforcement", "QUARANTINE")
    default_error = policy.get("defaultErrorEnforcement", "BLOCK")
    for evaluation in fitness.get("evaluations", []):
        status = evaluation.get("status")
        target_id = evaluation.get("targetId")
        if status in {"PASS", "NOT_APPLICABLE", "WAIVED"}:
            continue
        if status == "FAIL":
            action = evaluation.get("enforcement", "WARN")
        elif status in {"UNKNOWN", "STALE"}:
            action = evaluation.get("onUnknown") or default_unknown
        elif status == "ERROR":
            action = default_error
        else:
            action = "WARN"
        if action == "IGNORE":
            continue

        artifact = f"fitness:{evaluation.get('fitnessFunctionId')}:{target_id}"
        code = f"FITNESS_{status}"
        message = f"{evaluation.get('fitnessFunctionName')}: {evaluation.get('message')} [enforcement={action}]"
        if action == "BLOCK":
            diagnostics.append(Diagnostic("BLOCK", code, artifact, message))
            if target_id in nodes:
                node_decisions[target_id] = "rejected"
        elif action == "QUARANTINE":
            diagnostics.append(Diagnostic("QUARANTINE", code, artifact, message))
            if target_id in nodes and node_decisions.get(target_id) == "accepted":
                node_decisions[target_id] = "quarantine"
        elif action in {"WARN", "ADVISORY"}:
            diagnostics.append(Diagnostic("WARN", code, artifact, message))
        elif action == "OBSERVE":
            diagnostics.append(Diagnostic("INFO", code, artifact, message))


def filter_fitness_graph_artifacts(fitness: dict[str, Any], accepted_target_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    artifacts = fitness.get("graphArtifacts") or {"nodes": [], "relationships": []}
    all_nodes = {node["id"]: node for node in artifacts.get("nodes", [])}
    all_relationships = artifacts.get("relationships", [])
    keep_ids = {
        node_id
        for node_id, node in all_nodes.items()
        if node.get("type") in {"FitnessFunction", "ArchitectureWaiver"}
    }
    for rel in all_relationships:
        if rel.get("type") == "AFFECTS" and rel.get("target") in accepted_target_ids:
            keep_ids.add(str(rel.get("source")))
    endpoint_ids = accepted_target_ids | keep_ids
    nodes = [all_nodes[node_id] for node_id in sorted(keep_ids) if node_id in all_nodes]
    relationships = [
        rel
        for rel in all_relationships
        if rel.get("source") in endpoint_ids and rel.get("target") in endpoint_ids
    ]
    return {"nodes": nodes, "relationships": relationships}


def run_validation() -> dict[str, Any]:
    metamodel = load_json(ROOT / "config/metamodel.json")
    policy = load_json(ROOT / "config/relationship-policy.json")
    catalog = load_json(ROOT / "catalog/factsheets.json")
    factsheet_ids = {str(f.get("id")) for f in catalog.get("factsheets", []) if f.get("id")}

    diagnostics: list[Diagnostic] = []
    nodes: dict[str, dict[str, Any]] = {}
    node_decisions: dict[str, str] = {}
    node_artifacts: dict[str, str] = {}

    for path in list_json(NODE_DIR):
        artifact = path.relative_to(ROOT).as_posix()
        try:
            node = load_json(path)
        except Exception as exc:
            diagnostics.append(Diagnostic("BLOCK", "INVALID_JSON", artifact, str(exc)))
            continue
        node_id = str(node.get("id", ""))
        if node_id in nodes and nodes[node_id] != node:
            diagnostics.append(Diagnostic("BLOCK", "DUPLICATE_NODE_ID", artifact, f"ID duplicado também presente em {node_artifacts[node_id]}"))
            node_decisions[node_id] = "rejected"
            continue
        nodes[node_id] = node
        node_artifacts[node_id] = artifact
        node_decisions[node_id] = validate_node(node, artifact, metamodel, factsheet_ids, diagnostics)

    relationships: dict[str, dict[str, Any]] = {}
    rel_decisions: dict[str, str] = {}
    for path in list_json(REL_DIR):
        artifact = path.relative_to(ROOT).as_posix()
        try:
            rel = load_json(path)
        except Exception as exc:
            diagnostics.append(Diagnostic("BLOCK", "INVALID_JSON", artifact, str(exc)))
            continue
        rel_id = str(rel.get("id", ""))
        if rel_id in relationships and relationships[rel_id] != rel:
            diagnostics.append(Diagnostic("BLOCK", "DUPLICATE_RELATIONSHIP_ID", artifact, f"ID de relação duplicado: {rel_id}"))
            rel_decisions[rel_id] = "rejected"
            continue
        relationships[rel_id] = rel
        rel_decisions[rel_id] = validate_relationship(rel, artifact, nodes, policy, diagnostics)
        if rel.get("source") in node_decisions and node_decisions[rel["source"]] != "accepted":
            diagnostics.append(Diagnostic("QUARANTINE", "SOURCE_NOT_ACCEPTED", artifact, "Relação depende de source não aceito"))
            if rel_decisions[rel_id] == "accepted":
                rel_decisions[rel_id] = "quarantine"
        if rel.get("target") in node_decisions and node_decisions[rel["target"]] != "accepted":
            diagnostics.append(Diagnostic("QUARANTINE", "TARGET_NOT_ACCEPTED", artifact, "Relação depende de target não aceito"))
            if rel_decisions[rel_id] == "accepted":
                rel_decisions[rel_id] = "quarantine"

    fitness_policy_path = ROOT / "config/fitness-policy.json"
    fitness_policy = load_json(fitness_policy_path) if fitness_policy_path.exists() else {"enabled": False}
    fitness_result: dict[str, Any] = {"summary": {}, "evaluations": [], "violations": [], "diagnostics": [], "graphArtifacts": {"nodes": [], "relationships": []}}
    if fitness_policy.get("enabled", True):
        phases = set(fitness_policy.get("phasesRunByHarness") or ["SEMANTIC", "GRAPH", "COMPOSITE"])
        preliminary_graph = {
            "nodes": [nodes[k] for k in sorted(nodes) if node_decisions.get(k) == "accepted"],
            "relationships": [relationships[k] for k in sorted(relationships) if rel_decisions.get(k) == "accepted"],
        }
        fitness_result = evaluate_fitness(preliminary_graph, phases=phases)
        apply_fitness_enforcement(fitness_result, nodes, node_decisions, diagnostics, fitness_policy)

    # Uma fitness function pode rebaixar um nó depois da validação das relações.
    # Recalcule a elegibilidade das relações para não publicar endpoints ausentes.
    for rel_id, rel in relationships.items():
        if rel_decisions.get(rel_id) == "rejected":
            continue
        source_decision = node_decisions.get(rel.get("source"))
        target_decision = node_decisions.get(rel.get("target"))
        if source_decision == "rejected" or target_decision == "rejected":
            rel_decisions[rel_id] = "rejected"
        elif source_decision != "accepted" or target_decision != "accepted":
            rel_decisions[rel_id] = "quarantine"

    accepted_nodes = [nodes[k] for k in sorted(nodes) if node_decisions.get(k) == "accepted"]
    quarantine_nodes = [nodes[k] for k in sorted(nodes) if node_decisions.get(k) == "quarantine"]
    rejected_nodes = [nodes[k] for k in sorted(nodes) if node_decisions.get(k) == "rejected"]
    accepted_rels = [relationships[k] for k in sorted(relationships) if rel_decisions.get(k) == "accepted"]
    quarantine_rels = [relationships[k] for k in sorted(relationships) if rel_decisions.get(k) == "quarantine"]
    rejected_rels = [relationships[k] for k in sorted(relationships) if rel_decisions.get(k) == "rejected"]

    result = {
        "schemaVersion": "1.0.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "accepted": {"nodes": accepted_nodes, "relationships": accepted_rels},
        "quarantine": {"nodes": quarantine_nodes, "relationships": quarantine_rels},
        "rejected": {"nodes": rejected_nodes, "relationships": rejected_rels},
        "diagnostics": [asdict(d) for d in diagnostics],
        "fitness": fitness_result,
        "summary": {
            "acceptedNodes": len(accepted_nodes),
            "quarantineNodes": len(quarantine_nodes),
            "rejectedNodes": len(rejected_nodes),
            "acceptedRelationships": len(accepted_rels),
            "quarantineRelationships": len(quarantine_rels),
            "rejectedRelationships": len(rejected_rels),
            "blocks": sum(1 for d in diagnostics if d.severity == "BLOCK"),
            "warnings": sum(1 for d in diagnostics if d.severity == "WARN"),
            "quarantineDiagnostics": sum(1 for d in diagnostics if d.severity == "QUARANTINE"),
            "fitnessDefinitions": fitness_result.get("summary", {}).get("definitions", 0),
            "fitnessEvaluations": fitness_result.get("summary", {}).get("evaluations", 0),
            "fitnessFailures": fitness_result.get("summary", {}).get("fail", 0),
            "fitnessUnknown": fitness_result.get("summary", {}).get("unknown", 0),
            "fitnessWaived": fitness_result.get("summary", {}).get("waived", 0)
        }
    }
    return result


def persist_validation(result: dict[str, Any]) -> None:
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    write_json(HARNESS_DIR / "accepted.json", result["accepted"])
    write_json(HARNESS_DIR / "quarantine.json", result["quarantine"])
    write_json(HARNESS_DIR / "rejected.json", result["rejected"])
    write_json(HARNESS_DIR / "diagnostics.json", {"generatedAt": result["generatedAt"], "summary": result["summary"], "diagnostics": result["diagnostics"]})
    write_json(HARNESS_DIR / "fitness-results.json", result.get("fitness", {}))


def publish(result: dict[str, Any]) -> int:
    persist_validation(result)
    if result["summary"]["blocks"]:
        print("Publicação bloqueada: existem diagnósticos BLOCK.", file=sys.stderr)
        return 2

    previous_path = DIST_DIR / "graph.json"
    previous = load_json(previous_path) if previous_path.exists() else {"nodes": [], "relationships": []}
    accepted_target_ids = {node["id"] for node in result["accepted"]["nodes"]}
    fitness_artifacts = filter_fitness_graph_artifacts(result.get("fitness", {}), accepted_target_ids)
    graph = {
        "schemaVersion": "1.0.0",
        "generatedAt": result["generatedAt"],
        "nodes": result["accepted"]["nodes"] + fitness_artifacts["nodes"],
        "relationships": result["accepted"]["relationships"] + fitness_artifacts["relationships"],
        "fitness": {
            "summary": result.get("fitness", {}).get("summary", {}),
            "definitionsHash": result.get("fitness", {}).get("definitionsHash"),
            "evaluations": result.get("fitness", {}).get("evaluations", [])
        }
    }
    graph["contentHash"] = stable_hash({"nodes": graph["nodes"], "relationships": graph["relationships"]})

    old_nodes = {n["id"]: n for n in previous.get("nodes", [])}
    new_nodes = {n["id"]: n for n in graph.get("nodes", [])}
    old_rels = {r["id"]: r for r in previous.get("relationships", [])}
    new_rels = {r["id"]: r for r in graph.get("relationships", [])}
    diff = {
        "generatedAt": result["generatedAt"],
        "nodes": {
            "added": sorted(set(new_nodes) - set(old_nodes)),
            "removed": sorted(set(old_nodes) - set(new_nodes)),
            "changed": sorted(k for k in set(old_nodes) & set(new_nodes) if old_nodes[k] != new_nodes[k])
        },
        "relationships": {
            "added": sorted(set(new_rels) - set(old_rels)),
            "removed": sorted(set(old_rels) - set(new_rels)),
            "changed": sorted(k for k in set(old_rels) & set(new_rels) if old_rels[k] != new_rels[k])
        }
    }
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    write_json(DIST_DIR / "graph.json", graph)
    write_json(DIST_DIR / "diff.json", diff)
    print(json.dumps({"published": True, "contentHash": graph["contentHash"], "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "publish", "check"])
    args = parser.parse_args()

    result = run_validation()
    persist_validation(result)
    if args.command == "publish":
        return publish(result)

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.command == "check" and result["summary"]["blocks"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
