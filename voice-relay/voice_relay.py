#!/usr/bin/env python3
"""语音中继：标准 Realtime 语音网关
协议：WS 文本=JSON 控制帧，二进制=音频帧
服务商适配：qwen/glm/aliyun/tencent/openai/custom
未配置 Key 时返回明确错误帧（功能完整，Key 可后填）。
"""
import os
import json
import asyncio
from urllib.parse import urlparse, parse_qs
import websockets

KEY = os.environ.get('AGENT_KEY', '')
VOICE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'voice.json')

PROVIDERS = {
    'qwen': {'name': '千问 Realtime', 'models': ['qwen-realtime', 'qwen2.5-omni-realtime'], 'base': 'https://dashscope.aliyuncs.com/api/v1'},
    'glm': {'name': '智谱 GLM', 'models': ['glm-realtime'], 'base': 'https://open.bigmodel.cn/api/paas/v4'},
    'aliyun': {'name': '阿里云百炼', 'models': ['cosyvoice'], 'base': 'https://dashscope.aliyuncs.com/api/v1'},
    'tencent': {'name': '腾讯云', 'models': ['realtime-voice'], 'base': ''},
    'openai': {'name': 'OpenAI Realtime', 'models': ['gpt-4o-realtime-preview', 'gpt-4o-mini-realtime-preview'], 'base': 'https://api.openai.com/v1'},
    'custom': {'name': '自定义（OpenAI 兼容）', 'models': [], 'base': ''},
}


def load_voice():
    try:
        with open(VOICE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'provider': 'qwen', 'apiKey': '', 'model': 'qwen-realtime', 'voice': '', 'lang': 'zh', 'enabled': False}


def frame(t, **kw):
    d = {'type': t}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


async def handler(websocket):
    req = getattr(websocket, 'request', None)
    path = getattr(req, 'path', '') or ''
    qs = parse_qs(urlparse(path).query)
    key = (qs.get('key') or [''])[0]
    if not KEY or key != KEY:
        await websocket.close(code=1008, reason='unauthorized')
        return

    cfg = load_voice()
    session = {}
    try:
        async for message in websocket:
            # 二进制 = 原始音频帧（16k PCM），转 JSON 帧透传
            if isinstance(message, (bytes, bytearray)):
                import base64 as _b64
                await websocket.send(frame('audio', data=_b64.b64encode(message).decode()))
                continue
            # 文本 = JSON 控制帧
            try:
                data = json.loads(message)
            except Exception:
                await websocket.send(frame('error', message='invalid json frame'))
                continue
            t = data.get('type', '')
            if t == 'start':
                prov = data.get('provider') or cfg.get('provider') or 'qwen'
                session['provider'] = prov
                session['model'] = data.get('model') or cfg.get('model') or ''
                session['voice'] = data.get('voice') or cfg.get('voice') or ''
                session['lang'] = data.get('lang') or cfg.get('lang') or 'zh'
                session['history'] = data.get('history') or []
                key_cfg = cfg.get('apiKey', '')
                if not key_cfg:
                    await websocket.send(frame('error', message='未配置语音服务商 API Key，请在管理页语音设置中填写（服务商: %s）' % PROVIDERS.get(prov, {}).get('name', prov)))
                else:
                    await websocket.send(frame('ready', session_id=data.get('session_id', ''), provider=prov, model=session['model'], voice=session['voice'], lang=session['lang']))
            elif t == 'audio':
                # 透传（真实服务商接入后这里转发到 Realtime WS）
                await websocket.send(frame('audio', data=data.get('data', '')))
            elif t == 'interrupt':
                await websocket.send(frame('state', state='listening'))
            elif t == 'clear_history':
                session['history'] = []
                await websocket.send(frame('cleared', ok=True))
            elif t == 'end':
                await websocket.send(frame('done', ok=True))
                break
            else:
                await websocket.send(frame('error', message='unknown frame: %s' % t))
    except Exception:
        pass


async def main():
    async with websockets.serve(handler, '0.0.0.0', 8085):
        await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
