import assert from 'node:assert/strict'
import { test } from 'node:test'
import { buildStepRows, compileWorkflow, duplicateWorkflowNode, expandBranch, insertModule, moveWorkflowNode, normalizeWorkflow, removeWorkflowNode } from '../src/workflow.ts'
import { fixture, node } from './workflow-fixture.ts'

test('拖入生成如果和结束，双击结束仅添加一个否则，双击否则连续插入否则如果', () => {
  let nodes = insertModule([node('start', 'start'), node('done', 'end')], node('b', 'branch'))
  assert.deepEqual(nodes.slice(1).map(n => n.data.label), ['如果1', '结束'])
  const end = nodes[2].id
  nodes = expandBranch(nodes, end)
  nodes = expandBranch(nodes, end)
  const otherwise = nodes.find(n => n.data.branch?.role === 'else')!.id
  nodes = expandBranch(nodes, otherwise)
  nodes = expandBranch(nodes, otherwise)
  assert.deepEqual(nodes.slice(1).map(n => n.data.label), ['如果1', '否则如果1', '否则如果2', '否则1', '结束'])
  assert.ok(buildStepRows(nodes).every(row => row.level === 0))
})

test('分支体缩进，命中跳至自己的子节点并在块结束合流，未命中继续下个条件', () => {
  const nodes = fixture()
  const arms = nodes.filter(n => n.data.branch && n.data.branch.role !== 'end')
  const end = nodes.find(n => n.data.branch?.role === 'end')!
  arms.forEach((arm, index) => {
    if (index < 3) {
      assert.equal(arm.data.ports!.builtIn, arms[index + 1].id)
      assert.equal(arm.data.ports!.dynamic![0].targetId, `body-${index}`)
    } else assert.equal(arm.data.ports!.builtIn, 'body-3')
    assert.equal(nodes.find(n => n.id === `body-${index}`)!.data.ports!.builtIn, end.id)
  })
  assert.equal(end.data.ports!.builtIn, 'after')
  assert.equal(nodes.find(n => n.id === 'after')!.data.ports!.builtIn, null)
  assert.ok(buildStepRows(nodes).filter(row => row.node.id.startsWith('body-')).every(row => row.level === 1))
  assert.deepEqual(normalizeWorkflow(JSON.parse(JSON.stringify(nodes))), nodes)
})

test('嵌套分支的子节点和块结束保持正确层级及出口', () => {
  let nodes = fixture()
  nodes = insertModule(nodes, node('nested', 'branch'), 'if')
  nodes = insertModule(nodes, node('nested-body'), 'nested')
  const rows = buildStepRows(nodes)
  assert.equal(rows.find(r => r.key === 'nested')!.level, 1)
  assert.equal(rows.find(r => r.key === 'nested-body')!.level, 2)
  const end = nodes.find(n => n.data.branch?.blockId === 'nested' && n.data.branch.role === 'end')!
  assert.equal(rows.find(r => r.key === end.id)!.level, 1)
  assert.equal(end.data.ports!.builtIn, 'body-0')
})

test('复制整个分支块重建 ID 和内部变量引用，删除结束同时删除对应块', () => {
  let nodes = fixture()
  nodes.find(n => n.id === 'body-0')!.data.params.image = { type: 'ref', ref: 'body-1.Image' }
  const originalEnd = nodes.find(n => n.data.branch?.role === 'end')!.id
  nodes = duplicateWorkflowNode(nodes, 'if')
  assert.equal(new Set(nodes.map(n => n.id)).size, nodes.length)
  const copy = nodes.find(n => n.data.branch?.role === 'if' && n.id !== 'if')!
  const rows = buildStepRows(nodes)
  const copiedBody = nodes.slice(nodes.indexOf(copy)).find(n => n.data.label === 'body-0')!
  const copiedSource = nodes.slice(nodes.indexOf(copy)).find(n => n.data.label === 'body-1')!
  assert.equal(copiedBody.data.params.image.ref, `${copiedSource.id}.Image`)
  assert.equal(rows.find(r => r.key === copiedBody.id)!.level, 1)
  nodes = removeWorkflowNode(nodes, originalEnd)
  assert.ok(!nodes.some(n => n.id === 'if' || n.id.startsWith('body-')))
  assert.equal(nodes[0].data.ports!.builtIn, copy.id)
  compileWorkflow(nodes)
})

