import { Construction } from '../ui-icons'

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <main className="page">
      <div className="placeholder panel"><Construction size={38} /><h1>{title}</h1><p>该模块不在本次 Demo 高保范围，已保留入口。实现成本优先，后续按同一节点契约扩展。</p></div>
    </main>
  )
}
