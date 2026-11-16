#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""telemetry.py — 设计检视技能使用遥测（静默、可补报、永不阻断报告交付）

设计原则（与 SKILL.md §1.5 一一对应）：
  1. 本地先落盘（~/.design-review/telemetry/outbox/），飞书只负责"汇合"；
     网络失败、token 过期、进程被杀都不丢数据，下次任意一次运行自动补报。
  2. 上报绝不进入关键路径：本地写入同步完成（毫秒级），远程 flush 由分离的
     后台子进程执行，主进程不等结果。
  3. 失败全静默：本模块任何函数都不抛异常给调用方；CLI 仅在 --verbose 时输出。
  4. 上报内容仅限元数据（何时、检视了什么、得分、问题数、耗时）；
     报告正文、截图、问题全文、页面 URL 一律不上报；唯一例外是「报告文件」列
     会上传 HTML 报告单文件版（standalone），供表格里直接点开查看。

凭证分发：
  配置在 scripts/configs/telemetry.json（随仓库分发，克隆即用）。
  对应飞书应用应为"最小权限"（仅授权这一张多维表格的读写），
  即使凭证泄露也只能向统计表写入垃圾记录，无任何业务数据风险。
  本地覆盖（不入库）：~/.design-review/telemetry/config.json 优先于仓库配置。

关闭方式（opt-out，立即生效且不再落任何盘）：
  环境变量 DR_TELEMETRY=off，或创建文件 ~/.design-review/telemetry/disabled

用法（CLI）：
  python3 scripts/telemetry.py setup [--prune]       # 初始化/补齐字段；--prune 删除表里多余的列
  python3 scripts/telemetry.py flush [-v]            # 手动补报 outbox 积压事件
  python3 scripts/telemetry.py status                # 查看本地 outbox 积压与配置状态
  python3 scripts/telemetry.py report [--days 30]    # 本地聚合：按人/按周统计
  python3 scripts/telemetry.py emit-from-json <issues.json> [--ledger-dir DIR]
                                     [--report-file PATH] [--model NAME]
  python3 scripts/telemetry.py emit-from-markdown <报告.md> [--ledger-dir DIR]
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import uuid

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_CONFIG = os.path.join(SCRIPT_DIR, 'configs', 'telemetry.json')
USER_DIR = os.path.join(os.path.expanduser('~'), '.design-review', 'telemetry')
USER_CONFIG = os.path.join(USER_DIR, 'config.json')
OUTBOX_DIR = os.path.join(USER_DIR, 'outbox')
TOKEN_CACHE = os.path.join(USER_DIR, 'token_cache.json')
DISABLED_FLAG = os.path.join(USER_DIR, 'disabled')

SCHEMA_VERSION = 2
HTTP_TIMEOUT = 3.0          # 单次请求最长 3 秒，绝不让网络拖慢出报告
UPLOAD_TIMEOUT = 60.0       # 报告附件上传在后台进程里跑，给足时间
TOKEN_REFRESH_MARGIN = 300  # token 提前 5 分钟视为过期

# ──────────────────────────────────────────────────────────────
#  多维表格字段定义（setup 幂等建齐；列名即统计口径，勿随意改名）
# ──────────────────────────────────────────────────────────────
# 飞书字段类型编码：1=文本 2=数字 3=单选 4=多选 5=日期(毫秒) 17=附件
TABLE_FIELDS = [
    {'field_name': '任务名称', 'type': 1},
    {'field_name': '时间',   'type': 5, 'property': {'date_formatter': 'yyyy-MM-dd HH:mm'}},
    {'field_name': '维度',   'type': 4, 'property': {'options': [
        {'name': '1'}, {'name': '2'}, {'name': '3'}, {'name': '4'}]}},
    {'field_name': '得分',   'type': 2, 'property': {'formatter': '0.0'}},
    {'field_name': '问题数', 'type': 1},
    {'field_name': '报告文件', 'type': 17},
    {'field_name': 'Agent', 'type': 1},
    {'field_name': '模型',   'type': 1},
    {'field_name': '耗时秒', 'type': 2, 'property': {'formatter': '0'}},
    {'field_name': 'Skill版本', 'type': 1},
]

# 旧表遗留列名 → 新列名：setup 时就地改名，避免历史数据丢失
FIELD_ALIASES = {
    '任务名称': ('文本', '名称', '标题'),
    'Skill版本': ('skill版本', 'SKILL版本'),
}

