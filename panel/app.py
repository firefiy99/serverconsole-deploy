#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-monitor: 三端协同系统 8000 聚合面板"""
from gevent import monkey
monkey.patch_all()
import gevent

import os, json, time, socket, hashlib, secrets, urllib.request, logging
from logging.handlers import RotatingFileHandler
from collections import defaultdict
from flask import Flask, request, jsonify, make_response, redirect, Response, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
APK_DIR = os.path.join(BASE, 'apk')
os.makedirs(APK_DIR, exist_ok=True)
LOG_DIR = os.path.join(BASE, 'log')
os.makedirs(LOG_DIR, exist_ok=True)
KEY = os.environ.get('AGENT_KEY', '')
SESSION = secrets.token_hex(16)
COOKIE = 'agent_sess'

# ===== 面板品牌配置（可被环境变量覆盖，去品牌化默认值）=====
PANEL_NAME = os.environ.get('PANEL_NAME', '控制台')          # 页面标题/首页名称
PANEL_LOGO = os.environ.get('PANEL_LOGO', '控')              # 首页 logo 字符（1-2 字符）
PANEL_SUB = os.environ.get('PANEL_SUB', '服务器控制台')       # 副标题


BAN_FILE = os.path.join(BASE, 'banned.json')
BAN_SECONDS = 1800
LOGIN_WINDOW = 30
LOGIN_MAX = 10
SEC_CFG_FILE = os.path.join(BASE, 'security.json')


def load_sec_cfg():
    d = {'ban_seconds': 1800, 'login_window': 30, 'login_max': 10}
    try:
        with open(SEC_CFG_FILE, encoding='utf-8') as f:
            d.update(json.load(f))
    except Exception:
        pass
    return d


def save_sec_cfg(d):
    with open(SEC_CFG_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(SEC_CFG_FILE, 0o600)
    except Exception:
        pass

app = Flask(__name__)


def client_ip():
    if request.remote_addr in ('127.0.0.1', '::1'):
        return request.headers.get('X-Real-IP') or request.remote_addr
    return request.remote_addr



def _mk_logger(name, fn, max_bytes, backup):
    lg = logging.getLogger(name)
    lg.setLevel(logging.INFO)
    h = RotatingFileHandler(os.path.join(LOG_DIR, fn), maxBytes=max_bytes, backupCount=backup, encoding='utf-8')
    h.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    lg.addHandler(h)
    lg.propagate = False
    return lg


access_log = _mk_logger('access', 'access.log', 5 * 1024 * 1024, 3)
auth_log = _mk_logger('auth', 'auth.log', 2 * 1024 * 1024, 2)


def _load_bans():
    try:
        with open(BAN_FILE, encoding='utf-8') as f:
            return {k: v for k, v in json.load(f).items()}
    except Exception:
        return {}


BANNED = _load_bans()
LOGIN_ATTEMPTS = defaultdict(list)


def _persist_bans():
    try:
        with open(BAN_FILE, 'w', encoding='utf-8') as f:
            json.dump(BANNED, f)
    except Exception:
        pass


def _is_banned(ip):
    until = BANNED.get(ip, 0)
    if until and until > time.time():
        return True
    if until:
        del BANNED[ip]
        _persist_bans()
        auth_log.info('%s unbanned' % ip)
    return False


def _ban(ip):
    BANNED[ip] = time.time() + load_sec_cfg()['ban_seconds']
    _persist_bans()
    auth_log.info('%s banned' % ip)


def load_services():
    try:
        with open(os.path.join(BASE, 'services.json'), encoding='utf-8') as f:
            return json.loads(f.read())
    except Exception:
        return []


def probe_tcp(host, port, timeout=3):
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, int((time.time() - t0) * 1000)
    except Exception:
        return False, -1


def probe_http(url, timeout=3):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 400, int((time.time() - t0) * 1000)
    except Exception:
        return False, -1


def probe_service(s):
    kind = s.get('kind')
    if kind == 'self':
        return 'online', 0
    if kind == 'http':
        ok, ms = probe_http(s.get('target', ''))
        return ('online' if ok else 'offline'), ms
    if kind == 'tcp':
        ok, ms = probe_tcp(s.get('host', '127.0.0.1'), s.get('port', 0))
        return ('online' if ok else 'offline'), ms
    return 'unknown', -1


PROBE_INTERVAL = 30
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
HISTORY_FILE = os.path.join(BASE, 'history.json')
SERVICE_STATE = {}


def _load_history():
    try:
        with open(HISTORY_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


HISTORY = _load_history()


def _persist_history():
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(HISTORY, f, ensure_ascii=False)
    except Exception:
        pass


def _notify(ev):
    if not WEBHOOK_URL:
        return
    try:
        req = urllib.request.Request(WEBHOOK_URL, data=json.dumps(ev).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _record_event(sid, name, old, new):
    ev = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'id': sid, 'name': name, 'from': old, 'to': new}
    HISTORY.insert(0, ev)
    HISTORY[:] = HISTORY[:50]
    _persist_history()
    _notify(ev)


def _probe_once():
    for s in load_services():
        st, _ = probe_service(s)
        if st not in ('online', 'offline'):
            continue
        sid = s.get('id') or s.get('name')
        old = SERVICE_STATE.get(sid)
        SERVICE_STATE[sid] = st
        if old is not None and old != st:
            _record_event(sid, s.get('name', ''), old, st)


def _probe_loop():
    while True:
        try:
            _probe_once()
        except Exception:
            pass
        gevent.sleep(PROBE_INTERVAL)


@app.before_request
def security():
    ip = client_ip()
    if _is_banned(ip):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'banned'}), 403
        return 'IP 已被临时封禁，请稍后再试', 403
    if request.path == '/login':
        return
    if request.path.startswith('/api/bg/') or request.path == '/api/bg':
        return
    if request.cookies.get(COOKIE) == SESSION:
        return
    if request.args.get('key') == KEY:
        return
    if request.path.startswith('/api/'):
        return jsonify({'error': 'unauthorized'}), 401
    return redirect('/login')


