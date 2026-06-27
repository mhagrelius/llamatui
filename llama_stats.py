#!/usr/bin/env python3
"""llama_stats.py - usage statistics for the llama-server logs produced by the
run-llama-server*.ps1 supervisors in this directory.

Single file, standard library only. Parses two kinds of log:

  * llama-server*.log          - the server's own stdout/stderr (relative
                                 timestamps that reset on every restart). Source
                                 of per-request timings, speculative-decode
                                 acceptance, prompt-cache occupancy and errors.
  * supervisor*.log            - the .ps1 supervisor's wall-clock journal. Source
                                 of launch/exit/crash lifecycle and uptime.

Logs from the two deployments live side by side and are reported separately:
  * default  -> llama-server.log        (+ rotated llama-server-YYYYMMDD-*.log)
  * copilot  -> llama-server-copilot.log (+ rotated copilot archives)

Usage:
    python llama_stats.py                      # report on C:\\llama\\logs
    python llama_stats.py --log-dir D:\\logs
    python llama_stats.py a.log b.log          # explicit files
    python llama_stats.py --json               # machine-readable
    python llama_stats.py --csv requests.csv   # dump one row per request
    python llama_stats.py --top 10             # show N slowest/largest requests
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_LOG_DIR = r"C:\llama\logs"

# --------------------------------------------------------------------------- #
# Regexes for llama-server*.log lines
# --------------------------------------------------------------------------- #
# Line shape:  "<reltime> <I|W|E|D> <message>"  e.g. "4.23.044.774 I slot ..."
# The reltime (4 dotted ints) is elapsed time since process start; it resets on
# every restart, so a decrease across consecutive lines marks a new server run.
_SRV_PREFIX = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)\.(\d+)\s+([IWED])\s+(.*)$")
# PowerShell 7 wraps native stderr as error records prefixed with "<exe> : " and
# writes the copilot log as UTF-16; strip that prefix so the reltime is at col 0.
_EXE_PREFIX = re.compile(r"^\s*\S+\.exe\s*:\s+")
# A throughput >= this is llama.cpp's sentinel for a degenerate (sub-token) eval,
# not a real rate; excluded from throughput statistics.
_TPS_SENTINEL = 1e5

_RE_LOADING = re.compile(r"llama_server:\s*loading model")
_RE_MODEL = re.compile(r"load_model:\s*loading model '([^']+)'")
_RE_GPU = re.compile(r"-\s*CUDA\d+\s*:\s*(.+?)\s*\(")
_RE_THREADS = re.compile(r"system_info:\s*n_threads\s*=\s*(\d+)")
_RE_NCTX = re.compile(r"new slot,\s*n_ctx\s*=\s*(\d+)")

# "print_timing: id  0 | task 98 | <rest>"
_RE_TIMING = re.compile(r"print_timing:\s*id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*(.*)$")
_RE_PROMPT_EVAL = re.compile(
    r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*"
    r"\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\)"
)
_RE_EVAL = re.compile(
    r"^eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens\s*"
    r"\(\s*([\d.]+)\s*ms per token,\s*([\d.]+)\s*tokens per second\)"
)
_RE_TOTAL = re.compile(r"total time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens")
_RE_DRAFT = re.compile(
    r"draft acceptance\s*=\s*([\d.]+)\s*\(\s*(\d+)\s*accepted\s*/\s*(\d+)\s*generated\)"
    r".*?mean acceptance length\s*=\s*([\d.]+)"
)
_RE_RELEASE = re.compile(
    r"release:\s*id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*stop processing:\s*"
    r"n_tokens\s*=\s*(\d+),\s*truncated\s*=\s*(\d+)"
)
_RE_CACHE = re.compile(
    r"cache state:\s*(\d+)\s*prompts,\s*([\d.]+)\s*MiB"
    r"(?:\s*\(limits:\s*([\d.]+)\s*MiB)?"
)
_RE_STATS = re.compile(
    r"statistics\s+draft-mtp:.*?#gen drafts\s*=\s*(\d+).*?#acc drafts\s*=\s*(\d+)"
    r".*?#gen tokens\s*=\s*(\d+).*?#acc tokens\s*=\s*(\d+).*?#mean acc len\s*=\s*([\d.]+)"
)
# Argument errors emitted before the rel-time prefix exists.
_RE_BARE_ERROR = re.compile(r"^(error[: ].*|error while handling.*)$", re.IGNORECASE)

# --------------------------------------------------------------------------- #
# Regexes for supervisor*.log lines (wall clock)
# --------------------------------------------------------------------------- #
_SUP_PREFIX = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+\[(\w+)\]\s+(.*)$")
_RE_SUP_EXIT = re.compile(r"llama-server exited code=(-?\d+) after (\d+)\s*s")
_RE_SUP_FASTFAIL = re.compile(r"Fast failure #(\d+)")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Request:
    deployment: str
    run: int
    task: int
    prompt_tokens: int | None = None
    prompt_eval_ms: float | None = None
    prefill_tps: float | None = None
    gen_tokens: int | None = None
    eval_ms: float | None = None
    decode_tps: float | None = None
    total_ms: float | None = None
    total_tokens: int | None = None
    accepted: int | None = None
    generated: int | None = None
    accept_len: float | None = None
    truncated: bool = False

    @property
    def complete(self) -> bool:
        return self.total_ms is not None or self.gen_tokens is not None


@dataclass
class Deployment:
    name: str
    requests: list[Request] = field(default_factory=list)
    runs: int = 0
    files: list[str] = field(default_factory=list)
    model: str | None = None
    gpu: str | None = None
    threads: int | None = None
    n_ctx: int | None = None
    peak_cache_mib: float = 0.0
    peak_cache_prompts: int = 0
    cache_limit_mib: float = 0.0
    # cumulative speculative stats from end-of-run "statistics draft-mtp" lines
    stats_gen_drafts: int = 0
    stats_acc_drafts: int = 0
    stats_gen_tokens: int = 0
    stats_acc_tokens: int = 0
    errors: Counter = field(default_factory=Counter)
    warnings: Counter = field(default_factory=Counter)


@dataclass
class Supervisor:
    name: str
    file: str
    starts: int = 0
    clean_stops: int = 0
    launches: int = 0
    exits: list[tuple[int, int]] = field(default_factory=list)  # (code, seconds)
    fast_failures: int = 0
    first_ts: str | None = None
    last_ts: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def deployment_of(path: Path) -> str:
    return "copilot" if "copilot" in path.name.lower() else "default"


def read_lines(path: Path):
    """Yield logical log lines, handling both formats these logs appear in:

      * UTF-8 (default supervisor): one record per physical line.
      * UTF-16-LE-with-BOM (copilot supervisor): PS7 captures native stderr as
        error records, prefixes each with "<exe> : ", and HARD-WRAPS long lines
        at the console width. A physical line that doesn't begin a new record
        (reltime / supervisor timestamp / bare error) is a wrap continuation and
        is rejoined to the record it belongs to.
    """
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    elif raw[:3] == b"\xef\xbb\xbf":
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")

    buf: str | None = None
    for physical in text.splitlines():
        line = _EXE_PREFIX.sub("", physical.rstrip())
        starts_record = (
            _SRV_PREFIX.match(line)
            or _SUP_PREFIX.match(line)
            or _RE_BARE_ERROR.match(line.strip())
        )
        if starts_record:
            if buf is not None:
                yield buf
            buf = line
        elif buf is None:
            yield line  # orphan continuation with no owner
        else:
            buf = buf + " " + line.strip()
    if buf is not None:
        yield buf


def normalize_error(msg: str) -> str:
    """Collapse run-specific bits so identical errors group together."""
    msg = re.sub(r"[A-Za-z]:\\[^\s'\"]+", "<path>", msg)  # windows paths
    msg = re.sub(r"\b\d+\b", "N", msg)
    return msg.strip()


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def stat_block(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "min": min(values),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
    }


def human_dur(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_server_log(path: Path, dep: Deployment) -> None:
    dep.files.append(str(path))
    run = 0
    seen_first = False
    last_rt: tuple[int, int, int, int] | None = None
    # key requests within a file by (run, task); a fresh load resets task ids
    reqs: dict[tuple[int, int], Request] = {}

    def get_req(run_idx: int, task: int) -> Request:
        key = (run_idx, task)
        r = reqs.get(key)
        if r is None:
            r = Request(deployment=dep.name, run=run_idx, task=task)
            reqs[key] = r
            dep.requests.append(r)
        return r

    for raw in read_lines(path):
        m = _SRV_PREFIX.match(raw)
        if not m:
            if _RE_BARE_ERROR.match(raw.strip()):
                dep.errors[normalize_error(raw.strip())] += 1
            continue
        rt = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
        level, msg = m.group(5), m.group(6)

        # New server run: first prefixed line, or the elapsed clock resetting.
        if not seen_first:
            seen_first = True
            dep.runs += 1
        elif last_rt is not None and rt < last_rt:
            run += 1
            dep.runs += 1
        last_rt = rt

        if level == "E":
            dep.errors[normalize_error(msg)] += 1
        elif level == "W":
            dep.warnings[normalize_error(msg)] += 1

        # startup metadata (keep the latest run's values)
        mm = _RE_MODEL.search(msg)
        if mm:
            dep.model = mm.group(1)
        mm = _RE_GPU.search(msg)
        if mm:
            dep.gpu = mm.group(1)
        mm = _RE_THREADS.search(msg)
        if mm:
            dep.threads = int(mm.group(1))
        mm = _RE_NCTX.search(msg)
        if mm:
            dep.n_ctx = int(mm.group(1))

        mm = _RE_CACHE.search(msg)
        if mm:
            dep.peak_cache_prompts = max(dep.peak_cache_prompts, int(mm.group(1)))
            dep.peak_cache_mib = max(dep.peak_cache_mib, float(mm.group(2)))
            if mm.group(3):
                dep.cache_limit_mib = float(mm.group(3))
            continue

        mm = _RE_STATS.search(msg)
        if mm:
            dep.stats_gen_drafts += int(mm.group(1))
            dep.stats_acc_drafts += int(mm.group(2))
            dep.stats_gen_tokens += int(mm.group(3))
            dep.stats_acc_tokens += int(mm.group(4))
            continue

        mm = _RE_RELEASE.search(msg)
        if mm:
            r = get_req(run, int(mm.group(2)))
            r.truncated = mm.group(4) != "0"
            continue

        mm = _RE_TIMING.search(msg)
        if not mm:
            continue
        task = int(mm.group(2))
        rest = mm.group(3)
        r = get_req(run, task)

        pe = _RE_PROMPT_EVAL.search(rest)
        if pe:
            r.prompt_eval_ms = float(pe.group(1))
            r.prompt_tokens = int(pe.group(2))
            r.prefill_tps = float(pe.group(4))
            continue
        ev = _RE_EVAL.search(rest)
        if ev:
            r.eval_ms = float(ev.group(1))
            r.gen_tokens = int(ev.group(2))
            r.decode_tps = float(ev.group(4))
            continue
        tt = _RE_TOTAL.search(rest)
        if tt:
            r.total_ms = float(tt.group(1))
            r.total_tokens = int(tt.group(2))
            continue
        dr = _RE_DRAFT.search(rest)
        if dr:
            r.accept_len = float(dr.group(4))
            r.accepted = int(dr.group(2))
            r.generated = int(dr.group(3))
            continue


def parse_supervisor_log(path: Path) -> Supervisor:
    sup = Supervisor(name=deployment_of(path), file=str(path))
    for raw in read_lines(path):
        m = _SUP_PREFIX.match(raw)
        if not m:
            continue
        ts, _level, msg = m.group(1), m.group(2), m.group(3)
        sup.first_ts = sup.first_ts or ts
        sup.last_ts = ts
        if "Supervisor starting" in msg:
            sup.starts += 1
        elif "Supervisor stopped" in msg:
            sup.clean_stops += 1
        elif "Launching llama-server" in msg:
            sup.launches += 1
        em = _RE_SUP_EXIT.search(msg)
        if em:
            sup.exits.append((int(em.group(1)), int(em.group(2))))
        if _RE_SUP_FASTFAIL.search(msg):
            sup.fast_failures += 1
    return sup


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #
def discover(log_dir: Path) -> tuple[list[Path], list[Path]]:
    server, supervisor = [], []
    for p in sorted(log_dir.glob("*.log")):
        if p.name.startswith("supervisor"):
            supervisor.append(p)
        elif p.name.startswith("llama-server"):
            server.append(p)
    return server, supervisor


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def build_report(deps: dict[str, Deployment], sups: list[Supervisor]) -> dict:
    out: dict = {"deployments": {}, "supervisors": []}
    for name, dep in deps.items():
        complete = [r for r in dep.requests if r.complete]
        prompt_tokens = sum(r.prompt_tokens or 0 for r in complete)
        gen_tokens = sum(r.gen_tokens or 0 for r in complete)
        acc = sum(r.accepted or 0 for r in complete)
        gen_d = sum(r.generated or 0 for r in complete)
        out["deployments"][name] = {
            "files": dep.files,
            "runs": dep.runs,
            "model": dep.model,
            "gpu": dep.gpu,
            "threads": dep.threads,
            "n_ctx": dep.n_ctx,
            "requests": len(complete),
            "truncated_requests": sum(1 for r in complete if r.truncated),
            "prompt_tokens_total": prompt_tokens,
            "generated_tokens_total": gen_tokens,
            "tokens_total": prompt_tokens + gen_tokens,
            "max_prompt_tokens": max((r.prompt_tokens or 0 for r in complete), default=0),
            "max_generated_tokens": max((r.gen_tokens or 0 for r in complete), default=0),
            "decode_tps": stat_block(
                [r.decode_tps for r in complete
                 if r.decode_tps and r.decode_tps < _TPS_SENTINEL]
            ),
            "prefill_tps": stat_block(
                [r.prefill_tps for r in complete
                 if r.prefill_tps and r.prefill_tps < _TPS_SENTINEL]
            ),
            "total_ms": stat_block([r.total_ms for r in complete if r.total_ms]),
            "spec_decode": {
                # pooled = sum(accepted)/sum(generated), token-weighted across
                # requests; NOT the mean of per-request acceptance rates.
                "pooled_acceptance": (acc / gen_d) if gen_d else None,
                "accepted": acc,
                "generated": gen_d,
                "cumulative_acc_rate": (
                    dep.stats_acc_tokens / dep.stats_gen_tokens
                    if dep.stats_gen_tokens
                    else None
                ),
                "cumulative_gen_tokens": dep.stats_gen_tokens,
                "cumulative_acc_tokens": dep.stats_acc_tokens,
            },
            "peak_cache_mib": dep.peak_cache_mib,
            "peak_cache_prompts": dep.peak_cache_prompts,
            "cache_limit_mib": dep.cache_limit_mib,
            "errors": dep.errors.most_common(),
            "warnings": dep.warnings.most_common(),
        }
    for sup in sups:
        crashes = [c for c, _ in sup.exits if c != 0]
        out["supervisors"].append(
            {
                "deployment": sup.name,
                "file": sup.file,
                "window": [sup.first_ts, sup.last_ts],
                "starts": sup.starts,
                "launches": sup.launches,
                "clean_stops": sup.clean_stops,
                "fast_failures": sup.fast_failures,
                "exits": len(sup.exits),
                "crashes": len(crashes),
                "exit_codes": Counter(c for c, _ in sup.exits).most_common(),
                "total_runtime_s": sum(s for _, s in sup.exits),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# ASCII / box-drawing widgets
# --------------------------------------------------------------------------- #
GLYPHS_UNICODE = {
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│", "full": "█", "empty": "░",
}
GLYPHS_ASCII = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "h": "-", "v": "|", "full": "#", "empty": ".",
}

CARD_W = 58  # total width of a card, borders included


def fmt_n(n) -> str:
    """Thousands-separated integer, or '-' for None."""
    if n is None:
        return "-"
    return f"{int(round(n)):,}"


def visible_len(s: str) -> int:
    return len(s)


def hbar(frac: float, width: int, g: dict) -> str:
    frac = 0.0 if frac is None else max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return g["full"] * filled + g["empty"] * (width - filled)


def card(title: str, lines: list[str], g: dict, width: int = CARD_W) -> list[str]:
    """Render a titled box around already-formatted body lines."""
    inner = width - 4  # space between the "| " and " |"
    out = []
    head = f"{g['tl']}{g['h']} {title} "
    out.append(head + g["h"] * (width - visible_len(head) - 1) + g["tr"])
    for ln in lines:
        if ln == "<hr>":
            ln = g["h"] * inner
        ln = ln[:inner]
        out.append(f"{g['v']} {ln}{' ' * (inner - visible_len(ln))} {g['v']}")
    out.append(g["bl"] + g["h"] * (width - 2) + g["br"])
    return out


def kv(label: str, value: str, width: int = CARD_W - 4) -> str:
    """Left label, right-aligned value, on one card line."""
    pad = width - visible_len(label) - visible_len(value)
    return label + " " * max(1, pad) + value


def banner(title: str, totals: list[tuple[str, str]], g: dict) -> list[str]:
    lines = []
    for i in range(0, len(totals), 2):
        chunk = totals[i:i + 2]
        cells = [f"{k} {v}" for k, v in chunk]
        lines.append("   ".join(cells))
    return card(title, lines, g, width=CARD_W)


def rate_rows(rows: list[tuple[str, dict | None]]) -> list[str]:
    """Build a p50/p90/p99/max table; emphasises p90 per the user's ask."""
    lw = 13  # label column width
    header = f"{'':<{lw}}{'p50':>9}{'p90':>9}{'p99':>9}{'max':>9}"
    out = [header, "<hr>"]
    for label, blk in rows:
        if not blk:
            out.append(f"{label:<{lw}}{'(no data)':>36}")
            continue
        out.append(
            f"{label:<{lw}}{blk['p50']:>9.1f}{blk['p90']:>9.1f}"
            f"{blk['p99']:>9.1f}{blk['max']:>9.1f}"
        )
    return out


