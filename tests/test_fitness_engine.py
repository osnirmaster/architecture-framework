from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fitness_engine import evaluate_fitness  # noqa: E402


def node(node_id: str, node_type: str, owners=None):
    value = {
        "id": node_id,
        "type": node_type,
        "name": node_id.split(":")[-1],
        "lifecycle": "ACTIVE",
        "architectureState": "CURRENT",
        "attributes": {},
        "provenance": [{"path": "fixture"}],
    }
    if owners is not None:
        value["owners"] = owners
    return value


class FitnessEngineTests(unittest.TestCase):
    def test_semantic_owner_failure(self):
        graph = {"nodes": [node("urn:corp-arch:application:test:orders", "Application")], "relationships": []}
        result = evaluate_fitness(graph, phases={"SEMANTIC"})
        evaluation = next(item for item in result["evaluations"] if item["fitnessFunctionId"] == "FF-APP-001")
        self.assertEqual("FAIL", evaluation["status"])
        self.assertEqual("WARN", evaluation["enforcement"])

    def test_business_mapping_passes_with_supports_relation(self):
        app = node("urn:corp-arch:application:test:orders", "Application", ["orders-team"])
        capability = node("urn:corp-arch:capability:test:order-management", "Capability")
        graph = {
            "nodes": [app, capability],
            "relationships": [{"id": "rel-1", "type": "SUPPORTS", "source": app["id"], "target": capability["id"]}],
        }
        result = evaluate_fitness(graph, phases={"GRAPH"})
        evaluation = next(item for item in result["evaluations"] if item["fitnessFunctionId"] == "FF-META-001")
        self.assertEqual("PASS", evaluation["status"])

    def test_cycle_is_blocking(self):
        a = node("urn:corp-arch:application-component:test:a", "ApplicationComponent")
        b = node("urn:corp-arch:application-component:test:b", "ApplicationComponent")
        graph = {
            "nodes": [a, b],
            "relationships": [
                {"id": "rel-a-b", "type": "DEPENDS_ON", "source": a["id"], "target": b["id"]},
                {"id": "rel-b-a", "type": "DEPENDS_ON", "source": b["id"], "target": a["id"]},
            ],
        }
        result = evaluate_fitness(graph, phases={"GRAPH"})
        evaluations = [item for item in result["evaluations"] if item["fitnessFunctionId"] == "FF-GRAPH-001"]
        self.assertTrue(evaluations)
        self.assertTrue(all(item["status"] == "FAIL" for item in evaluations))
        self.assertTrue(all(item["enforcement"] == "BLOCK" for item in evaluations))

    def test_active_waiver_changes_fail_to_waived(self):
        component = node("urn:corp-arch:application-component:test:a", "ApplicationComponent")
        graph = {
            "nodes": [component],
            "relationships": [{"id": "self", "type": "DEPENDS_ON", "source": component["id"], "target": component["id"]}],
        }
        waiver = f"""apiVersion: architecture.company/v1
kind: ArchitectureWaiver
metadata:
  id: AW-TEST-001
  name: Test waiver for cyclic dependency
  owner: architecture-test
spec:
  fitnessFunctionIds: [FF-GRAPH-001]
  targetIds: [{component['id']}]
  justification: This temporary test waiver is intentionally long enough for schema validation.
  approvedBy: architecture-review-board
  validFrom: 2026-08-01
  expiresAt: 2026-08-31
  adrRef: ADR-TEST-001
"""
        with tempfile.TemporaryDirectory() as directory:
            waiver_dir = Path(directory)
            (waiver_dir / "waiver.yaml").write_text(waiver, encoding="utf-8")
            result = evaluate_fitness(
                graph,
                phases={"GRAPH"},
                waiver_directories=[waiver_dir],
                now=datetime(2026, 8, 2, tzinfo=timezone.utc),
            )
        evaluation = next(item for item in result["evaluations"] if item["fitnessFunctionId"] == "FF-GRAPH-001")
        self.assertEqual("WAIVED", evaluation["status"])
        self.assertEqual("AW-TEST-001", evaluation["waiver"]["id"])

    def test_iac_policy_detects_latest_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "Dockerfile").write_text("FROM python:latest\n", encoding="utf-8")
            component = node("urn:corp-arch:application-component:test:container", "ApplicationComponent")
            component["repositoryRefs"] = [{"localPath": directory}]
            result = evaluate_fitness({"nodes": [component], "relationships": []}, phases={"EVIDENCE"})
        evaluation = next(item for item in result["evaluations"] if item["fitnessFunctionId"] == "FF-IAC-001")
        self.assertEqual("FAIL", evaluation["status"])

    def test_schema_query_accepts_oauth2_openapi(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "openapi.yaml").write_text(
                "openapi: 3.0.3\ncomponents:\n  securitySchemes:\n    oauth2:\n      type: oauth2\n",
                encoding="utf-8",
            )
            service = node("urn:corp-arch:application-service:test:public-api", "ApplicationService")
            service["attributes"] = {"exposure": "PUBLIC"}
            service["repositoryRefs"] = [{"localPath": directory}]
            result = evaluate_fitness({"nodes": [service], "relationships": []}, phases={"EVIDENCE"})
        evaluation = next(item for item in result["evaluations"] if item["fitnessFunctionId"] == "FF-API-001")
        self.assertEqual("PASS", evaluation["status"])


if __name__ == "__main__":
    unittest.main()
