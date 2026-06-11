#!/usr/bin/env python3
"""
Minimax 监控的 Flask Web 服务（自包含：启动时自动后台轮询，无需 monitor.py）

特性：
- 启动 Flask 时自动启动后台 poller 线程，每 60s 调用 Minimax API 写入 NDJSON
- 提供 REST API（/api/keys, /api/history, /api/summary, /api/diag）
- 桌面端仪表板 / 和 iPhone 极简版 /mobile
- API 响应精简，只返回必要字段

启动：python app.py
访问：http://localhost:5050
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import gzip
import io

import requests
from flask import Flask, jsonify, make_response, render_template_string, request
from flask_cors import CORS


DEFAULT_DATA_FILE = Path("data/usage.ndjson")
DEFAULT_CONFIG_FILE = Path("config.json")
DEFAULT_KEY_FILE = Path("data/access_key")
MAX_HISTORY = 5000  # 单次返回的最大历史点数
MAX_SPARK_POINTS = 200  # 折线图最大数据点数


# ---------- 访问 key 管理 ----------
import secrets
import string


def _load_or_create_access_key(key_file: Path = DEFAULT_KEY_FILE) -> str:
    """读取已存在的 key；若没有则生成 32 字符 URL-safe 随机 key 并持久化（chmod 600）。"""
    if key_file.exists():
        existing = key_file.read_text(encoding="utf-8").strip()
        if existing and 16 <= len(existing) <= 128:
            return existing
    # 生成新 key
    alphabet = string.ascii_letters + string.digits
    key = "".join(secrets.choice(alphabet) for _ in range(32))
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key + "\n", encoding="utf-8")
    key_file.chmod(0o600)
    return key


# ---------- 字段精简 ----------
# 完整 record 中的 models[].interval/weekly 包含 7 个字段，我们只需要 3 个
_WINDOW_KEEP_FIELDS = ("used", "total", "remaining_percent",
                       "end_time", "remains_time_seconds")


def _simplify_record(record: dict[str, Any]) -> dict[str, Any]:
    """精简 NDJSON record：只保留必要字段，模型窗口只保留 used/total/remaining_percent。"""
    out: dict[str, Any] = {
        "alias": record.get("alias"),
        "ts": record.get("timestamp"),
        "ok": record.get("ok", False),
    }
    if not record.get("ok"):
        err = record.get("error") or record.get("error_msg")
        if err:
            out["error"] = err
    models = record.get("models")
    if isinstance(models, list):
        slim = []
        for m in models:
            if not isinstance(m, dict):
                continue
            slim_m: dict[str, Any] = {"name": m.get("name")}
            for wkey in ("interval", "weekly"):
                w = m.get(wkey)
                if not isinstance(w, dict):
                    continue
                slim_m[wkey] = {k: w.get(k) for k in _WINDOW_KEEP_FIELDS
                                if k in w}
            slim.append(slim_m)
        if slim:
            out["models"] = slim
    return out


# ---------- API 调用 ----------

REMAINING_KEYS = ("remains", "remaining", "remain", "current_remaining",
                  "left", "balance", "quota_remaining", "available")
TOTAL_KEYS = ("total", "quota", "current_total", "limit", "quota_total",
              "total_quota", "plan_total")
USED_KEYS = ("used", "used_amount", "consumed", "current_used", "used_quota")
RESET_KEYS = ("reset_time", "next_reset", "reset_at", "expire_at",
              "next_reset_time")
PLAN_KEYS = ("plan", "plan_name", "model", "package", "tier")


def _find_value(payload: Any, candidates: tuple[str, ...]) -> Any:
    """在嵌套 dict 中按候选字段名查找值（深度 3 层）。"""
    def _walk(node: Any, depth: int) -> Any:
        if depth > 3 or not isinstance(node, dict):
            return None
        for key in candidates:
            if key in node and node[key] is not None:
                return node[key]
        for value in node.values():
            found = _walk(value, depth + 1)
            if found is not None:
                return found
        return None
    return _walk(payload, 0)


def _ms_to_iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000,
                                      tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OSError, OverflowError):
        return None


def _parse_model_remains(payload: Any) -> list[dict[str, Any]]:
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
                # 原 API 返的是毫秒（5h 窗口 ~18,000,000 / 周窗口 ~604,800,000），
                # 转为秒供前端倒计时使用
                "remains_time_seconds": int(m.get("remains_time") or 0) // 1000,
            },
            "weekly": {
                "total": m.get("current_weekly_total_count"),
                "used": m.get("current_weekly_usage_count"),
                "remaining_percent": m.get("current_weekly_remaining_percent"),
                "status": m.get("current_weekly_status"),
                "start_time": _ms_to_iso(m.get("weekly_start_time")),
                "end_time": _ms_to_iso(m.get("weekly_end_time")),
                "remains_time_seconds": int(m.get("weekly_remains_time") or 0) // 1000,
            },
        })
    return out


def _is_success_response(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    base = payload.get("base_resp")
    if not isinstance(base, dict):
        return True
    return base.get("status_code") in (0, None)


def fetch_remains(api_url: str, token: str, timeout: int) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.get(api_url, headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        raise
    payload = response.json() if response.content else {}
    if not _is_success_response(payload):
        msg = "未知错误"
        if isinstance(payload, dict):
            base = payload.get("base_resp")
            if isinstance(base, dict):
                msg = str(base.get("status_msg") or msg)
        return {
            "ok": False, "http_status": response.status_code,
            "error_msg": msg, "models": [], "raw": payload,
        }
    return {
        "ok": True, "http_status": response.status_code,
        "models": _parse_model_remains(payload), "raw": payload,
    }


# ---------- 后台 poller ----------

class BackgroundPoller:
    """在 Flask 进程内启动后台线程，每 60s 调用 Minimax API 写入 NDJSON。

    简化版 UsageStore（不依赖 monitor.py），全量保留历史。
    """

    def __init__(self, config: dict[str, Any], store_path: Path,
                 history_limit: int = 50000):
        self.config = config
        self.store_path = store_path
        self.history_limit = history_limit
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: dict[str, dict[str, Any]] = {}
        self._total_polls = 0
        self._last_poll_ts: str | None = None
        self._last_error: str | None = None
        self._load_history()

    def _load_history(self) -> None:
        if not self.store_path.exists():
            return
        with self._lock:
            self._latest.clear()
        with self.store_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                alias = r.get("alias")
                if alias:
                    with self._lock:
                        self._latest[alias] = r

    def _append(self, record: dict[str, Any]) -> None:
        alias = record.get("alias")
        if alias:
            with self._lock:
                self._latest[alias] = record
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            self.store_path.touch()
        with self.store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="poller",
                                       daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        interval = self.config.get("poll_interval_seconds", 60)
        # 启动立即拉一次
        try:
            self._poll_once()
        except Exception as e:  # noqa: BLE001
            self._last_error = f"启动首次轮询异常: {e}"
        next_run = time.time() + interval
        while not self._stop.is_set():
            wait = next_run - time.time()
            if wait > 0:
                self._stop.wait(timeout=min(wait, 1.0))
                if self._stop.is_set():
                    return
                continue
            try:
                self._poll_once()
            except Exception as e:  # noqa: BLE001
                self._last_error = f"轮询异常: {e}"
            next_run += interval

    def _poll_once(self) -> None:
        api_url = self.config.get("api_url", "https://www.minimaxi.com/v1/token_plan/remains")
        timeout = self.config.get("request_timeout_seconds", 15)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._total_polls += 1
        self._last_poll_ts = ts
        for key in self.config.get("keys", []):
            if not key.get("enabled", True):
                continue
            alias = key["alias"]
            token = key.get("token", "")
            if not token or token.startswith("REPLACE_WITH"):
                self._append({
                    "alias": alias, "ok": False, "timestamp": ts,
                    "error": "token 未配置或仍为占位符",
                    "tags": key.get("tags", []),
                })
                continue
            try:
                parsed = fetch_remains(api_url, token, timeout)
                record: dict[str, Any] = {
                    "alias": alias, "ok": parsed["ok"], "timestamp": ts,
                    "tags": key.get("tags", []),
                }
                if parsed["ok"]:
                    record["models"] = parsed["models"]
                else:
                    record["error"] = parsed.get("error_msg", "未知错误")
            except requests.RequestException as e:
                record = {
                    "alias": alias, "ok": False, "timestamp": ts,
                    "tags": key.get("tags", []),
                    "error": f"{type(e).__name__}: {e}",
                }
            except (ValueError, json.JSONDecodeError) as e:
                record = {
                    "alias": alias, "ok": False, "timestamp": ts,
                    "tags": key.get("tags", []),
                    "error": f"JSON 解析失败: {e}",
                }
            self._append(record)

    def status(self) -> dict[str, Any]:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "total_polls": self._total_polls,
            "last_poll_ts": self._last_poll_ts,
            "last_error": self._last_error,
            "latest_keys": list(self._latest.keys()),
        }


class _KeyAuthMiddleware:
    """WSGI 中间件：验证 /mobile/<KEY>/ 或 /desktop/<KEY>/ 前缀，剥掉后传给 Flask。

    行为：
      - /mobile/<KEY>/...   → 剥前缀 + 设 X-Force-Mobile=1 → Flask 返移动版 HTML
      - /desktop/<KEY>/...  → 剥前缀（不设）→ Flask 返桌面版 HTML
      - /api/...            → 透传，返真实数据（兼容旧 iPhone 缓存的 JS）
      - 错 key 或其他        → 404
    """

    def __init__(self, wsgi_app, access_key: str):
        self.wsgi_app = wsgi_app
        self.access_key = access_key

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/")
        # /api/ 路径：兼容 iPhone 旧 JS 缓存，返 200 + 真实数据
        if path.startswith("/api/"):
            environ["PATH_INFO"] = path
            environ["SCRIPT_NAME"] = ""
            return self.wsgi_app(environ, start_response)
        # /mobile/ 和 /desktop/ 路径
        force_mobile = False
        if path.startswith("/mobile/"):
            force_mobile = True
        elif path.startswith("/desktop/"):
            force_mobile = False
        else:
            return _not_found(start_response)
        parts = path.split("/")
        if len(parts) < 3 or not parts[2]:
            return _not_found(start_response)
        if parts[2] != self.access_key:
            return _not_found(start_response)
        # 剥掉前缀：/mobile/<key>/X → /X
        remainder = "/".join(parts[3:])
        new_path = "/" + remainder if remainder else "/"
        environ["PATH_INFO"] = new_path
        environ["SCRIPT_NAME"] = ""
        if force_mobile:
            # 用 HTTP header 告诉 Flask 这个请求要返移动版
            environ["HTTP_X_FORCE_MOBILE"] = "1"
        return self.wsgi_app(environ, start_response)


def _not_found(start_response):
    body = b"Not Found"
    start_response("404 NOT FOUND", [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ])
    return [body]


def create_app(data_file: Path = DEFAULT_DATA_FILE,
               poller: BackgroundPoller | None = None,
               access_key: str | None = None) -> Flask:
    # 加载或生成访问 key
    if not access_key:
        access_key = _load_or_create_access_key()
    app = Flask(__name__)
    # 在 Flask 处理之前加 Key 验证中间件
    app.wsgi_app = _KeyAuthMiddleware(app.wsgi_app, access_key)
    CORS(app)
    app.config["POLLER"] = poller
    app.config["ACCESS_KEY"] = access_key

    # ---- gzip 响应压缩中间件 ----
    # 只压缩 JSON/HTML 响应，跳过 < 512 字节的小响应，节约 CPU
    MIN_GZIP_SIZE = 512
    GZIP_MIMETYPES = {"application/json", "text/html", "text/css",
                      "application/javascript", "text/javascript"}
    _GZIP_LEVEL = 6  # 1-9，6 是速度/压缩比最佳平衡

    @app.after_request
    def _gzip_response(response):
        try:
            # 已是压缩流（deflate/br）就跳过
            ce = (response.headers.get("Content-Encoding") or "").lower()
            if ce:
                return response
            # 仅对支持 gzip 的客户端压缩
            ae = (request.headers.get("Accept-Encoding") or "").lower()
            if "gzip" not in ae:
                return response
            # 仅对允许的 mime 压缩
            ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in GZIP_MIMETYPES:
                return response
            # 仅当 body 足够大时压缩（避免小响应 CPU 浪费）
            data = response.get_data()
            if len(data) < MIN_GZIP_SIZE:
                return response
            # 直接在内存中 gzip
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb",
                               compresslevel=_GZIP_LEVEL) as gz:
                gz.write(data)
            response.set_data(buf.getvalue())
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = str(len(response.get_data()))
            response.headers["Vary"] = "Accept-Encoding"
        except Exception:
            # 任何错误都不影响正常响应
            pass
        return response

    def load_records() -> list[dict[str, Any]]:
        if not data_file.exists():
            return []
        records: list[dict[str, Any]] = []
        with data_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({
            "ok": True,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        })

    @app.get("/api/keys")
    def list_keys() -> Any:
        """返回每个 key 的最新状态（精简字段）。"""
        records = load_records()
        latest: dict[str, dict[str, Any]] = {}
        for r in records:
            alias = r.get("alias")
            if alias:
                latest[alias] = r
        return jsonify({
            "keys": [_simplify_record(r) for r in latest.values()],
        })

    @app.get("/api/history")
    def history() -> Any:
        """返回历史时序数据（精简字段），可按 alias / model / view 过滤。

        view=mobile: 只返回 interval 窗口的 used + remaining_percent（用于趋势图/sparkline）
                    ~80% 更小，配合 gzip 约 30-50 kB
        默认：完整字段（兼容桌面端）
        """
        alias_filter = request.args.get("alias")
        model_filter = request.args.get("model")
        view = request.args.get("view", "full")  # "mobile" | "full"
        limit = int(request.args.get("limit", str(MAX_HISTORY)))
        records = load_records()
        if alias_filter:
            records = [r for r in records if r.get("alias") == alias_filter]
        if model_filter:
            for r in records:
                r["models"] = [m for m in (r.get("models") or [])
                               if m.get("name") == model_filter]
        records = records[-limit:]
        if view == "mobile":
            slim_records = []
            for r in records:
                slim_r = {"ts": r.get("timestamp"), "alias": r.get("alias"),
                          "ok": r.get("ok", False)}
                models = r.get("models") or []
                if isinstance(models, list) and models:
                    slim_r["models"] = [
                        {"name": m.get("name"),
                         "rp": (m.get("interval") or {}).get("remaining_percent"),
                         "u": (m.get("interval") or {}).get("used")}
                        for m in models if isinstance(m, dict)
                    ]
                slim_records.append(slim_r)
            return jsonify({"records": slim_records, "count": len(slim_records)})
        return jsonify({
            "records": [_simplify_record(r) for r in records],
            "count": len(records),
        })

    @app.get("/api/summary")
    def summary() -> Any:
        """汇总信息：每个 (alias, model) 组合的统计。"""
        records = load_records()
        grouped: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            alias = r.get("alias")
            if not alias or not r.get("ok"):
                continue
            for m in (r.get("models") or []):
                grouped[(alias, m.get("name"))].append((r, m))

        result: list[dict[str, Any]] = []
        for (alias, model_name), pairs in grouped.items():
            interval_used = [m.get("interval", {}).get("used")
                             for _, m in pairs
                             if isinstance(m.get("interval", {}).get("used"),
                                           (int, float))]
            interval_remaining = [m.get("interval", {}).get("remaining_percent")
                                  for _, m in pairs
                                  if isinstance(m.get("interval", {})
                                                .get("remaining_percent"),
                                                (int, float))]
            weekly_used = [m.get("weekly", {}).get("used")
                           for _, m in pairs
                           if isinstance(m.get("weekly", {}).get("used"),
                                         (int, float))]
            weekly_remaining = [m.get("weekly", {}).get("remaining_percent")
                                for _, m in pairs
                                if isinstance(m.get("weekly", {})
                                              .get("remaining_percent"),
                                              (int, float))]
            latest_record, latest_model = pairs[-1]
            result.append({
                "alias": alias,
                "model": model_name,
                "count": len(pairs),
                "first_seen": pairs[0][0].get("timestamp"),
                "last_seen": latest_record.get("timestamp"),
                "latest_interval_used": latest_model.get("interval", {}).get("used"),
                "latest_interval_remaining_percent": (
                    latest_model.get("interval", {}).get("remaining_percent")),
                "latest_weekly_used": latest_model.get("weekly", {}).get("used"),
                "latest_weekly_remaining_percent": (
                    latest_model.get("weekly", {}).get("remaining_percent")),
                "max_interval_used": max(interval_used) if interval_used else None,
                "min_interval_remaining_percent": (
                    min(interval_remaining) if interval_remaining else None),
                "max_weekly_used": max(weekly_used) if weekly_used else None,
                "min_weekly_remaining_percent": (
                    min(weekly_remaining) if weekly_remaining else None),
            })
        return jsonify({"summary": result})

    @app.get("/")
    def index() -> Any:
        # WSGI 中间件用 X-Force-Mobile header 标记 mobile 路径
        if request.headers.get("X-Force-Mobile") == "1":
            return render_template_string(MOBILE_HTML)
        return render_template_string(INDEX_HTML)

    @app.get("/api/diag")
    def diag() -> Any:
        """返回数据文件诊断信息：是否存在、大小、记录数、最后修改时间。"""
        info = {
            "data_file": str(data_file),
            "exists": data_file.exists(),
            "size_bytes": data_file.stat().st_size if data_file.exists() else 0,
            "mtime": (datetime.fromtimestamp(data_file.stat().st_mtime,
                                              ).isoformat() + "Z"
                      if data_file.exists() else None),
            "record_count": 0,
            "alias_count": 0,
            "latest_timestamp": None,
            "poller": (app.config.get("POLLER").status()
                       if app.config.get("POLLER") else None),
        }
        if data_file.exists():
            aliases = set()
            latest_ts = None
            with data_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    info["record_count"] += 1
                    if r.get("alias"):
                        aliases.add(r["alias"])
                    ts = r.get("timestamp")
                    if ts and (latest_ts is None or ts > latest_ts):
                        latest_ts = ts
            info["alias_count"] = len(aliases)
            info["latest_timestamp"] = latest_ts
        return jsonify(info)

    @app.get("/api/poller")
    def poller_status() -> Any:
        """返回后台 poller 状态。"""
        p = app.config.get("POLLER")
        if not p:
            return jsonify({"running": False, "enabled": False})
        return jsonify(p.status())

    @app.get("/mobile")
    def mobile() -> Any:
        """iPhone 友好的移动端页面，60 秒自动刷新。"""
        resp = make_response(render_template_string(MOBILE_HTML))
        # 强制客户端每次都重新拉取，避免 Safari 缓存旧版 HTML 导致的 JS 错误
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    return app


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Minimax Token Plan 监控</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
           margin: 0; background: #0f172a; color: #e2e8f0; }
    header { padding: 20px 28px; background: #1e293b; border-bottom: 1px solid #334155; }
    h1 { margin: 0 0 4px 0; font-size: 20px; color: #38bdf8; }
    header p { margin: 0; color: #94a3b8; font-size: 13px; }
    main { padding: 24px; max-width: 1500px; margin: 0 auto; }
    .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px; }
    .card h3 { margin: 0 0 10px 0; font-size: 14px; color: #38bdf8; display: flex; justify-content: space-between; align-items: center; }
    .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
    .badge.ok { background: #064e3b; color: #6ee7b7; }
    .badge.fail { background: #7f1d1d; color: #fca5a5; }
    .section { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #334155; }
    .section-title { font-size: 12px; color: #94a3b8; margin-bottom: 6px; }
    .row { display: flex; justify-content: space-between; font-size: 13px; margin: 4px 0; }
    .row span:first-child { color: #94a3b8; }
    .row span:last-child { color: #f1f5f9; font-weight: 500; }
    .bar { background: #334155; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 6px; }
    .bar > div { height: 100%; transition: width 0.4s ease; }
    .err-msg { color: #fca5a5; font-size: 12px; margin-top: 6px; }
    .chart-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
    .chart-card h2 { margin: 0 0 12px 0; font-size: 15px; color: #cbd5e1; }
    .toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
    .toolbar select, .toolbar button { background: #334155; color: #e2e8f0; border: 1px solid #475569; padding: 6px 10px; border-radius: 6px; font-size: 13px; }
    .toolbar button { cursor: pointer; }
    .toolbar button:hover { background: #475569; }
    canvas { max-height: 320px; }
    footer { text-align: center; padding: 16px; color: #64748b; font-size: 12px; }
  </style>
</head>
<body>
  <header>
    <h1>Minimax Token Plan 监控</h1>
    <p>每分钟自动轮询 · 多 key × 多模型对比 · 历史趋势</p>
  </header>
  <main>
    <div class="toolbar">
      <label>Key：<select id="aliasSelect"><option value="">全部</option></select></label>
      <label>Model：<select id="modelSelect"><option value="">全部</option></select></label>
      <label>窗口：
        <select id="metricSelect">
          <option value="interval.used">5h 已用</option>
          <option value="interval.remaining_percent">5h 剩余%</option>
          <option value="weekly.used">周 已用</option>
          <option value="weekly.remaining_percent">周 剩余%</option>
        </select>
      </label>
      <button id="refreshBtn">手动刷新</button>
      <span id="meta" style="color:#94a3b8;font-size:12px;margin-left:auto"></span>
    </div>

    <div id="cards" class="cards"></div>

    <div class="chart-card">
      <h2>趋势图</h2>
      <canvas id="trendChart"></canvas>
    </div>
  </main>
  <footer>数据来源：<code>data/usage.ndjson</code> · 每 30 秒自动刷新</footer>

  <script>
    const COLORS = ['#38bdf8', '#f472b6', '#fbbf24', '#a78bfa', '#34d399', '#fb7185', '#60a5fa', '#facc15'];
    let chart;

    async function fetchJSON(url) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(url + ' ' + r.status);
      return r.json();
    }

    function formatNumber(n) {
      if (n === null || n === undefined) return '-';
      if (Number.isInteger(n)) return n.toLocaleString();
      return Number(n).toLocaleString(undefined, { maximumFractionDigits: 4 });
    }

    function usedPct(used, total) {
      if (typeof used === 'number' && typeof total === 'number' && total) {
        return Math.max(0, Math.min(100, used / total * 100));
      }
      return null;
    }

    function getNested(obj, path) {
      return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
    }

    function colorByPct(p) {
      if (p == null) return '#475569';
      if (p < 60) return '#22c55e';
      if (p < 85) return '#eab308';
      return '#ef4444';
    }

    function renderCards(keys) {
      const container = document.getElementById('cards');
      container.innerHTML = '';
      if (!keys.length) {
        container.innerHTML = '<div class="card" style="grid-column: 1/-1;color:#94a3b8">暂无数据，请确认 monitor.py 正在运行</div>';
        return;
      }
      keys.forEach((k) => {
        const ok = k.ok === true;
        const models = k.models || [];
        if (!ok && !models.length) {
          const card = document.createElement('div');
          card.className = 'card';
          card.innerHTML = `
            <h3>${k.alias}
              <span class="badge ${ok ? 'ok' : 'fail'}">${ok ? '正常' : '异常'}</span>
            </h3>
            <div class="err-msg">${(k.error || k.error_msg || '无数据')}</div>
            <div class="row"><span>更新时间</span><span>${(k.timestamp || '').replace('T', ' ').replace('Z', '')}</span></div>
          `;
          container.appendChild(card);
          return;
        }
        models.forEach((m) => {
          const i = m.interval || {};
          const w = m.weekly || {};
          const iRatio = usedPct(i.used, i.total);
          const wRatio = usedPct(w.used, w.total);
          const card = document.createElement('div');
          card.className = 'card';
          card.innerHTML = `
            <h3>${k.alias} · <span style="color:#cbd5e1">${m.name || '-'}</span>
              <span class="badge ${ok ? 'ok' : 'fail'}">${ok ? '正常' : '异常'}</span>
            </h3>
            <div class="section">
              <div class="section-title">5 小时窗口 (剩余 ${i.remaining_percent ?? '?'}%)</div>
              <div class="row"><span>已用 / 总额</span><span>${formatNumber(i.used)} / ${formatNumber(i.total)}</span></div>
              <div class="row"><span>重置时间</span><span>${i.end_time || '-'}</span></div>
              ${iRatio != null ? `<div class="bar"><div style="width:${iRatio.toFixed(1)}%;background:${colorByPct(iRatio)}"></div></div>
                <div style="font-size:11px;color:#94a3b8;margin-top:4px;text-align:right">已用 ${iRatio.toFixed(1)}%</div>` : ''}
            </div>
            <div class="section">
              <div class="section-title">周窗口 (剩余 ${w.remaining_percent ?? '?'}%)</div>
              <div class="row"><span>已用 / 总额</span><span>${formatNumber(w.used)} / ${formatNumber(w.total)}</span></div>
              <div class="row"><span>重置时间</span><span>${w.end_time || '-'}</span></div>
              ${wRatio != null ? `<div class="bar"><div style="width:${wRatio.toFixed(1)}%;background:${colorByPct(wRatio)}"></div></div>
                <div style="font-size:11px;color:#94a3b8;margin-top:4px;text-align:right">已用 ${wRatio.toFixed(1)}%</div>` : ''}
            </div>
            <div class="row" style="margin-top:8px"><span>更新时间</span><span>${(k.timestamp || '').replace('T', ' ').replace('Z', '')}</span></div>
          `;
          container.appendChild(card);
        });
      });
    }

    function buildChart(records, metricPath) {
      const byKey = new Map();
      records.forEach((r) => {
        if (!r.ok) return;
        (r.models || []).forEach((m) => {
          const v = getNested(m, metricPath);
          if (typeof v !== 'number') return;
          const label = `${r.alias} · ${m.name}`;
          if (!byKey.has(label)) byKey.set(label, []);
          byKey.get(label).push({ x: r.timestamp, y: v });
        });
      });

      const datasets = [...byKey.entries()].map(([label, points], i) => ({
        label,
        data: points,
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length] + '33',
        tension: 0.25,
        pointRadius: 2,
        borderWidth: 2,
        spanGaps: true,
      }));

      if (chart) chart.destroy();
      const ctx = document.getElementById('trendChart').getContext('2d');
      chart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'nearest', intersect: false },
          plugins: { legend: { labels: { color: '#cbd5e1' } } },
          scales: {
            x: { type: 'time', time: { tooltipFormat: 'yyyy-MM-dd HH:mm:ss' },
                 ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } },
            y: { ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } },
          },
        },
      });
    }

    async function refreshAll() {
      const alias = document.getElementById('aliasSelect').value;
      const model = document.getElementById('modelSelect').value;
      const metric = document.getElementById('metricSelect').value;
      try {
        const params = new URLSearchParams();
        if (alias) params.set('alias', alias);
        if (model) params.set('model', model);
        params.set('limit', '2000');
        const [keysResp, histResp] = await Promise.all([
          fetchJSON('/api/keys'),
          fetchJSON('/api/history?' + params.toString()),
        ]);
        renderCards(keysResp.keys || []);
        buildChart(histResp.records || [], metric);
        document.getElementById('meta').textContent =
          `共 ${(histResp.records || []).length} 条记录 · ${new Date().toLocaleString()}`;

        // 同步 alias 下拉
        const aliasSel = document.getElementById('aliasSelect');
        const knownAliases = new Set((keysResp.keys || []).map((k) => k.alias));
        if (knownAliases.size && aliasSel.options.length - 1 !== knownAliases.size) {
          const cur = aliasSel.value;
          aliasSel.innerHTML = '<option value="">全部</option>' +
            [...knownAliases].map((a) => `<option value="${a}">${a}</option>`).join('');
          aliasSel.value = cur;
        }
        // 同步 model 下拉
        const modelSel = document.getElementById('modelSelect');
        const knownModels = new Set();
        (keysResp.keys || []).forEach((k) => (k.models || []).forEach((m) => knownModels.add(m.name)));
        if (knownModels.size && modelSel.options.length - 1 !== knownModels.size) {
          const cur = modelSel.value;
          modelSel.innerHTML = '<option value="">全部</option>' +
            [...knownModels].map((m) => `<option value="${m}">${m}</option>`).join('');
          modelSel.value = cur;
        }
      } catch (e) {
        document.getElementById('meta').textContent = '加载失败: ' + e.message;
      }
    }

    document.getElementById('refreshBtn').onclick = refreshAll;
    document.getElementById('aliasSelect').onchange = refreshAll;
    document.getElementById('modelSelect').onchange = refreshAll;
    document.getElementById('metricSelect').onchange = refreshAll;
    refreshAll();
    setInterval(refreshAll, 30000);
  </script>
</body>
</html>
"""


