import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'

const root = process.env.DV_PROJECT_ROOT || process.cwd()
const require = createRequire(path.join(root, 'package.json'))
const ts = require('typescript')
const source = readFileSync(process.env.DV_NODE_CONFIG_SOURCE || path.join(root, 'src/components/NodeConfigDialog.tsx'), 'utf8')
const compiled = ts.transpileModule(`${source}\nexport { collectVariables, initialDraft, validateDraft };`, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const module = { exports: {} }
vm.runInNewContext(compiled, { exports: module.exports, module, structuredClone, crypto: globalThis.crypto, require: id => {
  if (id === '../api') return { api: {} }
  if (id === '../icon-assets') return { iconAssets: { flow: {} }, iconSizes: { flow: {} } }
  if (id === './AnnotationOverlay') return { AnnotationOverlay: () => null }
  if (/\.(svg|jpg|css)$/.test(id)) return ''
  return require(id)
}})
const { collectVariables, initialDraft, validateDraft } = module.exports
const node = (id, nodeType, params = {}, outputs = []) => ({ id, data: { label: id, nodeType, params, outputs } })
const model = (id, key) => ({ model_id: id, manifest: { tasks: [{ key: 'detect', name: '检测', labels: [{ label: key, name: key }] }] } })

test('空上游不产生不存在的采集或模型引用', () => {
  const current = node('current', 'display')
  assert.equal(collectVariables(current, [current], [model('unused', 'unused')]).length, 0)
})
test('每个模型节点只展开自己配置的模型，数量保持 Int 类型', () => {
  const nodes = [node('camera-a', 'capture'), node('infer-a', 'model', { modelId: 'a' }), node('infer-b', 'model', { modelId: 'b' }), node('view', 'display')]
  const rows = collectVariables(nodes[3], nodes, [model('a', 'scratch'), model('b', 'hole'), model('unused', 'unused')])
  assert.ok(rows.some(row => row.ref === 'camera-a.Image' && row.kind === 'Image'))
  assert.ok(rows.some(row => row.ref === 'infer-a.detect:scratch:count' && row.kind === 'Int'))
  assert.ok(rows.some(row => row.ref === 'infer-b.detect:hole:regions' && row.kind === 'Region'))
  assert.ok(!rows.some(row => row.ref.includes('unused') || row.ref === 'infer-b.detect:scratch:count'))
})
test('类型映射不会把数字或图像数组降成 Text/Image', () => {
  const outputs = [{ variable: 'score', displayName: '分数', type: 'Float' }, { variable: 'batch', displayName: '批图', type: 'ImageArray' }, { variable: 'note', displayName: '备注', type: 'Text' }]
  const nodes = [node('input', 'capture', {}, outputs), node('current', 'model')]
  const rows = collectVariables(nodes[1], nodes, [])
  assert.equal(rows.find(row => row.ref === 'input.score')?.kind, 'Float')
  assert.equal(rows.find(row => row.ref === 'input.batch')?.kind, 'ImageArray')
  assert.equal(rows.find(row => row.ref === 'input.note')?.kind, 'Text')
})
test('其他分支和后续节点不进入引用列表', () => {
  const branch = (id, role) => ({ ...node(id, 'branch'), data: { ...node(id, 'branch').data, branch: { blockId: 'block', role } } })
  const nodes = [node('before', 'capture'), branch('if', 'if'), node('sibling', 'capture'), branch('else', 'else'), node('current', 'display'), node('after', 'capture')]
  const refs = collectVariables(nodes[4], nodes, []).map(row => row.ref)
  assert.ok(refs.includes('before.Image'))
  assert.ok(!refs.includes('sibling.Image'))
  assert.ok(!refs.includes('after.Image'))
})

test('默认采集不填演示路径，显示列表过滤旧 null 且保留原草稿隔离', () => {
  const capture = initialDraft(node('capture', 'capture'), [])
  assert.equal(capture.file.path, '')
  const input = node('view', 'display', { shapes: [null, { id: 'saved' }], texts: [] })
  const display = initialDraft(input, [])
  assert.equal(display.shapes.length, 1)
  assert.equal(display.texts.length, 0)
  display.shapes[0].style.lineWidth = 10
  assert.equal(input.data.params.shapes[1].style, undefined)
})
test('相机旧数字转为值对象，并保留 false 与 0', () => {
  const draft = initialDraft(node('capture', 'capture', { camera: { gain: { enabled: true, value: 0 }, mode: 'freeRun' } }), [])
  assert.equal(draft.camera.triggerMode, 'continuous')
  assert.equal(draft.camera.gain.value.type, 'content')
  assert.equal(draft.camera.gain.value.ref, 0)
  assert.equal(draft.camera.mode, undefined)
})
test('Bool 常量可保存，Text 不接受 Int 引用，坐标接受 Int 引用', () => {
  assert.equal(validateDraft('branch', { conditions: [{ value: { type: 'content', ref: false } }] }, '条件', [], [], []), null)
  const vars = [{ ref: 'cap.Image', kind: 'Image' }, { ref: 'infer.count', kind: 'Int' }]
  const draft = initialDraft(node('view', 'display', { image: { type: 'ref', ref: 'cap.Image' }, texts: [{ content: { type: 'ref', ref: 'infer.count' }, x: { type: 'ref', ref: 'infer.count' } }] }), [])
  assert.match(validateDraft('display', draft, '显示', vars, [], []), /类型不匹配/)
  draft.texts[0].content = { type: 'content', ref: '数量' }
  assert.equal(validateDraft('display', draft, '显示', vars, [], []), null)
})
test('Region 显示模型标签时不要求标签文字', () => {
  const vars = [{ ref: 'cap.Image', kind: 'Image' }, { ref: 'infer.regions', kind: 'Region' }]
  const draft = initialDraft(node('view', 'display', {
    image: { type: 'ref', ref: 'cap.Image' },
    shapes: [{ content: { type: 'ref', ref: 'infer.regions' }, showLabel: true, labelText: { type: 'content', ref: '' } }],
    texts: [],
  }), [])
  assert.equal(validateDraft('display', draft, '显示', vars, [], []), null)
})
test('PLC 寄存器允许零，拒绝越界地址与非 Bool 线圈值', () => {
  const p = { host: '127.0.0.1', port: 502, unitId: 1, address: 0, operation: 'register', value: { type: 'content', ref: 0 } }
  assert.equal(validateDraft('plc', p, 'PLC', [], [], []), null)
  assert.ok(validateDraft('plc', { ...p, address: 65536 }, 'PLC', [], [], []))
  assert.ok(validateDraft('plc', { ...p, operation: 'coil', value: 1 }, 'PLC', [], [], []))
})

test('历史数据库模型 ID 规范为 model_id，变量仍属于原节点', () => {
  const packageA = { ...model('canonical', 'scratch'), id: 'db-123' }
  const legacy = node('infer', 'model', { modelId: 'db-123' })
  assert.equal(initialDraft(legacy, [packageA]).modelId, 'canonical')
  const current = node('view', 'display')
  assert.ok(collectVariables(current, [legacy, current], [packageA]).some(row => row.ref === 'infer.detect:scratch:count'))
})
test('PLC 原始值规范成值对象，Bool/Int 引用按操作类型校验', () => {
  const p = initialDraft(node('plc', 'plc', { host: '127.0.0.1', value: false }), [])
  assert.equal(p.value.type, 'content')
  assert.equal(p.value.ref, false)
  const vars = [{ ref: 'infer.ok', kind: 'Bool' }, { ref: 'infer.count', kind: 'Int' }]
  assert.equal(validateDraft('plc', { ...p, value: { type: 'ref', ref: 'infer.ok' } }, 'PLC', vars, [], []), null)
  assert.equal(validateDraft('plc', { ...p, operation: 'register', value: { type: 'ref', ref: 'infer.count' } }, 'PLC', vars, [], []), null)
  assert.ok(validateDraft('plc', { ...p, value: { type: 'ref', ref: 'infer.count' } }, 'PLC', vars, [], []))
})
