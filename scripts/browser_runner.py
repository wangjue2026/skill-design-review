#!/usr/bin/env python3
"""
browser_runner.py — 跨平台浏览器走查执行器（零第三方依赖）

用途：
  在设计检视走查（SKILL.md §0.2 L1「独立浏览器窗口」）中，拉起一个独立可控的
  浏览器窗口，并通过 CDP 完成：视口设定 / 导航 / 取值 / 中文注入 / 截图 / 关闭。

为什么不用 Playwright / Puppeteer：
  它们是需要下载安装的第三方包。本脚本只用 Python 标准库 + 系统上「已经存在」的
  浏览器二进制，因此在任意环境都能直接跑，不需要用户安装任何东西。

设计红线（与 SKILL.md 一致）：
  - 绝不安装任何浏览器扩展、npm 包或业务软件；
  - 找不到可用浏览器 → 脚本明确报告失败，由 AI 回落 IDE 内置浏览器，不做任何安装尝试。

用法：
  python3 scripts/browser_runner.py detect
  python3 scripts/browser_runner.py probe  --url https://example.com
  python3 scripts/browser_runner.py run    --ops ops.json        # 推荐：一次调用跑完整段走查
  python3 scripts/browser_runner.py shot   --url https://example.com --out Reports/assets/x.png
  python3 scripts/browser_runner.py eval   --script "return document.title"
  python3 scripts/browser_runner.py setviewport --width 1440 --height 900
  python3 scripts/browser_runner.py status
  python3 scripts/browser_runner.py stop

关于 run 与分散调用的取舍：
  部分执行环境会在单条 shell 命令结束后回收其子进程，导致浏览器无法跨调用存活。
  因此走查一律优先使用 run：在同一次调用内拉起浏览器并顺序执行整段操作序列。
  分散命令（probe/shot/eval）仅适用于「进程不会被回收」的环境，用于交互式补拍。
"""

import os
import sys
import json
import time
import socket
import struct
import base64
import tempfile
import argparse
import platform
import subprocess
import urllib.request
import urllib.error

# 会话状态与临时 profile 统一放在系统临时目录（跨环境可写，不污染项目与用户目录）
RUNTIME_DIR = os.path.join(tempfile.gettempdir(), 'design_review_runner')
STATE_FILE = os.path.join(RUNTIME_DIR, 'browser_session.json')
PROFILE_ROOT = os.path.join(RUNTIME_DIR, 'profiles')


# ──────────────────────────────────────────────────────────────
#  1. 浏览器二进制探测（跨平台，含 Playwright / Puppeteer 已下载的缓存）
# ──────────────────────────────────────────────────────────────

def _playwright_browser_candidates():
    """Playwright 缓存里已下载的 Chrome for Testing（用户机器上可能已存在，无需安装）。"""
    home = os.path.expanduser('~')
    system = platform.system()
    if system == 'Darwin':
        cache = os.path.join(home, 'Library', 'Caches', 'ms-playwright')
        rel = 'chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
        rel_arm = 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
        # 旧版本目录名形如 chrome-mac-x64
        rels = [rel, rel_arm, 'chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing']
    elif system == 'Windows':
        cache = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ms-playwright')
        rels = ['chrome-win/chrome.exe']
    else:
        cache = os.path.join(home, '.cache', 'ms-playwright')
        rels = ['chrome-linux/chrome']

    out = []
    if not os.path.isdir(cache):
        return out
    try:
        entries = sorted(os.listdir(cache))
    except OSError:
        return out
    for entry in entries:
        if not entry.startswith('chromium-') or 'headless' in entry:
            continue
        for rel in rels:
            p = os.path.join(cache, entry, rel)
            if os.path.exists(p):
                out.append(p)
    return out


