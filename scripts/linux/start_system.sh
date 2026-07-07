#!/usr/bin/env bash
# =============================================================================
# start_system.sh — Linux/bash port of scripts/powershell/start_system.ps1
#
# Boots the full AI-Trader stack: Docker infrastructure (Redpanda/Kafka,
# QuestDB, Redis, PostgreSQL), then the producer/consumer services in order
# (ingestion -> technical -> sentiment -> aggregator -> predictive -> quant-rag
# -> Python Deep Quant agent -> auth -> payment -> frontend/Tauri).
#
# This script lives at <repo>/scripts/linux/start_system.sh, so the repo root
# is two directories up. Every relative path below is resolved against that
# root, so running the script from any directory works correctly.
#
# Usage:
#   chmod +x scripts/linux/start_system.sh
#   ./scripts/linux/start_system.sh
#
# Press Ctrl+C to stop all services and infrastructure.
# =============================================================================

# Do NOT use `set -e`: like the PowerShell original, we want to continue past
# non-fatal failures (a dependency already installed, a topic already existing,
# a port already free) rather than abort the whole boot.
set -uo pipefail

# ── Ensure cargo (and common toolchains) are on PATH for this session ────────
export PATH="$HOME/.cargo/bin:/usr/local/bin:$PATH"

# ── Anchor to the repository root regardless of the launch directory ─────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RepoRoot="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$RepoRoot"
echo -e "\033[36mRepository root: $RepoRoot\033[0m"

# ── Resolve the docker compose command (v2 plugin preferred, v1 fallback) ────
if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
else
    DC=(docker compose)  # will error later with a clear message if truly absent
fi

# ── Resolve the Python interpreter for the Deep Quant agent ──────────────────
# Prefer the project venv if present (mirrors the deployed layout), else the
# system python3.
if [ -x "$RepoRoot/agents/deep-quant-loop/.venv/bin/python" ]; then
    DEEP_QUANT_PY="$RepoRoot/agents/deep-quant-loop/.venv/bin/python"
else
    DEEP_QUANT_PY="python3"
fi

# Track background service PIDs so we can tear them down on exit.
processes=()

# ── Colored logging helpers ──────────────────────────────────────────────────
c_cyan()    { echo -e "\033[36m$*\033[0m"; }
c_dcyan()   { echo -e "\033[36m$*\033[0m"; }
c_green()   { echo -e "\033[32m$*\033[0m"; }
c_yellow()  { echo -e "\033[33m$*\033[0m"; }
c_magenta() { echo -e "\033[35m$*\033[0m"; }
c_gray()    { echo -e "\033[90m$*\033[0m"; }
c_red()     { echo -e "\033[31m$*\033[0m"; }

# ── Wait until a TCP port is open (polls up to $timeout seconds) ─────────────
# Uses bash's /dev/tcp pseudo-device; no external dependency required.
wait_for_port() {
    local port="$1"
    local timeout="${2:-60}"
    local label="${3:-port $port}"
    c_dcyan "  Waiting for $label to be ready..."
    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
            exec 3>&- 3<&- 2>/dev/null || true
            c_green "  [$label] is ready!"
            return 0
        fi
        sleep 0.5
    done
    c_yellow "  WARNING: $label did not become ready within ${timeout}s - continuing anyway."
    return 0
}

# ── Start a background service from a repo-relative directory ────────────────
# Runs each service in its own session (setsid) so we can terminate the whole
# process group — including child processes spawned by cargo/npm — on cleanup.
# stdout/stderr are inherited so all service logs stream to this console, just
# like the PowerShell -NoNewWindow behavior.
start_service() {
    local dir="$1"; shift
    setsid bash -c 'cd "$1" && shift && exec "$@"' _ "$RepoRoot/$dir" "$@" &
    processes+=("$!")
}

