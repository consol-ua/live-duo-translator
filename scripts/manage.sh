#!/usr/bin/env bash
# ==============================================================================
# Gemini Live Duo Translator - Service Lifecycle Manager
# Manages background & foreground execution for Backend (FastAPI) and Frontend (Vite/Tauri).
# ==============================================================================

set -m
set -uo pipefail

# Project root directory and runtime storage
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run"

BACKEND_PID_FILE="${RUN_DIR}/backend.pid"
FRONTEND_PID_FILE="${RUN_DIR}/frontend.pid"
BACKEND_LOG_FILE="${RUN_DIR}/backend.log"
FRONTEND_LOG_FILE="${RUN_DIR}/frontend.log"

BACKEND_PORT=8000
FRONTEND_PORT=1420

# UI Colors
CLR_RESET="\033[0m"
CLR_BOLD="\033[1m"
CLR_GREEN="\033[32m"
CLR_YELLOW="\033[33m"
CLR_RED="\033[31m"
CLR_BLUE="\033[34m"
CLR_CYAN="\033[36m"

ensure_run_dir() {
    mkdir -p "${RUN_DIR}"
}

log_info() {
    echo -e "${CLR_BLUE}[INFO]${CLR_RESET} $1"
}

log_success() {
    echo -e "${CLR_GREEN}[SUCCESS]${CLR_RESET} $1"
}

log_warn() {
    echo -e "${CLR_YELLOW}[WARN]${CLR_RESET} $1"
}

log_error() {
    echo -e "${CLR_RED}[ERROR]${CLR_RESET} $1"
}

# Check if a process ID is actively running
is_pid_running() {
    local pid="$1"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        return 0
    fi
    return 1
}

# Get numeric PID listening on a given port (macOS / Linux)
get_pid_on_port() {
    local port="$1"
    lsof -ti ":${port}" 2>/dev/null | head -n 1
}

# Resolve Python executable (prefer backend/venv/bin/python)
resolve_python() {
    if [ -x "${ROOT_DIR}/backend/venv/bin/python" ]; then
        echo "${ROOT_DIR}/backend/venv/bin/python"
    elif [ -x "${ROOT_DIR}/backend/venv/bin/python3" ]; then
        echo "${ROOT_DIR}/backend/venv/bin/python3"
    elif command -v python3 >/dev/null 2>&1; then
        echo "$(command -v python3)"
    else
        echo ""
    fi
}

# Wait for a specific TCP port to open
wait_for_port() {
    local port="$1"
    local timeout="${2:-15}"
    local count=0

    while [ ${count} -lt "${timeout}" ]; do
        if lsof -i ":${port}" -sTCP:LISTEN >/dev/null 2>&1 || nc -z 127.0.0.1 "${port}" 2>/dev/null; then
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    return 1
}

start_backend() {
    local python_bin
    python_bin="$(resolve_python)"

    if [ -z "${python_bin}" ]; then
        log_error "Python 3 was not found. Please install Python or set up 'backend/venv'."
        return 1
    fi

    # Check if backend port is already in use
    local port_pid
    port_pid="$(get_pid_on_port ${BACKEND_PORT})"
    if [ -n "${port_pid}" ]; then
        log_warn "Backend is already running on port ${BACKEND_PORT} (PID: ${port_pid})."
        echo "${port_pid}" > "${BACKEND_PID_FILE}"
        return 0
    fi

    log_info "Starting FastAPI backend with ${python_bin}..."
    echo -e "\n--- [$(date '+%Y-%m-%d %H:%M:%S')] Starting Backend ---" >> "${BACKEND_LOG_FILE}"

    pushd "${ROOT_DIR}/backend" >/dev/null
    nohup "${python_bin}" main.py </dev/null >> "${BACKEND_LOG_FILE}" 2>&1 &
    local backend_pid=$!
    disown "${backend_pid}" 2>/dev/null || true
    echo "${backend_pid}" > "${BACKEND_PID_FILE}"
    popd >/dev/null

    log_info "Waiting for backend on port ${BACKEND_PORT}..."
    if wait_for_port "${BACKEND_PORT}" 15; then
        local actual_pid
        actual_pid="$(get_pid_on_port ${BACKEND_PORT})"
        [ -n "${actual_pid}" ] && echo "${actual_pid}" > "${BACKEND_PID_FILE}"
        log_success "Backend started (PID: ${actual_pid:-$(cat "${BACKEND_PID_FILE}")}, Port: ${BACKEND_PORT})."
        return 0
    else
        log_error "Backend failed to bind to port ${BACKEND_PORT} within 15s. Check '${BACKEND_LOG_FILE}'."
        return 1
    fi
}

