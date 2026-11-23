/* Optional browser check. Run against the loopback-only preview server. */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');

async function run({browser, baseURL = 'http://127.0.0.1:8080', output = path.resolve(__dirname, '../docs')} = {}) {
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(baseURL);
  await page.getByRole('button', {name: 'Accept & get ready', exact: true}).waitFor();
  await page.getByRole('button', {name: 'In progress', exact: true}).click();
  await fs.mkdir(output, {recursive: true});
  await page.screenshot({path: path.join(output, 'activity-preview.png'), fullPage: true});
  assert(await page.locator('.champion-card').isVisible());
  const board = await page.locator('#board-scroll').evaluate(node => ({width: node.clientWidth, total: node.scrollWidth}));
  assert(board.total <= board.width + 2, 'The eight-player desktop bracket should fit without horizontal scrolling');
  await page.getByRole('button', {name: 'Semi-finals, match 2: Live', exact: true}).click();
  await page.getByRole('button', {name: 'Open problem', exact: true}).waitFor();
  await page.getByRole('button', {name: 'Select my match', exact: true}).click();
  await page.getByRole('button', {name: 'Accept & get ready', exact: true}).click();
  await page.locator('.countdown-number').waitFor();
  assert.equal(await page.getByRole('button', {name: 'Open problem', exact: true}).count(), 0);
  await page.getByRole('button', {name: 'Open problem', exact: true}).waitFor({timeout: 20000});
  await page.getByRole('button', {name: 'Check my solve', exact: true}).click();
  await page.getByRole('button', {name: 'Registration', exact: true}).click();
  await page.getByRole('button', {name: 'Leave registration', exact: true}).click();
  await page.getByRole('button', {name: 'Join tournament', exact: true}).click();
  await page.getByRole('button', {name: 'Start tournament', exact: true}).click();
  await page.getByRole('button', {name: 'Accept & get ready', exact: true}).waitFor();
  await page.getByRole('button', {name: 'Finished', exact: true}).click();
  await page.locator('.champion-name').waitFor();
  assert.equal(await page.locator('.champion-name').innerText(), 'ahmad');
  await page.getByRole('button', {name: 'In progress', exact: true}).click();
  await page.getByRole('button', {name: 'Cancel tournament', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: 'Keep playing', exact: true}).click();
  assert.equal(await page.getByRole('dialog').isVisible(), false);
  await page.setViewportSize({width: 390, height: 844});
  await page.waitForTimeout(250);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  assert.equal(overflow, false, 'Only the bracket canvas may scroll sideways on mobile');
  await page.locator('#toast').waitFor({state: 'hidden', timeout: 6500});
  await page.screenshot({path: path.join(output, 'activity-preview-mobile.png'), fullPage: true});
  assert.deepEqual(errors, []);
  await page.close();
  return {passed: ['desktop bracket', 'match selection', 'ready/countdown/reveal', 'join/leave/start', 'champion', 'cancel confirmation', 'mobile overflow', 'no browser errors']};
}
module.exports = {run};

if (require.main === module) {
  (async () => {
    const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
    const browser = await chromium.launch({headless: true, ...(process.env.ACTIVITY_BROWSER ? {executablePath: process.env.ACTIVITY_BROWSER} : {})});
    try { console.log(await run({browser})); } finally { await browser.close(); }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
