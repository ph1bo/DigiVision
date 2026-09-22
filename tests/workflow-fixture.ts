import { expandBranch, insertModule } from '../src/workflow.ts'
import type { NodeType, WorkflowNode } from '../src/types.ts'

export function node(id: string, nodeType: NodeType = 'plc'): WorkflowNode {
  return { id, type: 'workflow', position: { x: 0, y: 0 }, data: { label: id, nodeType, params: {}, ports: { builtIn: null, dynamic: [] } } }
}

export function fixture() {
  let nodes = insertModule([node('start', 'start'), node('done', 'end')], node('if', 'branch'))
  const end = nodes.find(n => n.data.branch?.role === 'end')!
  nodes = expandBranch(nodes, end.id)
  const otherwise = nodes.find(n => n.data.branch?.role === 'else')!
  nodes = expandBranch(nodes, otherwise.id)
  nodes = expandBranch(nodes, otherwise.id)
  const arms = nodes.filter(n => ['if', 'elseIf', 'else'].includes(n.data.branch?.role || ''))
  arms.forEach((arm, index) => { nodes = insertModule(nodes, node(`body-${index}`), arm.id) })
  // 空白区域添加的节点必须位于分支块之后、流程结束之前。
  nodes = insertModule(nodes, node('after'))
  return nodes
}

if (process.argv.includes('--json')) console.log(JSON.stringify(fixture()))
