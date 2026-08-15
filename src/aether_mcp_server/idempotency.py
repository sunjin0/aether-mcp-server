"""Process-local idempotency for side-effecting MCP tools.

A retried ``tools/call`` inside the same run (network timeout, LangGraph
re-invocation) must not repeat a side effect such as submitting a render job.
The Deep Agent's checkpoint/outbox recovery is the authority across service
restarts; this store guards against in-run retries so the same action returns
its original, already-confirmed result instead of creating a duplicate.
"""

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any

MAX_STORED_ACTIONS = 10_000


def decode_run_id(delegation_token: str | None) -> str | None:
    """Extract the runId from an already-verified Java delegation JWT.

    The middleware verified the signature before the tool runs, so reading the
    payload here does not re-verify and never trusts a user-supplied token.
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
    """Deterministic key for one side-effecting action within a run.

    Two invocations of the same tool with identical arguments in the same run
    map to the same key, so retries return the original result.
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
    """Run one side-effecting action once per ``(run_id, arguments)``.

    ``submit`` is a zero-argument callable that performs the action and returns
    the result ``dict``.  A retry with the same run id and arguments returns the
    previously stored result without invoking ``submit`` again.
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
    """Bounded, thread-safe result cache keyed by ``(run_id, action key)``."""

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
