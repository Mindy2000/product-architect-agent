import {test,expect} from '@playwright/test';
test('独立验收：笔记保存、检索、刷新和删除',async({page})=>{
 await page.goto('/');
 for(const [title,body] of [['工作安排','下午准备项目演示'],['旅行清单','带上充电器']]){
  await page.getByRole('button',{name:'新建笔记',exact:true}).first().click();
  await page.getByLabel('笔记标题',{exact:true}).fill(title);
  await page.getByLabel('笔记内容',{exact:true}).fill(body);
  await page.getByRole('button',{name:'保存笔记',exact:true}).click();
  await expect(page.getByText(title,{exact:true})).toBeVisible();
 }
 await page.reload();
 await expect(page.getByText('工作安排',{exact:true})).toBeVisible();
 await page.getByLabel('搜索笔记',{exact:true}).fill('充电器');
 await expect(page.getByText('旅行清单',{exact:true})).toBeVisible();
 await expect(page.getByText('工作安排',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'删除',exact:true}).click();
 const confirm = page.getByRole('button',{name:'确认删除',exact:true});
 if(await confirm.isVisible()) await confirm.click();
 await page.getByLabel('搜索笔记',{exact:true}).fill('');
 await page.reload();
 await expect(page.getByText('旅行清单',{exact:true})).toHaveCount(0);
 await expect(page.getByText('工作安排',{exact:true})).toBeVisible();
});
