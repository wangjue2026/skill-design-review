#!/usr/bin/env python3
"""
report_builder.py — 通用设计检视报告生成器

用法:
  python3 scripts/report_builder.py <issues_json_path>

示例:
  python3 scripts/report_builder.py scripts/configs/ga_issues.json

Issues JSON 格式: 见 scripts/configs/_template_issues.json
"""

import os
import re
import sys
import html
import json
import base64
from datetime import datetime

# ──────────────────────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, '..', 'references', '04_HTML检视报告模板.html')
REPORTS_DIR = os.path.join(SCRIPT_DIR, '..', 'Reports')
ASSETS_DIR = os.path.join(REPORTS_DIR, 'assets')

# 扣分规则
SEVERITY_DEDUCTIONS = {
    'p2': 5.0,
    'p3': 2.0,
    'p4': 0.5,
}
# 单项目累计扣分上限：P2 不设限（核心阻塞必须穿透分数），P3 封顶 30 分、P4 封顶 10 分。
# 目的：检视越彻底扣分越多，若不设累计上限，大项目必然归零，报告失去区分度且答辩常态化。
P3_CAP = 30.0
P4_CAP = 10.0

# 合法问题等级（写错即校验失败，不再静默按 0 分处理）
VALID_SEVERITIES = ('p2', 'p3', 'p4')
VALID_DIM_IDS = (1, 2, 3, 4)

# 四维结论标准字典与样式映射（与 SKILL.md §4 严格一致，禁止同义词）
VALID_DIM_ENUMS = {
    'dim1': {
        '超预期达成': 'result-pass', '达成': 'result-pass', '基础达成': 'result-warning',
        '未达成': 'result-fail', '未显式走查': ''
    },
    'dim2': {
        '符合': 'result-pass', '基本符合，有优化空间': 'result-pass', '部分符合': 'result-warning',
        '不符合': 'result-fail', '未显式走查': ''
    },
    'dim3': {
        '通过': 'result-pass', '基本通过，有优化空间': 'result-pass', '基础达成': 'result-warning',
        '未达成': 'result-fail', '未显式走查': ''
    },
    'dim4': {
        '完备': 'result-pass', '基本完备，存在边缘缺失': 'result-pass', '完备性不足': 'result-warning',
        '严重缺失': 'result-fail', '未显式走查': ''
    }
}

# 允许「0 问题」的满分结论词；其余结论必须 ≥1 个问题
FULL_SCORE_RESULTS = {
    'dim1': {'超预期达成', '达成'},
    'dim2': {'符合'},
    'dim3': {'通过'},
    'dim4': {'完备'},
}

# 4 大维度默认意图定义
DEFAULT_INTENTS = {
    1: '验证核心用户主任务剧本的通畅度与体验目标闭环',
    2: '评估方案与产品愿景、品牌内核及更高维解法的契合度',
    3: '排查通用 UX 交互原则、表单校验、防错及状态反馈合规性',
    4: '扫描全状态矩阵、极端临界数据与环境自适应能力'
}

# ──────────────────────────────────────────────────────────────
#  评分计算
# ──────────────────────────────────────────────────────────────

def _accumulate_score(issues, ignore_defense=False, prefer_original=False):
    """统一的扣分累加器（初评与终评共用，避免两套逻辑漂移）。

    - P2：每项 5 分，**不设上限**（核心阻塞必须穿透分数）；
    - P3：每项 2 分，累计封顶 P3_CAP=30 分；
    - P4：每项 0.5 分，累计封顶 P4_CAP=10 分。
    """
    score = 100.0
    deducted = {'p3': 0.0, 'p4': 0.0}
    caps = {'p3': P3_CAP, 'p4': P4_CAP}

    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if not ignore_defense:
            defense = issue.get('defense') or {}
            # 答辩采纳免责：扣分清零
            if str(defense.get('status', '')).lower() in ('waived', 'passed', 'accepted'):
                continue

        if prefer_original:
            sev = issue.get('original_severity') or issue.get('severity') or ''
        else:
            sev = issue.get('severity') or ''
        sev = str(sev).lower()

        deduction = SEVERITY_DEDUCTIONS.get(sev, 0.0)
        if sev in caps:
            actual = min(deduction, max(0.0, caps[sev] - deducted[sev]))
            deducted[sev] += actual
            score -= actual
        else:
            score -= deduction

    return max(0.0, score)


def calculate_initial_score(issues):
    """计算答辩前的原始初评得分（基于 original_severity 或 severity，忽略答辩豁免）。"""
    return _accumulate_score(issues, ignore_defense=True, prefer_original=True)


