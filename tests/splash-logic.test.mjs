import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import test from 'node:test'
import assert from 'node:assert/strict'

const require = createRequire(import.meta.url)
const ts = require('typescript')
const source = readFileSync(new URL('../src/splash-logic.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(`${source}\nmodule.exports = { summarizeCapabilities };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const module = { exports: {} }
vm.runInNewContext(compiled, { exports: module.exports, module })
const { summarizeCapabilities } = module.exports

test('MVS unavailable warns but does not block', () => {
  const result = summarizeCapabilities({
    database: { ready: true },
    inference: {
      cpuAvailable: true,
      recommendedProvider: 'CPUExecutionProvider',
      accelerated: false,
    },
    camera: { vendors: [{ id: 'hikvision', available: false, error: 'missing' }] },
  })
  assert.equal(result.blocked, false)
  assert.equal(result.steps.camera.tone, 'warning')
})

test('missing CPU provider blocks startup', () => {
  const result = summarizeCapabilities({
    database: { ready: true },
    inference: { cpuAvailable: false, availableProviders: [] },
    camera: { vendors: [] },
  })
  assert.equal(result.blocked, true)
  assert.equal(result.steps.inference.tone, 'error')
})
