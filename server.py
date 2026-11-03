#!/usr/bin/env python3
# Scorched Tanks: сервер комнат на Python 3 (только стандартная библиотека).
# Раздаёт index.html и пересылает сообщения между хостом и игроками по WebSocket.
import asyncio, base64, hashlib, json, os, random, re, socket, struct, subprocess

PORT = int(os.environ.get('PORT', '8787'))
DIR = os.path.dirname(os.path.abspath(__file__))
GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
rooms = {}


def addrs():
    ips = []
    try:
        out = subprocess.run(['ifconfig'], capture_output=True, text=True, timeout=3).stdout
        ips = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)', out)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('10.255.255.255', 1))
            ips = [s.getsockname()[0]]
            s.close()
        except Exception:
            pass
    pub = os.environ.get('PUBLIC_URL', '').strip().rstrip('/')
    return ([pub] if pub else []) + ['http://%s:%d' % (i, PORT) for i in ips if not i.startswith('127.') and not i.startswith('169.254.')]


def frame(text):
    p = text.encode('utf-8')
    n = len(p)
    if n < 126:
        h = bytes([0x81, n])
    elif n < 65536:
        h = bytes([0x81, 126]) + struct.pack('>H', n)
    else:
        h = bytes([0x81, 127]) + struct.pack('>Q', n)
    return h + p


class Client:
    def __init__(self, writer):
        self.writer = writer
        self.room = None
        self.id = None

    def send(self, obj):
        try:
            self.writer.write(frame(json.dumps(obj, ensure_ascii=False, separators=(',', ':'))))
        except Exception:
            pass


def new_code():
    letters = 'ABCDEFGHJKMNPQRSTUVWXYZ'
    while True:
        c = ''.join(random.choice(letters) for _ in range(4))
        if c not in rooms:
            return c


def handle_msg(c, m):
    t = m.get('t')
    if t == 'host' and not c.room:
        code = new_code()
        room = {'code': code, 'host': c, 'clients': {}, 'next': 1}
        rooms[code] = room
        c.room = room
        c.id = 0
        c.send({'t': 'hosted', 'code': code})
    elif t == 'join' and not c.room:
        room = rooms.get(str(m.get('code', '')).upper().strip())
        if not room:
            return c.send({'t': 'err', 'm': 'Комната не найдена. Проверьте код.'})
        if len(room['clients']) >= 9:
            return c.send({'t': 'err', 'm': 'Комната заполнена.'})
        cid = room['next']
        room['next'] += 1
        c.id = cid
        c.room = room
        room['clients'][cid] = c
        c.send({'t': 'joined', 'id': cid, 'code': room['code']})
        room['host'].send({'t': 'peer', 'id': cid, 'name': str(m.get('name') or 'Игрок')[:16], 'on': True})
    elif t == 'm' and c.room:
        room = c.room
        if c.id == 0:
            if m.get('to') == 'all':
                for x in list(room['clients'].values()):
                    x.send({'t': 'm', 'm': m.get('m')})
            else:
                x = room['clients'].get(m.get('to'))
                if x:
                    x.send({'t': 'm', 'm': m.get('m')})
        else:
            room['host'].send({'t': 'in', 'from': c.id, 'm': m.get('m')})


def drop(c):
    room = c.room
    if not room:
        return
    c.room = None
    if c.id == 0:
        for x in list(room['clients'].values()):
            x.send({'t': 'hostgone'})
            try:
                x.writer.close()
            except Exception:
                pass
        rooms.pop(room['code'], None)
    elif room['clients'].pop(c.id, None) is not None:
        room['host'].send({'t': 'peer', 'id': c.id, 'on': False})


async def read_frame(reader):
    h = await reader.readexactly(2)
    op = h[0] & 15
    masked = h[1] & 128
    n = h[1] & 127
    if n == 126:
        n = struct.unpack('>H', await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack('>Q', await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else None
    data = await reader.readexactly(n)
    if mask:
        key = (mask * (n // 4 + 1))[:n]
        data = (int.from_bytes(data, 'big') ^ int.from_bytes(key, 'big')).to_bytes(n, 'big') if n else b''
    return op, data


async def serve_ws(reader, writer, key):
    accept = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()
    writer.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                  'Sec-WebSocket-Accept: %s\r\n\r\n' % accept).encode())
    await writer.drain()
    sock = writer.get_extra_info('socket')
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
    c = Client(writer)
    try:
        while True:
            op, data = await read_frame(reader)
            if op == 8:
                break
            if op == 9:
                writer.write(bytes([0x8a, len(data)]) + data)
            elif op == 1:
                try:
                    m = json.loads(data.decode('utf-8'))
                except Exception:
                    continue
                if isinstance(m, dict):
                    handle_msg(c, m)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        drop(c)
        try:
            writer.close()
        except Exception:
            pass


def http_reply(writer, status, ctype, body):
    writer.write(('HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\n'
                  'Connection: close\r\n\r\n' % (status, ctype, len(body))).encode() + body)


async def handler(reader, writer):
    try:
        head = await reader.readuntil(b'\r\n\r\n')
        lines = head.decode('latin1').split('\r\n')
        path = lines[0].split(' ')[1].split('?')[0]
        hdr = {}
        for ln in lines[1:]:
            if ': ' in ln:
                k, v = ln.split(': ', 1)
                hdr[k.lower()] = v
        if path == '/ws' and 'sec-websocket-key' in hdr:
            return await serve_ws(reader, writer, hdr['sec-websocket-key'])
        if path == '/__tanks':
            http_reply(writer, '200 OK', 'text/plain', b'ok')
        elif path == '/__addrs':
            http_reply(writer, '200 OK', 'application/json', json.dumps(addrs()).encode())
        elif path in ('/', '/index.html'):
            try:
                with open(os.path.join(DIR, 'index.html'), 'rb') as f:
                    http_reply(writer, '200 OK', 'text/html; charset=utf-8', f.read())
            except OSError:
                http_reply(writer, '500 Error', 'text/plain', 'index.html не найден рядом с server.py'.encode())
        else:
            http_reply(writer, '404 Not Found', 'text/plain', b'not found')
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, OSError, IndexError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(handler, '0.0.0.0', PORT)
    print('\nScorched Tanks запущен.\n')
    print('  Вы:             http://localhost:%d' % PORT)
    for a in addrs():
        print(('  Друзьям (интернет): ' if a.startswith('https://') else '  Друзьям (сеть): ') + a)
    print('\n  Через интернет: cloudflared tunnel --url http://localhost:%d' % PORT)
    print('  или Tailscale: используйте адрес 100.x.x.x из списка выше.\n')
    print('  Остановить: Ctrl+C\n')
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
