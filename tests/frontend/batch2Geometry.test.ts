import test from 'node:test'
import assert from 'node:assert/strict'
import {selectionRect} from '../../apps/web/src/visualGeometry.ts'
import {pairsToRecord} from '../../apps/web/src/keyValueModel.ts'
test('rectangle supports reverse drag and clamps to the selected viewport',()=>{
  assert.deepEqual(selectionRect({x:410,y:900},{x:10,y:20},390,844),{x:10,y:20,width:380,height:824})
  assert.equal(selectionRect({x:2,y:3},{x:2,y:12},1000,720),null)
})
test('form records preserve strings and reject duplicate keys',()=>{
  assert.deepEqual({...pairsToRecord([{key:' foo ',value:'false'},{key:'',value:''}])},{foo:'false'})
  assert.throws(()=>pairsToRecord([{key:'x',value:'1'},{key:'x',value:'2'}]),/不能重复/)
  assert.throws(()=>pairsToRecord([{key:'',value:'value'}]),/请填写/)
})
test('special record keys cannot mutate prototypes',()=>{
  const value=pairsToRecord([{key:'__proto__',value:'private'},{key:'constructor',value:'ok'}])
  assert.equal(Object.getPrototypeOf(value),null)
  assert.equal(value['__proto__'],'private')
})