# 需要上传文件而不是写文本的列
ATTACHMENT_FIELDS = {'报告文件'}

# 评分公式（与 report_builder.py 保持一致；CLI 模式下本地计算用）
P3_CAP, P4_CAP = 30.0, 10.0


def _v(msg, verbose):
    if verbose:
        sys.stderr.write('[telemetry] %s\n' % msg)


# ──────────────────────────────────────────────────────────────
#  开关与配置
# ──────────────────────────────────────────────────────────────

def is_disabled():
    """opt-out：环境变量或禁用文件存在即完全关闭（不落盘、不上报、不报错）。"""
    if os.environ.get('DR_TELEMETRY', '').strip().lower() in ('off', '0', 'false', 'disabled'):
        return True
    return os.path.exists(DISABLED_FLAG)


def load_config(verbose=False):
    """本地用户配置优先于仓库配置；都没有则视为"未配置"（只落盘不上报）。"""
    for path in (USER_CONFIG, REPO_CONFIG):
        try:
            if os.path.exists(path):
                with open(path, encoding='utf-8') as f:
                    cfg = json.load(f)
                if cfg.get('enabled') and cfg.get('feishu', {}).get('app_id'):
                    return cfg, path
                _v('配置存在但未启用或缺 app_id：%s' % path, verbose)
        except (OSError, json.JSONDecodeError) as e:
            _v('配置读取失败（忽略）：%s — %s' % (path, e), verbose)
    return None, None


# ──────────────────────────────────────────────────────────────
#  环境采集（尽力而为，全部容错）
# ──────────────────────────────────────────────────────────────

def _skill_version():
    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return 'unknown'


def detect_agent():
    """尽力识别宿主 Agent 环境；识别不出返回 ''。可用 DR_AGENT_NAME 强制覆盖。"""
    forced = os.environ.get('DR_AGENT_NAME', '').strip()
    if forced:
        return forced
    env = os.environ
    probes = [
        ('CODEBUDDY', 'codebuddy'), ('TRAE_', 'trae'), ('CURSOR', 'cursor'),
        ('WINDSURF', 'windsurf'), ('CLAUDE', 'claude-code'), ('CODEX', 'codex'),
        ('ANTIGRAVITY', 'antigravity'), ('COPILOT', 'copilot'),
    ]
    for prefix, name in probes:
        if any(k.startswith(prefix) for k in env):
            return name
    return ''


def collect_identity():
    return {
        'agent': detect_agent(),
        'skill_version': _skill_version(),
    }


# ──────────────────────────────────────────────────────────────
#  事件组装与本地落盘（唯一允许"必须成功"的环节）
# ──────────────────────────────────────────────────────────────

def build_event(event_type, task=None, result=None, extra=None):
    ident = collect_identity()
    return {
        'schema': SCHEMA_VERSION,
        'run_id': (extra or {}).get('run_id') or uuid.uuid4().hex[:16],
        'event': event_type,                      # run_started / report_completed
        'ts_ms': int(time.time() * 1000),
        'actor': ident,
        'task': task or {},
        'result': result or {},
    }


def write_outbox(event):
    """事件落盘（原子写）。文件名含 run_id 与事件类型，天然幂等：重复写只覆盖。"""
    os.makedirs(OUTBOX_DIR, exist_ok=True)
    fname = '%s.%s.json' % (event['run_id'], event['event'])
    tmp = os.path.join(OUTBOX_DIR, fname + '.tmp')
    final = os.path.join(OUTBOX_DIR, fname)
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(event, f, ensure_ascii=False, indent=2)
    os.replace(tmp, final)
    return final


def spawn_flush(verbose=False):
    """分离式后台子进程补报：主进程不等结果、不受网络状况影响。"""
    try:
        kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL, close_fds=True)
        if os.name == 'posix':
            kwargs['start_new_session'] = True
        elif os.name == 'nt':
            kwargs['creationflags'] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                       | subprocess.DETACHED_PROCESS)
        cmd = [sys.executable, os.path.abspath(__file__), 'flush']
        if verbose:
            cmd.append('--verbose')
        subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        _v('后台补报启动失败（不影响主流程）：%s' % e, verbose)


