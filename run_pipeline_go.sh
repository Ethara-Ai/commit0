#!/usr/bin/env bash
# ============================================================
# 3-Stage Go Evaluation Pipeline for Commit0
# ============================================================
#
# Usage:
#     bash run_pipeline_go.sh --model <preset|model_id> --dataset <name>
#
# Examples:
#     bash run_pipeline_go.sh --model opus --dataset conc
#     bash run_pipeline_go.sh --model kimi --dataset conc --branch my-branch
#
# This is the Go counterpart of run_pipeline.sh. It routes through
# commit0/cli_go.py and agent/config_go.py instead of the Python
# pipeline commands.
#
# Requirements: jq, bc
# ============================================================

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -f "${BASE_DIR}/.env" ]]; then
    set -a
    source "${BASE_DIR}/.env"
    set +a
fi
REPO_BASE="${BASE_DIR}/repos"
VENV_PYTHON="${BASE_DIR}/.venv/bin/python"
BACKEND="docker"
MAX_ITERATION=3

MODEL_ARG=""
DATASET_ARG=""
BRANCH_OVERRIDE=""
REPO_SPLIT_OVERRIDE=""
STAGE_TIMEOUT=0
EVAL_TIMEOUT=3600
INACTIVITY_TIMEOUT=900
MAX_WALL_TIME=86400
NUM_SAMPLES=1

