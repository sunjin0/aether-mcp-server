"""用于一次性无网络产物任务的专用控制面工作进程。

该进程是唯一允许访问容器引擎的服务。任务容器既不会获得引擎套接字，也不会接入
Aether 网络。
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
RUNNER_ID = os.environ.get("AETHER_SANDBOX_RUNNER_ID", os.environ.get("HOSTNAME", "sandbox-runner"))
POLL_SECONDS = float(os.environ.get("AETHER_SANDBOX_POLL_SECONDS", "2"))
IMAGES = {"PYTHON": os.environ.get("AETHER_SANDBOX_PYTHON_IMAGE", "aether-sandbox-python:1"), "NODE": os.environ.get("AETHER_SANDBOX_NODE_IMAGE", "aether-sandbox-node:1")}
ALLOWED_IMAGE_DIGESTS = {value.strip() for value in os.environ.get("AETHER_SANDBOX_ALLOWED_IMAGE_DIGESTS", "").split(",") if value.strip()}
PLATFORM_GENERIC_ENTRY = "__platform_generic_renderer__"


def request(path: str, method: str = "POST", data: bytes | None = None, content_type: str = "application/json", execution_token: str | None = None) -> bytes:
    headers = {"X-Aether-Runner-Token": RUNNER_TOKEN, "X-Aether-Runner-Id": RUNNER_ID, "Content-Type": content_type}
    if execution_token: headers["X-Aether-Execution-Token"] = execution_token
    req = urllib.request.Request(ADMIN_URL + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def claim() -> dict | None:
    data = json.loads(request("/api/internal/sandbox/runner/claim", data=b"{}"))
    return data.get("data")


def claim_v2() -> dict | None:
    data = json.loads(request("/api/agent/sandbox/runner/claim", data=b"{}"))
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


def heartbeat(task: dict, logs: str = "") -> bool:
    """续租控制面任务；返回 True 表示请求方已取消。"""
    payload = urllib.parse.urlencode({"logSummary": logs[-4096:]}).encode()
    data = json.loads(request(
        f"/api/internal/sandbox/runner/executions/{task['executionId']}/heartbeat",
        data=payload,
        content_type="application/x-www-form-urlencoded",
        execution_token=task["executionToken"],
    ))
    return bool(data.get("data"))


def cancel_requested(task: dict) -> bool:
    data = json.loads(request(
        f"/api/internal/sandbox/runner/executions/{task['executionId']}/cancel",
        method="GET",
        data=None,
        content_type="application/json",
        execution_token=task["executionToken"],
    ))
    return bool(data.get("data"))


def v2_heartbeat(task: dict, logs: str = "") -> bool:
    payload = json.dumps({"summary": logs[-4096:]}).encode()
    data = json.loads(request(f"/api/agent/sandbox/runner/tasks/{task['taskId']}/heartbeat", data=payload, execution_token=task["executionToken"]))
    return bool(data.get("data"))


def v2_input(task: dict, input_id: str) -> bytes:
    return request(f"/api/agent/sandbox/runner/tasks/{task['taskId']}/inputs/{urllib.parse.quote(input_id, safe='')}", method="GET", data=None, content_type="application/octet-stream", execution_token=task["executionToken"])


def v2_usage(task: dict, wall_millis: int, output_bytes: int, exit_code: int | None) -> None:
    payload = json.dumps({"wallMillis": max(0, wall_millis), "outputBytes": max(0, output_bytes), "exitCode": exit_code}).encode()
    request(f"/api/agent/sandbox/runner/tasks/{task['taskId']}/usage", data=payload, execution_token=task["executionToken"])


def v2_complete(task: dict, file_name: str, content: bytes, logs: str, final_artifact: bool) -> None:
    boundary = "aether-sandbox-boundary"
    checksum = hashlib.sha256(content).hexdigest()
    fields = [("sha256", checksum), ("summary", logs[-4096:]), ("finalArtifact", str(final_artifact).lower())]
    parts = []
    for key, value in fields:
        parts += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), value.encode(), b"\r\n"]
    mime = {"csv": "text/csv", "json": "application/json", "md": "text/markdown", "xml": "application/xml", "txt": "text/plain", "html": "text/html", "zip": "application/zip"}.get(Path(file_name).suffix.lstrip(".").lower(), "application/octet-stream")
    parts += [f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), content, b"\r\n", f"--{boundary}--\r\n".encode()]
    request(f"/api/agent/sandbox/runner/tasks/{task['taskId']}/artifacts", data=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}", execution_token=task["executionToken"])


def v2_fail(task: dict, reason: str, logs: str, code: str = "RUNNER_FAILED") -> None:
    payload = urllib.parse.urlencode({"code": code, "reason": reason[:1024], "summary": logs[-4096:]}).encode()
    request(f"/api/agent/sandbox/runner/tasks/{task['taskId']}/fail", data=payload, content_type="application/x-www-form-urlencoded", execution_token=task["executionToken"])


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


def image_for(task: dict) -> str:
    """模板可选择摘要固定镜像；Agent 不得提供该值。"""
    requested = str(task.get("imageRef") or "")
    if not requested:
        return IMAGES[task["runtime"]]  # 旧版模板继续使用已部署的 Runner 镜像。
    if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}", requested):
        raise ValueError("template image is not digest-pinned")
    if requested not in ALLOWED_IMAGE_DIGESTS:
        raise ValueError("template image digest is not in the Runner allowlist")
    return requested


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
        if task.get("executionToken") and (cancel_requested(task) or heartbeat(task)):
            return
        for volume in volumes:
            subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
        entry = None
        for item in task["resources"]:
            # 文件名来自不可变 Skill 资源；即使旧数据异常也要阻止目录穿越。
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
        docker = ["docker", "run", "--rm", "--name", container_name, "-i", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "512m", "--cpus", "1", "--user", "10001:10001", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-e", "XDG_CACHE_HOME=/tmp/.cache", "-e", "AETHER_INPUT_JSON=" + input_json, "-e", "AETHER_INPUT_FILE=/work/input/input.json", "-e", "AETHER_RESOURCE_DIR=/work/resources", "-e", "AETHER_OUTPUT_DIR=/work/output", "-v", f"{resource_volume}:/work/resources:ro", "-v", f"{input_volume}:/work/input:ro", "-v", f"{output_volume}:/work/output:rw", "-w", "/work", image] + command
        # 冻结输入已通过 AETHER_INPUT_JSON 和只读文件提供，无需保留额外 stdin 管道。
        started = time.monotonic()
        if task.get("executionToken") and heartbeat(task):
            return
        try:
            process = subprocess.run(docker, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True,
                                     timeout=float(task["timeoutSeconds"]), check=False)
        except subprocess.TimeoutExpired as error:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
            raise RuntimeError("sandbox process timed out") from error
        logs = ((process.stdout or "") + "\n" + (process.stderr or ""))[-8192:]
        if process.returncode != 0:
            raise RuntimeError("sandbox process failed")
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


def run_v2(task: dict) -> None:
    """只执行新协议任务中由模板放行的脚本槽位。"""
    task_for_volume = dict(task, executionId=task["taskId"])
    resources, input_volume, output = task_volume(task_for_volume, "resources"), task_volume(task_for_volume, "input"), task_volume(task_for_volume, "output")
    container_name, image, logs = task_container(task_for_volume), "", ""
    # 在准备阶段前开始计时，使被拒绝、准备失败和取消的任务也具有可审计记录。
    started, output_bytes, exit_code, usage_reported = time.monotonic(), 0, None, False
    try:
        image = image_for(task)
        if str(task.get("executionMode") or "SCRIPT") == "WEB_COLLECTION":
            raise RuntimeError("web collection requires a configured egress proxy runner")
        memory_mb = min(4096, max(64, int(task.get("maxMemoryMb") or 512)))
        cpu_cores = min(4.0, max(0.1, float(task.get("maxCpuCores") or 1)))
        pids = min(512, max(16, int(task.get("maxPids") or 128)))
        temp_mb = min(1024, max(16, int(task.get("maxTempDiskMb") or 64)))
        script = str(task.get("input", {}).get("script") or "")
        language = str(task.get("input", {}).get("scriptLanguage") or "").upper()
        fixed_command = str(task.get("fixedCommand") or "")
        script_mode = str(task.get("executionMode") or "SCRIPT") == "SCRIPT"
        if script_mode and (not script or language != task["runtime"]): raise ValueError("missing or invalid frozen script")
        if not script_mode and (str(task.get("executionMode") or "") != "FIXED_COMMAND" or not fixed_command): raise ValueError("invalid fixed-command template")
        extension = "py" if language == "PYTHON" else "js"
        for volume in (resources, input_volume, output): subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
        if script_mode: volume_write(resources, image, "/work/task." + extension, script.encode("utf-8"))
        input_manifest = []
        for item in task.get("inputArtifacts") or []:
            input_id, file_name = str(item.get("id") or ""), str(item.get("fileName") or "")
            if not re.fullmatch(r"[A-Za-z0-9]{16,64}", input_id) or Path(file_name).name != file_name: raise ValueError("invalid frozen input metadata")
            content = v2_input(task, input_id)
            if len(content) != int(item.get("size") or -1) or hashlib.sha256(content).hexdigest() != str(item.get("sha256") or ""): raise ValueError("frozen input checksum mismatch")
            local_name = input_id + "-" + file_name
            # 仅使用已校验的 ID 和文件名构造容器内路径，隔离输入工件。
            volume_write(input_volume, image, "/work/" + local_name, content)
            input_manifest.append({"id": input_id, "fileName": file_name, "contentType": item.get("contentType"), "size": item.get("size"), "sha256": item.get("sha256"), "path": "/work/input/" + local_name})
        volume_command(output, image, "chmod 777 /work")
        command = (["python", "/work/resources/task.py"] if language == "PYTHON" else ["node", "/work/resources/task.js"]) if script_mode else ["sh", "-c", fixed_command]
        frozen_input = {key: value for key, value in (task.get("input") or {}).items() if key not in {"script", "scriptLanguage"}}
        docker = ["docker", "run", "--rm", "--name", container_name, "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", str(pids), "--memory", str(memory_mb) + "m", "--cpus", str(cpu_cores), "--user", "10001:10001", "--tmpfs", "/tmp:rw,noexec,nosuid,size=" + str(temp_mb) + "m", "-e", "XDG_CACHE_HOME=/tmp/.cache", "-e", "AETHER_OUTPUT_DIR=/work/output", "-e", "AETHER_INPUT_DIR=/work/input", "-e", "AETHER_INPUT_ARTIFACTS_JSON=" + json.dumps(input_manifest, ensure_ascii=False), "-e", "AETHER_INPUT_JSON=" + json.dumps(frozen_input, ensure_ascii=False), "-v", f"{resources}:/work/resources:ro", "-v", f"{input_volume}:/work/input:ro", "-v", f"{output}:/work/output:rw", "-w", "/work", image] + command
        process = subprocess.Popen(docker, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + float(task["timeoutSeconds"])
        cancelled = False
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
                raise TimeoutError("sandbox process timed out")
            if v2_heartbeat(task):
                cancelled = True
                process.kill()
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
                break
            time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))
        stdout, stderr = process.communicate()
        logs = (stdout + "\n" + stderr)[-8192:]
        if cancelled:
            v2_fail(task, "sandbox task cancelled", logs, "CANCELLED")
            return
        if process.returncode != 0: raise RuntimeError("sandbox process failed")
        names = [n for n in volume_command(output, image, "find /work -maxdepth 1 -type f -exec basename {} \\;").stdout.decode().splitlines() if Path(n).name == n]
        allowed = set(task.get("outputFormats") or [])
        if not names or len(names) > task["maxOutputFiles"] or any(Path(n).suffix.lstrip(".").lower() not in allowed for n in names): raise ValueError("output files do not match frozen contract")
        outputs = [(n, volume_read(output, image, "/work/" + n)) for n in names]
        if any(len(c) > task["maxOutputBytes"] for _, c in outputs): raise ValueError("output exceeds declared limit")
        output_bytes, exit_code = sum(len(c) for _, c in outputs), process.returncode
        # 最终产物会使控制面任务进入终态，因此必须先持久化成功用量。
        v2_usage(task, int((time.monotonic() - started) * 1000), output_bytes, exit_code)
        usage_reported = True
        for index, (name, content) in enumerate(outputs): v2_complete(task, name, content, logs, index == len(outputs) - 1)
    except Exception as error:
        try: v2_fail(task, str(error), logs, "TIMED_OUT" if isinstance(error, TimeoutError) else "RUNNER_FAILED")
        except Exception: pass
    finally:
        # 控制面只接受有界统计值。此处为尽力上报，因为取消可能使租约先于回调进入终态。
        if not usage_reported:
            try: v2_usage(task, int((time.monotonic() - started) * 1000), output_bytes, exit_code)
            except Exception: pass
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
        for volume in (resources, input_volume, output): subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, check=False)


def main() -> None:
    while True:
        try:
            task = claim_v2()
            if task: run_v2(task)
            else:
                task = claim()
                if task: run(task)
                else: time.sleep(POLL_SECONDS)
        # Admin can briefly reset active sockets while it is restarted or
        # redeployed. Keep polling instead of terminating the control plane.
        except (ConnectionError, TimeoutError, urllib.error.URLError, ValueError, KeyError):
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
