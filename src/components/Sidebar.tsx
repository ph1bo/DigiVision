import { useState } from 'react'
import { iconAssets, iconSizes } from '../icon-assets'
import { moduleGroups } from '../modules'
import type { NodeType } from '../types'
import { ChevronUp, Search, X } from '../ui-icons'

export function ModuleSidebar({ onAdd }: { onAdd: (type: NodeType) => void }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(moduleGroups.map((group) => [group.name, true])),
  )
  const [query, setQuery] = useState('')
  const groups = moduleGroups.map(group => ({ ...group, items: group.items.filter(item => `${group.name}${item.label}`.includes(query.trim())) })).filter(group => group.items.length)
  const [selected, setSelected] = useState<string>('图像采集')

  return (
    <aside className="module-panel panel">
      <div className="panel-header compact">
        <span>模块栏</span>
        <div><button><X size={16} /></button></div>
      </div>
      <div className="module-search">
        <Search size={16} />
        <input placeholder="请搜索" value={query} onChange={event => setQuery(event.target.value)} />
      </div>
      <div className="module-scroll">
        {groups.map((group) => {
          const open = !!query || expanded[group.name]
          return (
            <section className="module-group" key={group.name}>
              <button
                className="module-group-title"
                onClick={() => setExpanded((current) => ({ ...current, [group.name]: !open }))}
              >
                {group.name}
                <ChevronUp size={16} style={{ transform: open ? 'none' : 'rotate(180deg)' }} />
              </button>
              {open && (
                <div className="module-grid">
                  {group.items.map((item) => (
                    <button
                      className={`module-card ${selected === item.label ? 'selected' : ''}`}
                      key={item.label}
                      draggable
                      onDragStart={(event) => {
                        event.dataTransfer.setData('application/x-digivision-node', item.type)
                        event.dataTransfer.setData('text/plain', item.type)
                        event.dataTransfer.effectAllowed = 'copy'
                      }}
                      onClick={() => setSelected(item.label)}
                      onDoubleClick={() => onAdd(item.type)}
                      title={`${group.name} · 拖到流程栏或双击添加`}
                    >
                      <span className="module-art">
                        <img src={iconAssets.module[item.art]} alt="" {...iconSizes.module} />
                      </span>
                      <b>{item.label}</b>
                    </button>
                  ))}
                </div>
              )}
            </section>
          )
        })}
      </div>
    </aside>
  )
}
