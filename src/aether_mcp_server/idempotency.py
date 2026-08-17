"""为有副作用的 MCP 工具提供进程内幂等控制。

同一运行内重试 ``tools/call``（网络超时、LangGraph 再次调用）时，不能重复提交
渲染任务等副作用。服务重启后的恢复由 Deep Agent 的检查点和 outbox 负责；本存储
仅防护运行内重试，使相同行动返回已确认的原始结果而不是创建重复任务。
"""

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any

MAX_STORED_ACTIONS = 10_000


def decode_run_id(delegation_token: str | None) -> str | None:
    """从已验证的 Java 委派 JWT 中提取 runId。

    中间件会在工具运行前校验签名，因此此处只读取载荷，不再次验签，也不会信任用户
    自行传入的令牌。
    """
    if not delegation_token:
        return None
    try:
        import jwt

        claims = jwt.decode(delegation_token, options={"verify_signature": False})
        run_id = claims.get("runId")
        return str(run_id) if run_id else None
    except Exception:
        return None


def derive_idempotency_key(run_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    """为一次运行内的单个副作用操作生成确定性键。

    同一运行中，工具名和参数相同的两次调用会映射到同一键，因此重试会返回原始结果。
    """
    canonical = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(f"{run_id}:{tool_name}:{canonical}".encode("utf-8")).hexdigest()
    return digest[:32]


def execute_idempotently(
    store: "IdempotencyStore",
    run_id: str | None,
    tool_name: str,
    arguments: dict[str, Any],
    submit: Any,
) -> dict[str, Any]:
    """对每个 ``(run_id, arguments)`` 组合只执行一次副作用操作。

    ``submit`` 是执行操作并返回结果 ``dict`` 的无参可调用对象。同一运行 ID 和参数的
    重试会直接返回已存储结果，不会再次调用 ``submit``。
    """
    action_key: str | None = None
    if run_id:
        action_key = derive_idempotency_key(run_id, tool_name, arguments)
        cached = store.get(run_id, action_key)
        if cached is not None:
            store.record_hit()
            return cached
    result = submit()
    if run_id and action_key is not None:
        store.record_submission()
        store.put(run_id, action_key, result)
    return result


class IdempotencyStore:
    """以 ``(run_id, action key)`` 为键的有界线程安全结果缓存。"""

    def __init__(self, max_entries: int = MAX_STORED_ACTIONS) -> None:
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._results: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._hits = 0
        self._submitted = 0

    def get(self, run_id: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._results.get((run_id, key))

    def put(self, run_id: str, key: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._results[(run_id, key)] = result
            self._results.move_to_end((run_id, key))
            # 按插入顺序淘汰，限制长期运行进程的内存占用。
            while len(self._results) > self._max_entries:
                self._results.popitem(last=False)

    def record_submission(self) -> None:
        with self._lock:
            self._submitted += 1

    def record_hit(self) -> None:
        with self._lock:
            self._hits += 1

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "idempotencyHits": self._hits,
                "submittedActions": self._submitted,
                "cachedActions": len(self._results),
            }
