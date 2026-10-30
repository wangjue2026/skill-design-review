#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate_reports.py — 把历史平铺报告迁入「一次任务一个目录」结构

背景：
  早期报告平铺在 Reports/ 根下，截图统一放在 Reports/assets/，一份报告要找图得跨两个地方。
  本脚本把每份报告（含其 _standalone 版本）收进 Reports/<报告名>/，并把该报告引用的截图
  复制到 <目录>/assets/ —— 报告里本来就是相对路径 assets/xxx.png，因此 HTML 无需改动。

用法：
  python3 scripts/migrate_reports.py --dry-run     # 只看计划，不动文件
  python3 scripts/migrate_reports.py               # 执行迁移
  python3 scripts/migrate_reports.py --keep-global-assets   # 保留原 Reports/assets（默认保留）

原则：
  - 只移动/复制，不删除任何图片；全局 Reports/assets 默认保留作为归档
  - 图片被多份报告共享时各自复制一份，保证每个任务目录自包含
  - 目标目录已存在则跳过（除非 --force）
"""

import argparse
import os
import re
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(SCRIPT_DIR, '..', 'Reports')
GLOBAL_ASSETS = os.path.join(REPORTS_DIR, 'assets')

IMG_RE = re.compile(r'assets/([^"\'\s>]+\.(?:png|jpg|jpeg|webp|gif))', re.I)
STANDALONE_SUFFIX = '_standalone'


def collect_plan():
    """返回 [(task_dir_name, [html files], [image names])]"""
    if not os.path.isdir(REPORTS_DIR):
        print('Error: Reports 目录不存在')
        sys.exit(1)

    htmls = [f for f in os.listdir(REPORTS_DIR)
             if f.lower().endswith('.html') and os.path.isfile(os.path.join(REPORTS_DIR, f))]
    if not htmls:
        return []

    groups = {}
    for f in htmls:
        stem = os.path.splitext(f)[0]
        base = stem[:-len(STANDALONE_SUFFIX)] if stem.endswith(STANDALONE_SUFFIX) else stem
        groups.setdefault(base, {'files': [], 'images': set()})
        groups[base]['files'].append(f)

    plan = []
    for base, info in groups.items():
        info['files'].sort(key=lambda x: (STANDALONE_SUFFIX in x, x))  # 主报告在前
        for f in info['files']:
            path = os.path.join(REPORTS_DIR, f)
            try:
                with open(path, encoding='utf-8', errors='ignore') as fh:
                    for m in IMG_RE.finditer(fh.read()):
                        info['images'].add(m.group(1))
            except OSError as e:
                print(f'[Warning] 读取失败 {f}: {e}')
        plan.append((base, info['files'], sorted(info['images'])))
    return plan


def main():
    ap = argparse.ArgumentParser(description='把历史平铺报告迁入任务目录结构')
    ap.add_argument('--dry-run', action='store_true', help='只打印迁移计划，不移动文件')
    ap.add_argument('--force', action='store_true', help='目标目录已存在时仍然合并')
    args = ap.parse_args()

    plan = collect_plan()
    if not plan:
        print('没有需要迁移的平铺报告（Reports/ 根下已无 .html）。')
        return

    total_html = total_img = missing = skipped = 0
    print('迁移计划：\n')

    for base, files, images in plan:
        task_dir = os.path.join(REPORTS_DIR, base)
        exists = os.path.isdir(task_dir)
        if exists and not args.force:
            print(f'⏭️  跳过（目录已存在）：{base}/')
            skipped += 1
            continue

        print(f'📁 {base}/')
        for f in files:
            print(f'     ← {f}')
        for img in images:
            src = os.path.join(GLOBAL_ASSETS, img)
            if os.path.exists(src):
                print(f'     ⧉ assets/{img}')
                total_img += 1
            else:
                print(f'     ⚠ assets/{img}  （全局目录中不存在，保留原引用）')
                missing += 1

        if args.dry_run:
            total_html += len(files)
            continue

        os.makedirs(os.path.join(task_dir, 'assets'), exist_ok=True)
        for f in files:
            shutil.move(os.path.join(REPORTS_DIR, f), os.path.join(task_dir, f))
            total_html += 1
        for img in images:
            src = os.path.join(GLOBAL_ASSETS, img)
            dst = os.path.join(task_dir, 'assets', img)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copyfile(src, dst)

    mode = '（dry-run，未改动任何文件）' if args.dry_run else ''
    print(f'\n汇总：报告 {total_html} 份 · 截图 {total_img} 张 · 断链 {missing} 张 · 跳过目录 {skipped} 个 {mode}')
    if missing:
        print('提示：断链图片在全局 assets 中找不到，通常这些报告本来就缺图，迁移后仍会显示占位图。')


if __name__ == '__main__':
    main()
