/**
 * 数据访问层：内存缓存 + NDJSON 持久化
 * 提供 records 列表、追加、查询接口
 */
import { promises as fs } from 'node:fs';
import * as path from 'node:path';
import { appendNDJSON, loadNDJSON } from './utils.js';
import type { UsageRecord } from './types.js';

export class UsageStore {
  private records: UsageRecord[] = [];
  private dataFile: string;
  private historyLimit: number;
  private dirty = false;

  constructor(dataFile: string, historyLimit = 50000) {
    this.dataFile = dataFile;
    this.historyLimit = historyLimit;
  }

  /** 启动时加载历史（尾部 4MB 以加快启动） */
  async load(): Promise<number> {
    try {
      // 启动时读全量（Python 版也改了）
      this.records = await loadNDJSON<UsageRecord>(this.dataFile);
      // 截断到 historyLimit
      if (this.records.length > this.historyLimit) {
        this.records = this.records.slice(-this.historyLimit);
      }
      return this.records.length;
    } catch {
      this.records = [];
      return 0;
    }
  }

  /** 追加一条新记录（同步内存 + 异步落盘） */
  async append(record: UsageRecord): Promise<void> {
    this.records.push(record);
    if (this.records.length > this.historyLimit) {
      this.records = this.records.slice(-this.historyLimit);
    }
    try {
      await appendNDJSON(this.dataFile, record);
    } catch (e) {
      console.error(`[store] append failed: ${(e as Error).message}`);
    }
  }

  /** 返回全部记录的副本 */
  all(): UsageRecord[] {
    return [...this.records];
  }

  /** 返回最近 N 条 */
  recent(n: number): UsageRecord[] {
    return this.records.slice(-n);
  }

  /** 过滤 alias */
  byAlias(alias: string): UsageRecord[] {
    return this.records.filter((r) => r.alias === alias);
  }

  /** 数据文件大小（字节） */
  async fileSize(): Promise<number> {
    try {
      const stat = await fs.stat(this.dataFile);
      return stat.size;
    } catch {
      return 0;
    }
  }

  /** 诊断信息 */
  diag(): { exists: boolean; record_count: number; data_file: string; history_limit: number } {
    return {
      exists: this.records.length > 0,
      record_count: this.records.length,
      data_file: this.dataFile,
      history_limit: this.historyLimit,
    };
  }

  /** 截断内存（不删文件），用于按需调整 history_limit */
  truncate(limit: number): void {
    this.historyLimit = limit;
    if (this.records.length > limit) {
      this.records = this.records.slice(-limit);
    }
  }
}
