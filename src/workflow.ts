import type { WorkflowNode } from './types.ts'

type Role = NonNullable<WorkflowNode['data']['branch']>['role']
const uid = () => crypto.randomUUID()

function marker(role: Role, blockId: string, label: string): WorkflowNode {
  const isEnd = role === 'end'
  return {
    id: role === 'if' ? blockId : uid(), type: 'workflow', position: { x: 0, y: 0 },
    data: {
      label: isEnd ? '结束' : label, nodeType: isEnd ? 'end' : 'branch', branch: { blockId, role }, status: 'idle', outputs: [],
      params: { conditions: role === 'if' || role === 'elseIf' ? [{ id: uid(), value: { type: 'content', ref: false } }] : [] },
      ports: { builtIn: null, dynamic: [] },
    },
  }
}

interface Block { start: number; end: number; arms: number[] }

function blocksOf(nodes: WorkflowNode[]): Map<string, Block> {
  const blocks = new Map<string, Block>()
  const stack: string[] = []
  nodes.forEach((node, index) => {
    const info = node.data.branch
    if (!info) return
    if (info.role === 'if') {
      if (blocks.has(info.blockId)) throw new Error('条件分支块重复')
      blocks.set(info.blockId, { start: index, end: -1, arms: [index] })
      stack.push(info.blockId)
      return
    }
    if (stack.at(-1) !== info.blockId) throw new Error('条件分支结构不完整')
    const block = blocks.get(info.blockId)!
    if (info.role === 'end') {
      block.end = index
      stack.pop()
    } else {
      if (nodes[block.arms.at(-1)!].data.branch?.role === 'else') throw new Error('否则必须是最后一个分支')
      block.arms.push(index)
    }
  })
  if (stack.length) throw new Error('条件分支缺少结束')
  return blocks
}

/** 列表是编辑顺序；出口仍使用现有引擎协议，命中后直接在块结束处合流。 */
export function compileWorkflow(input: WorkflowNode[]): WorkflowNode[] {
  // 独立的结束节点不再进入运行图；带分支标记的结束节点是条件收口，必须保留。
  const nodes = structuredClone(input.filter(node => node.data.nodeType !== 'end' || !!node.data.branch))
  const blocks = blocksOf(nodes)
  const wire = (from: number, to: number, next: string | null) => {
    for (let i = to - 1; i >= from; i--) {
      const node = nodes[i]
      const info = node.data.branch
      if (info?.role === 'end') {
        const block = blocks.get(info.blockId)!
        node.data.ports = { builtIn: next, dynamic: [] }
        block.arms.forEach((armIndex, j) => {
          const arm = nodes[armIndex]
          const boundary = block.arms[j + 1] ?? block.end
          const body = wire(armIndex + 1, boundary, node.id)
          if (arm.data.branch?.role === 'else') {
            arm.data.ports = { builtIn: body, dynamic: [] }
          } else {
            arm.data.ports = {
              builtIn: nodes[boundary].id,
              dynamic: (arm.data.params.conditions || []).map((condition: { id: string }) => ({ id: condition.id, targetId: body || node.id })),
            }
          }
        })
        next = nodes[block.start].id
        i = block.start
      } else {
        if (info) throw new Error('条件分支结构不完整')
        node.data.ports = { builtIn: node.data.nodeType === 'end' ? null : next, dynamic: [] }
        next = node.id
      }
    }
    return next
  }
  wire(0, nodes.length, null)
  return nodes
}