# ── Cleanup on exit / interrupt ──────────────────────────────────────────────
cleanup() {
    # Guard against double-invocation (EXIT fires after INT/TERM handlers).
    if [ "${_CLEANED_UP:-0}" = "1" ]; then return; fi
    _CLEANED_UP=1

    echo ""
    c_yellow "Shutting down system..."
    local pid
    for pid in "${processes[@]:-}"; do
        [ -z "$pid" ] && continue
        if kill -0 "$pid" 2>/dev/null; then
            # Negative PID targets the whole process group created by setsid.
            kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    if docker info >/dev/null 2>&1; then
        c_yellow "Stopping Docker infrastructure..."
        "${DC[@]}" down 2>/dev/null || true
        if [ -d "$RepoRoot/alpha-backend" ]; then
            c_yellow "Stopping PostgreSQL infrastructure..."
            ( cd "$RepoRoot/alpha-backend" && "${DC[@]}" down 2>/dev/null || true )
        fi
    else
        c_yellow "Docker daemon not reachable, skipping container cleanup."
    fi
    c_green "System shutdown complete."
}
trap cleanup EXIT INT TERM

# =============================================================================
# Ports: 3000=Next.js, 3001=Auth, 3002=Payment, 8080-8083=WS agents,
#        8084=Tauri Tool Server, 8085=Ingestion Control, 8086=Python Agent,
#        8087=Kite REST API, 9000/9009=QuestDB, 5432=PG, 5433=Postgres DB,
#        6379=Redis, 19092=Kafka
# =============================================================================

c_magenta "==> Cleaning up stale processes and ports..."

# Exclude Docker-managed ports (5432, 5433, 6379, 9000, 9009, 19092) from direct
# kills — terminating those PIDs would kill docker-proxy / the Docker daemon.
ports_to_kill=(3000 3001 3002 8080 8081 8082 8083 8084 8085 8086 8087)

# Resolve the listeners on a port into PIDs, trying lsof then fuser then ss.
pids_on_port() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null
    elif command -v fuser >/dev/null 2>&1; then
        fuser "${port}/tcp" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$'
    elif command -v ss >/dev/null 2>&1; then
        ss -ltnp "sport = :${port}" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
    fi
}

for port in "${ports_to_kill[@]}"; do
    for procId in $(pids_on_port "$port"); do
        [[ "$procId" =~ ^[0-9]+$ ]] || continue
        [ "$procId" -gt 1 ] || continue
        # Safety: never kill Docker/WSL/system helper processes.
        procName="$(ps -p "$procId" -o comm= 2>/dev/null | tr -d ' ')"
        if echo "$procName" | grep -qiE 'docker|containerd|vpnkit|wsl|systemd|dockerd'; then
            c_yellow "  [skipped] system/docker process ($procName, PID $procId) on port $port"
            continue
        fi
        # Kill the process group (children of cargo/npm included) then the PID.
        kill -TERM -- "-$procId" 2>/dev/null || true
        kill -KILL "$procId" 2>/dev/null || true
        c_gray "  [killed] PID $procId on port $port"
    done
done
c_green "  Pre-flight cleanup done."
sleep 2

# ── Verify Docker is running and clean up old containers gracefully ──────────
c_cyan "Verifying Docker daemon..."
if ! docker info >/dev/null 2>&1; then
    c_red "ERROR: Docker is not running or unreachable."
    c_yellow "Please start Docker and ensure the daemon is running before launching the system."
    exit 1
fi

c_cyan "Stopping any stale Docker containers from previous runs..."
"${DC[@]}" down 2>/dev/null || true
if [ -d alpha-backend ]; then
    ( cd alpha-backend && "${DC[@]}" down 2>/dev/null || true )
fi

