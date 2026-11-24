/** Local WebSocket bridge. A shared token is required for every client. */
import { createHash, timingSafeEqual } from 'crypto';
import { WebSocketServer, WebSocket } from 'ws';
import { WhatsAppClient } from './whatsapp.js';

interface SendCommand { type: 'send'; to: string; text: string; }
interface BridgeMessage { type: 'message' | 'status' | 'qr' | 'error'; [key: string]: unknown; }

export class BridgeServer {
  private wss: WebSocketServer | null = null;
  private wa: WhatsAppClient | null = null;
  private clients: Set<WebSocket> = new Set();

  constructor(private port: number, private authDir: string, private token?: string) {}

  async start(): Promise<void> {
    if (typeof this.token !== 'string' || !this.token.trim()) {
      throw new Error('BRIDGE_TOKEN is required');
    }
    this.wss = new WebSocketServer({ host: '127.0.0.1', port: this.port, maxPayload: 65536 });
    console.log(`Bridge listening on ws://127.0.0.1:${this.port} (authentication required)`);
    this.wa = new WhatsAppClient({
      authDir: this.authDir,
      onMessage: (msg) => this.broadcast({ type: 'message', ...msg }),
      onQR: (qr) => this.broadcast({ type: 'qr', qr }),
      onStatus: (status) => this.broadcast({ type: 'status', status }),
    });
    this.wss.on('connection', (ws) => {
      const timeout = setTimeout(() => ws.terminate(), 5000);
      ws.on('close', () => clearTimeout(timeout));
      // Install an error handler even before authentication.
      ws.on('error', () => { clearTimeout(timeout); this.clients.delete(ws); });
      ws.once('message', (data) => {
        clearTimeout(timeout);
        try {
          const msg = JSON.parse(data.toString());
          const actual = typeof msg?.token === 'string' ? msg.token : '';
          const digest = (value: string) => createHash('sha256').update(value).digest();
          if (msg?.type !== 'auth' || !timingSafeEqual(digest(actual), digest(this.token!))) {
            ws.close(4003, 'Authentication failed');
            return;
          }
          this.setupClient(ws);
          ws.send(JSON.stringify({ type: 'auth_ok' }));
        } catch {
          ws.close(4003, 'Authentication failed');
        }
      });
    });
    await this.wa.connect();
  }

  private setupClient(ws: WebSocket): void {
    this.clients.add(ws);
    ws.on('message', async (data) => {
      try {
        const cmd = JSON.parse(data.toString()) as SendCommand;
        if (!cmd || cmd.type !== 'send' || typeof cmd.to !== 'string' || !cmd.to ||
            typeof cmd.text !== 'string' || !cmd.text.trim() || cmd.text.length > 4096 || !this.wa) {
          throw new Error('Invalid command');
        }
        await this.wa.sendMessage(cmd.to, cmd.text);
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'sent', to: cmd.to }));
      } catch {
        console.error('Bridge command failed');
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'error', error: 'Command failed' }));
      }
    });
    ws.on('close', () => this.clients.delete(ws));
  }

  private broadcast(msg: BridgeMessage): void {
    const data = JSON.stringify(msg);
    for (const client of this.clients) {
      if (client.readyState === WebSocket.OPEN) client.send(data);
    }
  }

  async stop(): Promise<void> {
    // Include unauthenticated sockets so they cannot prevent shutdown.
    if (this.wss) {
      for (const client of this.wss.clients) client.terminate();
      this.wss.close();
      this.wss = null;
    }
    this.clients.clear();
    if (this.wa) {
      await this.wa.disconnect();
      this.wa = null;
    }
  }
}
