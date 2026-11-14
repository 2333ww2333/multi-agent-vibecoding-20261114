/** 会话重命名与删除。 */
import { chromium } from '@playwright/test';

const browser = await chromium.launch({ channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

try {
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  const item = page.locator('.ant-conversations-item').first();
  await item.waitFor();
  const original = (await item.innerText()).trim();
  await item.hover();
  await item.locator('.ant-conversations-menu-icon').click();
  await page.getByRole('menuitem', { name: '重命名' }).click();

  const modal = page.locator('.ant-modal').filter({ hasText: '重命名会话' });
  const input = modal.locator('input');
  await input.fill('E2E 改名后的会话');
  await modal.getByRole('button', { name: /保\s*存/ }).click();
  await modal.waitFor({ state: 'hidden', timeout: 10_000 });
  await page.getByText('E2E 改名后的会话').waitFor({ timeout: 10_000 });
  await page.screenshot({ path: 'shots/session-01-renamed.png' });
  console.log('renamed from', original);

  const renamed = page.locator('.ant-conversations-item').filter({ hasText: 'E2E 改名后的会话' }).first();
  await renamed.hover();
  await renamed.locator('.ant-conversations-menu-icon').click();
  await page.getByRole('menuitem', { name: '删除' }).click();
  const confirm = page.locator('.ant-modal-confirm').filter({ hasText: '删除会话' });
  await confirm.getByRole('button', { name: /删\s*除/ }).click();
  await page.getByText('已删除').waitFor({ timeout: 10_000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: 'shots/session-02-deleted.png' });
  console.log('deleted');

  console.log('errors:', errors.length ? JSON.stringify(errors, null, 2) : 'none');
  if (errors.length) process.exitCode = 1;
} catch (error) {
  console.error('FAILED:', error.message);
  await page.screenshot({ path: 'shots/session-99-failure.png' });
  console.log('errors:', JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
