"""Dedicated control-plane worker for disposable, networkless artifact jobs.

This process is the only service allowed to talk to the container engine.  Job
containers receive neither the engine socket nor the Aether network.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ADMIN_URL = os.environ.get("AETHER_ADMIN_INTERNAL_URL", "http://aether-admin:8080").rstrip("/")
RUNNER_TOKEN = os.environ["AETHER_SANDBOX_RUNNER_TOKEN"]
POLL_SECONDS = float(os.environ.get("AETHER_SANDBOX_POLL_SECONDS", "2"))
IMAGES = {"PYTHON": os.environ.get("AETHER_SANDBOX_PYTHON_IMAGE", "aether-sandbox-python:1"), "NODE": os.environ.get("AETHER_SANDBOX_NODE_IMAGE", "aether-sandbox-node:1")}
PLATFORM_GENERIC_ENTRY = "__platform_generic_renderer__"


def request(path: str, method: str = "POST", data: bytes | None = None, content_type: str = "application/json", execution_token: str | None = None) -> bytes:
    headers = {"X-Aether-Runner-Token": RUNNER_TOKEN, "Content-Type": content_type}
    if execution_token: headers["X-Aether-Execution-Token"] = execution_token
    req = urllib.request.Request(ADMIN_URL + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def claim() -> dict | None:
    data = json.loads(request("/api/internal/sandbox/runner/claim", data=b"{}"))
    return data.get("data")


def resource(task: dict, resource_id: str) -> bytes:
    return request(f"/api/internal/sandbox/runner/executions/{task['executionId']}/resources/{resource_id}", method="GET", data=None, content_type="application/octet-stream", execution_token=task["executionToken"])


def form_complete(task: dict, file_name: str, content: bytes, logs: str, final_artifact: bool) -> None:
    boundary = "aether-sandbox-boundary"
    checksum = hashlib.sha256(content).hexdigest()
    fields = [("sha256", checksum), ("logSummary", logs[-4096:]), ("finalArtifact", str(final_artifact).lower())]
    parts = []
    for key, value in fields:
        parts += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), value.encode(), b"\r\n"]
    mime = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[Path(file_name).suffix.lstrip(".").lower()]
    parts += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), content, b"\r\n", f"--{boundary}--\r\n".encode()]
    request(f"/api/internal/sandbox/runner/executions/{task['executionId']}/complete", data=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}", execution_token=task["executionToken"])


def fail(task: dict, reason: str, logs: str) -> None:
    payload = urllib.parse.urlencode({"reason": reason[:1024], "logSummary": logs[-4096:]}).encode()
    request(f"/api/internal/sandbox/runner/executions/{task['executionId']}/fail", data=payload, content_type="application/x-www-form-urlencoded", execution_token=task["executionToken"])


def task_volume(task: dict, kind: str) -> str:
    execution_id = str(task["executionId"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", execution_id):
        raise ValueError("invalid execution id")
    if kind not in {"resources", "input", "output"}:
        raise ValueError("invalid sandbox volume kind")
    return "aether-sandbox-" + execution_id + "-" + kind


def task_container(task: dict) -> str:
    execution_id = str(task["executionId"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", execution_id):
        raise ValueError("invalid execution id")
    return "aether-sandbox-job-" + execution_id


def volume_command(volume: str, image: str, script: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", "-i", "--user", "0:0", "-v", f"{volume}:/work:rw", "--entrypoint", "sh", image, "-c", script],
        input=data, capture_output=True, check=True,
    )


def volume_write(volume: str, image: str, path: str, data: bytes) -> None:
    quoted = shlex.quote(path)
    volume_command(volume, image, f"mkdir -p $(dirname {quoted}) && cat > {quoted}", data)


def volume_read(volume: str, image: str, path: str) -> bytes:
    return volume_command(volume, image, "cat " + shlex.quote(path)).stdout


def run(task: dict) -> None:
    resource_volume = task_volume(task, "resources")
    input_volume = task_volume(task, "input")
    output_volume = task_volume(task, "output")
    container_name = task_container(task)
    volumes = (resource_volume, input_volume, output_volume)
    logs = ""
    image = IMAGES[task["runtime"]]
    try:
        for volume in volumes:
            subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
        entry = None
        for item in task["resources"]:
            # The filename arrives from an immutable Skill resource.  Prevent traversal even if legacy data is bad.
            if Path(item["name"]).name != item["name"]: raise ValueError("invalid frozen resource filename")
            data = resource(task, item["id"])
            if hashlib.sha256(data).hexdigest() != item["contentSha256"]: raise ValueError("resource checksum mismatch")
            volume_write(resource_volume, image, "/work/" + item["name"], data)
            if item["id"] == task["entryResourceId"]: entry = item["name"]
        if task["entryResourceId"] == PLATFORM_GENERIC_ENTRY:
            entry = "platform_generic_artifact.py"
            volume_write(
                resource_volume,
                image,
                "/work/" + entry,
                Path(__file__).with_name("generic_artifact.py").read_bytes(),
            )
        if not entry: raise ValueError("entry resource missing")
        input_json = json.dumps(task["input"], ensure_ascii=False)
        volume_write(input_volume, image, "/work/input.json", input_json.encode("utf-8"))
        volume_command(output_volume, image, "chmod 777 /work")
        command = ["python", f"/work/resources/{entry}"] if task["runtime"] == "PYTHON" else ["node", f"/work/resources/{entry}"]
        docker = ["docker", "run", "--rm", "--name", container_name, "-i", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "512m", "--cpus", "1", "--user", "10001:10001", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-e", "AETHER_INPUT_JSON=" + input_json, "-e", "AETHER_INPUT_FILE=/work/input/input.json", "-e", "AETHER_RESOURCE_DIR=/work/resources", "-e", "AETHER_OUTPUT_DIR=/work/output", "-v", f"{resource_volume}:/work/resources:ro", "-v", f"{input_volume}:/work/input:ro", "-v", f"{output_volume}:/work/output:rw", "-w", "/work", image] + command
        try:
            completed = subprocess.run(docker, input=input_json, capture_output=True, text=True, timeout=task["timeoutSeconds"], check=False)
        except subprocess.TimeoutExpired as error:
            # Killing the Docker CLI alone leaves the daemon-side container alive.
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
            raise RuntimeError("sandbox process timed out") from error
        logs = (completed.stdout + "\n" + completed.stderr)[-8192:]
        if completed.returncode != 0: raise RuntimeError("sandbox process failed")
        output_names = [name for name in volume_command(output_volume, image, "find /work -maxdepth 1 -type f -exec basename {} \\;").stdout.decode().splitlines() if Path(name).name == name]
        allowed = {item.lower() for item in task["outputFormats"]}
        if not output_names or len(output_names) > task["maxOutputFiles"] or any(Path(name).suffix.lstrip(".").lower() not in allowed for name in output_names): raise ValueError("Skill output files do not match the frozen contract")
        outputs = [(name, volume_read(output_volume, image, "/work/" + name)) for name in output_names]
        if any(len(content) > task["maxOutputBytes"] for _, content in outputs): raise ValueError("output exceeds declared limit")
        for index, (name, content) in enumerate(outputs): form_complete(task, name, content, logs, index == len(outputs) - 1)
    except Exception as error:
        try: fail(task, str(error), logs)
        except Exception: pass
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
        for volume in volumes:
            subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, check=False)


def main() -> None:
    while True:
        try:
            task = claim()
            if task: run(task)
            else: time.sleep(POLL_SECONDS)
        except (urllib.error.URLError, ValueError, KeyError):
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
