const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(__dirname, '..', 'frontend', 'node_modules', 'playwright-core'));

const OUT_DIR = path.resolve(__dirname, '..', 'screenshots', 'phase5-after');
fs.mkdirSync(OUT_DIR, { recursive: true });

async function main() {
  console.log('Launching Chrome to capture "after" screenshots...');
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
  });

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
  });

  const page = await context.newPage();

  // 1. Capture Loading Screen (within first 2 seconds)
  console.log('Capturing loading screen...');
  await page.goto('http://127.0.0.1:5173');
  await page.waitForTimeout(1400);
  await page.screenshot({
    path: path.join(OUT_DIR, 'loading_screen.png'),
    fullPage: true,
  });
  console.log('Saved: loading_screen.png');

  // 2. Wait for loading screen to finish (or click skip) and capture Main Control Room View
  console.log('Waiting for main control room dashboard...');
  const skipBtn = await page.$('.loading-skip-btn');
  if (skipBtn) {
    await skipBtn.click().catch(() => undefined);
  }
  await page.waitForSelector('.dashboard', { timeout: 10000 });
  // Give it 2.5s for WebSocket connection and telemetry streaming
  await page.waitForTimeout(2500);
  await page.screenshot({
    path: path.join(OUT_DIR, 'main_control_room.png'),
    fullPage: true,
  });
  console.log('Saved: main_control_room.png');

  // 3. Open Robot Detail Panel by clicking first robot row
  console.log('Opening robot detail panel...');
  await page.waitForSelector('.robot-row');
  await page.click('.robot-row');
  await page.waitForSelector('aside.robot-detail', { timeout: 5000 });
  await page.waitForTimeout(600);
  await page.screenshot({
    path: path.join(OUT_DIR, 'robot_detail_panel.png'),
    fullPage: true,
  });
  console.log('Saved: robot_detail_panel.png');

  // Close robot detail panel to reveal underlying panels
  const closeBtn = await page.$('.close-detail');
  if (closeBtn) {
    await closeBtn.click();
    await page.waitForTimeout(400);
  }

  // 4. Capture Task Submission Panel
  console.log('Capturing task submission panel...');
  const taskPanel = await page.$('.task-panel') || await page.$('.tasks-panel');
  if (taskPanel) {
    await taskPanel.screenshot({
      path: path.join(OUT_DIR, 'task_submission_panel.png'),
    });
  } else {
    await page.screenshot({
      path: path.join(OUT_DIR, 'task_submission_panel.png'),
      fullPage: true,
    });
  }
  console.log('Saved: task_submission_panel.png');

  await browser.close();
  console.log('All 4 after screenshots successfully captured in:', OUT_DIR);
}

main().catch((err) => {
  console.error('Error capturing screenshots:', err);
  process.exit(1);
});
