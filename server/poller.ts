/**
 * 后台 poller：定时调用 Minimax API 写入 NDJSON
 * 替代 Python 的 BackgroundPoller 类
 */
import axios, { AxiosError } from 'axios';
import { EventEmitter } from 'node:events';
import { parseApiResponse } from './utils.js';
import type { UsageStore } from './store.js';
import type { AppConfig, KeyConfig, UsageRecord } from './types.js';

export interface PollerEvents {
  /** 单条新记录产生 */
  record: (record: UsageRecord) => void;
  /** 一次完整轮询结束（不论成功失败） */
  tick: (timestamp: string) => void;
  /** 发生错误 */
  error: (err: Error) => void;
}

export declare interface BackgroundPoller {
  on<E extends keyof PollerEvents>(event: E, listener: PollerEvents[E]): this;
  emit<E extends keyof PollerEvents>(
    event: E,
    ...args: Parameters<PollerEvents[E]>
  ): boolean;
}

export class BackgroundPoller extends EventEmitter {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private lastPollAt: string | undefined;
  private lastPollOk: boolean | undefined;
  private inFlight = false;
  private startTime = Date.now();
  private pollCount = 0;

  constructor(
    private config: AppConfig,
    private store: UsageStore
  ) {
    super();
  }

  /** 启动定时轮询 */
  start(): void {
    if (this.running) return;
    this.running = true;
    console.log(`[poller] started, interval=${this.config.poll_interval_seconds}s, keys=${this.config.keys.length}`);
    // 立即执行一次，之后按 interval 循环
    this.scheduleNext(0);
  }

  /** 停止轮询 */
  stop(): void {
    this.running = false;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  /** 是否正在运行 */
  isRunning(): boolean {
    return this.running;
  }

  /** 调度下一次轮询（recursive setTimeout，避免重叠执行） */
  private scheduleNext(delayMs: number): void {
    if (!this.running) return;
    this.timer = setTimeout(() => this.runOnce(), delayMs);
  }

  /** 单次轮询全部 key（并发） */
  async runOnce(): Promise<void> {
    if (this.inFlight) {
      // 重叠检测：上一轮未结束就跳过（防御性，正常情况不会触发）
      this.scheduleNext(this.config.poll_interval_seconds * 1000);
      return;
    }
    this.inFlight = true;
    this.pollCount++;
    const startTime = Date.now();
    const enabledKeys = this.config.keys.filter((k) => k.enabled !== false);

    // 并发调用所有 key
    const results = await Promise.allSettled(
      enabledKeys.map((k) => this.pollOne(k))
    );

    this.inFlight = false;
    this.lastPollAt = new Date().toISOString();
    this.lastPollOk = results.every((r) => r.status === 'fulfilled' && r.value.ok);

    if (results.some((r) => r.status === 'rejected')) {
      const errs = results
        .filter((r) => r.status === 'rejected')
        .map((r) => (r as PromiseRejectedResult).reason?.message || 'unknown');
      this.emit('error', new Error(`Some keys failed: ${errs.join('; ')}`));
    }

    const elapsed = Date.now() - startTime;
    if (elapsed > 5000) {
      console.warn(`[poller] slow tick: ${elapsed}ms for ${enabledKeys.length} keys`);
    }

    this.emit('tick', this.lastPollAt);
    this.scheduleNext(this.config.poll_interval_seconds * 1000);
  }

  /** 轮询单个 key */
  private async pollOne(key: KeyConfig): Promise<UsageRecord> {
    const start = Date.now();
    try {
      const resp = await axios.get(this.config.api_url, {
        headers: {
          Authorization: `Bearer ${key.token}`,
          'Content-Type': 'application/json',
        },
        timeout: (this.config.request_timeout_seconds || 15) * 1000,
        // 关闭 axios 默认重试（避免占用 60s 窗口）
        validateStatus: (s) => s >= 200 && s < 300,
      });
      const pollMs = Date.now() - start;
      const { ok, record } = parseApiResponse(key.alias, resp.data, pollMs);
      // 失败注入 alias
      record.alias = key.alias;
      if (!ok) {
        console.warn(`[poller] ${key.alias}: parse failed`);
      }
      await this.store.append(record);
      this.emit('record', record);
      return record;
    } catch (e) {
      const pollMs = Date.now() - start;
      const err = e as AxiosError;
      const errMsg = err.response
        ? `HTTP ${err.response.status}: ${err.response.statusText}`
        : err.code === 'ECONNABORTED'
        ? `timeout after ${this.config.request_timeout_seconds}s`
        : err.message || 'unknown';
      const record: UsageRecord = {
        timestamp: new Date().toISOString(),
        alias: key.alias,
        ok: false,
        error: errMsg,
        poll_ms: pollMs,
      };
      await this.store.append(record);
      this.emit('record', record);
      console.warn(`[poller] ${key.alias}: ${errMsg}`);
      return record;
    }
  }

  /** 诊断 */
  diag(): { running: boolean; last_poll_at?: string; last_poll_ok?: boolean; poll_count: number; uptime_seconds: number } {
    return {
      running: this.running,
      last_poll_at: this.lastPollAt,
      last_poll_ok: this.lastPollOk,
      poll_count: this.pollCount,
      uptime_seconds: Math.floor((Date.now() - this.startTime) / 1000),
    };
  }
}
