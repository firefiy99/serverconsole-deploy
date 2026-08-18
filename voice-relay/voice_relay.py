#!/usr/bin/env python3
# 语音中继：WebSocket 回环（后续替换为千问 Realtime）
import os
import asyncio
from urllib.parse import urlparse, parse_qs
import websockets

KEY = os.environ.get('AGENT_KEY', '')

async def handler(websocket):
    req = getattr(websocket, 'request', None)
    path = getattr(req, 'path', '') or ''
    qs = parse_qs(urlparse(path).query)
    key = (qs.get('key') or [''])[0]
    if not KEY or key != KEY:
        await websocket.close(code=1008, reason='unauthorized')
        return
    try:
        async for message in websocket:
            await websocket.send(message)  # 回环
    except Exception:
        pass

async def main():
    async with websockets.serve(handler, '0.0.0.0', 8085):
        await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