def calculate_score(issues):
    """计算最终得分（支持答辩采纳免责与降级生效）。"""
    return _accumulate_score(issues, ignore_defense=False, prefer_original=False)

# ──────────────────────────────────────────────────────────────
#  证据与维度归属解析
# ──────────────────────────────────────────────────────────────

def asset_path(img_file):
    """返回截图在 Reports/assets 下的绝对路径；文件名留空时返回空串。"""
    name = str(img_file or '').strip()
    return os.path.join(ASSETS_DIR, name) if name else ''


def asset_exists(img_file):
    p = asset_path(img_file)
    return bool(p) and os.path.exists(p)


def resolve_dimensions(issue):
    """返回该问题归属的维度编号集合。

    优先读结构化字段 issues[].dimensions；为兼容历史 JSON 回退解析 dimension_tag，
    且只取第一个命中的维度——避免一个问题被计入多个维度，导致「结论-问题数」失真。
    """
    dims = issue.get('dimensions')
    if isinstance(dims, list) and dims:
        out = set()
        for d in dims:
            try:
                d = int(d)
            except (TypeError, ValueError):
                continue
            if d in VALID_DIM_IDS:
                out.add(d)
        if out:
            return out

    tag = str(issue.get('dimension_tag') or '').lower()
    for d in VALID_DIM_IDS:
        if f'维度 {d}' in tag or f'维度{d}' in tag or f'dim{d}' in tag:
            return {d}
    return set()


def count_dim_issues(issues):
    """按维度统计问题数（结构化归属 + 排除答辩免责项）。"""
    counts = {i: [] for i in VALID_DIM_IDS}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        defense = issue.get('defense') or {}
        if str(defense.get('status', '')).lower() in ('waived', 'passed', 'accepted'):
            continue
        sev = str(issue.get('severity', '')).upper()
        for d in resolve_dimensions(issue):
            counts[d].append(sev)
    return counts


# ──────────────────────────────────────────────────────────────
#  准出前的 schema 校验（errors 非空则拒绝出报告）
# ──────────────────────────────────────────────────────────────

