import puppeteer from 'puppeteer-core';
import fs from 'fs';
import path from 'path';

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const ARTIFACT_DIR = 'C:\\Users\\jites\\.gemini\\antigravity\\brain\\18349d6b-8b3f-4018-aa34-580c4c27d5e7';
const SCREENSHOT_DIR = path.join(ARTIFACT_DIR, 'screenshots_verified');
const SESSION_TOKEN = 'gqpEFhhr3YcEXGcXUvzhjFvzr7sZVBUVR50EbZwkgOU';
const BASE_URL = 'http://localhost:5175';

if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const ALL_ROUTES = [
  { path: '/', name: 'overview', title: 'Executive Overview' },
  { path: '/agents', name: 'agents', title: 'AI Financial Agents' },
  { path: '/budget', name: 'budget', title: 'Monthly Budget' },
  { path: '/months', name: 'months', title: 'Monthly Matrix' },
  { path: '/spending', name: 'spending', title: 'Spending Breakdown' },
  { path: '/recurring', name: 'recurring', title: 'Recurring Charges' },
  { path: '/forecast', name: 'forecast', title: 'Cashflow Forecast' },
  { path: '/position', name: 'position', title: 'Financial Position' },
  { path: '/debt', name: 'debt', title: 'Debt & Amortization' },
  { path: '/credit', name: 'credit', title: 'Credit Report' },
  { path: '/portfolio', name: 'portfolio', title: 'Holdings Portfolio' },
  { path: '/owed', name: 'owed', title: 'Claims & Reimbursements' },
  { path: '/ledger', name: 'ledger', title: 'Transaction Ledger' },
  { path: '/review', name: 'review', title: 'Review Inbox' },
  { path: '/explore', name: 'explore', title: 'Explore & Build' },
  { path: '/data', name: 'data', title: 'Data Management' },
  { path: '/rules', name: 'rules', title: 'Rules & Heuristics' },
  { path: '/settings', name: 'settings', title: 'Preferences' },
  { path: '/admin', name: 'admin', title: 'System Admin' },
  { path: '/profile', name: 'profile', title: 'Decryption Details' },
];

