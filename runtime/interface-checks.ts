// Appended to the trusted health suite. No model-authored code selects checks.
const interfaceChecks: {kind:string;route:string;input:string;list:string;add:string;delete_prefix?:string;checkbox_prefix?:string}[] = /* PA_INTERFACE_CHECKS */ []
for (const contract of interfaceChecks) {
 if(contract.kind === 'local_task_delete') test('固定控件约定：删除名称必须包含完整标题，删除后刷新不存在', async({page})=>{
  await page.goto(contract.route)
  const title='独立控件甲'
  const list=page.getByRole('list',{name:contract.list,exact:true})
  for(const value of [title,'独立控件乙']){
   await page.getByLabel(contract.input,{exact:true}).fill(value)
   await page.getByRole('button',{name:contract.add,exact:true}).click()
   await expect(list.getByRole('button',{name:contract.delete_prefix+value,exact:true})).toBeVisible()
  }
  await page.reload()
  await list.getByRole('button',{name:contract.delete_prefix+title,exact:true}).click()
  await expect(list.getByRole('button',{name:contract.delete_prefix+title,exact:true})).toHaveCount(0)
  await page.reload()
  await expect(list.getByRole('listitem')).toHaveCount(1)
  await expect(list.getByRole('button',{name:contract.delete_prefix+'独立控件乙',exact:true})).toBeVisible()
 })
 if(contract.kind === 'crud_task_toggle') {
  test('固定控件约定：勾选即时更新、等待保存、双向持久化', async({page})=>{
   await page.goto(contract.route)
   const title='独立状态核对'
   await page.getByLabel(contract.input,{exact:true}).fill(title)
   const created=page.waitForResponse(r=>r.request().method()==='POST' && new URL(r.url()).pathname==='/api/data/tasks')
   await page.getByRole('button',{name:contract.add,exact:true}).click()
   expect((await created).ok()).toBe(true)
   const box=page.getByRole('list',{name:contract.list,exact:true}).getByRole('checkbox',{name:contract.checkbox_prefix+title,exact:true})
   await expect(box).not.toBeChecked()
   // Hold the response until check() observes the controlled state. This exposes
   // async-only React updates deterministically, without arbitrary sleep timing.
   let release!:()=>void
   const gate=new Promise<void>(resolve=>{release=resolve})
   let writes=0
   await page.route('**/api/data/tasks/*',async route=>{
    if(route.request().method()!=='PATCH')return route.continue()
    writes++;await gate;await route.continue()
   })
   const saved=page.waitForResponse(r=>r.request().method()==='PATCH')
   try {await box.check();await expect(box).toBeChecked();await expect(box).toBeDisabled()}
   finally {release()}
   expect((await saved).ok()).toBe(true)
   await expect(box).toBeEnabled();expect(writes).toBe(1)
   await page.reload();await expect(box).toBeChecked()
   const undone=page.waitForResponse(r=>r.request().method()==='PATCH')
   await box.uncheck();expect((await undone).ok()).toBe(true)
   await expect(box).toBeEnabled();await page.reload();await expect(box).not.toBeChecked()
  })
  test('固定控件约定：保存失败回滚，允许重试且刷新一致',async({page})=>{
   await page.goto(contract.route)
   const title='独立失败核对'
   await page.getByLabel(contract.input,{exact:true}).fill(title)
   await page.getByRole('button',{name:contract.add,exact:true}).click()
   const box=page.getByRole('list',{name:contract.list,exact:true}).getByRole('checkbox',{name:contract.checkbox_prefix+title,exact:true})
   await expect(box).toBeEnabled();await expect(box).not.toBeChecked()
   let release!:()=>void
   const gate=new Promise<void>(resolve=>{release=resolve})
   await page.route('**/api/data/tasks/*',async route=>{
    if(route.request().method()!=='PATCH')return route.continue()
    await gate;await route.fulfill({status:500,contentType:'application/json',body:'{"detail":"合成保存失败"}'})
   })
   const failed=page.waitForResponse(r=>r.request().method()==='PATCH')
   try {await box.check();await expect(box).toBeChecked();await expect(box).toBeDisabled()}
   finally {release()}
   expect((await failed).status()).toBe(500)
   await expect(box).toBeEnabled();await expect(box).not.toBeChecked()
   await page.unroute('**/api/data/tasks/*')
   await page.reload();await expect(box).not.toBeChecked()
   const retry=page.waitForResponse(r=>r.request().method()==='PATCH')
   await box.check();expect((await retry).ok()).toBe(true)
   await expect(box).toBeEnabled();await page.reload();await expect(box).toBeChecked()
  })
 }
}
