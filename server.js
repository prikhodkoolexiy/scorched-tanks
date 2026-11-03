// Scorched Tanks: сервер комнат. Без зависимостей, нужен только Node.js.
// Раздаёт index.html и пересылает сообщения между хостом и игроками по WebSocket.
const http = require('http'), fs = require('fs'), path = require('path'), crypto = require('crypto'), os = require('os');
const PORT = +process.env.PORT || 8787;
const rooms = new Map();

function addrs() {
  const pub = (process.env.PUBLIC_URL || '').trim().replace(/\/$/, '');
  const out = pub ? [pub] : [];
  for (const list of Object.values(os.networkInterfaces()))
    for (const i of list || []) if (i.family === 'IPv4' && !i.internal) out.push('http://' + i.address + ':' + PORT);
  return out;
}

const server = http.createServer((req, res) => {
  const u = req.url.split('?')[0];
  if (u === '/__tanks') { res.writeHead(200, { 'content-type': 'text/plain' }); return res.end('ok'); }
  if (u === '/__addrs') { res.writeHead(200, { 'content-type': 'application/json' }); return res.end(JSON.stringify(addrs())); }
  if (u === '/' || u === '/index.html') {
    return fs.readFile(path.join(__dirname, 'index.html'), (e, d) => {
      if (e) { res.writeHead(500); return res.end('index.html не найден рядом с server.js'); }
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      res.end(d);
    });
  }
  res.writeHead(404); res.end('not found');
});

function frame(str) {
  const p = Buffer.from(str); let h;
  if (p.length < 126) h = Buffer.from([0x81, p.length]);
  else if (p.length < 65536) { h = Buffer.alloc(4); h[0] = 0x81; h[1] = 126; h.writeUInt16BE(p.length, 2); }
  else { h = Buffer.alloc(10); h[0] = 0x81; h[1] = 127; h.writeBigUInt64BE(BigInt(p.length), 2); }
  return Buffer.concat([h, p]);
}

server.on('upgrade', (req, socket) => {
  const key = req.headers['sec-websocket-key'];
  if (req.url.split('?')[0] !== '/ws' || !key) { socket.destroy(); return; }
  const accept = crypto.createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
  socket.setNoDelay(true);
  const c = { socket, buf: Buffer.alloc(0), room: null, id: null, send(o) { if (!socket.destroyed) socket.write(frame(JSON.stringify(o))); } };
  socket.on('data', d => { c.buf = Buffer.concat([c.buf, d]); parse(c); });
  socket.on('close', () => drop(c));
  socket.on('error', () => { try { socket.destroy(); } catch (e) { /* ignore */ } });
});

function parse(c) {
  for (;;) {
    const b = c.buf; if (b.length < 2) return;
    const op = b[0] & 15, masked = !!(b[1] & 128); let len = b[1] & 127, off = 2;
    if (len === 126) { if (b.length < 4) return; len = b.readUInt16BE(2); off = 4; }
    else if (len === 127) { if (b.length < 10) return; len = Number(b.readBigUInt64BE(2)); off = 10; }
    const need = off + (masked ? 4 : 0) + len; if (b.length < need) return;
    let payload = b.subarray(off + (masked ? 4 : 0), need);
    if (masked) { const m = b.subarray(off, off + 4); payload = Buffer.from(payload); for (let i = 0; i < payload.length; i++) payload[i] ^= m[i % 4]; }
    c.buf = b.subarray(need);
    if (op === 8) { c.socket.end(); return; }
    if (op === 9) { c.socket.write(Buffer.concat([Buffer.from([0x8a, payload.length]), payload])); continue; }
    if (op === 1) { let m = null; try { m = JSON.parse(payload.toString('utf8')); } catch (e) { /* ignore */ } if (m) handle(c, m); }
  }
}

function newCode() {
  const A = 'ABCDEFGHJKMNPQRSTUVWXYZ';
  for (;;) { let s = ''; for (let i = 0; i < 4; i++) s += A[Math.floor(Math.random() * A.length)]; if (!rooms.has(s)) return s; }
}

function handle(c, m) {
  if (m.t === 'host' && !c.room) {
    const code = newCode(), room = { code, host: c, clients: new Map(), next: 1 };
    rooms.set(code, room); c.room = room; c.id = 0;
    c.send({ t: 'hosted', code });
  } else if (m.t === 'join' && !c.room) {
    const room = rooms.get(String(m.code || '').toUpperCase().trim());
    if (!room) return c.send({ t: 'err', m: 'Комната не найдена. Проверьте код.' });
    if (room.clients.size >= 9) return c.send({ t: 'err', m: 'Комната заполнена.' });
    const id = room.next++; c.id = id; c.room = room; room.clients.set(id, c);
    c.send({ t: 'joined', id, code: room.code });
    room.host.send({ t: 'peer', id, name: String(m.name || 'Игрок').slice(0, 16), on: true });
  } else if (m.t === 'm' && c.room) {
    if (c.id === 0) {
      if (m.to === 'all') for (const x of c.room.clients.values()) x.send({ t: 'm', m: m.m });
      else { const x = c.room.clients.get(m.to); if (x) x.send({ t: 'm', m: m.m }); }
    } else c.room.host.send({ t: 'in', from: c.id, m: m.m });
  }
}

function drop(c) {
  const room = c.room; if (!room) return;
  if (c.id === 0) {
    for (const x of room.clients.values()) { x.send({ t: 'hostgone' }); x.socket.end(); }
    rooms.delete(room.code);
  } else if (room.clients.delete(c.id)) room.host.send({ t: 'peer', id: c.id, on: false });
}

server.listen(PORT, '0.0.0.0', () => {
  console.log('\nScorched Tanks запущен.\n');
  console.log('  Вы:            http://localhost:' + PORT);
  for (const a of addrs()) console.log((a.startsWith('https://') ? '  Друзьям (интернет): ' : '  Друзьям (сеть): ') + a);
  console.log('\n  Через интернет: cloudflared tunnel --url http://localhost:' + PORT);
  console.log('  или Tailscale: используйте адрес 100.x.x.x из списка выше.\n');
  console.log('  Остановить: Ctrl+C\n');
});