def _puppeteer_browser_candidates():
    """Puppeteer 缓存里已下载的 Chrome。"""
    home = os.path.expanduser('~')
    system = platform.system()
    if system == 'Darwin':
        base = os.path.join(home, '.cache', 'puppeteer', 'chrome')
    elif system == 'Windows':
        base = os.path.join(home, '.cache', 'puppeteer', 'chrome')
    else:
        base = os.path.join(home, '.cache', 'puppeteer', 'chrome')

    out = []
    if not os.path.isdir(base):
        return out
    for root, dirs, files in os.walk(base):
        for fn in files:
            if fn in ('Google Chrome for Testing', 'chrome', 'chrome.exe'):
                out.append(os.path.join(root, fn))
    return out


def detect_browsers():
    """返回本机已存在的可用浏览器二进制路径列表（按推荐优先级排序）。"""
    home = os.path.expanduser('~')
    system = platform.system()
    candidates = []

    if system == 'Darwin':
        candidates += [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
            '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
        ]
    elif system == 'Windows':
        pf = os.environ.get('ProgramFiles', r'C:\Program Files')
        pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        local = os.environ.get('LOCALAPPDATA', '')
        candidates += [
            os.path.join(pf, r'Google\Chrome\Application\chrome.exe'),
            os.path.join(pf86, r'Google\Chrome\Application\chrome.exe'),
            os.path.join(local, r'Google\Chrome\Application\chrome.exe'),
            os.path.join(pf86, r'Microsoft\Edge\Application\msedge.exe'),
            os.path.join(pf, r'Microsoft\Edge\Application\msedge.exe'),
        ]
    else:
        for name in ('google-chrome', 'google-chrome-stable', 'chromium',
                     'chromium-browser', 'microsoft-edge', 'microsoft-edge-stable'):
            for d in os.environ.get('PATH', '').split(os.pathsep):
                p = os.path.join(d, name)
                if os.path.exists(p):
                    candidates.append(p)
        candidates += [
            '/usr/bin/google-chrome', '/usr/bin/chromium',
            '/usr/bin/chromium-browser', '/snap/bin/chromium',
        ]

    # Playwright / Puppeteer 缓存（用户机器上可能早已存在）
    candidates += _playwright_browser_candidates()
    candidates += _puppeteer_browser_candidates()

    seen, result = set(), []
    for c in candidates:
        c = os.path.abspath(c)
        if c in seen:
            continue
        if os.path.exists(c):
            seen.add(c)
            result.append(c)
    return result


# ──────────────────────────────────────────────────────────────
#  2. 极简 WebSocket 客户端（CDP 需要，纯标准库实现）
# ──────────────────────────────────────────────────────────────

def _read_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('WebSocket 连接已断开')
        buf += chunk
    return buf


