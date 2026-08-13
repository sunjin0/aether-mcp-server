import subprocess
import unittest
import os
from unittest.mock import patch

# Runner deliberately rejects a missing production token. Unit tests supply an
# inert value before importing the module so no host configuration is needed.
os.environ.setdefault("AETHER_SANDBOX_RUNNER_TOKEN", "test-runner-token")
import runner


class _SuccessfulProcess:
    returncode = 0

    def poll(self):
        return 0

    def communicate(self):
        return "", ""

    def kill(self):
        self.returncode = -9


class RunnerV2UsageTest(unittest.TestCase):
    def v2_task(self, task_id="a"):
        return {
            "taskId": task_id * 32, "runtime": "PYTHON", "executionMode": "SCRIPT", "timeoutSeconds": 10,
            "maxMemoryMb": 128, "maxCpuCores": 1, "maxPids": 64, "maxTempDiskMb": 32,
            "maxOutputFiles": 10, "maxOutputBytes": 1024, "outputFormats": ["csv", "md", "json"],
            "input": {"script": "print('ok')", "scriptLanguage": "PYTHON"}, "inputArtifacts": [],
        }

    def test_successful_task_reports_usage_after_output_is_collected(self):
        task = {
            "taskId": "a" * 32, "runtime": "PYTHON", "executionMode": "SCRIPT",
            "timeoutSeconds": 10, "maxMemoryMb": 128, "maxCpuCores": 1,
            "maxPids": 64, "maxTempDiskMb": 32, "maxOutputFiles": 1,
            "maxOutputBytes": 1024, "outputFormats": ["csv"],
            "input": {"script": "print('ok')", "scriptLanguage": "PYTHON"},
            "inputArtifacts": [],
        }
        completed = subprocess.CompletedProcess([], 0, stdout=b"result.csv\n")
        with patch.object(runner, "volume_write"), \
             patch.object(runner, "volume_read", return_value=b"a,b"), \
             patch.object(runner, "volume_command", return_value=completed), \
             patch.object(runner.subprocess, "run"), \
             patch.object(runner.subprocess, "Popen", return_value=_SuccessfulProcess()), \
             patch.object(runner, "v2_complete") as complete, \
             patch.object(runner, "v2_usage") as usage:
            runner.run_v2(task)
        complete.assert_called_once()
        usage.assert_called_once()
        _, wall_millis, output_bytes, exit_code = usage.call_args.args
        self.assertGreaterEqual(wall_millis, 0)
        self.assertEqual(3, output_bytes)
        self.assertEqual(0, exit_code)

    def test_usage_is_reported_before_final_artifact_callback(self):
        task = {
            "taskId": "e" * 32, "runtime": "PYTHON", "executionMode": "SCRIPT", "timeoutSeconds": 10,
            "maxMemoryMb": 128, "maxCpuCores": 1, "maxPids": 64, "maxTempDiskMb": 32,
            "maxOutputFiles": 1, "maxOutputBytes": 1024, "outputFormats": ["csv"],
            "input": {"script": "print('ok')", "scriptLanguage": "PYTHON"}, "inputArtifacts": [],
        }
        calls = []
        def record_usage(*_): calls.append("usage")
        def record_artifact(*_): calls.append("artifact")
        with patch.object(runner, "volume_write"), patch.object(runner, "volume_read", return_value=b"a,b"), \
             patch.object(runner, "volume_command", return_value=subprocess.CompletedProcess([], 0, stdout=b"result.csv\n")), \
             patch.object(runner.subprocess, "run"), patch.object(runner.subprocess, "Popen", return_value=_SuccessfulProcess()), \
             patch.object(runner, "v2_usage", side_effect=record_usage), patch.object(runner, "v2_complete", side_effect=record_artifact):
            runner.run_v2(task)
        self.assertEqual(["usage", "artifact"], calls)

    def test_multiple_declared_outputs_are_returned_with_only_last_marked_final(self):
        task = self.v2_task("m")
        with patch.object(runner, "volume_write"), \
             patch.object(runner, "volume_read", side_effect=[b"a,b", b"# report"]), \
             patch.object(runner, "volume_command", return_value=subprocess.CompletedProcess([], 0, stdout=b"result.csv\nreport.md\n")), \
             patch.object(runner.subprocess, "run"), \
             patch.object(runner.subprocess, "Popen", return_value=_SuccessfulProcess()), \
             patch.object(runner, "v2_complete") as complete, \
             patch.object(runner, "v2_usage") as usage:
            runner.run_v2(task)
        self.assertEqual(2, complete.call_count)
        self.assertEqual(False, complete.call_args_list[0].args[-1])
        self.assertEqual(True, complete.call_args_list[1].args[-1])
        self.assertEqual(11, usage.call_args.args[2])

    def test_input_hash_mismatch_is_rejected_before_sandbox_starts(self):
        task = self.v2_task("h")
        task["inputArtifacts"] = [{"id": "i" * 32, "fileName": "source.csv", "size": 3, "sha256": "0" * 64}]
        with patch.object(runner, "v2_input", return_value=b"a,b"), \
             patch.object(runner, "volume_write"), \
             patch.object(runner.subprocess, "run"), \
             patch.object(runner.subprocess, "Popen") as popen, \
             patch.object(runner, "v2_fail") as fail, \
             patch.object(runner, "v2_usage"):
            runner.run_v2(task)
        popen.assert_not_called()
        self.assertIn("checksum mismatch", fail.call_args.args[1])

    def test_setup_failure_still_reports_zero_output_usage(self):
        task = {"taskId": "b" * 32, "runtime": "PYTHON", "executionMode": "WEB_COLLECTION"}
        with patch.object(runner, "v2_fail"), \
             patch.object(runner, "v2_usage") as usage, \
             patch.object(runner.subprocess, "run"):
            runner.run_v2(task)
        usage.assert_called_once()
        _, wall_millis, output_bytes, exit_code = usage.call_args.args
        self.assertGreaterEqual(wall_millis, 0)
        self.assertEqual(0, output_bytes)
        self.assertIsNone(exit_code)

    def test_invalid_template_image_reports_failure_instead_of_stranding_lease(self):
        task = {"taskId": "c" * 32, "runtime": "PYTHON", "executionMode": "SCRIPT", "imageRef": "python:latest"}
        with patch.object(runner, "v2_fail") as fail, \
             patch.object(runner, "v2_usage") as usage, \
             patch.object(runner.subprocess, "run"):
            runner.run_v2(task)
        fail.assert_called_once()
        self.assertIn("digest-pinned", fail.call_args.args[1])
        usage.assert_called_once()

    def test_timeout_reports_timed_out_code(self):
        task = {
            "taskId": "d" * 32, "runtime": "PYTHON", "executionMode": "SCRIPT",
            "timeoutSeconds": 1, "maxMemoryMb": 128, "maxCpuCores": 1,
            "maxPids": 64, "maxTempDiskMb": 32, "maxOutputFiles": 1,
            "maxOutputBytes": 1024, "outputFormats": ["csv"],
            "input": {"script": "print('ok')", "scriptLanguage": "PYTHON"},
            "inputArtifacts": [],
        }
        process = _SuccessfulProcess()
        process.poll = lambda: None
        with patch.object(runner, "volume_write"), \
             patch.object(runner, "volume_command", return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(runner.subprocess, "run"), \
             patch.object(runner.subprocess, "Popen", return_value=process), \
             patch.object(runner.time, "monotonic", side_effect=[0, 0, 2, 3]), \
             patch.object(runner, "v2_fail") as fail, \
             patch.object(runner, "v2_usage"):
            runner.run_v2(task)
        self.assertEqual("TIMED_OUT", fail.call_args.args[3])

    def test_running_cancellation_reports_cancelled_code(self):
        task = {
            "taskId": "f" * 32, "runtime": "PYTHON", "executionMode": "SCRIPT", "timeoutSeconds": 10,
            "maxMemoryMb": 128, "maxCpuCores": 1, "maxPids": 64, "maxTempDiskMb": 32,
            "maxOutputFiles": 1, "maxOutputBytes": 1024, "outputFormats": ["csv"],
            "input": {"script": "print('ok')", "scriptLanguage": "PYTHON"}, "inputArtifacts": [],
        }
        process = _SuccessfulProcess()
        process.poll = lambda: None
        with patch.object(runner, "volume_write"), patch.object(runner, "volume_command", return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(runner.subprocess, "run"), patch.object(runner.subprocess, "Popen", return_value=process), \
             patch.object(runner, "v2_heartbeat", return_value=True), patch.object(runner, "v2_fail") as fail, patch.object(runner, "v2_usage"):
            runner.run_v2(task)
        self.assertEqual("CANCELLED", fail.call_args.args[3])


if __name__ == "__main__":
    unittest.main()
