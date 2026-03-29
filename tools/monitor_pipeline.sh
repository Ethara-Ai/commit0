#!/usr/bin/env bash
# Live pipeline monitor — refreshes every 5 seconds
# Usage: bash monitor_pipeline.sh [RUN_ID]
# Ctrl+C to stop

set -u
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="${1:-minimax-m2.5_returns_nolint-s3}"
LOG_DIR="${BASE_DIR}/logs/agent/${RUN_ID}"
PIPELINE_LOG="${BASE_DIR}/logs/pipeline_${RUN_ID}_results.json"

while true; do
    clear
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  Pipeline Monitor: ${RUN_ID}"
    echo "║  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    # Detect current stage from pipeline log
    MAIN_LOG=$(ls -t "${BASE_DIR}"/logs/minimax*.log "${BASE_DIR}"/logs/*"${RUN_ID}"*.log 2>/dev/null | grep -v agent | head -1)
    if [[ -n "$MAIN_LOG" ]]; then
        CURRENT_STAGE=$(grep -o "STAGE [0-9]:" "$MAIN_LOG" 2>/dev/null | tail -1)
        STAGE_NUM=$(echo "$CURRENT_STAGE" | grep -o '[0-9]' | head -1)
        echo "  📍 Current: ${CURRENT_STAGE:-Starting...}"
    else
        STAGE_NUM=""
        echo "  📍 Current: Waiting for pipeline log..."
    fi
    echo ""

    # Count modules per stage
    for stage_dir in stage1_draft stage2_lint stage3_tests; do
        stage_path="${LOG_DIR}/${stage_dir}"
        if [[ -d "$stage_path" ]]; then
            count=$(find "$stage_path" -name "aider.log" 2>/dev/null | wc -l | tr -d ' ')
            # Check if actively writing (file modified in last 30s)
            active=$(find "$stage_path" -name "aider.log" -mtime -30s 2>/dev/null | tail -1)
            active_module=""
            if [[ -n "$active" ]]; then
                active_module=$(dirname "$active" | xargs basename 2>/dev/null)
            fi

            label=""
            case "$stage_dir" in
                stage1_draft) label="Stage 1 (Draft)" ;;
                stage2_lint)  label="Stage 2 (Lint)" ;;
                stage3_tests) label="Stage 3 (Test)" ;;
            esac

            if [[ -n "$active_module" ]]; then
                echo "  ${label}: ${count} modules ⚡ Active: ${active_module}"
            else
                echo "  ${label}: ${count} modules ✓"
            fi
        fi
    done
    echo ""

    # Latest cost from aider logs
    LATEST_COST=$(grep -rh "Cost:" "${LOG_DIR}" 2>/dev/null | grep "session" | tail -1 | sed 's/.*Cost: //' | tr -d '\n')
    if [[ -n "$LATEST_COST" ]]; then
        echo "  💰 Latest cost line: ${LATEST_COST}"
    fi
    echo ""

    # Show last 3 lines of pipeline log
    if [[ -n "$MAIN_LOG" ]]; then
        echo "  ─── Pipeline Log (last 3) ───"
        tail -3 "$MAIN_LOG" 2>/dev/null | while IFS= read -r line; do
            echo "  ${line}"
        done
    fi
    echo ""

    # Check if pipeline finished
    if [[ -f "$PIPELINE_LOG" ]]; then
        END_TIME=$(python3 -c "import json; d=json.load(open('$PIPELINE_LOG')); print(d.get('end_time',''))" 2>/dev/null)
        if [[ -n "$END_TIME" ]]; then
            echo "  ✅ PIPELINE COMPLETE at ${END_TIME}"
            echo ""
            echo "  ─── Final Results ───"
            python3 -c "
import json
with open('$PIPELINE_LOG') as f:
    d = json.load(f)
for s in ['stage1','stage2','stage3']:
    if s in d:
        r = d[s]
        print(f\"  {r['name']:30s}  {r['num_passed']}/{r['num_tests']}  pass_rate={r['pass_rate']:.4f}\")
" 2>/dev/null
            echo ""
            break
        fi
    fi

    # Check if process still alive
    if ! pgrep -f "run_pipeline.*${RUN_ID}" >/dev/null 2>&1 && ! pgrep -f "agent run.*${RUN_ID}" >/dev/null 2>&1; then
        # Double check — maybe it just finished
        if [[ -f "$PIPELINE_LOG" ]]; then
            echo "  ⚠️  Process not found but results exist — may have just finished"
        else
            echo "  ❌ Pipeline process not found! Check logs for errors."
        fi
    fi

    sleep 5
done
