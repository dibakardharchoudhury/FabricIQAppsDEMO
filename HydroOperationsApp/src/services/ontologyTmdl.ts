import { decodeDefinitionText, type DefinitionPart, type OntologyContract, type OntologyEntityType, type OntologyProperty } from './ontologyContract'

type Node = { text: string; children: Node[]; location: string }
const MAX_BYTES = 8 * 1024 * 1024
const MAX_NODES = 50_000
const fail = (node: Node, message: string): never => { throw new Error(`Ontology v2 ${node.location}: ${message}`) }

// A bounded reader for the ontology projection, not a general TOM/TMDL evaluator.
function tree(part: DefinitionPart): Node[] {
  if ((part.payload?.length ?? 0) > MAX_BYTES * 1.4) throw new Error(`Ontology v2 ${part.path}: definition part is too large.`)
  const text = decodeDefinitionText(part)
  const root: Node = { text: '', children: [], location: part.path }
  const stack = [{ indent: -1, node: root }]
  let count = 0
  for (const [index, line] of text.replace(/^\uFEFF/, '').split(/\r?\n/).entries()) {
    if (!line.trim() || line.trimStart().startsWith('//')) continue
    const indent = line.match(/^[ \t]*/)![0].replaceAll('\t', '    ').length
    const node: Node = { text: line.trim(), children: [], location: `${part.path}:${index + 1}` }
    if (++count > MAX_NODES || stack.length > 40) fail(node, 'definition exceeds parser limits.')
    while (stack.at(-1)!.indent >= indent) stack.pop()
    stack.at(-1)!.node.children.push(node)
    stack.push({ indent, node })
  }
  return root.children
}

function name(value: string, node: Node): string {
  if (/^'(?:[^']|'')+'$/.test(value)) return value.slice(1, -1).replaceAll("''", "'")
  if (!value || /['"\s,=]/.test(value)) return fail(node, `unsupported or invalid name ${value}. Use a single TMDL name, quoted when needed.`)
  return value
}

function field(node: Node, key: string): string | undefined {
  const matches = node.children.filter(child => new RegExp(`^${key}\\s*:`).test(child.text))
  if (matches.length > 1) fail(node, `duplicate ${key}.`)
  if (!matches.length) return undefined
  if (matches[0].children.length) fail(matches[0], `multiline ${key} is not supported by the app.`)
  return matches[0].text.slice(matches[0].text.indexOf(':') + 1).trim()
}

function namedField(node: Node, key: string): string | undefined {
  const value = field(node, key)
  return value === undefined ? undefined : name(value, node)
}

function jsonAssignment(node: Node, key: string): unknown {
  const matches = node.children.filter(child => new RegExp(`^${key}\\s*=`).test(child.text))
  if (matches.length > 1) fail(node, `duplicate ${key}.`)
  if (!matches.length) return undefined
  try {
    if (matches[0].children.length) throw new Error()
    return JSON.parse(matches[0].text.slice(matches[0].text.indexOf('=') + 1)) as unknown
  } catch { return fail(node, `${key} must be a single-line JSON object or array; multiline expressions are not supported.`) }
}

function record(value: unknown, node: Node): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail(node, 'expected a JSON object.')
  return value as Record<string, unknown>
}

function jsonField(node: Node, key: string): Record<string, unknown> | undefined {
  const value = jsonAssignment(node, key)
  return value === undefined ? undefined : record(value, node)
}

function block(node: Node, key: string): Node | undefined {
  const matches = node.children.filter(child => child.text === key)
  if (matches.length > 1) fail(node, `duplicate ${key}.`)
  return matches[0]
}

function qualified(value: string, node: Node) {
  const segments = value.split('#')
  if (segments.length > 2 || segments.some(segment => !segment)) fail(node, `invalid namespace-qualified name ${value}.`)
  const namespace = segments.length === 2 ? segments[0] : 'default'
  const localName = segments.at(-1)!
  return { namespace, localName, name: namespace === 'default' ? localName : `${namespace}#${localName}` }
}

