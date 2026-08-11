import importlib.util
import os
import subprocess
from pathlib import Path


def load_runner():
    os.environ.setdefault("AETHER_SANDBOX_RUNNER_TOKEN", "test-runner-token")
    path = Path(__file__).parents[1] / "sandbox-runner" / "runner.py"
    spec = importlib.util.spec_from_file_location("sandbox_runner_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_job_container_receives_readonly_resources_and_input(monkeypatch):
    runner = load_runner()
    commands = []
    uploaded = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "run", "--rm"] and "--entrypoint" in command:
            script = command[-1]
            if script.startswith("find "):
                return subprocess.CompletedProcess(command, 0, stdout=b"resume.pdf\n", stderr=b"")
            if script.startswith("cat "):
                return subprocess.CompletedProcess(command, 0, stdout=b"%PDF-demo", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "resource", lambda _task, _resource_id: b"print('render')")
    monkeypatch.setattr(runner, "form_complete", lambda *args: uploaded.append(args))

    runner.run({
        "executionId": "exec-1", "runtime": "PYTHON", "entryResourceId": "script-1",
        "resources": [{"id": "script-1", "name": "render.py", "contentSha256": __import__("hashlib").sha256(b"print('render')").hexdigest()}],
        "input": {"name": "Ada"}, "outputFormats": ["pdf"], "maxOutputFiles": 1,
        "maxOutputBytes": 1024, "timeoutSeconds": 10,
    })

    job_command = next(command for command in commands if command[:3] == ["docker", "run", "--rm"] and "--entrypoint" not in command)
    assert "aether-sandbox-exec-1-resources:/work/resources:ro" in job_command
    assert "aether-sandbox-exec-1-input:/work/input:ro" in job_command
    assert "aether-sandbox-exec-1-output:/work/output:rw" in job_command
    assert "aether-sandbox-exec-1:/work:rw" not in job_command
    assert uploaded and uploaded[0][1] == "resume.pdf"


def test_platform_generic_renderer_does_not_require_a_skill_resource(monkeypatch):
    runner = load_runner()
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "run", "--rm"] and "--entrypoint" in command:
            script = command[-1]
            if script.startswith("find "):
                return subprocess.CompletedProcess(command, 0, stdout=b"risk-ledger.docx\n", stderr=b"")
            if script.startswith("cat "):
                return subprocess.CompletedProcess(command, 0, stdout=b"PK-demo", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "form_complete", lambda *_args: None)
    runner.run({
        "executionId": "exec-generic", "runtime": "PYTHON", "entryResourceId": runner.PLATFORM_GENERIC_ENTRY,
        "resources": [], "input": {"title": "风险台账", "content": "# 内容", "format": "docx"},
        "outputFormats": ["docx"], "maxOutputFiles": 1, "maxOutputBytes": 1024, "timeoutSeconds": 10,
    })

    job_command = next(command for command in commands if command[:3] == ["docker", "run", "--rm"] and "--entrypoint" not in command)
    assert "/work/resources/platform_generic_artifact.py" in job_command
