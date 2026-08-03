#!/usr/bin/env python3
"""Engine determinística de architectural fitness functions para o digital twin.

A engine lê definições YAML/JSON, seleciona os nós aplicáveis, executa o
avaliador correspondente, aplica waivers temporários e produz resultados
estruturados. Ela não publica o grafo; o harness decide o enforcement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator, FormatChecker


class NoDatesSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that keeps ISO dates as strings for JSON Schema."""


NoDatesSafeLoader.yaml_implicit_resolvers = {
    key: [item for item in values if item[0] != "tag:yaml.org,2002:timestamp"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

ROOT = Path(__file__).resolve().parents[1]
FITNESS_SCHEMA = ROOT / "schemas/fitness/fitness-function.schema.json"
WAIVER_SCHEMA = ROOT / "schemas/fitness/architecture-waiver.schema.json"
DEFAULT_DEFINITIONS = ROOT / "fitness/definitions"
DEFAULT_WAIVERS = ROOT / "fitness/waivers"
DEFAULT_GRAPH = ROOT / "dist/graph/graph.json"
DEFAULT_RESULTS = ROOT / "workspace/fitness/results/latest.json"

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "NOT_APPLICABLE"
ERROR = "ERROR"
STALE = "STALE"
WAIVED = "WAIVED"

TERMINAL_FAILURES = {FAIL, ERROR}


@dataclass(frozen=True)
class LoadedDocument:
    path: Path
    value: dict[str, Any]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_data(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.load(text, Loader=NoDatesSafeLoader)
    return json.loads(text)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "unknown"


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def discover_documents(directories: Iterable[Path]) -> list[LoadedDocument]:
    documents: list[LoadedDocument] = []
    for directory in directories:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json"}:
                value = load_data(path)
                if value is not None:
                    if not isinstance(value, dict):
                        raise ValueError(f"{path}: documento deve ser um objeto")
                    documents.append(LoadedDocument(path=path, value=value))
    return documents


def schema_errors(document: dict[str, Any], schema_path: Path) -> list[str]:
    schema = load_data(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    rendered: list[str] = []
    for error in errors:
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        rendered.append(f"{location}: {error.message}")
    return rendered


def get_path(value: Any, path: str, default: Any = None) -> Any:
    current = value
    if not path:
        return current
    for token in path.split("."):
        if isinstance(current, dict) and token in current:
            current = current[token]
        else:
            return default
    return current


def compare(actual: Any, condition: Any) -> bool:
    if not isinstance(condition, dict):
        return actual == condition
    if len(condition) != 1:
        return False
    operator, expected = next(iter(condition.items()))
    if operator == "equals":
        return actual == expected
    if operator == "notEquals":
        return actual != expected
    if operator == "in":
        return actual in expected
    if operator == "notIn":
        return actual not in expected
    if operator == "exists":
        return (actual is not None) is bool(expected)
    if operator == "regex":
        return actual is not None and re.search(str(expected), str(actual)) is not None
    if operator == "contains":
        if isinstance(actual, (list, tuple, set, str)):
            return expected in actual
        return False
    if operator == "gte":
        return isinstance(actual, (int, float)) and actual >= expected
    if operator == "lte":
        return isinstance(actual, (int, float)) and actual <= expected
    return False


def node_matches_selector(node: dict[str, Any], selectors: dict[str, Any] | None) -> bool:
    if not selectors:
        return True
    for path, condition in (selectors.get("fields") or {}).items():
        if not compare(get_path(node, path), condition):
            return False
    attributes = node.get("attributes") or {}
    for path, condition in (selectors.get("attributes") or {}).items():
        if not compare(get_path(attributes, path), condition):
            return False
    return True


def policy_applies(node: dict[str, Any], policy: dict[str, Any]) -> bool:
    applies_to = policy.get("appliesTo") or {}
    lifecycles = applies_to.get("lifecycle")
    states = applies_to.get("architectureState")
    if lifecycles and node.get("lifecycle") not in lifecycles:
        return False
    if states and node.get("architectureState") not in states:
        return False
    return True


def parse_iso_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def definition_is_effective(definition: dict[str, Any], today: date) -> bool:
    policy = definition["spec"]["policy"]
    start = parse_iso_date(policy.get("effectiveFrom"))
    end = parse_iso_date(policy.get("effectiveUntil"))
    return (not start or today >= start) and (not end or today <= end)


def select_targets(definition: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = definition["spec"]["target"]
    node_types = set(target["nodeTypes"])
    selectors = target.get("selectors")
    policy = definition["spec"]["policy"]
    return [
        node
        for node in nodes
        if node.get("type") in node_types
        and node_matches_selector(node, selectors)
        and policy_applies(node, policy)
    ]


def relationship_matches(
    relationship: dict[str, Any],
    target_id: str,
    direction: str,
    relationship_types: set[str],
    target_types: set[str],
    nodes_by_id: dict[str, dict[str, Any]],
) -> bool:
    if relationship_types and relationship.get("type") not in relationship_types:
        return False
    direction = direction.upper()
    other_id: str | None = None
    if direction == "OUT" and relationship.get("source") == target_id:
        other_id = relationship.get("target")
    elif direction == "IN" and relationship.get("target") == target_id:
        other_id = relationship.get("source")
    elif direction == "EITHER":
        if relationship.get("source") == target_id:
            other_id = relationship.get("target")
        elif relationship.get("target") == target_id:
            other_id = relationship.get("source")
    if not other_id:
        return False
    if target_types:
        other = nodes_by_id.get(other_id) or {}
        return other.get("type") in target_types
    return True


def evaluate_graph_query(
    definition: dict[str, Any],
    targets: list[dict[str, Any]],
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    query = definition["spec"]["evaluation"]["query"]
    mode = query.get("mode")
    nodes_by_id = {node["id"]: node for node in graph.get("nodes", [])}
    relationships = graph.get("relationships", [])
    outcomes: list[dict[str, Any]] = []

    if mode in {"relationshipCardinality", "forbiddenRelationship"}:
        relationship_types = set(query.get("relationshipTypes") or ([query["relationship"]] if query.get("relationship") else []))
        target_types = set(query.get("targetNodeTypes") or [])
        direction = query.get("direction", "OUT")
        minimum = int(query.get("minimum", 0))
        maximum = query.get("maximum")
        maximum = int(maximum) if maximum is not None else None
        for target in targets:
            matching = [
                rel
                for rel in relationships
                if relationship_matches(rel, target["id"], direction, relationship_types, target_types, nodes_by_id)
            ]
            count = len(matching)
            if mode == "forbiddenRelationship":
                passed = count == 0
                expected = "0 relações proibidas"
            else:
                passed = count >= minimum and (maximum is None or count <= maximum)
                expected = {"minimum": minimum, "maximum": maximum}
            outcomes.append(
                {
                    "target": target,
                    "status": PASS if passed else FAIL,
                    "observedValue": count,
                    "expected": expected,
                    "message": query.get("passMessage" if passed else "failMessage")
                    or (f"Foram encontradas {count} relações compatíveis." if passed else f"Cardinalidade inválida: {count} relações compatíveis."),
                    "evidenceRefs": [rel.get("id") for rel in matching],
                }
            )
        return outcomes

    if mode == "acyclic":
        relationship_types = set(query.get("relationshipTypes") or [])
        adjacency: dict[str, list[str]] = defaultdict(list)
        for rel in relationships:
            if not relationship_types or rel.get("type") in relationship_types:
                adjacency[str(rel.get("source"))].append(str(rel.get("target")))

        def participates_in_cycle(start: str) -> bool:
            stack: list[tuple[str, set[str]]] = [(start, set())]
            while stack:
                current, path = stack.pop()
                if current in path:
                    return current == start
                next_path = set(path)
                next_path.add(current)
                for nxt in adjacency.get(current, []):
                    if nxt == start:
                        return True
                    if nxt not in next_path:
                        stack.append((nxt, next_path))
            return False

        for target in targets:
            cyclic = participates_in_cycle(target["id"])
            outcomes.append(
                {
                    "target": target,
                    "status": FAIL if cyclic else PASS,
                    "observedValue": {"participatesInCycle": cyclic},
                    "expected": {"participatesInCycle": False},
                    "message": query.get("failMessage" if cyclic else "passMessage")
                    or ("O nó participa de uma dependência cíclica." if cyclic else "Nenhuma dependência cíclica foi encontrada para o nó."),
                    "evidenceRefs": [],
                }
            )
        return outcomes

    return [
        {
            "target": target,
            "status": ERROR,
            "message": f"GRAPH_QUERY mode não suportado: {mode!r}",
            "observedValue": None,
            "expected": None,
            "evidenceRefs": [],
        }
        for target in targets
    ]


def evaluate_assertions(definition: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = definition["spec"]["evaluation"]["query"]
    assertions: list[dict[str, Any]] = list(query.get("assertions") or [])
    for path in query.get("requiredFields") or []:
        assertions.append({"path": path, "operator": "exists", "value": True})
    for path in query.get("requiredAttributes") or []:
        assertions.append({"path": f"attributes.{path}", "operator": "exists", "value": True})

    outcomes: list[dict[str, Any]] = []
    for target in targets:
        failures: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        for assertion in assertions:
            path = assertion["path"]
            operator = assertion.get("operator", "equals")
            expected = assertion.get("value")
            actual = get_path(target, path)
            condition = {operator: expected}
            passed = compare(actual, condition)
            observation = {"path": path, "operator": operator, "expected": expected, "actual": actual}
            observations.append(observation)
            if not passed:
                failures.append(observation)
        passed = not failures
        outcomes.append(
            {
                "target": target,
                "status": PASS if passed else FAIL,
                "observedValue": observations,
                "expected": "todas as assertions verdadeiras",
                "message": query.get("passMessage" if passed else "failMessage")
                or ("Todas as assertions semânticas foram atendidas." if passed else f"Assertions não atendidas: {len(failures)}."),
                "evidenceRefs": [p for p in target.get("provenance", [])],
            }
        )
    return outcomes


def repository_roots(target: dict[str, Any], repository_config: dict[str, Any]) -> list[Path]:
    configured: dict[str, Path] = {}
    for repo in repository_config.get("repositories", []):
        key = str(repo.get("id") or repo.get("name") or repo.get("repository") or "")
        local_path = repo.get("localPath") or repo.get("path")
        if key and local_path:
            configured_path = Path(local_path).expanduser()
            configured[key] = configured_path if configured_path.is_absolute() else ROOT / configured_path

    roots: list[Path] = []
    for ref in target.get("repositoryRefs") or []:
        if isinstance(ref, str):
            if ref in configured:
                roots.append(configured[ref])
            elif Path(ref).exists():
                roots.append(Path(ref))
        elif isinstance(ref, dict):
            local_path = ref.get("localPath") or ref.get("path")
            repo_key = str(ref.get("repository") or ref.get("id") or "")
            if local_path:
                ref_path = Path(local_path).expanduser()
                roots.append(ref_path if ref_path.is_absolute() else ROOT / ref_path)
            elif repo_key in configured:
                roots.append(configured[repo_key])
    return list(dict.fromkeys(root.resolve() for root in roots if root.exists()))


def matching_files(root: Path, include_globs: list[str], exclude_globs: list[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in include_globs:
        found.update(path for path in root.glob(pattern) if path.is_file())
    excluded: set[Path] = set()
    for pattern in exclude_globs:
        excluded.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(found - excluded)


def evaluate_file_query(
    definition: dict[str, Any],
    targets: list[dict[str, Any]],
    repository_config: dict[str, Any],
) -> list[dict[str, Any]]:
    query = definition["spec"]["evaluation"]["query"]
    include_globs = list(query.get("includeGlobs") or [])
    exclude_globs = list(query.get("excludeGlobs") or [])
    must_exist = bool(query.get("mustExist", True))
    require_regex = [re.compile(pattern, re.MULTILINE) for pattern in query.get("requireRegex") or []]
    forbid_regex = [re.compile(pattern, re.MULTILINE) for pattern in query.get("forbidRegex") or []]
    file_mode = str(query.get("fileMode", "ALL")).upper()
    outcomes: list[dict[str, Any]] = []

    for target in targets:
        roots = repository_roots(target, repository_config)
        if not roots:
            outcomes.append({"target": target, "status": UNKNOWN, "message": "Nenhum repositório local foi resolvido para o nó.", "observedValue": None, "expected": include_globs, "evidenceRefs": []})
            continue
        files: list[Path] = []
        for root in roots:
            files.extend(matching_files(root, include_globs, exclude_globs))
        files = sorted(set(files))
        if must_exist and not files:
            outcomes.append({"target": target, "status": FAIL, "message": "Nenhum arquivo obrigatório foi encontrado.", "observedValue": [], "expected": include_globs, "evidenceRefs": []})
            continue
        if not files and not must_exist:
            outcomes.append({"target": target, "status": PASS, "message": "Nenhum arquivo aplicável foi encontrado e mustExist=false.", "observedValue": [], "expected": include_globs, "evidenceRefs": []})
            continue

        checks: list[bool] = []
        evidence: list[str] = []
        problems: list[str] = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append(f"{path}: {exc}")
                checks.append(False)
                continue
            required_ok = all(pattern.search(text) for pattern in require_regex)
            forbidden_ok = not any(pattern.search(text) for pattern in forbid_regex)
            ok = required_ok and forbidden_ok
            checks.append(ok)
            evidence.append(path.as_posix())
            if not ok:
                problems.append(path.as_posix())
        passed = all(checks) if file_mode == "ALL" else any(checks)
        outcomes.append(
            {
                "target": target,
                "status": PASS if passed else FAIL,
                "message": query.get("passMessage" if passed else "failMessage") or ("A política de arquivos foi atendida." if passed else f"Arquivos incompatíveis: {', '.join(problems[:5])}"),
                "observedValue": {"matchedFiles": len(files), "failedFiles": problems},
                "expected": {"includeGlobs": include_globs, "requireRegex": [p.pattern for p in require_regex], "forbidRegex": [p.pattern for p in forbid_regex], "fileMode": file_mode},
                "evidenceRefs": evidence,
            }
        )
    return outcomes


def evaluate_schema_query(
    definition: dict[str, Any],
    targets: list[dict[str, Any]],
    repository_config: dict[str, Any],
) -> list[dict[str, Any]]:
    query = definition["spec"]["evaluation"]["query"]
    include_globs = list(query.get("includeGlobs") or [])
    assertions = list(query.get("assertions") or [])
    document_mode = str(query.get("documentMode", "ANY")).upper()
    outcomes: list[dict[str, Any]] = []

    for target in targets:
        roots = repository_roots(target, repository_config)
        if not roots:
            outcomes.append({"target": target, "status": UNKNOWN, "message": "Nenhum repositório local foi resolvido para o nó.", "observedValue": None, "expected": include_globs, "evidenceRefs": []})
            continue
        files: list[Path] = []
        for root in roots:
            files.extend(matching_files(root, include_globs, []))
        files = sorted(set(files))
        if not files:
            outcomes.append({"target": target, "status": UNKNOWN, "message": "Nenhum schema aplicável foi encontrado.", "observedValue": [], "expected": include_globs, "evidenceRefs": []})
            continue
        document_checks: list[bool] = []
        details: list[dict[str, Any]] = []
        for path in files:
            try:
                document = load_data(path)
                checks = []
                for assertion in assertions:
                    actual = get_path(document, assertion["path"])
                    checks.append(compare(actual, {assertion.get("operator", "equals"): assertion.get("value")}))
                passed = all(checks)
                document_checks.append(passed)
                details.append({"path": path.as_posix(), "passed": passed})
            except Exception as exc:  # malformed schemas are evaluation errors, not engine crashes
                document_checks.append(False)
                details.append({"path": path.as_posix(), "passed": False, "error": str(exc)})
        passed = all(document_checks) if document_mode == "ALL" else any(document_checks)
        outcomes.append(
            {
                "target": target,
                "status": PASS if passed else FAIL,
                "message": query.get("passMessage" if passed else "failMessage") or ("Schema compatível." if passed else "Nenhum schema satisfez as assertions."),
                "observedValue": details,
                "expected": assertions,
                "evidenceRefs": [path.as_posix() for path in files],
            }
        )
    return outcomes


def numeric_compare(actual: float, operator: str, threshold: float) -> bool:
    return {
        "gt": actual > threshold,
        "gte": actual >= threshold,
        "lt": actual < threshold,
        "lte": actual <= threshold,
        "eq": actual == threshold,
        "ne": actual != threshold,
    }.get(operator, False)


def evaluate_runtime_threshold(definition: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query = definition["spec"]["evaluation"]["query"]
    metrics_glob = query.get("metricsGlob", "workspace/evidence/**/runtime/**/*.json")
    metric_path = query["metricPath"]
    target_path = query.get("targetIdPath", "targetId")
    operator = query.get("operator", "lte")
    threshold = float(query["threshold"])
    max_age_hours = query.get("maxAgeHours")
    paths = sorted(ROOT.glob(metrics_glob))
    documents: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        try:
            value = load_data(path)
            if isinstance(value, dict):
                documents.append((path, value))
        except Exception:
            continue
    outcomes: list[dict[str, Any]] = []
    now = utc_now()
    for target in targets:
        matches = [(path, doc) for path, doc in documents if get_path(doc, target_path) == target["id"]]
        if not matches:
            outcomes.append({"target": target, "status": UNKNOWN, "message": "Nenhuma métrica foi encontrada para o alvo.", "observedValue": None, "expected": {"operator": operator, "threshold": threshold}, "evidenceRefs": []})
            continue
        path, document = matches[-1]
        observed = get_path(document, metric_path)
        if not isinstance(observed, (int, float)):
            outcomes.append({"target": target, "status": ERROR, "message": f"Métrica {metric_path!r} não é numérica.", "observedValue": observed, "expected": threshold, "evidenceRefs": [path.as_posix()]})
            continue
        if max_age_hours is not None:
            observed_at = get_path(document, query.get("observedAtPath", "observedAt"))
            try:
                observed_dt = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
                age_hours = (now - observed_dt).total_seconds() / 3600
                if age_hours > float(max_age_hours):
                    outcomes.append({"target": target, "status": STALE, "message": f"Métrica está desatualizada ({age_hours:.1f}h).", "observedValue": observed, "expected": {"maxAgeHours": max_age_hours}, "evidenceRefs": [path.as_posix()]})
                    continue
            except Exception:
                outcomes.append({"target": target, "status": STALE, "message": "Timestamp da métrica ausente ou inválido.", "observedValue": observed, "expected": {"maxAgeHours": max_age_hours}, "evidenceRefs": [path.as_posix()]})
                continue
        passed = numeric_compare(float(observed), operator, threshold)
        outcomes.append({"target": target, "status": PASS if passed else FAIL, "message": query.get("passMessage" if passed else "failMessage") or ("Threshold atendido." if passed else "Threshold violado."), "observedValue": observed, "expected": {"operator": operator, "threshold": threshold}, "evidenceRefs": [path.as_posix()]})
    return outcomes


def evaluate_composite(
    definition: dict[str, Any],
    targets: list[dict[str, Any]],
    prior_evaluations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    query = definition["spec"]["evaluation"]["query"]
    all_of = list(query.get("allOf") or [])
    any_of = list(query.get("anyOf") or [])
    index = {(evaluation["fitnessFunctionId"], evaluation["targetId"]): evaluation for evaluation in prior_evaluations}
    outcomes: list[dict[str, Any]] = []
    for target in targets:
        referenced = [index.get((function_id, target["id"])) for function_id in all_of + any_of]
        if any(item is None for item in referenced):
            outcomes.append({"target": target, "status": UNKNOWN, "message": "Uma ou mais fitness functions referenciadas não possuem avaliação para o alvo.", "observedValue": [item and item.get("status") for item in referenced], "expected": {"allOf": all_of, "anyOf": any_of}, "evidenceRefs": []})
            continue
        all_ok = all(index[(fid, target["id"])]["status"] in {PASS, WAIVED} for fid in all_of) if all_of else True
        any_ok = any(index[(fid, target["id"])]["status"] in {PASS, WAIVED} for fid in any_of) if any_of else True
        passed = all_ok and any_ok
        outcomes.append({"target": target, "status": PASS if passed else FAIL, "message": query.get("passMessage" if passed else "failMessage") or ("Composição atendida." if passed else "Composição de fitness functions violada."), "observedValue": {fid: index[(fid, target["id"])]["status"] for fid in all_of + any_of}, "expected": {"allOf": all_of, "anyOf": any_of}, "evidenceRefs": [index[(fid, target["id"])]["id"] for fid in all_of + any_of]})
    return outcomes


def load_repository_config() -> dict[str, Any]:
    path = ROOT / "config/repositories.json"
    return load_data(path) if path.exists() else {"repositories": []}


def make_evaluation(
    definition_doc: LoadedDocument,
    outcome: dict[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    definition = definition_doc.value
    metadata = definition["metadata"]
    spec = definition["spec"]
    target = outcome["target"]
    function_id = metadata["id"]
    target_id = target.get("id", "urn:corp-arch:unknown:fitness:unknown")
    evaluation_id = f"FE-{slug(function_id)}-{stable_hash(target_id)[:12]}"
    return {
        "id": evaluation_id,
        "fitnessFunctionId": function_id,
        "fitnessFunctionName": metadata["name"],
        "definitionPath": display_path(definition_doc.path),
        "definitionVersion": metadata["version"],
        "phase": spec["evaluation"]["phase"],
        "evaluatorType": spec["evaluation"]["evaluatorType"],
        "targetId": target_id,
        "targetType": target.get("type", "Unknown"),
        "status": outcome["status"],
        "severity": spec["policy"]["severity"],
        "enforcement": spec["policy"]["enforcement"],
        "onUnknown": spec["policy"].get("onUnknown"),
        "violationType": spec["result"]["violationType"],
        "message": outcome.get("message", ""),
        "observedValue": outcome.get("observedValue"),
        "expected": outcome.get("expected"),
        "evidenceRefs": outcome.get("evidenceRefs") or [],
        "remediation": spec["result"]["remediation"],
        "evaluatedAt": evaluated_at,
    }


def waiver_is_active(waiver: dict[str, Any], today: date) -> bool:
    spec = waiver["spec"]
    return parse_iso_date(spec["validFrom"]) <= today <= parse_iso_date(spec["expiresAt"])


def apply_waivers(evaluations: list[dict[str, Any]], waivers: list[LoadedDocument], today: date) -> None:
    for evaluation in evaluations:
        if evaluation["status"] != FAIL:
            continue
        for waiver_doc in waivers:
            waiver = waiver_doc.value
            if not waiver_is_active(waiver, today):
                continue
            spec = waiver["spec"]
            if evaluation["fitnessFunctionId"] in spec["fitnessFunctionIds"] and evaluation["targetId"] in spec["targetIds"]:
                evaluation["originalStatus"] = evaluation["status"]
                evaluation["status"] = WAIVED
                evaluation["waiver"] = {
                    "id": waiver["metadata"]["id"],
                    "name": waiver["metadata"]["name"],
                    "approvedBy": spec["approvedBy"],
                    "expiresAt": spec["expiresAt"],
                    "justification": spec["justification"],
                    "adrRef": spec.get("adrRef"),
                    "path": display_path(waiver_doc.path),
                }
                break


def provenance_for_file(path: str) -> list[dict[str, Any]]:
    return [{
        "repository": "corporate-architecture-twin",
        "commit": "WORKTREE",
        "path": path,
        "lineStart": 1,
        "lineEnd": 1,
        "extractor": "fitness-engine",
    }]


def graph_artifacts(definitions: list[LoadedDocument], evaluations: list[dict[str, Any]], waivers: list[LoadedDocument]) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    function_urns: dict[str, str] = {}
    for doc in definitions:
        metadata = doc.value["metadata"]
        spec = doc.value["spec"]
        function_urn = f"urn:corp-arch:fitness-function:fitness:{slug(metadata['id'])}"
        function_urns[metadata["id"]] = function_urn
        nodes.append({
            "id": function_urn,
            "type": "FitnessFunction",
            "name": metadata["name"],
            "canonicalName": slug(metadata["id"]),
            "description": metadata["description"],
            "metamodelOrigin": "TWIN_EXTENSION",
            "lifecycle": "ACTIVE",
            "architectureState": "CURRENT",
            "owners": [metadata["owner"]],
            "provenance": provenance_for_file(display_path(doc.path)),
            "inference": {"epistemicStatus": "EXPLICIT", "confidence": 1.0, "agent": "fitness-engine", "rationale": "Definição declarativa versionada."},
            "attributes": {
                "definitionId": metadata["id"],
                "version": metadata["version"],
                "phase": spec["evaluation"]["phase"],
                "evaluatorType": spec["evaluation"]["evaluatorType"],
                "severity": spec["policy"]["severity"],
                "enforcement": spec["policy"]["enforcement"],
                "targetNodeTypes": spec["target"]["nodeTypes"],
            },
        })

    waiver_nodes: dict[str, str] = {}
    for waiver_doc in waivers:
        waiver = waiver_doc.value
        urn = f"urn:corp-arch:architecture-waiver:fitness:{slug(waiver['metadata']['id'])}"
        waiver_nodes[waiver["metadata"]["id"]] = urn
        nodes.append({
            "id": urn,
            "type": "ArchitectureWaiver",
            "name": waiver["metadata"]["name"],
            "canonicalName": slug(waiver["metadata"]["id"]),
            "description": waiver["spec"]["justification"],
            "metamodelOrigin": "TWIN_EXTENSION",
            "lifecycle": "ACTIVE" if waiver_is_active(waiver, utc_now().date()) else "END_OF_LIFE",
            "architectureState": "CURRENT",
            "owners": [waiver["metadata"]["owner"]],
            "provenance": provenance_for_file(display_path(waiver_doc.path)),
            "inference": {"epistemicStatus": "EXPLICIT", "confidence": 1.0, "agent": "fitness-engine", "rationale": "Waiver arquitetural declarado e versionado."},
            "attributes": {"waiverId": waiver["metadata"]["id"], "approvedBy": waiver["spec"]["approvedBy"], "validFrom": waiver["spec"]["validFrom"], "expiresAt": waiver["spec"]["expiresAt"], "adrRef": waiver["spec"].get("adrRef")},
        })

    for evaluation in evaluations:
        function_urn = function_urns.get(evaluation["fitnessFunctionId"])
        if not function_urn or evaluation["targetId"].endswith(":not-applicable"):
            continue
        relationship_base = slug(evaluation["id"])
        relationships.append({
            "id": f"urn:corp-arch:relationship:fitness:applies-{relationship_base}",
            "type": "APPLIES_TO",
            "source": function_urn,
            "target": evaluation["targetId"],
            "description": f"{evaluation['fitnessFunctionName']} aplica-se ao alvo avaliado.",
            "provenance": provenance_for_file(evaluation["definitionPath"]),
            "inference": {"epistemicStatus": "EXPLICIT", "confidence": 1.0, "agent": "fitness-engine", "rationale": "Target selecionado deterministicamente pelo DSL."},
            "attributes": {"evaluationId": evaluation["id"], "status": evaluation["status"]},
        })
        if evaluation["status"] not in {FAIL, WAIVED}:
            continue
        violation_urn = f"urn:corp-arch:fitness-violation:fitness:{relationship_base}"
        nodes.append({
            "id": violation_urn,
            "type": "FitnessViolation",
            "name": f"{evaluation['fitnessFunctionId']} — {evaluation['targetId'].split(':')[-1]}",
            "canonicalName": relationship_base,
            "description": evaluation["message"] or f"Violação da fitness function {evaluation['fitnessFunctionId']}.",
            "metamodelOrigin": "TWIN_EXTENSION",
            "lifecycle": "ACTIVE" if evaluation["status"] == FAIL else "PHASE_OUT",
            "architectureState": "CURRENT",
            "provenance": provenance_for_file(evaluation["definitionPath"]),
            "inference": {"epistemicStatus": "EXPLICIT", "confidence": 1.0, "agent": "fitness-engine", "rationale": "Resultado produzido por avaliador determinístico."},
            "attributes": {"evaluationId": evaluation["id"], "fitnessFunctionId": evaluation["fitnessFunctionId"], "status": evaluation["status"], "severity": evaluation["severity"], "enforcement": evaluation["enforcement"], "violationType": evaluation["violationType"], "remediation": evaluation["remediation"], "waiver": evaluation.get("waiver")},
        })
        for relation_type, target in (("INSTANCE_OF", function_urn), ("AFFECTS", evaluation["targetId"])):
            relationships.append({
                "id": f"urn:corp-arch:relationship:fitness:{slug(relation_type)}-{relationship_base}",
                "type": relation_type,
                "source": violation_urn,
                "target": target,
                "description": f"Relacionamento materializado pela avaliação {evaluation['id']}.",
                "provenance": provenance_for_file(evaluation["definitionPath"]),
                "inference": {"epistemicStatus": "EXPLICIT", "confidence": 1.0, "agent": "fitness-engine", "rationale": "Resultado determinístico."},
                "attributes": {"evaluationId": evaluation["id"]},
            })
        if evaluation.get("waiver"):
            waiver_urn = waiver_nodes.get(evaluation["waiver"]["id"])
            if waiver_urn:
                relationships.append({
                    "id": f"urn:corp-arch:relationship:fitness:waives-{relationship_base}",
                    "type": "WAIVES",
                    "source": waiver_urn,
                    "target": violation_urn,
                    "description": f"Waiver temporário para a avaliação {evaluation['id']}.",
                    "provenance": provenance_for_file(evaluation["waiver"]["path"]),
                    "inference": {"epistemicStatus": "EXPLICIT", "confidence": 1.0, "agent": "fitness-engine", "rationale": "Waiver ativo e compatível."},
                    "attributes": {"expiresAt": evaluation["waiver"]["expiresAt"]},
                })
    return {"nodes": nodes, "relationships": relationships}


def evaluate_fitness(
    graph: dict[str, Any],
    phases: set[str] | None = None,
    definition_directories: list[Path] | None = None,
    waiver_directories: list[Path] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    today = now.date()
    definition_directories = definition_directories or [DEFAULT_DEFINITIONS]
    waiver_directories = waiver_directories or [DEFAULT_WAIVERS]
    diagnostics: list[dict[str, Any]] = []

    raw_definitions = discover_documents(definition_directories)
    definitions: list[LoadedDocument] = []
    for document in raw_definitions:
        errors = schema_errors(document.value, FITNESS_SCHEMA)
        if errors:
            diagnostics.append({"severity": "BLOCK", "code": "INVALID_FITNESS_DEFINITION", "artifact": display_path(document.path), "message": "; ".join(errors)})
        else:
            definitions.append(document)

    raw_waivers = discover_documents(waiver_directories)
    waivers: list[LoadedDocument] = []
    for document in raw_waivers:
        errors = schema_errors(document.value, WAIVER_SCHEMA)
        if errors:
            diagnostics.append({"severity": "BLOCK", "code": "INVALID_ARCHITECTURE_WAIVER", "artifact": display_path(document.path), "message": "; ".join(errors)})
        else:
            waivers.append(document)

    duplicate_ids: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        duplicate_ids[definition.value["metadata"]["id"]].append(display_path(definition.path))
    for function_id, paths in duplicate_ids.items():
        if len(paths) > 1:
            diagnostics.append({"severity": "BLOCK", "code": "DUPLICATE_FITNESS_FUNCTION_ID", "artifact": ", ".join(paths), "message": f"ID duplicado: {function_id}"})
    definitions = [doc for doc in definitions if len(duplicate_ids[doc.value["metadata"]["id"]]) == 1]

    repository_config = load_repository_config()
    nodes = graph.get("nodes", [])
    evaluated_at = now.isoformat()
    evaluations: list[dict[str, Any]] = []
    applicable_definitions = [doc for doc in definitions if definition_is_effective(doc.value, today) and (phases is None or doc.value["spec"]["evaluation"]["phase"] in phases)]
    regular = [doc for doc in applicable_definitions if doc.value["spec"]["evaluation"]["evaluatorType"] != "COMPOSITE"]
    composites = [doc for doc in applicable_definitions if doc.value["spec"]["evaluation"]["evaluatorType"] == "COMPOSITE"]

    for definition_doc in regular:
        definition = definition_doc.value
        targets = select_targets(definition, nodes)
        if not targets:
            placeholder = {"id": "urn:corp-arch:unknown:fitness:not-applicable", "type": "Unknown"}
            evaluations.append(make_evaluation(definition_doc, {"target": placeholder, "status": NOT_APPLICABLE, "message": "Nenhum nó corresponde ao target da fitness function.", "observedValue": 0, "expected": definition["spec"]["target"], "evidenceRefs": []}, evaluated_at))
            continue
        evaluator_type = definition["spec"]["evaluation"]["evaluatorType"]
        try:
            if evaluator_type == "GRAPH_QUERY":
                outcomes = evaluate_graph_query(definition, targets, graph)
            elif evaluator_type in {"SEMANTIC_ASSERTION", "STATIC_ASSERTION"}:
                outcomes = evaluate_assertions(definition, targets)
            elif evaluator_type in {"FILE_QUERY", "IAC_POLICY"}:
                outcomes = evaluate_file_query(definition, targets, repository_config)
            elif evaluator_type == "SCHEMA_QUERY":
                outcomes = evaluate_schema_query(definition, targets, repository_config)
            elif evaluator_type == "RUNTIME_THRESHOLD":
                outcomes = evaluate_runtime_threshold(definition, targets)
            elif evaluator_type == "LLM_ASSISTED":
                outcomes = [{"target": target, "status": UNKNOWN, "message": "LLM_ASSISTED produz somente recomendação; revisão determinística ou humana é necessária.", "observedValue": None, "expected": definition["spec"]["evaluation"]["query"], "evidenceRefs": []} for target in targets]
            else:
                outcomes = [{"target": target, "status": ERROR, "message": f"Evaluator não suportado: {evaluator_type}", "observedValue": None, "expected": None, "evidenceRefs": []} for target in targets]
        except Exception as exc:
            outcomes = [{"target": target, "status": ERROR, "message": f"Falha no evaluator: {exc}", "observedValue": None, "expected": None, "evidenceRefs": []} for target in targets]
        evaluations.extend(make_evaluation(definition_doc, outcome, evaluated_at) for outcome in outcomes)

    for definition_doc in composites:
        targets = select_targets(definition_doc.value, nodes)
        if not targets:
            placeholder = {"id": "urn:corp-arch:unknown:fitness:not-applicable", "type": "Unknown"}
            evaluations.append(make_evaluation(definition_doc, {"target": placeholder, "status": NOT_APPLICABLE, "message": "Nenhum nó corresponde ao target da fitness function.", "observedValue": 0, "expected": definition_doc.value["spec"]["target"], "evidenceRefs": []}, evaluated_at))
            continue
        outcomes = evaluate_composite(definition_doc.value, targets, evaluations)
        evaluations.extend(make_evaluation(definition_doc, outcome, evaluated_at) for outcome in outcomes)

    apply_waivers(evaluations, waivers, today)
    violations = [evaluation for evaluation in evaluations if evaluation["status"] == FAIL]
    effective_definitions = [doc for doc in definitions if definition_is_effective(doc.value, today)]
    artifacts = graph_artifacts(effective_definitions, evaluations, waivers)
    statuses = defaultdict(int)
    for evaluation in evaluations:
        statuses[evaluation["status"]] += 1
    result = {
        "schemaVersion": "1.0.0",
        "generatedAt": evaluated_at,
        "phases": sorted(phases) if phases else ["ALL"],
        "definitionsHash": stable_hash([doc.value for doc in applicable_definitions]),
        "definitionCount": len(applicable_definitions),
        "loadedDefinitionCount": len(definitions),
        "effectiveDefinitionCount": len(effective_definitions),
        "waiverCount": len(waivers),
        "evaluations": evaluations,
        "violations": violations,
        "diagnostics": diagnostics,
        "graphArtifacts": artifacts,
        "summary": {
            "definitions": len(applicable_definitions),
            "loadedDefinitions": len(definitions),
            "effectiveDefinitions": len(effective_definitions),
            "evaluations": len(evaluations),
            "pass": statuses[PASS],
            "fail": statuses[FAIL],
            "unknown": statuses[UNKNOWN],
            "notApplicable": statuses[NOT_APPLICABLE],
            "error": statuses[ERROR],
            "stale": statuses[STALE],
            "waived": statuses[WAIVED],
            "invalidDefinitions": sum(1 for item in diagnostics if item["code"] == "INVALID_FITNESS_DEFINITION"),
            "invalidWaivers": sum(1 for item in diagnostics if item["code"] == "INVALID_ARCHITECTURE_WAIVER"),
        },
    }
    return result


def load_graph(path: Path) -> dict[str, Any]:
    value = load_data(path)
    if "accepted" in value:
        return value["accepted"]
    return value


def cli_validate() -> int:
    graph = {"nodes": [], "relationships": []}
    result = evaluate_fitness(graph, phases=None)
    diagnostics = result["diagnostics"]
    print(json.dumps({"definitions": result["definitionCount"], "waivers": result["waiverCount"], "diagnostics": diagnostics}, ensure_ascii=False, indent=2))
    return 2 if any(item["severity"] == "BLOCK" for item in diagnostics) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-definitions", help="Valida DSL e waivers sem executar regras.")
    evaluate_parser = subparsers.add_parser("evaluate", help="Executa fitness functions contra um snapshot de grafo.")
    evaluate_parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    evaluate_parser.add_argument("--phase", action="append", choices=["EVIDENCE", "SEMANTIC", "GRAPH", "RUNTIME", "DOCUMENTATION", "COMPOSITE"])
    evaluate_parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()

    if args.command == "validate-definitions":
        return cli_validate()
    if not args.graph.exists():
        print(f"Grafo não encontrado: {args.graph}", file=sys.stderr)
        return 2
    result = evaluate_fitness(load_graph(args.graph), phases=set(args.phase) if args.phase else None)
    write_json(args.output, result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 2 if any(item["severity"] == "BLOCK" for item in result["diagnostics"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