def validate_dataset(data):
    """报告生成前的数据校验，返回 (errors, warnings)。

    errors 非空必须阻止出报告：一份带假分数、断链截图或矛盾结论的 HTML 报告，
    危害远大于少出一份报告。warnings 只提示，不阻断。
    """
    errors, warnings = [], []

    for key in ('project', 'scenario', 'persona'):
        if not str(data.get(key, '')).strip():
            errors.append(f'顶层字段 `{key}` 为空（报告头必需）')

    issues = data.get('issues')
    if issues is None:
        errors.append('缺少 `issues` 数组')
        issues = []
    elif not isinstance(issues, list):
        errors.append('`issues` 必须是数组')
        issues = []
    if not issues:
        warnings.append('`issues` 为空：报告将没有任何问题卡片（零问题时四维结论应为满分白名单词）')

    title_counter = {}
    for idx, issue in enumerate(issues, 1):
        if not isinstance(issue, dict):
            errors.append(f'issues[{idx}] 不是对象')
            continue
        prefix = f'issues[{idx}]'

        sev = str(issue.get('severity', '')).strip().lower()
        if sev not in VALID_SEVERITIES:
            errors.append(f'{prefix}.severity = {issue.get("severity")!r} 非法，只能是 p2 / p3 / p4')

        for field in ('title', 'related_task', 'tried_path', 'description',
                      'business_impact', 'solution'):
            if not str(issue.get(field, '')).strip():
                errors.append(f'{prefix}.{field} 为空，正式问题卡片字段不可缺')

        dims = resolve_dimensions(issue)
        if not dims:
            errors.append(f'{prefix} 未声明归属维度：请填写 `dimensions: [1|2|3|4]`')

        raw_dims = issue.get('dimensions')
        if isinstance(raw_dims, list) and len(set(raw_dims)) > 1:
            warnings.append(f'{prefix}.dimensions 同时声明了 {raw_dims}，一个问题应只归属单一维度，请拆分')
        if not isinstance(raw_dims, list) and str(issue.get('dimension_tag', '')).strip():
            warnings.append(f'{prefix} 使用旧字段 dimension_tag，建议迁移为 dimensions 数组')

        conf = str(issue.get('confidence_text', '')).strip()
        if conf == '待验证假设':
            errors.append(f'{prefix} 标记为「待验证假设」的问题不得进入 issues，请移入 hypotheses（不参与扣分）')
        if conf == '中置信' and sev == 'p2':
            errors.append(f'{prefix} 中置信问题不得定为 p2（SKILL.md §3 置信度规则）')

        if issue.get('original_severity') and not issue.get('defense'):
            errors.append(
                f'{prefix} 有 original_severity 但无 defense 记录：会被误算成答辩前的更高级别；'
                f'请删除 original_severity 或补齐 defense'
            )

        img = str(issue.get('img_file') or '').strip()
        if img:
            if os.path.basename(img) != img:
                errors.append(f'{prefix}.img_file 只能是纯文件名（禁止路径分隔符）：{img!r}')
            elif not asset_exists(img):
                errors.append(
                    f'{prefix}.img_file=`{img}` 在 Reports/assets 下不存在'
                    f'（正式问题必须绑定有效截图；确实无界面证据请留空）'
                )

        title = str(issue.get('title', '')).strip()
        if title:
            title_counter[title] = title_counter.get(title, 0) + 1

    for title, count in title_counter.items():
        if count > 1:
            warnings.append(f'问题标题重复 {count} 次：{title}（多为复制后未改）')

    dims_data = data.get('dimensions') or {}
    counts = count_dim_issues(issues)
    all_unwalked = True
    for i in VALID_DIM_IDS:
        dim_key = f'dim{i}'
        val = str(dims_data.get(f'dim{i}_result', '')).strip()
        if not val:
            errors.append(f'dimensions.dim{i}_result 为空（未检视请填「未显式走查」）')
            all_unwalked = False
            continue
        if val not in VALID_DIM_ENUMS[dim_key]:
            errors.append(
                f'dimensions.dim{i}_result = {val!r} 不在白名单内，合法值：{list(VALID_DIM_ENUMS[dim_key])}'
            )
            all_unwalked = False
            continue
        if val != '未显式走查':
            all_unwalked = False
        if val not in FULL_SCORE_RESULTS[dim_key] and val != '未显式走查' and not counts[i]:
            errors.append(
                f'dimensions.dim{i}_result = {val!r} 暗示存在优化/缺失，但该维度问题数为 0：'
                f'请补问题或改为满分词'
            )
        if not str(dims_data.get(f'dim{i}_explanation', '')).strip():
            warnings.append(f'dimensions.dim{i}_explanation 为空')

    if issues and all_unwalked:
        errors.append('`issues` 非空但四维结论全部为「未显式走查」：请填写本次实际检视的维度结论')

    hist = data.get('score_history') or {}
    declared = hist.get('initial_score')
    if declared is not None:
        try:
            declared = float(declared)
            recalculated = calculate_initial_score(issues)
            if abs(declared - recalculated) > 0.01:
                warnings.append(
                    f'score_history.initial_score={declared:.1f} 与按 original_severity 重算的 '
                    f'{recalculated:.1f} 不一致'
                )
        except (TypeError, ValueError):
            errors.append(f'score_history.initial_score 不是数字：{hist.get("initial_score")!r}')

    hyps = data.get('hypotheses')
    if hyps is not None and not isinstance(hyps, list):
        errors.append('`hypotheses` 必须是数组')

    return errors, warnings


# ──────────────────────────────────────────────────────────────
#  HTML 生成
# ──────────────────────────────────────────────────────────────

SEVERITY_DISPLAY = {
    'p2': 'P2 - 较重',
    'p3': 'P3 - 轻微',
    'p4': 'P4 - 瑕疵',
}


def _esc(value):
    """文本字段统一 HTML 转义。

    报告内容是 AI 生成的自然语言，难免出现 `<`（如引用页面报错 `TypeError: <anonymous>`
    或写 `a < b`），不转义会直接破坏报告结构。仅 `conclusion` 字段按设计保留原始 HTML。
    """
    return html.escape('' if value is None else str(value), quote=True)


# 维度展示名：仅用于卡片标签，与 SKILL.md 四维命名保持一致
DIM_NAMES = {
    1: '维度 1：体验目标达成',
    2: '维度 2：产品理念契合度',
    3: '维度 3：常规交互Checklist',
    4: '维度 4：方案完备性检视',
}


def _dimension_label(issue):
    """没有旧字段 dimension_tag 时，用结构化 dimensions 生成展示标签。"""
    dims = sorted(resolve_dimensions(issue))
    if not dims:
        return ''
    return ' + '.join(DIM_NAMES.get(d, '维度 %d' % d) for d in dims)

