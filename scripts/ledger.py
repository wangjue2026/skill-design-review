#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger.py — 设计检视过程账本（防丢档 + 出去报告门禁）

解决的核心问题：
  过去证据账本、已发现问题、未覆盖清单只存在于对话上下文里，
  一旦上下文压缩或会话中断，"截图还在磁盘、但截图证明了什么已经丢了"。
  本工具把「证据 → 结论」的绑定关系落盘为任务目录下的 ledger.json，并支持断点续跑。

任务目录结构（由 init 创建）：
  Reports/<任务名>_<日期>/
    ├─ ledger.json      过程账本（本工具维护，原子写 + 自动 .bak）
    ├─ issues_draft.json 候选问题（AI 维护，格式兼容 report_builder.py）
    ├─ ops/             ops 序列（可原样重跑）
    └─ assets/          截图与批注图

用法：
  python3 scripts/ledger.py init    --dir Reports/dem_20261030 --project "DEM 体验监测" \
                                    --pages "首页,用户详情,应用详情"
  python3 scripts/ledger.py add     --dir ... --stage p3 --name "从首页定位到何总" --label "I"
  python3 scripts/ledger.py apply   --dir ... --file updates.json     # 批量更新（每趟结束调一次）
  python3 scripts/ledger.py status  --dir ...                        # 查看进度
  python3 scripts/ledger.py check   --dir ...                        # 门禁：有 pending 即非零退出
  python3 scripts/ledger.py summary --dir ...                        # 输出 Markdown 覆盖声明