start_frontend() {
    local mode="${1:-web}"

    # Check if frontend port is already in use
    local port_pid
    port_pid="$(get_pid_on_port ${FRONTEND_PORT})"
    if [ -n "${port_pid}" ]; then
        log_warn "Frontend is already running on port ${FRONTEND_PORT} (PID: ${port_pid})."
        echo "${port_pid}" > "${FRONTEND_PID_FILE}"
        return 0
    fi

    echo -e "\n--- [$(date '+%Y-%m-%d %H:%M:%S')] Starting Frontend (${mode}) ---" >> "${FRONTEND_LOG_FILE}"

    local frontend_pid
    if [ "${mode}" = "tauri" ]; then
        log_info "Starting frontend in desktop mode (Tauri)..."
        local tauri_cmd="npm run tauri dev"
        if [ -x "${ROOT_DIR}/node_modules/.bin/tauri" ]; then
            tauri_cmd="${ROOT_DIR}/node_modules/.bin/tauri dev"
        fi
        nohup ${tauri_cmd} </dev/null >> "${FRONTEND_LOG_FILE}" 2>&1 &
        frontend_pid=$!
    else
        log_info "Starting frontend in web mode (Vite dev)..."
        local vite_cmd="npm run dev"
        if [ -x "${ROOT_DIR}/node_modules/.bin/vite" ]; then
            vite_cmd="${ROOT_DIR}/node_modules/.bin/vite"
        fi
        nohup ${vite_cmd} </dev/null >> "${FRONTEND_LOG_FILE}" 2>&1 &
        frontend_pid=$!
    fi

    disown "${frontend_pid}" 2>/dev/null || true
    echo "${frontend_pid}" > "${FRONTEND_PID_FILE}"

    log_info "Waiting for frontend on port ${FRONTEND_PORT}..."
    if wait_for_port "${FRONTEND_PORT}" 20; then
        local actual_pid
        actual_pid="$(get_pid_on_port ${FRONTEND_PORT})"
        [ -n "${actual_pid}" ] && echo "${actual_pid}" > "${FRONTEND_PID_FILE}"
        log_success "Frontend started (PID: ${actual_pid:-$(cat "${FRONTEND_PID_FILE}")}, Port: ${FRONTEND_PORT})."
        return 0
    else
        log_error "Frontend did not open port ${FRONTEND_PORT} within 20s. Check '${FRONTEND_LOG_FILE}'."
        return 1
    fi
}

stop_process_target() {
    local name="$1"
    local pid_file="$2"
    local port="$3"

    local stopped_anything=0

    # 1. Terminate using PID recorded in file
    if [ -f "${pid_file}" ]; then
        local pid
        pid="$(cat "${pid_file}" 2>/dev/null || true)"
        if [ -n "${pid}" ] && is_pid_running "${pid}"; then
            log_info "Stopping ${name} (PID: ${pid})..."
            pkill -P "${pid}" 2>/dev/null || true
            kill "${pid}" 2>/dev/null || true
            stopped_anything=1

            # Wait up to 3 seconds for graceful exit
            for _ in {1..6}; do
                if ! is_pid_running "${pid}"; then
                    break
                fi
                sleep 0.5
            done

            if is_pid_running "${pid}"; then
                pkill -9 -P "${pid}" 2>/dev/null || true
                kill -9 "${pid}" 2>/dev/null || true
            fi
        fi
        rm -f "${pid_file}"
    fi

    # 2. Release port if still bound
    if [ -n "${port}" ]; then
        local port_pids
        port_pids="$(lsof -ti ":${port}" 2>/dev/null || true)"
        if [ -n "${port_pids}" ]; then
            log_info "Releasing port ${port} (PID: ${port_pids})..."
            for p in ${port_pids}; do
                pkill -P "${p}" 2>/dev/null || true
                kill "${p}" 2>/dev/null || true
            done
            sleep 1
            local remaining_pids
            remaining_pids="$(lsof -ti ":${port}" 2>/dev/null || true)"
            if [ -n "${remaining_pids}" ]; then
                for p in ${remaining_pids}; do
                    kill -9 "${p}" 2>/dev/null || true
                done
            fi
            stopped_anything=1
        fi
    fi

    if [ ${stopped_anything} -eq 1 ]; then
        log_success "${name} stopped."
    else
        log_info "${name} is not running."
    fi
}

