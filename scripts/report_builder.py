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
import sys
import json
import base64
from datetime import datetime

# ──────────────────────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, '..', 'references', '05_HTML检视报告模板.html')
REPORTS_DIR = os.path.join(SCRIPT_DIR, '..', 'Reports')

# 扣分规则
SEVERITY_DEDUCTIONS = {
    'p1': 10.0,
    'p2': 5.0,
    'p3': 2.0,
    'p4': 0.5,
}
# P4 单项目的总扣分上限
P4_CAP = 3.0

# ──────────────────────────────────────────────────────────────
#  评分计算
# ──────────────────────────────────────────────────────────────

def calculate_score(issues):
    score = 100.0
    p4_deducted = 0.0

    for issue in issues:
        sev = issue.get('severity', '').lower()
        deduction = SEVERITY_DEDUCTIONS.get(sev, 0.0)

        if sev == 'p4':
            actual = min(deduction, max(0.0, P4_CAP - p4_deducted))
            p4_deducted += actual
            score -= actual
        else:
            score -= deduction

    return max(0.0, score)

# ──────────────────────────────────────────────────────────────
#  HTML 生成
# ──────────────────────────────────────────────────────────────

SEVERITY_DISPLAY = {
    'p1': 'P1 - 严重',
    'p2': 'P2 - 较重',
    'p3': 'P3 - 轻微',
    'p4': 'P4 - 瑕疵',
}

def build_issue_card(issue, standalone=False):
    img_file = issue.get('img_file', '')
    sev = issue.get('severity', 'p3').lower()
    
    img_url = ''
    if img_file:
        if standalone:
            img_path = os.path.join(REPORTS_DIR, 'assets', img_file)
            if os.path.exists(img_path):
                try:
                    with open(img_path, 'rb') as f:
                        img_data = f.read()
                    ext = os.path.splitext(img_file)[1].lower()
                    mime = 'image/png' if ext == '.png' else ('image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/gif')
                    img_url = f"data:{mime};base64,{base64.b64encode(img_data).decode('utf-8')}"
                except Exception as e:
                    print(f"Error encoding image {img_file}: {e}")
                    img_url = f"assets/{img_file}"
            else:
                img_url = f"assets/{img_file}"
    if img_url:
        media_html = f"""<div class="issue-img-wrapper" onclick="openLightbox('{img_url}')">
                            <img src="{img_url}" alt="问题截图">
                        </div>
                        <p class="image-caption">{issue.get('image_caption', '点击可放大查看截图证据')}</p>"""
    else:
        media_html = f"""<div class="issue-img-placeholder">
                            <svg viewBox="0 0 24 24">
                                <path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/>
                            </svg>
                            <div>
                                <strong>截图未获取或此问题关联界面不存在</strong>
                                <div class="placeholder-tip">代码静态审查发现 · 状态插槽未定义</div>
                            </div>
                        </div>
                        <p class="image-caption">{issue.get('image_caption', '说明：经源码静态检视，该状态组件在原型中未被定义或未提供独立渲染视图')}</p>"""

    return f"""
            <div class="issue-card">
                <div class="tag-group">
                    <span class="issue-tag tag-scenario">{issue.get('scenario_tag', '')}</span>
                    <span class="issue-tag tag-dimension">{issue.get('dimension_tag', '')}</span>
                    <span class="issue-tag tag-{sev}">{SEVERITY_DISPLAY.get(sev, sev.upper())}</span>
                    <span class="issue-tag tag-confidence">{issue.get('confidence_text', '实证成立')}</span>
                </div>
                <h3 class="issue-title">{issue.get('title', '')}</h3>
                <div class="issue-evidence-grid">
                    <div class="issue-evidence-media">
                        {media_html}
                    </div>
                    <div class="issue-detail-panel">
                        <div class="issue-field">
                            <h4>对应任务</h4>
                            <p>{issue.get('related_task', '—')}</p>
                        </div>
                        <div class="issue-field">
                            <h4>已尝试路径</h4>
                            <p>{issue.get('tried_path', '—')}</p>
                        </div>
                        <div class="issue-field">
                            <h4>替代路径检查</h4>
                            <p>{issue.get('alternative_path_check', '—')}</p>
                        </div>
                        <div class="issue-field">
                            <h4>问题描述</h4>
                            <p>{issue.get('description', '')}</p>
                        </div>
                        <div class="issue-field">
                            <h4>业务影响</h4>
                            <p>{issue.get('business_impact', '')}</p>
                        </div>
                        <div class="solution-box">
                            <h4>建议改进方案</h4>
                            <p>{issue.get('solution', '')}</p>
                        </div>
                    </div>
                </div>
            </div>
"""

