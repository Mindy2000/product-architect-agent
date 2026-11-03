import {test,expect} from '@playwright/test';
test('独立验收：阅读清单、阅读状态和筛选持久化',async({page})=>{
 await page.goto('/');
 for(const [title,author] of [['工程实践','作者甲'],['城市生活','作者乙']]){
  await page.getByLabel('书名',{exact:true}).fill(title);
  await page.getByLabel('作者',{exact:true}).fill(author);
  await page.getByRole('button',{name:'添加书籍',exact:true}).click();
  await expect(page.getByRole('listitem').filter({hasText:title})).toBeVisible();
 }
 const book=page.getByRole('listitem').filter({hasText:'工程实践'});
 await book.getByLabel('阅读状态',{exact:true}).selectOption({label:'读完'});
 await page.getByLabel('状态筛选',{exact:true}).selectOption({label:'读完'});
 await expect(book).toBeVisible();
 await expect(page.getByRole('listitem').filter({hasText:'城市生活'})).toHaveCount(0);
 await page.reload();
 await page.getByLabel('状态筛选',{exact:true}).selectOption({label:'全部'});
 await expect(book.getByLabel('阅读状态',{exact:true}).locator('option:checked')).toHaveText('读完');
 await expect(page.getByText('作者甲',{exact:true})).toBeVisible();
});
