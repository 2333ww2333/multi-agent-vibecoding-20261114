/** Mock 模式独立运行验证。 */
import { chromium } from '@playwright/test';

const browser = await chromium.launch({ channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const errors = [];
const apiCalls = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
page.on('request', (request) => {
  const path = new URL(request.url()).pathname;
  if (path.startsWith('/api/')) apiCalls.push(request.url());
});

try {
  await page.goto('http://localhost:5174', { waitUntil: 'networkidle' });
  await page.getByText('MOCK').waitFor({ timeout: 10_000 });
  const composer = page.locator('.composer textarea');
  await composer.fill('你好，给我一个三步计划');
  await composer.click();
  await page.keyboard.press('Enter');
  await page.locator('.assistant__body').first().waitFor({ timeout: 30_000 });
  await page.locator('.chat-header__status').filter({ hasText: '运行中' })
    .waitFor({ state: 'hidden', timeout: 30_000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: 'shots/mock-01-answer.png' });
  console.log('answer:', (await page.locator('.assistant__body').first().innerText()).slice(0, 100));
  console.log('apiCalls:', apiCalls.length);
  console.log('errors:', errors.length ? JSON.stringify(errors, null, 2) : 'none');
  if (errors.length || apiCalls.length) process.exitCode = 1;
} catch (error) {
  console.error('FAILED:', error.message);
  await page.screenshot({ path: 'shots/mock-99-failure.png' });
  console.log('errors:', JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
