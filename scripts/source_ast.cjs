// Parse bounded text only. Never import, evaluate, resolve or execute supplied source.
const fs = require('node:fs')
;(async () => {
  const { parseSync } = await import('rolldown/utils')
  const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
  const locations = [], errors = []
  const normalize = (s) => String(s || '').replace(/\s+/g, ' ').trim()
  const text = normalize(input.selection.text).slice(0, 120)
  const marker = String(input.selection.source_marker || '')
  for (const [path, source] of Object.entries(input.files)) {
    if (!/^src\/.*\.[jt]sx?$/.test(path)) continue
    let parsed
    try { parsed = parseSync(path, source) } catch { errors.push(path); continue }
    if (parsed.errors.length) { errors.push(path); continue }
    const stack = [{ node: parsed.program, component: null }]
    let visited = 0
    while (stack.length && ++visited < 300000) {
      const item = stack.pop(), node = item.node
      if (!node || typeof node !== 'object') continue
      let component = item.component
      if ((['FunctionDeclaration', 'FunctionExpression', 'ClassDeclaration'].includes(node.type) || (node.type === 'VariableDeclarator' && ['ArrowFunctionExpression', 'FunctionExpression'].includes(node.init?.type))) && node.id?.name) component = node.id.name
      if (node.type === 'JSXElement') {
        const opening = node.openingElement
        const attrs = opening.attributes || []
        const literal = (key) => attrs.find(a => a.type === 'JSXAttribute' && a.name?.name === key)?.value
        const sourceAttr = literal('data-pa-source')
        const line = source.slice(0, opening.start).split('\n').length
        const explicit = marker === `${path}:${line}` && sourceAttr?.type === 'Literal' && sourceAttr.value === marker
        const content = normalize(node.children.map(c => c.type === 'JSXText' ? c.value : c.type === 'JSXExpressionContainer' && c.expression.type === 'Literal' && typeof c.expression.value === 'string' ? c.expression.value : '').join(' '))
        const label = literal('aria-label')
        if (explicit || (text && (content === text || (label?.type === 'Literal' && normalize(label.value) === text)))) {
          locations.push({ path, line, component, tag: opening.name?.name || '组合组件', basis: explicit ? 'explicit_marker' : 'jsx_text', confidence: explicit ? '语法树确认标记与源码行一致；仍须核对组件逻辑' : 'JSX文字/标签匹配；组件可能复用，需人工核对' })
        }
      }
      for (const [key, value] of Object.entries(node)) {
        if (key === 'parent') continue
        if (Array.isArray(value)) { for (let i = value.length - 1; i >= 0; i--) if (value[i]?.type) stack.push({node: value[i], component}) }
        else if (value && typeof value === 'object' && value.type) stack.push({node: value, component})
      }
    }
    if (visited >= 300000) errors.push(path)
  }
  process.stdout.write(JSON.stringify({ locations: locations.slice(0, 24), errors: errors.slice(0, 20) }))
})().catch(() => { process.stderr.write('source parser unavailable'); process.exitCode = 1 })
