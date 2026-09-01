from pathlib import Path
import json
import sys
import unittest
from datetime import datetime

root = Path(__file__).resolve().parents[1]
patterns = ["test_model_self_support.py", "test_flat_base_gate.py", "test_m4_bambu_auto_orient.py", "test_verified_print_preparation.py",
            "test_general_printability_geometry_repair*.py", "test_geometry_fidelity_gate.py",
            "test_local_self_support_envelope.py", "test_printability_blocker_clustering.py",
            "test_printability_support_enforcer.py", "test_fdm_layer_printability_gate.py",
            "test_variable_layer_height_printability.py", "test_m2_normalization.py",
            "test_m2_normalized_validation.py", "test_m2_fdm_prompt_contract*.py", "test_m2_providers.py",
            "test_automatic_print_workflow.py", "test_gcode_motion_state_parser.py",
            "test_semantic_pose.py", "test_triposg_local_provider.py", "test_stl_handoff.py",
            "test_normalization_pipeline_v2.py", "test_m3_printable_orientation.py", "test_mesh_repair_pipeline.py"]
sys.path.insert(0, str(root / "tests"))
reports = []
for pattern in patterns:
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    reports.append({"pattern": pattern, "tests": result.testsRun, "passed": result.wasSuccessful(),
                    "failures": [str(t) for t, _ in result.failures], "errors": [str(t) for t, _ in result.errors]})
    print(pattern, result.testsRun, result.wasSuccessful(), flush=True)
out = root / "outputs/printable_foundation_validation" / datetime.now().strftime("regressions_%Y%m%d_%H%M%S.json")
out.write_text(json.dumps({"tests": sum(r["tests"] for r in reports), "passed": all(r["passed"] for r in reports), "suites": reports}, indent=2), encoding="utf-8")
print(out, flush=True)
sys.exit(0 if all(r["passed"] for r in reports) else 1)
