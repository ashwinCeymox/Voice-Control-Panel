#!/usr/bin/env bash
# scripts/ops-map.sh
# Read-only runtime & configuration diagnostic map for the pod (safe, never writes/kills/prints secrets).

set -uo pipefail

ENV_FILE="${ENV_FILE:-.env}"
DATA_DIR="${DATA_DIR:-data}"
MODELS_DIR="${MODELS_DIR:-models}"
RUN_DIR="${RUN_DIR:-.run}"
PIDS_DIR="${PIDS_DIR:-.pids}"

echo "=== 1. GIT & HOST ==="
echo "HEAD: $(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
echo "git status --short:"
git status --short 2>/dev/null || true
if command -v nproc >/dev/null 2>&1; then
    echo "nproc: $(nproc)"
elif command -v sysctl >/dev/null 2>&1; then
    echo "nproc: $(sysctl -n hw.ncpu 2>/dev/null || echo 'unknown')"
fi

echo ""
echo "=== 2. SERVICE PROCESSES (LocalAI, local-tts, gateway, bridge, parakeet_worker) ==="
MATCH_PATTERN="local-ai|localai|local-tts|uvicorn|services\.streaming_bridge\.server|parakeet_worker"
ps -eo pid=,ppid=,lstart=,args= 2>/dev/null | grep -E "${MATCH_PATTERN}" | grep -v "grep -E" | while IFS= read -r line; do
    pid="$(echo "${line}" | awk '{print $1}')"
    threads="?"
    omp_vars=""
    if [ -r "/proc/${pid}/status" ]; then
        threads="$(grep -m1 '^Threads:' "/proc/${pid}/status" 2>/dev/null | awk '{print $2}' || echo '?')"
    fi
    if [ -r "/proc/${pid}/environ" ]; then
        omp_vars="$(tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null | grep -E '^(OMP|GOMP)_' | tr '\n' ' ' || true)"
    fi
    echo "${line} | Threads=${threads} | OMP/GOMP=[${omp_vars}]"
done || true

echo ""
echo "=== 3. PID FILES (.run & .pids) ==="
for d in "${RUN_DIR}" "${PIDS_DIR}"; do
    if [ -d "${d}" ]; then
        for f in "${d}"/*.pid; do
            [ -e "${f}" ] || continue
            echo "  ${f}: $(cat "${f}" 2>/dev/null || echo '?')"
        done
    else
        echo "  ${d}: (missing)"
    fi
done

echo ""
echo "=== 4. MODEL YAMLS & THREADS ==="
if [ -d "${MODELS_DIR}" ]; then
    find "${MODELS_DIR}" -maxdepth 3 \( -name "*.yaml" -o -name "*.yml" \) 2>/dev/null | sort | while IFS= read -r yf; do
        tline="$(grep -E '^[[:space:]]*threads:' "${yf}" 2>/dev/null | tr '\n' ' ' || true)"
        echo "  ${yf}: ${tline:-threads: (unset)}"
    done
else
    echo "  ${MODELS_DIR}: (missing)"
fi

echo ""
echo "=== 5. CALIBRATION FILES ==="
if [ -d "${DATA_DIR}" ]; then
    for cf in "${DATA_DIR}"/bridge_calibration_*.json; do
        [ -e "${cf}" ] || continue
        python3 - "${cf}" <<'PYEOF'
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
    print(f"  {p}: status={d.get('status', 'unknown')} passed={d.get('passed')} admitted_streams={d.get('admitted_streams')}")
except Exception as e:
    print(f"  {p}: error={e}")
PYEOF
    done
else
    echo "  ${DATA_DIR}: (missing)"
fi

echo ""
echo "=== 6. LISTENING PORTS ==="
if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP -sTCP:LISTEN -n -P 2>/dev/null || true
else
    echo "  (neither ss nor lsof available)"
fi

echo ""
echo "=== 7. .ENV KEY NAMES ONLY (NEVER VALUES) ==="
if [ -f "${ENV_FILE}" ]; then
    grep -E '^[A-Za-z0-9_]+=' "${ENV_FILE}" | cut -d= -f1 | sort
else
    echo "  ${ENV_FILE}: (not found)"
fi
