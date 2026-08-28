const puppeteer = require('puppeteer');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--user-data-dir=/tmp/dem_chrome_profile']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.on('console', m => { if (m.type()==='error') console.log('[err]', m.text().slice(0,150)); });
  page.on('pageerror', e => console.log('[pageerr]', e.message.slice(0,150)));

  const urls = {
    '用户详情': 'DEM-用户详情.html?type=用户&name=45232（何总）',
    '应用详情': 'DEM-应用详情.html?appId=app_oa'
  };
  for (const [name, u] of Object.entries(urls)) {
    await page.goto(encodeURI('http://127.0.0.1:8734/05-方案五/' + u), { waitUntil: 'networkidle0', timeout: 30000 });
    await sleep(4000);
    const info = await page.evaluate(() => {
      const txt = document.body.innerText;
      // 提取标题类元素文本
      const heads = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,.section-title,[class*="title"]')).map(e => e.textContent.trim()).filter(t => t && t.length < 30);
      return { txt: txt.slice(0, 3000), heads: heads.slice(0, 60), hasAlpine: typeof window.Alpine !== 'undefined' };
    });
    console.log('\n\n=====', name, '=====');
    console.log('Alpine loaded:', info.hasAlpine);
    console.log('--- headings ---');
    console.log(JSON.stringify(info.heads, null, 0));
    console.log('--- body text (first 3000) ---');
    console.log(info.txt);
  }
  await browser.close();
})().catch(e => { console.error('FATAL', e); process.exit(1); });
