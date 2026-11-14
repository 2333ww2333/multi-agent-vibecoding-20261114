/** 生产模式：FastAPI 托管 dist，同源 /api。 */
import { chromium } from '@playwright/test';

const browser = await chromium.launch({ channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const errors = [];
const bad = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('response', (res) => {
  if (res.status() >= 400) bad.push(`${res.status()} ${res.url()}`);
});

try {
  await page.goto('http://127.0.0.1:8787', { waitUntil: 'networkidle' });
  await page.locator('.composer textarea').waitFor({ timeout: 15_000 });
  await page.screenshot({ path: 'shots/prod-01-home.png' });

  const composer = page.locator('.composer textarea');
  await composer.fill('只回复：生产模式正常');
  await composer.click();
  await page.keyboard.press('Enter');
  await page.locator('.assistant__body').first().waitFor({ timeout: 90_000 });
  await page.locator('.chat-header__status').filter({ hasText: '运行中' })
    .waitFor({ state: 'hidden', timeout: 90_000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: 'shots/prod-02-answer.png' });
  console.log('answer:', (await page.locator('.assistant__body').first().innerText()).slice(0, 80));

  console.log('bad:', bad.length ? JSON.stringify(bad, null, 2) : 'none');
  console.log('errors:', errors.length ? JSON.stringify(errors, null, 2) : 'none');
  if (errors.length || bad.length) process.exitCode = 1;
} catch (error) {
  console.error('FAILED:', error.message);
  await page.screenshot({ path: 'shots/prod-99-failure.png' });
  console.log('bad:', JSON.stringify(bad, null, 2));
  console.log('errors:', JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