function column(value: string, node: Node): { table: string; column: string } {
  const match = value.match(/^('(?:[^']|'')+'|[^.'\s]+)\.('(?:[^']|'')+'|[^.'\s]+)$/)
  if (!match) return fail(node, `unsupported column reference ${value}; expected table.column.`)
  return { table: name(match[1], node), column: name(match[2], node) }
}

const primitive = /^(string|int64|double|dateTime|boolean|decimal)$/i

function validateFields(value: unknown, member: string, node: Node, validate: (value: unknown) => void) {
  if (!Array.isArray(value) || !value.length) return fail(node, 'struct fields must be a non-empty JSON array.')
  const names = new Set<string>()
  for (const entry of value) {
    const item = record(entry, node)
    if (typeof item.name !== 'string' || !item.name || names.has(item.name.toLowerCase())) fail(node, 'struct field names must be non-empty and unique.')
    names.add((item.name as string).toLowerCase())
    validate(item[member])
  }
}

function validateComplex(value: unknown, node: Node, depth = 0) {
  if (depth > 24) fail(node, 'complex metadata exceeds nesting limits.')
  const shape = record(value, node)
  if (depth === 0 && shape.kind !== 'struct' && shape.kind !== 'timeSeries') fail(node, 'complexDataType root kind must be struct or timeSeries.')
  switch (shape.kind) {
    case 'primitive':
      if (typeof shape.dataType !== 'string' || !primitive.test(shape.dataType)) fail(node, 'unsupported complex primitive type.')
      break
    case 'any': break
    case 'struct': validateFields(shape.fields, 'type', node, child => validateComplex(child, node, depth + 1)); break
    case 'timeSeries': validateComplex(shape.elementType, node, depth + 1); break
    default: fail(node, `unsupported complex kind ${String(shape.kind)}.`)
  }
}

function validateBacking(value: unknown, node: Node, depth = 0) {
  if (depth > 24) fail(node, 'backing metadata exceeds nesting limits.')
  const shape = record(value, node)
  const validateColumn = (ref: unknown) => {
    if (typeof ref === 'string') { column(ref, node); return }
    const pair = record(ref, node)
    if (typeof pair.table !== 'string' || !pair.table || typeof pair.column !== 'string' || !pair.column) fail(node, 'column references require table and column.')
  }
  switch (shape.type) {
    case 'default':
      validateColumn(shape.valueColumn)
      if (shape.fields || shape.orderingColumn || shape.valueBackingConfiguration) fail(node, 'default backing supports only valueColumn.')
      break
    case 'struct':
      if (shape.valueColumn || shape.orderingColumn || shape.valueBackingConfiguration) fail(node, 'struct backing supports only fields.')
      validateFields(shape.fields, 'backingConfiguration', node, child => validateBacking(child, node, depth + 1))
      break
    case 'timeSeries':
      if (shape.fields || !shape.orderingColumn || Boolean(shape.valueColumn) === Boolean(shape.valueBackingConfiguration)) fail(node, 'timeSeries requires orderingColumn and exactly one value source.')
      validateColumn(shape.orderingColumn)
      if (shape.valueColumn) validateColumn(shape.valueColumn)
      else validateBacking(shape.valueBackingConfiguration, node, depth + 1)
      break
    default: fail(node, `unsupported backing type ${String(shape.type)}.`)
  }
}