@app.after_request
def access(resp):
    ip = client_ip()
    access_log.info('%s %s %s %s' % (ip, request.method, request.path, resp.status_code))
    return resp


@app.route('/login')
def login():
    ip = client_ip()
    cfg = load_sec_cfg()
    now = time.time()
    recent = [t for t in LOGIN_ATTEMPTS[ip] if now - t < cfg['login_window']]
    if len(recent) >= cfg['login_max']:
        _ban(ip)
        LOGIN_ATTEMPTS[ip] = []
        return '尝试次数过多，IP 已被临时封禁', 403
    key = request.args.get('key', '')
    if key and key == KEY:
        LOGIN_ATTEMPTS[ip] = []
        auth_log.info('%s login_ok' % ip)
        resp = make_response(redirect('/'))
        resp.set_cookie(COOKIE, SESSION, httponly=True, samesite='Lax', max_age=60*60*24*30)
        return resp
    LOGIN_ATTEMPTS[ip].append(now)
    auth_log.info('%s login_fail' % ip)
    return '<h2>未授权</h2><p>请通过 App 打开，或使用正确的统一密钥。</p>', 401


@app.route('/')
def index():
    with open(os.path.join(BASE, 'index.html'), encoding='utf-8') as f:
        html = f.read()
    # 注入品牌配置（去品牌化：默认「控制台」，可通过环境变量覆盖）
    html = html.replace('{{PANEL_NAME}}', PANEL_NAME)
    html = html.replace('{{PANEL_LOGO}}', PANEL_LOGO)
    html = html.replace('{{PANEL_SUB}}', PANEL_SUB)
    return Response(html, mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/api/status')
def status():
    host = request.host.split(':')[0]
    data = []
    for s in load_services():
        url = s.get('url', '')
        if url and '{host}' in url:
            url = url.replace('{host}', host)
        if not url:
            pp = s.get('public_port')
            p = s.get('path', '')
            if pp:
                url = 'http://%s:%d%s' % (host, pp, p)
            elif p:
                url = p
            elif s.get('kind') == 'self':
                url = '/'
        st, ms = probe_service(s)
        d = {'id': s.get('id'), 'name': s.get('name'), 'sub': s.get('sub'), 'icon': s.get('icon'),
             'color': s.get('color', '#3B82F6'), 'kind': s.get('kind'), 'status': st, 'url': url, 'latency': ms}
        data.append(d)
    total = len(data)
    online = sum(1 for d in data if d['status'] == 'online')
    ports = sum(1 for d in data if d['status'] == 'online' and d['kind'] == 'tcp')
    return jsonify({'services': data, 'panel_version': PANEL_VERSION, 'stats': {'total': total, 'online': online, 'ports': ports}})


@app.route('/api/ping')
def ping():
    return jsonify({'ok': True})


@app.route('/api/bans')
def bans():
    return jsonify({'banned': BANNED})


@app.route('/api/history')
def history():
    return jsonify({'events': HISTORY})


def load_latest():
    try:
        with open(os.path.join(BASE, 'latest.json'), encoding='utf-8') as f:
            return json.loads(f.read())
    except Exception:
        return {'versionCode': 0, 'versionName': '0', 'url': '', 'sha256': '', 'changelog': ''}


@app.route('/api/update/check')
def update_check():
    d = load_latest()
    url = d.get('url', '')
    if url.startswith('/'):
        url = 'http://' + request.host + url
    return jsonify({'versionCode': d.get('versionCode', 0),
                    'versionName': d.get('versionName', ''),
                    'url': url,
                    'sha256': d.get('sha256', ''),
                    'changelog': d.get('changelog', '')})


@app.route('/apk/<path:filename>')
def apk(filename):
    safe = os.path.basename(filename)
    if not safe.endswith('.apk'):
        return 'forbidden', 403
    return send_from_directory(APK_DIR, safe, as_attachment=False)


@app.route('/api/upload', methods=['POST'])
def upload():
    f = request.files.get('apk')
    if not f or not f.filename.endswith('.apk'):
        return jsonify({'error': 'need apk file'}), 400
    data = f.read()
    if len(data) > 200 * 1024 * 1024:
        return jsonify({'error': 'too large'}), 400
    name = os.path.basename(f.filename)
    if not name.endswith('.apk'):
        name = 'app.apk'
    with open(os.path.join(APK_DIR, name), 'wb') as out:
        out.write(data)
    return jsonify({'ok': True, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'name': name})


@app.route('/admin')
def admin():
    with open(os.path.join(BASE, 'admin.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/api/services', methods=['GET'])
def get_services():
    return jsonify({'services': load_services()})


@app.route('/api/services', methods=['POST'])
def save_services():
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, list):
        return jsonify({'error': 'invalid body, expect list'}), 400
    for s in data:
        if not isinstance(s, dict) or not s.get('name'):
            return jsonify({'error': 'each service needs a name'}), 400
        s.setdefault('id', secrets.token_hex(8))
    with open(os.path.join(BASE, 'services.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({'ok': True, 'count': len(data)})


VOICE_FILE = os.path.join(BASE, 'voice.json')


def load_voice():
    try:
        with open(VOICE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'apiKey': '', 'model': '', 'enabled': False}


def save_voice(d):
    with open(VOICE_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


@app.route('/api/voice/config', methods=['GET'])
def get_voice():
    return jsonify(load_voice())


@app.route('/api/voice/config', methods=['POST'])
def set_voice():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict):
        return jsonify({'error': 'invalid'}), 400
    v = load_voice()
    v.update(d)
    save_voice(v)
    return jsonify({'ok': True, 'config': v})


@app.route('/ws/voice')
def ws_voice():
    ws = request.environ.get('wsgi.websocket')
    if ws is None:
        return 'websocket required', 400
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            ws.send(msg)
    except Exception:
        pass
    return ''


# ================= Pi 编码助手 =================
PANEL_VERSION = '2.1.0'
PI_FILE = os.path.join(BASE, 'pi.json')
PI_PROVIDERS = {
    'glm': {'name': '智谱 GLM', 'base': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
            'models': ['glm-4.5', 'glm-4.5-air', 'glm-4.6', 'glm-5.7']},
    'deepseek': {'name': 'DeepSeek', 'base': 'https://api.deepseek.com/chat/completions',
                 'models': ['deepseek-chat', 'deepseek-reasoner']},
    'custom': {'name': '自定义 OpenAI 兼容', 'base': '', 'models': []},
}
PI_DEFAULT_PROMPT = ('你是一个专业的编程助手，擅长写代码、改 Bug、解释技术问题。'
                     '请用中文回答，给出准确、可直接运行的代码，并用 markdown 代码块包裹；'
                     '必要时简短解释关键思路。')


def load_pi():
    try:
        with open(PI_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'provider': 'glm', 'apiKey': '', 'model': 'glm-4.5',
                'systemPrompt': PI_DEFAULT_PROMPT, 'temperature': 0.4}


def save_pi(d):
    with open(PI_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(PI_FILE, 0o600)
    except Exception:
        pass


@app.route('/pi')
def pi_page():
    with open(os.path.join(BASE, 'pi.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/api/pi/config', methods=['GET'])
def get_pi():
    c = load_pi()
    key = c.get('apiKey', '')
    return jsonify({'provider': c.get('provider', 'glm'),
                    'base_url': c.get('base_url', ''),
                    'model': c.get('model', ''),
                    'systemPrompt': c.get('systemPrompt', ''),
                    'temperature': c.get('temperature', 0.4),
                    'has_key': bool(key),
                    'key_hint': ('****' + key[-4:]) if key else ''})


@app.route('/api/pi/config', methods=['POST'])
def set_pi():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict):
        return jsonify({'error': 'invalid'}), 400
    c = load_pi()
    if d.get('apiKey'):
        c['apiKey'] = d['apiKey'].strip()
    if d.get('provider') in PI_PROVIDERS:
        c['provider'] = d['provider']
    if d.get('base_url'):
        c['base_url'] = d['base_url'].strip()
    if d.get('model'):
        c['model'] = d['model'].strip()
    if d.get('systemPrompt') is not None:
        c['systemPrompt'] = d['systemPrompt'].strip()
    if isinstance(d.get('temperature'), (int, float)):
        c['temperature'] = float(d['temperature'])
    save_pi(c)
    return jsonify({'ok': True, 'has_key': bool(c.get('apiKey'))})


def _pi_stream(messages, cfg):
    provider = cfg.get('provider', 'glm')
    api_key = cfg.get('apiKey', '')
    model = cfg.get('model', '') or 'glm-4.5'
    if not api_key:
        yield json.dumps({'error': '未配置 API Key：请到「⚙ 设置 / Pi 设置」填写模型服务商密钥'})
        return
    if provider not in PI_PROVIDERS:
        yield json.dumps({'error': '未知的模型服务商: %s' % provider})
        return
    sys_p = cfg.get('systemPrompt', '') or PI_DEFAULT_PROMPT
    msgs = [{'role': 'system', 'content': sys_p}] + messages
    body = json.dumps({'model': model, 'messages': msgs, 'stream': True,
                       'temperature': float(cfg.get('temperature', 0.4))}).encode('utf-8')
    base = cfg.get('base_url') or PI_PROVIDERS[provider]['base']
    if provider == 'custom' and not base:
        yield json.dumps({'error': '自定义服务商需要填写接口地址'})
        return
    req = urllib.request.Request(base, data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + api_key,
                                          'Content-Type': 'application/json'})
    try:
        resp = urllib.request.urlopen(req, timeout=180)
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode('utf-8', 'ignore')[:300]
        except Exception:
            msg = str(e)
        yield json.dumps({'error': '模型接口错误 %s: %s' % (e.code, msg)})
        return
    except Exception as e:
        yield json.dumps({'error': '连接模型接口失败: %s' % e})
        return
    try:
        for raw in resp:
            line = raw.decode('utf-8', 'ignore').strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                break
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            ch = obj.get('choices') or []
            if not ch:
                continue
            delta = (ch[0].get('delta') or {}).get('content')
            if delta:
                yield json.dumps({'delta': delta})
    except Exception as e:
        yield json.dumps({'error': '读取模型响应中断: %s' % e})


@app.route('/api/pi/chat', methods=['POST'])
def pi_chat():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict) or not isinstance(d.get('messages'), list):
        return jsonify({'error': 'invalid body'}), 400
    cfg = load_pi()

    def gen():
        for piece in _pi_stream(d['messages'], cfg):
            yield 'data: ' + piece + '\n\n'
        yield 'data: [DONE]\n\n'

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ================= 共享知识库（云端 API） =================
KB_FILE = os.path.join(BASE, 'kb.json')
KB_DOCS_FILE = os.path.join(BASE, 'kb_docs.json')
KB_PROVIDERS = {
    'zhipu': {'name': '智谱 GLM', 'embed': 'https://open.bigmodel.cn/api/paas/v4/embeddings',
              'rerank': 'https://open.bigmodel.cn/api/paas/v4/rerank',
              'models': ['embedding-3'], 'rerank_models': ['bge-reranker-v2-m3']},
    'dashscope': {'name': '阿里百炼', 'embed': 'https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings',
                  'rerank': 'https://dashscope.aliyuncs.com/compatible-mode/v1/rerank',
                  'models': ['text-embedding-v3'], 'rerank_models': ['gte-rerank']},
    'siliconflow': {'name': '硅基流动', 'embed': 'https://api.siliconflow.cn/v1/embeddings',
                    'rerank': 'https://api.siliconflow.cn/v1/rerank',
                    'models': ['BAAI/bge-m3'], 'rerank_models': ['BAAI/bge-reranker-v2-m3']},
    'custom': {'name': '自定义 Embedding', 'embed': '', 'rerank': '', 'models': [], 'rerank_models': []},
}
KB_DEFAULT_CFG = {'provider': 'zhipu', 'apiKey': '', 'embedModel': 'embedding-3',
                  'rerank': True, 'rerankModel': 'bge-reranker-v2-m3',
                  'chunkSize': 500}


def load_kb():
    try:
        with open(KB_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return dict(KB_DEFAULT_CFG)


def save_kb(d):
    with open(KB_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(KB_FILE, 0o600)
    except Exception:
        pass


def load_kb_docs():
    try:
        with open(KB_DOCS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_kb_docs(docs):
    with open(KB_DOCS_FILE, 'w', encoding='utf-8') as f:
        json.dump(docs, f, ensure_ascii=False)


def kb_chunk(text, size, overlap=40):
    import re as _re
    text = _re.sub(r'\s+', ' ', text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    i = 0
    step = size - overlap
    while i < len(text):
        chunks.append(text[i:i + size])
        i += step
    return chunks


def kb_cosine(a, b):
    if not a or len(a) != len(b):
        return 0.0
    s = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        s += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    import math
    return s / (math.sqrt(na) * math.sqrt(nb))


def kb_embed(texts, cfg):
    provider = cfg.get('provider', 'zhipu')
    key = cfg.get('apiKey', '')
    if not key:
        raise RuntimeError('未配置 API Key')
    base = cfg.get('embed_base') or KB_PROVIDERS[provider]['embed']
    if provider == 'custom' and not base:
        raise RuntimeError('自定义服务商需要填写接口地址')
    model = cfg.get('embedModel') or KB_PROVIDERS[provider]['models'][0]
    body = json.dumps({'model': model, 'input': texts}).encode('utf-8')
    req = urllib.request.Request(base, data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + key,
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        msg = ''
        try:
            msg = e.read().decode('utf-8', 'ignore')[:200]
        except Exception:
            pass
        raise RuntimeError('Embedding 接口错误 %s: %s' % (e.code, msg))
    return [d['embedding'] for d in data.get('data', [])]


def kb_rerank(query, hits, cfg):
    provider = cfg.get('provider', 'zhipu')
    base = KB_PROVIDERS[provider]['rerank']
    model = cfg.get('rerankModel') or KB_PROVIDERS[provider]['rerank_models'][0]
    body = json.dumps({'model': model, 'query': query,
                       'documents': [h['text'] for h in hits]}).encode('utf-8')
    req = urllib.request.Request(base, data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + cfg.get('apiKey', ''),
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode('utf-8', 'ignore'))
    ranked = []
    for res in data.get('results', []):
        i = res.get('index')
        if i is None or i >= len(hits):
            continue
        h = dict(hits[i])
        try:
            h['score'] = round(float(res.get('relevance_score', 0)), 4)
        except Exception:
            pass
        ranked.append(h)
    ranked.sort(key=lambda h: h['score'], reverse=True)
    return ranked or hits


@app.route('/kb')
def kb_page():
    with open(os.path.join(BASE, 'kb.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/api/kb/config', methods=['GET'])
def get_kb():
    c = load_kb()
    key = c.get('apiKey', '')
    return jsonify({'provider': c.get('provider', 'zhipu'),
                    'embed_base': c.get('embed_base', ''),
                    'embedModel': c.get('embedModel', ''),
                    'rerank': bool(c.get('rerank')),
                    'rerankModel': c.get('rerankModel', ''),
                    'chunkSize': c.get('chunkSize', 500),
                    'has_key': bool(key),
                    'key_hint': ('****' + key[-4:]) if key else ''})


@app.route('/api/kb/config', methods=['POST'])
def set_kb():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict):
        return jsonify({'error': 'invalid'}), 400
    c = load_kb()
    if d.get('apiKey'):
        c['apiKey'] = d['apiKey'].strip()
    if d.get('provider') in KB_PROVIDERS:
        c['provider'] = d['provider']
    if d.get('embed_base'):
        c['embed_base'] = d['embed_base'].strip()
    if d.get('embedModel'):
        c['embedModel'] = d['embedModel'].strip()
    if d.get('rerankModel'):
        c['rerankModel'] = d['rerankModel'].strip()
    if isinstance(d.get('rerank'), bool):
        c['rerank'] = d['rerank']
    if isinstance(d.get('chunkSize'), int) and 100 <= d['chunkSize'] <= 2000:
        c['chunkSize'] = d['chunkSize']
    save_kb(c)
    return jsonify({'ok': True, 'has_key': bool(c.get('apiKey'))})


@app.route('/api/kb/docs', methods=['GET'])
def kb_list_docs():
    docs = load_kb_docs()
    return jsonify({'docs': [{'id': d.get('id'), 'name': d.get('name'),
                              'chunks': len(d.get('chunks', [])),
                              'created': d.get('created')} for d in docs]})


@app.route('/api/kb/docs', methods=['POST'])
def kb_add_doc():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict) or not d.get('text'):
        return jsonify({'error': 'need text'}), 400
    cfg = load_kb()
    if not cfg.get('apiKey'):
        return jsonify({'error': '未配置 API Key'}), 400
    name = (d.get('name') or '').strip() or ('文档' + str(int(time.time())))
    chunks = kb_chunk(d['text'], int(cfg.get('chunkSize', 500)))
    if not chunks:
        return jsonify({'error': '空内容'}), 400
    vecs = []
    for i in range(0, len(chunks), 8):
        try:
            vecs.extend(kb_embed(chunks[i:i + 8], cfg))
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    doc = {'id': secrets.token_hex(8), 'name': name, 'created': int(time.time()),
           'chunks': [{'text': t, 'vec': v} for t, v in zip(chunks, vecs)]}
    docs = load_kb_docs()
    docs.append(doc)
    save_kb_docs(docs)
    return jsonify({'ok': True, 'id': doc['id'], 'name': name, 'chunks': len(chunks)})


@app.route('/api/kb/docs/<doc_id>', methods=['DELETE'])
def kb_del_doc(doc_id):
    docs = load_kb_docs()
    docs = [d for d in docs if d.get('id') != doc_id]
    save_kb_docs(docs)
    return jsonify({'ok': True})


@app.route('/api/kb/search', methods=['POST'])
def kb_search():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict) or not d.get('query'):
        return jsonify({'error': 'need query'}), 400
    cfg = load_kb()
    if not cfg.get('apiKey'):
        return jsonify({'error': '未配置 API Key'}), 400
    query = d['query'].strip()
    try:
        top_k = int(d.get('top_k') or 5)
    except Exception:
        top_k = 5
    docs = load_kb_docs()
    if not docs:
        return jsonify({'results': []})
    try:
        qvec = kb_embed([query], cfg)[0]
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    hits = []
    for doc in docs:
        for c in doc.get('chunks', []):
            hits.append({'doc': doc.get('name'), 'text': c.get('text'),
                         'score': round(kb_cosine(qvec, c.get('vec', [])), 4)})
    hits.sort(key=lambda h: h['score'], reverse=True)
    hits = hits[:30]
    if cfg.get('rerank') and len(hits) > 1:
        try:
            hits = kb_rerank(query, hits, cfg)
        except Exception:
            pass
    return jsonify({'results': hits[:top_k]})


# ================= 远程协助（远程命令执行） =================
import subprocess as _sp
import signal as _sig
import re as _re
REMOTE_LOG = _mk_logger('remote', 'remote.log', 2 * 1024 * 1024, 2)
REMOTE_MAX_SEC = 300
REMOTE_MAX_OUT = 200 * 1024
REMOTE_WINDOW = 60
REMOTE_LIMIT = 10
REMOTE_LAST = defaultdict(list)
REMOTE_BANNED = [
    _re.compile(r'\brm\s+-(rf|fr)\s+/\s*$'),
    _re.compile(r'\brm\s+-(rf|fr)\s+/\s'),
    _re.compile(r'\bmkfs\b'),
    _re.compile(r'\bdd\b[^|;&]*\bof=/dev/sd'),
    _re.compile(r'\bshutdown\b'),
    _re.compile(r'\breboot\b'),
    _re.compile(r'\bpoweroff\b'),
    _re.compile(r'\bhalt\b'),
    _re.compile(r'\binit\s+[06]\b'),
    _re.compile(r':\s*\(\s*\)\s*\{'),
    _re.compile(r'chmod\s+(-R\s+)?777\s+/\s*$'),
    _re.compile(r'chown\s+(-R\s+)?[^\s]+\s+/\s*$'),
    _re.compile(r'\bfdisk\b'),
    _re.compile(r'\bparted\b'),
    _re.compile(r'>\s*/dev/sd'),
]


def _remote_ban_match(cmd):
    for rx in REMOTE_BANNED:
        if rx.search(cmd):
            return rx.pattern
    return None


def _run_remote(cmd, timeout):
    t0 = time.time()
    try:
        p = _sp.Popen(cmd, shell=True, executable='/bin/bash',
                      stdout=_sp.PIPE, stderr=_sp.PIPE,
                      start_new_session=True)
    except Exception as e:
        return {'exit_code': -1, 'stdout': '', 'stderr': str(e),
                'elapsed': 0, 'truncated': False, 'timed_out': False}
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except _sp.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(p.pid), _sig.SIGKILL)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        out, err = p.communicate()
    try:
        code = p.returncode
    except Exception:
        code = -1
    out_s = (out or b'').decode('utf-8', 'ignore')
    err_s = (err or b'').decode('utf-8', 'ignore')
    tr_o = len(out_s) > REMOTE_MAX_OUT
    tr_e = len(err_s) > REMOTE_MAX_OUT
    return {'exit_code': code if code is not None else -1,
            'stdout': out_s[-REMOTE_MAX_OUT:],
            'stderr': err_s[-REMOTE_MAX_OUT:],
            'elapsed': round(time.time() - t0, 2),
            'truncated': tr_o or tr_e,
            'timed_out': timed_out}


@app.route('/remote')
def remote_page():
    with open(os.path.join(BASE, 'remote.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/api/remote/exec', methods=['POST'])
def remote_exec():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict) or not d.get('cmd') or not str(d.get('cmd')).strip():
        return jsonify({'error': 'need cmd'}), 400
    ip = client_ip()
    now = time.time()
    recent = [t for t in REMOTE_LAST[ip] if now - t < REMOTE_WINDOW]
    if len(recent) >= REMOTE_LIMIT:
        return jsonify({'error': '执行过于频繁，请稍后再试'}), 429
    REMOTE_LAST[ip] = recent + [now]
    cmd = str(d['cmd']).strip()
    try:
        timeout = int(d.get('timeout') or 60)
    except Exception:
        timeout = 60
    timeout = max(1, min(timeout, REMOTE_MAX_SEC))
    hit = _remote_ban_match(cmd)
    if hit:
        REMOTE_LOG.info('%s exec_BLOCKED cmd=%r pattern=%s' % (ip, cmd, hit))
        return jsonify({'error': '命令被安全策略拦截（命中危险模式 %s）' % hit, 'blocked': True}), 400
    result = _run_remote(cmd, timeout)
    REMOTE_LOG.info('%s exec cmd=%r code=%s elapsed=%s' % (ip, cmd, result['exit_code'], result['elapsed']))
    result['cmd'] = cmd
    result['ip'] = ip
    return jsonify(result)


@app.route('/api/remote/history', methods=['GET'])
def remote_history():
    try:
        lines = []
        with open(os.path.join(LOG_DIR, 'remote.log'), encoding='utf-8', errors='ignore') as f:
            for line in f.readlines()[-30:]:
                line = line.strip()
                if ' exec ' in line or ' exec_BLOCKED ' in line:
                    lines.append(line)
        return jsonify({'events': lines})
    except Exception as e:
        return jsonify({'events': [], 'error': str(e)})


# ================= 安全页面（Token与攻击） =================
@app.route('/security')
def security_page():
    with open(os.path.join(BASE, 'security.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/api/security/status')
def sec_status():
    bans = _load_bans()
    now = time.time()
    ban_list = [{'ip': ip, 'until': until, 'remaining': max(0, int(until - now))}
                for ip, until in sorted(bans.items(), key=lambda x: x[1])]
    try:
        with open(os.path.join(LOG_DIR, 'auth.log'), encoding='utf-8', errors='ignore') as f:
            auth_lines = f.readlines()[-30:]
        auth_lines = [l.strip() for l in auth_lines if l.strip()]
    except Exception:
        auth_lines = []
    fail_24h = 0
    events = []
    for line in auth_lines:
        parts = line.split(' ', 3)
        if len(parts) >= 4:
            ts = parts[0] + ' ' + parts[1]
            ip = parts[2]
            ev = parts[3]
            if 'login_fail' in ev:
                fail_24h += 1
                events.append({'time': ts, 'ip': ip, 'type': 'login_fail'})
            elif 'login_ok' in ev:
                events.append({'time': ts, 'ip': ip, 'type': 'login_ok'})
            elif 'banned' in ev:
                events.append({'time': ts, 'ip': ip, 'type': 'banned'})
            elif 'unbanned' in ev:
                events.append({'time': ts, 'ip': ip, 'type': 'unbanned'})
            elif 'exec_BLOCKED' in ev:
                events.append({'time': ts, 'ip': ip, 'type': 'blocked', 'detail': ev[:60]})
    events = events[-20:]
    cfg = load_sec_cfg()
    return jsonify({'bans': ban_list,
                    'has_key': bool(KEY),
                    'login_limit': cfg['login_max'],
                    'login_window': cfg['login_window'],
                    'ban_seconds': cfg['ban_seconds'],
                    'login_fail_24h': fail_24h,
                    'events': events})


@app.route('/api/security/unban', methods=['POST'])
def sec_unban():
    d = request.get_json(force=True, silent=True)
    ip = (d.get('ip') or '').strip()
    if not ip:
        return jsonify({'error': 'need ip'}), 400
    if ip in BANNED:
        del BANNED[ip]
        _persist_bans()
        auth_log.info('%s unbanned (admin)' % ip)
        return jsonify({'ok': True, 'message': '已从应用层封禁中解除 %s' % ip,
                        'iptables_hint': '如需解除系统级 iptables 封禁，请在远程协助执行：iptables -D INPUT -s %s -j DROP && systemctl restart agent-ban' % ip})
    return jsonify({'error': 'IP 未在封禁列表中'}), 404


@app.route('/api/security/config', methods=['GET'])
def get_sec_cfg():
    return jsonify(load_sec_cfg())


@app.route('/api/security/config', methods=['POST'])
def set_sec_cfg():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict):
        return jsonify({'error': 'invalid'}), 400
    cfg = load_sec_cfg()
    try:
        if 'ban_seconds' in d:
            v = int(d['ban_seconds'])
            cfg['ban_seconds'] = max(60, min(v, 86400))
        if 'login_window' in d:
            v = int(d['login_window'])
            cfg['login_window'] = max(5, min(v, 3600))
        if 'login_max' in d:
            v = int(d['login_max'])
            cfg['login_max'] = max(3, min(v, 100))
    except Exception:
        return jsonify({'error': 'invalid values'}), 400
    save_sec_cfg(cfg)
    auth_log.info('%s update_sec_cfg %s' % (client_ip(), json.dumps(cfg)))
    return jsonify({'ok': True, 'config': cfg})


@app.route('/api/security/reset', methods=['POST'])
def sec_reset():
    BANNED.clear()
    _persist_bans()
    auth_log.info('%s reset_all_bans (admin)' % client_ip())
    return jsonify({'ok': True, 'message': '应用层封禁已全部清除。如需解除系统级 iptables 封禁，请执行 iptables -F INPUT 后重启 agent-ban（注意：iptables -F 会清空所有规则，谨慎操作）'})


# ================= 运维脚本 =================
SCRIPTS_FILE = os.path.join(BASE, 'scripts.json')
SCRIPTS_HISTORY = []
SCRIPT_LOG = _mk_logger('script', 'script.log', 2 * 1024 * 1024, 2)


def load_scripts():
    try:
        with open(SCRIPTS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_scripts(docs):
    with open(SCRIPTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)


@app.route('/appearance')
def appearance_page():
    with open(os.path.join(BASE, 'appearance.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/models')
def models_page():
    with open(os.path.join(BASE, 'models.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/scripts')
def scripts_page():
    with open(os.path.join(BASE, 'scripts.html'), encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html', headers={'Cache-Control': 'no-cache'})


@app.route('/api/scripts', methods=['GET'])
def scripts_list():
    docs = load_scripts()
    return jsonify({'scripts': [{'id': d['id'], 'name': d['name'], 'desc': d.get('desc', ''),
                                'created': d.get('created')} for d in docs]})


@app.route('/api/scripts/<sid>', methods=['GET'])
def scripts_get(sid):
    for d in load_scripts():
        if d['id'] == sid:
            return jsonify(d)
    return jsonify({'error': 'not found'}), 404


@app.route('/api/scripts', methods=['POST'])
def scripts_save():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict) or not d.get('name') or not d.get('content'):
        return jsonify({'error': 'need name and content'}), 400
    docs = load_scripts()
    if d.get('id'):
        for i, x in enumerate(docs):
            if x['id'] == d['id']:
                docs[i]['name'] = d['name'].strip()
                docs[i]['content'] = d['content']
                docs[i]['desc'] = (d.get('desc') or '').strip()
                save_scripts(docs)
                return jsonify({'ok': True, 'id': d['id']})
        return jsonify({'error': 'not found'}), 404
    sid = secrets.token_hex(8)
    docs.append({'id': sid, 'name': d['name'].strip(), 'content': d['content'],
                'desc': (d.get('desc') or '').strip(), 'created': int(time.time())})
    save_scripts(docs)
    return jsonify({'ok': True, 'id': sid})


@app.route('/api/scripts/<sid>', methods=['DELETE'])
def scripts_del(sid):
    docs = [x for x in load_scripts() if x['id'] != sid]
    save_scripts(docs)
    return jsonify({'ok': True})


@app.route('/api/scripts/<sid>/run', methods=['POST'])
def scripts_run(sid):
    ip = client_ip()
    now = time.time()
    recent = [t for t in REMOTE_LAST[ip] if now - t < REMOTE_WINDOW]
    if len(recent) >= REMOTE_LIMIT:
        return jsonify({'error': '执行过于频繁，请稍后再试'}), 429
    REMOTE_LAST[ip] = recent + [now]
    for x in load_scripts():
        if x['id'] == sid:
            content = x['content']
            hit = _remote_ban_match(content)
            if hit:
                SCRIPT_LOG.info('%s run_BLOCKED script=%s pattern=%s' % (ip, x['name'], hit))
                return jsonify({'error': '脚本内容命中危险模式，已拦截', 'blocked': True}), 400
            try:
                timeout = int((request.get_json(force=True, silent=True) or {}).get('timeout') or 60)
            except Exception:
                timeout = 60
            timeout = max(1, min(timeout, REMOTE_MAX_SEC))
            result = _run_remote(content, timeout)
            result['script'] = x['name']
            SCRIPT_LOG.info('%s run script=%s code=%s elapsed=%s' % (ip, x['name'], result['exit_code'], result['elapsed']))
            SCRIPTS_HISTORY.insert(0, {'time': time.strftime('%m-%d %H:%M'), 'name': x['name'],
                                      'code': result['exit_code']})
            SCRIPTS_HISTORY[:] = SCRIPTS_HISTORY[:20]
            return jsonify(result)
    return jsonify({'error': 'not found'}), 404


@app.route('/api/scripts/history', methods=['GET'])
def scripts_history():
    return jsonify({'history': SCRIPTS_HISTORY})


@app.route('/api/models/fetch', methods=['POST'])
def models_fetch():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict) or not d.get('base_url') or not d.get('api_key'):
        return jsonify({'error': 'need base_url and api_key'}), 400
    base = str(d['base_url']).strip().rstrip('/')
    if not base.startswith('http'):
        return jsonify({'error': '接口地址需以 http(s):// 开头'}), 400
    url = base + '/models'
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + d['api_key'].strip()})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        msg = ''
        try:
            msg = e.read().decode('utf-8', 'ignore')[:200]
        except Exception:
            pass
        return jsonify({'error': '接口错误 %s: %s' % (e.code, msg)}), 400
    except Exception as e:
        return jsonify({'error': '连接失败: %s' % e}), 400
    models = [m.get('id') for m in data.get('data', []) if m.get('id')]
    return jsonify({'models': models, 'count': len(models)})


UI_FILE = os.path.join(BASE, 'ui.json')


def load_ui():
    try:
        with open(UI_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'bg_type': 'gradient', 'bg_value': '', 'bg_darkness': 55, 'bg_blur': 0}


@app.route('/api/ui', methods=['GET'])
def get_ui():
    return jsonify(load_ui())


@app.route('/api/ui', methods=['POST'])
def set_ui():
    d = request.get_json(force=True, silent=True)
    if not isinstance(d, dict):
        return jsonify({'error': 'invalid'}), 400
    c = load_ui()
    if d.get('bg_type') in ('gradient', 'light', 'image', 'color'):
        c['bg_type'] = d['bg_type']
    if d.get('bg_value') is not None:
        c['bg_value'] = d['bg_value'].strip()
    if isinstance(d.get('bg_darkness'), (int, float)):
        c['bg_darkness'] = max(0, min(int(d['bg_darkness']), 100))
    if isinstance(d.get('bg_blur'), (int, float)):
        c['bg_blur'] = max(0, min(int(d['bg_blur']), 30))
    try:
        with open(UI_FILE, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return jsonify({'ok': True, 'config': c})


BG_DIR = os.path.join(BASE, 'bg')


@app.route('/api/ui/upload', methods=['POST'])
def ui_upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'need file'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
        return jsonify({'error': 'bad type'}), 400
    os.makedirs(BG_DIR, exist_ok=True)
    name = secrets.token_hex(8) + ext
    f.save(os.path.join(BG_DIR, name))
    return jsonify({'ok': True, 'url': '/api/bg/' + name})


@app.route('/api/ui/url-bg', methods=['POST'])
def ui_url_bg():
    d = request.get_json(force=True, silent=True) or {}
    url = (d.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'need url'}), 400
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'bad url'}), 400
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            ctype = r.headers.get('Content-Type', '')
    except Exception as e:
        return jsonify({'error': '下载失败: %s' % e}), 400
    ext_map = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}
    ext = ext_map.get(ctype.split(';')[0].strip().lower(), '')
    if not ext:
        # 从 URL 路径猜扩展名
        path_ext = os.path.splitext(url.split('?')[0])[1].lower()
        if path_ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
            ext = '.jpg' if path_ext == '.jpeg' else path_ext
    if not ext:
        return jsonify({'error': '不支持的图片类型: %s' % ctype}), 400
    if len(data) > 20 * 1024 * 1024:
        return jsonify({'error': '图片过大'}), 400
    os.makedirs(BG_DIR, exist_ok=True)
    name = secrets.token_hex(8) + ext
    with open(os.path.join(BG_DIR, name), 'wb') as f:
        f.write(data)
    return jsonify({'ok': True, 'url': '/api/bg/' + name})


@app.route('/api/bg/<name>')
def bg_file(name):
    safe = os.path.basename(name)
    if safe != name:
        return 'forbidden', 403
    return send_from_directory(BG_DIR, safe)


if __name__ == '__main__':
    from geventwebsocket.handler import WebSocketHandler
    from gevent.pywsgi import WSGIServer
    gevent.spawn(_probe_loop)
    print('agent-monitor listening on :8081')
    WSGIServer(('0.0.0.0', 8081), app, handler_class=WebSocketHandler).serve_forever()
