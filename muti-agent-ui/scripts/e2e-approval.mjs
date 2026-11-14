/** 审批弹窗：先截图，再允许（dry-run，不真正安装）。 */
import { chromium } from '@playwright/test';

const browser = await chromium.launch({ channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

try {
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  const composer = page.locator('.composer textarea');
  await composer.fill('请用 run_command 执行 pip install --dry-run requests，然后告诉我结果。');
  await composer.click();
  await page.keyboard.press('Enter');

  const modal = page.locator('.ant-modal').filter({ hasText: '需要确认' });
  await modal.waitFor({ state: 'visible', timeout: 90_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'shots/approval-01-modal.png' });
  console.log('modal text:', (await modal.innerText()).slice(0, 200));

  await modal.getByRole('button', { name: /允\s*许/ }).click();
  await modal.waitFor({ state: 'hidden', timeout: 20_000 });
  await page.locator('.chat-header__status').filter({ hasText: '运行中' })
    .waitFor({ state: 'hidden', timeout: 120_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'shots/approval-02-allowed.png' });

  console.log('errors:', errors.length ? JSON.stringify(errors, null, 2) : 'none');
  if (errors.length) process.exitCode = 1;
} catch (error) {
  console.error('FAILED:', error.message);
  await page.screenshot({ path: 'shots/approval-99-failure.png' });
  console.log('errors:', JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
