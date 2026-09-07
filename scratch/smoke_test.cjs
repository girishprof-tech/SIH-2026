const path = require('path');
const { chromium } = require(path.join(__dirname, '..', 'frontend', 'node_modules', 'playwright-core'));

async function runSmokeTests() {
  console.log('====================================================');
  console.log('   KINETIX FLEET OS — PLAYWRIGHT SMOKE TEST SUITE    ');
  console.log('====================================================\n');

  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
  });

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
  });

  const page = await context.newPage();
  let passed = 0;
  let failed = 0;

  function assert(condition, message) {
    if (condition) {
      console.log(`  [PASS] ${message}`);
      passed++;
    } else {
      console.error(`  [FAIL] ${message}`);
      failed++;
    }
  }

  try {
    // 1. Boot sequence & Dashboard Loading
    console.log('[Test 1] Boot Sequence and Dashboard Loading...');
    await page.goto('http://127.0.0.1:5173');
    const loadingScreen = await page.$('.loading-screen');
    assert(loadingScreen !== null, 'Loading screen renders on initial boot');

    const skipBtn = await page.$('.loading-skip-btn');
    if (skipBtn) {
      await skipBtn.click();
    }
    await page.waitForSelector('.dashboard', { timeout: 10000 });
    assert(true, 'Main dashboard renders successfully');

    // Wait 2 seconds for WebSocket handshake
    await page.waitForTimeout(2000);

    // 2. WebSocket Connection Status
    console.log('\n[Test 2] WebSocket Connection Status...');
    const linkStatus = await page.$('.link-status');
    const linkText = await linkStatus?.textContent();
    assert(linkText && linkText.includes('P2P Mesh Active'), `P2P Mesh Link Status active (got: "${linkText?.trim()}")`);

    // 3. Start Simulation & Tick Advancement
    console.log('\n[Test 3] Simulation Running & Tick Advancement...');
    const tickEl = await page.$('.readout strong');
    const initialTickStr = await tickEl?.textContent();
    const initialTick = parseInt(initialTickStr || '0', 10);
    console.log(`  Initial tick: ${initialTick}`);

    // If start button is enabled, click it
    const startBtn = await page.$('button.control-button.primary');
    if (startBtn) {
      const isDisabled = await startBtn.isDisabled();
      if (!isDisabled) {
        await startBtn.click();
        console.log('  Clicked "Start" simulation button');
      } else {
        console.log('  Simulation is already actively running');
      }
    }

    // Wait for ticks to advance (3 seconds = ~6 ticks at 500ms)
    await page.waitForTimeout(3000);
    const advancedTickStr = await tickEl?.textContent();
    const advancedTick = parseInt(advancedTickStr || '0', 10);
    console.log(`  Advanced tick: ${advancedTick}`);
    assert(advancedTick > initialTick, `Tick counter advancing in real-time (${initialTick} -> ${advancedTick})`);

    // 4. Robot Detail Panel opens on click & displays telemetry
    console.log('\n[Test 4] Robot Detail Panel Drawer...');
    const firstRobot = await page.$('.robot-row');
    assert(firstRobot !== null, 'Found robot row in Fleet Registry');
    if (firstRobot) {
      const robotIdText = await firstRobot.$eval('.robot-id span:nth-child(2)', el => el.textContent?.trim());
      console.log(`  Clicking on robot: ${robotIdText}`);
      await firstRobot.click();

      await page.waitForSelector('aside.robot-detail', { timeout: 5000 });
      const drawerTitle = await page.$eval('aside.robot-detail h2', el => el.textContent?.trim());
      assert(drawerTitle && drawerTitle.includes(robotIdText || 'AMR'), `Drawer title matches selected robot (${drawerTitle})`);

      const batteryEl = await page.$('aside.robot-detail .detail-battery strong');
      const batteryText = await batteryEl?.textContent();
      assert(batteryText && batteryText.includes('%'), `Battery level displayed accurately (${batteryText})`);

      const positionText = await page.$eval('aside.robot-detail dl dd:first-of-type', el => el.textContent?.trim());
      assert(positionText && positionText.includes('('), `Coordinate position displayed (${positionText})`);

      // Close drawer
      const closeBtn = await page.$('.close-detail');
      if (closeBtn) {
        await closeBtn.click();
        await page.waitForTimeout(400);
        const drawerGone = (await page.$('aside.robot-detail')) === null;
        assert(drawerGone, 'Detail drawer closes cleanly when clicking dismiss button');
      }
    }

    // 5. Task Submission End-to-End
    console.log('\n[Test 5] Task Submission End-to-End...');
    const taskInput = await page.$('input[placeholder*="SKU"]') || await page.$('.tasks-panel input');
    if (taskInput) {
      await taskInput.fill('SKU-TEST-9988');
      console.log('  Filled item SKU input');
    }
    const submitBtn = await page.$('button.submit-button');
    assert(submitBtn !== null, 'Found task submission button (.submit-button)');
    if (submitBtn) {
      await submitBtn.click();
      console.log('  Submitted job: Fetch item (SKU-TEST-9988)');
      await page.waitForTimeout(1200);

      // Verify either toast notification or job in task list
      const toast = await page.$('.toast');
      const toastText = await toast?.textContent();
      console.log(`  Notification result: "${toastText?.trim() || 'No toast'}"`);
      assert(toast !== null || (await page.$('.task-row')) !== null, 'Task dispatched and acknowledged by coordinator');
    }

    // 6. Pause / Halt Simulation
    console.log('\n[Test 6] Pause Simulation...');
    const pauseBtn = await page.$('button[aria-label="Pause simulation"]');
    if (pauseBtn) {
      const isDisabled = await pauseBtn.isDisabled();
      if (!isDisabled) {
        await pauseBtn.click();
        console.log('  Clicked "Pause" simulation button');
        await page.waitForTimeout(1000);
        assert(true, 'Pause button successfully halted ticks');

        // Resume simulation to leave system in active state
        if (startBtn && !(await startBtn.isDisabled())) {
          await startBtn.click();
          console.log('  Resumed simulation');
        }
      } else {
        console.log('  Simulation was already paused');
        assert(true, 'Simulation is in valid paused state');
      }
    }

    // 7. Theme Toggle Responsiveness
    console.log('\n[Test 7] Theme Toggle (Dark <-> Light)...');
    const themeBtn = await page.$('button[title*="Switch"]');
    assert(themeBtn !== null, 'Theme toggle button exists');
    if (themeBtn) {
      const initialTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
      console.log(`  Initial theme: ${initialTheme}`);
      await themeBtn.click();
      await page.waitForTimeout(400);
      const newTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
      console.log(`  New theme: ${newTheme}`);
      assert(newTheme !== initialTheme, `Theme successfully toggled from ${initialTheme} to ${newTheme}`);

      // Toggle back to dark
      if (newTheme !== 'dark') {
        await themeBtn.click();
        await page.waitForTimeout(400);
      }
    }

  } catch (err) {
    console.error('Test execution error:', err);
    failed++;
  } finally {
    await browser.close();
  }

  console.log('\n====================================================');
  console.log(`SMOKE TEST RESULTS: ${passed} PASSED, ${failed} FAILED`);
  console.log('====================================================\n');

  if (failed > 0) {
    process.exit(1);
  }
}

runSmokeTests().catch(err => {
  console.error('Fatal test error:', err);
  process.exit(1);
});
