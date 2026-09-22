import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { url: 'http://localhost/' })
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Event', 'MouseEvent', 'KeyboardEvent']) globalThis[key] = dom.window[key]
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const require = createRequire(import.meta.url)
const React = require('react')
const { createRoot } = require('react-dom/client')
const ts = require('typescript')
const source = readFileSync(new URL('../src/components/NodeConfigDialog.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source + '\nexport { ValueField, collectVariables, variableValue };', { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const module = { exports: {} }
vm.runInNewContext(compiled, { exports: module.exports, module, window, document, HTMLElement, Element, structuredClone, crypto: globalThis.crypto, require: id => {
  if (id === '../api') return { api: {} }
  if (id === '../icon-assets') return { iconAssets: { flow: {} }, iconSizes: { flow: {} } }
  if (id === './AnnotationOverlay') return { AnnotationOverlay: () => null }
  if (id === './DesignSelect') return { DesignSelect: ({ value, options, onChange, 'ariaLabel': ariaLabel }) => React.createElement('select', { value, 'aria-label': ariaLabel, onChange: event => onChange(event.target.value) }, options.map(option => React.createElement('option', { key: option.value, value: option.value, disabled: option.disabled }, option.label))) }
  if (/\.(svg|css)$/.test(id)) return id
  return require(id)
}})
const { ValueField, collectVariables, variableValue } = module.exports
const variable = (id, kind = 'Image', nodeName = id) => ({ ref: id + '.value', kind, label: nodeName + ' · 输出', nodeId: id, nodeName, name: '输出' })
async function setup(value, variables, kind = 'Image') {
  const calls = []
  let outerEscapes = 0, submits = 0
  const root = createRoot(document.getElementById('root'))
  function Harness() {
    const [current, setCurrent] = React.useState(value)
    return React.createElement('div', { className: 'app-shell' }, React.createElement('form', { className: 'nc-dialog', onSubmit: e => { e.preventDefault(); submits++ }, onKeyDown: e => { if (e.key === 'Escape') outerEscapes++ } }, React.createElement(ValueField, { label: '输入', kind, value: current, variables, onChange: v => { calls.push(v); setCurrent(v) } })))
  }
  await React.act(async () => root.render(React.createElement(Harness)))
  return { calls, outerEscapes: () => outerEscapes, submits: () => submits, close: async () => { await React.act(async () => root.unmount()) } }
}
const button = text => [...document.querySelectorAll('.vr-dialog button')].find(b => b.textContent === text)
const click = async el => { assert.ok(el); await React.act(async () => el.click()) }
const open = async () => { const trigger = document.querySelector('[aria-haspopup="dialog"]'); trigger.focus(); await click(trigger); return trigger }

test('引用按节点 ID 分组，同名节点不合并；选中后保存才回填', async () => {
  const h = await setup({ type: 'ref', ref: 'a.value' }, [variable('a', 'Image', '同名采集'), variable('b', 'Image', '同名采集'), variable('wrong', 'Bool')])
  const trigger = await open()
  try {
    assert.equal(document.querySelector('.vr-dialog').closest('form'), null)
    assert.equal(document.querySelector('.nc-dialog').inert, true)
    assert.equal(document.querySelectorAll('.vr-sources button').length, 2)
    await click(document.querySelectorAll('.vr-sources button')[1])
    assert.equal(button('保存').disabled, true)
    await click(document.querySelector('.vr-row'))
    assert.equal(h.calls.length, 0)
    await click(button('保存'))
    assert.equal(h.calls.length, 1)
    assert.equal(h.calls[0].ref, 'b.value')
    assert.equal(document.querySelector('.vr-dialog'), null)
    assert.equal(document.activeElement, trigger)
    assert.notEqual(document.querySelector('.nc-dialog').inert, true)
    assert.equal(h.submits(), 0)
  } finally { await h.close() }
})

test('取消、Escape 保留原值，Escape 不关闭底层配置窗口', async () => {
  const h = await setup({ type: 'ref', ref: 'a.value' }, [variable('a'), variable('b')])
  try {
    await open()
    await click(document.querySelectorAll('.vr-sources button')[1])
    await click(document.querySelector('.vr-row'))
    await click(button('取消'))
    assert.equal(h.calls.length, 0)
    await open()
    await React.act(async () => document.querySelector('.vr-dialog').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    assert.equal(h.calls.length, 0)
    assert.equal(h.outerEscapes(), 0)
    assert.equal(document.querySelector('.vr-dialog'), null)
  } finally { await h.close() }
})

test('空变量列表无法保存；常量切换引用后取消保留常量', async () => {
  const h = await setup({ type: 'content', ref: false }, [], 'Bool')
  try {
    const select = document.querySelector('[aria-label="输入输入方式"]')
    await React.act(async () => { select.value = 'ref'; select.dispatchEvent(new Event('change', { bubbles: true })) })
    assert.match(document.querySelector('.vr-empty').textContent, /Bool/)
    assert.equal(button('保存').disabled, true)
    await click(button('取消'))
    assert.equal(h.calls.length, 0)
    assert.equal(select.value, 'content')
  } finally { await h.close() }
})

test('全部字段类型共用独立窗口，只显示匹配类型并隔离键盘焦点', async () => {
  for (const kind of ['Image', 'ImageArray', 'Region', 'Bool', 'Int', 'Float', 'Text']) {
    const h = await setup({ type: 'ref', ref: '' }, [variable('match', kind), variable('wrong', kind === 'Bool' ? 'Image' : 'Bool')], kind)
    try {
      await open()
      assert.equal(document.querySelectorAll('.vr-row').length, 1)
      const close = document.querySelector('[aria-label="关闭变量引用"]')
      close.focus()
      await React.act(async () => close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })))
      assert.equal(document.activeElement?.textContent, '取消')
      await click(document.querySelector('.vr-row'))
      await click(button('保存'))
      assert.equal(h.calls[0].ref, 'match.value')
    } finally { await h.close() }
  }
})

test('变量表格使用真实输出，保留零、False，缺失值不伪造', () => {
  const node = (id, nodeType, outputs = []) => ({ id, data: { label: id, nodeType, params: {}, outputs } })
  const a = node('a', 'capture', [{ variable: 'Image', displayName: 'Image', type: 'Image', value: '/real.png' }, { variable: 'zero', displayName: '零', type: 'Int', value: 0 }, { variable: 'ok', displayName: '通过', type: 'Bool', value: false }])
  const current = node('b', 'display')
  const rows = collectVariables(current, [a, current], [])
  assert.equal(rows.find(v => v.ref === 'a.Image').name, '图像')
  assert.equal(variableValue(rows.find(v => v.ref === 'a.zero')), '0')
  assert.equal(variableValue(rows.find(v => v.ref === 'a.ok')), 'False')
  assert.equal(variableValue(variable('empty')), '—')
})
