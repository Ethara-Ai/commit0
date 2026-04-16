#!/usr/bin/env python3
"""Live pipeline monitor. Usage: .venv/bin/python tools/monitor_pipeline.py [RUN_ID]"""

import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.cells import cell_len
from rich.color import Color
from rich.console import Console, Group
from rich.live import Live
from rich.style import Style
from rich.text import Text

logger = logging.getLogger(__name__)


def _find_project_root():
    path = Path(__file__).resolve().parent
    while path != path.parent:
        if (path / "pyproject.toml").exists():
            return path
        path = path.parent
    raise RuntimeError("Cannot find project root (no pyproject.toml found)")


BASE_DIR = _find_project_root()
RUN_ID = sys.argv[1] if len(sys.argv) > 1 else "minimax-m2.5_returns_nolint-s3"
LOG_DIR = BASE_DIR / "logs" / "agent" / RUN_ID
PIPELINE_LOG = BASE_DIR / "logs" / f"pipeline_{RUN_ID}_results.json"

STAGES = [
    ("stage1_draft", "S1 Draft", "cyan"),
    ("stage2_lint", "S2 Lint", "yellow"),
    ("stage3_tests", "S3 Test", "green"),
]

COST_RE = re.compile(r"Cost:\s+\$[\d.]+\s+message,\s+\$([\d.]+)\s+session")

# ── Aesthetic constants ──────────────────────────────────────────────────
# Spinner frames (manually cycled each refresh for animation)
SPINNER_FRAMES = "⣾⣽⣻⢿⡿⣟⣯⣷"
PULSE_DOTS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Stage color palettes (from_rgb, to_rgb) for gradient bars
STAGE_GRADIENTS = {
    "cyan": ((30, 140, 180), (80, 240, 255)),  # deep teal → electric cyan
    "yellow": ((180, 120, 20), (255, 220, 60)),  # burnt amber → bright gold
    "green": ((30, 160, 80), (100, 255, 140)),  # forest → neon emerald
}

# Eval bar gradient
EVAL_GRADIENTS = {
    "green": ((40, 180, 80), (120, 255, 160)),
    "yellow": ((200, 160, 30), (255, 230, 80)),
    "red": ((200, 50, 50), (255, 100, 80)),
    "dim": ((80, 80, 80), (120, 120, 120)),
}

# Box chrome
BORDER_COLOR = Style(color=Color.from_rgb(60, 70, 90))
ACCENT_DIM = Style(color=Color.from_rgb(70, 80, 100))
HEADER_BORDER = Style(color=Color.from_rgb(80, 100, 140))

_frame_counter = 0