def build_issue_card(issue, standalone=False):
    img_file = str(issue.get('img_file') or '').strip()
    sev = str(issue.get('severity') or 'p3').lower()
    img_path = asset_path(img_file) if img_file else ''
    img_exists = bool(img_path) and os.path.exists(img_path)

    img_url = ''
    if img_exists:
        if standalone:
            try:
                with open(img_path, 'rb') as f:
                    img_data = f.read()
                ext = os.path.splitext(img_file)[1].lower()
                mime = 'image/png' if ext == '.png' else ('image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/gif')
                img_url = f"data:{mime};base64,{base64.b64encode(img_data).decode('utf-8')}"
            except OSError as e:
                print(f"Error encoding image {img_file}: {e}")
                img_url = f"assets/{img_file}"
        else:
            img_url = f"assets/{img_file}"

    if img_url:
        media_html = f"""<div class="issue-img-wrapper" onclick="openLightbox(this.querySelector('img').src)">
                            <img src="{_esc(img_url)}" alt="问题截图">
                        </div>
                        <p class="image-caption">{_esc(issue.get('image_caption') or '点击可放大查看截图证据')}</p>"""
    else:
        # 区分「确实无界面证据」与「引用了不存在的文件」：后者必须在报告上留痕，不能伪装成静态审查
        if img_file:
            ph_title = '截图文件缺失'
            ph_tip = f'未在 Reports/assets 中找到 {img_file} · 结论仅供参考'
            ph_caption = issue.get('image_caption') or f'证据缺失：未找到 {img_file}'
        else:
            ph_title = '截图未获取或此问题关联界面不存在'
            ph_tip = '代码静态审查发现 · 状态插槽未定义'
            ph_caption = issue.get('image_caption') or '说明：经源码静态检视，该状态组件在原型中未被定义或未提供独立渲染视图'
        media_html = f"""<div class="issue-img-placeholder">
                            <svg viewBox="0 0 24 24">
                                <path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/>
                            </svg>
                            <div>
                                <strong>{_esc(ph_title)}</strong>
                                <div class="placeholder-tip">{_esc(ph_tip)}</div>
                            </div>
                        </div>
                        <p class="image-caption">{_esc(ph_caption)}</p>"""

    # 答辩复议板块渲染
    defense_html = ""
    tag_defense_html = ""
    defense = issue.get('defense')
    if defense:
        status_raw = defense.get('status', 'rejected').lower()
        if status_raw in ('waived', 'passed', 'accepted'):
            status_key = 'waived'
            status_text = '🟢 答辩采纳 · 免除扣分'
            tag_defense_html = '<span class="issue-tag tag-defense-waived">答辩免责</span>'
        elif status_raw in ('downgraded', 'demoted'):
            status_key = 'downgraded'
            orig_sev = (issue.get('original_severity') or 'P2').upper()
            curr_sev = sev.upper()
            status_text = f'🟡 答辩折衷 · 降级处理 ({orig_sev} ➔ {curr_sev})'
            tag_defense_html = '<span class="issue-tag tag-defense-downgraded">答辩降级</span>'
        else:
            status_key = 'rejected'
            status_text = '🔴 答辩驳回 · 维持原判扣分'
            tag_defense_html = '<span class="issue-tag tag-defense-rejected">答辩驳回</span>'

        re_verif_html = ""
        re_fact = defense.get('re_verification_fact') or defense.get('re_verification_action')
        if re_fact:
            re_verif_html = f"""
                        <div class="defense-block re-verification">
                            <h5>🔍 二次实证走查事实</h5>
                            <p>{_esc(re_fact)}</p>
                        </div>"""

        defense_html = f"""
                <div class="defense-panel defense-status-{status_key}">
                    <div class="defense-header">
                        <div class="defense-title-group">
                            <span class="defense-icon">⚖️</span>
                            <strong>设计答辩与复议记录</strong>
                        </div>
                        <span class="defense-status-badge">{status_text}</span>
                    </div>
                    <div class="defense-content-grid">
                        <div class="defense-block user-argument">
                            <h5>👤 答辩陈述与业务事实</h5>
                            <p>{_esc(defense.get('user_argument') or '—')}</p>
                        </div>
                        {re_verif_html}
                        <div class="defense-block reviewer-verdict">
                            <h5>🏛️ 资深设计师复议裁决</h5>
                            <p>{_esc(defense.get('reviewer_verdict') or '—')}</p>
                        </div>
                    </div>
                </div>"""

    dimension_label = issue.get('dimension_tag') or _dimension_label(issue)

    return f"""
            <div class="issue-card">
                <div class="tag-group">
                    <span class="issue-tag tag-scenario">{_esc(issue.get('scenario_tag'))}</span>
                    <span class="issue-tag tag-dimension">{_esc(dimension_label)}</span>
                    <span class="issue-tag tag-{sev}">{_esc(SEVERITY_DISPLAY.get(sev, sev.upper()))}</span>
                    <span class="issue-tag tag-confidence">{_esc(issue.get('confidence_text') or '高置信')}</span>
                    {tag_defense_html}
                </div>
                <h3 class="issue-title">{_esc(issue.get('title'))}</h3>
                <div class="issue-evidence-grid">
                    <div class="issue-evidence-media">
                        {media_html}
                    </div>
                    <div class="issue-detail-panel">
                        <div class="issue-field">
                            <h4>对应任务</h4>
                            <p>{_esc(issue.get('related_task') or '—')}</p>
                        </div>
                        <div class="issue-field">
                            <h4>已尝试路径</h4>
                            <p>{_esc(issue.get('tried_path') or '—')}</p>
                        </div>
                        <div class="issue-field">
                            <h4>替代路径检查</h4>
                            <p>{_esc(issue.get('alternative_path_check') or '—')}</p>
                        </div>
                        <div class="issue-field">
                            <h4>问题描述</h4>
                            <p>{_esc(issue.get('description'))}</p>
                        </div>
                        <div class="issue-field">
                            <h4>业务影响</h4>
                            <p>{_esc(issue.get('business_impact'))}</p>
                        </div>
                        <div class="solution-box">
                            <h4>建议改进方案</h4>
                            <p>{_esc(issue.get('solution'))}</p>
                        </div>
                    </div>
                </div>
                {defense_html}
            </div>
"""

