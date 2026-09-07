const path = require('path');
const { chromium } = require(path.join(__dirname, '..', 'frontend', 'node_modules', 'playwright-core'));

async function testLight() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto('http://127.0.0.1:5173');
  const skipBtn = await page.$('.loading-skip-btn');
  if (skipBtn) await skipBtn.click();
  await page.waitForSelector('.dashboard', { timeout: 10000 });
  await page.waitForTimeout(1500);
  const themeBtn = await page.$('button[title*="Switch"]');
  if (themeBtn) await themeBtn.click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(__dirname, '..', 'screenshots', 'phase5-after', 'main_control_room_light.png'), fullPage: true });
  await page.evaluate(() => localStorage.setItem('sih_theme', 'dark'));
  await browser.close();
  console.log('Saved main_control_room_light.png and restored dark theme in localStorage');
}

testLight().catch(console.error);
