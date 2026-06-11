#!/usr/bin/env python3
"""
Minimax 监控历史数据查看器
- 独立运行，从 NDJSON 文件读取历史数据
- 不发起任何网络请求
- 适合在监控进程重启或离线时回看趋势

用法：
    python viewer.py                        # 显示最近 50 条
    python viewer.py --alias primary        # 只看某个 key
    python viewer.py --last 200             # 看最近 200 条
    python viewer.py --from 2026-06-09      # 从某天开始
    python viewer.py --summary              # 统计摘要
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def filter_records(records: list[dict[str, Any]],
                   alias: str | None,
                   since: datetime | None) -> list[dict[str, Any]]:
    out = records
    if alias:
        out = [r for r in out if r.get("alias") == alias]
    if since:
        out = [r for r in out if r.get("timestamp")
               and r["timestamp"] >= since.isoformat()]
    return out


def show_recent(records: list[dict[str, Any]], last: int) -> None:
    console = Console()
    records = records[-last:]
    if not records:
        console.print("[yellow]没有匹配的数据[/yellow]")
        return
    # 展开为 (alias, model, window) 形式的行
    rows: list[dict[str, Any]] = []
    for r in records:
        ts = (r.get("timestamp") or "").replace("T", " ").replace("Z", "")
        alias = r.get("alias", "-")
        if not r.get("ok"):
            rows.append({
                "ts": ts, "alias": alias, "model": "-", "window": "-",
                "status": "FAIL", "used": None, "total": None,
                "remaining_pct": None, "end_time": "-",
                "note": (r.get("error") or r.get("error_msg") or "")[:40],
            })
            continue
        for m in (r.get("models") or []):
            for wkey, wlabel in (("interval", "5h"), ("weekly", "周")):
                w = m.get(wkey) or {}
                rows.append({
                    "ts": ts, "alias": alias, "model": m.get("name", "-"),
                    "window": wlabel, "status": "OK",
                    "used": w.get("used"), "total": w.get("total"),
                    "remaining_pct": w.get("remaining_percent"),
                    "end_time": w.get("end_time") or "-", "note": "",
                })

    if not rows:
        console.print("[yellow]没有匹配的数据[/yellow]")
        return
    table = Table(title=f"最近 {len(rows)} 条 (alias × model × window)",
                  show_lines=False, header_style="bold magenta")
    table.add_column("时间", style="dim", no_wrap=True)
    table.add_column("Alias", style="bold")
    table.add_column("Model", style="cyan")
    table.add_column("Window", style="dim")
    table.add_column("状态", justify="center")
    table.add_column("已用", justify="right")
    table.add_column("总额", justify="right")
    table.add_column("剩余%", justify="right")
    table.add_column("重置时间", style="dim")
    table.add_column("备注")
    for r in rows:
        status = f"[green]{r['status']}[/green]"
        table.add_row(
            r["ts"], r["alias"], r["model"], r["window"], status,
            _fmt(r["used"]), _fmt(r["total"]),
            _fmt(r["remaining_pct"]), r["end_time"], r["note"],
        )
    console.print(table)


def show_summary(records: list[dict[str, Any]]) -> None:
    console = Console()
    grouped: dict[tuple, list[tuple[dict, dict, str]]] = defaultdict(list)
    for r in records:
        alias = r.get("alias")
        if not alias or not r.get("ok"):
            continue
        for m in (r.get("models") or []):
            name = m.get("name", "-")
            for wkey in ("interval", "weekly"):
                grouped[(alias, name, wkey)].append((r, m.get(wkey) or {}, wkey))

    if not grouped:
        console.print("[yellow]没有数据[/yellow]")
        return
    table = Table(title="汇总统计 (alias × model × window)",
                  header_style="bold magenta")
    table.add_column("Alias", style="bold")
    table.add_column("Model", style="cyan")
    table.add_column("Window", style="dim")
    table.add_column("样本", justify="right")
    table.add_column("首次", style="dim")
    table.add_column("最近", style="dim")
    table.add_column("最近已用", justify="right")
    table.add_column("最大已用", justify="right")
    table.add_column("平均剩余%", justify="right")
    table.add_column("最低剩余%", justify="right")

    for (alias, model, wkey), items in sorted(grouped.items()):
        used_vals = [w.get("used") for _, w, _ in items
                     if isinstance(w.get("used"), (int, float))]
        rem_vals = [w.get("remaining_percent") for _, w, _ in items
                    if isinstance(w.get("remaining_percent"), (int, float))]
        avg_rem = statistics.mean(rem_vals) if rem_vals else None
        min_rem = min(rem_vals) if rem_vals else None
        latest_record, latest_w, _ = items[-1]
        table.add_row(
            alias, model, "5h" if wkey == "interval" else "周",
            str(len(items)),
            (items[0][0].get("timestamp") or "")[:19],
            (latest_record.get("timestamp") or "")[:19],
            _fmt(latest_w.get("used")),
            _fmt(max(used_vals) if used_vals else None),
            _fmt(avg_rem),
            _fmt(min_rem),
        )
    console.print(table)


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimax 监控历史查看器")
    parser.add_argument("--data-file", default="data/usage.ndjson")
    parser.add_argument("--alias", help="只查看指定 alias")
    parser.add_argument("--last", type=int, default=50, help="最近 N 条 (默认 50)")
    parser.add_argument("--from", dest="since", help="起始时间，例 2026-06-09 或 2026-06-09T10:00:00")
    parser.add_argument("--summary", action="store_true", help="只显示统计摘要")
    args = parser.parse_args()

    records = load_records(Path(args.data_file))
    since_dt = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since)
        except ValueError:
            try:
                since_dt = datetime.strptime(args.since, "%Y-%m-%d")
            except ValueError:
                Console().print(f"[red]无法解析时间: {args.since}[/red]")
                return 2
    records = filter_records(records, args.alias, since_dt)

    if args.summary:
        show_summary(records)
    else:
        show_recent(records, args.last)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
