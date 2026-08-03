from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from harness import apply_fitness_enforcement  # noqa: E402


class HarnessFitnessTests(unittest.TestCase):
    def test_quarantine_enforcement_reclassifies_target(self):
        target_id = "urn:corp-arch:application:test:orders"
        nodes = {target_id: {"id": target_id}}
        decisions = {target_id: "accepted"}
        diagnostics = []
        fitness = {
            "diagnostics": [],
            "evaluations": [{
                "fitnessFunctionId": "FF-TEST-001",
                "fitnessFunctionName": "Test rule",
                "targetId": target_id,
                "status": "FAIL",
                "enforcement": "QUARANTINE",
                "message": "Test violation",
            }],
        }
        apply_fitness_enforcement(fitness, nodes, decisions, diagnostics, {"defaultUnknownEnforcement": "QUARANTINE", "defaultErrorEnforcement": "BLOCK"})
        self.assertEqual("quarantine", decisions[target_id])
        self.assertEqual("QUARANTINE", diagnostics[0].severity)


if __name__ == "__main__":
    unittest.main()