def split_dim4_batch(issues):
    """按「维度 4 批量 P3/P4 表格化」规则分流，返回 (table_issues, card_issues)。

    唯一判定入口：渲染与自检共用，避免同一规则在多处实现后漂移。
    命中表格化时返回 (维度4的P3/P4问题, 其余问题)；未命中时返回 ([], 全部问题)。
    """
    batch, cards = [], []
    for issue in issues:
        is_dim4 = 4 in resolve_dimensions(issue)
        sev = str(issue.get('severity', '')).lower()
        if is_dim4 and sev in ('p3', 'p4'):
            batch.append(issue)
        else:
            cards.append(issue)

    if len(batch) > 3:
        return batch, cards
    return [], list(issues)

def build_dim4_table(issues):
    """生成维度 4 完备性汇总表格 HTML（字段：页面、缺失类型、缺失描述）。"""
    rows_html = []
    for idx, issue in enumerate(issues, 1):
        page = issue.get('scenario_tag') or issue.get('page') or f"模块 {idx}"
        missing_type = issue.get('missing_type') or issue.get('type')
        if not missing_type:
            title = issue.get('title', '')
            missing_type = title.split('缺失')[0] + '缺失' if '缺失' in title else title
            if len(missing_type) > 24:
                missing_type = "全状态/交互完备性"

        desc = issue.get('description', '')
        sol = issue.get('solution', '')
        desc_html = f"<p>{_esc(desc)}</p>"
        if sol:
            desc_html += f"<p style='margin-top: 6px; color: var(--accent-primary); font-size: 12px;'><strong>建议：</strong>{_esc(sol)}</p>"

        sev = str(issue.get('severity') or 'p3').lower()
        sev_badge = f"<span class='issue-tag tag-{sev}' style='margin-left: 6px;'>{_esc(sev.upper())}</span>"

        row = f"""                        <tr>
                            <td style="width: 25%;">
                                <span class="dim4-page-badge">{_esc(page)}</span>
                                {sev_badge}
                            </td>
                            <td style="width: 25%;">
                                <span class="dim4-type-badge">{_esc(missing_type)}</span>
                            </td>
                            <td style="width: 50%;">
                                {desc_html}
                            </td>
                        </tr>"""
        rows_html.append(row)

    table_rows = '\n'.join(rows_html)
    return f"""
        <section class="dim4-table-section">
            <h2>📋 维度 4：方案完备性检视缺失清单</h2>
            <p class="dim4-table-desc">注：经源码与原型静态审查，以下问题均属于维度 4（P3/P4 级）完备性细节缺失项，已按规范统一汇总展示：</p>
            <div class="dim4-table-wrapper">
                <table class="dim4-completeness-table">
                    <thead>
                        <tr>
                            <th>页面</th>
                            <th>缺失类型</th>
                            <th>缺失描述</th>
                        </tr>
                    </thead>
                    <tbody>
{table_rows}
                    </tbody>
                </table>
            </div>
        </section>
"""

