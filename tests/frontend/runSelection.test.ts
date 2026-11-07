import test from 'node:test'
import assert from 'node:assert/strict'
import {RunSelection} from '../../apps/web/src/runSelection.ts'
import {readJson, apiFetch} from '../../apps/web/src/api.ts'

test('late project A reply cannot replace selected B', () => {
 const gate=new RunSelection();const a=gate.begin('A',true);const b=gate.begin('B',true)
 assert.equal(gate.visible(b),true);assert.equal(gate.visible(a),false)
})
test('polling A never selects it after switching to B', () => {
 const gate=new RunSelection();gate.begin('A',true);gate.begin('B',true)
 assert.equal(gate.visible(gate.begin('A')),false);assert.equal(gate.id,'B')
})
test('stream updates invalidate older HTTP data for the same project', () => {
 const gate=new RunSelection();const request=gate.begin('A',true);const stream=gate.begin('A')
 assert.equal(gate.visible(stream),true);assert.equal(gate.latest(request),false)
})
test('new task, remount and reselection invalidate in-flight requests', () => {
 const gate=new RunSelection();const old=gate.begin('A',true);gate.clear();const next=gate.begin('A',true)
 assert.equal(gate.latest(old),false);assert.equal(gate.visible(old),false);assert.equal(gate.visible(next),true)
})
test('two explicit selections of the same project keep only the newer reply', () => {
 const gate=new RunSelection();const first=gate.begin('A',true);const second=gate.begin('A',true)
 assert.equal(gate.visible(first),false);assert.equal(gate.visible(second),true)
})
test('shared JSON reader handles string, structured and invalid errors',async()=>{
 for(const detail of ['safe message',{message:'safe message'}]) await assert.rejects(readJson(new Response(JSON.stringify({detail}),{status:409})),/safe message/)
 await assert.rejects(readJson(new Response('not json',{status:503})),/未完成/)
 await assert.rejects(readJson(new Response('not json')),/无法读取/)
 assert.deepEqual(await readJson(new Response('{"ok":true}')),{ok:true})
})
test('transport preserves cancellation and never retries failed mutations',async()=>{
 const original=globalThis.fetch;let calls=0
 try {
  globalThis.fetch=async()=>{calls++;throw new TypeError('private transport details')}
  await assert.rejects(apiFetch('http://127.0.0.1:8000',{method:'POST'}),/不要重复提交/);assert.equal(calls,1)
  globalThis.fetch=async()=>{throw new DOMException('aborted','AbortError')}
  await assert.rejects(apiFetch('http://127.0.0.1:8000'),{name:'AbortError'})
 }finally{globalThis.fetch=original}
})
