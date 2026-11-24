import { parse } from '@babel/parser'
import MagicString from 'magic-string'
import { createRequire } from 'node:module'
import { readFileSync, statSync } from 'node:fs'
import path from 'node:path'

const require = createRequire(import.meta.url)
const nameOf = node => node?.name ?? node?.value

// Read the installed package's public ESM barrel, rather than deriving filenames
// from icon names: several public names (including Loader2) are aliases.
export function readLucideExports(packageJsonPath = require.resolve('lucide-react/package.json')) {
  const root = path.dirname(packageJsonPath)
  const pkg = JSON.parse(readFileSync(packageJsonPath, 'utf8'))
  if (typeof pkg.module !== 'string') throw new Error('lucide-direct-imports: lucide ESM entry is missing')
  if (pkg.sideEffects !== false) throw new Error('lucide-direct-imports: cannot safely bypass a barrel with possible side effects')
  const entry = path.resolve(root, pkg.module)
  const ast = parse(readFileSync(entry, 'utf8'), { sourceType: 'module' })
  const icons = new Map()
  const otherExports = new Set()
  for (const node of ast.program.body) {
    if (node.type === 'ExportAllDeclaration') {
      throw new Error('lucide-direct-imports: unsupported export-all in lucide barrel')
    }
    if (node.type !== 'ExportNamedDeclaration') continue
    if (node.declaration) {
      const declaration = node.declaration
      if (declaration.id) otherExports.add(declaration.id.name)
      for (const d of declaration.declarations ?? []) if (d.id.type === 'Identifier') otherExports.add(d.id.name)
    }
    for (const spec of node.specifiers) {
      const exported = nameOf(spec.exported)
      if (!exported) throw new Error('lucide-direct-imports: unsupported barrel export')
      if (spec.type === 'ExportSpecifier' && nameOf(spec.local) === 'default' && node.source?.value?.startsWith('./icons/')) {
        const target = path.resolve(path.dirname(entry), node.source.value)
        if (!target.startsWith(path.resolve(path.dirname(entry), 'icons') + path.sep)) throw new Error(`lucide-direct-imports: unsafe icon path for ${exported}`)
        if (icons.has(exported) && icons.get(exported) !== target) throw new Error(`lucide-direct-imports: conflicting icon mapping for ${exported}`)
        icons.set(exported, target)
      } else {
        otherExports.add(exported)
      }
    }
  }
  if (!icons.size) throw new Error('lucide-direct-imports: no icon exports found')
  return { icons, otherExports }
}

export function transformLucideImports(code, id, exportsMap) {
  const filename = id.split('?')[0]
  const ext = path.extname(filename).toLowerCase()
  if (!['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts'].includes(ext) || !code.includes('lucide-react')) return null
  const plugins = []
  if (['.ts', '.tsx', '.mts', '.cts'].includes(ext)) plugins.push('typescript')
  if (['.jsx', '.tsx'].includes(ext)) plugins.push('jsx')
  let ast
  try { ast = parse(code, { sourceType: 'unambiguous', plugins }) }
  catch (error) { throw new Error(`lucide-direct-imports: cannot parse ${filename}: ${error.message}`) }
  const edits = new MagicString(code)
  let changed = false
  for (const node of ast.program.body) {
    if (node.type !== 'ImportDeclaration' || node.source.value !== 'lucide-react') continue
    // Side-effect, default, namespace, import attributes and pure type imports
    // stay untouched. Vite/TypeScript retain their ordinary semantics.
    if (!node.specifiers.length || node.importKind === 'type' || node.specifiers.some(s => s.type !== 'ImportSpecifier') || node.attributes?.length || node.assertions?.length) continue
    const values = node.specifiers.filter(s => s.importKind !== 'type')
    if (!values.length) continue
    for (const spec of values) {
      const name = nameOf(spec.imported)
      if (!exportsMap.icons.has(name) && !exportsMap.otherExports.has(name)) throw new Error(`lucide-direct-imports: unknown lucide export ${name} in ${filename}`)
    }
    // Public non-icon exports (e.g. createLucideIcon) must keep the barrel.
    if (values.some(s => !exportsMap.icons.has(nameOf(s.imported)))) continue
    const imports = []
    // Retain comments exactly; do not interpret comment or string contents.
    for (const comment of ast.comments ?? []) {
      if (comment.start >= node.start && comment.end <= node.end) imports.push(code.slice(comment.start, comment.end) + '\n')
    }
    const types = node.specifiers.filter(s => s.importKind === 'type')
    if (types.length) imports.push(`import type { ${types.map(s => `${code.slice(s.imported.start, s.imported.end)}${nameOf(s.imported) === s.local.name ? '' : ` as ${s.local.name}`}`).join(', ')} } from 'lucide-react';\n`)
    for (const spec of values) {
      const name = nameOf(spec.imported)
      const target = exportsMap.icons.get(name)
      try { if (!statSync(target).isFile()) throw new Error('not a file') }
      catch { throw new Error(`lucide-direct-imports: icon file missing for ${name}: ${target}`) }
      // Normalize Windows separators; JSON encoding protects path characters.
      imports.push(`import ${spec.local.name} from ${JSON.stringify(target.split(path.sep).join('/'))};\n`)
    }
    edits.overwrite(node.start, node.end, imports.join(''))
    changed = true
  }
  return changed ? { code: edits.toString(), map: edits.generateMap({ hires: true, source: filename, includeContent: true }) } : null
}

export default function lucideDirectImports() {
  let exportsMap
  return {
    name: 'lucide-direct-imports',
    apply: 'build',
    enforce: 'pre',
    buildStart() { exportsMap = readLucideExports() },
    transform(code, id) {
      if (id.includes('/node_modules/') || id.includes('\\node_modules\\')) return null
      return transformLucideImports(code, id, exportsMap)
    },
  }
}
