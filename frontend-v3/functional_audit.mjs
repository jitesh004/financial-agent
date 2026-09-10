import puppeteer from 'puppeteer-core';
import fs from 'fs';
import path from 'path';

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const ARTIFACT_DIR = 'C:\\Users\\jites\\.gemini\\antigravity\\brain\\18349d6b-8b3f-4018-aa34-580c4c27d5e7';
const SCREENSHOT_DIR = path.join(ARTIFACT_DIR, 'screenshots_functional');
const SESSION_TOKEN = 'gqpEFhhr3YcEXGcXUvzhjFvzr7sZVBUVR50EbZwkgOU';
const BASE_URL = 'http://localhost:5175';

if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let totalTests = 0;
let passedTests = 0;
let failedTests = 0;
const testResults = [];

function assert(condition, description, detail = '') {
  totalTests++;
  if (condition) {
    passedTests++;
    console.log(`  ✓ PASS: ${description}`);
    testResults.push({ status: 'PASS', description, detail });
  } else {
    failedTests++;
    console.error(`  ✗ FAIL: ${description} - ${detail}`);
    testResults.push({ status: 'FAIL', description, detail });
  }
}

async function runFunctionalSuite() {
  console.log('Starting Full Functional E2E Test Suite on Chrome Browser...');
  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: 'new',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-gpu',
      '--window-size=1440,900',
    ],
  });

  const page = await browser.newPage();
  const logs = { errors: [], warnings: [] };

  page.on('console', (msg) => {
    if (msg.type() === 'error') logs.errors.push(msg.text());
  });

  page.on('pageerror', (err) => {
    logs.errors.push(`PageError: ${err.message}`);
  });

  // Inject session cookie
  await page.setCookie({
    name: 'fa_session',
    value: SESSION_TOKEN,
    url: BASE_URL,
    path: '/',
    httpOnly: false,
    secure: false,
  });

  try {
    await page.setViewport({ width: 1440, height: 900 });

    // ────────────────────────────────────────────────────────────
    // TEST SUITE 1: Overview & PeriodBar Temporal State
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 1/8] Testing Overview & PeriodBar Temporal State Switching...');
    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle2' });
    await sleep(2000);

    const initialLabel = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('.glass-card span'));
      // The resolved range label is in the right section of the period bar
      const rangeSpan = spans.find((s) => s.textContent.includes('All time') || s.textContent.includes('—') || /\b202\d\b/.test(s.textContent));
      return rangeSpan ? rangeSpan.textContent.trim() : (spans[spans.length - 1]?.textContent.trim() || '');
    });
    console.log(`    Initial Period Label: "${initialLabel}"`);
    assert(initialLabel.length > 0, 'Period bar displays resolved date range label');

    // Click '3M' preset in .segmented-control
    const clicked3M = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('.segmented-control button, button[role="tab"]'));
      const b3m = btns.find((b) => b.textContent.trim() === '3M');
      if (b3m) { b3m.click(); return true; }
      return false;
    });
    assert(clicked3M, 'Clicked 3M period button in Segmented control');
    await sleep(1500);

    const label3M = await page.evaluate(() => {
      const spans = Array.from(document.querySelectorAll('.glass-card span'));
      const rangeSpan = spans.find((s) => s.textContent.includes('All time') || s.textContent.includes('—') || /\b202\d\b/.test(s.textContent));
      return rangeSpan ? rangeSpan.textContent.trim() : (spans[spans.length - 1]?.textContent.trim() || '');
    });
    console.log(`    Updated Period Label (3M): "${label3M}"`);
    assert(label3M !== initialLabel, 'Period label updated when 3M selected', `Previous: ${initialLabel}, Now: ${label3M}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, '01_period_3m_selected.png') });

    // Click 'All' preset to reset
    await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('.segmented-control button, button[role="tab"]'));
      const bAll = btns.find((b) => b.textContent.trim() === 'All');
      if (bAll) bAll.click();
    });
    await sleep(1000);

    // ────────────────────────────────────────────────────────────
    // TEST SUITE 2: Debt & Loan Prepayment Simulator
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 2/8] Testing Debt Screen & Prepayment Simulator...');
    await page.goto(`${BASE_URL}/debt`, { waitUntil: 'networkidle2' });
    await sleep(1500);

    // Read initial simulation values
    const debtInitial = await page.evaluate(() => {
      const savedEl = document.querySelector('.stat-value.pos');
      return {
        interestSaved: savedEl ? savedEl.textContent.trim() : '',
        hasSliders: document.querySelectorAll('input[type="range"]').length >= 2,
      };
    });
    assert(debtInitial.hasSliders, 'Found prepayment simulator sliders on /debt');
    console.log(`    Initial Interest Saved with +₹5,000/mo: "${debtInitial.interestSaved}"`);

    // Toggle strategy to Snowball
    const toggledSnowball = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const sb = btns.find((b) => b.textContent.includes('Snowball'));
      if (sb) { sb.click(); return true; }
      return false;
    });
    assert(toggledSnowball, 'Toggled payoff strategy to Snowball (Lowest Balance)');
    await sleep(500);

    // Toggle back to Avalanche
    await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const av = btns.find((b) => b.textContent.includes('Avalanche'));
      if (av) av.click();
    });
    await sleep(500);

    // Adjust Prepayment Slider to ₹25,000/mo using React native value setter
    const sliderResult = await page.evaluate(() => {
      const sliders = document.querySelectorAll('input[type="range"]');
      const monthlySlider = sliders[0];
      if (monthlySlider) {
        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        nativeSetter.call(monthlySlider, '25000');
        monthlySlider.dispatchEvent(new Event('input', { bubbles: true }));
        monthlySlider.dispatchEvent(new Event('change', { bubbles: true }));
      }
      const savedEl = document.querySelector('.stat-value.pos');
      const numLabel = document.querySelector('.num.font-semibold[style*="color: var(--brand-primary)"]');
      return {
        newInterestSaved: savedEl ? savedEl.textContent.trim() : '',
        labelMonthly: numLabel ? numLabel.textContent.trim() : '',
      };
    });
    console.log(`    After setting +₹25,000/mo prepayment -> Label: ${sliderResult.labelMonthly}, Interest Saved: ${sliderResult.newInterestSaved}`);
    assert(sliderResult.labelMonthly.includes('25,000'), 'Prepayment slider updated label to +₹25,000/mo');
    assert(sliderResult.newInterestSaved !== debtInitial.interestSaved, 'Prepayment simulator dynamically recalculated interest saved', `Initial: ${debtInitial.interestSaved}, After: ${sliderResult.newInterestSaved}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, '02_debt_simulator_updated.png') });


    // ────────────────────────────────────────────────────────────
    // TEST SUITE 3: Budget Cut Simulator
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 3/8] Testing Monthly Budget & Cut Simulator...');
    await page.goto(`${BASE_URL}/budget`, { waitUntil: 'networkidle2' });
    await sleep(1500);

    const budgetInitial = await page.evaluate(() => {
      const boostEl = document.querySelector('.num.font-bold.pos');
      return {
        boostText: boostEl ? boostEl.textContent.trim() : '',
      };
    });
    console.log(`    Initial monthly cash boost (0% cut): "${budgetInitial.boostText}"`);

    // Click −10% Quick-cut button
    const cutClicked = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const b10 = btns.find((b) => b.textContent.trim() === '−10%');
      if (b10) { b10.click(); return true; }
      return false;
    });
    assert(cutClicked, 'Clicked −10% preset in Budget Cut Simulator');
    await sleep(600);

    const budgetAfter10 = await page.evaluate(() => {
      const boostEl = document.querySelector('.num.font-bold.pos');
      const pctEl = document.querySelector('.num.font-semibold[style*="color: var(--accent-emerald)"]');
      return {
        boostText: boostEl ? boostEl.textContent.trim() : '',
        pctLabel: pctEl ? pctEl.textContent.trim() : '',
      };
    });
    console.log(`    After −10% cut -> Label: ${budgetAfter10.pctLabel}, Monthly Cash Boost: ${budgetAfter10.boostText}`);
    assert(budgetAfter10.pctLabel === '−10%', 'Budget cut percentage updated to −10%');
    assert(budgetAfter10.boostText !== budgetInitial.boostText, 'Budget simulator dynamically calculated monthly surplus', `Initial: ${budgetInitial.boostText}, New: ${budgetAfter10.boostText}`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, '03_budget_cut_10pct.png') });

    // ────────────────────────────────────────────────────────────
    // TEST SUITE 4: Power Terminal (Ledger) Filtering & Search
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 4/8] Testing Power Terminal Ledger Search, Filters & Details...');
    await page.goto(`${BASE_URL}/ledger`, { waitUntil: 'networkidle2' });
    await sleep(1500);

    const ledgerInitial = await page.evaluate(() => {
      const rows = document.querySelectorAll('.virtual-row, table tbody tr');
      return {
        rowCount: rows.length,
        hasRows: rows.length > 0,
      };
    });
    assert(ledgerInitial.hasRows, `Ledger loaded successfully with ${ledgerInitial.rowCount} visible rows`);

    // Type query into search input
    console.log('    Typing "UPI" into Ledger search bar...');
    const searchInput = await page.$('input[placeholder*="Search"], input[type="search"], .search input');
    if (searchInput) {
      await searchInput.type('UPI');
      await sleep(1200); // Wait for debounce
      const afterSearch = await page.evaluate(() => {
        const rows = Array.from(document.querySelectorAll('.virtual-row, table tbody tr'));
        const hasText = rows.some((r) => r.textContent.includes('UPI'));
        return { count: rows.length, hasMatch: hasText };
      });
      assert(afterSearch.hasMatch, 'Ledger filtered transactions matching query "UPI"');
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, '04_ledger_search_upi.png') });

      // Clear search
      await page.evaluate(() => {
        const inp = document.querySelector('input[placeholder*="Search"], input[type="search"], .search input');
        if (inp) {
          inp.value = '';
          inp.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });
      await sleep(800);
    }

    // Switch account tab to "Credit Cards"
    console.log('    Switching ledger tab to "Credit Cards"...');
    const clickedCardsTab = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('.segmented button, .tab-btn, button'));
      const cardTab = btns.find((b) => b.textContent.trim() === 'Credit Cards');
      if (cardTab) { cardTab.click(); return true; }
      return false;
    });
    assert(clickedCardsTab, 'Switched Ledger view to "Credit Cards"');
    await sleep(1000);

    // Switch back to "All Accounts"
    await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('.segmented button, .tab-btn, button'));
      const allTab = btns.find((b) => b.textContent.trim() === 'All Accounts');
      if (allTab) allTab.click();
    });
    await sleep(800);

    // ────────────────────────────────────────────────────────────
    // TEST SUITE 5: Deterministic Rules Engine Heuristic Sandbox
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 5/8] Testing Rules Engine & Explain Sandbox...');
    await page.goto(`${BASE_URL}/rules?section=explain`, { waitUntil: 'networkidle2' });
    await sleep(1500);

    const testDescription = 'UPI/SWIGGY/AUG25/BANGALORE/123456/FOOD';
    console.log(`    Testing narration in sandbox: "${testDescription}"...`);

    const testedSandbox = await page.evaluate(async (desc) => {
      const input = document.querySelector('input[placeholder*="SWIGGY"], input.input');
      if (input) {
        input.value = desc;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      try {
        const res = await fetch('/api/rules/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ description: desc, direction: 'debit' }),
        });
        const data = await res.json();
        return {
          ok: res.ok,
          status: res.status,
          category: data?.description?.winner?.category || data?.category || '',
          data,
        };
      } catch (err) {
        return { ok: false, error: err.message };
      }
    }, testDescription);

    console.log('    Sandbox API Classification Result:', testedSandbox);
    assert(testedSandbox.ok, 'Rule engine test endpoint responded with 200 OK');
    assert(testedSandbox.category === 'dining', `Rule engine accurately assigned category: "${testedSandbox.category}"`);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, '05_rules_explain_sandbox.png') });

    // ────────────────────────────────────────────────────────────
    // TEST SUITE 6: Custom Dashboards & Analytics Widget Studio
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 6/8] Testing Explore & Custom Dashboard Builder...');
    await page.goto(`${BASE_URL}/explore`, { waitUntil: 'networkidle2' });
    await sleep(2000);

    const exploreWidgets = await page.evaluate(() => {
      const widgets = document.querySelectorAll('.explore-grid > *');
      return { count: widgets.length };
    });
    console.log(`    Found ${exploreWidgets.count} custom widgets rendered on board`);
    assert(exploreWidgets.count > 0, 'Explore board loaded widgets cleanly');
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, '06_explore_dashboard.png') });

    // ────────────────────────────────────────────────────────────
    // TEST SUITE 7: Omni-Command Palette (⌘K) Navigation
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 7/8] Testing Omni-Command Palette Navigation...');
    await page.evaluate(() => {
      const trigger = document.querySelector('.topbar-search-trigger');
      if (trigger) trigger.click();
    });
    await sleep(600);

    const paletteCheck = await page.evaluate(() => {
      const modal = document.querySelector('.command-palette, .modal-dialog, .palette-modal');
      const input = document.querySelector('.command-palette input, .palette-input, input[placeholder*="command"], input[placeholder*="Type"]');
      return {
        hasModal: !!modal,
        hasInput: !!input,
      };
    });
    assert(paletteCheck.hasModal || paletteCheck.hasInput, 'Command Palette opened upon trigger click');
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, '07_command_palette_active.png') });

    // Type "forecast"
    await page.keyboard.type('forecast');
    await sleep(500);

    // Press Escape to dismiss
    await page.keyboard.press('Escape');
    await sleep(400);

    // ────────────────────────────────────────────────────────────
    // TEST SUITE 8: Financial Copilot AI Assistant Drawer
    // ────────────────────────────────────────────────────────────
    console.log('\n[Suite 8/8] Testing Financial Copilot Drawer Interactions...');
    await page.evaluate(() => {
      const btn = document.querySelector('.copilot-trigger-btn');
      if (btn) btn.click();
    });
    await sleep(600);

    const copilotCheck = await page.evaluate(() => {
      const drawer = document.querySelector('.drawer-panel, .copilot-drawer');
      const chips = Array.from(document.querySelectorAll('.drawer-panel button, .copilot-drawer button'))
        .map((b) => b.textContent.trim());
      return {
        hasDrawer: !!drawer,
        chipsCount: chips.length,
      };
    });
    assert(copilotCheck.hasDrawer, 'Financial Copilot drawer opened smoothly');
    assert(copilotCheck.chipsCount > 0, 'Financial Copilot loaded prompt suggestions and specialized agents');
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, '08_copilot_drawer_active.png') });

    // Close Copilot Drawer
    await page.evaluate(() => {
      const closeBtn = document.querySelector('.drawer-panel button, .drawer-backdrop');
      if (closeBtn) closeBtn.click();
    });
    await sleep(400);

    // ────────────────────────────────────────────────────────────
    // FINAL RESULTS & REPORT GENERATION
    // ────────────────────────────────────────────────────────────
    console.log('\n============================================================');
    console.log(`Functional E2E Suite Completed: ${passedTests}/${totalTests} Passed (${failedTests} Failures)`);
    console.log(`Console Errors Encountered: ${logs.errors.length}`);
    console.log('============================================================');

    const summary = {
      totalTests,
      passedTests,
      failedTests,
      allPassed: failedTests === 0,
      testResults,
      consoleErrors: logs.errors,
    };

    fs.writeFileSync(
      path.join(ARTIFACT_DIR, 'functional_audit_report.json'),
      JSON.stringify(summary, null, 2),
    );
    console.log(`Saved report to: ${path.join(ARTIFACT_DIR, 'functional_audit_report.json')}`);

  } catch (err) {
    console.error('Suite crashed with error:', err);
  } finally {
    await browser.close();
    console.log('Browser closed.');
  }
}

runFunctionalSuite();
