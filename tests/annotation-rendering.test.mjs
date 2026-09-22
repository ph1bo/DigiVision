import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const root = process.env.DV_PROJECT_ROOT || process.cwd()
const require = createRequire(path.join(root, 'package.json'))
const ts = require('typescript')
const overlaySource = readFileSync(path.join(root, 'src/components/AnnotationOverlay.tsx'), 'utf8')
const overlayCompiled = ts.transpileModule(`${overlaySource}\nexport { layoutRegionLabels };`, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const overlayModule = { exports: {} }
vm.runInNewContext(overlayCompiled, { exports: overlayModule.exports, module: overlayModule, require })
const source = readFileSync(path.join(root, 'src/pages/HomePage.tsx'), 'utf8')
const compiled = ts.transpileModule(`${source}\nexport { ImageTile, runResultLogLevel };`, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const module = { exports: {} }
vm.runInNewContext(compiled, { exports: module.exports, module, structuredClone, crypto: globalThis.crypto, require: id => {
  if (id === '../api') return { api: {}, imageUrl: value => value, wsUrl: value => value }
  if (id === '../icon-assets') return { iconAssets: { flow: {} }, iconSizes: { flow: {} } }
  if (id === '../modules') return { moduleCatalog: [] }
  if (id === '../components/AnnotationOverlay') return overlayModule.exports
  if (id === '../ui-icons' || id === '../workflow' || id === '../components/Sidebar' || id === '../components/NodeConfigDialog') return {}
  return require(id)
}})

test('Text 使用图像窗口左上角为原点的显示像素坐标', () => {
  const markup = renderToStaticMarkup(React.createElement(module.exports.ImageTile, {
    className: 'test', image: '/frame.jpg',
    annotations: [{ kind: 'text', content: 'NG', x: 12, y: 34, color: '#F76965', fontSize: 24 }],
  }))
  assert.match(markup, /class="annotation-text-layer"/)
  assert.match(markup, /class="annotation-overlay-text"/)
  assert.match(markup, /left:12px;top:34px/)
  assert.doesNotMatch(markup, /left:1200%;top:3400%/)
  assert.match(markup, /color:#fff;background-color:#F76965/)
  assert.match(markup, /font-size:24px/)
  assert.match(markup, />NG<\/span>/)
})

test('Region 类别名称使用带背景的界面标签而不是 SVG 小字', () => {
  const markup = renderToStaticMarkup(React.createElement(module.exports.ImageTile, {
    className: 'test', image: '/frame.jpg',
    annotations: [{ kind: 'rect', x: 12, y: 34, width: 50, height: 40, color: '#27C346', lineWidth: 2, label: '螺丝钉' }],
  }))
  assert.match(markup, /class="annotation-region-label"/)
  assert.match(markup, /background-color:#27C346/)
  assert.match(markup, />螺丝钉<\/span>/)
})

test('相邻 Region 标签在界面坐标中自动错开', () => {
  const layout = overlayModule.exports.layoutRegionLabels
  assert.equal(typeof layout, 'function')
  const labels = layout([
    { kind: 'rect', x: 100, y: 100, label: '螺丝钉' },
    { kind: 'rect', x: 108, y: 106, label: '螺丝钉' },
    { kind: 'rect', x: 116, y: 112, label: '螺丝钉' },
  ], { left: 0, top: 0, width: 500, height: 500 }, 1000, 1000)
  assert.equal(labels.length, 3)
  for (let i = 0; i < labels.length; i += 1) {
    for (let j = i + 1; j < labels.length; j += 1) {
      const a = labels[i]
      const b = labels[j]
      const overlaps = a.left < b.left + b.width && a.left + a.width > b.left
        && a.top < b.top + b.height && a.top + a.height > b.top
      assert.equal(overlaps, false)
    }
  }
})

test('用户配置的 OK 或 NG 文本不会生成系统状态徽标', () => {
  for (const content of ['OK', 'NG']) {
    const markup = renderToStaticMarkup(React.createElement(module.exports.ImageTile, {
      className: 'test', status: content, image: '/frame.jpg',
      annotations: [{ kind: 'text', content, x: 0, y: 0, color: '#F76965', fontSize: 24 }],
    }))
    assert.match(markup, new RegExp(`>${content}<\\/span>`))
    assert.doesNotMatch(markup, /image-state/)
  }
})

test('执行成功记录 SUCCESS，只有流程失败记录 ERROR', () => {
  assert.equal(module.exports.runResultLogLevel('success'), 'success')
  assert.equal(module.exports.runResultLogLevel('ok'), 'success')
  assert.equal(module.exports.runResultLogLevel('ng'), 'success')
  assert.equal(module.exports.runResultLogLevel('failed'), 'error')
})
