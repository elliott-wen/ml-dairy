#!/bin/bash
# Tests for task_resource_guard.py
#
# Strategy: submit jobs that violate limits, wait for the guard to run,
# then verify the jobs were cancelled.
#
# Requirements:
#   - task_resource_guard.py must be running (via main.py or manually)
#   - OR pass --manual to trigger a single guard run yourself after submission
#   - Must be run as a non-admin user (admins are exempt from cancellation).
#     Members of CSML_admins will see all "cancelled" tests FAIL — this is expected.
#
# Usage:
#   bash test_resource_guard.sh           # assumes guard is running in background
#   bash test_resource_guard.sh --manual  # runs the guard once per test

PASS=0
FAIL=0
MANUAL=false
GUARD_INTERVAL=20   # seconds to wait for the guard to fire (interval=60 + buffer)

if [[ "$1" == "--manual" ]]; then
    MANUAL=true
    GUARD_INTERVAL=5
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

submit_job() {
    # submit and return the job ID, or empty string on failure
    local output
    output=$(sbatch "$@" --wrap="sleep 3600" 2>&1)
    echo "$output" | grep -oP '(?<=Submitted batch job )\d+'
}

wait_for_guard() {
    if $MANUAL; then
        echo "  [running guard manually...]"
        python3.12 -c "
import sys; sys.path.insert(0, '/home/jwen929/maintain')
from task_resource_guard import main; main()
"
    else
        echo "  [waiting ${GUARD_INTERVAL}s for guard to fire...]"
        sleep "$GUARD_INTERVAL"
    fi
}

check_cancelled() {
    local job_id="$1"
    local state
    state=$(squeue --jobs="$job_id" --format="%T" --noheader 2>/dev/null | tr -d ' ')
    # No longer in queue (completed/cancelled) or explicitly CANCELLED
    if [[ -z "$state" || "$state" == "CANCELLED" || "$state" == "COMPLETING" ]]; then
        return 0  # cancelled
    fi
    return 1  # still running or pending
}

run_test() {
    local description="$1"
    local expect="$2"   # "cancelled" or "running"
    local job_id="$3"

    if [[ -z "$job_id" ]]; then
        echo "[FAIL] $description — job did not submit"
        ((FAIL++))
        return
    fi

    echo "  Submitted job $job_id, waiting for guard..."
    wait_for_guard

    # Check final state via sacct (works even after job leaves the queue)
    local sacct_state
    sacct_state=$(sacct --jobs="$job_id" --format=State --noheader --parsable2 2>/dev/null | head -1 | tr -d ' ')

    if [[ "$sacct_state" == CANCELLED* ]]; then
        local result="cancelled"
    elif check_cancelled "$job_id"; then
        # No longer in squeue and sacct shows non-cancelled — treat as cancelled
        # (guard may have fired before sacct updated)
        local result="cancelled"
    else
        local result="running"
        scancel "$job_id" 2>/dev/null  # clean up
    fi

    if [[ "$result" == "$expect" ]]; then
        echo "[PASS] $description"
        ((PASS++))
    else
        echo "[FAIL] $description (expected=$expect, got=$result)"
        ((FAIL++))
    fi
}

# ── Tests ─────────────────────────────────────────────────────────────────────

echo "======================================"
echo " task_resource_guard.py tests"
if $MANUAL; then
    echo " Mode: manual (guard triggered per test)"
else
    echo " Mode: background (waiting ${GUARD_INTERVAL}s per test)"
fi
echo "======================================"
echo ""

echo "--- GPU limit (max: 2) ---"

job=$(submit_job --gres=gpu:a100:2 --mem=1024)
run_test "2 GPUs — should stay running" running "$job"

job=$(submit_job --gres=gpu:a100:3 --mem=1024)
run_test "3 GPUs — should be cancelled" cancelled "$job"

echo ""
echo "--- CPU limit (max: 32) ---"

job=$(submit_job -c 32 --mem=1024)
run_test "32 CPUs — should stay running" running "$job"

job=$(submit_job -c 33 --mem=1024)
run_test "33 CPUs — should be cancelled" cancelled "$job"

echo ""
echo "--- Memory limit (max: 128 GB) ---"

job=$(submit_job --mem=131072)   # exactly 128 GB
run_test "128 GB — should stay running" running "$job"

job=$(submit_job --mem=132096)   # 129 GB
run_test "129 GB — should be cancelled" cancelled "$job"

echo ""
echo "--- Combined: within limits — should stay running ---"

job=$(submit_job --gres=gpu:a100:2 -c 32 --mem=131072 --time=1-00:00:00)
run_test "2 GPUs + 32 CPUs + 128 GB — should stay running" running "$job"

echo ""
echo "--- Combined: multiple violations — should be cancelled ---"

job=$(submit_job --gres=gpu:a100:4 -c 64 --mem=262144)
run_test "4 GPUs + 64 CPUs + 256 GB — should be cancelled" cancelled "$job"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "======================================"
echo " Results: $PASS passed, $FAIL failed"
echo "======================================"
