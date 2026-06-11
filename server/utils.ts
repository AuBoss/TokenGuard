/**
 * 工具函数：NDJSON 读写、访问 key 管理
 */
import { promises as fs } from 'node:fs';
import * as fsSync from 'node:fs';
import * as path from 'node:path';
import crypto from 'node:crypto';

/**
 * 加载访问 key；不存在则生成 32 字符 URL-safe 随机 key
 * 权限 chmod 600
 */
export function loadOrCreateAccessKey(keyFile: string): string {
  if (fsSync.existsSync(keyFile)) {
    const existing = fsSync.readFileSync(keyFile, 'utf-8').trim();
    if (existing && existing.length >= 16 && existing.length <= 128) {
      return existing;
    }
  }
  // 生成新 key：32 字符 URL-safe
  const key = crypto.randomBytes(24).toString('base64')
    .replace(/\+/g, 'a').replace(/\//g, 'b').replace(/=/g, 'c')
    .slice(0, 32);
  fsSync.mkdirSync(path.dirname(keyFile), { recursive: true });
  fsSync.writeFileSync(keyFile, key, { mode: 0o600 });
  return key;
}

/**
 * 读取 NDJSON 文件全部记录
 * 启动时调用一次，加载历史到内存
 */
export async function loadNDJSON<T = unknown>(filePath: string): Promise<T[]> {
  try {
    const content = await fs.readFile(filePath, 'utf-8');
    const records: T[] = [];
    for (const line of content.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        records.push(JSON.parse(trimmed) as T);
      } catch {
        // 跳过损坏行（不中断）
      }
    }
    return records;
  } catch (e: unknown) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') {
      return [];
    }
    throw e;
  }
}

/**
 * 追加单条记录到 NDJSON 文件
 * 每次轮询产生一条新记录
 */
export async function appendNDJSON(filePath: string, record: unknown): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const line = JSON.stringify(record) + '\n';
  await fs.appendFile(filePath, line, 'utf-8');
}

/**
 * 解析并精简 Minimax API 响应为 UsageRecord
 * 兼容多种字段命名（不同 API 版本）
 */
export function parseApiResponse(
  alias: string,
  data: Record<string, unknown> | null | undefined,
  pollMs?: number
): { ok: boolean; record: import('./types.js').UsageRecord } {
  if (!data || typeof data !== 'object') {
    return {
      ok: false,
      record: {
        timestamp: new Date().toISOString(),
        alias,
        ok: false,
        error: 'Empty or invalid response',
        poll_ms: pollMs,
      },
    };
  }

  // 找到 model_remains 数组（兼容多种字段名）
  const models: any[] =
    (data.model_remains as any[]) ||
    (data.models as any[]) ||
    (data.data as any)?.model_remains ||
    [];

  const parsedModels = models.map((m: any) => {
    const interval = m.interval || {};
    const weekly = m.weekly || {};
    return {
      name: m.name || m.model || 'unknown',
      interval: {
        used: interval.used,
        total: interval.total,
        remaining_percent: interval.remaining_percent,
        end_time: interval.end_time,
        // 兼容：API 返回 ms 的话除以 1000
        remains_time_seconds: Math.floor((interval.remains_time || 0) / 1000),
      },
      weekly: {
        used: weekly.used,
        total: weekly.total,
        remaining_percent: weekly.remaining_percent,
        end_time: weekly.end_time,
        remains_time_seconds: Math.floor((weekly.remains_time || 0) / 1000),
      },
    };
  });

  return {
    ok: true,
    record: {
      timestamp: new Date().toISOString(),
      alias,
      ok: true,
      models: parsedModels,
      poll_ms: pollMs,
    },
  };
}

/**
 * 精简 record 用于 API 响应（去掉不必要的字段）
 */
export function simplifyRecord<T extends Record<string, any>>(r: T): unknown {
  const out: Record<string, unknown> = {
    alias: r.alias,
    ts: (r as any).timestamp || (r as any).ts,
    ok: (r as any).ok,
  };
  if (!(r as any).ok && (r as any).error) {
    out.error = (r as any).error;
  }
  if (Array.isArray(r.models)) {
    out.models = r.models.map((m: any) => ({
      name: m.name,
      interval: m.interval
        ? {
            used: m.interval.used,
            total: m.interval.total,
            remaining_percent: m.interval.remaining_percent,
            end_time: m.interval.end_time,
            remains_time_seconds: m.interval.remains_time_seconds,
          }
        : undefined,
      weekly: m.weekly
        ? {
            used: m.weekly.used,
            total: m.weekly.total,
            remaining_percent: m.weekly.remaining_percent,
            end_time: m.weekly.end_time,
            remains_time_seconds: m.weekly.remains_time_seconds,
          }
        : undefined,
    }));
  }
  return out;
}
