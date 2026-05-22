import { chromium } from 'playwright';
import path from 'path';
import fs from 'fs';

(async () => {
  console.log('启动 Chromium 浏览器...');
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  
  // Set larger viewport to see all cards and tables comfortably
  await page.setViewportSize({ width: 1440, height: 900 });
  
  console.log('正在导航至 SASE Terminal Assets 页...');
  await page.goto('http://localhost:5173/SaseTerminalAssets', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000); // Allow stable render

  const assetsDir = '/Users/wj/Desktop/项目文件/SD规范Demo/SKillS/skill-design-review/Reports/assets';
  if (!fs.existsSync(assetsDir)) {
    fs.mkdirSync(assetsDir, { recursive: true });
  }

  // -----------------------------
  // Issue 1: Missing core running anomaly metric, fragmented summary cards
  // Highlight the top metric card container
  // -----------------------------
  console.log('生成截图 1: 统计概览区域...');
  await page.evaluate(() => {
    const el = document.querySelector('.app-overview-container');
    if (el) {
      el.style.outline = '4px solid #d29922'; // Warning (orange)
      el.style.outlineOffset = '4px';
    }
  });
  await page.screenshot({ path: path.join(assetsDir, 'issue1_metrics.png') });

  // Reset outline
  await page.evaluate(() => {
    const el = document.querySelector('.app-overview-container');
    if (el) el.style.outline = 'none';
  });

  // -----------------------------
  // Issue 2: Missing quick filter in toolbar for uninstalled/never-installed employees
  // Highlight the table toolbar
  // -----------------------------
  console.log('生成截图 2: 表格工具栏...');
  await page.evaluate(() => {
    const el = document.querySelector('.app-toolbar');
    if (el) {
      el.style.outline = '4px solid #f85149'; // Error (red)
      el.style.outlineOffset = '4px';
    }
  });
  await page.screenshot({ path: path.join(assetsDir, 'issue2_toolbar.png') });

  // Reset outline
  await page.evaluate(() => {
    const el = document.querySelector('.app-toolbar');
    if (el) el.style.outline = 'none';
  });

  // -----------------------------
  // Issue 3: Disjointed UX flow (opens category drawer instead of inline filter), missing closed-loop push reminder
  // Click on "已卸载" grid cell, wait for Drawer A to open, and highlight drawer content
  // -----------------------------
  console.log('打开已卸载侧边栏并截图...');
  // Find grid cell containing uninstalled and click it
  await page.click('.text-uninstalled');
  await page.waitForTimeout(1500); // Wait for drawer open animation

  await page.evaluate(() => {
    const drawer = document.querySelector('.ant-drawer-content-wrapper');
    if (drawer) {
      drawer.style.outline = '4px solid #f85149'; // Error (red)
      drawer.style.outlineOffset = '-4px';
    }
  });
  await page.screenshot({ path: path.join(assetsDir, 'issue3_drawer.png') });

  console.log('所有截图生成成功！');
  await browser.close();
})();