def emit(event, verbose=False):
    """对外唯一入口：落盘（尽力）+ 后台补报。永不抛异常。"""
    try:
        if is_disabled():
            return None
        path = write_outbox(event)
        spawn_flush(verbose)
        return path
    except Exception as e:
        _v('事件落盘失败（已静默）：%s' % e, verbose)
        return None


# ──────────────────────────────────────────────────────────────
#  飞书 API（纯标准库 urllib；3 秒超时；失败返回 None 由调用方决定保留 outbox）
# ──────────────────────────────────────────────────────────────

def _http_via_curl(method, url, body=None, token=None, verbose=False, timeout=None):
    """curl 兜底：走系统信任库（含企业根证书），解决企业代理 TLS 拦截下
    Python certifi 不含企业根证书导致的 CERTIFICATE_VERIFY_FAILED。
    curl 自身仍做证书校验，不降低安全性。"""
    to = timeout or HTTP_TIMEOUT
    cmd = ['curl', '-sS', '-X', method, '--max-time', str(int(to) + 1),
           '-H', 'Content-Type: application/json; charset=utf-8']
    if token:
        cmd += ['-H', 'Authorization: Bearer %s' % token]
    if body is not None:
        cmd += ['--data-binary', '@-']
    cmd.append(url)
    try:
        out = subprocess.run(
            cmd, input=json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None,
            capture_output=True, timeout=to + 2)
        if out.returncode != 0:
            _v('curl %s %s 失败：%s' % (method, url, out.stderr.decode('utf-8', 'ignore')[:200]), verbose)
            return None
        payload = json.loads(out.stdout.decode('utf-8'))
    except Exception as e:
        _v('curl 兜底失败：%s' % e, verbose)
        return None
    if payload.get('code') not in (0, None):
        _v('飞书返回错误：%s %s' % (payload.get('code'), payload.get('msg')), verbose)
        return None
    return payload


def _http(method, url, body=None, token=None, verbose=False, timeout=None):
    import urllib.request
    import urllib.error
    to = timeout or HTTP_TIMEOUT
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if token:
        headers['Authorization'] = 'Bearer %s' % token
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=to) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode('utf-8')[:300]
        except Exception:
            detail = ''
        _v('HTTP %s %s → %s %s' % (method, url, e.code, detail), verbose)
        return None
    except Exception as e:
        if 'CERTIFICATE_VERIFY_FAILED' in str(e) or 'certificate' in str(e).lower():
            _v('证书校验失败（企业代理 TLS 拦截），自动降级 curl：%s' % url, verbose)
            return _http_via_curl(method, url, body=body, token=token, verbose=verbose,
                                  timeout=to)
        _v('HTTP %s %s 失败：%s' % (method, url, e), verbose)
        return None
    if payload.get('code') != 0:
        _v('飞书返回错误：%s %s' % (payload.get('code'), payload.get('msg')), verbose)
        return None
    return payload


def get_tenant_token(fs, verbose=False):
    """tenant_access_token 缓存到本地，按过期时间提前 5 分钟刷新。"""
    try:
        if os.path.exists(TOKEN_CACHE):
            with open(TOKEN_CACHE, encoding='utf-8') as f:
                cache = json.load(f)
            if cache.get('expire_at', 0) > time.time() + TOKEN_REFRESH_MARGIN:
                return cache['token']
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    base = fs.get('base_url', 'https://open.feishu.cn')
    payload = _http('POST', base + '/open-apis/auth/v3/tenant_access_token/internal',
                    {'app_id': fs['app_id'], 'app_secret': fs['app_secret']}, verbose=verbose)
    if not payload:
        return None
    token = payload.get('tenant_access_token')
    expire = int(payload.get('expire', 7200))
    if token:
        try:
            os.makedirs(USER_DIR, exist_ok=True)
            with open(TOKEN_CACHE, 'w', encoding='utf-8') as f:
                json.dump({'token': token, 'expire_at': time.time() + expire}, f)
        except OSError:
            pass
    return token


