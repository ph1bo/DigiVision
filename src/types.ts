export type NodeType =
  | 'start'
  | 'capture'
  | 'quickCapture'
  | 'preprocess'
  | 'calibration'
  | 'affine'
  | 'model'
  | 'branch'
  | 'display'
  | 'storage'
  | 'alarm'
  | 'plc'
  | 'end'

export interface VariableDef {
  variable: string
  displayName: string
  type: string
  value?: unknown
}

export interface WorkflowNodeData extends Record<string, unknown> {
  label: string
  nodeType: NodeType
  branch?: { blockId: string; role: 'if' | 'elseIf' | 'else' | 'end' }
  params: Record<string, any>
  outputs?: VariableDef[]
  status?: 'idle' | 'running' | 'success' | 'failed' | 'skipped'
  durationMs?: number
  ports?: {
    builtIn?: string | null
    dynamic?: Array<{ id: string; label?: string; targetId: string }>
  }
}

export interface WorkflowNode {
  id: string
  type: string
  position: { x: number; y: number }
  data: WorkflowNodeData
}

export interface FlowSummary {
  id: string
  name: string
  description: string
  updated_at: string
}

export interface FlowDetail extends FlowSummary {
  nodes: WorkflowNode[]
}

export interface Camera {
  id: string
  name: string
  vendor: string
  model: string
  serial: string
  interface: string
  ip_address: string
  remark: string
  auto_connect: boolean
  connected: boolean
  created_at: string
  updated_at: string
}

export interface CameraDevice {
  serial: string
  model: string
  interface: string
  ip_address: string
  vendor: string
  added?: boolean
}

export interface ModelPackage {
  id: string
  model_id: string
  name: string
  version: string
  description: string
  manifest: {
    tasks?: Array<{
      key: string
      type: string
      name: string
      labels: Array<{ label: string; name: string }>
    }>
    params?: Array<Record<string, any>>
  }
  package_path: string
  enabled: boolean
  serviceStatus: 'stopped' | 'running' | 'error'
  lastError: string
  imported_at: string
}

export interface RunEvent {
  type: string
  runId?: string
  flowId?: string
  nodeId?: string
  nodeType?: string
  nodeLabel?: string
  status?: string
  durationMs?: number
  result?: string
  level?: string
  content?: string
  time?: string
  imageUrl?: string
  windowId?: number
  annotations?: Array<Record<string, any>>
  outputs?: Record<string, unknown>
  [key: string]: unknown
}

export interface LogItem {
  id: string
  time: string
  level: string
  module: string
  content: string
}

export interface ModuleItem {
  type: NodeType
  group: string
  label: string
  description: string
  accent?: string
}
