const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  
  // Navigate to the list page
  await page.goto('http://localhost:5173/InterfaceTemplatePage', { waitUntil: 'networkidle' });
  
  // Wait a bit to ensure everything is rendered
  await page.waitForTimeout(2000);
  
  // Issue 1: Hardcoded disabled buttons in Toolbar
  // Highlight the toolbar
  await page.evaluate(() => {
    const toolbar = document.querySelector('.toolbar-left');
    if (toolbar) {
        toolbar.style.outline = '4px solid red';
        toolbar.style.outlineOffset = '4px';
    }
  });
  await page.screenshot({ path: 'issue1_toolbar.png' });
  
  // Reset outline
  await page.evaluate(() => {
    const toolbar = document.querySelector('.toolbar-left');
    if (toolbar) toolbar.style.outline = 'none';
  });
  
  // Click '新增' to open Drawer
  await page.click('text="新增"');
  await page.waitForTimeout(1000); // wait for drawer animation
  
  // Issue 2: Traditional network config instead of intent-driven (Vision violation)
  // Highlight the MTU / MSS fields
  await page.evaluate(() => {
    const mtuField = [...document.querySelectorAll('.ant-form-item-label label')].find(el => el.innerText.includes('MTU'))?.closest('.ant-form-item');
    const mssField = [...document.querySelectorAll('.ant-form-item-label label')].find(el => el.innerText.includes('MSS'))?.closest('.ant-form-item');
    if (mtuField) mtuField.style.outline = '4px solid #d29922'; // Warning color
    if (mssField) mssField.style.outline = '4px solid #d29922';
  });
  await page.screenshot({ path: 'issue2_form.png' });

  // Issue 3: Required field asterisks alignment hack
  // Highlight the form section
  await page.evaluate(() => {
      // Clear previous outlines
      const items = document.querySelectorAll('.ant-form-item');
      items.forEach(i => i.style.outline = 'none');
      
      const formSection = document.querySelector('.form-section:nth-child(1)');
      if(formSection) {
          formSection.style.outline = '4px solid #3fb950'; // Minor issue
      }
  });
  await page.screenshot({ path: 'issue3_form_alignment.png' });

  await browser.close();
})();
