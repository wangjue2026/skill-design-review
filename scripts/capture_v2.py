import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto("http://localhost:5173/InterfaceTemplatePage", wait_until="networkidle")
        
        # -----------------------------
        # Issue 1: Hardcoded disabled button, Batch Operations gap
        # Highlight toolbar buttons
        # -----------------------------
        await page.evaluate("""() => {
            const btns = document.querySelectorAll('.toolbar-left .seer-btn[disabled]');
            btns.forEach(b => {
                b.style.outline = '4px solid #f85149'; // Error color
                b.style.outlineOffset = '2px';
            });
        }""")
        await asyncio.sleep(0.5)
        await page.screenshot(path="issue1.png")
        
        # Reset
        await page.evaluate("""() => {
            document.querySelectorAll('.toolbar-left .seer-btn').forEach(b => b.style.outline = 'none');
        }""")
        
        # -----------------------------
        # Issue 2 & 3: Open drawer
        # -----------------------------
        # The first button in the toolbar is "新增"
        await page.locator(".toolbar-left .seer-btn").first.click()
        await asyncio.sleep(1) # wait for drawer
        
        # Highlight MTU/MSS & Asterisks
        await page.evaluate("""() => {
            // Issue 2: Traditional Config (Not intent-driven)
            const labels = Array.from(document.querySelectorAll('.ant-form-item-label label'));
            const mtuLabel = labels.find(l => l.innerText.includes('MTU'));
            if(mtuLabel) mtuLabel.closest('.ant-form-item').style.outline = '4px solid #d29922'; // Warning
            
            const mssLabel = labels.find(l => l.innerText.includes('MSS'));
            if(mssLabel) mssLabel.closest('.ant-form-item').style.outline = '4px solid #d29922';
            
            // Issue 3: Required asterisk misalignment
            const nameLabel = labels.find(l => l.innerText.includes('模板名称'));
            if(nameLabel) nameLabel.closest('.ant-form-item').style.outline = '4px solid #3fb950'; // Minor
        }""")
        await asyncio.sleep(0.5)
        await page.screenshot(path="issue2.png")
        
        await browser.close()

asyncio.run(main())
