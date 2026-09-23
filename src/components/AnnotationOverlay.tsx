import { useEffect, useRef, useState, type CSSProperties } from 'react'

type Annotation = Record<string, any>
type Frame = { left: number; top: number; width: number; height: number }
type RegionLabelLayout = { index: number; left: number; top: number; width: number; height: number }

const LABEL_HEIGHT = 20

function estimateLabelWidth(value: unknown) {
  const text = String(value ?? '')
  const textWidth = Array.from(text).reduce((width, character) => width + ((character.codePointAt(0) ?? 0) <= 0x7f ? 7 : 14), 0)
  return Math.max(LABEL_HEIGHT, Math.ceil(textWidth + 10))
}

function layoutRegionLabels(annotations: Annotation[], frame: Frame, imageWidth: number, imageHeight: number): RegionLabelLayout[] {
  return annotations.flatMap((item, index) => {
    if (item.kind === 'text' || item.label == null || item.label === '') return []
    const width = Math.min(estimateLabelWidth(item.label), frame.width)
    const anchorX = Number(item.x ?? item.bbox?.[0] ?? 0) / Math.max(imageWidth, 1) * frame.width
    const anchorY = Number(item.y ?? item.bbox?.[1] ?? 0) / Math.max(imageHeight, 1) * frame.height
    const clamp = (value: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(value, maximum))
    return [{
      index,
      left: clamp(anchorX, 0, Math.max(0, frame.width - width)),
      top: clamp(anchorY - LABEL_HEIGHT, 0, Math.max(0, frame.height - LABEL_HEIGHT)),
      width,
      height: LABEL_HEIGHT,
    }]
  })
}

export function AnnotationOverlay({ width, height, annotations, className = '', ariaLabel }: {
  width: number
  height: number
  annotations: Annotation[]
  className?: string
  ariaLabel?: string
}) {
  const rootRef = useRef<HTMLDivElement>(null)
  const [frame, setFrame] = useState<Frame | null>(null)

  useEffect(() => {
    const root = rootRef.current
    if (!root || width <= 0 || height <= 0) return
    const measure = () => {
      const containerWidth = root.clientWidth
      const containerHeight = root.clientHeight
      if (containerWidth <= 0 || containerHeight <= 0) return
      const scale = Math.min(containerWidth / width, containerHeight / height)
      const next = {
        left: (containerWidth - width * scale) / 2,
        top: (containerHeight - height * scale) / 2,
        width: width * scale,
        height: height * scale,
      }
      setFrame(current => current && Object.keys(next).every(key => Math.abs(current[key as keyof Frame] - next[key as keyof Frame]) < 0.1) ? current : next)
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(root)
    window.addEventListener('resize', measure)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [width, height])

  const layerStyle: CSSProperties = frame
    ? { left: frame.left, top: frame.top, width: frame.width, height: frame.height }
    : { inset: 0 }
  const xPosition = (value: unknown) => `${Number(value || 0) / Math.max(width, 1) * 100}%`
  const yPosition = (value: unknown) => `${Number(value || 0) / Math.max(height, 1) * 100}%`
  const regionLabels = frame ? layoutRegionLabels(annotations, frame, width, height) : []
  const regionLabelByIndex = new Map(regionLabels.map(item => [item.index, item]))

  return <div ref={rootRef} className={`annotation-overlay-root ${className}`.trim()} aria-label={ariaLabel}>
    <svg className="annotation-vector-layer" style={layerStyle} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      {annotations.map((item, index) => {
        if (item.kind === 'text') return null
        if (item.kind === 'polygon') return <polygon key={index} points={(item.points || []).map((point: number[]) => point.join(',')).join(' ')} fill="none" stroke={item.color} strokeWidth={item.lineWidth || 1} />
        if (item.kind !== 'rect' && item.kind !== 'shape') return null
        const x = item.x ?? item.bbox?.[0] ?? 0
        const y = item.y ?? item.bbox?.[1] ?? 0
        const itemWidth = item.width ?? (item.bbox ? item.bbox[2] - x : 0)
        const itemHeight = item.height ?? (item.bbox ? item.bbox[3] - y : 0)
        return <rect key={index} x={x} y={y} width={itemWidth} height={itemHeight} fill="none" stroke={item.color} strokeWidth={item.lineWidth || 1} />
      })}
    </svg>
    <div className="annotation-label-layer" style={layerStyle}>
      {annotations.map((item, index) => {
        if (item.kind === 'text') return null
        if (item.label == null || item.label === '') return null
        const y = Number(item.y ?? item.bbox?.[1] ?? 0)
        const placeInside = !!frame && y * frame.height / Math.max(height, 1) < 22
        const layout = regionLabelByIndex.get(index)
        return <span key={`label-${index}`} className={`annotation-region-label${layout || placeInside ? ' inside' : ''}`} style={layout
          ? { left: `${layout.left}px`, top: `${layout.top}px`, backgroundColor: item.color }
          : { left: xPosition(item.x ?? item.bbox?.[0]), top: yPosition(y), backgroundColor: item.color }}>{String(item.label)}</span>
      })}
    </div>
    <div className="annotation-text-layer">
      {annotations.map((item, index) => item.kind === 'text' ? (
        <span key={`text-${index}`} className="annotation-overlay-text" style={{
          left: `${Number(item.x || 0)}px`,
          top: `${Number(item.y || 0)}px`,
          color: '#fff',
          backgroundColor: item.color,
          fontSize: `${item.fontSize || 32}px`,
        }}>{String(item.content ?? '')}</span>
      ) : null)}
    </div>
  </div>
}