/** 将旧版端口图转换成显式分支行，保留节点 ID、条件、变量引用和合流点。 */
export function normalizeWorkflow(input: WorkflowNode[]): WorkflowNode[] {
  if (!input.some(n => n.data.nodeType === 'branch' && !n.data.branch)) return compileWorkflow(input)
  const nodes = structuredClone(input)
  const byId = new Map(nodes.map(n => [n.id, n]))
  const seen = new Set<string>()
  let ordinal = 0
  const distances = (start: string | null | undefined) => {
    const result = new Map<string, number>()
    const queue: [string, number][] = start ? [[start, 0]] : []
    for (let i = 0; i < queue.length; i++) {
      const [id, distance] = queue[i]
      if (result.has(id)) continue
      result.set(id, distance)
      const node = byId.get(id)
      if (!node || node.data.nodeType === 'end') continue
      const ports = node.data.ports
      for (const target of [ports?.builtIn, ...(ports?.dynamic || []).map(p => p.targetId)]) {
        if (target) queue.push([target, distance + 1])
      }
    }
    return result
  }
  const walk = (start: string | null | undefined, stop: string | null = null): WorkflowNode[] => {
    const result: WorkflowNode[] = []
    let current = start
    while (current && current !== stop) {
      if (seen.has(current)) throw new Error('旧流程存在循环或交叉分支，无法转换为分支块')
      const node = byId.get(current)
      if (!node) throw new Error('流程引用的节点不存在')
      seen.add(current)
      if (node.data.nodeType !== 'branch') {
        result.push(node)
        current = node.data.nodeType === 'end' ? null : node.data.ports?.builtIn
        continue
      }
      const conditions = node.data.params.conditions || []
      const ports = node.data.ports || {}
      const targets = conditions.map((c: { id: string }) => ports.dynamic?.find(p => p.id === c.id)?.targetId || ports.builtIn)
      targets.push(ports.builtIn)
      const paths: Map<string, number>[] = targets.map(distances)
      const join = [...(paths[0]?.keys() || [])]
        .filter(id => paths.every(path => path.has(id)))
        .sort((a, b) => paths.reduce((sum, path) => sum + path.get(a)! - path.get(b)!, 0))[0] || stop
      const number = ++ordinal
  const end = marker('end', node.id, '结束')
      const effectiveConditions = conditions.length ? conditions : [{ id: uid(), value: { type: 'content', ref: false } }]
      effectiveConditions.forEach((condition: Record<string, unknown>, index: number) => {
        const arm = index === 0 ? node : marker('elseIf', node.id, `否则如果${index}`)
        arm.data.branch = { blockId: node.id, role: index === 0 ? 'if' : 'elseIf' }
        arm.data.params = { ...node.data.params, conditions: [condition] }
        if (index === 0 && !arm.data.label.startsWith('如果')) arm.data.label = `如果${number}`
        result.push(arm, ...walk(conditions.length ? targets[index] : join, join))
      })
      if (ports.builtIn && ports.builtIn !== join) {
        result.push(marker('else', node.id, `否则${number}`), ...walk(ports.builtIn, join))
      }
      result.push(end)
      current = join
    }
    return result
  }
  const start = nodes.find(n => n.data.nodeType === 'start')
  const result = walk(start?.id || nodes[0]?.id)
  // 不静默丢弃历史上追加在流程结束后的节点，移到最终结束之前。
  const remaining = nodes.filter(n => !seen.has(n.id))
  if (remaining.some(n => n.data.nodeType === 'branch')) throw new Error('旧流程包含未连接的条件分支')
  const terminal = result.findLastIndex(n => n.data.nodeType === 'end')
  result.splice(terminal < 0 ? result.length : terminal, 0, ...remaining)
  return compileWorkflow(result)
}

export interface StepRow {
  key: string
  index: number
  level: number
  label: string
  node: WorkflowNode
  ancestors: string[]
  hasChildren: boolean
}

export function buildStepRows(nodes: WorkflowNode[]): StepRow[] {
  const rows: StepRow[] = []
  const stack: string[] = []
  const visible = nodes.filter(node => node.data.nodeType !== 'start' && (node.data.nodeType !== 'end' || !!node.data.branch))
  visible.forEach((node, index) => {
    const role = node.data.branch?.role
    if (role && role !== 'if') stack.pop()
    const next = visible[index + 1]
    rows.push({ key: node.id, index: rows.length + 1, level: stack.length, label: node.data.label, node,
      ancestors: [...stack], hasChildren: !!role && role !== 'end' && !!next && next.data.branch?.blockId !== node.data.branch?.blockId })
    if (role && role !== 'end') stack.push(node.id)
  })
  return rows
}

export function insertModule(nodes: WorkflowNode[], node: WorkflowNode, targetId?: string, place: 'before' | 'after' = 'after'): WorkflowNode[] {
  const next = [...nodes]
  const targetIndex = targetId ? next.findIndex(n => n.id === targetId) : -1
  const index = targetIndex < 0 ? next.length : place === 'before' ? targetIndex : targetIndex + 1
  let inserted = [node]
  if (node.data.nodeType === 'branch') {
    let number = 1
    while (nodes.some(n => n.data.label === `如果${number}` || n.data.label === `结束${number}`)) number++
    inserted = [marker('if', node.id, `如果${number}`), marker('end', node.id, '结束')]
  }
  next.splice(index, 0, ...inserted)
  return compileWorkflow(next)
}

export function expandBranch(nodes: WorkflowNode[], nodeId: string): WorkflowNode[] {
  const index = nodes.findIndex(n => n.id === nodeId)
  const info = nodes[index]?.data.branch
  if (!info || !['end', 'else'].includes(info.role)) return nodes
  const block = blocksOf(nodes).get(info.blockId)!
  const arms = block.arms.map(i => nodes[i])
  if (info.role === 'end' && arms.some(n => n.data.branch?.role === 'else')) return nodes
  const role = info.role === 'end' ? 'else' : 'elseIf'
  let number = 1
  const prefix = role === 'else' ? '否则' : '否则如果'
  while (arms.some(n => n.data.label === `${prefix}${number}`)) number++
  const next = [...nodes]
  next.splice(index, 0, marker(role, info.blockId, `${prefix}${number}`))
  return compileWorkflow(next)
}