def build_report(data, standalone=False):
    issues = data.get('issues', [])
    project = data.get('project', '未命名项目')
    scenario = data.get('scenario', '—')
    persona = data.get('persona', '—')
    date_str = data.get('date', datetime.now().strftime('%Y-%m-%d'))

    # 统计
    p1 = sum(1 for i in issues if i.get('severity', '').lower() == 'p1')
    p2 = sum(1 for i in issues if i.get('severity', '').lower() == 'p2')
    p3 = sum(1 for i in issues if i.get('severity', '').lower() == 'p3')
    p4 = sum(1 for i in issues if i.get('severity', '').lower() == 'p4')
    total = len(issues)
    score = calculate_score(issues)

    # 读取模板
    if not os.path.exists(TEMPLATE_PATH):
        print(f'Error: Template not found: {TEMPLATE_PATH}')
        sys.exit(1)

    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # 替换基础占位符
    content = content.replace('{{PROJECT_NAME}}', f'{project} 体验检视报告')
    content = content.replace('{{SCENARIO}}', scenario)
    content = content.replace('{{PERSONA}}', persona)
    content = content.replace('{{DATE}}', date_str)
    content = content.replace('{{SCORE}}', f'{score:.1f}')
    content = content.replace('{{P1_COUNT}}', str(p1))
    content = content.replace('{{P2_COUNT}}', str(p2))
    content = content.replace('{{P3_COUNT}}', str(p3))
    content = content.replace('{{P4_COUNT}}', str(p4))
    
    # 4 大维度定性结果标准字典与样式映射
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
            '通过': 'result-pass', '基础达成': 'result-warning', '未达成': 'result-fail', '未显式走查': ''
        },
        'dim4': {
            '完备': 'result-pass', '基本完备，存在边缘缺失': 'result-pass', '完备性不足': 'result-warning',
            '严重缺失': 'result-fail', '未显式走查': ''
        }
    }

    # 4 大维度默认意图定义
    DEFAULT_INTENTS = {
        1: '验证核心用户主任务剧本的通畅度与体验目标闭环',
        2: '评估方案与产品愿景、品牌内核及更高维解法的契合度',
        3: '排查通用 UX 交互原则、表单校验、防错及状态反馈合规性',
        4: '扫描全状态矩阵、极端临界数据与环境自适应能力'
    }

    # 统计各维度的问题数
    dim_issue_counts = {1: [], 2: [], 3: [], 4: []}
    for issue in issues:
        dim_str = issue.get('dimension_tag', '')
        sev = issue.get('severity', 'p3').upper()
        for d in range(1, 5):
            if f'维度 {d}' in dim_str or f'维度{d}' in dim_str or f'dim{d}' in dim_str.lower():
                dim_issue_counts[d].append(sev)

    # 替换 4 大维度定性结果占位符并做合法性校验
    dimensions = data.get('dimensions', {})
    for i in range(1, 5):
        dim_key = f'dim{i}'
        res_key = f'dim{i}_result'
        exp_key = f'dim{i}_explanation'
        intent_key = f'dim{i}_intent'
        content_key = f'dim{i}_content'

        val = dimensions.get(res_key, '未显式走查').strip()
        intent_val = dimensions.get(intent_key, DEFAULT_INTENTS.get(i, ''))
        content_val = dimensions.get(content_key, f'执行维度 {i} 专项走查与验证')
        
        # 白名单断言校验
        if val not in VALID_DIM_ENUMS[dim_key]:
            print(f"[Warning] 维度 {i} 定性结论 '{val}' 不在标准白名单中！合法取值: {list(VALID_DIM_ENUMS[dim_key].keys())}")
        
        # 计算问题数字符串
        sev_list = dim_issue_counts[i]
        if not sev_list:
            issue_count_str = "0"
            issue_count_cls = "issue-count-zero"
        else:
            p1_c = sev_list.count('P1')
            p2_c = sev_list.count('P2')
            p3_c = sev_list.count('P3')
            p4_c = sev_list.count('P4')
            parts = []
            if p1_c: parts.append(f"{p1_c}个 P1")
            if p2_c: parts.append(f"{p2_c}个 P2")
            if p3_c: parts.append(f"{p3_c}个 P3")
            if p4_c: parts.append(f"{p4_c}个 P4")
            issue_count_str = ", ".join(parts) if parts else f"{len(sev_list)}个"
            issue_count_cls = "issue-count-has"

        cls_name = VALID_DIM_ENUMS[dim_key].get(val, '')
        content = content.replace(f'{{{{DIMENSION_{i}_INTENT}}}}', intent_val)
        content = content.replace(f'{{{{DIMENSION_{i}_CONTENT}}}}', content_val)
        content = content.replace(f'{{{{DIMENSION_{i}_RESULT}}}}', val)
        content = content.replace(f'{{{{DIMENSION_{i}_RESULT_CLASS}}}}', cls_name)
        content = content.replace(f'{{{{DIMENSION_{i}_ISSUE_COUNT}}}}', issue_count_str)
        content = content.replace(f'{{{{DIMENSION_{i}_ISSUE_COUNT_CLASS}}}}', issue_count_cls)
        content = content.replace(f'{{{{DIMENSION_{i}_EXPLANATION}}}}', dimensions.get(exp_key, f'未提供维度 {i} 结果说明。'))

    # 注入定性结论
    conclusion_html = data.get('conclusion', '<p style="color: var(--text-dim);">未提供定性检视结论。</p>')
    content = content.replace('{{QUALITATIVE_CONCLUSION}}', conclusion_html)

    # 生成问题列表 HTML
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

    return content, score, total, p1, p2, p3, p4

