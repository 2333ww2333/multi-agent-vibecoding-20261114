/** 停止按钮：流式过程中取消，应保留已生成文本。 */
import { chromium } from '@playwright/test';

const browser = await chromium.launch({ channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));

try {
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  const composer = page.locator('.composer textarea');
  await composer.fill('请写一篇关于多智能体系统的详细长文，至少两千字，分十节。');
  await composer.click();
  await page.keyboard.press('Enter');

  await page.locator('.chat-header__status').filter({ hasText: '运行中' })
    .waitFor({ timeout: 30_000 });
  await page.waitForTimeout(1800);
  await page.getByRole('button', { name: /停\s*止/ }).click();

  await page.getByText('已停止', { exact: false }).first().waitFor({ timeout: 30_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: 'shots/cancel-01-stopped.png' });
  const text = await page.locator('.assistant__body').first().innerText();
  console.log('assistant chars:', text.length, '| tail:', text.slice(-40));
  console.log('errors:', errors.length ? JSON.stringify(errors, null, 2) : 'none');
  if (errors.length) process.exitCode = 1;
} catch (error) {
  console.error('FAILED:', error.message);
  await page.screenshot({ path: 'shots/cancel-99-failure.png' });
  console.log('errors:', JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