function selectionRange(nodes: WorkflowNode[], id: string): [number, number] {
  const index = nodes.findIndex(n => n.id === id)
  const info = nodes[index]?.data.branch
  if (!info) return [index, index + 1]
  const block = blocksOf(nodes).get(info.blockId)!
  if (info.role === 'if' || info.role === 'end') return [block.start, block.end + 1]
  return [index, block.arms.find(i => i > index) ?? block.end]
}

export function removeWorkflowNode(nodes: WorkflowNode[], id: string): WorkflowNode[] {
  const [from, to] = selectionRange(nodes, id)
  if (from < 0 || nodes[from].data.nodeType === 'start') return nodes
  return compileWorkflow([...nodes.slice(0, from), ...nodes.slice(to)])
}

export function moveWorkflowNode(nodes: WorkflowNode[], sourceId: string, targetId: string | null, place: 'before' | 'after'): WorkflowNode[] {
  const sourceIndex = nodes.findIndex(node => node.id === sourceId)
  if (sourceIndex < 0 || nodes[sourceIndex].data.nodeType === 'start') return nodes
  const blocks = blocksOf(nodes)
  const rangeOf = (index: number): [number, number] => {
    const info = nodes[index].data.branch
    if (!info) return [index, index + 1]
    const block = blocks.get(info.blockId)!
    return [block.start, block.end + 1]
  }
  const sourceRange = rangeOf(sourceIndex)
  let insertIndex = nodes.length
  if (targetId) {
    const targetIndex = nodes.findIndex(node => node.id === targetId)
    if (targetIndex < 0) return nodes
    if (targetIndex >= sourceRange[0] && targetIndex < sourceRange[1]) return nodes
    // 条件块拖动时以整个目标块为落点；普通节点仍按当前行精确插入。
    const targetRange = nodes[sourceIndex].data.branch && nodes[targetIndex].data.branch
      ? rangeOf(targetIndex)
      : [targetIndex, targetIndex + 1]
    insertIndex = place === 'before' ? targetRange[0] : targetRange[1]
  }
  const moving = nodes.slice(sourceRange[0], sourceRange[1])
  const remaining = [...nodes.slice(0, sourceRange[0]), ...nodes.slice(sourceRange[1])]
  if (sourceRange[0] < insertIndex) insertIndex -= sourceRange[1] - sourceRange[0]
  insertIndex = Math.max(0, Math.min(insertIndex, remaining.length))
  return compileWorkflow([...remaining.slice(0, insertIndex), ...moving, ...remaining.slice(insertIndex)])
}

export function duplicateWorkflowNode(nodes: WorkflowNode[], id: string): WorkflowNode[] {
  const [from, to] = selectionRange(nodes, id)
  if (from < 0 || ['start', 'end'].includes(nodes[from].data.nodeType)) return nodes
  const copies = structuredClone(nodes.slice(from, to))
  const ids = new Map(copies.map(n => [n.id, uid()]))
  const rewriteRefs = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(rewriteRefs)
    if (value && typeof value === 'object') {
      const record = value as Record<string, unknown>
      const result = Object.fromEntries(Object.entries(record).map(([k, v]) => [k, rewriteRefs(v)]))
      if (record.type === 'ref' && typeof record.ref === 'string') {
        const dot = record.ref.indexOf('.')
        const source = record.ref.slice(0, dot)
        if (dot > 0 && ids.has(source)) result.ref = ids.get(source)! + record.ref.slice(dot)
      }
      return result
    }
    return value
  }
  copies.forEach(node => {
    node.id = ids.get(node.id)!
    node.data.status = 'idle'
    node.data.outputs = []
    delete node.data.durationMs
    node.data.params = rewriteRefs(node.data.params) as WorkflowNode['data']['params']
    if (node.data.branch) node.data.branch.blockId = ids.get(node.data.branch.blockId) || node.data.branch.blockId
  })
  const first = copies[0]
  if (first.data.branch?.role === 'if') {
    let number = 1
    while (nodes.some(n => n.data.label === `如果${number}`)) number++
    first.data.label = `如果${number}`
    copies.at(-1)!.data.label = '结束'
  } else if (first.data.branch) {
    first.data.branch.role = 'elseIf'
    let number = 1
    while (nodes.some(n => n.data.branch?.blockId === first.data.branch?.blockId && n.data.label === `否则如果${number}`)) number++
    first.data.label = `否则如果${number}`
    if (!first.data.params.conditions?.length) first.data.params.conditions = marker('elseIf', first.data.branch.blockId, '').data.params.conditions
  }
  const insertAt = nodes[from].data.branch?.role === 'else' ? from : to
  return compileWorkflow([...nodes.slice(0, insertAt), ...copies, ...nodes.slice(insertAt)])
}
