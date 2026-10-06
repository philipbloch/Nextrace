#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
launch_agents_dir="${HOME}/Library/LaunchAgents"

db_path="${NEXTRACE_DB:-${repo_dir}/demo-traces.db}"
python_bin="${NEXTRACE_PYTHON:-${repo_dir}/.venv/bin/python}"
cli_bin="${NEXTRACE_CLI:-${repo_dir}/.venv/bin/nextrace}"
dashboard_port="${NEXTRACE_DASHBOARD_PORT:-8765}"
tool_gateway_port="${NEXTRACE_TOOL_GATEWAY_PORT:-8766}"
tool_gateway_target="${NEXTRACE_TOOL_GATEWAY_TARGET:-https://tool-gateway.shopify.io/mcp}"
import_interval="${NEXTRACE_IMPORT_INTERVAL:-300}"
import_state_path="${NEXTRACE_IMPORT_STATE:-${HOME}/.nextrace/local-usage-import-state.json}"

dashboard_label="com.philipbloch.nextrace-dashboard"
http_proxy_label="com.philipbloch.nextrace-mcp-http-proxy"
importer_label="com.philipbloch.nextrace-local-usage-importer"

mkdir -p "${launch_agents_dir}"

"${python_bin}" - "${repo_dir}" "${launch_agents_dir}" "${db_path}" "${python_bin}" "${cli_bin}" \
  "${dashboard_port}" "${tool_gateway_port}" "${tool_gateway_target}" "${import_interval}" "${import_state_path}" <<'PY'
import plistlib
import sys
from pathlib import Path

(repo, destination, database, python, cli, dashboard_port, proxy_port,
 target, interval, import_state) = sys.argv[1:]
interval = int(interval)
if interval <= 0:
    raise SystemExit("NEXTRACE_IMPORT_INTERVAL must be positive")

base = {
    "WorkingDirectory": repo,
    "EnvironmentVariables": {"NEXTRACE_DB": database, "PYTHONUNBUFFERED": "1"},
    "RunAtLoad": True,
}
jobs = {
    "dashboard": {
        "ProgramArguments": [python, "-m", "uvicorn", "nextrace.dashboard.app:create_app",
                             "--factory", "--host", "127.0.0.1", "--port", dashboard_port],
        "KeepAlive": True,
    },
    "mcp-http-proxy": {
        "ProgramArguments": [python, "-m", "nextrace.cli", "mcp-http-proxy",
                             "--application", "auto", "--server", "tool-gateway", "--target", target,
                             "--host", "127.0.0.1", "--port", proxy_port, "--db", database],
        "KeepAlive": True,
    },
    "local-usage-importer": {
        "ProgramArguments": [cli, "import-local-usage", "--db", database, "--state-path", import_state],
        "StartInterval": interval,
    },
}
for name, options in jobs.items():
    label = f"com.philipbloch.nextrace-{name}"
    job = {**base, **options, "Label": label,
           "StandardOutPath": f"/tmp/nextrace-{name}.out.log",
           "StandardErrorPath": f"/tmp/nextrace-{name}.err.log"}
    (Path(destination) / f"{label}.plist").write_bytes(plistlib.dumps(job, sort_keys=False))
PY

load_agent() {
  local label="$1"
  local path="${launch_agents_dir}/${label}.plist"
  plutil -lint "${path}" >/dev/null
  launchctl bootout "gui/${UID}/${label}" 2>/dev/null ||
    launchctl bootout "gui/${UID}" "${path}" 2>/dev/null ||
    true
  sleep 1
  if ! launchctl bootstrap "gui/${UID}" "${path}"; then
    if ! launchctl print "gui/${UID}/${label}" >/dev/null 2>&1; then
      echo "Failed to load ${label}" >&2
      return 1
    fi
  fi
  launchctl kickstart -k "gui/${UID}/${label}"
}

for label in "${dashboard_label}" "${http_proxy_label}" "${importer_label}"; do
  load_agent "${label}"
done

cat <<EOF
Installed Nextrace LaunchAgents:
- ${dashboard_label} -> http://127.0.0.1:${dashboard_port}
- ${http_proxy_label} -> http://127.0.0.1:${tool_gateway_port}/mcp
- ${importer_label} -> every ${import_interval}s

Database: ${db_path}
EOF