def build_report(data, standalone=False):
    issues = data.get('issues', [])
    project = data.get('project', '未命名项目')
    scenario = data.get('scenario', '—')
    persona = data.get('persona', '—')
    date_str = str(data.get('date') or '').strip() or datetime.now().strftime('%Y-%m-%d')

    # 统计有效问题（答辩采纳免责的问题不计入扣分统计）
    def is_waived(i):
        return i.get('defense', {}).get('status', '').lower() in ('waived', 'passed', 'accepted')

    p2 = sum(1 for i in issues if i.get('severity', '').lower() == 'p2' and not is_waived(i))
    p3 = sum(1 for i in issues if i.get('severity', '').lower() == 'p3' and not is_waived(i))
    p4 = sum(1 for i in issues if i.get('severity', '').lower() == 'p4' and not is_waived(i))
    total = len(issues)
    score = calculate_score(issues)

    # 答辩与双轨得分逻辑
    score_hist = data.get('score_history', {})
    has_defense = score_hist.get('defense_triggered', False) or any(i.get('defense') for i in issues)
    initial_score = score_hist.get('initial_score')
    if initial_score is None:
        initial_score = calculate_initial_score(issues) if has_defense else score

    # 读取模板
    if not os.path.exists(TEMPLATE_PATH):
        print(f'Error: Template not found: {TEMPLATE_PATH}')
        sys.exit(1)

    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # 替换基础占位符
    content = content.replace('{{PROJECT_NAME}}', _esc(f'{project} 体验检视报告'))
    content = content.replace('{{SCENARIO}}', _esc(scenario))
    content = content.replace('{{PERSONA}}', _esc(persona))
    content = content.replace('{{DATE}}', _esc(date_str))
    content = content.replace('{{SCORE}}', f'{score:.1f}')
    content = content.replace('{{P2_COUNT}}', str(p2))
    content = content.replace('{{P3_COUNT}}', str(p3))
    content = content.replace('{{P4_COUNT}}', str(p4))

    # 替换答辩评分相关占位符
    if has_defense:
        content = content.replace('{{SCORE_BADGE_CLASS}}', ' has-defense')
        content = content.replace('{{SCORE_LABEL}}', '体验评分 · 复议终审')
        content = content.replace('{{SCORE_SUB_HTML}}', f'<span class="initial-score-tag" title="答辩前初评得分">初评 {initial_score:.1f}</span>')
        content = content.replace('{{SCORE_DEFENSE_TAG}}', '<div class="defense-stamp-badge">已完成答辩复议</div>')
    else:
        content = content.replace('{{SCORE_BADGE_CLASS}}', '')
        content = content.replace('{{SCORE_LABEL}}', '体验评分')
        content = content.replace('{{SCORE_SUB_HTML}}', '')
        content = content.replace('{{SCORE_DEFENSE_TAG}}', '')
    
    # 统计各维度的问题数（模块级 count_dim_issues：结构化解归属 + 排除答辩免责项）
    dim_issue_counts = count_dim_issues(issues)

    # 替换 4 大维度定性结果占位符并做合法性校验
    dimensions = data.get('dimensions', {})
    for i in range(1, 5):
        dim_key = f'dim{i}'
        res_key = f'dim{i}_result'
        exp_key = f'dim{i}_explanation'
        intent_key = f'dim{i}_intent'
        content_key = f'dim{i}_content'

        val = str(dimensions.get(res_key, '未显式走查') or '未显式走查').strip()
        intent_val = dimensions.get(intent_key) or DEFAULT_INTENTS.get(i, '')
        content_val = dimensions.get(content_key) or f'执行维度 {i} 专项走查与验证'

        # 白名单断言校验（权威校验在 validate_dataset，此处为渲染期兜底）
        if val not in VALID_DIM_ENUMS[dim_key]:
            print(f"[Warning] 维度 {i} 定性结论 '{val}' 不在标准白名单中！合法取值: {list(VALID_DIM_ENUMS[dim_key].keys())}")

        # 结论-问题数一致性校验：非满分结论必须 ≥1 个问题
        if val in VALID_DIM_ENUMS[dim_key] and val != '未显式走查' and val not in FULL_SCORE_RESULTS[dim_key]:
            if not dim_issue_counts[i]:
                print(f"[Warning] 维度 {i} 结论 '{val}' 暗示存在优化/缺失，但该维度问题数为 0，请补问题或改为满分结论词。")
        
        # 计算问题数字符串
        sev_list = dim_issue_counts[i]
        if not sev_list:
            issue_count_str = "0"
            issue_count_cls = "issue-count-zero"
        else:
            p2_c = sev_list.count('P2')
            p3_c = sev_list.count('P3')
            p4_c = sev_list.count('P4')
            parts = []
            if p2_c: parts.append(f"{p2_c}个 P2")
            if p3_c: parts.append(f"{p3_c}个 P3")
            if p4_c: parts.append(f"{p4_c}个 P4")
            issue_count_str = ", ".join(parts) if parts else f"{len(sev_list)}个"
            issue_count_cls = "issue-count-has"

        cls_name = VALID_DIM_ENUMS[dim_key].get(val, '')
        exp_val = dimensions.get(exp_key) or f'未提供维度 {i} 结果说明。'
        content = content.replace(f'{{{{DIMENSION_{i}_INTENT}}}}', _esc(intent_val))
        content = content.replace(f'{{{{DIMENSION_{i}_CONTENT}}}}', _esc(content_val))
        content = content.replace(f'{{{{DIMENSION_{i}_RESULT}}}}', _esc(val))
        content = content.replace(f'{{{{DIMENSION_{i}_RESULT_CLASS}}}}', cls_name)
        content = content.replace(f'{{{{DIMENSION_{i}_ISSUE_COUNT}}}}', issue_count_str)
        content = content.replace(f'{{{{DIMENSION_{i}_ISSUE_COUNT_CLASS}}}}', issue_count_cls)
        content = content.replace(f'{{{{DIMENSION_{i}_EXPLANATION}}}}', _esc(exp_val))

    # 注入定性结论
    conclusion_html = data.get('conclusion', '<p style="color: var(--text-dim);">未提供定性检视结论。</p>')
    content = content.replace('{{QUALITATIVE_CONCLUSION}}', conclusion_html)

    # 生成问题列表 HTML：维度 4 批量 P3/P4 命中表格化（判定统一在 split_dim4_batch）
    dim4_p3p4_issues, card_issues = split_dim4_batch(issues)
    if dim4_p3p4_issues:
        rendered_parts = []
        if card_issues:
            rendered_parts.append('\n'.join(build_issue_card(issue, standalone=standalone) for issue in card_issues))
        rendered_parts.append(build_dim4_table(dim4_p3p4_issues))
        issue_list_html = '\n'.join(rendered_parts)
    else:
        issue_list_html = '\n'.join(build_issue_card(issue, standalone=standalone) for issue in issues)

    # 注入问题列表
    start_ph = '<!-- ISSUE_LIST_START -->'
    end_ph = '<!-- ISSUE_LIST_END -->'
    start_idx = content.find(start_ph)
    end_idx = content.find(end_ph)

    if start_idx != -1 and end_idx != -1:
        content = content[:start_idx + len(start_ph)] + '\n' + issue_list_html + '\n' + content[end_idx:]
    else:
        content = content.replace('{{ISSUE_LIST}}', issue_list_html)

    # 处理待验证假设区域：无假设时移除整个 section，避免 {{HYPOTHESIS_*}} 占位符与默认截图容器残留
    if not data.get('hypotheses'):
        sec_start = content.find('<section class="hypothesis-section">')
        if sec_start != -1:
            sec_end = content.find('</section>', sec_start)
            if sec_end != -1:
                content = content[:sec_start] + content[sec_end + len('</section>'):]

    return content, score, total, p2, p3, p4

