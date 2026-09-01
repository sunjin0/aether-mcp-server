import json
from unittest.mock import patch

import pytest

from aether_mcp_server.tools import grafana_query, kubernetes_get_pods, prometheus_query


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return json.dumps({"status": "success", "data": {"resultType": "vector", "result": []}}).encode()


class _KubernetesResponse(_Response):
    def read(self, _limit):
        return json.dumps({"metadata": {"resourceVersion": "42"}, "items": [
            {"metadata": {"name": "api-1", "namespace": "prod", "labels": {"app": "api"}},
             "status": {"phase": "Running"}}
        ]}).encode()


def test_prometheus_query_uses_only_scoped_credential_and_returns_data():
    credential = {"endpoint": "https://prometheus.internal", "token": "secret-token"}
    with patch("aether_mcp_server.tools.urllib.request.urlopen", return_value=_Response()) as opened:
        result = prometheus_query(credential, "up{job=\"api\"}")

    assert result.status == "success"
    assert result.data["resultType"] == "vector"
    request = opened.call_args.args[0]
    assert "secret-token" not in request.full_url
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert "query=up%7Bjob%3D%22api%22%7D" in request.full_url


@pytest.mark.parametrize("credential", [
    {"endpoint": "file:///etc/passwd", "token": "x"},
    {"endpoint": "https://prometheus.internal", "token": ""},
])
def test_prometheus_query_rejects_invalid_scoped_credential(credential):
    with pytest.raises(ValueError, match="凭据无效"):
        prometheus_query(credential, "up")


def test_prometheus_query_rejects_empty_or_oversized_query():
    credential = {"endpoint": "https://prometheus.internal", "token": "x"}
    with pytest.raises(ValueError):
        prometheus_query(credential, "")
    with pytest.raises(ValueError):
        prometheus_query(credential, "x" * 4097)


def test_grafana_query_routes_through_the_declared_datasource():
    credential = {"endpoint": "https://grafana.internal", "token": "secret", "datasource_uid": "prom-main"}
    with patch("aether_mcp_server.tools.urllib.request.urlopen", return_value=_Response()) as opened:
        result = grafana_query(credential, "up")

    assert result.status == "success"
    request = opened.call_args.args[0]
    assert "/api/datasources/uid/prom-main/resources/api/v1/query?query=up" in request.full_url


def test_grafana_query_rejects_unsafe_datasource_uid():
    with pytest.raises(ValueError, match="凭据无效"):
        grafana_query({"endpoint": "https://grafana.internal", "token": "x", "datasource_uid": "prom/main"}, "up")


def test_kubernetes_get_pods_returns_sanitized_pod_summaries():
    credential = {"endpoint": "https://kubernetes.internal", "token": "secret"}
    with patch("aether_mcp_server.tools.urllib.request.urlopen", return_value=_KubernetesResponse()) as opened:
        result = kubernetes_get_pods(credential, "prod", "app=api")

    assert result.resource_version == "42"
    assert result.items == [{"name": "api-1", "namespace": "prod", "phase": "Running", "labels": {"app": "api"}}]
    assert "/api/v1/namespaces/prod/pods?labelSelector=app%3Dapi" in opened.call_args.args[0].full_url


def test_kubernetes_get_pods_rejects_path_injection():
    with pytest.raises(ValueError, match="命名空间无效"):
        kubernetes_get_pods({"endpoint": "https://kubernetes.internal", "token": "x"}, "prod/../secrets")