print_usage() {
    cat <<'USAGE'
Usage: run_pipeline_go.sh --model <preset|model_id> --dataset <name> [OPTIONS]

Required:
  --model    <preset|id>   Model preset or full model ID
  --dataset  <name|path>   Dataset name or path to JSON file

Model presets: opus, kimi, glm5, minimax, gpt54

Options:
  --branch         <name>    Override auto-generated branch name
  --repo-split     <name>    Override repo_split
  --max-iteration  <n>       Max agent iterations per stage (default: 3)
  --stage-timeout  <secs>    Hard stage timeout in seconds (default: 0=disabled)
  --eval-timeout   <secs>    Eval timeout in seconds (default: 3600)
  --backend        <name>    Backend: docker or modal (default: docker)
  --inactivity-timeout <s>   Kill agent if no activity for N seconds (default: 900)
  --max-wall-time  <secs>    Absolute per-stage wall-time cap (default: 86400)
  --num-samples    <n>       Number of independent samples (default: 1)
  -h, --help                 Show this help
USAGE
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)       [[ $# -lt 2 ]] && { echo "Error: --model requires a value"; exit 1; }; MODEL_ARG="$2";          shift 2 ;;
        --dataset)     [[ $# -lt 2 ]] && { echo "Error: --dataset requires a value"; exit 1; }; DATASET_ARG="$2";       shift 2 ;;
        --branch)      [[ $# -lt 2 ]] && { echo "Error: --branch requires a value"; exit 1; }; BRANCH_OVERRIDE="$2";   shift 2 ;;
        --repo-split)  [[ $# -lt 2 ]] && { echo "Error: --repo-split requires a value"; exit 1; }; REPO_SPLIT_OVERRIDE="$2"; shift 2 ;;
        --max-iteration) [[ $# -lt 2 ]] && { echo "Error: --max-iteration requires a value"; exit 1; }; MAX_ITERATION="$2"; shift 2 ;;
        --stage-timeout) [[ $# -lt 2 ]] && { echo "Error: --stage-timeout requires a value"; exit 1; }; STAGE_TIMEOUT="$2"; shift 2 ;;
        --eval-timeout)  [[ $# -lt 2 ]] && { echo "Error: --eval-timeout requires a value"; exit 1; }; EVAL_TIMEOUT="$2";  shift 2 ;;
        --backend)     [[ $# -lt 2 ]] && { echo "Error: --backend requires a value"; exit 1; }; BACKEND="$2";           shift 2 ;;
        --inactivity-timeout) [[ $# -lt 2 ]] && { echo "Error: --inactivity-timeout requires a value"; exit 1; }; INACTIVITY_TIMEOUT="$2"; shift 2 ;;
        --max-wall-time) [[ $# -lt 2 ]] && { echo "Error: --max-wall-time requires a value"; exit 1; }; MAX_WALL_TIME="$2"; shift 2 ;;
        --num-samples) [[ $# -lt 2 ]] && { echo "Error: --num-samples requires a value"; exit 1; }; NUM_SAMPLES="$2"; shift 2 ;;
        -h|--help)     print_usage ;;
        *)             echo "Error: Unknown argument '$1'"; print_usage ;;
    esac
done

[[ -z "$MODEL_ARG" ]] && { echo "Error: --model is required"; print_usage; }
[[ -z "$DATASET_ARG" ]] && { echo "Error: --dataset is required"; print_usage; }

resolve_model() {
    local arg="$1"
    case "$arg" in
        opus)    MODEL_NAME="bedrock/converse/arn:aws:bedrock:us-east-1:426628337772:application-inference-profile/4w7tmk1iplxi"; MODEL_SHORT="opus4.6"; CACHE_PROMPTS="true" ;;
        kimi)    MODEL_NAME="bedrock/converse/arn:aws:bedrock:us-east-1:426628337772:application-inference-profile/5m69567zugvx"; MODEL_SHORT="kimi-k2.5"; CACHE_PROMPTS="false" ;;
        glm5)    MODEL_NAME="bedrock/converse/arn:aws:bedrock:us-east-1:426628337772:application-inference-profile/8lzlkxguk85a"; MODEL_SHORT="glm-5"; CACHE_PROMPTS="false" ;;
        minimax) MODEL_NAME="bedrock/converse/arn:aws:bedrock:us-east-1:426628337772:application-inference-profile/6oaav7wbxid4"; MODEL_SHORT="minimax-m2.5"; CACHE_PROMPTS="false" ;;
        gpt54)   MODEL_NAME="openai/gpt-5.4"; MODEL_SHORT="gpt-5.4"; CACHE_PROMPTS="false" ;;
        *)
            MODEL_NAME="$arg"
            MODEL_SHORT=$(echo "$arg" | sed 's|.*/||' | tr -dc 'a-zA-Z0-9._-' | cut -c1-20)
            [[ -z "$MODEL_SHORT" ]] && MODEL_SHORT="custom"
            CACHE_PROMPTS="false"
            if [[ "$MODEL_NAME" == bedrock/* ]] && [[ "$MODEL_NAME" == *:aws:bedrock:* ]] && [[ "$MODEL_NAME" != bedrock/converse/* ]]; then
                MODEL_NAME="bedrock/converse/${MODEL_NAME#bedrock/}"
            fi
            ;;
    esac
}
resolve_model "$MODEL_ARG"

if [[ "$MODEL_NAME" == bedrock/* ]] && [[ -n "${AWS_BEARER_TOKEN_BEDROCK:-}" ]]; then
    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE 2>/dev/null || true
    export AWS_SHARED_CREDENTIALS_FILE="/dev/null"
fi

resolve_dataset() {
    local arg="$1"
    if [[ "$arg" == *.json ]] || [[ "$arg" == */* ]]; then
        [[ ! -f "$arg" ]] && [[ -f "${BASE_DIR}/${arg}" ]] && arg="${BASE_DIR}/${arg}"
        [[ ! -f "$arg" ]] && { echo "Error: Dataset file not found: $arg"; exit 1; }
        DATASET_FILE="$arg"
        REPO_SPLIT="${REPO_SPLIT_OVERRIDE:-$(basename "$arg" .json | sed 's/_dataset$//')}"
        DATASET_SHORT=$(basename "$arg" .json)
        return
    fi
    local candidate="${BASE_DIR}/${arg}_go_dataset.json"
    [[ ! -f "$candidate" ]] && candidate="${BASE_DIR}/${arg}_dataset.json"
    if [[ -f "$candidate" ]]; then
        DATASET_FILE="$candidate"
        REPO_SPLIT="${REPO_SPLIT_OVERRIDE:-$arg}"
        DATASET_SHORT="${arg}"
        return
    fi
    local known_splits
    known_splits=$("$VENV_PYTHON" -c "
from commit0.harness.constants_go import GO_SPLIT
for k in sorted(GO_SPLIT.keys()):
    print(k)
" 2>/dev/null || true)
    if echo "$known_splits" | grep -qx "$arg"; then
        DATASET_FILE="wentingzhao/commit0_go"
        REPO_SPLIT="${REPO_SPLIT_OVERRIDE:-$arg}"
        DATASET_SHORT="$arg"
        DATASET_SPLIT="test"
        return
    fi
    echo "Error: Cannot resolve dataset '$arg'"
    exit 1
}

DATASET_FILE=""
REPO_SPLIT=""
DATASET_SHORT=""
DATASET_SPLIT="test"
resolve_dataset "$DATASET_ARG"

BASE_BRANCH_NAME="${BRANCH_OVERRIDE:-aider-go-${MODEL_SHORT}-${DATASET_SHORT}}"
BASE_RUN_ID_FLAT=$(echo "go_${MODEL_SHORT}_${DATASET_SHORT}" | tr -dc 'a-zA-Z0-9._-')
DATASET_DIR_NAME=$(echo "${DATASET_SHORT}" | tr -dc 'a-zA-Z0-9._-')
MODEL_DIR_NAME=$(echo "${MODEL_SHORT}" | tr -dc 'a-zA-Z0-9._-')

set_sample_vars() {
    local sample_idx="$1"
    if [[ "$NUM_SAMPLES" -eq 1 ]]; then
        BRANCH_NAME="${BASE_BRANCH_NAME}"
        RUN_ID="${BASE_RUN_ID_FLAT}"
    else
        BRANCH_NAME="${BASE_BRANCH_NAME}-run_${sample_idx}"
        RUN_ID="${BASE_RUN_ID_FLAT}_run_${sample_idx}"
    fi
    LOG_BASE="${BASE_DIR}/logs/agent_go/${DATASET_DIR_NAME}/${MODEL_DIR_NAME}/run_${sample_idx}"
    PIPELINE_LOG="${BASE_DIR}/logs/pipeline_go_${RUN_ID}_results.json"
    COMMIT0_CONFIG="${BASE_DIR}/.commit0_go_${RUN_ID}.yaml"
    AGENT_CONFIG="${BASE_DIR}/.agent_go_${RUN_ID}.yaml"
}
set_sample_vars 1

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] [${RUN_ID}] $1"; }

write_commit0_go_config() {
    local ds_value
    if [[ "$DATASET_FILE" == wentingzhao/* ]]; then
        ds_value="$DATASET_FILE"
    else
        ds_value="$(cd "$(dirname "$DATASET_FILE")" && pwd)/$(basename "$DATASET_FILE")"
    fi
    cat > "$COMMIT0_CONFIG" <<EOF
base_dir: ${REPO_BASE}
dataset_name: ${ds_value}
dataset_split: ${DATASET_SPLIT}
repo_split: ${REPO_SPLIT}
EOF
    log "  Wrote Go commit0 config: ${COMMIT0_CONFIG}"
}

write_go_agent_config() {
    local run_tests="$1"
    local use_lint_info="$2"
    local use_unit_tests_info="$3"

    cat > "$AGENT_CONFIG" <<EOF
agent_name: aider
model_name: '${MODEL_NAME}'
model_short: '${MODEL_SHORT}'
use_user_prompt: false
user_prompt: 'You need to complete the implementations for all stubbed functions
  (those containing "STUB: not implemented") and pass the unit tests.
  Do not change the names or signatures of existing functions.
  IMPORTANT: You must NEVER modify, edit, or delete any test files
  (files matching *_test.go). Test files are read-only.'
use_topo_sort_dependencies: false
add_import_module_to_context: false
use_repo_info: false
max_repo_info_length: 10000
use_unit_tests_info: ${use_unit_tests_info}
max_unit_tests_info_length: 10000
use_spec_info: false
max_spec_info_length: 10000
use_lint_info: ${use_lint_info}
max_lint_info_length: 10000
run_entire_dir_lint: true
pre_commit_config_path: ''
run_tests: ${run_tests}
max_iteration: ${MAX_ITERATION}
record_test_for_each_commit: false
cache_prompts: ${CACHE_PROMPTS}
max_test_output_length: 15000
capture_thinking: true
trajectory_md: true
output_jsonl: true
EOF
    log "  Wrote Go agent config: ${AGENT_CONFIG}"
}

RESULTS_JSON=""

init_results() {
    RESULTS_JSON=$(jq -n \
        --arg model "$MODEL_SHORT" \
        --arg branch "$BRANCH_NAME" \
        --arg backend "$BACKEND" \
        --arg repo_split "$REPO_SPLIT" \
        --arg dataset "$DATASET_FILE" \
        --arg dataset_short "$DATASET_SHORT" \
        --argjson max_iter "$MAX_ITERATION" \
        --arg start_time "$(ts)" \
        --arg language "go" \
        '{
            language: $language,
            model: $model,
            branch: $branch,
            backend: $backend,
            repo_split: $repo_split,
            dataset: $dataset,
            dataset_short: $dataset_short,
            max_iteration: $max_iter,
            start_time: $start_time
        }')
}

save_results() {
    mkdir -p "$(dirname "$PIPELINE_LOG")"
    echo "$RESULTS_JSON" | jq '.' > "$PIPELINE_LOG"
}

run_go_setup() {
    log "Setting up Go repos..."
    "$VENV_PYTHON" commit0/cli_go.py setup "$REPO_SPLIT" \
        --dataset-name "$DATASET_FILE" \
        --dataset-split "$DATASET_SPLIT" \
        --base-dir "$REPO_BASE"
}

run_go_build() {
    log "Building Go Docker images..."
    "$VENV_PYTHON" commit0/cli_go.py build \
        --num-workers 1
}

EVAL_NUM_PASSED=0
EVAL_NUM_TESTS=0
EVAL_PASS_RATE="0.0"
EVAL_ELAPSED=0

run_go_evaluate() {
    local branch="$1"
    local stage_label="${2:-eval}"

    local eval_log="${LOG_BASE}/${stage_label}_eval.log"
    log "  Running Go evaluation..."

    local start_time
    start_time=$(date +%s)

    set +e
    timeout "$EVAL_TIMEOUT" "$VENV_PYTHON" commit0/cli_go.py evaluate \
        --branch "$branch" \
        --backend "$BACKEND" \
        --timeout 300 \
        --num-cpus 1 \
        --num-workers 1 >"$eval_log" 2>&1
    local eval_rc=$?
    set -e

    local end_time
    end_time=$(date +%s)
    EVAL_ELAPSED=$(( end_time - start_time ))

    log "  Evaluation finished in ${EVAL_ELAPSED}s (rc=${eval_rc})"

    EVAL_NUM_PASSED=0
    EVAL_NUM_TESTS=0
    EVAL_PASS_RATE="0.0"

    local output
    output=$(cat "$eval_log")

    local total_passed=0
    local total_tests=0
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        [[ "$line" == repo,* ]] && continue
        if [[ "$line" == *","*"/"* ]]; then
            local passed_total passed total
            passed_total=$(echo "$line" | cut -d',' -f3 | tr -d ' ')
            if [[ "$passed_total" == *"/"* ]]; then
                passed=$(echo "$passed_total" | cut -d'/' -f1)
                total=$(echo "$passed_total" | cut -d'/' -f2)
                if [[ "$passed" =~ ^[0-9]+$ ]] && [[ "$total" =~ ^[0-9]+$ ]]; then
                    total_passed=$((total_passed + passed))
                    total_tests=$((total_tests + total))
                fi
            fi
        fi
    done <<< "$output"

    EVAL_NUM_PASSED="$total_passed"
    EVAL_NUM_TESTS="$total_tests"
    if [[ "$total_tests" -gt 0 ]]; then
        EVAL_PASS_RATE=$(echo "scale=6; $total_passed / $total_tests" | bc)
    fi
}

run_go_agent() {
    local branch="$1"
    local override="$2"
    local log_dir="$3"

    local agent_log="${log_dir}/agent_run.log"
    mkdir -p "$log_dir"

    local cmd=(
        "$VENV_PYTHON" agent/config_go.py run "$branch"
        --backend "$BACKEND"
        --agent-config-file "$AGENT_CONFIG"
        --commit0-config-file "$COMMIT0_CONFIG"
        --log-dir "$log_dir"
        --max-parallel-repos 1
    )

    if [[ "$override" == "true" ]]; then
        cmd+=(--override-previous-changes)
    fi

    log "  Running Go agent..."
    local start_time
    start_time=$(date +%s)

    set +e
    "${cmd[@]}" >>"$agent_log" 2>&1
    AGENT_RC=$?
    set -e

    local end_time
    end_time=$(date +%s)
    AGENT_ELAPSED=$(( end_time - start_time ))
    log "  Agent finished in ${AGENT_ELAPSED}s (rc=${AGENT_RC})"
}

AGENT_ELAPSED=0
AGENT_RC=0

format_pct() {
    local val="$1"
    printf "%.1f%%" "$(echo "$val * 100" | bc)"
}

preflight() {
    local errors=0
    for cmd in jq bc; do
        if ! command -v "$cmd" &>/dev/null; then
            echo "Error: Required command '$cmd' not found"
            errors=$((errors + 1))
        fi
    done
    [[ ! -x "$VENV_PYTHON" ]] && { echo "Error: Python venv not found at $VENV_PYTHON"; errors=$((errors + 1)); }
    [[ ! -d "$REPO_BASE" ]] && { echo "Error: Repo base not found at $REPO_BASE"; errors=$((errors + 1)); }
    [[ "$errors" -gt 0 ]] && { echo "Preflight failed with $errors error(s)."; exit 1; }
}

stage_1_draft() {
    log "======================================================================"
    log "STAGE 1: Draft Initial Go Implementations"
    log "======================================================================"

    write_go_agent_config "false" "false" "true"
    local stage_log_dir="${LOG_BASE}/stage1_draft"
    mkdir -p "$stage_log_dir"

    run_go_agent "$BRANCH_NAME" "true" "$stage_log_dir"
    run_go_evaluate "$BRANCH_NAME" "stage1"

    log "  Stage 1 results: ${EVAL_NUM_PASSED}/${EVAL_NUM_TESTS} ($(format_pct "$EVAL_PASS_RATE"))"

    RESULTS_JSON=$(echo "$RESULTS_JSON" | jq \
        --argjson elapsed "$AGENT_ELAPSED" \
        --argjson eval_time "$EVAL_ELAPSED" \
        --argjson rc "$AGENT_RC" \
        --argjson num_passed "$EVAL_NUM_PASSED" \
        --argjson num_tests "$EVAL_NUM_TESTS" \
        --argjson pass_rate "$EVAL_PASS_RATE" \
        '.stage1 = {
            name: "Draft (no feedback)",
            elapsed_s: $elapsed,
            eval_time_s: $eval_time,
            returncode: $rc,
            num_passed: $num_passed,
            num_tests: $num_tests,
            pass_rate: $pass_rate
        }')
    save_results
}

stage_2_lint_refine() {
    log "======================================================================"
    log "STAGE 2: Refine with Go Static Analysis"
    log "======================================================================"

    write_go_agent_config "false" "true" "false"
    local stage_log_dir="${LOG_BASE}/stage2_lint"
    mkdir -p "$stage_log_dir"

    run_go_agent "$BRANCH_NAME" "false" "$stage_log_dir"
    run_go_evaluate "$BRANCH_NAME" "stage2"

    log "  Stage 2 results: ${EVAL_NUM_PASSED}/${EVAL_NUM_TESTS} ($(format_pct "$EVAL_PASS_RATE"))"

    RESULTS_JSON=$(echo "$RESULTS_JSON" | jq \
        --argjson elapsed "$AGENT_ELAPSED" \
        --argjson eval_time "$EVAL_ELAPSED" \
        --argjson rc "$AGENT_RC" \
        --argjson num_passed "$EVAL_NUM_PASSED" \
        --argjson num_tests "$EVAL_NUM_TESTS" \
        --argjson pass_rate "$EVAL_PASS_RATE" \
        '.stage2 = {
            name: "Lint refine (goimports+staticcheck+govet)",
            elapsed_s: $elapsed,
            eval_time_s: $eval_time,
            returncode: $rc,
            num_passed: $num_passed,
            num_tests: $num_tests,
            pass_rate: $pass_rate
        }')
    save_results
}

stage_3_test_refine() {
    log "======================================================================"
    log "STAGE 3: Refine with Go Test Feedback"
    log "======================================================================"

    write_go_agent_config "true" "true" "false"
    local stage_log_dir="${LOG_BASE}/stage3_tests"
    mkdir -p "$stage_log_dir"

    run_go_agent "$BRANCH_NAME" "false" "$stage_log_dir"
    run_go_evaluate "$BRANCH_NAME" "stage3"

    log "  Stage 3 results: ${EVAL_NUM_PASSED}/${EVAL_NUM_TESTS} ($(format_pct "$EVAL_PASS_RATE"))"

    RESULTS_JSON=$(echo "$RESULTS_JSON" | jq \
        --argjson elapsed "$AGENT_ELAPSED" \
        --argjson eval_time "$EVAL_ELAPSED" \
        --argjson rc "$AGENT_RC" \
        --argjson num_passed "$EVAL_NUM_PASSED" \
        --argjson num_tests "$EVAL_NUM_TESTS" \
        --argjson pass_rate "$EVAL_PASS_RATE" \
        '.stage3 = {
            name: "Test refine (go test -json)",
            elapsed_s: $elapsed,
            eval_time_s: $eval_time,
            returncode: $rc,
            num_passed: $num_passed,
            num_tests: $num_tests,
            pass_rate: $pass_rate
        }')
    save_results
}

print_summary_table() {
    log ""
    log "=========================================================================================="
    log "GO PIPELINE RESULTS — 3-Stage Pipeline"
    log "Model: ${MODEL_SHORT} | Dataset: ${DATASET_SHORT} | Branch: ${BRANCH_NAME}"
    log "=========================================================================================="

    printf -v header "%-40s %12s %14s %10s" "Stage" "Pass Rate" "Passed/Total" "Time (s)"
    log "$header"
    log "------------------------------------------------------------------------------------"

    for stage_key in stage1 stage2 stage3; do
        local name passed total pass_rate elapsed
        name=$(echo "$RESULTS_JSON" | jq -r ".${stage_key}.name // \"—\"")
        [[ "$name" == "—" ]] && continue
        passed=$(echo "$RESULTS_JSON" | jq -r ".${stage_key}.num_passed // 0")
        total=$(echo "$RESULTS_JSON" | jq -r ".${stage_key}.num_tests // 0")
        pass_rate=$(echo "$RESULTS_JSON" | jq -r ".${stage_key}.pass_rate // 0")
        elapsed=$(echo "$RESULTS_JSON" | jq -r ".${stage_key}.elapsed_s // 0")

        printf -v row "%-40s %12s %14s %10s" "$name" "$(format_pct "$pass_rate")" "${passed}/${total}" "$(printf "%.0f" "$elapsed")"
        log "$row"
    done

    log "------------------------------------------------------------------------------------"
    log "Results saved to: ${PIPELINE_LOG}"
    log ""
}

cleanup() {
    for _si in $(seq 1 "$NUM_SAMPLES"); do
        set_sample_vars "$_si"
        rm -f "$COMMIT0_CONFIG" "$AGENT_CONFIG" 2>/dev/null || true
    done
}
trap cleanup EXIT
trap 'exit' INT TERM

main() {
    preflight
    log "Starting Go 3-Stage Pipeline"
    log "  Model: ${MODEL_SHORT} | Dataset: ${DATASET_SHORT} | Repo: ${REPO_SPLIT}"

    for sample_idx in $(seq 1 "$NUM_SAMPLES"); do
        set_sample_vars "$sample_idx"
        mkdir -p "$LOG_BASE"

        init_results
        write_commit0_go_config

        run_go_setup
        run_go_build

        stage_1_draft
        stage_2_lint_refine
        stage_3_test_refine

        RESULTS_JSON=$(echo "$RESULTS_JSON" | jq --arg end_time "$(ts)" '. + {end_time: $end_time}')
        save_results
        print_summary_table
    done

    log "Go pipeline complete."
}

main