# ──────────────────────────────────────────────────────────────
#  准出自检（报告生成后强制校验）
# ──────────────────────────────────────────────────────────────

def self_check(html_content, issues):
    """准出自检。返回 (检查项名, 是否通过, 说明) 列表；任一项 False 即阻止交付。

    与旧实现的区别：截图不再只数 DOM 里的字符串出现次数（那样断链图也会通过），
    而是先校验证据文件真实存在于 Reports/assets，再核对渲染数量是否对齐。
    """
    checks = []

    def declared(i):
        return bool(str(i.get('img_file') or '').strip())

    def usable(i):
        return declared(i) and asset_exists(i.get('img_file'))

    dim4_batch, card_issues = split_dim4_batch(issues)
    rendered = card_issues if dim4_batch else list(issues)

    # ① 证据文件存在性（断链即失败）
    missing = [str(i.get('img_file')) for i in rendered if declared(i) and not usable(i)]
    checks.append((
        '截图证据文件存在',
        not missing,
        '全部命中 Reports/assets' if not missing
        else f'{len(missing)} 个引用文件缺失：{", ".join(missing[:5])}'
    ))

    # ② 渲染对齐（真实图片数 / 占位图数 与数据一致）
    expect_wrap = sum(1 for i in rendered if usable(i))
    expect_ph = sum(1 for i in rendered if not usable(i))
    wrap = html_content.count('class="issue-img-wrapper"')
    ph = html_content.count('class="issue-img-placeholder"')
    checks.append((
        '截图渲染对齐',
        wrap == expect_wrap and ph == expect_ph,
        f'实图={wrap}/{expect_wrap} 占位图={ph}/{expect_ph}'
    ))

    # ③ 维度 4 批量表格化
    if dim4_batch:
        has_table = 'class="dim4-completeness-table"' in html_content
        checks.append((
            '维度 4 完备性汇总表格',
            has_table,
            f'已汇总展示 {len(dim4_batch)} 个 P3/P4 问题' if has_table else '表格缺失'
        ))

    # ④ 底部评分说明表
    has_rules = '体验评分与问题等级定性规则' in html_content
    checks.append(('底部评分表', has_rules, '存在' if has_rules else '缺失'))

    # ⑤ 渲染完整性：不得残留未替换占位符
    residue = sorted(set(re.findall(r'\{\{[A-Z0-9_]+\}\}', html_content)))
    checks.append(('无未替换占位符', not residue, '干净' if not residue else '残留：' + ', '.join(residue[:5])))

    # ⑥ 答辩复议面板
    defense_issues = [i for i in issues if i.get('defense')]
    if defense_issues:
        panel_count = html_content.count('class="defense-panel')
        checks.append((
            '答辩复议记录面板',
            panel_count == len(defense_issues),
            f'已渲染 {panel_count}/{len(defense_issues)} 个复议记录'
        ))

    return checks