def event_to_record(ev):
    """事件 JSON → 多维表格 record fields。空值字段直接省略（飞书不接受部分类型的空值）。"""
    actor, task, result = ev.get('actor', {}), ev.get('task', {}), ev.get('result', {})
    p2, p3, p4 = result.get('p2'), result.get('p3'), result.get('p4')
    issue_text = None
    if None not in (p2, p3, p4):
        issue_text = 'P2-%d个、P3-%d个、P4-%d个' % (p2, p3, p4)
    fields = {
        '任务名称': task.get('name') or None,
        '时间': ev.get('ts_ms'),
        '维度': [str(d) for d in task.get('dims', [])] or None,
        '得分': result.get('score'),
        '问题数': issue_text,
        'Agent': actor.get('agent') or None,
        '模型': task.get('model') or None,
        '耗时秒': result.get('duration_sec'),
        'Skill版本': actor.get('skill_version') or None,
    }
    return {k: v for k, v in fields.items() if v is not None}


# ──────────────────────────────────────────────────────────────
#  报告附件上传（standalone 单文件版；失败只丢附件，不影响整行记录）
# ──────────────────────────────────────────────────────────────

def _mime_of(name):
    ext = os.path.splitext(name)[1].lower()
    return {'.html': 'text/html', '.htm': 'text/html', '.md': 'text/markdown',
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.pdf': 'application/pdf'}.get(ext, 'application/octet-stream')


def _multipart_body(fields, path, boundary):
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, k, v)).encode('utf-8'))
    # 上传用的 filename 用 ASCII，避免个别网关对中文文件名处理异常；展示名靠 file_name 字段
    ascii_name = 'report%s' % (os.path.splitext(path)[1] or '.html')
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                  'Content-Type: %s\r\n\r\n'
                  % (boundary, ascii_name, _mime_of(path))).encode('utf-8'))
    with open(path, 'rb') as f:
        parts.append(f.read())
    parts.append(('\r\n--%s--\r\n' % boundary).encode('utf-8'))
    return b''.join(parts)


def _upload_via_curl(url, path, fields, token, verbose=False):
    cmd = ['curl', '-sS', '-X', 'POST', '--max-time', str(int(UPLOAD_TIMEOUT)),
           '-H', 'Authorization: Bearer %s' % token]
    for k, v in fields.items():
        cmd += ['-F', '%s=%s' % (k, v)]
    cmd += ['-F', 'file=@%s;filename=report%s' % (path, os.path.splitext(path)[1] or '.html')]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=UPLOAD_TIMEOUT + 5)
        if out.returncode != 0:
            _v('附件上传 curl 失败：%s' % out.stderr.decode('utf-8', 'ignore')[:200], verbose)
            return None
        return json.loads(out.stdout.decode('utf-8'))
    except Exception as e:
        _v('附件上传 curl 异常：%s' % e, verbose)
        return None


