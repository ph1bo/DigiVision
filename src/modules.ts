import type { ModuleItem, NodeType } from './types'

export interface ModuleCard extends ModuleItem {
  /** icon-assets.ts 中 module 分组下的插画资源名。 */
  art: 'collect' | 'condition' | 'display' | 'model' | 'plc'
}

export interface ModuleGroup {
  name: string
  items: ModuleCard[]
}

/**
 * 模块栏结构（与高保稿一致）：
 * 图像处理 → 图像采集、推理显示
 * 模型工具 → 模型推理
 * 逻辑工具 → 条件分支
 * 通信工具 → PLC写入
 */
export const moduleGroups: ModuleGroup[] = [
  {
    name: '图像处理',
    items: [
      { type: 'capture', group: '图像处理', label: '图像采集', description: '', accent: '#654DFA', art: 'collect' },
      { type: 'display', group: '图像处理', label: '推理显示', description: '', accent: '#654DFA', art: 'display' },
    ],
  },
  {
    name: '模型工具',
    items: [
      { type: 'model', group: '模型工具', label: '模型推理', description: '', accent: '#654DFA', art: 'model' },
    ],
  },
  {
    name: '逻辑工具',
    items: [
      { type: 'branch', group: '逻辑工具', label: '条件分支', description: '', accent: '#654DFA', art: 'condition' },
    ],
  },
  {
    name: '通信工具',
    items: [
      { type: 'plc', group: '通信工具', label: 'PLC写入', description: '', accent: '#654DFA', art: 'plc' },
    ],
  },
]

export const moduleCards: ModuleCard[] = moduleGroups.flatMap((group) => group.items)

export const moduleCatalog: ModuleItem[] = moduleCards

export function artFor(type: NodeType): ModuleCard['art'] | undefined {
  return moduleCards.find((item) => item.type === type)?.art
}