class MiniWebSocket:
    """只实现 CDP 所需的子集：文本帧收发、掩码、分片与 ping/pong。"""

    def __init__(self, url, timeout=30):
        # ws://127.0.0.1:PORT/devtools/page/XXXX
        assert url.startswith('ws://'), '仅支持 ws://'
        rest = url[len('ws://'):]
        hostport, _, path = rest.partition('/')
        host, _, port = hostport.partition(':')
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self.sock.settimeout(timeout)

        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f'GET /{path} HTTP/1.1\r\n'
            f'Host: {hostport}\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {key}\r\n'
            'Sec-WebSocket-Version: 13\r\n\r\n'
        )
        self.sock.sendall(handshake.encode())

        # 读到响应头结束
        header = b''
        while b'\r\n\r\n' not in header:
            header += self.sock.recv(4096)
        if b'101' not in header.split(b'\r\n')[0]:
            raise ConnectionError('WebSocket 握手失败：%s' % header.split(b'\r\n')[0])

    def send_text(self, text):
        payload = text.encode('utf-8')
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header = bytes([0x81, 0x80 | n])
        elif n < 65536:
            header = bytes([0x81, 0x80 | 126]) + struct.pack('>H', n)
        else:
            header = bytes([0x81, 0x80 | 127]) + struct.pack('>Q', n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _recv_frame(self):
        b1, b2 = _read_exact(self.sock, 2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack('>H', _read_exact(self.sock, 2))[0]
        elif length == 127:
            length = struct.unpack('>Q', _read_exact(self.sock, 8))[0]
        if b2 & 0x80:
            mask = _read_exact(self.sock, 4)
        else:
            mask = None
        data = _read_exact(self.sock, length) if length else b''
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return fin, opcode, data

    def recv_text(self):
        """收一条完整的文本消息（按 FIN 位拼接分片，自动回 pong）。"""
        buffer = b''
        while True:
            fin, opcode, data = self._recv_frame()
            if opcode == 0x9:      # ping → 回 pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode == 0xA:      # pong
                continue
            if opcode == 0x8:      # close
                raise ConnectionError('WebSocket 已被对端关闭')
            if opcode in (0x1, 0x2):   # 新消息（可能被分片）
                buffer = data
            elif opcode == 0x0:        # 续帧
                buffer += data
            else:
                continue
            if fin:
                return buffer.decode('utf-8', errors='replace')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class CDPPage:
    """基于页面级 WebSocket 的极简 CDP 客户端（无需 sessionId）。"""

    def __init__(self, ws_url, timeout=30):
        self.ws = MiniWebSocket(ws_url, timeout=timeout)
        self._id = 0

    def call(self, method, params=None, timeout=60):
        self._id += 1
        my_id = self._id
        self.ws.send_text(json.dumps({
            'id': my_id, 'method': method, 'params': params or {}
        }))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv_text()
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get('id') == my_id:
                if 'error' in msg:
                    raise RuntimeError('CDP %s 失败: %s' % (method, msg['error']))
                return msg.get('result', {})
            # 忽略其它事件通知
        raise TimeoutError('CDP %s 超时' % method)

    def evaluate(self, script):
        """执行 JS 并返回其值（脚本需用 return 返回值）。"""
        r = self.call('Runtime.evaluate', {
            'expression': '(function(){%s})()' % script,
            'returnByValue': True,
            'awaitPromise': True,
        })
        result = r.get('result', {})
        if result.get('subtype') == 'error':
            raise RuntimeError('页面内 JS 报错: %s' % result.get('description'))
        return result.get('value')

    def set_viewport(self, width, height, dpr=1, mobile=False):
        """通过 CDP 强制设定视口尺寸（比 --window-size 精确，不受浏览器边框影响）。"""
        self.call('Emulation.setDeviceMetricsOverride', {
            'width': int(width), 'height': int(height),
            'deviceScaleFactor': float(dpr), 'mobile': bool(mobile),
        })

    def navigate(self, url, wait=1.5):
        self.call('Page.enable')
        self.call('Page.navigate', {'url': url})
        time.sleep(wait)

    def screenshot(self, out_path, full_page=False):
        params = {'format': 'png'}
        if full_page:
            # 取整页尺寸后再截，避免部分后端 fullPage 不生效
            metrics = self.evaluate(
                "return JSON.stringify({w:document.documentElement.scrollWidth,"
                "h:document.documentElement.scrollHeight})"
            )
            try:
                m = json.loads(metrics)
                self.call('Emulation.setDeviceMetricsOverride', {
                    'width': int(m['w']), 'height': int(m['h']),
                    'deviceScaleFactor': 1, 'mobile': False,
                })
            except (ValueError, TypeError, KeyError):
                pass
            r = self.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})
        else:
            r = self.call('Page.captureScreenshot', params)

        data = r.get('data')
        if not data:
            raise RuntimeError('截图失败：CDP 未返回图像数据')
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, 'wb') as f:
            f.write(base64.b64decode(data))
        return out_path


# ──────────────────────────────────────────────────────────────
#  3. 会话管理（拉起 / 复用 / 关闭）
# ──────────────────────────────────────────────────────────────

def _http_json(url, method='GET', timeout=3):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode('utf-8', errors='replace')
    return json.loads(body) if body.strip() else {}