cmd_start() {
    local mode="web"
    local foreground=0

    while [ $# -gt 0 ]; do
        case "$1" in
            tauri|--tauri|-t|desktop|--desktop)
                mode="tauri"
                shift
                ;;
            web|--web|-w)
                mode="web"
                shift
                ;;
            --foreground|-f)
                foreground=1
                shift
                ;;
            *)
                shift
                ;;
        esac
    done

    ensure_run_dir

    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"
    echo -e "${CLR_BOLD} Starting Gemini Live Duo Translator (${mode} mode)${CLR_RESET}"
    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"

    start_backend || return 1
    start_frontend "${mode}" || return 1

    local b_pid
    b_pid="$(get_pid_on_port ${BACKEND_PORT})"
    if [ -z "${b_pid}" ]; then
        b_pid="$(cat "${BACKEND_PID_FILE}" 2>/dev/null || echo "N/A")"
    fi

    local f_pid
    f_pid="$(get_pid_on_port ${FRONTEND_PORT})"
    if [ -z "${f_pid}" ]; then
        f_pid="$(cat "${FRONTEND_PID_FILE}" 2>/dev/null || echo "N/A")"
    fi

    echo ""
    echo -e "${CLR_GREEN}${CLR_BOLD}✓ Project is up and running!${CLR_RESET}"
    echo -e "  ${CLR_CYAN}Backend:${CLR_RESET}   http://127.0.0.1:${BACKEND_PORT} (PID: ${b_pid})"
    echo -e "  ${CLR_CYAN}Frontend:${CLR_RESET}  http://localhost:${FRONTEND_PORT} (PID: ${f_pid})"
    echo -e "  ${CLR_CYAN}Logs:${CLR_RESET}      ${BACKEND_LOG_FILE}"
    echo -e "             ${FRONTEND_LOG_FILE}"
    echo ""
    echo -e "  ${CLR_BOLD}Quick commands:${CLR_RESET}"
    echo -e "    Stop all:    ${CLR_YELLOW}./stop.sh${CLR_RESET}  (or ${CLR_YELLOW}npm stop${CLR_RESET})"
    echo -e "    Status:      ${CLR_YELLOW}npm run status${CLR_RESET} (or ${CLR_YELLOW}./scripts/manage.sh status${CLR_RESET})"
    echo -e "    Live logs:   ${CLR_YELLOW}npm run logs${CLR_RESET}   (or ${CLR_YELLOW}./scripts/manage.sh logs${CLR_RESET})"
    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"

    if [ ${foreground} -eq 1 ]; then
        echo ""
        log_info "Running in foreground. Press Ctrl+C to stop all services..."
        trap 'echo ""; log_info "Received interrupt signal. Shutting down..."; cmd_stop; exit 0' INT TERM
        tail -n 20 -f "${BACKEND_LOG_FILE}" "${FRONTEND_LOG_FILE}"
    fi
}

cmd_stop() {
    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"
    echo -e "${CLR_BOLD} Stopping Gemini Live Duo Translator${CLR_RESET}"
    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"

    stop_process_target "Frontend (Port ${FRONTEND_PORT})" "${FRONTEND_PID_FILE}" "${FRONTEND_PORT}"
    stop_process_target "Backend (Port ${BACKEND_PORT})" "${BACKEND_PID_FILE}" "${BACKEND_PORT}"

    # Also stop any Tauri desktop processes if running
    pkill -f "VoiceoverDesktop" 2>/dev/null || true

    echo -e "${CLR_GREEN}${CLR_BOLD}✓ All services have been stopped.${CLR_RESET}"
    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"
}

cmd_status() {
    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"
    echo -e "${CLR_BOLD} Project Status${CLR_RESET}"
    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"

    local b_port_pid
    b_port_pid="$(get_pid_on_port ${BACKEND_PORT})"
    if [ -n "${b_port_pid}" ]; then
        echo -e "  Backend (Port ${BACKEND_PORT}):  ${CLR_GREEN}RUNNING${CLR_RESET} (PID: ${b_port_pid})"
    else
        echo -e "  Backend (Port ${BACKEND_PORT}):  ${CLR_RED}STOPPED${CLR_RESET}"
    fi

    local f_port_pid
    f_port_pid="$(get_pid_on_port ${FRONTEND_PORT})"
    if [ -n "${f_port_pid}" ]; then
        echo -e "  Frontend (Port ${FRONTEND_PORT}): ${CLR_GREEN}RUNNING${CLR_RESET} (PID: ${f_port_pid})"
    else
        echo -e "  Frontend (Port ${FRONTEND_PORT}): ${CLR_RED}STOPPED${CLR_RESET}"
    fi

    echo -e "${CLR_BOLD}=======================================================${CLR_RESET}"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start "$@"
}

cmd_logs() {
    local target="${1:-all}"
    ensure_run_dir
    touch "${BACKEND_LOG_FILE}" "${FRONTEND_LOG_FILE}"

    case "${target}" in
        backend|api)
            tail -f "${BACKEND_LOG_FILE}"
            ;;
        frontend|ui)
            tail -f "${FRONTEND_LOG_FILE}"
            ;;
        *)
            tail -f "${BACKEND_LOG_FILE}" "${FRONTEND_LOG_FILE}"
            ;;
    esac
}

# Main command dispatcher
COMMAND="${1:-start}"
shift || true

case "${COMMAND}" in
    start)
        cmd_start "$@"
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart "$@"
        ;;
    status)
        cmd_status
        ;;
    logs)
        cmd_logs "$@"
        ;;
    help|--help|-h)
        echo "Usage: $0 {start|stop|restart|status|logs} [options]"
        echo ""
        echo "Commands:"
        echo "  start [web|tauri] [--foreground|-f]   Start backend and frontend (default: web, background)"
        echo "  stop                                 Stop all project processes"
        echo "  restart [web|tauri]                  Restart all services"
        echo "  status                               Show active status of services"
        echo "  logs [backend|frontend]              Stream service logs"
        ;;
    *)
        log_error "Unknown command: ${COMMAND}"
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