def print_report(rep: dict, top: int, deps: dict[str, Deployment], ascii_only: bool) -> None:
    g = GLYPHS_ASCII if ascii_only else GLYPHS_UNICODE
    out: list[str] = []

    # ---- top banner: grand totals across all deployments ----
    grand_tok = sum(d["tokens_total"] for d in rep["deployments"].values())
    grand_req = sum(d["requests"] for d in rep["deployments"].values())
    grand_sessions = sum(s["starts"] for s in rep["supervisors"])
    grand_crashes = sum(s["crashes"] for s in rep["supervisors"])
    out += banner(
        "LLAMA-SERVER USAGE",
        [
            ("tokens", fmt_n(grand_tok)),
            ("requests", fmt_n(grand_req)),
            ("sessions", fmt_n(grand_sessions)),
            ("crashes", fmt_n(grand_crashes)),
        ],
        g,
    )
    out.append("")

    for name, d in rep["deployments"].items():
        out.append(f"  deployment: {name}   "
                   f"[{d['model'] or '?'} on {d['gpu'] or '?'}]")
        out.append("")

        # ---- TOKENS card ----
        total = d["tokens_total"] or 1
        pf = d["prompt_tokens_total"] / total
        of = d["generated_tokens_total"] / total
        tok_lines = [
            kv("total tokens", fmt_n(d["tokens_total"])),
            "<hr>",
            kv(f"prefill {hbar(pf, 16, g)}", f"{fmt_n(d['prompt_tokens_total'])} ({pf*100:.0f}%)"),
            kv(f"output  {hbar(of, 16, g)}", f"{fmt_n(d['generated_tokens_total'])} ({of*100:.0f}%)"),
            "<hr>",
            kv("largest prefill", fmt_n(d["max_prompt_tokens"])),
            kv("largest generation", fmt_n(d["max_generated_tokens"])),
        ]
        out += card("TOKENS", tok_lines, g)

        # ---- THROUGHPUT card (rates at p90) ----
        out += card(
            "RATES",
            rate_rows([
                ("decode t/s", d["decode_tps"]),
                ("prefill t/s", d["prefill_tps"]),
                ("latency ms", d["total_ms"]),
            ]),
            g,
        )

        # ---- CACHE card ----
        lim = d["cache_limit_mib"]
        util = (d["peak_cache_mib"] / lim) if lim else None
        cache_lines = []
        if util is not None:
            cache_lines.append(kv(f"usage  {hbar(util, 16, g)}", f"{util*100:.1f}%"))
            cache_lines.append(kv("peak", f"{fmt_n(d['peak_cache_mib'])} / {fmt_n(lim)} MiB"))
        else:
            cache_lines.append(kv("peak usage", f"{fmt_n(d['peak_cache_mib'])} MiB"))
        cache_lines.append(kv("cached prompts (peak)", fmt_n(d["peak_cache_prompts"]))) 
        out += card("PROMPT CACHE", cache_lines, g)

        # ---- ACTIVITY card ----
        sd = d["spec_decode"]
        spec = (f"{sd['pooled_acceptance']*100:.1f}%"
                if sd["pooled_acceptance"] is not None else "-")
        act_lines = [
            kv("server runs", fmt_n(d["runs"])),
            kv("requests", fmt_n(d["requests"])),
            kv("truncated", fmt_n(d["truncated_requests"])),
            kv("spec-decode accept (pooled)", spec),
        ]
        if sd["cumulative_acc_rate"] is not None:
            act_lines.append(
                kv("spec-decode (cumulative)",
                   f"{sd['cumulative_acc_rate']*100:.1f}%  "
                   f"{fmt_n(sd['cumulative_acc_tokens'])}/{fmt_n(sd['cumulative_gen_tokens'])}")
            )
        out += card("ACTIVITY", act_lines, g)

        # ---- issues (errors / warnings), compact ----
        if d["errors"] or d["warnings"]:
            issue_lines = []
            if d["errors"]:
                issue_lines.append(kv("errors", fmt_n(sum(c for _, c in d["errors"]))))
                for msg, c in d["errors"][:4]:
                    issue_lines.append(f"  {c:>3}x {msg[:44]}")
            if d["warnings"]:
                issue_lines.append(kv("warnings", fmt_n(sum(c for _, c in d["warnings"]))))
                for msg, c in d["warnings"][:3]:
                    issue_lines.append(f"  {c:>3}x {msg[:44]}")
            out += card("ISSUES", issue_lines, g)

        # ---- optional: slowest requests ----
        if top:
            dep = deps[name]
            complete = [r for r in dep.requests if r.complete]
            slow = sorted(complete, key=lambda r: r.total_ms or 0, reverse=True)[:top]
            if slow:
                rows = [f"{'ms':>8}{'prompt':>8}{'gen':>7}{'dec t/s':>9}", "<hr>"]
                for r in slow:
                    rows.append(
                        f"{(r.total_ms or 0):>8.0f}{(r.prompt_tokens or 0):>8}"
                        f"{(r.gen_tokens or 0):>7}{(r.decode_tps or 0):>9.1f}"
                    )
                out += card(f"SLOWEST {len(slow)} REQUESTS", rows, g)

        out.append("")

    # ---- lifecycle cards from supervisor logs ----
    for s in rep["supervisors"]:
        life = [
            kv("window", f"{(s['window'][0] or '?')[:10]} .. {(s['window'][1] or '?')[:10]}"),
            "<hr>",
            kv("sessions (supervisor starts)", fmt_n(s["starts"])),
            kv("process launches", fmt_n(s["launches"])),
            kv("clean stops", fmt_n(s["clean_stops"])),
            kv("crashes (exit!=0)", fmt_n(s["crashes"])),
            kv("fast failures", fmt_n(s["fast_failures"])),
        ]
        if s["total_runtime_s"]:
            life.append(kv("measured runtime", human_dur(s["total_runtime_s"])))
        if s["exit_codes"]:
            life.append("<hr>")
            for c, n in s["exit_codes"]:
                life.append(kv(f"exit code {c}", f"{n}x"))
        out += card(f"LIFECYCLE / {s['deployment']}", life, g)
        out.append("")

    out += [
        "notes:",
        "  prefill   = prompt tokens processed per turn; cached prefix is skipped,",
        "              so this is prefill compute, not full conversation size.",
        "  spec-decode acceptance is pooled: sum(accepted) / sum(generated).",
        "  rate percentiles exclude llama.cpp's 1e6 tok/s sentinel (sub-token evals).",
    ]
    sys.stdout.write("\n".join(out) + "\n")


