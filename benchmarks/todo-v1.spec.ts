import { test, expect } from '@playwright/test';

test('独立验收：任务完整生命周期与刷新持久化', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('任务标题', { exact: true }).fill('独立验收任务');
  await page.getByRole('button', { name: '新增任务', exact: true }).click();
  await expect(page.getByText('独立验收任务', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('独立验收任务', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  await page.getByLabel('编辑任务标题', { exact: true }).fill('已修改的独立任务');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByText('已修改的独立任务', { exact: true })).toBeVisible();
  const checkbox = page.getByRole('checkbox');
  await checkbox.check();
  await expect(checkbox).toBeChecked();
  await checkbox.press('Space');
  await expect(checkbox).not.toBeChecked();
  await page.getByRole('button', { name: '删除', exact: true }).click();
  await expect(page.getByText('已修改的独立任务', { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('checkbox')).toHaveCount(0);
});

test('独立验收：优先级筛选', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('任务标题', { exact: true }).fill('高优先独立任务');
  await page.getByLabel('优先级', { exact: true }).selectOption({ label: '高' });
  await page.getByRole('button', { name: '新增任务', exact: true }).click();
  await page.getByLabel('任务标题', { exact: true }).fill('低优先独立任务');
  await page.getByLabel('优先级', { exact: true }).selectOption({ label: '低' });
  await page.getByRole('button', { name: '新增任务', exact: true }).click();
  await page.getByRole('button', { name: '高优先级', exact: true }).click();
  await expect(page.getByText('高优先独立任务', { exact: true })).toBeVisible();
  await expect(page.getByText('低优先独立任务', { exact: true })).toHaveCount(0);
});

test('独立验收：手机宽度无水平溢出', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 720 });
  await page.goto('/');
  await expect(page.getByLabel('任务标题', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});