# ── Load environment variables from .env ─────────────────────────────────────
c_cyan "Loading environment variables from .env..."
if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and lines without an '=' assignment.
        case "$line" in
            \#*) continue ;;
            *=*) ;;
            *) continue ;;
        esac
        varName="${line%%=*}"
        varValue="${line#*=}"
        # Trim surrounding whitespace.
        varName="$(echo "$varName" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        varValue="$(echo "$varValue" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        # Strip a single layer of surrounding double or single quotes.
        case "$varValue" in
            \"*\") varValue="${varValue%\"}"; varValue="${varValue#\"}" ;;
            \'*\') varValue="${varValue%\'}"; varValue="${varValue#\'}" ;;
        esac
        [ -z "$varName" ] && continue
        export "$varName=$varValue"
    done < .env
fi

# ── Start infrastructure ─────────────────────────────────────────────────────
c_cyan "Starting infrastructure (Kafka/Redpanda, QuestDB, Redis)..."
"${DC[@]}" up -d redpanda questdb redis

c_cyan "Starting PostgreSQL infrastructure for Auth & Payment services..."
if [ -d alpha-backend ]; then
    ( cd alpha-backend && "${DC[@]}" up -d postgres )
fi

# Wait for each infra service to be reachable before proceeding.
wait_for_port 6379  60 "Redis (:6379)"
wait_for_port 9000  90 "QuestDB (:9000)"
wait_for_port 19092 90 "Redpanda/Kafka (:19092)"
wait_for_port 5433  90 "PostgreSQL Auth/Payment DB (:5433)"

# ── Install Node.js dependencies ─────────────────────────────────────────────
c_cyan "Checking and installing Node.js dependencies..."
install_node_deps() {
    local dir="$1" label="$2"
    [ -d "$dir" ] || return 0
    c_dcyan "Checking $label dependencies..."
    if [ ! -d "$dir/node_modules" ]; then
        c_yellow "Installing $label dependencies..."
        ( cd "$dir" && npm install )
    else
        c_green "$label dependencies already installed."
    fi
}
install_node_deps "alpha-backend"   "alpha-backend"
install_node_deps "agents/sentiment" "agents/sentiment"
install_node_deps "frontend"        "frontend"

# ── Push database schema & generate Prisma clients ───────────────────────────
if [ -d alpha-backend ]; then
    c_cyan "Synchronizing databases and generating Prisma clients..."
    (
        cd alpha-backend
        c_dcyan "  Pushing schema & generating client for Auth Service..."
        npm run auth:db-push
        c_dcyan "  Pushing schema & generating client for Payment Service..."
        npm run payment:db-push
    )
fi

# ── Pre-create Kafka topics via rpk ──────────────────────────────────────────
c_cyan "Pre-creating Kafka topics via rpk..."
topics=(market.ticks market.ohlc.10m technical_signals sentiment_signals trade_decisions signals.predictive signals.insights)
for topic in "${topics[@]}"; do
    if docker exec stratai-redpanda rpk topic create "$topic" --partitions 3 >/dev/null 2>&1; then
        c_green "  [+] Topic created: $topic"
    else
        c_gray "  [=] Topic already exists: $topic"
    fi
done
docker exec stratai-redpanda rpk topic list || true
c_green "All infrastructure is ready!"

# ── Start PRODUCERS first, then CONSUMERS ────────────────────────────────────
# Order: ingestion -> technical -> sentiment -> aggregator -> predictive ->
#        quant-rag -> python deep-quant -> auth -> payment -> frontend
# NOTE: the standalone auth/profile gate was removed from the dashboard entry.

c_cyan "Starting Rust Ingestion Service (Kite -> Kafka)..."
start_service "ingestion" cargo run --release
sleep 5

c_cyan "Starting Rust Technical Agent (Kafka ticks -> signals)..."
start_service "agents/technical" cargo run --release

c_cyan "Starting Node Sentiment Agent (News -> Kafka signals)..."
start_service "agents/sentiment" npm start
sleep 3

c_cyan "Starting Rust Aggregator (signals -> WS 8080 + OHLC -> WS 8081)..."
start_service "aggregator" cargo run --release
sleep 3

c_cyan "Starting Predictive Agent (OHLC -> LinReg -> WS 8082)..."
start_service "agents/predictive" cargo run --release

c_cyan "Starting Quant-RAG Agent (anomalies -> DeepSeek -> WS 8083)..."
start_service "agents/quant-rag" cargo run --release
sleep 3

c_cyan "Starting Python LangGraph Deep Quant Agent (Port 8086)..."
start_service "agents/deep-quant-loop" "$DEEP_QUANT_PY" main.py
wait_for_port 8086 60 "Python Deep Quant Loop (:8086)"
sleep 3

c_cyan "Starting Alpha-Backend Auth Service (Port 3001)..."
start_service "alpha-backend" npm run auth:dev

c_cyan "Starting Alpha-Backend Payment Service (Port 3002)..."
start_service "alpha-backend" npm run payment:dev

wait_for_port 3001 30 "Auth Service (:3001)"
wait_for_port 3002 30 "Payment Service (:3002)"

c_cyan "Starting Next.js Frontend (Tauri)..."
start_service "frontend" npm run tauri:dev

echo ""
c_green "All services are running! Power Phase 3.1 FULLY ENGAGED."
c_yellow "Press Ctrl+C to stop all services and infrastructure."

# Keep the script alive so the trap can tear everything down on Ctrl+C.
while true; do
    sleep 1
done