def _wait_for_cdp(port, timeout=25):
    """轮询 CDP HTTP 端点，直到浏览器就绪。"""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            return _http_json('http://127.0.0.1:%d/json/version' % port)
        except (urllib.error.URLError, OSError, ValueError) as e:
            last_err = e
            time.sleep(0.4)
    raise TimeoutError('浏览器 CDP 端口 %d 未就绪（%s）' % (port, last_err))


def _pick_free_port(start=9333, tries=40):
    for port in range(start, start + tries):
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError('找不到可用端口')


def _read_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(state):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


def _is_alive(state):
    if not state:
        return False
    try:
        _http_json('http://127.0.0.1:%d/json/version' % state['port'], timeout=2)
        return True
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return False


def launch(binary=None, width=1440, height=900, dpr=1, headless=False, url='about:blank'):
    """拉起浏览器（独立临时 profile + 随机 CDP 端口），返回 state。"""
    state = _read_state()
    if _is_alive(state):
        return state, False  # 复用已有会话

    if binary and not os.path.exists(binary):
        raise RuntimeError('BROWSER_NOT_FOUND：指定的浏览器二进制不存在：%s' % binary)

    if not binary:
        found = detect_browsers()
        if not found:
            raise RuntimeError(
                'NO_BROWSER_FOUND：本机未探测到任何可用的 Chromium 系浏览器二进制。'
                '（不会尝试安装，请回落 IDE 内置浏览器）'
            )
        binary = found[0]

    port = _pick_free_port()
    profile_dir = os.path.join(PROFILE_ROOT, 'run-%d' % port)
    os.makedirs(profile_dir, exist_ok=True)

    args = [
        binary,
        '--remote-debugging-port=%d' % port,
        # 独立 profile：既避免与用户正在使用的 Chrome 冲突，
        # 也规避 Chrome 136+ 在默认 profile 下忽略调试端口的安全策略。
        '--user-data-dir=%s' % profile_dir,
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-background-networking',
        '--disable-features=Translate,MediaRouter',
        '--window-size=%d,%d' % (width, height),
    ]
    if headless:
        args.append('--headless=new')
    args.append(url)

    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _wait_for_cdp(port)

    state = {
        'pid': proc.pid, 'port': port, 'binary': binary,
        'profile': profile_dir, 'headless': headless,
        # 视口随会话持久化：CDP 的 Emulation 覆盖只对单个连接有效，
        # 必须记录下来，在每次 connect 时重放，否则截图尺寸会随浏览器边框漂移。
        'width': int(width), 'height': int(height), 'dpr': float(dpr),
    }
    _write_state(state)
    return state, True


