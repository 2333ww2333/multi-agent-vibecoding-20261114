/** 前后端联调冒烟：真实 BFF + 真实模型。 */
import { chromium } from '@playwright/test';
import fs from 'node:fs';

fs.mkdirSync('shots', { recursive: true });
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL || 'msedge' });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
const page = await context.newPage();
const errors = [];
const apiErrors = [];

page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => {
  if (m.type() === 'error' && !m.text().includes('favicon')) errors.push(`console: ${m.text()}`);
});
page.on('response', (res) => {
  if (res.url().includes('/api/') && res.status() >= 400) {
    apiErrors.push(`${res.status()} ${res.url()}`);
  }
});

const shot = async (name) => {
  await page.screenshot({ path: `shots/${name}.png` });
  console.log('shot', name);
};

try {
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  await shot('live-01-empty');

  const composer = page.locator('.composer textarea');
  await composer.waitFor({ timeout: 15_000 });
  await composer.fill('只回复这一句：联调成功');
  await composer.click();
  await page.keyboard.press('Enter');

  // 等主区域真的出现助手回答（不能只匹配侧栏标题）
  const answer = page.locator('.assistant__body').first();
  await answer.waitFor({ timeout: 90_000 });
  await page.locator('.chat-header__status').filter({ hasText: '运行中' })
    .waitFor({ state: 'hidden', timeout: 90_000 });
  await page.waitForTimeout(800);
  await shot('live-02-answer');
  console.log('url:', page.url());
  console.log('answer:', (await answer.innerText()).slice(0, 120));

  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.assistant__body').first().waitFor({ timeout: 30_000 });
  await shot('live-03-refresh');

  // 看板 / 队友 / 文件 / 账单
  for (const tab of ['看板', '队友', '文件', '账单']) {
    await page.getByRole('tab', { name: tab }).click();
    await page.waitForTimeout(700);
    await shot(`live-04-${tab}`);
  }

  // 审批：让模型调用高危命令
  await page.getByRole('tab', { name: '计划' }).click();
  await composer.fill('请用 run_command 执行 pip install requests，然后告诉我结果。');
  await composer.click();
  await page.keyboard.press('Enter');

  const modal = page.locator('.ant-modal').filter({ hasText: '需要确认' });
  let sawApproval = false;
  try {
    await modal.waitFor({ timeout: 90_000 });
    sawApproval = true;
    await shot('live-05-approval');
    await page.getByRole('button', { name: /拒\s*绝/ }).click();
    await page.waitForTimeout(6000);
    await shot('live-06-denied');
  } catch {
    console.log('approval modal not shown (model may not have called run_command)');
    await shot('live-05-no-approval');
  }

  console.log('sawApproval:', sawApproval);
  console.log('apiErrors:', apiErrors.length ? JSON.stringify(apiErrors, null, 2) : 'none');
  console.log('errors:', errors.length ? JSON.stringify(errors, null, 2) : 'none');
  if (errors.length) process.exitCode = 1;
} catch (error) {
  console.error('FAILED:', error.message);
  await shot('live-99-failure');
  console.log('apiErrors:', JSON.stringify(apiErrors, null, 2));
  console.log('errors:', JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}
