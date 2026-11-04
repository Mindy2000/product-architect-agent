// Fixed platform checks. These do not certify product requirements or visual quality.
import {test,expect} from '@playwright/test'
for(const [width,height] of [[1280,800],[390,844]]){
 test(`页面基础检查 ${width}`,async({page})=>{
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.setViewportSize({width,height})
  const response=await page.goto('/')
  expect(response?.status()).toBeLessThan(400)
  await expect(page.locator('#root')).toBeVisible()
  await expect.poll(()=>page.locator('#root').evaluate(root=>root.childElementCount)).toBeGreaterThan(0)
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2)).toBe(true)
  expect(errors).toEqual([])
 })
}
