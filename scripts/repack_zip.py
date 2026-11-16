#!/usr/bin/env python3
"""重打包：把解压后的 Skill 目录重新打成可分发的 ZIP（干净、中文名不乱码）。

标准分发流程：
  1) 从 GitHub 下载 ZIP（Code → Download ZIP），解压得到 design-review-skill-main/
  2) 打开 scripts/configs/telemetry.json，把 "app_secret": "" 填成真实密钥
  3) 在该目录执行：python3 scripts/repack_zip.py
     → 产出 dist/design-review-skill-<日期>.zip，直接发给同事，解压即用

为什么不用系统自带压缩：
  - Finder「压缩」会掺入 __MACOSX/、.DS_Store，包就不干净了；
  - 且中文文件名不带 UTF-8 标记，Windows 解压后 references/01_体验目标制定.md 会变乱码。
  本脚本按 UTF-8 标记写入条目名，并排除这些垃圾文件。

用法：
  python3 scripts/repack_zip.py                 # 打包当前目录
  python3 scripts/repack_zip.py --no-secret     # 明知没填密钥也硬打包（会提示）
  python3 scripts/repack_zip.py --output /tmp/x.zip --prefix design-review-skill
"""
import argparse
import datetime
import json
import os
import stat
import sys
import zipfile

SKIP_DIRS = {'.git', 'dist', '__pycache__', '.idea', '.vscode', 'node_modules', '.mypy_cache'}
SKIP_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', '__MACOSX'}
SKIP_SUFFIX = ('.pyc', '.pyo')
TELEMETRY_JSON = os.path.join('scripts', 'configs', 'telemetry.json')


def has_secret(src):
    path = os.path.join(src, TELEMETRY_JSON)
    try:
        with open(path, encoding='utf-8') as f:
            return bool(((json.load(f).get('feishu') or {}).get('app_secret') or '').strip())
    except (OSError, json.JSONDecodeError):
        return False


def iter_files(src):
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith('.'))
        for name in sorted(filenames):
            # 跳过 .DS_Store / __MACOSX 垃圾 / AppleDouble(._xxx) / 字节码；
            # 但保留 .gitignore 这类被跟踪的点文件
            if name in SKIP_FILES or name.endswith(SKIP_SUFFIX) or name.startswith('._'):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            yield full, os.path.relpath(full, src).replace(os.sep, '/')


def main():
    ap = argparse.ArgumentParser(description='把解压后的 Skill 目录重新打成分发 ZIP')
    ap.add_argument('--src', default='.', help='要打包的目录，默认当前目录')
    ap.add_argument('--output', default='', help='输出路径，默认 dist/design-review-skill-<日期>.zip')
    ap.add_argument('--prefix', default='', help='包内顶层目录名，默认为源目录名')
    ap.add_argument('--no-secret', action='store_true', help='未填密钥时仍继续打包')
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        print('目录不存在：%s' % src)
        return 1
    prefix = args.prefix or os.path.basename(src.rstrip(os.sep)) or 'design-review-skill'
    out = args.output or os.path.join(src, 'dist', 'design-review-skill-%s.zip'
                                      % datetime.date.today().strftime('%Y%m%d'))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    secret_ok = has_secret(src)
    if not secret_ok and not args.no_secret:
        print('[中止] %s 里的 app_secret 还是空的，收包人将无法上报统计表。' % TELEMETRY_JSON)
        print('       先填入密钥，或加 --no-secret 强行打一个"不得上报"的包。')
        return 1

    files = list(iter_files(src))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for full, rel in files:
            info = zipfile.ZipInfo('%s/%s' % (prefix, rel), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | (os.stat(full).st_mode & 0o777)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, 'rb') as f:
                zf.writestr(info, f.read())

    print('已生成：%s' % out)
    print('  包内文件 : %d 个，%.1f KB' % (len(files), os.path.getsize(out) / 1024.0))
    print('  解压后   : %s/' % prefix)
    print('  遥测密钥 : %s' % ('已填入，收包人解压即可上报' if secret_ok
                               else '未填 —— 收包人只落本地 outbox，不进统计表'))
    print('  已排除   : .git / dist / __pycache__ / __MACOSX / .DS_Store 等')
    return 0


if __name__ == '__main__':
    sys.exit(main())
