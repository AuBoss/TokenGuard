/**
 * 配置管理：增删 alias、读写 config.json
 * 本地模式（--desktop）专用，让用户在 UI 里管理 token
 */
import { promises as fs } from 'node:fs';
import * as fsSync from 'node:fs';
import * as path from 'node:path';
import type { AppConfig, KeyConfig } from './types.js';

export class ConfigManager {
  constructor(private configPath: string) {}

  /** 读取 config.json（不存在则返回空 config） */
  async load(): Promise<AppConfig> {
    try {
      const content = await fs.readFile(this.configPath, 'utf-8');
      return JSON.parse(content) as AppConfig;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === 'ENOENT') {
        // 首次启动：返回空 config
        return {
          api_url: 'https://www.minimaxi.com/v1/token_plan/remains',
          poll_interval_seconds: 60,
          request_timeout_seconds: 15,
          data_file: 'data/usage.ndjson',
          history_limit: 50000,
          keys: [],
        };
      }
      throw e;
    }
  }

  /** 写入 config.json（原子：先写 .tmp 再 rename） */
  async save(config: AppConfig): Promise<void> {
    fsSync.mkdirSync(path.dirname(this.configPath), { recursive: true});
    const tmp = this.configPath + '.tmp';
    await fs.writeFile(tmp, JSON.stringify(config, null, 2), 'utf-8');
    await fs.rename(tmp, this.configPath);
  }

  /** 添加 alias */
  async addKey(alias: string, token: string, tags: string[] = []): Promise<AppConfig> {
    const cfg = await this.load();
    // 唯一性检查
    if (cfg.keys.some((k) => k.alias === alias)) {
      throw new Error(`Alias "${alias}" already exists`);
    }
    cfg.keys.push({ alias, token, tags, enabled: true });
    await this.save(cfg);
    return cfg;
  }

  /** 更新 alias（修改 token / tags / enabled） */
  async updateKey(alias: string, patch: Partial<KeyConfig>): Promise<AppConfig> {
    const cfg = await this.load();
    const idx = cfg.keys.findIndex((k) => k.alias === alias);
    if (idx < 0) throw new Error(`Alias "${alias}" not found`);
    cfg.keys[idx] = { ...cfg.keys[idx], ...patch };
    await this.save(cfg);
    return cfg;
  }

  /** 删除 alias */
  async removeKey(alias: string): Promise<AppConfig> {
    const cfg = await this.load();
    cfg.keys = cfg.keys.filter((k) => k.alias !== alias);
    await this.save(cfg);
    return cfg;
  }
}
