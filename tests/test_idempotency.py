import time

import jwt

from aether_mcp_server.idempotency import (
    IdempotencyStore,
    decode_run_id,
    derive_idempotency_key,
    execute_idempotently,
)


def _delegation_token(run_id: str = "run-1") -> str:
    return jwt.encode(
        {
            "exp": int(time.time()) + 300,
            "runId": run_id,
            "userId": "user-1",
            "agentId": "agent-1",
            "allowedTools": ["generate_artifact"],
        },
        "test-secret",
        algorithm="HS256",
    )


def test_decode_run_id_reads_verified_jwt_payload() -> None:
    assert decode_run_id(_delegation_token("run-42")) == "run-42"
    assert decode_run_id(None) is None
    assert decode_run_id("not-a-jwt") is None


def test_idempotency_key_is_deterministic_and_run_scoped() -> None:
    arguments = {"title": "报告", "content": "正文", "format": "pdf", "file_name": None, "document": {"a": 1}}
    first = derive_idempotency_key("run-1", "generate_artifact", arguments)
    second = derive_idempotency_key("run-1", "generate_artifact", arguments)
    different_args = derive_idempotency_key("run-1", "generate_artifact", {**arguments, "content": "其他正文"})
    different_run = derive_idempotency_key("run-2", "generate_artifact", arguments)

    assert first == second
    assert first != different_args
    assert first != different_run


def test_store_dedupes_retries_and_tracks_metrics() -> None:
    store = IdempotencyStore()
    key = derive_idempotency_key("run-1", "generate_artifact", {"content": "x"})

    assert store.get("run-1", key) is None
    store.put("run-1", key, {"execution_id": "exec-1", "run_id": "run-1", "status": "queued"})
    store.record_submission()

    assert store.get("run-1", key)["execution_id"] == "exec-1"
    store.record_hit()
    stats = store.stats()
    assert stats == {"idempotencyHits": 1, "submittedActions": 1, "cachedActions": 1}


def test_store_bounds_memory_by_evicting_oldest() -> None:
    store = IdempotencyStore(max_entries=2)
    for run_id in ("run-1", "run-2", "run-3"):
        store.put(run_id, "key", {"execution_id": "exec", "run_id": run_id, "status": "queued"})

    assert store.get("run-1", "key") is None  # 最早写入的已被淘汰
    assert store.get("run-3", "key") is not None


def test_execute_idempotently_runs_submit_once_and_returns_cached_result() -> None:
    store = IdempotencyStore()
    calls = {"count": 0}

    def submit():
        calls["count"] += 1
        return {"execution_id": f"exec-{calls['count']}", "run_id": "run-1", "status": "queued"}

    arguments = {"title": "报告", "content": "正文", "format": "pdf"}
    first = execute_idempotently(store, "run-1", "generate_artifact", arguments, submit)
    second = execute_idempotently(store, "run-1", "generate_artifact", arguments, submit)

    assert calls["count"] == 1
    assert first == second == {"execution_id": "exec-1", "run_id": "run-1", "status": "queued"}
    assert store.stats()["idempotencyHits"] == 1
    assert store.stats()["submittedActions"] == 1


def test_execute_idempotently_without_run_id_never_deduplicates() -> None:
    store = IdempotencyStore()
    calls = {"count": 0}

    def submit():
        calls["count"] += 1
        return {"execution_id": f"exec-{calls['count']}", "run_id": "run-1", "status": "queued"}

    execute_idempotently(store, None, "generate_artifact", {"format": "pdf"}, submit)
    execute_idempotently(store, None, "generate_artifact", {"format": "pdf"}, submit)

    assert calls["count"] == 2
    assert store.stats()["submittedActions"] == 0  # 无 runId 时不记录幂等键