async function runAudit() {
  console.log('Starting full application audit across viewports...');
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
  const logs = {
    errors: [],
    warnings: [],
  };

  page.on('console', (msg) => {
    if (msg.type() === 'error') logs.errors.push(msg.text());
    if (msg.type() === 'warning') logs.warnings.push(msg.text());
  });

  page.on('pageerror', (err) => {
    logs.errors.push(`PageError: ${err.message}`);
  });

  // Inject authenticated session cookie directly on BASE_URL
  await page.setCookie({
    name: 'fa_session',
    value: SESSION_TOKEN,
    url: BASE_URL,
    path: '/',
    httpOnly: false,
    secure: false,
  });

  const report = {
    desktopSidebar: {},
    desktopOverlays: {},
    desktopScreens: {},
    tabletScreens: {},
    mobileDrawer: {},
    mobileScreens: {},
    narrowScreens: {},
    errors: [],
  };

  try {
    // ════════════════════════════════════════════════════════════
    // PHASE 1: DESKTOP AUDIT (1440x900)
    // ════════════════════════════════════════════════════════════
    console.log('\n================== PHASE 1: DESKTOP AUDIT (1440x900) ==================');
    await page.setViewport({ width: 1440, height: 900 });
    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle2' });
    await sleep(2000);

    // Initial Overview Screenshot
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'desktop_01_overview.png') });
    console.log('✓ Desktop Overview captured');

    // Sidebar Scroll Test
    console.log('Testing Desktop Left Sidebar scrolling...');
    const railInitial = await page.evaluate(() => {
      const scroll = document.querySelector('.rail-scroll');
      return {
        clientHeight: scroll ? scroll.clientHeight : 0,
        scrollHeight: scroll ? scroll.scrollHeight : 0,
        canScroll: scroll ? scroll.scrollHeight > scroll.clientHeight : false,
      };
    });

    // Scroll rail-scroll to bottom
    await page.evaluate(() => {
      const scroll = document.querySelector('.rail-scroll');
      if (scroll) scroll.scrollTop = scroll.scrollHeight;
    });
    await sleep(600);

    const railScrolled = await page.evaluate(() => {
      const scroll = document.querySelector('.rail-scroll');
      const items = Array.from(document.querySelectorAll('.rail-item'));
      const admin = items.find((i) => i.textContent.includes('Admin'));
      const settings = items.find((i) => i.textContent.includes('Settings'));
      const rules = items.find((i) => i.textContent.includes('Rules'));
      const data = items.find((i) => i.textContent.includes('Data'));
      const foot = document.querySelector('.rail-foot');

      const isInsideViewport = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        return rect.top >= 0 && rect.bottom <= window.innerHeight;
      };

      return {
        scrollTop: scroll ? scroll.scrollTop : 0,
        adminVisible: isInsideViewport(admin),
        settingsVisible: isInsideViewport(settings),
        rulesVisible: isInsideViewport(rules),
        dataVisible: isInsideViewport(data),
        footVisible: isInsideViewport(foot),
      };
    });

    report.desktopSidebar = { railInitial, railScrolled };
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'desktop_02_sidebar_scrolled.png') });
    console.log('✓ Desktop Sidebar scrolled to bottom captured (Admin & Settings visible:', railScrolled.adminVisible, railScrolled.settingsVisible, ')');

    // Light Theme Toggle
    console.log('Testing Light Mode theme toggle...');
    await page.evaluate(() => {
      const btn = document.querySelector('.rail-foot button');
      if (btn) btn.click();
    });
    await sleep(600);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'desktop_03_light_theme.png') });
    console.log('✓ Light mode captured');

    // Toggle back to dark mode
    await page.evaluate(() => {
      const btn = document.querySelector('.rail-foot button');
      if (btn) btn.click();
    });
    await sleep(400);

    // Command Palette (⌘K)
    console.log('Testing Command Palette...');
    await page.keyboard.press('/');
    await sleep(500);
    const paletteOpened = await page.evaluate(() => !!document.querySelector('.command-palette, .modal-backdrop'));
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'desktop_04_command_palette.png') });
    await page.keyboard.press('Escape');
    await sleep(400);
    report.desktopOverlays.palette = paletteOpened;
    console.log('✓ Command Palette opened:', paletteOpened);

    // Copilot Drawer
    console.log('Testing Copilot Drawer...');
    await page.evaluate(() => {
      const btn = document.querySelector('.copilot-trigger-btn');
      if (btn) btn.click();
    });
    await sleep(600);
    const copilotOpened = await page.evaluate(() => !!document.querySelector('.copilot-drawer, .copilot-panel, .drawer-panel'));
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'desktop_05_copilot_drawer.png') });
    // Close copilot
    await page.evaluate(() => {
      const closeBtn = document.querySelector('.drawer-backdrop, .copilot-panel button');
      if (closeBtn) closeBtn.click();
    });
    await sleep(400);
    report.desktopOverlays.copilot = copilotOpened;
    console.log('✓ Copilot Drawer opened:', copilotOpened);

    // Import Wizard
    console.log('Testing Import Wizard...');
    await page.evaluate(() => {
      const btn = document.querySelector('.topbar-import-btn');
      if (btn) btn.click();
    });
    await sleep(600);
    const wizardOpened = await page.evaluate(() => !!document.querySelector('.import-wizard, .modal-backdrop, .modal-dialog'));
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'desktop_06_import_wizard.png') });
    await page.keyboard.press('Escape');
    await sleep(400);
    report.desktopOverlays.wizard = wizardOpened;
    console.log('✓ Import Wizard opened:', wizardOpened);

    // Audit All 20 Screens on Desktop
    console.log('\nAuditing all 20 screens on Desktop (1440x900)...');
    for (const r of ALL_ROUTES) {
      await page.goto(`${BASE_URL}${r.path}`, { waitUntil: 'networkidle2' });
      await sleep(1000);

      const metrics = await page.evaluate(() => {
        const root = document.documentElement;
        return {
          scrollWidth: root.scrollWidth,
          clientWidth: root.clientWidth,
          hasHorizontalOverflow: root.scrollWidth > root.clientWidth,
          title: document.title,
        };
      });

      console.log(`  [Desktop] ${r.path.padEnd(12)} -> overflow: ${metrics.hasHorizontalOverflow}, scrollWidth: ${metrics.scrollWidth}`);
      report.desktopScreens[r.name] = metrics;
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, `desktop_screen_${r.name}.png`) });
    }

    // ════════════════════════════════════════════════════════════
    // PHASE 2: TABLET AUDIT (768x1024)
    // ════════════════════════════════════════════════════════════
    console.log('\n================== PHASE 2: TABLET AUDIT (768x1024) ==================');
    await page.setViewport({ width: 768, height: 1024, isMobile: true, hasTouch: true });
    for (const r of ALL_ROUTES) {
      await page.goto(`${BASE_URL}${r.path}`, { waitUntil: 'networkidle2' });
      await sleep(800);

      const metrics = await page.evaluate(() => {
        const root = document.documentElement;
        return {
          scrollWidth: root.scrollWidth,
          clientWidth: root.clientWidth,
          hasHorizontalOverflow: root.scrollWidth > root.clientWidth,
        };
      });

      console.log(`  [Tablet] ${r.path.padEnd(12)} -> overflow: ${metrics.hasHorizontalOverflow}, scrollWidth: ${metrics.scrollWidth}`);
      report.tabletScreens[r.name] = metrics;
      if (['overview', 'budget', 'spending', 'ledger', 'debt', 'forecast'].includes(r.name)) {
        await page.screenshot({ path: path.join(SCREENSHOT_DIR, `tablet_screen_${r.name}.png`) });
      }
    }

    // ════════════════════════════════════════════════════════════
    // PHASE 3: MOBILE AUDIT (375x667 - iPhone SE)
    // ════════════════════════════════════════════════════════════
    console.log('\n================== PHASE 3: MOBILE AUDIT (375x667) ==================');
    await page.setViewport({ width: 375, height: 667, isMobile: true, hasTouch: true });
    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle2' });
    await sleep(1200);

    // Verify Hamburger Menu Button
    const mobileHeader = await page.evaluate(() => {
      const btn = document.querySelector('.mobile-menu-btn');
      return {
        exists: !!btn,
        display: btn ? window.getComputedStyle(btn).display : 'none',
        visible: btn ? btn.offsetParent !== null : false,
      };
    });
    console.log('Mobile Hamburger Button:', mobileHeader);

    // Open Drawer via Click
    await page.click('.mobile-menu-btn');
    await sleep(600);

    const drawerCheck = await page.evaluate(() => {
      const rail = document.querySelector('.rail');
      const backdrop = document.querySelector('.drawer-backdrop');
      const scroll = document.querySelector('.rail-scroll');
      return {
        hasDrawerOpen: rail ? rail.classList.contains('drawer-open') : false,
        display: rail ? window.getComputedStyle(rail).display : 'none',
        hasBackdrop: !!backdrop,
        canScroll: scroll ? scroll.scrollHeight > scroll.clientHeight : false,
      };
    });

    report.mobileDrawer = drawerCheck;
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'mobile_drawer_open.png') });
    console.log('✓ Mobile Drawer open state:', drawerCheck);

    // Scroll drawer to bottom
    await page.evaluate(() => {
      const scroll = document.querySelector('.rail-scroll');
      if (scroll) scroll.scrollTop = scroll.scrollHeight;
    });
    await sleep(400);
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'mobile_drawer_scrolled.png') });

    // Dismiss drawer by clicking backdrop
    await page.click('.drawer-backdrop');
    await sleep(400);

    // Audit all 20 screens on mobile
    console.log('\nAuditing all 20 screens on Mobile (375x667)...');
    for (const r of ALL_ROUTES) {
      await page.goto(`${BASE_URL}${r.path}`, { waitUntil: 'networkidle2' });
      await sleep(800);

      const metrics = await page.evaluate(() => {
        const root = document.documentElement;
        return {
          scrollWidth: root.scrollWidth,
          clientWidth: root.clientWidth,
          hasHorizontalOverflow: root.scrollWidth > root.clientWidth,
        };
      });

      console.log(`  [Mobile] ${r.path.padEnd(12)} -> overflow: ${metrics.hasHorizontalOverflow}, scrollWidth: ${metrics.scrollWidth}`);
      report.mobileScreens[r.name] = metrics;
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, `mobile_screen_${r.name}.png`) });
    }

    // ════════════════════════════════════════════════════════════
    // PHASE 4: NARROW MOBILE (360x640)
    // ════════════════════════════════════════════════════════════
    console.log('\n================== PHASE 4: NARROW MOBILE (360x640) ==================');
    await page.setViewport({ width: 360, height: 640, isMobile: true, hasTouch: true });
    for (const r of ALL_ROUTES) {
      await page.goto(`${BASE_URL}${r.path}`, { waitUntil: 'networkidle2' });
      await sleep(600);

      const metrics = await page.evaluate(() => {
        const root = document.documentElement;
        return {
          scrollWidth: root.scrollWidth,
          clientWidth: root.clientWidth,
          hasHorizontalOverflow: root.scrollWidth > root.clientWidth,
        };
      });

      report.narrowScreens[r.name] = metrics;
    }

    report.errors = logs.errors;
    fs.writeFileSync(path.join(ARTIFACT_DIR, 'audit_comprehensive.json'), JSON.stringify(report, null, 2));
    console.log('\n================== AUDIT COMPLETED SUCCESSFULLY ==================');
    console.log(`Saved comprehensive report to: ${path.join(ARTIFACT_DIR, 'audit_comprehensive.json')}`);

  } catch (err) {
    console.error('Fatal error during audit:', err);
  } finally {
    await browser.close();
    console.log('Browser instance closed.');
  }
}

runAudit();
