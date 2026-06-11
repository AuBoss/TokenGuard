/**
 * WebSocket 实时推送
 * - 订阅 poller 的 'record' 事件，单条新记录产生即广播
 * - 客户端订阅协议：发 "subscribe" / "unsubscribe" / "ping"
 * - 鉴权：通过 URL ?key=XXX 或首个消息 {type:'auth', key:XXX}
 */
import { WebSocketServer, WebSocket } from 'ws';
import type { Server } from 'node:http';
import type { BackgroundPoller } from './poller.js';
import type { UsageRecord } from './types.js';

export interface WSClient {
  ws: WebSocket;
  authenticated: boolean;
  subscribedAliases: Set<string> | 'all';
}

export function attachWebSocket(
  httpServer: Server,
  poller: BackgroundPoller,
  accessKey: string
): WebSocketServer {
  const wss = new WebSocketServer({ noServer: true });
  const clients = new Set<WSClient>();

  // poller 推一条新记录 → 广播给所有已认证 + 订阅的客户端
  poller.on('record', (record: UsageRecord) => {
    const payload = JSON.stringify({
      type: 'usage_update',
      record: {
        alias: record.alias,
        ts: record.timestamp,
        ok: record.ok,
        error: record.error,
        models: record.models,
      },
    });
    for (const c of clients) {
      if (!c.authenticated) continue;
      if (c.ws.readyState !== WebSocket.OPEN) continue;
      // 过滤订阅
      if (c.subscribedAliases !== 'all' && !c.subscribedAliases.has(record.alias)) continue;
      try {
        c.ws.send(payload);
      } catch {
        // ignore
      }
    }
  });

  // HTTP upgrade 处理（避免与 express 路由冲突）
  httpServer.on('upgrade', (req, socket, head) => {
    // 只处理 /ws 路径
    if (!req.url?.startsWith('/ws')) {
      socket.destroy();
      return;
    }
    // URL 参数鉴权
    const url = new URL(req.url, `http://${req.headers.host}`);
    const urlKey = url.searchParams.get('key');
    let preAuthed = urlKey === accessKey;

    wss.handleUpgrade(req, socket, head, (ws) => {
      const client: WSClient = {
        ws,
        authenticated: preAuthed,
        subscribedAliases: 'all',
      };
      clients.add(client);
      console.log(`[ws] client connected (auth=${preAuthed}), total=${clients.size}`);

      ws.send(JSON.stringify({ type: 'hello', auth_required: !preAuthed }));

      ws.on('message', (raw) => {
        let msg: any;
        try {
          msg = JSON.parse(raw.toString());
        } catch {
          ws.send(JSON.stringify({ type: 'error', message: 'invalid JSON' }));
          return;
        }
        switch (msg.type) {
          case 'auth':
            if (msg.key === accessKey) {
              client.authenticated = true;
              ws.send(JSON.stringify({ type: 'auth_ok' }));
            } else {
              ws.send(JSON.stringify({ type: 'error', message: 'invalid key' }));
              ws.close(4001, 'auth failed');
            }
            break;
          case 'subscribe':
            if (msg.aliases === 'all' || msg.aliases == null) {
              client.subscribedAliases = 'all';
            } else if (Array.isArray(msg.aliases)) {
              client.subscribedAliases = new Set(msg.aliases);
            }
            ws.send(JSON.stringify({
              type: 'subscribed',
              aliases: client.subscribedAliases === 'all' ? 'all' : [...client.subscribedAliases],
            }));
            break;
          case 'unsubscribe':
            client.subscribedAliases = 'all';
            ws.send(JSON.stringify({ type: 'unsubscribed' }));
            break;
          case 'ping':
            ws.send(JSON.stringify({ type: 'pong', ts: Date.now() }));
            break;
          case 'request_poll':
            // 客户端连接后请求立即推送一次最新数据
            // 复用 poller.runOnce() —— 调真实 API → 触发 'record' 事件 → 自动广播
            if (client.authenticated) {
              ws.send(JSON.stringify({ type: 'poll_triggered' }));
              poller.runOnce().catch((e) => {
                console.error(`[ws] runOnce failed: ${e.message}`);
              });
            } else {
              ws.send(JSON.stringify({ type: 'error', message: 'auth required' }));
            }
            break;
          default:
            ws.send(JSON.stringify({ type: 'error', message: `unknown type: ${msg.type}` }));
        }
      });

      ws.on('close', () => {
        clients.delete(client);
        console.log(`[ws] client closed, remaining=${clients.size}`);
      });

      ws.on('error', (err) => {
        console.error(`[ws] client error: ${err.message}`);
      });
    });
  });

  return wss;
}
