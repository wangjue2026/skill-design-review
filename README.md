# 安全产品设计检视 (Design Review) Skill 包

本仓库旨在通过 AI 代理（Agent）标准化、专业化地对安全产品界面进行深度设计检视，确保设计方案不仅符合规范，更能体现产品的顶层体验愿景。

## 价值主张
- **专业深度**: 结合 10 年以上资深体验专家视角，识别隐藏的交互漏洞与心智模型偏差。
- **高维对齐**: 确保每一个设计细节都符合产品的“品牌灵魂”与“体验愿景”。
- **闭环产出**: 生成带有截图批注、问题定级（P1-P4）以及 To-Be 改进方案的专业 HTML 报告。
- **降本增效**: 通过自动化脚本实现毫秒级精准坐标定位与批注，告别手动绘图。

## 包含内容
- `Skill.md`: **核心指令集**。定义了 AI Agent 的角色、执行逻辑、评分规则及输出格式要求。
- `Reference/`: **知识库**。
  - `02_设计检视Checklist.md`: 通用合规审计准则。
  - `03_SASE体验基准规范.md`: SASE 产品专属设计规范。
  - `04_SASE体验愿景.md`: 通用体验愿景基准。
  - `05_HTML检视报告模板.html`: 报告生成模板。
- `Reports/`: **产出目录**。生成的 HTML 报告及 `assets` 截图资源存放地。
- `scripts/`: **自动化工具箱**。
  - `runner.js`: 通用走查执行引擎，读取 JSON 配置执行页面操作（如导航、点击、截图等）并自动调用 `auto_annotate.py` 进行批注。
  - `report_builder.py`: 通用报告生成器，读取问题 JSON 并结合模板生成最终的 HTML 检视报告。
  - `auto_annotate.py`: 图片标注底层画笔，由 `runner.js` 自动调用，无需手动直接运行。

## 🎯 适用场景
1. **旅程走查**: 针对特定的用户剧本，验证关键路径是否存在断点。
2. **合规审计**: 快速检视方案是否符合 SeerDesign 5.0 基础规范。
3. **愿景提质**: 在方案基本合格的基础上，寻找能提升产品“高级感”与“品牌拉力”的优化点。

## 🛠️ 环境依赖与安装
本工具包内的自动化脚本需要 Node.js 和 Python 环境支持：

1. **安装 Node.js 依赖** (用于运行页面执行引擎):
   ```bash
   npm install puppeteer
   ```
2. **安装 Python 依赖** (用于底层图像标注):
   ```bash
   pip install pillow
   ```

## 🚀 标准工作流

按照以下两个步骤完成设计检视与报告生成：

### Step 1: 执行页面走查并生成批注截图
1. 参考 `scripts/configs/_template.json`，在 `scripts/configs/` 下为你的项目新建走查配置文件 `<项目名>_review.json`。
2. 运行页面走查引擎，自动执行操作并对截图进行标注：
   ```bash
   node scripts/runner.js scripts/configs/<项目名>_review.json
   ```
   *生成的批注截图将自动输出到 `Reports/assets/` 目录。*

### Step 2: 整理问题列表并生成 HTML 报告
1. 参考 `scripts/configs/_template_issues.json`，在 `scripts/configs/` 下新建问题列表文件 `<项目名>_issues.json`，填入检视发现的问题、严重程度（P1-P4）以及改进建议。
2. 运行报告生成器，生成最终的 HTML 报告：
   ```bash
   python3 scripts/report_builder.py scripts/configs/<项目名>_issues.json
   ```
   *生成的 HTML 报告将输出到 `Reports/<项目名>_设计检视报告_YYYYMMDD.html`。*

---
**Maintainer**: 体验设计团队 (UX Design Team)