def _first_page_ws(port, create_url=None):
    """取页面级 WebSocket 地址；必要时先新建一个空白页。"""
    try:
        targets = _http_json('http://127.0.0.1:%d/json/list' % port)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError('无法获取页面列表：%s' % e)
    pages = [t for t in targets if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    if not pages:
        # 新版 Chrome 要求 /json/new 用 PUT
        target_url = create_url or 'about:blank'
        for method in ('PUT', 'GET'):
            try:
                _http_json('http://127.0.0.1:%d/json/new?%s' % (port, target_url), method=method)
                break
            except (urllib.error.URLError, OSError, ValueError):
                continue
        time.sleep(1.0)
        targets = _http_json('http://127.0.0.1:%d/json/list' % port)
        pages = [t for t in targets if t.get('type') == 'page' and t.get('webSocketDebuggerUrl')]
    if not pages:
        raise RuntimeError('未找到可用的页面目标')
    return pages[0]['webSocketDebuggerUrl']


def connect(state=None, apply_viewport=True):
    """连接到已有会话；默认自动重放会话记录的视口，保证截图尺寸确定。"""
    state = state or _read_state()
    if not _is_alive(state):
        raise RuntimeError('NO_SESSION：当前没有运行中的走查浏览器会话，请先执行 launch/probe')
    page = CDPPage(_first_page_ws(state['port']))
    if apply_viewport and state.get('width') and state.get('height'):
        page.set_viewport(state['width'], state['height'], state.get('dpr', 1))
    return page


# ──────────────────────────────────────────────────────────────
#  4. 命令行入口
# ──────────────────────────────────────────────────────────────

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_detect(args):
    found = detect_browsers()
    _emit({
        'platform': platform.system(),
        'found': found,
        'count': len(found),
        'verdict': 'L1_AVAILABLE' if found else 'L1_UNAVAILABLE',
        'note': ('可直接拉起独立浏览器窗口' if found
                 else '未探测到浏览器二进制，不会尝试安装，请回落 IDE 内置浏览器'),
    })
    return 0 if found else 1


def cmd_probe(args):
    state, started = launch(
        binary=args.binary, width=args.width, height=args.height,
        dpr=args.dpr, headless=args.headless, url=args.url,
    )
    page = connect(state)
    page.set_viewport(args.width, args.height, args.dpr)
    page.navigate(args.url)
    info = json.loads(page.evaluate(
        "return JSON.stringify({iw:innerWidth,ih:innerHeight,dpr:devicePixelRatio,"
        "url:location.href,title:document.title})"
    ))
    _emit({
        'status': 'ok',
        'reused_existing': not started,
        'binary': state['binary'],
        'cdp_port': state['port'],
        'viewport': info,
        'verdict': ('VIEWPORT_OK' if info['iw'] >= 1280 else 'VIEWPORT_LIMITED'),
    })
    return 0


def cmd_eval(args):
    page = connect()
    value = page.evaluate(args.script)
    _emit({'status': 'ok', 'value': value})
    return 0


def cmd_setviewport(args):
    page = connect()
    page.set_viewport(args.width, args.height, args.dpr)
    state = _read_state() or {}
    state.update({'width': args.width, 'height': args.height, 'dpr': args.dpr})
    _write_state(state)
    info = json.loads(page.evaluate(
        "return JSON.stringify({iw:innerWidth,ih:innerHeight,dpr:devicePixelRatio})"
    ))
    _emit({'status': 'ok', 'viewport': info})
    return 0


def cmd_shot(args):
    page = connect()
    if args.url:
        page.navigate(args.url)
    if args.width and args.height:
        page.set_viewport(args.width, args.height, args.dpr)
        state = _read_state() or {}
        state.update({'width': args.width, 'height': args.height, 'dpr': args.dpr})
        _write_state(state)
    out = page.screenshot(args.out, full_page=args.full_page)
    info = json.loads(page.evaluate(
        "return JSON.stringify({iw:innerWidth,ih:innerHeight,dpr:devicePixelRatio})"
    ))
    _emit({'status': 'ok', 'out': out, 'viewport': info})
    return 0


def _stop_session():
    """关闭当前会话进程并清理状态。"""
    state = _read_state()
    if not state:
        return None
    try:
        if platform.system() == 'Windows':
            subprocess.run(['taskkill', '/PID', str(state['pid']), '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(state['pid'], 15)
    except (OSError, ProcessLookupError):
        pass
    _clear_state()
    return state.get('pid')


def cmd_stop(args):
    pid = _stop_session()
    if pid is None:
        _emit({'status': 'ok', 'note': '没有运行中的会话'})
    else:
        _emit({'status': 'ok', 'stopped_pid': pid})
    return 0


def cmd_status(args):
    state = _read_state()
    alive = _is_alive(state)
    _emit({'session': state, 'alive': alive})
    return 0 if alive else 1


def _run_ops(page, ops):
    """在单个会话内顺序执行操作序列，返回结果字典。

    支持的操作：
      {"op":"setviewport","width":1440,"height":900,"dpr":2}
      {"op":"navigate","url":"https://...","wait":1.5}
      {"op":"eval","script":"return ...","name":"结果键名"}
      {"op":"shot","out":"Reports/assets/x.png","full_page":false}
      {"op":"wait","seconds":1.0}
    """
    results = {}
    for idx, item in enumerate(ops):
        op = (item.get('op') or '').lower()
        if op == 'setviewport':
            page.set_viewport(item.get('width', 1440), item.get('height', 900),
                              item.get('dpr', 1))
            results['viewport'] = json.loads(page.evaluate(
                "return JSON.stringify({iw:innerWidth,ih:innerHeight,dpr:devicePixelRatio})"
            ))
        elif op == 'navigate':
            page.navigate(item['url'], wait=item.get('wait', 1.5))
            results.setdefault('navigations', []).append(item['url'])
        elif op == 'eval':
            value = page.evaluate(item['script'])
            results[item.get('name') or ('eval_%d' % idx)] = value
        elif op == 'shot':
            results.setdefault('shots', []).append(
                page.screenshot(item['out'], full_page=item.get('full_page', False))
            )
        elif op == 'wait':
            time.sleep(float(item.get('seconds', 1.0)))
        else:
            results.setdefault('skipped', []).append(op or '(空)')
    return results


def cmd_run(args):
    """一次性拉起浏览器 + 执行整段操作序列（推荐：不依赖跨进程会话存活）。"""
    with open(args.ops, 'r', encoding='utf-8') as f:
        spec = json.load(f)
    if isinstance(spec, dict):
        launch_cfg = spec.get('launch', {})
        ops = spec.get('ops', [])
    else:
        launch_cfg, ops = {}, spec

    state, started = launch(
        binary=launch_cfg.get('binary'),
        width=launch_cfg.get('width', 1440),
        height=launch_cfg.get('height', 900),
        dpr=launch_cfg.get('dpr', 1),
        headless=launch_cfg.get('headless', False),
        url=launch_cfg.get('url', 'about:blank'),
    )
    page = connect(state)
    results = _run_ops(page, ops)

    if args.keep_open is False:
        _stop_session()

    _emit({
        'status': 'ok',
        'reused_existing': not started,
        'binary': state['binary'],
        'cdp_port': state['port'],
        'results': results,
    })
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='跨平台浏览器走查执行器（零第三方依赖）'
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('detect', help='探测本机可用的浏览器二进制')
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser('probe', help='拉起浏览器并输出 CDP 端点与实测视口')
    p.add_argument('--url', default='about:blank')
    p.add_argument('--binary', default=None)
    p.add_argument('--width', type=int, default=1440)
    p.add_argument('--height', type=int, default=900)
    p.add_argument('--dpr', type=float, default=1)
    p.add_argument('--headless', action='store_true')
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser('eval', help='在当前页面执行 JS（需用 return 返回值）')
    p.add_argument('--script', required=True)
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser('setviewport', help='设定视口尺寸')
    p.add_argument('--width', type=int, default=1440)
    p.add_argument('--height', type=int, default=900)
    p.add_argument('--dpr', type=float, default=1)
    p.set_defaults(func=cmd_setviewport)

    p = sub.add_parser('shot', help='截图')
    p.add_argument('--out', required=True)
    p.add_argument('--url', default=None)
    p.add_argument('--width', type=int, default=None)
    p.add_argument('--height', type=int, default=None)
    p.add_argument('--dpr', type=float, default=1)
    p.add_argument('--full-page', action='store_true')
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser('status', help='查询会话状态')
    p.set_defaults(func=cmd_status)

    p = sub.add_parser('run', help='一次性拉起浏览器并执行整段操作序列（推荐）')
    p.add_argument('--ops', required=True, help='操作序列 JSON 文件路径')
    p.add_argument('--keep-open', action='store_true',
                   help='执行完保留浏览器窗口（默认执行完关闭）')
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('stop', help='关闭会话')
    p.set_defaults(func=cmd_stop)

    args = parser.parse_args()
    try:
        sys.exit(args.func(args))
    except Exception as e:
        _emit({'status': 'error', 'error': str(e)})
        sys.exit(1)


if __name__ == '__main__':
    main()
