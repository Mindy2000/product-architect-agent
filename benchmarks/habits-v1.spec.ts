import {test,expect} from '@playwright/test';
test('独立验收：习惯创建、打卡、恢复及取消',async({page})=>{
 await page.goto('/');
 for(const name of ['晨间阅读','散步']){
  await page.getByLabel('习惯名称',{exact:true}).fill(name);
  await page.getByRole('button',{name:'添加习惯',exact:true}).click();
  await expect(page.getByRole('listitem').filter({hasText:name})).toBeVisible();
 }
 const reading=page.getByRole('listitem').filter({hasText:'晨间阅读'});
 await reading.getByRole('button',{name:'今日打卡',exact:true}).click();
 await expect(page.getByTestId('today-count')).toHaveText('1');
 await page.reload();
 await expect(page.getByTestId('today-count')).toHaveText('1');
 await reading.getByRole('button',{name:'取消打卡',exact:true}).click();
 await expect(page.getByTestId('today-count')).toHaveText('0');
 await page.getByRole('listitem').filter({hasText:'散步'}).getByRole('button',{name:'删除',exact:true}).click();
 await page.reload();
 await expect(page.getByRole('listitem').filter({hasText:'散步'})).toHaveCount(0);
});
