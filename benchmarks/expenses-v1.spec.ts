import {test,expect} from '@playwright/test';
test('独立验收：金额计算、刷新和删除',async({page})=>{
 await page.goto('/');
 for(const [name,amount] of [['早餐','12.50'],['公交','7.50']]){
  await page.getByLabel('项目名称',{exact:true}).fill(name);
  await page.getByLabel('金额',{exact:true}).fill(amount);
  await page.getByRole('button',{name:'添加记录',exact:true}).click();
 }
 await expect(page.getByTestId('total-amount')).toContainText('20.00');
 await page.reload();
 await expect(page.getByTestId('total-amount')).toContainText('20.00');
 await expect(page.getByText('早餐',{exact:true})).toBeVisible();
 await page.getByRole('listitem').filter({hasText:'早餐'}).getByRole('button',{name:/^删除/}).click();
 await expect(page.getByTestId('total-amount')).toContainText('7.50');
 await page.reload();
 await expect(page.getByText('早餐',{exact:true})).toHaveCount(0);
 await expect(page.getByTestId('total-amount')).toContainText('7.50');
});
