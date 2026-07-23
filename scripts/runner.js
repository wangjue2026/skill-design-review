/**
 * runner.js — 通用设计检视走查引擎
 *
 * 用法:
 *   node scripts/runner.js <config_path>
 *
 * 示例:
 *   node scripts/runner.js scripts/configs/ga_review.json
 *
 * Config 文件格式: 见 scripts/configs/_template.json
 */

const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');

// ──────────────────────────────────────────────────────────────
//  工具函数
// ──────────────────────────────────────────────────────────────

const SCRIPT_DIR = __dirname;
const ANNOTATE_SCRIPT = path.join(SCRIPT_DIR, 'auto_annotate.py');

/** 等待指定毫秒 */
const wait = ms => new Promise(res => setTimeout(res, ms));

/**
 * 解析 Selector 字符串，返回 { type, value }
 *   text/创建加速       → { type: 'text',  value: '创建加速' }
 *   xpath/...           → { type: 'xpath', value: '...' }
 *   css:div.foo > span  → { type: 'css',   value: 'div.foo > span' }
 *   div.foo > span      → { type: 'css',   value: 'div.foo > span' }  (默认 CSS)
 */
function parseSelector(rawSel) {
  if (!rawSel) return null;
  if (rawSel.startsWith('text/')) return { type: 'text',  value: rawSel.slice(5) };
  if (rawSel.startsWith('xpath/')) return { type: 'xpath', value: rawSel.slice(6) };
  if (rawSel.startsWith('css:')) return { type: 'css',   value: rawSel.slice(4) };
  return { type: 'css', value: rawSel };
}

/**
 * 获取元素的 BoundingRect（页面绝对坐标）
 * 在浏览器沙盒内执行，不接触磁盘文件
 * 返回 [x1, y1, x2, y2] 或 null
 */
