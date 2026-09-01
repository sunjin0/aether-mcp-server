$ErrorActionPreference = 'Stop'

$image = 'aether-mcp-server-aether-mcp:latest'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$kubectl = 'C:\Program Files\Docker\Docker\resources\bin\kubectl.exe'
$proxy = Start-Process -FilePath $kubectl -ArgumentList 'proxy','--port=8001','--accept-hosts=.*' -PassThru -WindowStyle Hidden
try {
    Start-Sleep -Seconds 3
    $code = @'
import sys
sys.path.insert(0, '/src/src')
from aether_mcp_server.tools import grafana_query, kubernetes_get_pods, prometheus_query

prom = prometheus_query({'endpoint': 'http://host.docker.internal:9090', 'token': 'local-smoke'}, 'up')
grafana = grafana_query({'endpoint': 'http://host.docker.internal:3000', 'token': 'local-smoke', 'datasource_uid': 'prom-main'}, 'up')
kubernetes = kubernetes_get_pods({'endpoint': 'http://host.docker.internal:8001', 'token': 'local-smoke'}, 'aether-connector-test', 'app=connector-sample')
assert prom.status == 'success' and len(prom.data.get('result', [])) >= 1
assert grafana.status == 'success' and len(grafana.data.get('result', [])) >= 1
assert any(item.get('phase') == 'Running' for item in kubernetes.items)
print('Local MCP connector smoke passed: Prometheus=1 Grafana=1 Kubernetes=1')
'@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
    docker run --rm --entrypoint /app/.venv/bin/python --add-host host.docker.internal:host-gateway -v "${repo}:/src" -w /src $image -c "exec(__import__('base64').b64decode('$encoded'))"
    if ($LASTEXITCODE -ne 0) { throw "Connector smoke exited with code $LASTEXITCODE" }
} finally {
    Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
}
