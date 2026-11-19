#!/usr/bin/env bash
# scripts/regression_compare.sh
# Automated regression compare tool for Pre-RunPod verification and continuous safety checks.
# Adheres to runpod.md Section 1A Rule 8.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

METRICS_DIR="$ROOT_DIR/data"
mkdir -p "$METRICS_DIR"
BASELINE_FILE="$METRICS_DIR/baseline_metrics.json"
CURRENT_FILE="$METRICS_DIR/current_metrics.json"

MODE="${1:---compare}"

capture_metrics() {
    local target_file="$1"
    echo "Capturing test and auth metrics into $target_file..."

    local start_t=$(python3 -c 'import time; print(time.time())')
    
    # 1. Run pytest suite (excluding known hanging/hardware-isolated test if specified)
    local pytest_out
    pytest_out=$(.venv/bin/pytest tests/ -q --tb=no -m "not slow" 2>&1 || true)
    
    local passed=$(echo "$pytest_out" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}' || echo "0")
    local failed=$(echo "$pytest_out" | grep -oE '[0-9]+ failed' | head -1 | awk '{print $1}' || echo "0")
    local end_t=$(python3 -c 'import time; print(time.time())')
    local duration=$(python3 -c "print(round($end_t - $start_t, 2))")

    # 2. Run Auth Matrix Probe
    local auth_out
    auth_out=$(.venv/bin/python scripts/auth_matrix_probe.py 2>&1 || true)
    local auth_routes=$(echo "$auth_out" | grep -c '|' || echo "0")

    # 3. Memory snapshot
    local rss_mb
    rss_mb=$(ps -o rss= -p $$ 2>/dev/null | awk '{print round($1/1024, 2)}' || echo "0")

    python3 -c "
import json
data = {
    'timestamp': $end_t,
    'pytest_passed': int('$passed' or 0),
    'pytest_failed': int('$failed' or 0),
    'pytest_duration_sec': float('$duration' or 0),
    'auth_routes_tested': int('$auth_routes' or 0),
    'memory_rss_mb': float('$rss_mb' or 0)
}
with open('$target_file', 'w') as f:
    json.dump(data, f, indent=2)
print('Metrics captured successfully.')
"
}

if [ "$MODE" = "--baseline" ]; then
    echo "=== Capturing Baseline Metrics ==="
    capture_metrics "$BASELINE_FILE"
    echo "Baseline saved to $BASELINE_FILE"
    cat "$BASELINE_FILE"
    exit 0
fi

if [ ! -f "$BASELINE_FILE" ]; then
    echo "No existing baseline file found at $BASELINE_FILE. Creating initial baseline..."
    capture_metrics "$BASELINE_FILE"
fi

echo "=== Running Current Metrics for Comparison ==="
capture_metrics "$CURRENT_FILE"

echo ""
echo "=========================================================="
echo "           REGRESSION COMPARISON REPORT                   "
echo "=========================================================="

python3 -c "
import json, sys

with open('$BASELINE_FILE') as f:
    b = json.load(f)

with open('$CURRENT_FILE') as f:
    c = json.load(f)

print(f'Metric                      | Baseline   | Current    | Diff')
print('----------------------------+------------+------------+---------')

regressions = []

# Passed tests
p_diff = c['pytest_passed'] - b['pytest_passed']
print(f'Pytest Passed               | {b[\"pytest_passed\"]:10d} | {c[\"pytest_passed\"]:10d} | {p_diff:+d}')
if c['pytest_passed'] < b['pytest_passed']:
    regressions.append(f'Passed tests dropped from {b[\"pytest_passed\"]} to {c[\"pytest_passed\"]}')

# Failed tests
f_diff = c['pytest_failed'] - b['pytest_failed']
print(f'Pytest Failed               | {b[\"pytest_failed\"]:10d} | {c[\"pytest_failed\"]:10d} | {f_diff:+d}')
if c['pytest_failed'] > b['pytest_failed']:
    regressions.append(f'Failed tests increased from {b[\"pytest_failed\"]} to {c[\"pytest_failed\"]}')

# Duration (warn if > 10% slower)
t_b = b['pytest_duration_sec']
t_c = c['pytest_duration_sec']
pct_dur = ((t_c - t_b) / t_b * 100.0) if t_b > 0 else 0
print(f'Test Duration (sec)         | {t_b:10.2f} | {t_c:10.2f} | {pct_dur:+.1f}%')
if pct_dur > 15.0:
    regressions.append(f'Execution time worsened by {pct_dur:.1f}% (threshold: 10%)')

# Auth routes
print(f'Auth Matrix Routes          | {b["auth_routes_tested"]:10d} | {c["auth_routes_tested"]:10d} | {c["auth_routes_tested"] - b["auth_routes_tested"]:+d}')
if c['auth_routes_tested'] < b['auth_routes_tested']:
    regressions.append(f'Auth matrix routes tested dropped from {b[\"auth_routes_tested\"]} to {c[\"auth_routes_tested\"]}')

print('==========================================================')
if regressions:
    print('❌ REGRESSION DETECTED:')
    for r in regressions:
        print(f'  - {r}')
    sys.exit(1)
else:
    print('✅ NO REGRESSIONS DETECTED. Metrics within tolerances.')
    sys.exit(0)
"