async function getBoundingRect(page, rawSel) {
  const sel = parseSelector(rawSel);
  if (!sel) return null;

  return page.evaluate((selObj) => {
    let el;
    if (selObj.type === 'css') {
      el = document.querySelector(selObj.value);
    } else if (selObj.type === 'xpath') {
      const result = document.evaluate(selObj.value, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
      el = result.singleNodeValue;
    } else if (selObj.type === 'text') {
      const tags = ['button', 'a', 'span', 'div', 'td', 'th', 'label', 'li'];
      const candidates = tags.flatMap(tag => Array.from(document.querySelectorAll(tag)));
      el = candidates.find(e => {
        const style = window.getComputedStyle(e);
        return e.innerText && e.innerText.trim().includes(selObj.value)
          && style.display !== 'none'
          && style.visibility !== 'hidden'
          && (e.offsetWidth > 0 || e.offsetHeight > 0 || e.getClientRects().length > 0);
      });
    }
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return null;
    return [
      Math.round(r.left + window.scrollX),
      Math.round(r.top + window.scrollY),
      Math.round(r.right + window.scrollX),
      Math.round(r.bottom + window.scrollY)
    ];
  }, sel).catch(() => null);
}

/**
 * 截图并调用 auto_annotate.py 进行标注，覆盖原截图
 */
async function screenshotAndAnnotate(page, outputPath, annotations, assetsDir) {
  fs.mkdirSync(assetsDir, { recursive: true });
  await wait(600);
  await page.screenshot({ path: outputPath });
  console.log(`  [screenshot] Saved: ${path.basename(outputPath)}`);

  if (!annotations || annotations.length === 0) return;

  const processedAnnotations = [];
  for (const ann of annotations) {
    const rect = await getBoundingRect(page, ann.selector);
    if (rect) {
      // 回显命中元素的文字，供 AI 对照确认标注打在了正确目标上
      const hitText = await page.evaluate((selObj) => {
        let el;
        if (selObj.type === 'css') {
          el = document.querySelector(selObj.value);
        } else if (selObj.type === 'xpath') {
          const result = document.evaluate(selObj.value, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
          el = result.singleNodeValue;
        } else if (selObj.type === 'text') {
          const tags = ['button', 'a', 'span', 'div', 'td', 'th', 'label', 'li'];
          const candidates = tags.flatMap(tag => Array.from(document.querySelectorAll(tag)));
          el = candidates.find(e => {
            const s = window.getComputedStyle(e);
            return e.innerText && e.innerText.trim().includes(selObj.value)
              && s.display !== 'none' && s.visibility !== 'hidden'
              && (e.offsetWidth > 0 || e.offsetHeight > 0 || e.getClientRects().length > 0);
          }) || null;
        }
        return el ? (el.innerText || el.textContent || '').trim().slice(0, 60) : '';
      }, { type: ann.selector.startsWith('text/') ? 'text' : ann.selector.startsWith('xpath/') ? 'xpath' : 'css', value: ann.selector.startsWith('text/') ? ann.selector.slice(5) : ann.selector.startsWith('xpath/') ? ann.selector.slice(6) : ann.selector.startsWith('css:') ? ann.selector.slice(4) : ann.selector }).catch(() => '');
      console.log(`  [annotate: hit] selector="${ann.selector}" → 命中元素文字: "${hitText || '(无文字/图标类元素)'}"`);
      processedAnnotations.push({ rect, label: ann.label, color: ann.color || 'orange' });
    } else {
      console.warn(`  [annotate] Selector not found or zero-size: ${ann.selector}`);
    }
  }

  if (processedAnnotations.length === 0) return;

  const jsonStr = JSON.stringify(processedAnnotations);
  try {
    execSync(`python3 "${ANNOTATE_SCRIPT}" "${outputPath}" '${jsonStr}'`);
    const ext = path.extname(outputPath);
    const annotatedPath = outputPath.replace(ext, `_annotated${ext}`);
    if (fs.existsSync(annotatedPath)) {
      fs.renameSync(annotatedPath, outputPath);
      console.log(`  [annotate] ${processedAnnotations.length} annotation(s) applied to ${path.basename(outputPath)}`);
    }
  } catch (err) {
    console.error(`  [annotate] auto_annotate.py error: ${err.message}`);
  }
}

// ──────────────────────────────────────────────────────────────
//  Action 执行器
// ──────────────────────────────────────────────────────────────

async function executeAction(page, action, config, resolvedAssetsDir) {
  const { type } = action;

  switch (type) {

    case 'navigate': {
      const url = action.url.startsWith('http')
        ? action.url
        : `${config.base_url}${action.url}`;
      await page.goto(url, { waitUntil: 'networkidle0' });
      console.log(`  [navigate] → ${url}`);
      break;
    }

    case 'click': {
      const sel = parseSelector(action.selector);
      try {
        if (sel.type === 'text') {
          const handle = await page.evaluateHandle((txt) => {
            const tags = ['button', 'a', 'span', 'div', 'td', 'th', 'label', 'li'];
            return tags.flatMap(tag => Array.from(document.querySelectorAll(tag)))
              .find(e => {
                const s = window.getComputedStyle(e);
                return e.innerText && e.innerText.trim().includes(txt)
                  && s.display !== 'none' && s.visibility !== 'hidden'
                  && (e.offsetWidth > 0 || e.offsetHeight > 0 || e.getClientRects().length > 0);
              }) || null;
          }, sel.value);
          const el = handle.asElement();
          if (el) { await el.click(); console.log(`  [click] text/${sel.value}`); }
          else console.warn(`  [click] text not found: ${sel.value}`);
        } else {
          await page.click(sel.value);
          console.log(`  [click] ${sel.value}`);
        }
      } catch (e) {
        console.warn(`  [click] Failed on "${action.selector}": ${e.message}`);
      }
      break;
    }

    case 'type': {
      try {
        await page.click(action.selector, { clickCount: 3 });
        await page.type(action.selector, action.value || '');
        console.log(`  [type] "${action.value}" → ${action.selector}`);
      } catch (e) {
        console.warn(`  [type] Failed on "${action.selector}": ${e.message}`);
      }
      break;
    }

    case 'alpine_set': {
      // 直接改写 Alpine.js $data，在浏览器沙盒内执行，完全不接触磁盘文件
      const rootSel = action.selector || 'main';
      await page.evaluate((sel, dataObj) => {
        const rootEl = document.querySelector(sel);
        if (!rootEl || !window.Alpine) { console.warn('alpine_set: Alpine or root element not found'); return; }
        const data = window.Alpine.$data(rootEl);
        if (!data) { console.warn('alpine_set: No Alpine data on element'); return; }

        function setNestedValue(obj, keyPath, value) {
          if (!keyPath.includes('.') && !keyPath.includes('[')) {
            obj[keyPath] = value;
            return;
          }
          const parts = keyPath.replace(/\[(\d+)\]/g, '.$1').split('.').filter(Boolean);
          let cur = obj;
          for (let i = 0; i < parts.length - 1; i++) {
            if (cur[parts[i]] === undefined || cur[parts[i]] === null) return;
            cur = cur[parts[i]];
          }
          cur[parts[parts.length - 1]] = value;
        }

        for (const [key, value] of Object.entries(dataObj)) {
          setNestedValue(data, key, value);
        }
      }, rootSel, action.data || {});
      console.log(`  [alpine_set] ${JSON.stringify(action.data)}`);
      break;
    }

    case 'eval': {
      // 在浏览器沙盒中执行任意 JS 字符串
      // 适用于 Vue / 原生 JS 等所有页面类型
      // 安全说明: 操作的是浏览器运行态内存，完全不接触磁盘上的源文件
      const result = await page.evaluate(new Function(action.script));
      console.log(`  [eval] Executed. Result: ${JSON.stringify(result)}`);
      break;
    }

    case 'wait': {
      const ms = action.ms || 500;
      await wait(ms);
      console.log(`  [wait] ${ms}ms`);
      break;
    }

    case 'scroll_to': {
      try {
        await page.evaluate((sel) => {
          const el = document.querySelector(sel);
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, action.selector);
        console.log(`  [scroll_to] ${action.selector}`);
      } catch (e) {
        console.warn(`  [scroll_to] Failed: ${e.message}`);
      }
      break;
    }

    case 'screenshot': {
      const outputPath = path.join(resolvedAssetsDir, action.file);
      await screenshotAndAnnotate(page, outputPath, action.annotations || [], resolvedAssetsDir);
      break;
    }

    default:
      console.warn(`  [runner] Unknown action type: "${type}". Skipping.`);
  }
}

// ──────────────────────────────────────────────────────────────
//  主流程
// ──────────────────────────────────────────────────────────────

const configPath = path.resolve(process.argv[2] || '');

if (!process.argv[2]) {
  console.error('Usage: node scripts/runner.js <config_path>');
  console.error('Example: node scripts/runner.js scripts/configs/ga_review.json');
  process.exit(1);
}

if (!fs.existsSync(configPath)) {
  console.error(`Config file not found: ${configPath}`);
  process.exit(1);
}

const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));

// assets_dir 相对于 config 文件的目录来解析
const configDir = path.dirname(configPath);
const resolvedAssetsDir = config.assets_dir
  ? path.resolve(configDir, config.assets_dir)
  : path.resolve(SCRIPT_DIR, '..', 'Reports', 'assets');

(async () => {
  console.log('\n🚀 Design Review Runner');
  console.log(`   Project  : ${config.project || '未命名'}`);
  console.log(`   Base URL : ${config.base_url}`);
  console.log(`   Steps    : ${config.steps.length}`);
  console.log(`   Assets   : ${resolvedAssetsDir}`);
  console.log('─'.repeat(60));

  const browser = await puppeteer.launch({
    headless: false,
    slowMo: 150, // 增加每步延迟，便于用户肉眼跟随走查细节
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  const page = await browser.newPage();
  const vp = config.viewport || { width: 1440, height: 900 };
  await page.setViewport(vp);

  page.on('console', msg => {
    if (msg.type() === 'error') console.log(`  [browser:error] ${msg.text()}`);
  });

  let stepIndex = 0;
  for (const step of config.steps) {
    stepIndex++;
    const stepName = step.name || `Step ${stepIndex}`;
    console.log(`\n[${stepIndex}/${config.steps.length}] ${stepName}`);

    if (Array.isArray(step.actions)) {
      for (const action of step.actions) {
        await executeAction(page, action, config, resolvedAssetsDir);
      }
    }

    if (step.screenshot) {
      await executeAction(page, { type: 'screenshot', ...step.screenshot }, config, resolvedAssetsDir);
    }
  }

  await browser.close();

  console.log('\n' + '─'.repeat(60));
  console.log(`✅ All ${config.steps.length} steps completed.`);
  console.log(`   Screenshots → ${resolvedAssetsDir}`);
  console.log('');
})();
