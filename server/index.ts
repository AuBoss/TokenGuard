/**
 * MinimaxGuard Node.js 主程序
 * - Express HTTP API
 * - 后台 poller（60s 轮询）
 * - 静态文件（mobile.html / desktop.html）
 * - WebSocket 实时推送（Phase 4）
 */
import express, { type Request, type Response, type NextFunction } from 'express';
import compression from 'compression';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promises as fs } from 'node:fs';
import { createServer } from 'node:http';

import { loadOrCreateAccessKey, simplifyRecord } from './utils.js';
import { UsageStore } from './store.js';
import { BackgroundPoller } from './poller.js';
import { attachWebSocket } from './ws.js';
import type { AppConfig, UsageRecord } from './types.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, '..');

// ---- 加载配置 ----
const CONFIG_PATH = process.env.CONFIG_PATH || path.join(PROJECT_ROOT, 'config.json');
const KEY_FILE = process.env.KEY_FILE || path.join(PROJECT_ROOT, 'data/access_key');
const DATA_FILE = process.env.DATA_FILE || path.join(PROJECT_ROOT, 'data/usage.ndjson');
const PUBLIC_DIR = path.join(PROJECT_ROOT, 'public');
const TEMPLATE_DIR = path.join(PROJECT_ROOT, 'templates');

async function loadConfig(): Promise<AppConfig> {
  try {
    const content = await fs.readFile(CONFIG_PATH, 'utf-8');
    return JSON.parse(content) as AppConfig;
  } catch (e) {
    console.error(`[config] failed to load ${CONFIG_PATH}: ${(e as Error).message}`);
    console.error('[config] copy config.example.json to config.json and edit it');
    process.exit(1);
  }
}

// ---- 中间件：访问 key 验证 ----
function keyAuthMiddleware(accessKey: string) {
  return (req: Request, res: Response, next: NextFunction): void => {
    // 跳过健康检查
    if (req.path === '/api/health' || req.path === '/health') {
      return next();
    }
    // 从 URL 路径提取 key（兼容 /<key>/ 或 /<key> 形式）
    // 例如 /minimax/mobile/lkUa.../ 或 /minimax/api/... 内的 lkUa...
    const urlKey = extractKeyFromUrl(req.path, accessKey);
    if (urlKey === accessKey) {
      // 把 PREFIX 剥离到 PATH_INFO，让 Flask 风格路由正常工作
      rewritePrefix(req, urlKey);
      return next();
    }
    // Header 中也允许（curl 调用方便）
    if (req.headers['x-access-key'] === accessKey) {
      return next();
    }
    res.status(404).send('Not Found');
  };
}

function extractKeyFromUrl(urlPath: string, validKey: string): string | null {
  // 匹配 /<32+ chars> 出现在路径任意位置
  const m = urlPath.match(/\/([A-Za-z0-9_-]{16,128})/);
  if (m && m[1] === validKey) return m[1];
  return null;
}