# ──────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # 命令行参数解析（仅支持极简 flag，便于 CI / 脚本调用）
    args = [a for a in sys.argv[1:]]
    issues_path = None
    slug = None
    standalone_only = False
    strict = True
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--slug' and i + 1 < len(args):
            slug = args[i + 1]; i += 2
        elif a == '--standalone-only':
            standalone_only = True; i += 1
        elif a == '--no-strict':
            strict = False; i += 1
        elif not a.startswith('--') and issues_path is None:
            issues_path = a; i += 1
        else:
            i += 1

    if not issues_path:
        print('Usage: python3 scripts/report_builder.py <issues_json_path> '
              '[--slug NAME] [--standalone-only] [--no-strict]')
        print('Example: python3 scripts/report_builder.py scripts/configs/ga_issues.json')
        print('Exit codes: 0 通过 / 1 参数或文件错误 / 2 数据校验失败 / 3 准出自检失败')
        sys.exit(1)

    issues_path = os.path.abspath(issues_path)
    if not os.path.exists(issues_path):
        print(f'Error: Issues file not found: {issues_path}')
        sys.exit(1)

    with open(issues_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 出报告前先过 schema 校验：带假分数/断链证据/矛盾结论的报告比不出报告更危险
    errors, warnings = validate_dataset(data)
    if warnings:
        print('\n⚠️  数据校验告警（不阻断）：')
        for w in warnings:
            print(f'   - {w}')
    if errors:
        print('\n❌ 数据校验未通过，已拒绝生成报告：')
        for e in errors:
            print(f'   - {e}')
        if strict:
            print('\n（如确认要在不合规数据上出报告，可追加 --no-strict；此时报告会带明显缺陷）')
            sys.exit(2)
        print('\n   --no-strict 已生效：继续生成（报告存在上述缺陷）。')

    project_slug = slug or str(data.get('project') or 'Review').replace(' ', '_').replace('/', '_')
    date_tag = str(data.get('date') or datetime.now().strftime('%Y%m%d')).replace('-', '')

    # 1. 生成普通版本（引用外部资产图片）
    if not standalone_only:
        html_content, score, total, p2, p3, p4 = build_report(data, standalone=False)
        output_filename = f'{project_slug}_设计检视报告_{date_tag}.html'
        output_path = os.path.join(REPORTS_DIR, output_filename)
        os.makedirs(REPORTS_DIR, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f'\n✅ Standard Report generated: {output_path}')

        print('   准出自检：')
        checks = self_check(html_content, data.get('issues', []))
        for name, ok, note in checks:
            mark = '✅' if ok else '❌'
            print(f'     {mark} {name}: {note}')

        failed = [c for c in checks if not c[1]]
        if failed and strict:
            print(f'\n❌ 准出自检未通过（{len(failed)} 项），按门禁策略退出非零：'
                  f'{[c[0] for c in failed]}')
            sys.exit(3)

    # 2. 生成 Standalone 版本（图片 Base64 内联）
    html_content_standalone, score, total, p2, p3, p4 = build_report(data, standalone=True)
    output_filename_standalone = f'{project_slug}_设计检视报告_{date_tag}_standalone.html'
    output_path_standalone = os.path.join(REPORTS_DIR, output_filename_standalone)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(output_path_standalone, 'w', encoding='utf-8') as f:
        f.write(html_content_standalone)
    print(f'✅ Standalone Report generated: {output_path_standalone}')

    print(f'   Total issues : {total} (P2:{p2} P3:{p3} P4:{p4})')
    print(f'   Score        : {score:.1f} / 100')
