#!/usr/bin/env bash
# ==============================================================================
# scripts/lib/runpod.sh
# RunPod cloud environment detection, dynamic URL derivation, and connectivity check
# Adheres to run-pod.md Section 1 & B7
# ==============================================================================

# Derive public and local URLs dynamically
compute_urls() {
    if [ "${IS_RUNPOD}" = "true" ]; then
        RUNPOD_PROXY_URL="https://${RUNPOD_POD_ID}-${GATEWAY_PORT}.proxy.runpod.net"
        PUBLIC_URL="${RUNPOD_PROXY_URL}"
    elif [ -n "${PUBLIC_BASE_URL:-}" ]; then
        PUBLIC_URL="${PUBLIC_BASE_URL}"
    else
        PUBLIC_URL="http://127.0.0.1:${GATEWAY_PORT}"
    fi

    LOCAL_URL="http://127.0.0.1:${GATEWAY_PORT}"
}

# Verify storage persistence location on RunPod
check_runpod_storage() {
    if [ "${IS_RUNPOD}" = "true" ]; then
        local repo_path="${ROOT_DIR}"
        if [[ "${repo_path}" != "/workspace"* ]]; then
            log_warn "===================================================================="
            log_warn "WARNING: Repository is located at ${repo_path}, NOT under /workspace!"
            log_warn "On RunPod, any files outside /workspace will be lost when the pod stops."
            log_warn "Recommended: Clone repository into /workspace/voice-agent to retain data."
            log_warn "===================================================================="
        else
            log_ok "Persistent volume verified (/workspace mount path confirmed)."
        fi
    fi
}

# Run self-check ping to verify gateway reachability
check_public_reachability() {
    local target_url="$1"
    local max_wait="${2:-60}"
    local elapsed=0

    log_info "Verifying gateway self-check at ${target_url}/health/live (up to ${max_wait}s)..."

    while [ "${elapsed}" -lt "${max_wait}" ]; do
        if curl -skf -m 3 "${target_url}/health/live" >/dev/null 2>&1; then
            log_ok "Gateway is live and reachable via ${target_url}."
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    if [ "${IS_RUNPOD}" = "true" ]; then
        log_warn "Gateway is responding locally inside container, but external URL (${target_url}) was not reached within ${max_wait}s."
        log_warn "Please ensure HTTP port ${GATEWAY_PORT} is added to 'Expose HTTP Ports' in your RunPod pod settings."
    else
        log_warn "Gateway self-check on ${target_url} timed out after ${max_wait}s."
    fi
    return 1
}
