const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');

const BASE = 'http://127.0.0.1:8734/05-方案五/';
const ASSETS = '/Users/wj/Desktop/项目文件/SMG-DEM/SKillS/skill-design-review/Reports/assets';
const ANNOTATE = '/Users/wj/Desktop/项目文件/SMG-DEM/SKillS/skill-design-review/scripts/auto_annotate.py';

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  const consoleErrors = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
  page.on('pageerror', e => consoleErrors.push('PAGEERROR: ' + e.message.slice(0, 300)));

  // 文本命中最小可见元素
  async function findByText(txt) {
    return page.evaluate((t) => {
      const tags = ['div','span','button','a','td','th','label','li','h1','h2','h3','h4','p'];
      let best = null, bestArea = Infinity;
      for (const tag of tags) {
        for (const e of document.querySelectorAll(tag)) {
          const s = window.getComputedStyle(e);
          const ok = e.childElementCount === 0 && e.textContent && e.textContent.trim().includes(t)
            && s.display !== 'none' && s.visibility !== 'hidden'
            && (e.offsetWidth > 0 || e.offsetHeight > 0);
          if (!ok) continue;
          const r = e.getBoundingClientRect();
          const area = r.width * r.height;
          if (area > 0 && area < bestArea) { bestArea = area; best = e; }
        }
      }
      if (!best) return null;
      const r = best.getBoundingClientRect();
      return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
    }, txt);
  }
  async function findByCss(sel) {
    return page.evaluate((s) => {
      const e = document.querySelector(s);
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
    }, sel);
  }

  async function shot(name, annotations = []) {
    await sleep(700);
    const file = path.join(ASSETS, name);
    await page.screenshot({ path: file });
    const anns = [];
    for (const a of annotations) {
      let rect = null;
      if (a.selector.startsWith('css:')) rect = await findByCss(a.selector.slice(4));
      else if (a.selector.startsWith('text:')) rect = await findByText(a.selector.slice(5));
      else rect = await findByCss(a.selector);
      if (rect) anns.push({ rect, label: a.label, color: a.color || 'orange' });
      else console.log('  [annotate] MISS selector:', a.selector);
    }
    if (anns.length) {
      try {
        execSync(`python3 "${ANNOTATE}" "${file}" '${JSON.stringify(anns)}'`);
        const ext = path.extname(file);
        const annotated = file.replace(ext, `_annotated${ext}`);
        if (fs.existsSync(annotated)) fs.renameSync(annotated, file);
      } catch (e) { console.log('  annotate err', e.message); }
    }
    console.log('  [shot]', name, fs.existsSync(file) ? fs.statSync(file).size + ' bytes' : 'MISSING');
  }

  const log = (...a) => console.log(...a);

  // ============ 首页 ============
  await page.goto(encodeURI(BASE + 'DEM-首页.html'), { waitUntil: 'networkidle0', timeout: 30000 });
  await sleep(2500);
  log('=== 首页 loaded, URL=', page.url());

  // 数据一致性校验
  const homeData = await page.evaluate(() => {
    const t = document.body.innerText;
    const q = (re) => { const m = t.match(re); return m ? m[1] : null; };
    return {
      hasFilterAlertLevel: typeof window.filterAlertLevel !== 'undefined',
      overviewText: t.slice(0, 800)
    };
  });
  log('首页 hasFilterAlertLevel(global):', homeData.hasFilterAlertLevel);

  // 点击环形图 高优 弧线，观察是否抛错
  const beforeErrors = consoleErrors.length;
  try {
    const arc = await page.evaluateHandle(() => {
      const circles = Array.from(document.querySelectorAll('circle[cursor]'));
      return circles.length;
    });
    log('ring circles count:', await arc.jsonValue());
    await page.evaluate(() => {
      const c = document.querySelector('circle[title="高优告警 (4)"]');
      if (c) c.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await sleep(800);
    log('点击高优弧线后 新增console错误:', consoleErrors.slice(beforeErrors));
  } catch (e) { log('click ring err', e.message); }

  await shot('dem_home_overview.png', [
    { selector: 'css:div.grid', label: '[P2] 进度条比例与数字/总数不符', color: 'orange' },
    { selector: 'text:体验预警事件分布', label: '[P3] 分项之和≠总数', color: 'red' }
  ]);

  // 用户 tab 默认
  await shot('dem_home_user_table.png', [
    { selector: 'text:访问体验质量', label: '[P2] 用户\"差\"9人却画8%红色条', color: 'orange' }
  ]);

  // 切到应用 tab
  await page.evaluate(() => {
    const tabs = Array.from(document.querySelectorAll('div'));
    const t = tabs.find(d => d.textContent.trim() === '应用' && d.className.includes('group'));
    // fallback: click via Alpine
    if (window.Alpine) {
      const root = document.querySelector('[x-data="demHomeTabState()"]') || document.querySelector('[x-data]');
      if (root && window.Alpine.$data(root)) {
        try { window.Alpine.$data(root).contentTab = '应用'; } catch(e){}
      }
    }
  });
  await sleep(800);
  await shot('dem_home_app_table.png');

  // 搜索空态（用户 tab 输入不存在关键词）
  await page.evaluate(() => {
    const root = document.querySelector('[x-data="demHomeTabState()"]') || document.querySelector('[x-data]');
    if (root && window.Alpine && window.Alpine.$data) {
      const d = window.Alpine.$data(root);
      d.contentTab = '用户'; d.userSearchQuery = 'zzzz不存在用户';
    }
  });
  await sleep(800);
  await shot('dem_home_empty_state.png', [
    { selector: 'text:没有匹配的用户体验数据', label: '[P3] 空态仅文字，无\"重置筛选\"引导', color: 'orange' }
  ]);

  // 打开 AI 抽屉
  await page.evaluate(() => {
    const root = document.querySelector('[x-data="demHomeTabState()"]') || document.querySelector('[x-data]');
    if (root && window.Alpine && window.Alpine.$data) {
      const d = window.Alpine.$data(root);
      d.userSearchQuery = ''; d.showAiDrawer1 = true;
    }
  });
  await sleep(1000);
  await shot('dem_home_ai_drawer.png', [
    { selector: 'text:DEM AI 智能排障 Agent', label: '[P2] AI回复为固定\"上海CRM/SD-WAN\"模板', color: 'red' }
  ]);

  // ============ 应用详情 ============
  await page.goto(encodeURI(BASE + 'DEM-应用详情.html?appId=app_oa'), { waitUntil: 'networkidle0', timeout: 30000 });
  await sleep(2500);
  log('=== 应用详情 loaded, URL=', page.url());
  const appHas = await page.evaluate(() => {
    const t = document.body.innerText;
    return {
      hasPaiZhang: /排障结论|影响范围|问题定位|处置建议/.test(t),
      hasAI: /智能排障|AI/.test(t),
      hasResponsibility: /责任域|异常分段/.test(t),
      text: t.slice(0, 400)
    };
  });
  log('应用详情 排障结构存在性:', JSON.stringify({ hasPaiZhang: appHas.hasPaiZhang, hasAI: appHas.hasAI, hasResponsibility: appHas.hasResponsibility }));
  await shot('dem_app_detail_topology.png', [
    { selector: 'text:应用详情', label: '[P2] 应用侧仅拓扑/聚类，缺排障结论与处置建议', color: 'orange' }
  ]);

  // ============ 用户详情 ============
  await page.goto(encodeURI(BASE + 'DEM-用户详情.html?type=用户&name=45232（何总）'), { waitUntil: 'networkidle0', timeout: 30000 });
  await sleep(2500);
  log('=== 用户详情 loaded, URL=', page.url());
  const userHas = await page.evaluate(() => {
    const t = document.body.innerText;
    return {
      hasPaiZhang: /排障结论|影响范围|问题定位|处置建议/.test(t),
      hasAI: /AI 智能分析|智能分析|AI/.test(t),
      hasResponsibility: /责任域|异常分段|根因|原因/.test(t)
    };
  });
  log('用户详情 排障结构存在性:', JSON.stringify(userHas));
  await shot('dem_user_detail.png', [
    { selector: 'text:访问路径', label: '[观察] 用户详情排障结构完整', color: 'blue' }
  ]);

  log('\n=== CONSOLE ERRORS (total ' + consoleErrors.length + ') ===');
  consoleErrors.slice(0, 20).forEach(e => log('  ', e));

  await browser.close();
  log('DONE');
})().catch(e => { console.error('FATAL', e); process.exit(1); });
