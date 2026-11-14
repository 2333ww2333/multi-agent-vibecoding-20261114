/** 工作台 / 会话操作的联调冒烟。 */
import { chromium } from '@playwright/test';
import fs from 'node:fs';

fs.mkdirSync('shots', { recursive: true });
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL || 'msedge' });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => {
  if (m.type() === 'error' && !m.text().includes('favicon')) errors.push(`console: ${m.text()}`);
});

const shot = async (name) => {
  await page.screenshot({ path: `shots/${name}.png` });
  console.log('shot', name);
};

try {
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  const composer = page.locator('.composer textarea');
  await composer.waitFor();

  // 先发一条短消息，进入会话
  await composer.fill('只回复：好');
  await composer.click();
  await page.keyboard.press('Enter');
  await page.locator('.assistant__body').first().waitFor({ timeout: 90_000 });
  await page.locator('.chat-header__status').filter({ hasText: '运行中' })
    .waitFor({ state: 'hidden', timeout: 90_000 });

  // 看板：新建 -> 认领 -> 进行中
  const taskTitle = `联调看板任务-${Date.now().toString().slice(-4)}`;
  await page.getByRole('tab', { name: '看板' }).click();
  await page.locator('.panel__extra').getByRole('button', { name: /新建/ }).click();
  await page.locator('#task-title').fill(taskTitle);
  await page.locator('#task-desc').fill('由 E2E 创建，用于验证状态流转');
  await page.getByRole('button', { name: '创 建' }).click();
  await page.getByText(taskTitle).waitFor({ timeout: 15_000 });
  await page.locator('.ant-modal').filter({ hasText: '新建任务' })
    .waitFor({ state: 'hidden', timeout: 15_000 });
  await shot('wb-01-board-created');

  const row = page.locator('.board-item').filter({ hasText: taskTitle });
  await row.getByRole('button', { name: '推进' }).click();
  await page.getByRole('menuitem', { name: '已认领' }).click();
  const claimModal = page.locator('.ant-modal').filter({ hasText: '认领任务' });
  await claimModal.locator('input').fill('alice');
  await claimModal.getByRole('button', { name: '确 认' }).click();
  await claimModal.waitFor({ state: 'hidden', timeout: 15_000 });
  await row.getByText('已认领').waitFor({ timeout: 15_000 });
  await shot('wb-02-board-claimed');

  await row.getByRole('button', { name: '推进' }).click();
  await page.getByRole('menuitem', { name: '进行中' }).click();
  await row.getByText('进行中').waitFor({ timeout: 15_000 });
  await shot('wb-03-board-progress');

  // 队友：召入 -> 关停
  await page.getByRole('tab', { name: '队友' }).click();
  await page.locator('.panel__extra').getByRole('button', { name: /召入/ }).click();
  const spawnModal = page.locator('.ant-modal').filter({ hasText: '召入队友' });
  await spawnModal.locator('#member-name').fill('e2e');
  await spawnModal.locator('#member-role').fill('researcher');
  await spawnModal.getByRole('button', { name: '召 入' }).click();
  await page.getByText('e2e').first().waitFor({ timeout: 20_000 });
  await shot('wb-04-team');

  const teamItem = page.locator('.team-item').filter({ hasText: 'e2e' }).first();
  await teamItem.getByRole('button', { name: '关停' }).click();
  await teamItem.getByText('shutdown', { exact: false }).waitFor({ timeout: 20_000 });

  await page.getByRole('tab', { name: '收件' }).click();
  await page.waitForTimeout(800);
  await shot('wb-05-inbox');

  // 设置页
  await page.goto('http://localhost:5173/settings', { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  await shot('wb-06-settings');

  // 主题切换
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  const themeBtn = page.locator('button[title*="主题"], button[aria-label*="主题"]').first();
  if (await themeBtn.count()) {
    await themeBtn.click();
    await page.waitForTimeout(500);
    await shot('wb-07-theme');
  }

  // 命令面板
  await page.keyboard.press('Control+k');
  await page.waitForTimeout(500);
  await shot('wb-08-palette');
  await page.keyboard.press('Escape');

  console.log('errors:', errors.length ? JSON.stringify(errors, null, 2) : 'none');
  if (errors.length) process.exitCode = 1;
} catch (error) {
  console.error('FAILED:', error.message);
  await shot('wb-99-failure');
  console.log('errors:', JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
