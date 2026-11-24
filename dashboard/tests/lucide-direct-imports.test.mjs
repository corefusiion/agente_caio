import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { rollup } from 'rollup'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { SourceMapConsumer } from 'source-map-js'
import plugin, { readLucideExports, transformLucideImports } from '../vite-plugins/lucide-direct-imports.js'

const require = createRequire(import.meta.url)
const mapping = readLucideExports()
const transform = (code, filename = '/project/example.tsx', map = mapping) => transformLucideImports(code, filename, map)

test('real barrel mappings resolve to identical icon components and rendering, including aliases', async () => {
  const packagePath = require.resolve('lucide-react/package.json')
  const pkg = JSON.parse(readFileSync(packagePath, 'utf8'))
  const lucide = await import(pathToFileURL(path.resolve(path.dirname(packagePath), pkg.module)).href)
  for (const name of ['Activity', 'Loader2', 'LoaderCircle', 'ArrowUp', 'Fingerprint', 'FingerprintPattern', 'FileText']) {
    assert.ok(mapping.icons.has(name), name)
    const direct = await import(pathToFileURL(mapping.icons.get(name)).href)
    assert.equal(direct.default, lucide[name], name)
    assert.equal(renderToStaticMarkup(React.createElement(direct.default, { size: 18 })), renderToStaticMarkup(React.createElement(lucide[name], { size: 18 })))
  }
  assert.equal(mapping.icons.get('Loader2'), mapping.icons.get('LoaderCircle'))
})

test('mixed values, aliases, type specifiers, TSX and comments', () => {
  const source = `// import { Fake } from 'lucide-react';
const s = "import { Fake } from 'lucide-react'";
import { /* before */ type LucideProps as Props, Loader2 as Spinner, // important
 Activity } from 'lucide-react';
const C = (props: Props) => <Spinner {...props} />;
`
  const out = transform(source).code
  assert.ok(out.includes(`// import { Fake } from 'lucide-react';`))
  assert.ok(out.includes(`const s = "import { Fake } from 'lucide-react'";`))
  assert.ok(out.includes('/* before */'))
  assert.ok(out.includes('// important\n'))
  assert.match(out, /import type \{ LucideProps as Props \} from 'lucide-react'/)
  assert.ok(out.includes(`import Spinner from ${JSON.stringify(mapping.icons.get('Loader2').split(path.sep).join('/'))}`))
  assert.ok(out.includes(`import Activity from ${JSON.stringify(mapping.icons.get('Activity').split(path.sep).join('/'))}`))
  assert.ok(out.includes('const C = (props: Props) => <Spinner {...props} />;'))
})

test('unsupported forms and non-icon exports remain unchanged', () => {
  for (const input of [
    `import 'lucide-react'`, `import type { LucideProps } from 'lucide-react'`,
    `import { type LucideProps } from 'lucide-react'`, `import Default from 'lucide-react'`,
    `import * as icons from 'lucide-react'`, `import Default, { Activity } from 'lucide-react'`,
    `import { Activity, createLucideIcon } from 'lucide-react'`,
    `export { Activity } from 'lucide-react'`, `export * from 'lucide-react'`,
    `const x = import('lucide-react')`, `const x = "import { Activity } from 'lucide-react'"`,
  ]) assert.equal(transform(input), null, input)
})

test('unknown exports and missing icon files fail closed', () => {
  assert.throws(() => transform(`import { NotARealIcon } from 'lucide-react'`), /unknown lucide export NotARealIcon/)
  const broken = { ...mapping, icons: new Map(mapping.icons) }
  broken.icons.set('Activity', '/missing/lucide/icon.js')
  assert.throws(() => transform(`import { Activity } from 'lucide-react'`, '/project/test.js', broken), /icon file missing for Activity/)
  assert.throws(() => readLucideExports('/missing/package.json'))
})

test('JS, JSX, TS, TSX parsing and syntax error handling', () => {
  for (const [ext, body] of [['js', 'const x = 1'], ['jsx', 'const x = <div />'], ['ts', 'const x: number = 1'], ['tsx', 'const x = <div />']]) {
    assert.ok(transform(`import { Activity } from 'lucide-react'; ${body}`, `/test.${ext}`))
  }
  assert.throws(() => transform(`import { Activity } from 'lucide-react'; const = ;`, '/x.ts'), /cannot parse/)
})

test('source map traces unmodified content to the original source', () => {
  const input = `import { Activity } from 'lucide-react';\nconst answer = 42;\n`
  const out = transform(input, '/project/test.ts')
  const lines = out.code.split('\n')
  const line = lines.findIndex(x => x.includes('const answer')) + 1
  const column = lines[line - 1].indexOf('answer')
  const consumer = new SourceMapConsumer(JSON.parse(out.map.toString()))
  const pos = consumer.originalPositionFor({ line, column })
  assert.equal(pos.line, 2)
  assert.equal(pos.column, 6)
  assert.equal(consumer.sourceContentFor(pos.source), input)
})

test('configuration is build-only, default minification, constrained concurrency', async () => {
  const p = plugin()
  assert.equal(p.apply, 'build')
  assert.equal(p.enforce, 'pre')
  p.buildStart()
  assert.ok(p.transform(`import { Activity } from 'lucide-react'`, '/project/a.js'))
  assert.equal(p.transform(`import { Activity } from 'lucide-react'`, '/project/node_modules/lib/a.js'), null)
  const config = (await import('../vite.config.js')).default
  assert.equal(config.build.rollupOptions.maxParallelFileOps, 8)
  assert.equal(config.build.reportCompressedSize, false)
  assert.equal(config.build.minify, undefined)
})

test('small Rollup bundle resolves direct icon modules without the barrel', async () => {
  const p = plugin()
  const entry = path.resolve('virtual-entry.js')
  const bundle = await rollup({
    input: entry,
    external: ['react'],
    plugins: [{ name: 'fixture', resolveId(id) { if (id === entry) return id }, load(id) { if (id === entry) return `import { Loader2, Activity } from 'lucide-react'; export { Loader2, Activity }` } }, { name: p.name, buildStart: p.buildStart, transform: p.transform }],
    maxParallelFileOps: 8,
  })
  const result = await bundle.generate({ format: 'es' })
  const ids = Object.keys(result.output[0].modules)
  assert.ok(ids.some(id => id.endsWith('/icons/loader-circle.js')))
  assert.ok(ids.some(id => id.endsWith('/icons/activity.js')))
  assert.ok(!ids.some(id => id.endsWith('/lucide-react.js')))
  await bundle.close()
})

test('all existing source modules transform without errors', () => {
  function scan(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name)
      if (entry.isDirectory()) scan(file)
      else if (/\.(js|jsx|ts|tsx)$/.test(file)) transform(readFileSync(file, 'utf8'), file)
    }
  }
  scan(path.resolve('src'))
})

test('lock root and installed declared versions are synchronized offline', () => {
  const pkg = JSON.parse(readFileSync('package.json', 'utf8'))
  const lock = JSON.parse(readFileSync('package-lock.json', 'utf8'))
  assert.deepEqual(lock.packages[''].dependencies, pkg.dependencies)
  assert.deepEqual(lock.packages[''].devDependencies, pkg.devDependencies)
  for (const name of ['@babel/parser', 'magic-string']) {
    const installed = require(`${name}/package.json`).version
    assert.equal(lock.packages[`node_modules/${name}`].version, installed)
    assert.equal(pkg.devDependencies[name], `^${installed}`)
  }
})