def find_pipeline_log():
    logs_dir = BASE_DIR / "logs"
    if not logs_dir.is_dir():
        return None
    dataset_hint = RUN_ID.split("_", 1)[-1].split("_")[0] if "_" in RUN_ID else RUN_ID
    for c in sorted(
        logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if "agent" in str(c.relative_to(BASE_DIR)):
            continue
        if dataset_hint.lower().replace("-", "") in c.stem.lower().replace(
            "-", ""
        ).replace("_", ""):
            return c
    for c in sorted(
        logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if "agent" not in str(c.relative_to(BASE_DIR)):
            return c
    return None


def get_current_stage_key(log_path):
    if not log_path or not log_path.exists():
        return None
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk_size = min(size, 8192)
            f.seek(size - chunk_size)
            tail = f.read().decode(errors="replace")
        matches = re.findall(r"STAGE (\d):", tail)
        if matches:
            num = int(matches[-1])
            if 1 <= num <= 3:
                return STAGES[num - 1][0]
    except OSError:
        logger.debug("Failed to read log file for stage key detection", exc_info=True)
    return None


def _detect_total_modules():
    best = 0
    for stage_key, _, _ in STAGES:
        stage_dir = LOG_DIR / stage_key
        if stage_dir.exists():
            count = len(list(stage_dir.rglob("aider.log")))
            if count > best:
                best = count
    return best if best > 0 else 1


def count_modules(stage_dir):
    if not stage_dir.exists():
        return 0, []
    logs = list(stage_dir.rglob("aider.log"))
    return len(logs), logs


def get_active_module(logs):
    if not logs:
        return None, 0
    now = time.time()
    newest = max(logs, key=lambda p: p.stat().st_mtime)
    age = int(now - newest.stat().st_mtime)
    if age < 300:
        return newest.parent.name.replace("__", "."), age
    return None, 0


def get_stage_cost(stage_dir):
    total = 0.0
    if not stage_dir.exists():
        return total
    for log in stage_dir.rglob("aider.log"):
        try:
            last = None
            with open(log, errors="replace") as f:
                for line in f:
                    m = COST_RE.search(line)
                    if m:
                        last = m
            if last:
                total += float(last.group(1))
        except OSError:
            logger.debug("Failed to read cost from %s", log, exc_info=True)
    return total


def is_alive():
    try:
        return (
            subprocess.run(
                ["pgrep", "-f", f"agent run.*{RUN_ID.replace('_', '.')}"],
                capture_output=True,
                timeout=5,
            ).returncode
            == 0
        )
    except Exception:
        logger.debug("Failed to check if pipeline is alive", exc_info=True)
        return False


def get_elapsed(log_path, end_time_str=None):
    if not log_path or not log_path.exists():
        return ""
    try:
        with open(log_path, errors="replace") as f:
            first = f.readline()
        m = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", first)
        if m:
            start = datetime.strptime(m.group(), "%Y-%m-%d %H:%M:%S")
            if end_time_str:
                end_m = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", end_time_str)
                end = (
                    datetime.strptime(end_m.group(), "%Y-%m-%d %H:%M:%S")
                    if end_m
                    else datetime.now()
                )
            else:
                end = datetime.now()
            s = int((end - start).total_seconds())
            return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    except Exception:
        logger.debug("Failed to parse elapsed time from log", exc_info=True)
    return ""


# ── Visual rendering helpers ─────────────────────────────────────────────


def _lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB tuples."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def make_gradient_bar(
    ratio,
    width,
    color_key,
    filled_char="━",
    empty_char="╌",
    is_done=False,
    is_pending=False,
):
    """Build a Rich Text with per-character color gradient for the filled portion."""
    ratio = max(0.0, min(ratio, 1.0))
    filled = int(ratio * width)
    empty = width - filled

    bar = Text()

    if is_pending:
        # Dim monochrome for pending stages
        bar.append(empty_char * width, style=Style(color=Color.from_rgb(50, 55, 65)))
        return bar

    # Get gradient endpoints
    grad = STAGE_GRADIENTS.get(color_key, ((100, 100, 100), (200, 200, 200)))

    if is_done:
        # Completed: full solid glow at 60% brightness (elegant, not overwhelming)
        mid = _lerp_color(grad[0], grad[1], 0.6)
        done_style = Style(color=Color.from_rgb(*mid))
        bar.append(filled_char * filled, style=done_style)
        bar.append(empty_char * empty, style=Style(color=Color.from_rgb(45, 50, 60)))
        return bar

    # Active: per-character gradient
    for i in range(filled):
        t = i / max(filled - 1, 1)
        r, g, b = _lerp_color(grad[0], grad[1], t)
        bar.append(filled_char, style=Style(color=Color.from_rgb(r, g, b)))

    # Empty portion with subtle dot pattern
    bar.append(empty_char * empty, style=Style(color=Color.from_rgb(45, 50, 60)))
    return bar


def make_eval_bar(rate, width, rate_color):
    """Build gradient bar for evaluation results."""
    ratio = max(0.0, min(rate, 1.0))
    filled = int(ratio * width)
    empty = width - filled

    bar = Text()
    grad = EVAL_GRADIENTS.get(rate_color, EVAL_GRADIENTS["dim"])

    for i in range(filled):
        t = i / max(filled - 1, 1)
        r, g, b = _lerp_color(grad[0], grad[1], t)
        bar.append("▪", style=Style(color=Color.from_rgb(r, g, b)))

    bar.append("·" * empty, style=Style(color=Color.from_rgb(40, 45, 55)))
    return bar


def _header_rule(text, width, alive):
    """Create a styled horizontal rule with centered text."""
    rule_char = "─"
    label = f" {text} "
    label_cells = cell_len(label)
    side_width = (width - label_cells - 2) // 2
    side_width = max(2, side_width)

    line = Text()
    line.append("╭" + rule_char * side_width, style=HEADER_BORDER)
    if alive:
        line.append(label, style=Style(color=Color.from_rgb(100, 200, 255), bold=True))
    else:
        line.append(label, style=Style(color=Color.from_rgb(255, 100, 80), bold=True))
    remaining = width - side_width - label_cells - 2
    line.append(rule_char * max(0, remaining) + "╮", style=HEADER_BORDER)
    return line


def _footer_rule(width):
    """Bottom border."""
    line = Text()
    line.append("╰" + "─" * (width - 2) + "╯", style=BORDER_COLOR)
    return line


def _section_divider(label, width):
    """Subtle section divider with label."""
    rule_char = "╌"
    label_str = f" {label} "
    left_w = 3
    right_w = max(0, width - left_w - cell_len(label_str) - 4)

    line = Text()
    line.append("│ ", style=BORDER_COLOR)
    line.append(rule_char * left_w, style=ACCENT_DIM)
    line.append(
        label_str,
        style=Style(color=Color.from_rgb(100, 115, 145), bold=False, italic=True),
    )
    line.append(rule_char * right_w, style=ACCENT_DIM)
    line.append(" │", style=BORDER_COLOR)
    return line


def _bordered_line(content, width):
    """Wrap a Text or string inside │ ... │ border, padding to width."""
    line = Text()
    line.append("│ ", style=BORDER_COLOR)

    inner_width = width - 4
    if isinstance(content, Text):
        visible_len = cell_len(content.plain)
        if visible_len > inner_width:
            chars = 0
            cw = 0
            for ch in content.plain:
                cw += cell_len(ch)
                if cw > inner_width:
                    break
                chars += 1
            content.truncate(chars)
            visible_len = cell_len(content.plain)
        line.append_text(content)
        if visible_len < inner_width:
            line.append(" " * (inner_width - visible_len))
    elif isinstance(content, str):
        if cell_len(content) > inner_width:
            while cell_len(content) > inner_width and content:
                content = content[:-1]
        line.append(content)
        pad = inner_width - cell_len(content)
        if pad > 0:
            line.append(" " * pad)
    else:
        line.append(str(content))

    line.append(" │", style=BORDER_COLOR)
    return line


def _empty_bordered(width):
    """Empty bordered line."""
    line = Text()
    line.append("│", style=BORDER_COLOR)
    line.append(" " * (width - 2))
    line.append("│", style=BORDER_COLOR)
    return line


# ── Main build ───────────────────────────────────────────────────────────


def build_all(console_width):
    global _frame_counter
    _frame_counter += 1

    log_path = find_pipeline_log()
    current_key = get_current_stage_key(log_path)
    alive = is_alive()
    now = datetime.now().strftime("%H:%M:%S")

    results = None
    if PIPELINE_LOG.exists():
        try:
            with open(PIPELINE_LOG) as f:
                results = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    pipeline_finished = bool(results and results.get("end_time"))
    pipeline_errored = bool(results and results.get("error"))
    # No stage is "active" once the pipeline is done, errored, or the process died
    pipeline_idle = pipeline_finished or pipeline_errored or not alive

    elapsed = get_elapsed(log_path, results.get("end_time") if results else None)

    w = min(console_width, 157)  # Cap at terminal width
    inner = w - 4  # usable inside borders

    parts = []

    # ── HEADER ───────────────────────────────────────────────────────
    if pipeline_errored:
        spinner = "✖"
        status_label = "PIPELINE ERROR"
        header_alive = False
    elif pipeline_finished:
        spinner = "◆"
        status_label = "PIPELINE COMPLETE"
        header_alive = True
    elif alive:
        spinner = PULSE_DOTS[_frame_counter % len(PULSE_DOTS)]
        status_label = "PIPELINE ACTIVE"
        header_alive = True
    else:
        spinner = "■"
        status_label = "PIPELINE STOPPED"
        header_alive = False
    parts.append(_header_rule(f"{spinner} {status_label}", w, header_alive))

    # Status bar line
    status_content = Text()
    if pipeline_errored:
        status_content.append(
            "✖ ", style=Style(color=Color.from_rgb(255, 90, 70), bold=True)
        )
        status_content.append(
            "ERROR", style=Style(color=Color.from_rgb(255, 90, 70), bold=True)
        )
    elif pipeline_finished:
        status_content.append(
            "◆ ", style=Style(color=Color.from_rgb(80, 230, 160), bold=True)
        )
        status_content.append(
            "COMPLETE", style=Style(color=Color.from_rgb(80, 230, 160), bold=True)
        )
    elif alive:
        status_content.append(
            "● ", style=Style(color=Color.from_rgb(80, 255, 140), bold=True)
        )
        status_content.append(
            "RUNNING", style=Style(color=Color.from_rgb(80, 255, 140), bold=True)
        )
    else:
        status_content.append(
            "■ ", style=Style(color=Color.from_rgb(255, 90, 70), bold=True)
        )
        status_content.append(
            "STOPPED", style=Style(color=Color.from_rgb(255, 90, 70), bold=True)
        )

    status_content.append("  ░  ", style=Style(color=Color.from_rgb(50, 55, 70)))
    status_content.append(
        RUN_ID, style=Style(color=Color.from_rgb(180, 195, 220), bold=True)
    )
    status_content.append("  ░  ", style=Style(color=Color.from_rgb(50, 55, 70)))
    status_content.append(
        f"⏱ {elapsed}", style=Style(color=Color.from_rgb(140, 155, 180))
    )
    status_content.append("  ░  ", style=Style(color=Color.from_rgb(50, 55, 70)))
    status_content.append(now, style=Style(color=Color.from_rgb(100, 115, 140)))

    parts.append(_bordered_line(status_content, w))
    parts.append(_empty_bordered(w))

    # ── STAGES ───────────────────────────────────────────────────────
    total_target = _detect_total_modules()
    total_cost = 0.0
    bar_width = max(20, min(55, inner - 55))  # Auto-adapt, leave room for stats

    stage_num = 0
    for stage_key, label, color in STAGES:
        stage_num += 1
        stage_dir = LOG_DIR / stage_key
        count, logs = count_modules(stage_dir)
        active, age = get_active_module(logs)
        cost = get_stage_cost(stage_dir)
        total_cost += cost

        is_current = stage_key == current_key and not pipeline_idle
        is_done = stage_dir.exists() and not is_current and count > 0
        is_pending = not stage_dir.exists()

        ratio = count / total_target if total_target > 0 else 0
        pct = f"{ratio * 100:5.1f}%"

        # ── Stage header icon + label ──
        stage_line = Text()
        if is_current:
            spin_char = SPINNER_FRAMES[_frame_counter % len(SPINNER_FRAMES)]
            stage_line.append(
                f" {spin_char} ",
                style=Style(color=Color.from_rgb(255, 200, 50), bold=True),
            )
            stage_line.append(
                f"{label}", style=Style(color=Color.from_rgb(255, 230, 100), bold=True)
            )
        elif is_done:
            stage_line.append(
                " ✔ ", style=Style(color=Color.from_rgb(80, 230, 120), bold=True)
            )
            stage_line.append(
                f"{label}", style=Style(color=Color.from_rgb(120, 210, 150))
            )
        else:
            stage_line.append(" ○ ", style=Style(color=Color.from_rgb(70, 75, 90)))
            stage_line.append(
                f"{label}", style=Style(color=Color.from_rgb(90, 95, 110))
            )

        # Stats after label
        stage_line.append("  ", style=Style())
        bar = make_gradient_bar(
            ratio, bar_width, color, is_done=is_done, is_pending=is_pending
        )
        stage_line.append_text(bar)
        stage_line.append("  ", style=Style())

        # Percentage with color matching stage state
        if is_current:
            pct_color = STAGE_GRADIENTS[color][1]
            stage_line.append(
                pct, style=Style(color=Color.from_rgb(*pct_color), bold=True)
            )
        elif is_done:
            stage_line.append(pct, style=Style(color=Color.from_rgb(120, 200, 150)))
        else:
            stage_line.append(pct, style=Style(color=Color.from_rgb(70, 75, 90)))

        # Module count
        stage_line.append(
            f"  {count:>2}", style=Style(color=Color.from_rgb(150, 160, 180))
        )
        stage_line.append(
            f"/{total_target}", style=Style(color=Color.from_rgb(80, 85, 100))
        )

        # Cost
        cost_str = f"  ${cost:.4f}"
        if cost > 0:
            stage_line.append(cost_str, style=Style(color=Color.from_rgb(200, 170, 80)))
        else:
            stage_line.append(cost_str, style=Style(color=Color.from_rgb(60, 65, 75)))

        parts.append(_bordered_line(stage_line, w))

        # ── Active module / status sub-line ──
        result_key = {
            "stage1_draft": "stage1",
            "stage2_lint": "stage2",
            "stage3_tests": "stage3",
        }[stage_key]
        has_eval = bool(results and results.get(result_key))

        sub_line = Text()
        if is_current and active:
            age_str = f"{age}s ago" if age > 0 else "now"
            sub_line.append(
                "      ↳ ", style=Style(color=Color.from_rgb(100, 110, 140))
            )
            sub_line.append(
                active, style=Style(color=Color.from_rgb(220, 230, 255), bold=True)
            )
            sub_line.append(
                f"  ({age_str})",
                style=Style(color=Color.from_rgb(90, 100, 130), italic=True),
            )
            parts.append(_bordered_line(sub_line, w))
        elif is_current:
            sub_line.append(
                "      ↳ ", style=Style(color=Color.from_rgb(100, 110, 140))
            )
            pulse = PULSE_DOTS[(_frame_counter + 3) % len(PULSE_DOTS)]
            sub_line.append(
                f"{pulse} processing",
                style=Style(color=Color.from_rgb(200, 180, 80), italic=True),
            )
            parts.append(_bordered_line(sub_line, w))
        elif is_done and not has_eval:
            sub_line.append("      ↳ ", style=Style(color=Color.from_rgb(80, 180, 120)))
            sub_line.append(
                "✔ done",
                style=Style(color=Color.from_rgb(80, 200, 130), italic=True),
            )
            parts.append(_bordered_line(sub_line, w))
        elif is_pending:
            sub_line.append("      ↳ ", style=Style(color=Color.from_rgb(60, 65, 80)))
            sub_line.append(
                "waiting", style=Style(color=Color.from_rgb(70, 75, 90), italic=True)
            )
            parts.append(_bordered_line(sub_line, w))

        # ── Eval results ──
        if has_eval:
            r = results[result_key]
            passed = r.get("num_passed", 0)
            total = r.get("num_tests", 0)
            rate = r.get("pass_rate", 0.0)

            rate_pct = f"{rate * 100:.1f}%"
            rate_color = (
                "green"
                if rate > 0.5
                else "yellow"
                if rate > 0.1
                else "red"
                if total > 0
                else "dim"
            )

            eval_line = Text()
            eval_line.append(
                "      ├ eval ", style=Style(color=Color.from_rgb(80, 90, 115))
            )
            eval_bar = make_eval_bar(rate, min(bar_width, 40), rate_color)
            eval_line.append_text(eval_bar)
            eval_line.append(
                f"  {rate_pct}",
                style=Style(
                    color=Color.from_rgb(
                        *{
                            "green": (80, 230, 120),
                            "yellow": (230, 200, 60),
                            "red": (230, 80, 70),
                            "dim": (90, 95, 110),
                        }[rate_color]
                    ),
                    bold=True,
                ),
            )
            eval_line.append(
                f"  ({passed}/{total})",
                style=Style(color=Color.from_rgb(100, 110, 130)),
            )
            parts.append(_bordered_line(eval_line, w))

        # Stage separator (except after last stage)
        if stage_num < len(STAGES):
            parts.append(_empty_bordered(w))

    # ── COST SUMMARY ─────────────────────────────────────────────────
    parts.append(_empty_bordered(w))
    parts.append(_section_divider("COST", w))

    cost_line = Text()
    cost_line.append(" 💰 Total:  ", style=Style(color=Color.from_rgb(140, 150, 170)))
    if total_cost > 0:
        cost_dollars = f"${total_cost:.4f}"
        # Gold gradient for cost
        for i, ch in enumerate(cost_dollars):
            t = i / max(len(cost_dollars) - 1, 1)
            r, g, b = _lerp_color((220, 170, 50), (255, 240, 120), t)
            cost_line.append(ch, style=Style(color=Color.from_rgb(r, g, b), bold=True))
    else:
        cost_line.append("$0.0000", style=Style(color=Color.from_rgb(70, 75, 90)))

    parts.append(_bordered_line(cost_line, w))

    # ── COMPLETION STATE ─────────────────────────────────────────────
    if results:
        end_time = results.get("end_time", "")
        error = results.get("error", "")
        parts.append(_empty_bordered(w))

        if end_time:
            done_line = Text()
            # Celebratory gradient text
            label = "◆ PIPELINE COMPLETE"
            for i, ch in enumerate(label):
                t = i / max(len(label) - 1, 1)
                r, g, b = _lerp_color((60, 200, 120), (120, 255, 200), t)
                done_line.append(
                    ch, style=Style(color=Color.from_rgb(r, g, b), bold=True)
                )
            done_line.append(
                f"  {end_time}",
                style=Style(color=Color.from_rgb(100, 115, 140), italic=True),
            )
            parts.append(_bordered_line(done_line, w))

        if error:
            err_line = Text()
            err_line.append(
                " ✖ ERROR: ", style=Style(color=Color.from_rgb(255, 80, 60), bold=True)
            )
            err_msg = error
            max_err = inner - 12
            if cell_len(err_msg) > max_err:
                while cell_len(err_msg) > max_err - 3 and err_msg:
                    err_msg = err_msg[:-1]
                err_msg = err_msg + "..."
            err_line.append(err_msg, style=Style(color=Color.from_rgb(255, 130, 110)))
            parts.append(_bordered_line(err_line, w))

    # ── LOG TAIL ─────────────────────────────────────────────────────
    if log_path and log_path.exists():
        try:
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                chunk = min(size, 4096)
                f.seek(size - chunk)
                raw = f.read().decode(errors="replace")
            tail = raw.strip().split("\n")[-4:]
        except OSError:
            tail = []

        if tail:
            parts.append(_empty_bordered(w))
            parts.append(_section_divider("LOG", w))

            for raw_line in tail:
                raw_line = raw_line.strip()
                max_line_len = inner - 4
                if cell_len(raw_line) > max_line_len:
                    while cell_len(raw_line) > max_line_len - 3 and raw_line:
                        raw_line = raw_line[:-1]
                    raw_line = raw_line + "..."

                log_line = Text()
                log_line.append(" ", style=Style())

                if "ERROR" in raw_line or "FAILED" in raw_line:
                    log_line.append(
                        "▸ ", style=Style(color=Color.from_rgb(255, 80, 60))
                    )
                    log_line.append(
                        raw_line, style=Style(color=Color.from_rgb(255, 120, 100))
                    )
                elif "STAGE" in raw_line or "results:" in raw_line.lower():
                    log_line.append(
                        "▸ ", style=Style(color=Color.from_rgb(80, 180, 230))
                    )
                    log_line.append(
                        raw_line, style=Style(color=Color.from_rgb(120, 200, 240))
                    )
                else:
                    log_line.append("  ", style=Style())
                    log_line.append(
                        raw_line, style=Style(color=Color.from_rgb(90, 100, 120))
                    )

                parts.append(_bordered_line(log_line, w))

    # ── FOOTER ───────────────────────────────────────────────────────
    parts.append(_footer_rule(w))

    # Hint line (outside the box)
    hint = Text()
    hint.append("  Ctrl+C", style=Style(color=Color.from_rgb(100, 115, 140), bold=True))
    hint.append(" to exit  ░  ", style=Style(color=Color.from_rgb(60, 70, 90)))
    hint.append(
        "refreshing every 3s",
        style=Style(color=Color.from_rgb(60, 70, 90), italic=True),
    )
    parts.append(hint)

    return Group(*parts)


def main():
    console = Console()
    try:
        with Live(console=console, refresh_per_second=0.5, screen=True) as live:
            while True:
                live.update(build_all(console.width))
                time.sleep(3)
    except KeyboardInterrupt:
        console.print()
        farewell = Text()
        farewell.append("  ■", style=Style(color=Color.from_rgb(100, 115, 140)))
        farewell.append(
            " Monitor stopped.",
            style=Style(color=Color.from_rgb(80, 90, 110), italic=True),
        )
        console.print(farewell)


if __name__ == "__main__":
    main()