MOBILE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>MinimaxGuard</title>
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="theme-color" content="#000000">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
  <style>
    * { box-sizing: border-box; }
    html, body { background: #000; color: #e5e5e5; margin: 0; padding: 0; }
    body {
      font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      padding: env(safe-area-inset-top) 16px
               env(safe-area-inset-bottom) 16px;
    }
    /* 顶部 */
    .top { display: flex; align-items: center; gap: 12px; padding: 8px 0 14px; }
    .top h1 { margin: 0; font-size: 16px; font-weight: 700; color: #fff; letter-spacing: 0.5px; }
    .top .meta { color: #555; font-size: 11px; flex: 1; }
    .top .meta .t { color: #888; }
    /* key 切换器：暗色下拉 */
    .alias-sel { background: #0a0a0a; color: #e5e5e5;
                 border: 1px solid #1f1f1f; border-radius: 4px;
                 font: 11px ui-monospace, SFMono-Regular, Menlo, monospace;
                 padding: 4px 8px; cursor: pointer;
                 appearance: none; -webkit-appearance: none;
                 background-image: linear-gradient(45deg, transparent 50%, #888 50%),
                                   linear-gradient(135deg, #888 50%, transparent 50%);
                 background-position: calc(100% - 11px) 50%, calc(100% - 7px) 50%;
                 background-size: 4px 4px, 4px 4px;
                 background-repeat: no-repeat;
                 padding-right: 22px; }
    .alias-sel:focus { outline: none; border-color: #333; }
    .top button { background: transparent; color: #888; border: 1px solid #333;
                  padding: 5px 12px; border-radius: 4px; font: inherit; cursor: pointer; }
    .top button:active { background: #1a1a1a; color: #fff; }

    /* 卡片网格：display:contents 让 main 的 grid 直接看到 cards + trend-box */
    .grid { display: contents; }
    .card { min-width: 0; }

    /* 移动端主体布局：CSS Grid 3 列 + 显式位置
       4 个 card 自动排成 3+1（dense），trend-box 显式 grid-column: 2/4 占第 2 行 col 2-3 */
    main {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      grid-auto-flow: row dense;
      gap: 10px;
    }
    .toolbar { grid-column: 1 / -1; }  /* 顶部 toolbar 跨整行 */

    /* 移动端回 1 列 */
    @media (max-width: 720px) {
      main { grid-template-columns: 1fr; }
    }

    /* 仪表盘卡片 */
    .card { background: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 8px;
            padding: 12px 14px; position: relative; overflow: hidden; }
    .card-head { display: flex; align-items: baseline; justify-content: space-between;
                 margin-bottom: 8px; }
    .card-title { font-size: 14px; font-weight: 600; color: #fff; }
    .card-title .model { color: #888; font-weight: 400; margin-left: 4px; }
    .card-status { font-size: 10px; padding: 1px 6px; border-radius: 3px; }
    .card-status.ok { color: #4ade80; border: 1px solid #166534; }
    .card-status.fail { color: #f87171; border: 1px solid #7f1d1d; }

    /* 5h 大数字 */
    .big { display: flex; align-items: baseline; gap: 6px; margin: 2px 0 6px; }
    .big .pct { font-size: 28px; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
    .big .lbl { color: #666; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }

    /* 进度条 */
    .bar { width: 100%; height: 4px; background: #1a1a1a; border-radius: 2px;
           overflow: hidden; margin: 4px 0 10px; }
    .bar > div { height: 100%; transition: width 0.4s ease; }

    /* 周用量（小） */
    .small { display: flex; align-items: center; gap: 8px; font-size: 11px;
             color: #888; margin: 2px 0 4px; }
    .small .pct { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 36px; }
    .small .bar { flex: 1; height: 3px; margin: 0; }

    /* sparkline */
    .spark { font-family: monospace; letter-spacing: -1px; font-size: 10px;
             line-height: 1; margin-top: 6px; opacity: 0.85; }

    /* 倒计时 + 剩余额度行 */
    .meta-row { display: flex; align-items: center; gap: 4px;
                font-size: 10px; margin-top: 6px;
                font-variant-numeric: tabular-nums; }
    .meta-row .cd { color: #cbd5e1; font-weight: 500; }
    .cd-row { display: flex; align-items: center; gap: 6px;
              font-size: 10px; margin-top: 4px; margin-bottom: 2px;
              font-variant-numeric: tabular-nums; }
    .cd-row .cd { color: #cbd5e1; font-weight: 500; }
    /* 5H 用量（左侧大数字） + 5H/周 倒计时（右侧两行）横排
       关键对齐：pct 24px 文字，cd-stack 高度 = 24px，
       两行 12px 居中堆叠（5H 刷新在 0-12px 区、周刷新在 12-24px 区）
       整体与 pct 等高对齐 */
    .big-row { display: flex; align-items: center; gap: 10px;
               min-width: 0; margin: 2px 0 6px; }
    .big-left { display: flex; align-items: baseline; gap: 6px;
                flex-shrink: 0; min-height: 24px; }
    .big-left .pct { font-size: 24px; font-weight: 700; line-height: 1;
                     font-variant-numeric: tabular-nums; }
    .big-left .lbl { color: #666; font-size: 9px; text-transform: uppercase;
                     letter-spacing: 0.5px; }
    /* 5H / 周 倒计时两行：固定 24px 高，5H 刷新顶部对齐 pct 顶部，
       周刷新底部对齐 pct 底部（两行 12px 严格均分） */
    .cd-stack { display: flex; flex-direction: column; flex: 1;
                min-width: 0; height: 24px;
                justify-content: space-between;
                font-variant-numeric: tabular-nums; }
    .cd-line { font-size: 9px; line-height: 12px; color: #888;
               white-space: nowrap; overflow: hidden;
               text-overflow: ellipsis; }
    .cd-line .cd { color: #cbd5e1; font-weight: 600; }
    .cd-combo-inline { display: inline-flex; align-items: center; gap: 4px;
                       font-size: 10px; margin-left: auto;
                       font-variant-numeric: tabular-nums;
                       color: #888; white-space: nowrap; }
    .cd-combo-inline .cd { color: #cbd5e1; font-weight: 600; }
    .cd-combo-inline .sep { color: #444; margin: 0 1px; }
    /* 保留旧 .cd-combo 兼容（如有其他位置在用） */
    .cd-combo { display: flex; align-items: center; gap: 5px;
                font-size: 10px; margin: 8px 0 4px;
                font-variant-numeric: tabular-nums;
                color: #888; }
    .cd-combo .cd { color: #cbd5e1; font-weight: 600; }
    .cd-combo .sep { color: #444; margin: 0 1px; }
    .quota-row { display: flex; gap: 12px; font-size: 10px; color: #888;
                 margin-top: 4px; padding-top: 4px;
                 border-top: 1px dashed #1a1a1a;
                 font-variant-numeric: tabular-nums; }

    /* 颜色 */
    .g { color: #4ade80; } .y { color: #facc15; } .r { color: #f87171; } .d { color: #555; }
    .bgg { background: #22c55e; } .bgy { background: #eab308; } .bgr { background: #ef4444; }

    /* 状态/错误 */
    .err { color: #f87171; font-size: 12px; padding: 6px 0; }
    .empty { color: #555; padding: 60px 0; text-align: center; }

    /* 趋势图区（与第 4 个 card 同行，撑满该行剩余 2 列） */
    .trend-box {
      grid-column: 2 / 4;       /* 第 2 行 col 2-3（与第 4 个 card 同行） */
      grid-row: 2;              /* 强制第 2 行 */
      display: flex;
      flex-direction: column;
      min-height: 180px;
      padding: 10px 12px;
      background: #0a0a0a;
      border: 1px solid #1a1a1a; border-radius: 8px;
    }
    @media (max-width: 720px) {
      .trend-box { grid-column: 1; grid-row: auto; }
    }
    /* 单 alias 模式：cards 数 ≤ 2，trend-box 移到下一行占整行 */
    main.single-alias .trend-box {
      grid-column: 1 / -1;
      grid-row: auto;
      margin-top: 4px;
    }
    .trend-head { display: flex; justify-content: space-between; color: #888;
                  font-size: 11px; margin-bottom: 6px; flex-shrink: 0; }
    .trend-head .lbl { color: #ccc; }
    .trend-body { flex: 1; position: relative; min-height: 120px; }
    svg.spark-svg { width: 100%; height: 100%; display: block; }
  </style>
</head>
<body>
  <div class="top">
    <h1>MinimaxGuard</h1>
    <select id="aliasSelect" class="alias-sel" onchange="onAliasChange()">
      <option value="__all__">全部 keys</option>
    </select>
    <div class="meta">
      <span class="t" id="curTime">--:--:--</span> ·
      <span id="keyCount">0 keys</span> ·
      <span id="histCount">0 hist</span> ·
      <span id="countdown">--s</span>
    </div>
    <button onclick="refresh()">刷新</button>
  </div>

  <main>
  <div id="grid" class="grid">
    <!-- 4 个 card 由 JS 注入到这里 -->
  </div>
  <!-- trend-box 放在 #grid 之外（main 直接子节点），避免 grid.innerHTML='' 把它清空 -->
  <div class="trend-box">
    <div class="trend-head">
      <span class="lbl">5h 已用% 趋势（24h）</span>
      <span id="chartRange">—</span>
    </div>
    <div class="trend-body"><canvas id="trendChart"></canvas></div>
  </div>
  </main>

  <script>
    let nextRefresh = 0;
    let lastSparkData = new Map();
    let lastKeyCount = 0;
    let lastHistCount = 0;
    let lastErr = '';
    // ---- key 切换器 ----
    const STORAGE_ALIAS = 'minimaxguard.alias';
    let allKeysCache = [];        // 缓存 /api/keys 完整结果，避免重复请求
    let allRecordsCache = [];     // 缓存 /api/history records（用于 onAliasChange 重绘 chart）
    let selectedAlias = '__all__'; // 当前选中的 alias（'__all__' = 全部）

    function loadSelectedAlias() {
      try {
        const v = localStorage.getItem(STORAGE_ALIAS);
        if (v) selectedAlias = v;
      } catch (e) { /* localStorage 不可用时静默 */ }
    }
    function saveSelectedAlias() {
      try { localStorage.setItem(STORAGE_ALIAS, selectedAlias); }
      catch (e) { /* ignore */ }
    }
    function populateAliasSelect() {
      const sel = document.getElementById('aliasSelect');
      if (!sel) return;
      // 用 Set 去重 alias
      const aliases = [...new Set(allKeysCache.map(k => k.alias))];
      // 重建 options（保留 "__all__"）
      sel.innerHTML = '<option value="__all__">全部 keys</option>'
        + aliases.map(a => `<option value="${a}">${a}</option>`).join('');
      // 恢复选择（如果该 alias 已被删除则回退到 __all__）
      if (selectedAlias !== '__all__' && !aliases.includes(selectedAlias)) {
        selectedAlias = '__all__';
      }
      sel.value = selectedAlias;
    }
    function onAliasChange() {
      const sel = document.getElementById('aliasSelect');
      if (!sel) return;
      selectedAlias = sel.value;
      saveSelectedAlias();
      // 切换 main 的 single-alias class 决定 trend-box 位置
      document.querySelector('main')?.classList.toggle(
        'single-alias', selectedAlias !== '__all__');
      // 立即用缓存数据重渲染（不重新请求 API）
      const diag = { record_count: lastHistCount, exists: true };
      rebuildChartFromCache(allRecordsCache);
      renderCards(visibleKeys(), diag);
    }
    function visibleKeys() {
      if (selectedAlias === '__all__') return allKeysCache;
      return allKeysCache.filter(k => k.alias === selectedAlias);
    }

    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    function nowStr() {
      const d = new Date();
      return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }
    function colorCls(p) {
      if (p == null) return 'd';
      if (p < 60) return 'g';
      if (p < 85) return 'y';
      return 'r';
    }
    function bgCls(p) {
      if (p == null) return '';
      if (p < 60) return 'bgg';
      if (p < 85) return 'bgy';
      return 'bgr';
    }
    function fmtTime(iso) {
      if (!iso) return '-';
      return iso.slice(11, 19);
    }
    function sparklineStr(values, w = 24) {
      if (!values || !values.length) return '·'.repeat(w);
      const sample = values.slice(-w);
      while (sample.length < w) sample.unshift(sample[0]);
      const blocks = '▁▂▃▄▅▆▇█';
      const lo = Math.min(...sample), hi = Math.max(...sample);
      if (hi === lo) return blocks[0].repeat(w);
      return sample.map(v =>
        blocks[Math.round((v - lo) / (hi - lo) * (blocks.length - 1))]
      ).join('');
    }
    function usedPctFromRemain(rp) {
      return typeof rp === 'number' ? Math.max(0, Math.min(100, 100 - rp)) : null;
    }

    function updateMeta() {
      document.getElementById('curTime').textContent = nowStr();
      document.getElementById('keyCount').textContent = lastKeyCount + ' keys';
      document.getElementById('histCount').textContent = lastHistCount + ' hist';
      const remain = Math.max(0, Math.round((nextRefresh - Date.now()) / 1000));
      const cd = document.getElementById('countdown');
      if (lastErr) { cd.textContent = 'err'; cd.className = 'r'; }
      else { cd.textContent = remain + 's'; cd.className = remain < 10 ? 'y' : 'd'; }
    }

    async function refresh() {
      try {
        const [keysResp, histResp, diagResp] = await Promise.all([
          fetch('/minimax/api/keys').then(r => r.json()),
          fetch('/minimax/api/history?limit=2000&view=mobile').then(r => r.json()),
          fetch('/minimax/api/diag').then(r => r.json()),
        ]);
        lastKeyCount = (keysResp.keys || []).length;
        lastHistCount = diagResp.record_count || 0;
        lastErr = '';

        // 缓存全量 keys + 填充切换器
        allKeysCache = keysResp.keys || [];
        allRecordsCache = histResp.records || [];
        populateAliasSelect();

        // lastSparkData：仍按全量记录累计（sparkline 在卡片内显示，与 alias 过滤独立）
        lastSparkData.clear();
        for (const r of (histResp.records || [])) {
          if (!r.ok || !r.models) continue;
          for (const m of r.models) {
            // mobile view 字段：u (used), rp (remaining_percent)
            const used = (m.u != null) ? m.u : (m.interval || {}).used;
            if (typeof used === 'number') {
              const k = `${r.alias}/${m.name}`;
              if (!lastSparkData.has(k)) lastSparkData.set(k, []);
              lastSparkData.get(k).push(used);
            }
          }
        }
        // 先 buildChart（按当前 alias 过滤）
        rebuildChartFromCache(allRecordsCache);
        // 切换 main 的 single-alias class（cards ≤ 2 时 trend-box 移到底部）
        const visKeys = visibleKeys();
        document.querySelector('main')?.classList.toggle(
          'single-alias', visKeys.length > 0 && visKeys.length <= 2);
        // 再 renderCards（按当前 alias 过滤）
        renderCards(visKeys, diagResp);
        const total = [...lastSparkData.values()].reduce((a, v) => a + v.length, 0);
        document.getElementById('chartRange').textContent = total ? `${total} 点` : '—';
        nextRefresh = Date.now() + 60000;
        lastRefreshAt = Date.now() / 1000;
        updateCountdowns();
      } catch (e) {
        // 详细错误信息，便于排查 Safari 缓存的旧版 JS
        lastErr = `${e.name}: ${e.message || String(e)}`;
        if (window.console) console.error('[MinimaxGuard] refresh error', e);
      }
    }
    // 用缓存的 histResp.records 重新构建 chart（按当前 alias 过滤）
    function rebuildChartFromCache(records) {
      if (!records) {
        // 没有传 records → 重新请求（仅在 onAliasChange 中通过 refresh 触发的情况）
        // 这里用 buildChart 直接传全量（不可取），所以从已有 allKeysCache 推导
        return;
      }
      const filtered = (selectedAlias === '__all__')
        ? records
        : records.filter(r => r.alias === selectedAlias);
      buildChart(filtered);
    }

    // 捕获整个页面的 JS 错误，显示在 meta 区
    window.addEventListener('error', (ev) => {
      lastErr = `${ev.message} @ ${ev.filename}:${ev.lineno}`;
      if (window.console) console.error('[MinimaxGuard] window error', ev.error);
    });
    window.addEventListener('unhandledrejection', (ev) => {
      const r = ev.reason || {};
      lastErr = `Promise: ${r.message || r}`;
    });

    function renderCards(keys, diag) {
      const grid = document.getElementById('grid');
      if (!keys.length) {
        let hint = '无 alias 记录。在 config.json 的 keys 列表里至少配置一个 token。';
        if (diag && !diag.exists) hint = '数据文件不存在。';
        else if (diag && diag.record_count === 0) hint = '数据为空，等待首次轮询（最多 60s）…';
        else if (lastErr) hint = '请求失败: ' + lastErr;
        grid.innerHTML = `<div class="empty">${hint}</div>`;
        return;
      }
      const cards = [];
      for (const k of keys) {
        const status = k.ok ? 'ok' : 'fail';
        const statusTxt = k.ok ? 'OK' : 'FAIL';
        if (!k.models || !k.models.length) {
          cards.push(`
            <div class="card">
              <div class="card-head">
                <div class="card-title">${k.alias}</div>
                <span class="card-status ${status}">${statusTxt}</span>
              </div>
              <div class="err">${k.error || '无数据'}</div>
            </div>`);
          continue;
        }
        for (const m of k.models) {
          const interval = m.interval || {};
          const weekly = m.weekly || {};
          const iP = usedPctFromRemain(interval.remaining_percent);
          const wP = usedPctFromRemain(weekly.remaining_percent);
          const sparkVals = lastSparkData.get(`${k.alias}/${m.name}`) || [];
          const spark = sparklineStr(sparkVals, 28);
          const sparkColor = sparkVals.length ? colorCls(
            (iP || 0) >= 85 ? 100 : (iP || 0) >= 60 ? 70 : 30
          ) : 'd';
          // 计算剩余额度（绝对值）
          const iRemain = (interval.total != null && interval.used != null)
            ? (interval.total - interval.used) : null;
          const wRemain = (weekly.total != null && weekly.used != null)
            ? (weekly.total - weekly.used) : null;
          // 存储原始数据供 tick 倒计时更新用
          window.__minimaxLast = window.__minimaxLast || {};
          window.__minimaxLast[`${k.alias}/${m.name}`] = { interval, weekly };
          // 计算初始倒计时（render 时显示）
          const iCountdown = formatCountdown(interval.remains_time_seconds);
          const wCountdown = formatCountdown(weekly.remains_time_seconds);
          cards.push(`
            <div class="card">
              <div class="card-head">
                <div class="card-title">${k.alias}<span class="model">· ${m.name || '-'}</span></div>
                <span class="card-status ${status}">${statusTxt}</span>
              </div>
              <div class="big-row">
                <div class="big-left">
                  <span class="pct ${colorCls(iP)}">${iP == null ? '-' : iP.toFixed(0) + '%'}</span>
                  <span class="lbl">5h 用量</span>
                </div>
                <div class="cd-stack">
                  <div class="cd-line">
                    <span class="d">5H 刷新</span>
                    <span class="cd i-cd" data-remain="${interval.remains_time_seconds || 0}">${iCountdown}</span>
                  </div>
                  <div class="cd-line">
                    <span class="d">周 刷新</span>
                    <span class="cd w-cd" data-remain="${weekly.remains_time_seconds || 0}">${wCountdown}</span>
                  </div>
                </div>
              </div>
              <div class="bar"><div class="${bgCls(iP)}" style="width:${iP || 0}%"></div></div>
              <div class="small">
                <span>周用量</span>
                <span class="pct ${colorCls(wP)}">${wP == null ? '-' : wP.toFixed(0) + '%'}</span>
                <div class="bar"><div class="${bgCls(wP)}" style="width:${wP || 0}%"></div></div>
              </div>
              <div class="quota-row">
                <span class="d">5h 余 ${iRemain == null ? '-' : iRemain.toLocaleString()}</span>
                <span class="d">周余 ${wRemain == null ? '-' : wRemain.toLocaleString()}</span>
              </div>
              <div class="spark ${sparkColor}">${spark}</div>
            </div>`);
        }
      }
      // 4 个 card 注入到 #grid（trend-box 是 main 的直接子节点，不在 #grid 内，不会被 innerHTML 清空）
      grid.innerHTML = cards.join('');
    }

    // ---- 倒计时格式化 ----
    let lastRefreshAt = 0;
    function formatCountdown(seconds) {
      if (seconds == null || isNaN(seconds) || seconds < 0) return '-';
      const s = Math.floor(seconds);
      const d = Math.floor(s / 86400);
      const h = Math.floor((s % 86400) / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      const pad = n => n < 10 ? '0' + n : '' + n;
      if (d > 0) return `${d}d ${pad(h)}:${pad(m)}:${pad(sec)}`;
      if (h > 0) return `${pad(h)}:${pad(m)}:${pad(sec)}`;
      return `${pad(m)}:${pad(sec)}`;
    }

    // 每秒更新所有倒计时（基于数据刷新时刻 + 流逝秒数）
    function updateCountdowns() {
      const elapsed = Date.now() / 1000 - lastRefreshAt;
      document.querySelectorAll('.i-cd').forEach(el => {
        const initial = parseInt(el.dataset.remain) || 0;
        el.textContent = formatCountdown(Math.max(0, initial - elapsed));
      });
      document.querySelectorAll('.w-cd').forEach(el => {
        const initial = parseInt(el.dataset.remain) || 0;
        el.textContent = formatCountdown(Math.max(0, initial - elapsed));
      });
    }

    function tick() {
      updateMeta();
      updateCountdowns();
      if (Date.now() >= nextRefresh) refresh();
    }

    // ---- Chart.js 折线图（24h 5h 已用值）----
    const COLORS = ['#38bdf8', '#fbbf24', '#f472b6', '#a78bfa', '#34d399',
                    '#fb7185', '#60a5fa', '#facc15', '#22d3ee', '#a3e635'];
    let chart = null;
    function buildChart(records) {
      // 拉取 24h × 60min = 1440 个点（按当前 60s 一次轮询的频率）
      const byKey = new Map();
      const cutoff = Date.now() - 24 * 60 * 60 * 1000;
      for (const r of (records || [])) {
        if (!r.ok || !r.models) continue;
        const t = Date.parse(r.ts);
        if (isNaN(t) || t < cutoff) continue;
        for (const m of r.models) {
          // 用 5h 已用百分比（100 - remaining_percent），比绝对值更有参考意义
          // 兼容 mobile view 精简字段（m.rp）和完整字段（m.interval.remaining_percent）
          const rem = (m.rp != null) ? m.rp : (m.interval || {}).remaining_percent;
          if (typeof rem !== 'number') continue;
          const usedPct = Math.max(0, Math.min(100, 100 - rem));
          const k = `${r.alias}/${m.name}`;
          if (!byKey.has(k)) byKey.set(k, []);
          byKey.get(k).push({ x: t, y: usedPct });
        }
      }
      // 按时间排序
      for (const arr of byKey.values()) arr.sort((a, b) => a.x - b.x);

      const datasets = [...byKey.entries()].map(([label, points], i) => {
        const color = COLORS[i % COLORS.length];
        return {
          label,
          data: points,
          borderColor: color,
          backgroundColor: color + '20',
          tension: 0.25,
          pointRadius: 0,
          pointHoverRadius: 3,
          borderWidth: 1.5,
          spanGaps: true,
        };
      });

      if (chart) { chart.destroy(); chart = null; }
      const ctx = document.getElementById('trendChart');
      if (!ctx) return;
      chart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: { mode: 'nearest', intersect: false, axis: 'x' },
          plugins: {
            legend: {
              display: true, position: 'top', align: 'end',
              labels: {
                color: '#aaa', font: { size: 10, family: 'monospace' },
                boxWidth: 8, boxHeight: 8, padding: 8, usePointStyle: true,
              },
            },
            tooltip: {
              backgroundColor: '#000', borderColor: '#333', borderWidth: 1,
              titleColor: '#fff', bodyColor: '#ccc',
              titleFont: { size: 11 }, bodyFont: { size: 11 },
              callbacks: {
                title: (items) => {
                  const t = items[0].parsed.x;
                  const d = new Date(t);
                  return d.toLocaleString('zh-CN', {
                    month: '2-digit', day: '2-digit',
                    hour: '2-digit', minute: '2-digit',
                  });
                },
                label: (item) => `${item.dataset.label}: ${item.parsed.y}`,
              },
            },
          },
          scales: {
            x: {
              type: 'time',
              min: Date.now() - 24 * 60 * 60 * 1000,
              max: Date.now(),
              time: {
                displayFormats: { hour: 'HH:mm', day: 'MM-dd HH:mm' },
                tooltipFormat: 'MM-dd HH:mm',
              },
              ticks: {
                color: '#888', maxRotation: 0, autoSkip: true, maxTicksLimit: 6,
                font: { size: 10 },
                source: 'auto',
              },
              grid: { color: '#1a1a1a', drawTicks: false },
            },
            y: {
              beginAtZero: true,
              suggestedMax: 100,
              ticks: {
                color: '#888', font: { size: 10 },
                callback: (v) => v + '%',
                precision: 0,
              },
              grid: { color: '#1a1a1a' },
              title: { display: true, text: '5h 已用 %', color: '#666',
                       font: { size: 10 } },
            },
          },
        },
      });
    }

    // 启动前先恢复用户上次选择的 alias（localStorage）
    loadSelectedAlias();
    refresh();
    updateMeta();
    setInterval(tick, 1000);
  </script>
</body>
</html>
"""


def main() -> None:
    import argparse
    import socket
    parser = argparse.ArgumentParser(description="Minimax 监控 Web 服务")
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址，默认 0.0.0.0（iPhone LAN 可访问）。如需锁本地传 127.0.0.1")
    parser.add_argument("--port", type=int, default=5050,
                        help="监听端口，默认 5050（macOS Monterey+ 的 AirPlay Receiver 占用了 5000 端口，"
                             "使用 5000 会导致页面打不开或被 AirPlay 接管）")
    parser.add_argument("--data-file", default="data/usage.ndjson")
    parser.add_argument("--config", default="config.json",
                        help="配置文件路径（用于后台 poller）")
    parser.add_argument("--no-poller", action="store_true",
                        help="禁用后台 poller（假设由 monitor.py 或其他进程写 NDJSON）")
    args = parser.parse_args()

    # 启动前检测端口冲突
    def _check_port(host: str, port: int) -> str | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
            return None
        except OSError as e:
            return f"{type(e).__name__}: {e}"

    err = _check_port("0.0.0.0", args.port)
    if err:
        print(f"❌ 端口 {args.port} 不可用: {err}")
        print(f"   💡 提示: macOS AirPlay Receiver 默认占用 5000 端口")
        print(f"   💡 解决: ./start.sh web --port 8080  （或 5050、8000 等）")
        print(f"   💡 或关闭: 系统设置 → 通用 → 隔空播放接收器 → 关闭")
        return

    # 启动后台 poller
    poller = None
    if not args.no_poller:
        try:
            cfg_path = Path(args.config)
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            else:
                cfg = {}
            if cfg.get("keys"):
                poller = BackgroundPoller(cfg, Path(args.data_file))
                poller.start()
                print(f"🔄 后台 poller 已启动: {len(cfg.get('keys', []))} keys, "
                      f"{cfg.get('poll_interval_seconds', 60)}s/次")
            else:
                print("⚠️  config.json 中没有 keys 配置，禁用 poller")
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            print(f"⚠️  poller 启动失败: {e}")
            print(f"   提示: 用 --no-poller 跳过；或修复 {args.config}")

    # 加载或生成访问 key
    access_key = _load_or_create_access_key()
    app = create_app(Path(args.data_file), poller=poller, access_key=access_key)

    # 探测本机 LAN IP
    lan_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except OSError:
        pass

    print(f"🚀 MinimaxGuard 启动中: http://{args.host}:{args.port}")
    print(f"   访问 key:  {access_key}")
    print(f"   桌面端:    http://localhost:{args.port}/mobile/{access_key}/")
    print(f"   手机端:    http://{lan_ip}:{args.port}/mobile/{access_key}/   ← iPhone Safari 打开")
    print(f"   公开 URL:  https://myhaixian.top/minimax/mobile/{access_key}/")
    print(f"   ⚠️  key 已保存到 {DEFAULT_KEY_FILE} (chmod 600)，重启后保持不变")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