status 取值：pending / done / not_reachable / skipped
原因码（not_reachable 必填）：NO_DATA / NO_PERM / OVER_BUDGET / ENV_LIMIT
"""

import argparse
import json
import os
import shutil
import sys
import time

STAGES = [
    ('p0', '建账与假设'),
    ('p1', '静态扫描'),
    ('p2', '全景快照'),
    ('p3', '任务剧本走查'),
    ('p4', '维度2初判'),
    ('p5', '全状态取证'),
    ('p6', '定向交互取证'),
    ('p7', '维度2终判'),
    ('p8', '收口对账'),
    ('p9', '定级与报告'),
]
STAGE_KEYS = [s[0] for s in STAGES]
STAGE_NAMES = dict(STAGES)

STATUSES = ('pending', 'done', 'not_reachable', 'skipped')
REASONS = ('NO_DATA', 'NO_PERM', 'OVER_BUDGET', 'ENV_LIMIT')

# 允许出现的证据标签（与 references/02、05 的标注体系一致）
LABELS = ('S', 'R', 'I', 'S+R', 'S+I', 'R+I', '')


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _die(msg):
    _emit({'status': 'error', 'error': msg})
    sys.exit(2)


def ledger_path(d):
    return os.path.join(d, 'ledger.json')


def load(d):
    p = ledger_path(d)
    if not os.path.exists(p):
        _die('账本不存在，请先执行 init：%s' % p)
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _die('账本损坏（%s）。同目录下若有 ledger.json.bak 可改名恢复。' % e)


def save(d, data):
    """原子写：先写临时文件再 os.replace；旧版本自动备份为 .bak。

    「避免丢档」是本工具的第一目标，所以任何一次写入失败都不会破坏已有账本。
    """
    p = ledger_path(d)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if os.path.exists(p):
        try:
            shutil.copyfile(p, p + '.bak')
        except OSError:
            pass
    os.replace(tmp, p)


def _next_id(data, stage):
    n = 1
    used = {i.get('id') for i in data.get('items', [])}
    while '%s-%03d' % (stage, n) in used:
        n += 1
    return '%s-%03d' % (stage, n)


def cmd_init(args):
    d = args.dir
    os.makedirs(os.path.join(d, 'assets'), exist_ok=True)
    os.makedirs(os.path.join(d, 'ops'), exist_ok=True)
    if os.path.exists(ledger_path(d)) and not args.force:
        _die('账本已存在，如需重建请加 --force（会覆盖现有进度）')

    data = {
        'project': args.project or '',
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'pages': [p.strip() for p in (args.pages or '').split(',') if p.strip()],
        'stages': {k: {'name': n, 'status': 'pending', 'note': ''} for k, n in STAGES},
        'items': [],
    }
    save(d, data)
    _emit({'status': 'ok', 'dir': d, 'ledger': ledger_path(d),
           'stages': STAGE_KEYS, 'pages': data['pages']})


def cmd_add(args):
    data = load(args.dir)
    if args.stage not in STAGE_KEYS:
        _die('非法 stage：%s（可选：%s）' % (args.stage, ', '.join(STAGE_KEYS)))
    label = (args.label or '').upper()
    if label not in LABELS:
        _die('非法 label：%s（可选：%s）' % (args.label, ', '.join([x for x in LABELS if x])))

    item = {
        'id': args.id or _next_id(data, args.stage),
        'stage': args.stage,
        'kind': args.kind or 'check',          # hypothesis / check / evidence
        'name': args.name,
        'page': args.page or '',
        'label': label,
        'status': 'pending',
        'evidence': '',
        'reason': '',
        'note': args.note or '',
    }
    data['items'].append(item)
    data['stages'][args.stage]['status'] = 'in_progress'
    save(args.dir, data)
    _emit({'status': 'ok', 'added': item, 'total': len(data['items'])})


def cmd_apply(args):
    """批量更新（每趟结束调一次）：--file 指向 JSON 数组。

    [{"id":"p3-001","status":"done","evidence":"assets/x.png"},
     {"id":"p5-003","status":"not_reachable","reason":"NO_DATA"}]
    """
    data = load(args.dir)
    try:
        with open(args.file, encoding='utf-8') as f:
            updates = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _die('读取更新文件失败：%s' % e)
    if not isinstance(updates, list):
        _die('更新文件必须是数组')

    index = {i['id']: i for i in data['items'] if 'id' in i}
    applied, errors = [], []
    for u in updates:
        iid = u.get('id')
        if iid not in index:
            errors.append('未知 id：%s' % iid)
            continue
        st = u.get('status')
        if st and st not in STATUSES:
            errors.append('%s 非法 status：%s' % (iid, st))
            continue
        target = index[iid]
        if st:
            target['status'] = st
        for k in ('evidence', 'note', 'page'):
            if k in u:
                target[k] = u[k]
        if 'reason' in u or target['status'] == 'not_reachable':
            reason = u.get('reason', target.get('reason') or '')
            if target['status'] == 'not_reachable':
                if reason not in REASONS:
                    errors.append('%s 标记 not_reachable 必须给合法原因（%s）'
                                  % (iid, ' / '.join(REASONS)))
                    target['status'] = 'pending'
                else:
                    target['reason'] = reason
            else:
                target['reason'] = reason if reason in REASONS else ''
        applied.append(iid)

    # 每趟状态：只要有条目就标记 in_progress；全部完成则 done
    for k in STAGE_KEYS:
        items = [i for i in data['items'] if i.get('stage') == k]
        if not items:
            continue
        if all(i['status'] in ('done', 'not_reachable', 'skipped') for i in items):
            data['stages'][k]['status'] = 'done'
        else:
            data['stages'][k]['status'] = 'in_progress'

    data['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    save(args.dir, data)

    result = {'status': 'ok' if not errors else 'partial', 'applied': applied}
    if errors:
        result['errors'] = errors
    _emit(result)
    if errors:
        sys.exit(3)


def _stats(data):
    items = data.get('items', [])
    by_status = {}
    for i in items:
        by_status[i.get('status', 'pending')] = by_status.get(i.get('status', 'pending'), 0) + 1
    reasons = {}
    for i in items:
        if i.get('status') == 'not_reachable':
            r = i.get('reason') or 'UNSPECIFIED'
            reasons[r] = reasons.get(r, 0) + 1
    return items, by_status, reasons


def cmd_status(args):
    data = load(args.dir)
    items, by_status, reasons = _stats(data)
    stages = []
    for k in STAGE_KEYS:
        si = [i for i in items if i.get('stage') == k]
        pending = [i['id'] for i in si if i.get('status') == 'pending']
        stages.append({
            'stage': k, 'name': STAGE_NAMES[k],
            'status': data['stages'].get(k, {}).get('status', 'pending'),
            'total': len(si),
            'pending': len(pending),
            'pending_ids': pending[:10],
        })
    _emit({'project': data.get('project'), 'total_items': len(items),
           'by_status': by_status, 'not_reachable_reasons': reasons, 'stages': stages})


def cmd_check(args):
    """门禁：账本为空、或存在 pending 条目、或缺原因码，即非零退出，阻止出报告。"""
    data = load(args.dir)
    items, by_status, reasons = _stats(data)
    if not items:
        _emit({'status': 'incomplete', 'total_items': 0,
               'error': '账本为空：请先用 add 登记检视项（P0 静态清点后至少应有「页面 × 状态」条目）',
               'hint': '空账本等同于未做检视，不允许出报告'})
        sys.exit(1)
    pending = [i for i in items if i.get('status') == 'pending']
    bad_reason = [i for i in items
                  if i.get('status') == 'not_reachable' and not i.get('reason')]
    no_evidence = [i for i in items
                   if i.get('status') == 'done' and i.get('kind') != 'hypothesis'
                   and not i.get('evidence') and not i.get('page')]

    ok = not pending and not bad_reason
    _emit({
        'status': 'ok' if ok else 'incomplete',
        'total_items': len(items),
        'by_status': by_status,
        'not_reachable_reasons': reasons,
        'pending_count': len(pending),
        'pending_ids': [i['id'] for i in pending][:20],
        'missing_reason_ids': [i['id'] for i in bad_reason][:20],
        'done_without_evidence_ids': [i['id'] for i in no_evidence][:20],
        'hint': 'pending 必须清零（做完或标记 not_reachable 并给原因码）后才能出报告',
    })
    if not ok:
        sys.exit(1)


def cmd_summary(args):
    """输出 Markdown 覆盖声明，供报告「覆盖情况声明」区块使用。"""
    data = load(args.dir)
    items, by_status, reasons = _stats(data)
    done = by_status.get('done', 0)
    nr = by_status.get('not_reachable', 0)
    pending = by_status.get('pending', 0)
    total = len(items)
    covered = done + by_status.get('skipped', 0)

    lines = []
    lines.append('本次共登记检视项 **%d** 条，已完成 **%d** 条，'
                 '未覆盖 **%d** 条，未完成 **%d** 条。' % (total, covered, nr, pending))
    lines.append('')
    lines.append('| 趟次 | 名称 | 条目 | 完成 | 未覆盖 |')
    lines.append('| --- | --- | --- | --- | --- |')
    for k in STAGE_KEYS:
        si = [i for i in items if i.get('stage') == k]
        if not si:
            continue
        sd = sum(1 for i in si if i.get('status') == 'done')
        sn = sum(1 for i in si if i.get('status') == 'not_reachable')
        lines.append('| %s | %s | %d | %d | %d |' % (k.upper(), STAGE_NAMES[k], len(si), sd, sn))

    if reasons:
        lines.append('')
        lines.append('**未覆盖原因分布**：' + '、'.join(
            '%s × %d' % (k, v) for k, v in sorted(reasons.items())))
    nr_items = [i for i in items if i.get('status') == 'not_reachable']
    if nr_items:
        lines.append('')
        lines.append('**未覆盖明细**：')
        for i in nr_items[:30]:
            lines.append('- `%s` %s（%s）' % (i.get('reason'), i.get('name'), i.get('page') or '—'))

    print('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description='设计检视过程账本（防丢档 + 门禁）')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('init', help='创建任务目录与空账本')
    p.add_argument('--dir', required=True)
    p.add_argument('--project', default='')
    p.add_argument('--pages', default='')
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_init)

    p = sub.add_parser('add', help='登记一条检视项')
    p.add_argument('--dir', required=True)
    p.add_argument('--stage', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--label', default='', help='S / R / I / S+R / S+I / R+I')
    p.add_argument('--kind', default='check', help='hypothesis / check / evidence')
    p.add_argument('--page', default='')
    p.add_argument('--id', default=None)
    p.add_argument('--note', default='')
    p.set_defaults(func=cmd_add)

    p = sub.add_parser('apply', help='批量更新条目状态（每趟结束调用）')
    p.add_argument('--dir', required=True)
    p.add_argument('--file', required=True)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('status', help='查看进度')
    p.add_argument('--dir', required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser('check', help='门禁：有 pending 即非零退出')
    p.add_argument('--dir', required=True)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('summary', help='输出 Markdown 覆盖声明')
    p.add_argument('--dir', required=True)
    p.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