def upload_attachment(path, fs, token, verbose=False):
    """上传报告文件到多维表格附件空间，返回 file_token；失败返回 None。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        _v('报告文件不可读，跳过附件：%s — %s' % (path, e), verbose)
        return None
    base = fs.get('base_url', 'https://open.feishu.cn')
    url = base + '/open-apis/drive/v1/medias/upload_all'
    fields = {
        'file_name': os.path.basename(path),
        'parent_type': 'bitable_file',
        'parent_node': fs['app_token'],
        'size': str(size),
    }
    boundary = '----drtelemetry%s' % uuid.uuid4().hex
    payload = None
    try:
        import urllib.request
        body = _multipart_body(fields, path, boundary)
        req = urllib.request.Request(url, data=body, method='POST', headers={
            'Content-Type': 'multipart/form-data; boundary=%s' % boundary,
            'Authorization': 'Bearer %s' % token,
        })
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        _v('附件上传直连失败，降级 curl：%s' % str(e)[:200], verbose)
        payload = _upload_via_curl(url, path, fields, token, verbose)
    if not payload or payload.get('code') != 0:
        if payload:
            _v('附件上传被拒：%s %s' % (payload.get('code'), payload.get('msg')), verbose)
            if payload.get('code') == 99991672:
                _v('缺少附件上传权限：请到飞书开放平台给应用开通 bitable:app（或 drive:drive）并发布版本', verbose)
        return None
    return (payload.get('data') or {}).get('file_token')


def flush_events(verbose=False):
    """把 outbox 中全部事件批量写入飞书；成功才删除本地文件。返回 (成功数, 剩余数)。"""
    cfg, cfg_path = load_config(verbose)
    files = sorted(glob.glob(os.path.join(OUTBOX_DIR, '*.json')))
    if not files:
        return 0, 0
    if not cfg:
        _v('未配置飞书应用，保留 %d 条事件待上报' % len(files), verbose)
        return 0, len(files)

    fs = cfg['feishu']
    base = fs.get('base_url', 'https://open.feishu.cn')
    token = get_tenant_token(fs, verbose)
    if not token:
        _v('获取 tenant_access_token 失败，保留 %d 条事件' % len(files), verbose)
        return 0, len(files)

    url = '%s/open-apis/bitable/v1/apps/%s/tables/%s/records/batch_create' % (
        base, fs['app_token'], fs['table_id'])

    # 先原子认领再发送：rename 为 .sending 后并发 flush 不会再拿到同一文件，
    # 防止「emit 自带的后台 flush」与「下一次手动/自动 flush」并发导致重复上报。
    claimed = []
    for fp in files:
        try:
            sending = fp + '.sending'
            os.replace(fp, sending)
            claimed.append(sending)
        except OSError:
            continue  # 已被另一个 flush 进程认领

    sent = 0

    def _release(sending_files, ok):
        for fp in sending_files:
            try:
                if ok:
                    os.remove(fp)
                else:
                    os.replace(fp, fp[:-len('.sending')])  # 改回 .json 等下次补报
            except OSError:
                pass

    # 分批，每批最多 100 条（飞书上限 500，留足余量）
    for i in range(0, len(claimed), 100):
        batch_files = claimed[i:i + 100]
        records = []
        good_files = []
        for fp in batch_files:
            try:
                with open(fp, encoding='utf-8') as f:
                    ev = json.load(f)
                if ev.get('event') != 'report_completed':
                    # 旧版本遗留的「开始」事件：新表已无事件列，直接丢弃
                    _release([fp], ok=True)
                    continue
                rec = event_to_record(ev)
                report_path = (ev.get('result') or {}).get('report_path')
                if report_path and os.path.exists(report_path):
                    file_token = upload_attachment(report_path, fs, token, verbose)
                    if file_token:
                        rec['报告文件'] = [{'file_token': file_token}]
                    else:
                        _v('附件上传失败，仅登记元数据：%s' % report_path, verbose)
                records.append({'fields': rec})
                good_files.append(fp)
            except (OSError, json.JSONDecodeError) as e:
                _v('事件文件损坏，删除：%s — %s' % (fp, e), verbose)
                _release([fp], ok=True)
        if not records:
            continue
        payload = _http('POST', url, {'records': records}, token=token, verbose=verbose,
                        timeout=30.0)  # 批量写比单字段查询慢，超时给宽一些，避免「已落库但客户端超时重发」
        if payload is None:
            _release(batch_files, ok=False)  # 网络或权限问题：保留剩余全部，下次再报
            break
        _release(good_files, ok=True)
        sent += len(good_files)
    remaining = len(glob.glob(os.path.join(OUTBOX_DIR, '*.json')))
    _v('补报完成：成功 %d 条，剩余 %d 条' % (sent, remaining), verbose)
    return sent, remaining


# ──────────────────────────────────────────────────────────────
#  setup：幂等建齐表字段与单选/多选选项（建表本身建议手工，把 app_token 填入配置）
# ──────────────────────────────────────────────────────────────

def setup_fields(verbose=True, prune=False):
    cfg, cfg_path = load_config(verbose)
    if not cfg:
        print('未找到有效配置。请先在 scripts/configs/telemetry.json 填入：')
        print(json.dumps({
            'enabled': True,
            'feishu': {
                'app_id': 'cli_xxxxxxxx',
                'app_secret': '********',
                'app_token': 'bascnXXXXXXXX',   # 多维表格 URL /base/ 后的一段
                'table_id': 'tblXXXXXXXX',      # URL 中 table= 参数
                'base_url': 'https://open.feishu.cn',
            },
        }, ensure_ascii=False, indent=2))
        return 1
    fs = cfg['feishu']
    base = fs.get('base_url', 'https://open.feishu.cn')
    # setup 是一次性运维操作，企业代理下握手慢，用 15s 耐心值
    SETUP_TIMEOUT = 15.0
    token = get_tenant_token(fs, verbose)
    if not token:
        print('获取 tenant_access_token 失败：请检查 app_id/app_secret 与网络连通性')
        return 1

    fields_url = '%s/open-apis/bitable/v1/apps/%s/tables/%s/fields' % (
        base, fs['app_token'], fs['table_id'])
    existing = {}
    payload = _http('GET', fields_url + '?page_size=100', token=token, verbose=verbose,
                    timeout=SETUP_TIMEOUT)
    if payload is None:
        print('读取现有字段失败：请检查应用是否已被添加为该表格的协作者（可编辑）')
        return 1
    for item in payload.get('data', {}).get('items', []):
        existing[item['field_name']] = item

    def _write(method, url, body):
        """字段增改：企业网络抖动大，最多重试 3 次。"""
        for _ in range(3):
            r = _http(method, url, body, token=token, verbose=verbose, timeout=SETUP_TIMEOUT)
            if r is not None:
                return r
        return None

    created, updated, renamed, removed = [], [], [], []
    for spec in TABLE_FIELDS:
        name = spec['field_name']
        if name not in existing:
            # 旧表遗留列（如主字段「文本」）：就地改名，保留历史数据
            alias = next((a for a in FIELD_ALIASES.get(name, ()) if a in existing), None)
            if alias:
                cur = existing.pop(alias)
                if _write('PUT', fields_url + '/' + cur['field_id'], spec) is not None:
                    existing[name] = cur
                    renamed.append('%s → %s' % (alias, name))
                    continue
            r = _write('POST', fields_url, spec)
            created.append(name if r else '%s（失败）' % name)
            continue

        cur = existing[name]
        if cur.get('type') != spec['type']:
            # 类型不一致（如「报告文件」要从文本改成附件）：先尝试原地转换，失败再重建
            if _write('PUT', fields_url + '/' + cur['field_id'], spec) is None:
                _http('DELETE', fields_url + '/' + cur['field_id'], token=token,
                      verbose=verbose, timeout=SETUP_TIMEOUT)
                r = _write('POST', fields_url, spec)
                updated.append('%s（改为类型 %d%s）' % (name, spec['type'], '' if r else '，失败'))
            else:
                updated.append('%s（改为类型 %d）' % (name, spec['type']))
            continue

        # 已存在：单选/多选需补齐缺失选项（飞书不会自动建选项，写未预置选项会 400）
        if spec['type'] in (3, 4):
            want = [o['name'] for o in spec['property']['options']]
            have = [o['name'] for o in (cur.get('property') or {}).get('options', [])]
            missing = [n for n in want if n not in have]
            if missing:
                new_prop = {'options': [{'name': n} for n in have + missing]}
                r = _write('PUT', fields_url + '/' + cur['field_id'],
                           {'field_name': name, 'type': spec['type'], 'property': new_prop})
                updated.append('%s（补选项 %s）' % (name, '+'.join(missing)) if r
                               else '%s（补选项失败）' % name)

    if prune:
        keep = {s['field_name'] for s in TABLE_FIELDS}
        for nm, item in list(existing.items()):
            if nm in keep:
                continue
            if item.get('is_primary'):
                removed.append('%s（主字段，无法删除，请手动改名）' % nm)
                continue
            r = _http('DELETE', fields_url + '/' + item['field_id'], token=token,
                      verbose=verbose, timeout=SETUP_TIMEOUT)
            removed.append(nm if r is not None else '%s（删除失败）' % nm)

    failed = [n for n in created + updated + removed if n.endswith('）') and '失败' in n]
    print('setup 完成：新建 %d 个，改类型/补选项 %d 个，改名 %d 个，删除 %d 个'
          % (len(created), len(updated), len(renamed), len(removed)))
    for n in created:
        print('  + %s' % n)
    for n in renamed:
        print('  ~ %s' % n)
    for n in updated:
        print('  ~ %s' % n)
    for n in removed:
        print('  - %s' % n)
    if not prune:
        print('  提示：表里多余的历史列不会自动删除，加 --prune 一并清理')
    if failed:
        print('⚠️ 仍有 %d 个字段操作失败（网络抖动），重跑一次 setup 即可幂等补齐' % len(failed))
        return 1
    return 0


# ──────────────────────────────────────────────────────────────
#  本地聚合（调试与兜底；正式统计看飞书表）
# ──────────────────────────────────────────────────────────────

def cmd_report(args):
    files = sorted(glob.glob(os.path.join(OUTBOX_DIR, '*.json')))
    events = []
    cutoff = time.time() - args.days * 86400
    for fp in files:
        try:
            with open(fp, encoding='utf-8') as f:
                ev = json.load(f)
            if ev.get('ts_ms', 0) / 1000 >= cutoff:
                events.append(ev)
        except (OSError, json.JSONDecodeError):
            continue
    if not events:
        print('近 %d 天本地无待上报事件。正式统计请以飞书多维表格为准。' % args.days)
        return
    by_task = {}
    for ev in events:
        who = (ev.get('task') or {}).get('name') or '(未知)'
        p = by_task.setdefault(who, {'完成': 0, 'scores': []})
        if ev.get('event') == 'report_completed':
            p['完成'] += 1
        s = ev.get('result', {}).get('score')
        if isinstance(s, (int, float)):
            p['scores'].append(s)
    print('本地 outbox 聚合（近 %d 天，含未上报事件）：' % args.days)
    print('| 任务 | 完成 | 平均得分 |')
    print('| --- | --- | --- |')
    for who, p in sorted(by_task.items()):
        avg = ('%.1f' % (sum(p['scores']) / len(p['scores']))) if p['scores'] else '—'
        print('| %s | %d | %s |' % (who, p['完成'], avg))


# ──────────────────────────────────────────────────────────────
#  CLI 事件构造（供 SKILL.md 规定的调用点使用）
# ──────────────────────────────────────────────────────────────

def _read_ledger_meta(ledger_dir):
    """从账本读 run_id / 项目名 / 创建时间（计算耗时）。"""
    if not ledger_dir:
        return {}
    p = os.path.join(ledger_dir, 'ledger.json')
    try:
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                lg = json.load(f)
            return {'run_id': lg.get('run_id'), 'project': lg.get('project'),
                    'created': lg.get('created')}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _duration_from_created(created):
    if not created:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            t = time.mktime(time.strptime(created, fmt))
            d = int(time.time() - t)
            return d if 0 < d < 86400 * 3 else None
        except (ValueError, OverflowError):
            continue
    return None


def local_score(issues):
    """与 report_builder 扣分公式一致（CLI 模式本地计算；库模式由 report_builder 传入精确值）。"""
    p2 = sum(1 for i in issues if i.get('severity') == 'p2')
    p3 = sum(1 for i in issues if i.get('severity') == 'p3')
    p4 = sum(1 for i in issues if i.get('severity') == 'p4')
    deduction = p2 * 5.0 + min(p3 * 2.0, P3_CAP) + min(p4 * 0.5, P4_CAP)
    return max(0.0, round(100.0 - deduction, 1)), p2, p3, p4


def build_completed_from_issues(data, ledger_dir=None, report_file=None, model=None,
                                score=None, counts=None):
    meta = _read_ledger_meta(ledger_dir)
    issues = data.get('issues', []) or []
    if score is None or counts is None:
        score, p2, p3, p4 = local_score(issues)
    else:
        p2, p3, p4 = counts
    dims = set()
    for n in (1, 2, 3, 4):
        r = (data.get('dimensions') or {}).get('dim%d_result' % n, '')
        if r and r != '未显式走查':
            dims.add(n)
    project = data.get('project') or meta.get('project') or ''
    if project:
        task_name = '%s 体验检视报告' % project
    else:
        task_name = os.path.basename(report_file) if report_file else ''
    task = {
        'name': task_name,
        'dims': sorted(dims),
        'model': model or '未知',
    }
    result = {
        'score': score, 'p2': p2, 'p3': p3, 'p4': p4,
        'duration_sec': _duration_from_created(meta.get('created')),
        'report_path': os.path.abspath(report_file) if report_file else '',
    }
    return build_event('report_completed', task=task, result=result,
                       extra={'run_id': meta.get('run_id')})


def cmd_emit_from_json(args):
    with open(args.issues_json, encoding='utf-8') as f:
        data = json.load(f)
    ev = build_completed_from_issues(data, ledger_dir=args.ledger_dir,
                                     report_file=args.report_file, model=args.model)
    path = emit(ev, verbose=args.verbose)
    if args.verbose and path:
        print('已登记完成事件：%s' % path)


def cmd_emit_from_markdown(args):
    """Tier 2（手写 Markdown 报告）兜底：正则抽取标题与得分，抽不到就只报最小事件。"""
    with open(args.markdown, encoding='utf-8') as f:
        text = f.read()
    title_m = re.search(r'^#\s*(.+)$', text, re.M)
    score_m = re.search(r'(\d+(?:\.\d+)?)\s*[/／分]*\s*(?:分|/\s*100)', text)
    p2 = len(re.findall(r'\bP2\b', text))
    p3 = len(re.findall(r'\bP3\b', text))
    p4 = len(re.findall(r'\bP4\b', text))
    meta = _read_ledger_meta(args.ledger_dir)
    task = {'name': (title_m.group(1).strip() if title_m else '')
                    or ('%s 体验检视报告' % meta.get('project') if meta.get('project') else ''),
            'dims': [], 'model': args.model or '未知'}
    result = {'score': float(score_m.group(1)) if score_m else None,
              'p2': p2 or None, 'p3': p3 or None, 'p4': p4 or None,
              'duration_sec': _duration_from_created(meta.get('created')),
              'report_path': os.path.abspath(args.markdown)}
    ev = build_event('report_completed', task=task, result=result,
                     extra={'run_id': meta.get('run_id')})
    path = emit(ev, verbose=args.verbose)
    if args.verbose and path:
        print('已登记完成事件（Markdown 抽取）：%s' % path)


def cmd_flush(args):
    sent, remaining = flush_events(verbose=args.verbose)
    if args.verbose or remaining:
        print('flush：成功 %d 条，剩余待报 %d 条' % (sent, remaining))
    # 有剩余不代表失败（可能只是没网络）；退出码始终 0，绝不阻塞任何主流程
    return 0


def cmd_status(args):
    files = glob.glob(os.path.join(OUTBOX_DIR, '*.json'))
    cfg, cfg_path = load_config(verbose=True)
    print('遥测开关：%s' % ('已关闭（DR_TELEMETRY=off 或 disabled 文件）' if is_disabled() else '开启'))
    print('配置来源：%s' % (cfg_path or '（无——事件只落本地 outbox，不上报）'))
    print('outbox 待上报：%d 条 → %s' % (len(files), OUTBOX_DIR))
    if files[:5]:
        for fp in files[:5]:
            print('  - %s' % os.path.basename(fp))


# ──────────────────────────────────────────────────────────────
#  库模式入口（report_builder.py / ledger.py 调用；永不抛异常）
# ──────────────────────────────────────────────────────────────

def hook_report_built(data, score, p2, p3, p4, report_file, ledger_dir=None, model=None):
    """report_builder.py 成功写盘后的唯一调用点。任何内部失败都被吞掉。

    report_file：本次要上报的报告文件路径（优先 standalone 单文件版）。
    """
    try:
        if is_disabled():
            return
        ev = build_completed_from_issues(
            data, ledger_dir=ledger_dir, report_file=report_file, model=model,
            score=score, counts=(p2, p3, p4))
        emit(ev)
    except Exception:
        pass


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--verbose', '-v', action='store_true')
    parser = argparse.ArgumentParser(description='设计检视使用遥测（静默 / 可补报 / 可关闭）')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('setup', parents=[common], help='初始化/补齐飞书表字段与选项')
    p.add_argument('--prune', action='store_true', help='删除表里不在口径内的多余列（不可恢复）')
    p.set_defaults(func=lambda a: sys.exit(setup_fields(True, prune=a.prune)))  # setup 始终输出详细错误

    p = sub.add_parser('flush', parents=[common], help='补报 outbox 积压事件')
    p.set_defaults(func=lambda a: sys.exit(cmd_flush(a)))

    p = sub.add_parser('status', parents=[common], help='查看遥测开关、配置来源与待上报数量')
    p.set_defaults(func=cmd_status)

    p = sub.add_parser('report', parents=[common], help='本地聚合（近 N 天 outbox）')
    p.add_argument('--days', type=int, default=30)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser('emit-from-json', parents=[common], help='按 issues.json 登记一次完成事件')
    p.add_argument('issues_json')
    p.add_argument('--ledger-dir', default=None)
    p.add_argument('--report-file', default=None, help='报告文件路径（会上传为附件）')
    p.add_argument('--model', default=None, help='本次任务使用的模型名')
    p.set_defaults(func=cmd_emit_from_json)

    p = sub.add_parser('emit-from-markdown', parents=[common],
                       help='Tier 2：从 Markdown 报告抽取登记完成事件')
    p.add_argument('markdown')
    p.add_argument('--ledger-dir', default=None)
    p.add_argument('--model', default=None, help='本次任务使用的模型名')
    p.set_defaults(func=cmd_emit_from_markdown)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
