// 运行：DV_PLAYWRIGHT_MODULE=/绝对路径/playwright/index.mjs node tests/node-config.browser.mjs
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
const root = process.env.DV_PROJECT_ROOT || process.cwd()
const require = createRequire(path.join(root, 'package.json'))
const { createServer } = await import(pathToFileURL(require.resolve('vite')).href)
const { chromium } = await import(process.env.DV_PLAYWRIGHT_MODULE || 'playwright')
const folder = await mkdtemp(path.join(tmpdir(), 'dv-node-config-browser-'))
const component = path.join(root, 'src/components/NodeConfigDialog.tsx')
await writeFile(path.join(folder, 'index.html'), '<div id="root"></div><script type="module" src="/entry.tsx"></script>')
await writeFile(path.join(folder, 'entry.tsx'), `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { NodeConfigDialog } from ${JSON.stringify('/@fs' + component)};
import ${JSON.stringify('/@fs' + root + '/src/App.css')};
const w = window as any;
function Harness() {
  const [config, configure] = useState<any>(null);
  w.configure = (value: any) => { w.calls = []; configure(value) };
  if (!config) return <div>ready</div>;
  return <div style={{ position: 'absolute', top: 96, left: 0, width: 1920, height: 984 }}>
    <NodeConfigDialog {...config} preview={config.preview || null} models={config.models || []} cameras={config.cameras || []}
      onClose={() => { w.calls.push({ type: 'close' }); configure(null) }}
      onPatch={(params) => w.calls.push({ type: 'patch', params })}
      onLabel={(label) => w.calls.push({ type: 'label', label })}
      onSave={(params, label) => { w.calls.push({ type: 'save', params, label }); return new Promise((resolve, reject) => { w.resolveSave = resolve; w.rejectSave = reject }) }}
      onDebug={(params) => { w.calls.push({ type: 'debug', params }); return new Promise((resolve, reject) => { w.resolveDebug = resolve; w.rejectDebug = reject }) }} />
  </div>
}
createRoot(document.getElementById('root')!).render(<Harness />);
`)
const server = await createServer({ configFile: false, root: folder, cacheDir: path.join(folder, '.vite'), resolve: { alias: { react: path.join(root, 'node_modules/react'), 'react-dom': path.join(root, 'node_modules/react-dom') } }, plugins: [{ name: 'staged-component', async load(id) { if (process.env.DV_NODE_CONFIG_SOURCE && id === component) return readFile(process.env.DV_NODE_CONFIG_SOURCE, 'utf8'); if (process.env.DV_NODE_CONFIG_CSS && id === path.join(root, 'src/components/node-config.css')) return readFile(process.env.DV_NODE_CONFIG_CSS, 'utf8') } }], server: { host: '127.0.0.1', port: 0, fs: { allow: [root, folder] } } })
let browser
try {
  await server.listen()
  browser = await chromium.launch({ headless: true, ...(process.env.DV_CHROMIUM_PATH ? { executablePath: process.env.DV_CHROMIUM_PATH } : {}) })
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0])
  await page.getByText('ready', { exact: true }).waitFor()
  const makeNode = (id, nodeType, params = {}) => ({ id, type: 'workflow', position: { x: 0, y: 0 }, data: { label: id, nodeType, params } })
  const capture = makeNode('采集', 'capture')
  const configure = async (current, before = [], extra = {}) => { await page.evaluate(config => window.configure(config), { node: current, nodes: [...before, current], ...extra }); await page.getByRole('dialog').waitFor() }
  const calls = () => page.evaluate(() => window.calls)
  const choose = async label => { if (!await page.getByRole('dialog', { name: '变量引用', exact: true }).isVisible()) await page.getByRole('button', { name: `选择${label}引用`, exact: true }).click(); await page.locator('.vr-row').first().click(); await page.getByRole('dialog', { name: '变量引用', exact: true }).getByRole('button', { name: '保存', exact: true }).click() }

  await configure(capture)
  assert.equal(await page.getByRole('img').count(), 0) // 装饰图标为空 alt，真实预览不存在
  const bounds = await page.getByRole('dialog').boundingBox()
  assert.deepEqual(bounds, { x: 360, y: 100, width: 1200, height: 880 })
  await page.getByRole('button', { name: '保存', exact: true }).click()
  assert.match(await page.getByRole('alert').innerText(), /图片/)
  assert.equal((await calls()).length, 0)
  await page.getByRole('button', { name: '取消', exact: true }).click()
  console.log('PASS 空采集校验、无假预览、舞台居中')

  const display = makeNode('显示', 'display', { image: { type: 'ref', ref: '采集.Image' }, shapes: [null], texts: [] })
  await configure(display, [capture])
  const displayPanel = page.locator('.nc-display-settings')
  assert.deepEqual(await displayPanel.boundingBox().then(({ width, height }) => ({ width, height })), { width: 448, height: 296 })
  await page.locator('.nc-display-tabs button').nth(1).click()
  await page.getByRole('button', { name: '新增文字', exact: true }).click()
  let itemDialog = page.getByRole('dialog', { name: 'Text设置', exact: true })
  assert.deepEqual(await itemDialog.boundingBox().then(({ width, height }) => ({ width, height })), { width: 480, height: 500 })
  await itemDialog.getByLabel('显示内容', { exact: true }).fill('第一条')
  await itemDialog.getByRole('button', { name: '保存', exact: true }).click()
  await page.getByRole('button', { name: '新增文字', exact: true }).click()
  itemDialog = page.getByRole('dialog', { name: 'Text设置', exact: true })
  await itemDialog.getByLabel('显示内容', { exact: true }).fill('第二条')
  await itemDialog.getByRole('button', { name: '保存', exact: true }).click()
  assert.equal(await page.locator('.nc-display-row').count(), 2)
  await page.locator('.nc-display-row').first().click()
  await page.getByRole('button', { name: '删除选中文字', exact: true }).click()
  assert.equal(await page.locator('.nc-display-row').count(), 1)
  await page.locator('.nc-display-row').dblclick()
  itemDialog = page.getByRole('dialog', { name: 'Text设置', exact: true })
  assert.equal(await itemDialog.getByLabel('显示内容', { exact: true }).inputValue(), '第二条')
  await itemDialog.getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('button', { name: '保存', exact: true }).click()
  let saved = (await calls()).find(call => call.type === 'save')
  assert.equal(saved.params.texts.length, 1)
  assert.equal(saved.params.texts[0].content.ref, '第二条')
  assert.equal(saved.params.shapes.length, 0)
  assert.equal(await page.getByRole('button', { name: '取消', exact: true }).isDisabled(), true)
  await page.evaluate(() => window.rejectSave(new Error('保存失败测试')))
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').innerText(), /保存失败测试/)
  assert.match(await page.locator('.nc-display-table').innerText(), /第二条/)
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await page.evaluate(() => window.resolveSave())
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  assert.equal((await calls()).filter(call => ['label', 'patch'].includes(call.type)).length, 0)
  console.log('PASS 多文字增删、无 null、保存错误重试、异步成功关闭、旧回调不重复')

  const model = { id: 'db-model', model_id: 'real-model', name: '测试模型', version: '1', serviceStatus: 'running', manifest: { tasks: [{ key: 'detect', name: '检测', labels: [{ label: 'defect', name: '缺陷' }] }], params: [{ name: 'threshold', label: '阈值', type: 'int', default: 80, min: 1, max: 255 }] } }
  const infer = makeNode('推理', 'model', { modelId: 'db-model', image: { type: 'ref', ref: '采集.Image' } })
  await configure(infer, [capture], { models: [model] })
  assert.equal(await page.getByLabel('模型选择').inputValue(), 'real-model')
  await page.getByRole('button', { name: '基础设置', exact: true }).click()
  await choose('输入图像')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  saved = (await calls()).find(call => call.type === 'save')
  assert.equal(saved.params.modelId, 'real-model')
  assert.equal(saved.params.threshold.ref, 80)
  await page.evaluate(() => window.resolveSave())
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  console.log('PASS 历史模型 ID、基础图像、manifest 参数')

  await configure(makeNode('显示图形', 'display', { image: { type: 'ref', ref: '采集.Image' } }), [capture, infer], { models: [model] })
  await page.getByRole('button', { name: '新增图形', exact: true }).click()
  itemDialog = page.getByRole('dialog', { name: 'Region设置', exact: true })
  assert.deepEqual(await itemDialog.boundingBox().then(({ width, height }) => ({ width, height })), { width: 480, height: 608 })
  await choose('显示内容')
  await choose('状态链接')
  await itemDialog.getByRole('switch', { name: '是否显示标签', exact: true }).click()
  await itemDialog.getByRole('button', { name: '保存', exact: true }).click()
  await page.getByRole('button', { name: '新增图形', exact: true }).click()
  itemDialog = page.getByRole('dialog', { name: 'Region设置', exact: true })
  await choose('显示内容')
  await itemDialog.getByRole('switch', { name: '是否显示标签', exact: true }).click()
  await itemDialog.getByRole('button', { name: '保存', exact: true }).click()
  await page.locator('.nc-display-row').nth(1).click()
  await page.getByRole('button', { name: '删除选中图形', exact: true }).click()
  await page.getByRole('button', { name: '保存', exact: true }).click()
  saved = (await calls()).find(call => call.type === 'save')
  assert.equal(saved.params.shapes.length, 1)
  assert.equal(saved.params.shapes[0].content.ref, '推理.detect:defect:regions')
  assert.equal(saved.params.shapes[0].status.ref, '推理.detect:defect:detected')
  await page.evaluate(() => window.resolveSave())
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  console.log('PASS 多图形增删和类型引用')

  await configure(makeNode('PLC', 'plc', { host: '127.0.0.1', value: { type: 'content', ref: false } }), [infer], { models: [model] })
  await page.getByLabel('写入值输入方式').selectOption('ref')
  await choose('写入值')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  saved = (await calls()).find(call => call.type === 'save')
  assert.equal(saved.params.value.ref, '推理.detect:defect:detected')
  await page.evaluate(() => window.rejectSave(new Error('继续编辑')))
  await page.getByLabel('操作类型').selectOption('register')
  await page.getByLabel('写入值输入方式').selectOption('ref')
  await choose('写入值')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  saved = (await calls()).filter(call => call.type === 'save').at(-1)
  assert.equal(saved.params.value.ref, '推理.detect:defect:count')
  await page.evaluate(() => window.resolveSave())
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  console.log('PASS PLC Bool/Int 引用与操作切换')

  await configure(makeNode('条件', 'branch'))
  await page.getByLabel('条件值', { exact: true }).selectOption('true')
  await page.getByRole('button', { name: '调试', exact: true }).click()
  assert.equal((await calls())[0].params.conditions[0].value.ref, true)
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await page.evaluate(() => window.resolveDebug({ imageUrl: '/late.png' }))
  assert.equal(await page.getByRole('dialog').count(), 0)
  await configure(makeNode('新条件', 'branch'))
  assert.equal(await page.getByLabel('条件值', { exact: true }).inputValue(), 'false')
  assert.equal(await page.getByRole('alert').count(), 0)
  console.log('PASS Bool 常量、取消后调试结果隔离、新节点草稿隔离')

  await configure(makeNode('标注预览', 'display', { image: { type: 'ref', ref: '采集.Image' } }), [capture])
  await page.getByRole('button', { name: '调试', exact: true }).click()
  await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = 800; canvas.height = 400
    window.resolveDebug({ imageUrl: canvas.toDataURL(), annotations: [
      { kind: 'rect', x: 80, y: 40, width: 160, height: 80, color: '#22C55E', lineWidth: 3, label: '区域标签' },
      { kind: 'text', x: 320, y: 200, content: '检测结果', color: '#EF4444', fontSize: 40 }
    ] })
  })
  const overlay = page.getByRole('img', { name: '调试标注' })
  await overlay.waitFor()
  assert.equal(await overlay.getAttribute('viewBox'), '0 0 800 400')
  assert.equal(await overlay.locator('rect').getAttribute('stroke-width'), '3')
  assert.equal(await overlay.locator('text').filter({ hasText: '检测结果' }).getAttribute('font-size'), '40')
  assert.equal(await overlay.locator('text').filter({ hasText: '区域标签' }).count(), 1)
  const imageBounds = await page.getByRole('img', { name: '节点真实图像预览' }).boundingBox()
  assert.deepEqual(await overlay.boundingBox(), imageBounds)
  await page.screenshot({ path: '/tmp/dv-node-config-annotations.png' })
  await page.getByRole('button', { name: '取消', exact: true }).click()
  console.log('PASS SVG 原图天然尺寸、同区域等比对齐、轮廓标签线宽及文字字号')

  const camera = { id: 'camera-connected', name: '工业相机', model: '真实设备', connected: true }
  await configure(makeNode('相机采集', 'capture', { source: 'camera' }), [], { cameras: [camera, { ...camera, id: 'offline', connected: false }] })
  assert.equal(await page.getByLabel('相机选择').locator('option').count(), 2)
  await page.getByLabel('相机选择').selectOption(camera.id)
  await page.getByLabel('采集模式').selectOption('hardware')
  await page.getByRole('switch', { name: '增益', exact: true }).click()
  await page.getByLabel('增益值', { exact: true }).fill('0')
  await page.screenshot({ path: '/tmp/dv-node-config-camera.png' })
  await page.getByRole('button', { name: '保存', exact: true }).click()
  saved = (await calls()).find(call => call.type === 'save')
  assert.equal(saved.params.camera.triggerMode, 'hardware')
  assert.equal(saved.params.camera.gain.value.ref, 0)
  await page.evaluate(() => window.resolveSave())
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  console.log('PASS 已连接相机筛选、采集模式、零增益值对象')

  let releaseScan
  await page.route('**/api/v1/files/scan', async route => {
    const data = await new Promise(resolve => { releaseScan = resolve })
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, data }) })
  })
  await configure(makeNode('目录采集', 'capture', { source: 'dir', dir: { path: '/images' } }))
  await page.getByRole('button', { name: '扫描', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.nc-feedback')?.textContent.includes('扫描中'))
  while (!releaseScan) await new Promise(resolve => setTimeout(resolve, 10))
  releaseScan({ files: [{ path: '/images/a.png', name: 'a.png' }, { path: '/images/b.png', name: 'b.png' }] })
  await page.getByText('a.png', { exact: true }).waitFor()
  await page.getByRole('switch', { name: '自动切换预览', exact: true }).click()
  await page.getByRole('switch', { name: '循环', exact: true }).click()
  await page.getByLabel('切换间隔(μs)').fill('1000')
  await page.getByRole('button', { name: '播放预览', exact: true }).click()
  await page.waitForFunction(() => window.calls.filter(call => call.type === 'debug').length === 1)
  assert.equal((await calls()).find(call => call.type === 'debug').params.file.path, '/images/a.png')
  await page.evaluate(() => window.resolveDebug({}))
  await page.waitForFunction(() => window.calls.filter(call => call.type === 'debug').length === 2)
  assert.equal((await calls()).filter(call => call.type === 'debug')[1].params.file.path, '/images/b.png')
  await page.evaluate(() => window.resolveDebug({}))
  await page.getByRole('button', { name: '播放预览', exact: true }).waitFor()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  assert.equal((await calls()).filter(call => call.type === 'save').length, 0)
  console.log('PASS 目录扫描、选择、自动预览逐张调试与末张停止')

  await configure(makeNode('扫描取消', 'capture', { source: 'dir', dir: { path: '/old' } }))
  releaseScan = undefined
  await page.getByRole('button', { name: '扫描', exact: true }).click()
  while (!releaseScan) await new Promise(resolve => setTimeout(resolve, 10))
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await configure(makeNode('新的目录', 'capture', { source: 'dir' }))
  releaseScan({ files: [{ path: '/old/late.png', name: 'late.png' }] })
  await page.waitForTimeout(100)
  assert.equal(await page.getByLabel('目录路径').inputValue(), '')
  assert.equal(await page.getByText('late.png', { exact: true }).count(), 0)
  console.log('PASS 扫描取消后旧结果不污染新节点')

  assert.deepEqual(errors, [])
  console.log('PASS 浏览器无运行异常')
} finally {
  await browser?.close()
  await server.close()
  await rm(folder, { recursive: true, force: true })
}