function property(node: Node, entityName: string, warnings: Set<string>): [string, OntologyProperty] {
  for (const child of node.children) {
    if (!/^(dataType:|lineageTag:|complexDataType\s*=|backingConfiguration$|synonym |description:|annotation )/.test(child.text)) {
      fail(child, 'unsupported property construct; the app cannot safely project this definition.')
    }
  }
  const propertyName = name(node.text.slice('property '.length), node)
  const dataType = field(node, 'dataType')
  if (!dataType || !/^(string|int64|double|dateTime|boolean|decimal|Any|complex|TimeSeries<(string|int64|double|dateTime|boolean|decimal)>)$/i.test(dataType)) {
    fail(node, `unsupported or missing property dataType ${dataType ?? ''}.`)
  }
  const complexDataType = jsonField(node, 'complexDataType')
  if (dataType!.toLowerCase() === 'complex' && !complexDataType) fail(node, 'complex requires complexDataType.')
  if (complexDataType && dataType!.toLowerCase() !== 'complex') fail(node, 'complexDataType requires dataType: complex.')
  if (complexDataType) {
    validateComplex(complexDataType, node)
    warnings.add('Complex property types are retained as metadata, not evaluated by the app.')
  }
  const backing = block(node, 'backingConfiguration')
  let backingConfiguration: Record<string, unknown> | undefined
  if (backing) {
    for (const child of backing.children) {
      if (!/^(type:|valueColumn:|orderingColumn:|valueBackingConfiguration\s*=|fields\s*=)/.test(child.text)) fail(child, 'unsupported property backing construct.')
    }
    const type = field(backing, 'type') ?? 'default'
    if (!['default', 'struct', 'timeSeries'].includes(type)) fail(backing, `unsupported backingConfiguration type ${type}.`)
    const valueColumn = field(backing, 'valueColumn')
    const orderingColumn = field(backing, 'orderingColumn')
    const valueBackingConfiguration = jsonField(backing, 'valueBackingConfiguration')
    const fields = jsonAssignment(backing, 'fields')
    if (type === 'timeSeries' && (!orderingColumn || Boolean(valueColumn) === Boolean(valueBackingConfiguration))) {
      fail(backing, 'timeSeries requires orderingColumn and exactly one of valueColumn or valueBackingConfiguration.')
    }
    if (type === 'default' && (orderingColumn || valueBackingConfiguration)) fail(backing, 'orderingColumn and valueBackingConfiguration require type: timeSeries.')
    backingConfiguration = {
      type,
      ...(valueColumn ? { valueColumn: column(valueColumn, backing) } : {}),
      ...(orderingColumn ? { orderingColumn: column(orderingColumn, backing) } : {}),
      ...(valueBackingConfiguration ? { valueBackingConfiguration } : {}),
      ...(fields !== undefined ? { fields } : {}),
    }
    validateBacking(backingConfiguration, backing)
    if (type === 'timeSeries') warnings.add('Time-series bindings are metadata only; live readings still use the app Eventhouse OPC UA join.')
  }
  return [propertyName, { id: namedField(node, 'lineageTag') ?? `${entityName}.${propertyName}`, dataType: dataType!, complexDataType, backingConfiguration }]
}

