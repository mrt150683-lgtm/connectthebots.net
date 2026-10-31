"""Run the shipped standard-library suite without pretending to deny egress."""
from __future__ import annotations

import importlib.util
import hashlib
import io
import platform
from pathlib import Path
import time
from typing import Any
import unittest
import xml.etree.ElementTree as ET

from .audit import write_json


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.records: list[dict[str, str]] = []

    def addSuccess(self, test: Any) -> None:
        super().addSuccess(test)
        self.records.append({"test_id": test.id(), "status": "passed"})

    def addFailure(self, test: Any, err: Any) -> None:
        super().addFailure(test, err)
        self.records.append({"test_id": test.id(), "status": "failed", "detail": self._exc_info_to_string(err, test)})

    def addError(self, test: Any, err: Any) -> None:
        super().addError(test, err)
        self.records.append({"test_id": test.id(), "status": "error", "detail": self._exc_info_to_string(err, test)})

    def addSkip(self, test: Any, reason: str) -> None:
        super().addSkip(test, reason)
        self.records.append({"test_id": test.id(), "status": "skipped", "detail": reason})

    def addSubTest(self, test: Any, subtest: Any, err: Any) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None:
            status = "failed" if issubclass(err[0], test.failureException) else "error"
            self.records.append({"test_id": subtest.id(), "status": status,
                                 "detail": self._exc_info_to_string(err, test)})


def run_verification(output: Path | None = None) -> dict[str, Any]:
    spec = importlib.util.find_spec("agentmatch_selftest")
    if spec is not None and spec.origin:
        start = Path(spec.origin).parent
    else:
        start = Path(__file__).resolve().parents[2] / "tests"
    if not (start / "test_matching.py").is_file():
        raise ValueError("TEST_SUITE_MISSING: use the complete source checkout or official wheel")
    loader = unittest.TestLoader()
    suite = loader.discover(str(start), pattern="test_*.py", top_level_dir=str(start.parent))
    stream = io.StringIO()
    began = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordingResult).run(suite)
    elapsed = time.perf_counter() - began
    records = result.records
    suite_hash = hashlib.sha256()
    for source in sorted(start.glob("*.py")):
        suite_hash.update(source.name.encode() + b"\0" + source.read_bytes())
    report = {"release": "R0", "synthetic_only": True, "success": result.wasSuccessful(),
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "checks": records,
              "test_suite_sha256": suite_hash.hexdigest(),
              "measurements": {"elapsed_seconds": round(elapsed, 4), "python": platform.python_version(),
                               "platform": platform.platform()},
              "network_enforcement": "Not provided by --offline. See external isolation harness report.",
              "not_tested": ["Real-history import and encrypted vault", "Local-model accuracy and speed",
                             "Actual passkey registration and recovery", "WebRTC signaling/encryption/connectivity",
                             "Durable encrypted queues and crash recovery", "Human usefulness or dating compatibility",
                             "Mac Studio or Ryzen hardware performance"]}
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "verification.json", report)
        (output / "test_output.txt").write_text(stream.getvalue(), encoding="utf-8")
        xml = ET.Element("testsuite", name="AgentMatch R0", tests=str(result.testsRun),
                         failures=str(len(result.failures)), errors=str(len(result.errors)),
                         skipped=str(len(result.skipped)), time=f"{elapsed:.4f}")
        for record in records:
            case = ET.SubElement(xml, "testcase", name=record["test_id"])
            if record["status"] != "passed":
                node = ET.SubElement(case, {"failed": "failure", "error": "error", "skipped": "skipped"}[record["status"]])
                node.text = record.get("detail", "")
        ET.indent(xml)
        ET.ElementTree(xml).write(output / "test_results.xml", encoding="utf-8", xml_declaration=True)
    return report
