/**
 * MinimaxGuard 共享类型定义
 * 与原 Python 版（app.py）保持字段一致，确保 NDJSON 数据兼容
 */

/** Minimax API 原始窗口（interval/weekly） */
export interface ApiWindow {
  used?: number;
  total?: number;
  remaining_percent?: number;
  end_time?: string;
  /** 单位：秒（已从 ms 转换） */
  remains_time_seconds?: number;
}

/** Minimax API 单个 model 返回 */
export interface ApiModelUsage {
  name: string;
  interval?: ApiWindow;
  weekly?: ApiWindow;
}

/** Minimax API 单个 alias 返回 */
export interface ApiAliasResponse {
  model_remains?: ApiModelUsage[];
  // 备用字段（不同 API 版本兼容）
  [key: string]: unknown;
}

/** NDJSON 持久化的单条记录 */
export interface UsageRecord {
  timestamp: string;       // ISO 8601
  alias: string;
  ok: boolean;
  models?: ApiModelUsage[];
  error?: string;
  poll_ms?: number;        // 本次轮询耗时
}

/** 内存中累积的历史（截断到 history_limit） */
export interface HistoryStore {
  records: UsageRecord[];
}

/** 配置文件中单个 key */
export interface KeyConfig {
  alias: string;
  token: string;
  tags?: string[];
  enabled?: boolean;
}

/** 完整配置 */
export interface AppConfig {
  api_url: string;
  poll_interval_seconds: number;
  request_timeout_seconds: number;
  data_file: string;
  history_limit: number;
  keys: KeyConfig[];
  /** 访问 key（必填，部署时生成） */
  access_key?: string;
}

/** API 响应：单 key 最新状态（精简） */
export interface KeyLatestResponse {
  alias: string;
  ok: boolean;
  error?: string;
  models: ApiModelUsage[];
  fetched_at: string;
}

/** API 响应：诊断信息 */
export interface DiagResponse {
  exists: boolean;
  record_count: number;
  data_file: string;
  history_limit: number;
  poller_running: boolean;
  last_poll_at?: string;
  last_poll_ok?: boolean;
  keys_count: number;
  uptime_seconds: number;
}
