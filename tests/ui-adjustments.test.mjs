import { readFileSync } from 'node:fs'
import test from 'node:test'
import assert from 'node:assert/strict'

const splashCss = readFileSync(new URL('../src/splash.css', import.meta.url), 'utf8')
const appCss = readFileSync(new URL('../src/App.css', import.meta.url), 'utf8')
const indexCss = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
const tauriConfig = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'))
const homePage = readFileSync(new URL('../src/pages/HomePage.tsx', import.meta.url), 'utf8')

test('启动页和主视图使用轻微圆角并裁剪内容', () => {
  assert.match(splashCss, /\.splash-shell\s*\{[\s\S]*?border-radius:\s*12px;[\s\S]*?overflow:\s*hidden;/)
  assert.match(appCss, /\.app-shell\s*\{[\s\S]*?border-radius:\s*12px;[\s\S]*?overflow:\s*hidden;/)
})

test('透明窗口避免圆角外露出方形背景', () => {
  assert.equal(tauriConfig.app.windows.find(item => item.label === 'main').transparent, true)
  assert.equal(tauriConfig.app.windows.find(item => item.label === 'splashscreen').transparent, true)
  assert.match(indexCss, /:root\s*\{[\s\S]*?background:\s*transparent;/)
})

test('启动进度遮罩高度覆盖背景自带进度区域', () => {
  assert.match(splashCss, /\.splash-progress-area\s*\{[\s\S]*?min-height:\s*96px;/)
  assert.match(splashCss, /\.splash-progress-area\s*\{[\s\S]*?background:\s*#18191C;/)
})

test('节点配置保存提示不暴露存储实现', () => {
  assert.match(homePage, /节点配置已保存['`]/)
  assert.doesNotMatch(homePage, /节点配置已保存到 SQLite/)
})

test('模块节点之间不绘制灰白分隔线', () => {
  assert.doesNotMatch(appCss, /\.module-panel \.module-card:nth-child\(2n \+ 1\)\s*\{[^}]*border-right/)
  assert.doesNotMatch(appCss, /\.module-panel \.module-card:nth-child\(n \+ 3\)\s*\{[^}]*border-top/)
})
