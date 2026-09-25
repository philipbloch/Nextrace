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
pricing_file="${NEXTRACE_PRICING_FILE:-}"
if [[ -z "${pricing_file}" && -f "${repo_dir}/config/shopify-pricing.json" ]]; then
  pricing_file="${repo_dir}/config/shopify-pricing.json"
fi

dashboard_label="com.philipbloch.nextrace-dashboard"
http_proxy_label="com.philipbloch.nextrace-mcp-http-proxy"
importer_label="com.philipbloch.nextrace-local-usage-importer"

mkdir -p "${launch_agents_dir}"

write_dashboard_plist() {
  local path="${launch_agents_dir}/${dashboard_label}.plist"
  cat >"${path}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${dashboard_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${python_bin}</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>nextrace.dashboard.app:create_app</string>
    <string>--factory</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>${dashboard_port}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${repo_dir}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NEXTRACE_DB</key>
    <string>${db_path}</string>
    <key>NEXTRACE_PRICING_FILE</key>
    <string>${pricing_file}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/nextrace-dashboard.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/nextrace-dashboard.err.log</string>
</dict>
</plist>
EOF
}

write_http_proxy_plist() {
  local path="${launch_agents_dir}/${http_proxy_label}.plist"
  cat >"${path}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${http_proxy_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${python_bin}</string>
    <string>-m</string>
    <string>nextrace.cli</string>
    <string>mcp-http-proxy</string>
    <string>--application</string>
    <string>auto</string>
    <string>--server</string>
    <string>tool-gateway</string>
    <string>--target</string>
    <string>${tool_gateway_target}</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>${tool_gateway_port}</string>
    <string>--db</string>
    <string>${db_path}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${repo_dir}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NEXTRACE_DB</key>
    <string>${db_path}</string>
    <key>NEXTRACE_PRICING_FILE</key>
    <string>${pricing_file}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/nextrace-mcp-http-proxy.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/nextrace-mcp-http-proxy.err.log</string>
</dict>
</plist>
EOF
}

write_importer_plist() {
  local path="${launch_agents_dir}/${importer_label}.plist"
  cat >"${path}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${importer_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${cli_bin}</string>
    <string>import-local-usage</string>
    <string>--db</string>
    <string>${db_path}</string>
    <string>--state-path</string>
    <string>${import_state_path}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${repo_dir}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>NEXTRACE_DB</key>
    <string>${db_path}</string>
    <key>NEXTRACE_PRICING_FILE</key>
    <string>${pricing_file}</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>${import_interval}</integer>
  <key>StandardOutPath</key>
  <string>/tmp/nextrace-local-usage-importer.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/nextrace-local-usage-importer.err.log</string>
</dict>
</plist>
EOF
}

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

write_dashboard_plist
write_http_proxy_plist
write_importer_plist

load_agent "${dashboard_label}"
load_agent "${http_proxy_label}"
load_agent "${importer_label}"

cat <<EOF
Installed Nextrace LaunchAgents:
- ${dashboard_label} -> http://127.0.0.1:${dashboard_port}
- ${http_proxy_label} -> http://127.0.0.1:${tool_gateway_port}/mcp
- ${importer_label} -> every ${import_interval}s

Database: ${db_path}
Pricing file: ${pricing_file:-built-in public estimates}
EOF
