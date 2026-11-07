// Trusted checks, one fresh browser context per route and size. Not semantic acceptance.
import {test,expect} from '@playwright/test'
const routes: string[] = /* PA_ROUTES */ ["/"]
for(const route of routes) for(const [width,height] of [[1280,800],[390,844]]){
 test(`页面基础检查 ${route} ${width}`,async({page})=>{
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.setViewportSize({width,height})
  const response=await page.goto(route)
  expect(response?.status()).toBeLessThan(400)
  async function mounted(){
   await expect(page.locator('#root')).toBeVisible()
   await expect.poll(()=>page.locator('#root').evaluate(root=>root.childElementCount)).toBeGreaterThan(0)
   await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2)).toBe(true)
   expect(errors).toEqual([])
  }
  await mounted()
  await page.reload()
  await mounted()
  const focusable=await page.locator('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]').evaluateAll(elements=>elements.some(el=>el.getClientRects().length>0))
  if(focusable){
   await page.keyboard.press('Tab')
   await expect.poll(()=>page.evaluate(()=>document.activeElement!==document.body && document.activeElement!==document.documentElement)).toBe(true)
  }
  expect(errors).toEqual([])
 })
}