def write_csv(deps: dict[str, Deployment], path: Path) -> int:
    import csv

    cols = [
        "deployment", "run", "task", "prompt_tokens", "prompt_eval_ms",
        "prefill_tps", "gen_tokens", "eval_ms", "decode_tps", "total_ms",
        "total_tokens", "accepted", "generated", "accept_len", "truncated",
    ]
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for dep in deps.values():
            for r in dep.requests:
                if r.complete:
                    w.writerow({k: getattr(r, k) for k in cols})
                    n += 1
    return n


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Parse usage statistics from llama-server / supervisor logs.",
    )
    ap.add_argument("files", nargs="*", type=Path, help="explicit log files")
    ap.add_argument("--log-dir", type=Path, default=Path(DEFAULT_LOG_DIR),
                    help=f"directory to scan (default: {DEFAULT_LOG_DIR})")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("--csv", type=Path, metavar="FILE",
                    help="write one row per request to FILE")
    ap.add_argument("--top", type=int, default=0, metavar="N",
                    help="also list the N slowest requests per deployment")
    ap.add_argument("--ascii", action="store_true",
                    help="use plain ASCII box characters instead of Unicode")
    args = ap.parse_args(argv)

    # Box-drawing glyphs aren't in cp1252; force UTF-8 so a legacy Windows
    # console can't raise UnicodeEncodeError (errors='replace' as a backstop).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if args.files:
        server = [p for p in args.files if not p.name.startswith("supervisor")]
        supervisor = [p for p in args.files if p.name.startswith("supervisor")]
    else:
        if not args.log_dir.is_dir():
            ap.error(f"log dir not found: {args.log_dir}")
        server, supervisor = discover(args.log_dir)

    if not server and not supervisor:
        ap.error("no llama-server*.log or supervisor*.log files found")

    deps: dict[str, Deployment] = {}
    for p in server:
        if not p.exists():
            print(f"warning: {p} not found, skipping", file=sys.stderr)
            continue
        name = deployment_of(p)
        dep = deps.setdefault(name, Deployment(name=name))
        parse_server_log(p, dep)

    sups = [parse_supervisor_log(p) for p in supervisor if p.exists()]

    rep = build_report(deps, sups)

    if args.csv:
        n = write_csv(deps, args.csv)
        print(f"wrote {n} request rows to {args.csv}", file=sys.stderr)

    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print_report(rep, args.top, deps, args.ascii)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
