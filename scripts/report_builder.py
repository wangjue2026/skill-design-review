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
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, '..', 'Reference', '05_HTML检视报告模板.html')
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
        else:
            img_url = f"assets/{img_file}"

    return f"""
            <div class="issue-card">
                <div class="issue-img-wrapper" onclick="openLightbox('{img_url}')">
                    <img src="{img_url}" alt="问题截图">
                </div>
                <div class="issue-content">
                    <div class="tag-group">
                        <span class="issue-tag tag-scenario">{issue.get('scenario_tag', '')}</span>
                        <span class="issue-tag tag-dimension">{issue.get('dimension_tag', '')}</span>
                        <span class="issue-tag tag-{sev}">{SEVERITY_DISPLAY.get(sev, sev.upper())}</span>
                    </div>
                    <h3 class="issue-title">{issue.get('title', '')}</h3>
                    <div class="issue-description">
                        {issue.get('description', '')}
                    </div>
                    <div class="solution-box">
                        <h4>建议改进方案</h4>
                        {issue.get('solution', '')}
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