# ──────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 scripts/report_builder.py <issues_json_path>')
        print('Example: python3 scripts/report_builder.py scripts/configs/ga_issues.json')
        sys.exit(1)

    issues_path = os.path.abspath(sys.argv[1])
    if not os.path.exists(issues_path):
        print(f'Error: Issues file not found: {issues_path}')
        sys.exit(1)

    with open(issues_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 1. 生成普通版本（引用外部资产图片）
    html_content, score, total, p1, p2, p3, p4 = build_report(data, standalone=False)
    
    project_slug = data.get('project', 'Review').replace(' ', '_').replace('/', '_')
    date_tag = data.get('date', datetime.now().strftime('%Y%m%d')).replace('-', '')
    output_filename = f'{project_slug}_设计检视报告_{date_tag}.html'
    output_path = os.path.join(REPORTS_DIR, output_filename)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f'\n✅ Standard Report generated: {output_path}')

    # 2. 生成 Standalone 版本（图片 Base64 内联）
    html_content_standalone, _, _, _, _, _, _ = build_report(data, standalone=True)
    output_filename_standalone = f'{project_slug}_设计检视报告_{date_tag}_standalone.html'
    output_path_standalone = os.path.join(REPORTS_DIR, output_filename_standalone)
    
    with open(output_path_standalone, 'w', encoding='utf-8') as f:
        f.write(html_content_standalone)
    print(f'✅ Standalone Report generated: {output_path_standalone}')
    
    print(f'   Total issues : {total} (P1:{p1} P2:{p2} P3:{p3} P4:{p4})')
    print(f'   Score        : {score:.1f} / 100')