test('删除中间条件连同分支体；复制否则生成可配置条件，保持唯一否则', () => {
  let nodes = fixture()
  const firstElseIf = nodes.find(n => n.data.branch?.role === 'elseIf')!
  nodes = removeWorkflowNode(nodes, firstElseIf.id)
  assert.ok(!nodes.some(n => n.id === 'body-1'))
  const otherwise = nodes.find(n => n.data.branch?.role === 'else')!
  nodes = duplicateWorkflowNode(nodes, otherwise.id)
  assert.equal(nodes.filter(n => n.data.branch?.role === 'else').length, 1)
  assert.equal(nodes.filter(n => n.data.branch?.role === 'elseIf').length, 2)
  assert.equal(nodes.filter(n => n.data.label === 'body-3').length, 2)
})

test('旧图的分支路径和变量引用转换后保持不变且不注入业务结果', () => {
  const nodes = [node('start', 'start'), node('branch', 'branch'), node('ng'), node('ok'), node('done', 'end')]
  nodes[0].data.ports!.builtIn = 'branch'
  nodes[1].data.params = { conditions: [{ id: 'ng', value: { type: 'ref', ref: 'model.defect' } }] }
  nodes[1].data.ports = { builtIn: 'ok', dynamic: [{ id: 'ng', targetId: 'ng' }] }
  nodes[2].data.ports!.builtIn = 'done'
  nodes[3].data.ports!.builtIn = 'done'
  const converted = normalizeWorkflow(nodes)
  assert.deepEqual(converted.filter(n => n.data.branch).map(n => n.data.branch!.role), ['if', 'else', 'end'])
  assert.equal(converted.find(n => n.id === 'branch')!.data.params.conditions[0].value.ref, 'model.defect')
  assert.equal('resultOnMatch' in converted.find(n => n.id === 'branch')!.data.params, false)
  assert.deepEqual(converted.filter(n => n.id === 'ng' || n.id === 'ok').map(n => n.data.ports!.builtIn),
    Array(2).fill(converted.find(n => n.data.branch?.role === 'end')!.id))
})

test('条件收口使用结束节点样式，顶格与如果同级且没有额外流程终点', () => {
  const nodes = fixture()
  const normalized = normalizeWorkflow(nodes)
  assert.equal(normalized.filter(n => n.data.nodeType === 'end' && !n.data.branch).length, 0)
  assert.equal(normalized.filter(n => n.data.nodeType === 'end' && n.data.branch?.role === 'end').length, 1)
  const rows = buildStepRows(normalized)
  const branchEnd = rows.find(row => row.node.data.branch?.role === 'end')!
  const branchIf = rows.find(row => row.node.data.branch?.role === 'if')!
  assert.equal(branchEnd.level, branchIf.level)
  assert.equal(branchEnd.node.data.nodeType, 'end')
  assert.equal(branchEnd.label, '结束')
})

test('普通节点按落点插入到目标前后', () => {
  const nodes = [node('start', 'start'), node('a'), node('b'), node('c')]
  const before = moveWorkflowNode(nodes, 'c', 'a', 'before').filter(n => n.data.nodeType !== 'start').map(n => n.id)
  const after = moveWorkflowNode(nodes, 'a', 'c', 'after').filter(n => n.data.nodeType !== 'start').map(n => n.id)
  assert.deepEqual(before, ['c', 'a', 'b'])
  assert.deepEqual(after, ['b', 'c', 'a'])
})

test('模块按落点插入到目标前后', () => {
  const nodes = [node('start', 'start'), node('a'), node('b')]
  const before = insertModule(nodes, node('c'), 'a', 'before').map(n => n.id)
  const after = insertModule(nodes, node('c'), 'b', 'after').map(n => n.id)
  assert.deepEqual(before, ['start', 'c', 'a', 'b'])
  assert.deepEqual(after, ['start', 'a', 'b', 'c'])
})

test('拖动任意条件分支行会整体移动分支块', () => {
  const nodes = fixture()
  const moved = moveWorkflowNode(nodes, 'if', 'after', 'after')
  assert.deepEqual(moved.map(n => n.data.label), ['start', 'after', '如果1', 'body-0', '否则如果1', 'body-1', '否则如果2', 'body-2', '否则1', 'body-3', '结束'])
  const rows = buildStepRows(moved)
  assert.equal(rows.find(row => row.node.id === 'if')!.level, 0)
  assert.equal(rows.find(row => row.node.id === 'body-0')!.level, 1)
})

test('普通节点按行落点拖入条件分支内部', () => {
  const nodes = fixture()
  const moved = moveWorkflowNode(nodes, 'after', 'if', 'after')
  assert.deepEqual(moved.map(n => n.data.label), ['start', '如果1', 'after', 'body-0', '否则如果1', 'body-1', '否则如果2', 'body-2', '否则1', 'body-3', '结束'])
  assert.equal(buildStepRows(moved).find(row => row.node.id === 'after')!.level, 1)
})