export function parseTmdlContract(id: string, displayName: string, parts: DefinitionPart[]): OntologyContract {
  if (parts.length > 2_000 || parts.reduce((size, part) => size + (part.payload?.length ?? 0), 0) > MAX_BYTES * 4) throw new Error('Ontology v2 definition exceeds app parser limits.')
  for (const part of parts) {
    if (part.path.startsWith('entities/') && !/^entities\/[^/]+\.tmdl$/.test(part.path)) throw new Error(`Ontology v2 ${part.path}: unsupported entity part path.`)
    if (part.path.endsWith('.tmdl') && !/^(database|model|relationships|entityRelationships|expressions)\.tmdl$|^(entities|tables|metrics|namespaces|rules)\/[^/]+\.tmdl$/.test(part.path)) {
      throw new Error(`Ontology v2 ${part.path}: unsupported definition part.`)
    }
  }
  const warnings = new Set<string>()
  const entityTypes: OntologyEntityType[] = []
  const identities = new Set<string>()
  const reserve = (value: string, node: Node) => {
    const key = value.toLowerCase()
    if (identities.has(key)) fail(node, `duplicate identity ${value}.`)
    identities.add(key)
  }
  for (const part of parts.filter(part => /^entities\/[^/]+\.tmdl$/.test(part.path))) {
    const roots = tree(part)
    if (roots.length !== 1 || !roots[0].text.startsWith('entity ')) throw new Error(`Ontology v2 ${part.path}: expected one entity declaration.`)
    const node = roots[0]
    const identity = qualified(name(node.text.slice(7), node), node)
    const fileIdentity = qualified(part.path.slice(9, -5), node)
    if (identity.name !== fileIdentity.name) fail(node, 'entity name does not match its part path.')
    reserve(`entity:${identity.name}`, node)
    const propertyMetadata = Object.create(null) as Record<string, OntologyProperty>
    for (const child of node.children.filter(child => child.text.startsWith('property '))) {
      const [propertyName, metadata] = property(child, identity.name, warnings)
      reserve(`property:${identity.name}.${propertyName}`, child)
      propertyMetadata[propertyName] = metadata
    }
    const key = namedField(node, 'keyProperty')
    if (key && !propertyMetadata[key]) fail(node, `keyProperty ${key} does not reference a declared property.`)
    if (key && !primitive.test(propertyMetadata[key].dataType)) fail(node, 'keyProperty must reference a scalar primitive property.')
    const entityId = namedField(node, 'lineageTag') ?? identity.name
    reserve(`id:${entityId}`, node)
    const additionalBackingTables = node.children.filter(child => child.text === 'additionalBackingTable').map(child => {
      for (const field of child.children) if (!/^(table:|relationship:)/.test(field.text)) fail(field, 'unsupported additional backing table construct.')
      const table = namedField(child, 'table')
      const relationship = namedField(child, 'relationship')
      if (!table || !relationship) fail(child, 'additionalBackingTable requires table and relationship.')
      reserve(`additional-table:${identity.name}.${table}`, child)
      return { table: table!, relationship: relationship! }
    })
    entityTypes.push({
      ...identity,
      id: entityId,
      entityIdParts: key ? [propertyMetadata[key].id] : [],
      properties: Object.fromEntries(Object.entries(propertyMetadata).map(([key, value]) => [key, value.id])),
      propertyMetadata,
      sourceTable: namedField(node, 'backingTable'),
      additionalBackingTables,
    })
    if (node.children.some(child => child.text === 'resourceLink')) warnings.add('Entity resource links are not rendered or followed by the app.')
    const supported = /^(property |lineageTag:|backingTable:|keyProperty:|resourceLink$|additionalBackingTable$|synonym |description:|displayName:|namespace:|annotation )/
    for (const child of node.children) if (!supported.test(child.text)) fail(child, 'unsupported entity construct; the app cannot safely project this definition.')
  }
  const byName = new Map(entityTypes.map(entity => [entity.name.toLowerCase(), entity]))
  const physical = new Map<string, Node>()
  const physicalPart = parts.find(part => part.path === 'relationships.tmdl')
  if (physicalPart) for (const node of tree(physicalPart)) {
    if (!node.text.startsWith('relationship ')) fail(node, 'expected a physical relationship declaration.')
    const key = name(node.text.slice(13), node)
    if (physical.has(key)) fail(node, `duplicate physical relationship ${key}.`)
    physical.set(key, node)
  }
  for (const entity of entityTypes) for (const backing of entity.additionalBackingTables ?? []) {
    const relationship = physical.get(backing.relationship)
    if (!relationship) throw new Error(`Ontology v2 entity ${entity.name}: additional backing relationship ${backing.relationship} was not found in relationships.tmdl.`)
    const from = field(relationship, 'fromColumn')
    const to = field(relationship, 'toColumn')
    if (!from || !to) fail(relationship, 'additional backing tables require a single-column physical relationship.')
    const tables = new Set([column(from!, relationship).table, column(to!, relationship).table])
    if (!entity.sourceTable || !tables.has(entity.sourceTable) || !tables.has(backing.table)) fail(relationship, 'additional backing relationship must join the primary and additional table.')
  }
  const relationshipTypes: OntologyContract['relationshipTypes'] = []
  const edgePart = parts.find(part => part.path === 'entityRelationships.tmdl')
  if (edgePart) for (const node of tree(edgePart)) {
    if (!node.text.startsWith('entityRelationship ')) fail(node, 'expected an entityRelationship declaration (not a physical relationship).')
    for (const child of node.children) {
      if (!/^(label:|lineageTag:|fromEntity:|toEntity:|backingConfiguration$|synonym |description:|displayName:|namespace:|annotation )/.test(child.text)) fail(child, 'unsupported semantic relationship construct.')
    }
    const identity = qualified(name(node.text.slice(19), node), node)
    reserve(`relationship:${identity.name}`, node)
    const endpoint = (key: string) => {
      const value = namedField(node, key)
      const entity = value ? byName.get(qualified(value, node).name.toLowerCase()) : undefined
      if (!entity) return fail(node, `${key} ${value ?? ''} does not resolve to a declared entity.`)
      return entity
    }
    const source = endpoint('fromEntity')
    const target = endpoint('toEntity')
    const backing = block(node, 'backingConfiguration')
    if (backing) for (const child of backing.children) {
      if (!/^(type:|relationship:|table:|fromRelationship:|toRelationship:)/.test(child.text)) fail(child, 'unsupported semantic relationship backing construct.')
    }
    const backingRelationship = backing ? namedField(backing, 'relationship') : undefined
    const binding = backingRelationship ? physical.get(backingRelationship) : undefined
    let sourceKeys: string[] = []
    let targetKeys: string[] = []
    const backingType = backing ? field(backing, 'type') : undefined
    if (backingType && !['relationship', 'table', 'default'].includes(backingType)) fail(node, `unsupported relationship backing type ${backingType}.`)
    const compatibilityUnsupported = backingType === 'table' || !backingRelationship
    if (backingType === 'table' && (!namedField(backing!, 'table') || !namedField(backing!, 'fromRelationship') || !namedField(backing!, 'toRelationship') || backingRelationship)) {
      fail(node, 'table-backed relationships require table, fromRelationship and toRelationship, not relationship.')
    }
    if (compatibilityUnsupported) warnings.add('Unbound or junction-table relationship definitions are retained as metadata. Only materialized native graph instances supply rendered relationships.')
    if (backingRelationship && !binding) fail(node, `backing relationship ${backingRelationship} was not found in relationships.tmdl.`)
    if (binding) {
      const type = field(binding, 'type')
      if (type && type !== 'singleColumn') fail(binding, 'only single-column physical relationships are supported.')
      const from = field(binding, 'fromColumn')
      const to = field(binding, 'toColumn')
      if (!from || !to) fail(binding, 'only single-column physical relationship bindings are supported.')
      const fromRef = column(from!, binding)
      const toRef = column(to!, binding)
      if (source.sourceTable === fromRef.table && target.sourceTable === toRef.table) {
        sourceKeys = [fromRef.column]; targetKeys = [toRef.column]
      } else if (source.sourceTable === toRef.table && target.sourceTable === fromRef.table) {
        sourceKeys = [toRef.column]; targetKeys = [fromRef.column]
      } else fail(node, 'physical relationship tables do not match the semantic endpoints.')
    }
    const relationshipId = namedField(node, 'lineageTag') ?? identity.name
    const label = field(node, 'label')
    reserve(`relationship-id:${relationshipId}`, node)
    relationshipTypes.push({
      id: relationshipId, name: identity.name,
      label: label?.startsWith("'") ? name(label, node) : label,
      sourceEntityTypeId: source.id, targetEntityTypeId: target.id,
      sourceEntityName: source.name, targetEntityName: target.name,
      sourceKeys, targetKeys, backingRelationship, compatibilityUnsupported,
    })
  }
  if (!entityTypes.length) {
    const database = parts.find(part => part.path === 'database.tmdl')
    if (!database || !tree(database).some(node => /^database(?: |$)/.test(node.text))) throw new Error('Ontology v2 definition has no entities or valid database.tmdl.')
    warnings.add('This Ontology v2 definition contains no entities. Publish entity definitions to show governed topology.')
  }
  if (parts.some(part => /^(rules|metrics)\//.test(part.path))) warnings.add('Rules and metrics are not evaluated by the app.')
  return { id, displayName, generation: 2, entityTypes, relationshipTypes, warnings: [...warnings] }
}
