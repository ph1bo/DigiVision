import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import chevronDownIcon from '../assets/mastergo/chevron-down.svg'

export interface DesignSelectOption {
  value: string
  label: ReactNode
  disabled?: boolean
}

interface DesignSelectProps {
  value: string
  options: DesignSelectOption[]
  onChange: (value: string) => void
  ariaLabel?: string
  disabled?: boolean
  className?: string
}

interface MenuPosition {
  left: number
  top: number
  width: number
  maxHeight: number
  openAbove: boolean
}

export function DesignSelect({ value, options, onChange, ariaLabel, disabled = false, className = '' }: DesignSelectProps) {
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [focusedIndex, setFocusedIndex] = useState(() => Math.max(0, options.findIndex(option => option.value === value)))
  const [position, setPosition] = useState<MenuPosition | null>(null)
  const selected = options.find(option => option.value === value)
  const selectedIndex = options.findIndex(option => option.value === value)

  const updatePosition = () => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const gap = 4
    const roomBelow = window.innerHeight - rect.bottom - gap
    const roomAbove = rect.top - gap
    const openAbove = roomBelow < Math.min(240, roomAbove) && roomAbove > roomBelow
    const maxHeight = Math.max(96, Math.min(240, openAbove ? roomAbove : roomBelow))
    setPosition({
      left: rect.left,
      top: openAbove ? rect.top - gap : rect.bottom + gap,
      width: rect.width,
      maxHeight,
      openAbove,
    })
  }

  useLayoutEffect(() => {
    if (!open) return
    updatePosition()
  }, [open])

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node
      if (!triggerRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false)
    }
    const reposition = () => updatePosition()
    document.addEventListener('mousedown', closeOnOutsideClick)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open])

  useEffect(() => {
    if (!open || !menuRef.current) return
    const option = menuRef.current.querySelector<HTMLElement>(`[data-index="${focusedIndex}"]`)
    option?.scrollIntoView({ block: 'nearest' })
  }, [focusedIndex, open])

  useEffect(() => {
    if (!open) return
    setFocusedIndex(selectedIndex >= 0 ? selectedIndex : Math.max(0, options.findIndex(option => !option.disabled)))
  }, [open, selectedIndex, options])

  const choose = (option: DesignSelectOption) => {
    if (option.disabled) return
    onChange(option.value)
    setOpen(false)
    triggerRef.current?.focus()
  }

  const toggleOpen = () => {
    if (disabled) return
    setOpen(current => !current)
  }

  const moveFocus = (direction: 1 | -1) => {
    let next = focusedIndex
    for (let count = 0; count < options.length; count += 1) {
      next = (next + direction + options.length) % options.length
      if (!options[next]?.disabled) {
        setFocusedIndex(next)
        break
      }
    }
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) setOpen(true)
      else moveFocus(event.key === 'ArrowDown' ? 1 : -1)
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (!open) setOpen(true)
      else if (options[focusedIndex]) choose(options[focusedIndex])
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
    } else if (event.key === 'Home' && open) {
      event.preventDefault()
      const first = options.findIndex(option => !option.disabled)
      if (first >= 0) setFocusedIndex(first)
    } else if (event.key === 'End' && open) {
      event.preventDefault()
      const last = options.findLastIndex(option => !option.disabled)
      if (last >= 0) setFocusedIndex(last)
    }
  }

  const menu = open && position ? createPortal(
    <div
      ref={menuRef}
      className={`design-select-menu ${position.openAbove ? 'open-above' : ''}`}
      role="listbox"
      aria-label={ariaLabel}
      style={{ left: position.left, width: position.width, maxHeight: position.maxHeight, ...(position.openAbove ? { bottom: window.innerHeight - position.top } : { top: position.top }) }}
    >
      {options.map((option, index) => (
        <button
          type="button"
          role="option"
          aria-selected={option.value === value}
          aria-disabled={option.disabled || undefined}
          className={`${option.value === value ? 'selected' : ''} ${index === focusedIndex ? 'focused' : ''}`}
          data-index={index}
          key={`${option.value}-${index}`}
          disabled={option.disabled}
          onMouseDown={event => event.preventDefault()}
          onClick={() => choose(option)}
        >
          <span>{option.label}</span>
          {option.value === value && <i aria-hidden="true" />}
        </button>
      ))}
    </div>,
    document.body,
  ) : null

  return <>
    <button
      ref={triggerRef}
      type="button"
      className={`design-select-trigger ${className} ${open ? 'is-open' : ''}`}
      role="combobox"
      aria-label={ariaLabel}
      aria-expanded={open}
      aria-haspopup="listbox"
      disabled={disabled}
      onClick={toggleOpen}
      onKeyDown={handleKeyDown}
    >
      <span>{selected?.label || '请选择'}</span>
      <img src={chevronDownIcon} alt="" aria-hidden="true" />
    </button>
    {menu}
  </>
}
