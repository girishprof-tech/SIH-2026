---
name: playwright-cli
description: Playwright CLI commands, headless browser automation, capturing full-page UI screenshots, and running smoke-test assertions against localhost web applications.
---

# Playwright CLI Reference

## 1. Quick Screenshot Capture
Capture full page screenshot:
```powershell
npx -y playwright screenshot --viewport-size=1600,1000 --full-page http://localhost:5173/ screenshot.png
```

## 2. Interactive Testing & Smoke Test Scripts
Run automated browser smoke test script via Node:
```powershell
node smoke_test.js
```
Using `@playwright/test` or `playwright` directly:
```javascript
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('http://localhost:5173');
  await page.waitForSelector('.control-bar');
  await page.screenshot({ path: 'control_room.png', fullPage: true });
  await browser.close();
})();
```