function rewritePrefix(req: Request, key: string): void {
  // 把 /<key> 段从路径中任意位置剥掉（如 /api/keys/<KEY>/ → /api/keys/）
  let u = req.url.replace(`/${key}`, '') || '/';
  // 兼容 nginx /minimax/ 反代（去前缀后可能残留 /minimax/）
  u = u.replace(/^\/minimax\//, '/');
  req.url = u;
}

// ---- 主程序 ----
async function main(): Promise<void> {
  const config = await loadConfig();
  const accessKey = loadOrCreateAccessKey(KEY_FILE);
  console.log(`[init] access key loaded (${accessKey.length} chars)`);

  // 数据存储
  const store = new UsageStore(DATA_FILE, config.history_limit || 50000);
  const loadedCount = await store.load();
  console.log(`[init] loaded ${loadedCount} historical records from ${DATA_FILE}`);

  // 后台 poller
  const poller = new BackgroundPoller(config, store);
  poller.on('error', (e) => console.error(`[poller-error] ${e.message}`));
  poller.start();

  // Express app
  const app = express();
  app.use(compression());
  app.use(keyAuthMiddleware(accessKey));

  // 健康检查
  app.get('/api/health', (_req, res) => {
    res.json({ ok: true, uptime: Math.floor((Date.now() - startTime) / 1000) });
  });

  // 诊断信息
  app.get('/api/diag', (_req, res) => {
    res.json({
      ...store.diag(),
      poller_running: poller.isRunning(),
      ...poller.diag(),
      keys_count: config.keys.length,
    });
  });

  // 当前所有 key 的最新状态
  app.get('/api/keys', (_req, res) => {
    const latest = new Map<string, UsageRecord>();
    for (const r of store.recent(500)) {
      if (!latest.has(r.alias) || latest.get(r.alias)!.timestamp < r.timestamp) {
        latest.set(r.alias, r);
      }
    }
    const out: any[] = [];
    for (const k of config.keys) {
      const r = latest.get(k.alias);
      if (r) {
        out.push(simplifyRecord(r));
      } else {
        out.push({
          alias: k.alias,
          ts: null,
          ok: false,
          error: 'No data yet (poller just started)',
        });
      }
    }
    res.json({ keys: out, count: out.length });
  });

  // 历史数据
  app.get('/api/history', (req, res) => {
    const limit = Math.min(parseInt((req.query.limit as string) || '2000', 10), 20000);
    const hours = parseInt((req.query.hours as string) || '0', 10);  // 0 = 不按时间过滤
    const aliasFilter = req.query.alias as string | undefined;
    const view = (req.query.view as string) || 'full';

    let records = store.all();
    if (aliasFilter) {
      records = records.filter((r) => r.alias === aliasFilter);
    }
    // 按时间窗口过滤（小时数）：chart 需要 24h 完整曲线
    if (hours > 0) {
      const cutoff = Date.now() - hours * 60 * 60 * 1000;
      records = records.filter((r) => {
        const t = Date.parse(r.timestamp);
        return !isNaN(t) && t >= cutoff;
      });
    }
    records = records.slice(-limit);

    if (view === 'mobile') {
      // mobile view: 只返回 5h 窗口的 used + remaining_percent
      const slim = records.map((r) => {
        const models = (r.models || []).map((m) => ({
          name: m.name,
          rp: m.interval?.remaining_percent,
          u: m.interval?.used,
        }));
        return {
          alias: r.alias,
          ts: r.timestamp,
          ok: r.ok,
          models: models.length ? models : undefined,
        };
      });
      return res.json({ records: slim, count: slim.length });
    }
    res.json({
      records: records.map((r) => simplifyRecord(r)),
      count: records.length,
    });
  });

  // 静态文件（HTML / CSS / JS / 图表）
  app.use(express.static(PUBLIC_DIR, { maxAge: 0, etag: false }));

  // 移动端首页
  app.get('/mobile', async (req, res) => {
    try {
      const html = await fs.readFile(path.join(TEMPLATE_DIR, 'mobile.html'), 'utf-8');
      // 注入 <KEY>（access key）和 <BASE_URL>（包含 key 的 URL 前缀）
      // 这样前端 fetch 知道在哪加 key，无需从 location.pathname 解析
      const baseUrl = `/${accessKey}`;
      const rendered = html
        .replace(/<KEY>/g, accessKey)
        .replace(/<BASE_URL>/g, baseUrl);
      res.set('Cache-Control', 'no-cache, no-store, must-revalidate');
      res.type('html').send(rendered);
    } catch (e) {
      res.status(500).send(`mobile.html not found: ${(e as Error).message}`);
    }
  });

  // 移动端 v2（不同文件名强制 iPhone 重新下载）
  app.get('/mobile2', async (req, res) => {
    try {
      const html = await fs.readFile(path.join(TEMPLATE_DIR, 'mobile2.html'), 'utf-8');
      const baseUrl = `/${accessKey}`;
      const rendered = html
        .replace(/<KEY>/g, accessKey)
        .replace(/<BASE_URL>/g, baseUrl);
      res.set('Cache-Control', 'no-cache, no-store, must-revalidate');
      res.type('html').send(rendered);
    } catch (e) {
      res.status(500).send(`mobile2.html not found: ${(e as Error).message}`);
    }
  });

  // 桌面端首页
  app.get('/', async (req, res) => {
    try {
      const html = await fs.readFile(path.join(TEMPLATE_DIR, 'desktop.html'), 'utf-8');
      const baseUrl = `/${accessKey}`;
      const rendered = html
        .replace(/<KEY>/g, accessKey)
        .replace(/<BASE_URL>/g, baseUrl);
      res.set('Cache-Control', 'no-cache, no-store, must-revalidate');
      res.type('html').send(rendered);
    } catch (e) {
      res.status(500).send(`desktop.html not found: ${(e as Error).message}`);
    }
  });

  // 404
  app.use((_req, res) => res.status(404).send('Not Found'));

  // 启动 HTTP + WebSocket
  const port = parseInt(process.env.PORT || '5050', 10);
  const host = process.env.HOST || '127.0.0.1';
  const httpServer = createServer(app);

  // 挂载 WebSocket（poller emit('record') → 推送到所有订阅客户端）
  attachWebSocket(httpServer, poller, accessKey);

  httpServer.listen(port, host, () => {
    console.log(`[server] listening on http://${host}:${port}`);
    console.log(`[server] mobile:   http://${host}:${port}/mobile/${accessKey}/`);
    console.log(`[server] ws:       ws://${host}:${port}/ws?key=${accessKey}`);
  });

  // 优雅关闭
  const shutdown = (sig: string) => {
    console.log(`\n[server] ${sig} received, shutting down...`);
    poller.stop();
    process.exit(0);
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

const startTime = Date.now();
main().catch((e) => {
  console.error(`[fatal] ${e.stack || e.message}`);
  process.exit(1);
});
