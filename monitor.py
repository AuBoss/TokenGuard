#!/usr/bin/env python3
"""
Minimax Token Plan 监控脚本
- 每分钟轮询配置的多个 API key 的 token plan 剩余量
- 数据以 NDJSON 格式追加到本地文件
- 终端实时展示所有 key 的当前使用情况和趋势
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from rich.align import Align
from rich.box import HEAVY, ROUNDED, DOUBLE, Box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text


# ---------- 视觉常量 ----------

# emoji 状态图标
ICON_OK = "✅"
ICON_FAIL = "❌"
ICON_PENDING = "⏳"
ICON_KEY = "🔑"
ICON_CLOCK = "🕐"
ICON_FIRE = "🔥"
ICON_PARTY = "🎉"
ICON_WARN = "⚠️"
ICON_BOLT = "⚡"
ICON_OK_CIRCLE = "🟢"
ICON_WARN_CIRCLE = "🟡"
ICON_DANGER_CIRCLE = "🔴"
ICON_NEUTRAL_CIRCLE = "⚪"

# ASCII 横幅
BANNER = r"""
 __   __  ___   _______  ______    _______  _______  _______  __    _  _______  ___
|  |_|  ||   | |       ||    _ |  |       ||       ||       ||  |  | ||   _   ||   |
|       ||   | |       ||   | ||  |   _   ||_     _||   _   ||   |_| ||  |_|  ||   |
|       ||   | |       ||   |_||_ |  | |  |  |   |  |  | |  ||       ||       ||   |
|       ||   | |      _||    __  ||  |_|  |  |   |  |  |_|  ||  _    ||       ||   |___
| ||_|| ||   | |     |_ |   |  | ||       |  |   |  |       || | |   ||   _   ||       |
|_|   |_||___| |_______||___|  |_||_______|  |___|  |_______||_|  |__||__| |__||_______|
"""

# 脉冲指示器状态（每 0.5s 切换一次）
_PULSE_FRAMES = ["●", "○", "●", "○"]


def _level_icon(used_pct: float) -> str:
    """根据使用率返回带 emoji 的状态。"""
    if used_pct < 60:
        return ICON_OK_CIRCLE
    if used_pct < 85:
        return ICON_WARN_CIRCLE
    return ICON_DANGER_CIRCLE


def _level_style(used_pct: float) -> str:
    if used_pct < 60:
        return "bold bright_green"
    if used_pct < 85:
        return "bold bright_yellow"
    return "bold bright_red"


# 用于 Live 模式记录启动时间
import time as _time
_START_TIME = _time.time()


# 8 级 Unicode 块字符，作为自实现的 sparkline
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int = 16) -> str:
    """极简 sparkline：把一组数值映射为 8 级块字符的字符串。"""
    if not values:
        return "·" * width
    sample = values[-width:]
    if len(sample) < width:
        sample = [sample[0]] * (width - len(sample)) + sample
    lo, hi = min(sample), max(sample)
    if hi == lo:
        return "▄" * width
    out = []
    for v in sample:
        idx = int((v - lo) / (hi - lo) * (len(_SPARK_CHARS) - 1) + 0.5)
        out.append(_SPARK_CHARS[max(0, min(len(_SPARK_CHARS) - 1, idx))])
    return "".join(out)


# ---------- 配置加载 ----------

DEFAULT_CONFIG_PATH = Path("config.json")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件 {path} 不存在，请先创建并填入 API keys"
        )
    with path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("api_url", "https://www.minimaxi.com/v1/token_plan/remains")
    cfg.setdefault("poll_interval_seconds", 60)
    cfg.setdefault("request_timeout_seconds", 15)
    cfg.setdefault("data_file", "data/usage.ndjson")
    cfg.setdefault("history_limit", 50000)
    if not cfg.get("keys"):
        raise ValueError("配置文件中 keys 列表为空，请至少配置一个 API key")
    return cfg


# ---------- API 调用与字段抽取 ----------

# Minimax 真实接口 (token_plan/remains) 的字段映射：
# payload = {
#   "model_remains": [
#     {"model_name": "general",
#      "current_interval_total_count": N, "current_interval_usage_count": N,
#      "current_interval_remaining_percent": 93, "current_interval_status": 1,
#      "start_time": ms, "end_time": ms, "remains_time": s,
#      "current_weekly_total_count": N, "current_weekly_usage_count": N,
#      "current_weekly_remaining_percent": 100, "current_weekly_status": 3,
#      "weekly_start_time": ms, "weekly_end_time": ms, "weekly_remains_time": s,
#     },
#     ...
#   ],
#   "base_resp": {"status_code": 0, "status_msg": "success"}
# }


def _ms_to_iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000,
                                      tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OSError, OverflowError):
        return None


def _parse_model_remains(payload: Any) -> list[dict[str, Any]]:
    """把 model_remains 列表解析为结构化的窗口数据。"""
    if not isinstance(payload, dict):
        return []
    raw_models = payload.get("model_remains")
    if not isinstance(raw_models, list):
        return []
    out: list[dict[str, Any]] = []
    for m in raw_models:
        if not isinstance(m, dict):
            continue
        out.append({
            "name": m.get("model_name") or "-",
            "interval": {
                "total": m.get("current_interval_total_count"),
                "used": m.get("current_interval_usage_count"),
                "remaining_percent": m.get("current_interval_remaining_percent"),
                "status": m.get("current_interval_status"),
                "start_time": _ms_to_iso(m.get("start_time")),
                "end_time": _ms_to_iso(m.get("end_time")),
                "remains_time_seconds": m.get("remains_time"),
            },
            "weekly": {
                "total": m.get("current_weekly_total_count"),
                "used": m.get("current_weekly_usage_count"),
                "remaining_percent": m.get("current_weekly_remaining_percent"),
                "status": m.get("current_weekly_status"),
                "start_time": _ms_to_iso(m.get("weekly_start_time")),
                "end_time": _ms_to_iso(m.get("weekly_end_time")),
                "remains_time_seconds": m.get("weekly_remains_time"),
            },
        })
    return out


def _is_success_response(payload: Any) -> bool:
    """检查 base_resp.status_code == 0（Minimax 约定的成功标志）。"""
    if not isinstance(payload, dict):
        return False
    base = payload.get("base_resp")
    if not isinstance(base, dict):
        return True  # 没有 base_resp 也视为成功（旧版接口兼容）
    return base.get("status_code") in (0, None)


def fetch_remains(api_url: str, token: str, timeout: int) -> dict[str, Any]:
    """调用 token_plan/remains 接口并解析成结构化数据。

    返回字段：
      - ok: bool
      - http_status: int
      - models: list[dict]  解析后的模型使用情况
      - error_msg: str | None
      - raw: dict  原始响应
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.get(api_url, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json() if response.content else {}

    if not _is_success_response(payload):
        msg = "未知错误"
        if isinstance(payload, dict):
            base = payload.get("base_resp")
            if isinstance(base, dict):
                msg = str(base.get("status_msg") or msg)
        return {
            "ok": False,
            "http_status": response.status_code,
            "error_msg": msg,
            "models": [],
            "raw": payload,
        }

    models = _parse_model_remains(payload)
    return {
        "ok": True,
        "http_status": response.status_code,
        "models": models,
        "raw": payload,
    }


# ---------- 数据存储（NDJSON 追加） ----------

class UsageStore:
    """按 alias 维护内存中的历史序列 + NDJSON 追加写。

    默认 history_limit=50000（远大于 1440）以保留更长时间的历史；
    设为 None / 0 表示完全不截断，内存中保留全量历史。
    磁盘 NDJSON 文件始终是全量追加，不受 history_limit 影响。
    """

    def __init__(self, path: Path, history_limit: int | None = 50000):
        self.path = path
        self.history_limit = history_limit  # None/0 = 不截断
        self._lock = threading.Lock()
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self._load_history()

    def _load_history(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
            return
        # 启动时读全量 NDJSON（不再做尾部截断），数据全量保留
        with self.path.open("r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        loaded = 0
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._absorb(record, persist=False)
            loaded += 1
        if loaded:
            print(f"[UsageStore] 从 {self.path} 加载 {loaded} 条历史记录"
                  f"（全量保留）", file=__import__("sys").stderr)

    def _absorb(self, record: dict[str, Any], persist: bool) -> None:
        alias = record.get("alias")
        if not alias:
            return
        history = self._history.setdefault(alias, [])
        history.append(record)
        if self.history_limit and len(history) > self.history_limit:
            del history[: len(history) - self.history_limit]
        self._latest[alias] = record
        if persist:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._absorb(record, persist=True)

    def history(self, alias: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(alias, []))

    def latest(self, alias: str) -> dict[str, Any] | None:
        with self._lock:
            return self._latest.get(alias)

    def all_latest(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: v for k, v in self._latest.items()}


# ---------- 终端 UI ----------

def _format_number(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            return f"{value:,.4f}".rstrip("0").rstrip(".")
        return f"{int(value):,}"
    return str(value)


def _format_time(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        # 假设是 unix 时间戳
        try:
            return datetime.fromtimestamp(int(value),
                                          tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, OSError, OverflowError):
            return str(value)
    if isinstance(value, str):
        return value
    return str(value)


def _usage_percent(record: dict[str, Any]) -> float | None:
    """兼容旧字段 (used/total/remaining)。新结构请用 _percent_from_remaining。"""
    used = record.get("used")
    total = record.get("total")
    if isinstance(used, (int, float)) and isinstance(total, (int, float)) and total:
        return max(0.0, min(100.0, used / total * 100))
    remaining = record.get("remaining")
    if (isinstance(remaining, (int, float))
            and isinstance(total, (int, float)) and total):
        return max(0.0, min(100.0, (1 - remaining / total) * 100))
    return None


def _percent_from_remaining(remaining_percent: Any) -> float | None:
    """接口直接返回的是 remaining_percent (剩余百分比)，转成已用百分比供 _bar 使用。"""
    if isinstance(remaining_percent, (int, float)):
        used = 100.0 - float(remaining_percent)
        return max(0.0, min(100.0, used))
    return None


def _bar(percent: float | None, width: int = 24):
    """使用纯文本绘制一个简单的进度条，便于嵌在表格单元格里。"""
    if percent is None:
        return Text("-", style="dim")
    style = "green" if percent < 60 else "yellow" if percent < 85 else "red"
    filled = max(0, min(width, int(round(percent / 100 * width))))
    bar_str = "█" * filled + "░" * (width - filled)
    return Text.assemble(
        (f"{percent:5.1f}% ", f"bold {style}"),
        (bar_str, style),
    )


def _format_usage(window: dict[str, Any]) -> Text:
    """把窗口数据格式化为 "🟢 12.0%" 形式的彩色百分比，含 emoji 状态图标。"""
    rem = window.get("remaining_percent")
    if isinstance(rem, (int, float)):
        used_pct = 100.0 - float(rem)
    else:
        used = window.get("used")
        total = window.get("total")
        if not isinstance(used, (int, float)) or not isinstance(total, (int, float)) or not total:
            return Text(f"{ICON_NEUTRAL_CIRCLE} -", style="dim")
        used_pct = used / total * 100
    used_pct = max(0.0, min(100.0, used_pct))
    icon = _level_icon(used_pct)
    style = _level_style(used_pct)
    return Text.assemble(
        (f"{icon} ", style),
        (f"{used_pct:5.1f}%", style),
    )


class TerminalUI:
    def __init__(self, store: UsageStore, config: dict[str, Any]):
        self.store = store
        self.config = config
        self.console = Console()
        self.start_time = time.time()

    def _summary_panel(self) -> Panel:
        elapsed = int(time.time() - self.start_time)
        interval = self.config.get("poll_interval_seconds", 60)
        latest = self.store.all_latest()
        ok_count = sum(1 for r in latest.values() if r.get("ok"))
        fail_count = len(latest) - ok_count
        total = len(latest)

        # 脉冲指示器（每 0.5s 切换一次）
        pulse_idx = int((time.time() * 2) % len(_PULSE_FRAMES))
        pulse_color = "bright_green" if fail_count == 0 else (
            "bright_red" if fail_count > ok_count else "bright_yellow")
        pulse_char = _PULSE_FRAMES[pulse_idx]

        # 状态摘要（彩色方括号 + 数字）
        status_line = Text()
        status_line.append("  ")
        status_line.append(f"{pulse_char} ", f"bold {pulse_color}")
        status_line.append(f"运行 [bold bright_white]{elapsed}s[/]  ", "dim")
        status_line.append(f"⏱ [bold bright_white]{interval}s[/] 轮询  ", "dim")
        status_line.append(f"{ICON_OK_CIRCLE} ", "bright_green")
        status_line.append(f"正常 [bold bright_green]{ok_count}[/]  ", "dim")
        status_line.append(f"{ICON_DANGER_CIRCLE} " if fail_count else f"{ICON_NEUTRAL_CIRCLE} ",
                          "bright_red" if fail_count else "dim")
        status_line.append(f"异常 [bold {('bright_red' if fail_count else 'grey50')}]{fail_count}[/]",
                           "dim")
        status_line.append(f"  /  共 [bold bright_white]{total}[/] keys", "dim")

        # ASCII banner（渐变色）
        banner_lines = BANNER.rstrip("\n").splitlines()
        banner = Text()
        # 用 cyan→magenta 渐变上色
        banner_colors = ["bright_cyan", "cyan", "deep_sky_blue1", "blue_violet",
                         "magenta", "deep_pink3"]
        for i, line in enumerate(banner_lines):
            color = banner_colors[min(i, len(banner_colors) - 1)]
            banner.append(line, style=f"bold {color}")
            if i < len(banner_lines) - 1:
                banner.append("\n")

        body = Group(
            Align.center(banner),
            status_line,
            Text(),
        )
        # 副标题
        sub = Text()
        sub.append("  🌐 ", "cyan")
        sub.append(self.config["api_url"], "cyan")
        sub.append("    💾 ", "yellow")
        sub.append(self.config["data_file"], "yellow")
        sub.append("    📦 ", "magenta")
        sub.append(f"history={self.config.get('history_limit', 50000)}", "magenta")
        return Panel(
            Group(body, Align.center(sub)),
            border_style="bright_cyan",
            box=DOUBLE,
            padding=(0, 1),
        )

    def _colored_sparkline(self, values: list[float], width: int = 14) -> Text:
        """彩色 sparkline：每个段根据相对高度使用不同色阶（绿→青→黄→红）。"""
        if not values:
            return Text("·" * width, style="dim")
        sample = values[-width:]
        if len(sample) < width:
            sample = [sample[0]] * (width - len(sample)) + sample
        lo, hi = min(sample), max(sample)
        if hi == lo:
            return Text("▄" * width, style="cyan")
        palette = ["bright_green", "cyan", "yellow", "bright_red"]
        out = Text()
        for v in sample:
            idx = int((v - lo) / (hi - lo) * (len(_SPARK_CHARS) - 1) + 0.5)
            idx = max(0, min(len(_SPARK_CHARS) - 1, idx))
            color_idx = min(len(palette) - 1,
                            idx * len(palette) // len(_SPARK_CHARS))
            out.append(_SPARK_CHARS[idx], style=palette[color_idx])
        return out

    def _build_alias_panels(self) -> Group:
        """为每个 alias 构建独立的彩色 Panel。"""
        keys = [k for k in self.config.get("keys", []) if k.get("enabled", True)]
        panels: list[Panel] = []
        for key_idx, key in enumerate(keys):
            alias = key["alias"]
            latest = self.store.latest(alias) or {}
            record = latest
            if not record.get("ok") and self.store.history(alias):
                for r in reversed(self.store.history(alias)):
                    if r.get("ok"):
                        record = r
                        break
            ok = latest.get("ok")
            status_text = (
                Text.assemble((f" {ICON_OK} ", "bold bright_green"),
                              ("ONLINE", "bold bright_green"))
                if ok else
                (Text.assemble((f" {ICON_FAIL} ", "bold bright_red"),
                               ("OFFLINE", "bold bright_red"))
                 if "ok" in latest else
                 Text.assemble((f" {ICON_PENDING} ", "dim"),
                               ("WAITING", "dim"))))
            ts = latest.get("timestamp", "-")
            ts_short = (str(ts).replace("T", " ").replace("Z", "") or "-")
            tags = key.get("tags") or []
            tags_str = " · ".join(tags) if tags else ""

            header_text = Text()
            header_text.append(f" {ICON_KEY} ", "bold bright_cyan")
            header_text.append(f" {alias} ", "bold bright_white on grey15")
            if tags_str:
                header_text.append(f"  {tags_str} ", "dim")
            header_text.append("  ")
            header_text.append(status_text)
            header_text.append(f"   {ICON_CLOCK} {ts_short}", "dim")

            models = record.get("models") or []
            if not models:
                err = latest.get("error") or latest.get("error_msg") or "尚无数据"
                body = Text(f"  {ICON_WARN}  {err}", style="yellow")
                border_color = "yellow" if "ok" in latest and not ok else "grey50"
                panels.append(Panel(
                    Align.left(body),
                    title=header_text, title_align="left",
                    border_style=border_color, box=ROUNDED,
                    padding=(0, 1),
                ))
                continue

            history_by_model: dict[str, list[float]] = {}
            for r in self.store.history(alias):
                if not r.get("ok"):
                    continue
                for m in (r.get("models") or []):
                    name = m.get("name")
                    used = (m.get("interval") or {}).get("used")
                    if name and isinstance(used, (int, float)):
                        history_by_model.setdefault(name, []).append(used)

            max_used_pct = 0.0
            for m in models:
                rem = (m.get("weekly") or {}).get("remaining_percent")
                if isinstance(rem, (int, float)):
                    max_used_pct = max(max_used_pct, 100.0 - float(rem))

            sub = Table(
                expand=True, show_header=True, show_lines=False,
                header_style="bold magenta", box=None,
                pad_edge=False, padding=(0, 1),
            )
            sub.add_column("Model", style="bold bright_cyan", no_wrap=True)
            sub.add_column("5h 使用量", justify="left", header_style="bold", min_width=12)
            sub.add_column("5h 重置", style="dim", no_wrap=True)
            sub.add_column("周 使用量", justify="left", header_style="bold", min_width=12)
            sub.add_column("周 重置", style="dim", no_wrap=True)
            sub.add_column("趋势", justify="left")

            for m in models:
                name = m.get("name", "-")
                interval = m.get("interval") or {}
                weekly = m.get("weekly") or {}
                used_series = history_by_model.get(name, [])
                spark = self._colored_sparkline(used_series, width=12)
                sub.add_row(
                    f" {ICON_BOLT} {name}",
                    _format_usage(interval),
                    (interval.get("end_time") or "-").split(" ")[0],
                    _format_usage(weekly),
                    (weekly.get("end_time") or "-").split(" ")[0],
                    spark,
                )

            if max_used_pct < 60:
                border = "bright_green"
            elif max_used_pct < 85:
                border = "bright_yellow"
            else:
                border = "bright_red"

            panels.append(Panel(
                sub,
                title=header_text, title_align="left",
                border_style=border, box=ROUNDED,
                padding=(0, 0),
            ))

        return Group(*panels) if panels else Text("无启用 key", style="dim")

    def _trend_chart_panel(self) -> Panel:
        """简易 ASCII 折线图：每个 (alias, model) 一条线（5h 已用值随时间变化）。"""
        series: dict[str, list[float]] = {}
        for key in self.config.get("keys", []):
            if not key.get("enabled", True):
                continue
            alias = key["alias"]
            by_model: dict[str, list[float]] = {}
            for r in self.store.history(alias):
                if not r.get("ok"):
                    continue
                for m in (r.get("models") or []):
                    name = m.get("name")
                    used = (m.get("interval") or {}).get("used")
                    if name and isinstance(used, (int, float)):
                        by_model.setdefault(name, []).append(used)
            for model_name, values in by_model.items():
                label = f"{alias}/{model_name}"
                series[label] = values[-60:]

        if not series:
            return Panel(
                Text(f"  {ICON_PENDING}  暂无趋势数据，等待更多轮询…", style="dim"),
                title=Text.assemble((" 📈 ", "bold"), ("[bold]用量趋势[/bold]", "bold")),
                title_align="left",
                border_style="bright_cyan",
                box=ROUNDED,
                padding=(0, 0),
            )

        # 全局 y 范围
        all_vals = [v for vs in series.values() for v in vs]
        lo, hi = min(all_vals), max(all_vals)
        if hi == lo:
            hi = lo + 1
        height = 8
        # 宽度 = 该 panel 最多显示的点数
        max_len = max((len(vs) for vs in series.values()), default=20)
        width = max(20, min(80, max_len))

        # 颜色分配（按 series 出现顺序取 palette）
        palette = ["bright_cyan", "bright_yellow", "bright_magenta",
                   "bright_green", "bright_red", "bright_blue", "cyan", "magenta"]
        label_colors: dict[str, str] = {}
        for i, label in enumerate(series.keys()):
            label_colors[label] = palette[i % len(palette)]

        # 画布：每行一个 y 阈值
        chart_text = Text()
        for y in range(height, -1, -1):
            threshold = lo + (hi - lo) * y / height
            chart_text.append(f" {threshold:>6.0f} ┤ ", "dim")
            # 当前阈值之上的点
            for col in range(width):
                # 找出该列所有 series 中，值最接近或超过阈值的那个（最高的）
                chosen_char: str | None = None
                chosen_color: str | None = None
                for label, values in series.items():
                    if col < len(values):
                        v = values[col]
                        if v >= threshold:
                            idx = int(
                                (v - lo) / (hi - lo) * (len(_SPARK_CHARS) - 1) + 0.5
                            )
                            idx = max(0, min(len(_SPARK_CHARS) - 1, idx))
                            if chosen_char is None or _SPARK_CHARS[idx] > chosen_char:
                                chosen_char = _SPARK_CHARS[idx]
                                chosen_color = label_colors[label]
                if chosen_char is not None:
                    chart_text.append(chosen_char, chosen_color)
                else:
                    chart_text.append(" ")
            chart_text.append("\n")

        # x 轴
        chart_text.append(" " * 8, "dim")
        chart_text.append("└" + "─" * width, "dim")
        chart_text.append("\n")

        # 底部图例：每个 series 一行
        legend = Text()
        legend.append("  ",)
        for label, color in label_colors.items():
            legend.append("  ",)
            legend.append("■ ", color)
            legend.append(f"{label}  ", color)
        legend.append("\n")
        chart_text.append_text(legend)
        chart_text.append(
            f"  最近 {max_len} 个采样点 · y 轴 = 5h 窗口已用值", "dim"
        )

        return Panel(
            chart_text,
            title=Text.assemble(
                (" 📈 ", "bold"),
                ("[bold]用量趋势 (5h 已用)[/bold]", "bold"),
            ),
            title_align="left",
            border_style="bright_cyan",
            box=ROUNDED,
            padding=(0, 0),
        )

    def _recent_events(self) -> Panel:
        events: list[dict[str, Any]] = []
        for alias in (k["alias"] for k in self.config.get("keys", [])
                      if k.get("enabled", True)):
            for r in self.store.history(alias)[-2:]:
                events.append(r)
        events.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        lines: list[Text] = []
        for r in events[:5]:
            line = Text()
            line.append(f"  {ICON_CLOCK} ", "bright_cyan")
            line.append(f"{(r.get('timestamp') or '')[-8:]} ", "dim")
            line.append(f" {r.get('alias', '?'):<10} ", "bold bright_white")
            if r.get("ok"):
                models = r.get("models") or []
                if not models:
                    line.append(f" {ICON_OK} ", "bold bright_green")
                    line.append("无模型数据", "dim")
                else:
                    line.append(f" {ICON_OK} ", "bold bright_green")
                    parts: list[Text] = []
                    for m in models[:3]:
                        i = m.get("interval") or {}
                        w = m.get("weekly") or {}
                        i_pct = i.get("remaining_percent")
                        w_pct = w.get("remaining_percent")
                        # 给百分比染色
                        if isinstance(i_pct, (int, float)):
                            i_used = 100 - i_pct
                            i_style = _level_style(i_used)
                            i_block = Text.assemble(
                                ("5h=", "dim"),
                                (f"{i_pct:g}%", i_style),
                            )
                        else:
                            i_block = Text("5h=-", "dim")
                        if isinstance(w_pct, (int, float)):
                            w_used = 100 - w_pct
                            w_style = _level_style(w_used)
                            w_block = Text.assemble(
                                ("week=", "dim"),
                                (f"{w_pct:g}%", w_style),
                            )
                        else:
                            w_block = Text("week=-", "dim")
                        part = Text()
                        part.append(f"{m.get('name', '?')}", "bold bright_cyan")
                        part.append(":", "dim")
                        part.append_text(i_block)
                        part.append(" ", "dim")
                        part.append_text(w_block)
                        parts.append(part)
                    sep = Text(" │ ", "dim")
                    for idx, p in enumerate(parts):
                        if idx > 0:
                            line.append_text(sep)
                        line.append_text(p)
            else:
                line.append(f" {ICON_FAIL} ", "bold bright_red")
                err = r.get("error") or r.get("error_msg") or "unknown"
                line.append(str(err)[:50], "bright_red")
            lines.append(line)
        body = lines or [Text(f"  {ICON_PENDING}  暂无数据，等待第一次轮询…", "dim")]
        return Panel(
            Group(*body),
            title=Text.assemble((" 📡 ", "bold"), ("[bold]最近事件[/bold]", "bold")),
            title_align="left",
            border_style="bright_blue",
            box=ROUNDED,
            padding=(0, 0),
        )

    def render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=11),
            Layout(name="body", ratio=1, minimum_size=10),
            Layout(name="trend", size=12),
            Layout(name="footer", size=8),
        )
        layout["header"].update(self._summary_panel())
        layout["body"].update(self._build_alias_panels())
        layout["trend"].update(self._trend_chart_panel())
        layout["footer"].update(self._recent_events())
        return layout


# ---------- 调度器 ----------

class Monitor:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.store = UsageStore(
            Path(config["data_file"]),
            history_limit=config.get("history_limit", 50000),
        )
        self.ui = TerminalUI(self.store, config)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.console = Console()
        self._poll_inflight = False

    def stop(self, *_: Any) -> None:
        self.console.print("\n[yellow]收到停止信号，正在退出…[/yellow]")
        self._stop_event.set()

    def _poll_once(self) -> None:
        with self._lock:
            if self._poll_inflight:
                return
            self._poll_inflight = True
        try:
            for key in self.config.get("keys", []):
                if not key.get("enabled", True):
                    continue
                alias = key["alias"]
                token = key.get("token", "")
                if not token or token.startswith("REPLACE_WITH"):
                    self.store.append({
                        "alias": alias,
                        "ok": False,
                        "error": "token 未配置或仍为占位符",
                        "timestamp": datetime.now(timezone.utc)
                        .isoformat(timespec="seconds"),
                    })
                    continue
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                try:
                    parsed = fetch_remains(
                        self.config["api_url"],
                        token,
                        self.config.get("request_timeout_seconds", 15),
                    )
                    record = {
                        "alias": alias,
                        "ok": True,
                        "timestamp": ts,
                        "tags": key.get("tags", []),
                        **parsed,
                    }
                except requests.RequestException as exc:
                    record = {
                        "alias": alias,
                        "ok": False,
                        "timestamp": ts,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                except (ValueError, json.JSONDecodeError) as exc:
                    record = {
                        "alias": alias,
                        "ok": False,
                        "timestamp": ts,
                        "error": f"JSON 解析失败: {exc}",
                    }
                self.store.append(record)
        finally:
            with self._lock:
                self._poll_inflight = False

    def _scheduler_loop(self) -> None:
        interval = self.config.get("poll_interval_seconds", 60)
        # 启动后立即拉取一次
        self._poll_once()
        next_run = time.time() + interval
        while not self._stop_event.is_set():
            wait = next_run - time.time()
            if wait > 0:
                if self._stop_event.wait(timeout=min(wait, 1.0)):
                    return
                continue
            self._poll_once()
            next_run += interval

    def run(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)
        scheduler = threading.Thread(target=self._scheduler_loop,
                                     name="scheduler", daemon=True)
        scheduler.start()
        self.console.print("[bold green]Minimax 监控已启动[/bold green]"
                           "  (Ctrl+C 退出)")
        try:
            with Live(self.ui.render(), console=self.console,
                      refresh_per_second=2, screen=True) as live:
                while not self._stop_event.is_set():
                    live.update(self.ui.render())
                    if self._stop_event.wait(timeout=0.5):
                        break
        finally:
            self._stop_event.set()
            scheduler.join(timeout=3)
            self.console.print("[bold]再见 👋[/bold]")


def main() -> int:
    try:
        config = load_config()
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        Console().print(f"[bold red]配置错误:[/bold red] {exc}")
        return 2
    Monitor(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
