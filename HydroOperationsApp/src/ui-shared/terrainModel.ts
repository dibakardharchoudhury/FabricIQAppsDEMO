export const MAX_TERRAIN_BYTES = 32 * 1024 * 1024
export const MAX_MANIFEST_BYTES = 16 * 1024
export const MAX_TERRAIN_VERTICES = 500_000
export const MAX_TERRAIN_TRIANGLES = 500_000

export type TerrainManifest = {
  schemaVersion: 1
  catchmentName: string
  crs: string
  elevationUnit: 'm'
  verticalDatum: string
  source: string
  attribution: string
  generatedAt: string
  meshSha256: string
  toleranceMetres?: number
}

export type TerrainMesh = {
  positions: Float32Array
  indices: Uint32Array
  origin: [number, number, number]
  bounds: { min: [number, number, number]; max: [number, number, number] }
  ignoredFaceProperties: string[]
}

export type ImportedTerrain = TerrainMesh & { manifest: TerrainManifest; fileName: string }

export function parseTerrainManifest(text: string): TerrainManifest {
  const value: unknown = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Terrain metadata must be a JSON object.')
  const data = value as Record<string, unknown>
  const requiredText = (key: string) => {
    const field = data[key]
    if (typeof field !== 'string' || !field.trim() || field.length > 2000) throw new Error(`Metadata ${key} must be non-empty text (at most 2,000 characters).`)
    return field.trim()
  }
  if (data.schemaVersion !== 1) throw new Error('Terrain metadata requires schemaVersion 1.')
  const crs = requiredText('crs')
  const code = /^EPSG:(\d+)$/.exec(crs)?.[1]
  const epsg = Number(code)
  if (!code || !((epsg >= 25828 && epsg <= 25838) || (epsg >= 32601 && epsg <= 32660) || (epsg >= 32701 && epsg <= 32760))) {
    throw new Error('Use a supported metre-based UTM CRS: EPSG:25828-25838, EPSG:32601-32660 or EPSG:32701-32760. Geographic degrees and other projections are not supported.')
  }
  if (data.elevationUnit !== 'm') throw new Error('Terrain elevations must be in metres (elevationUnit: "m").')
  const generatedAt = requiredText('generatedAt')
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(generatedAt)
    || !Number.isFinite(Date.parse(generatedAt))
    || new Date(generatedAt).toISOString().slice(0, 19) !== generatedAt.slice(0, 19)) {
    throw new Error('Metadata generatedAt must be a valid UTC ISO timestamp, for example 2026-11-05T10:00:00Z.')
  }
  const meshSha256 = requiredText('meshSha256').toLowerCase()
  if (!/^[0-9a-f]{64}$/.test(meshSha256)) throw new Error('Metadata meshSha256 must be the mesh file SHA-256 (64 hexadecimal characters).')
  if (data.toleranceMetres !== undefined && (typeof data.toleranceMetres !== 'number' || !Number.isFinite(data.toleranceMetres) || data.toleranceMetres <= 0)) {
    throw new Error('Metadata toleranceMetres must be a positive finite number.')
  }
  return {
    schemaVersion: 1, catchmentName: requiredText('catchmentName'), crs, elevationUnit: 'm',
    verticalDatum: requiredText('verticalDatum'), source: requiredText('source'),
    attribution: requiredText('attribution'), generatedAt, meshSha256,
    ...(typeof data.toleranceMetres === 'number' ? { toleranceMetres: data.toleranceMetres } : {}),
  }
}

type ScalarType = 'double' | 'float' | 'uint' | 'int' | 'uchar'
type FaceProperty = { name: string; type: ScalarType; list: boolean }
const widths: Record<ScalarType, number> = { double: 8, float: 4, uint: 4, int: 4, uchar: 1 }
const scalarType = (value: string): value is ScalarType => Object.hasOwn(widths, value)

/** Read Rasputin's double-precision coordinates before converting to local GPU floats. */
export function parseRasputinPly(buffer: ArrayBuffer): TerrainMesh {
  if (!buffer.byteLength || buffer.byteLength > MAX_TERRAIN_BYTES) throw new Error('The PLY mesh must be non-empty and at most 32 MiB.')
  const bytes = new Uint8Array(buffer)
  const decoder = new TextDecoder()
  let headerEnd = -1
  let lineStart = 0
  const headerLines: string[] = []
  for (let i = 0; i < Math.min(bytes.length, 64 * 1024); i++) {
    if (bytes[i] !== 10) continue
    const line = decoder.decode(bytes.subarray(lineStart, i)).replace(/\r$/, '')
    headerLines.push(line)
    lineStart = i + 1
    if (line === 'end_header') { headerEnd = i + 1; break }
  }
  if (headerEnd < 0 || headerLines[0] !== 'ply') throw new Error('Missing or oversized PLY header.')
  const format = headerLines[1]
  if (format !== 'format ascii 1.0' && format !== 'format binary_little_endian 1.0') {
    throw new Error('Only Rasputin ASCII and binary little-endian PLY 1.0 meshes are supported.')
  }
  let element = ''
  let vertexCount = 0
  let faceCount = 0
  const vertexProperties: string[] = []
  const faceProperties: FaceProperty[] = []
  for (const line of headerLines.slice(2, -1)) {
    if (line.startsWith('comment ')) continue
    const words = line.split(' ')
    if (words[0] === 'element') {
      if (words.length !== 3 || !/^[1-9]\d*$/.test(words[2])) throw new Error('Invalid PLY element count.')
      if (words[1] === 'vertex' && !element) {
        element = 'vertex'
        vertexCount = Number(words[2])
      } else if (words[1] === 'face' && element === 'vertex') {
        element = 'face'
        faceCount = Number(words[2])
      } else throw new Error('Import the terrain surface PLY, not an edges file. Only vertex followed by face elements are supported.')
    } else if (words[0] === 'property' && element === 'vertex') {
      if (words.length !== 3 || words[1] !== 'double') throw new Error('Rasputin terrain requires double x/y/z vertex coordinates.')
      vertexProperties.push(words[2])
    } else if (words[0] === 'property' && element === 'face') {
      if (words.length === 5 && words[1] === 'list' && words[2] === 'uchar' && scalarType(words[3])) {
        faceProperties.push({ name: words[4], type: words[3], list: true })
      } else if (words.length === 3 && scalarType(words[1])) {
        faceProperties.push({ name: words[2], type: words[1], list: false })
      } else throw new Error('Unsupported PLY face property declaration.')
    } else throw new Error(`Unsupported PLY header declaration: ${line.slice(0, 100)}`)
  }
  if (vertexCount < 3 || vertexCount > MAX_TERRAIN_VERTICES || faceCount < 1 || faceCount > MAX_TERRAIN_TRIANGLES) {
    throw new Error('Terrain requires 3-500,000 vertices and 1-500,000 triangles. Export a smaller catchment or triangle budget.')
  }
  if (vertexProperties.join(',') !== 'x,y,z' || faceProperties[0]?.name !== 'vertex_indices'
    || !faceProperties[0].list || !['uint', 'int'].includes(faceProperties[0].type)
    || new Set(faceProperties.map(property => property.name)).size !== faceProperties.length) {
    throw new Error('Expected double x/y/z vertices and a triangle vertex_indices list.')
  }

  const view = new DataView(buffer)
  let offset = headerEnd
  const ascii = format === 'format ascii 1.0'
  const whitespace = (byte: number) => byte === 9 || byte === 10 || byte === 13 || byte === 32
  // Tokenize incrementally: splitting a large ASCII file into millions of strings is unbounded overhead.
  const read = (type: ScalarType): number => {
    let value: number
    if (ascii) {
      while (offset < bytes.length && whitespace(bytes[offset])) offset++
      const start = offset
      while (offset < bytes.length && !whitespace(bytes[offset])) {
        if (offset - start >= 64) throw new Error('Invalid oversized PLY number.')
        offset++
      }
      if (start === offset) throw new Error('Truncated PLY body.')
      const token = decoder.decode(bytes.subarray(start, offset))
      if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(token)) throw new Error('Invalid PLY number.')
      value = Number(token)
    } else {
      if (offset + widths[type] > bytes.length) throw new Error('Truncated PLY body.')
      switch (type) {
        case 'double': value = view.getFloat64(offset, true); break
        case 'float': value = view.getFloat32(offset, true); break
        case 'uint': value = view.getUint32(offset, true); break
        case 'int': value = view.getInt32(offset, true); break
        case 'uchar': value = view.getUint8(offset); break
      }
      offset += widths[type]
    }
    if (!Number.isFinite(value)) throw new Error('PLY contains non-finite values.')
    if ((type === 'uchar' && (!Number.isInteger(value) || value < 0 || value > 255))
      || (type === 'uint' && (!Number.isInteger(value) || value < 0 || value > 0xffffffff))
      || (type === 'int' && (!Number.isInteger(value) || value < -0x80000000 || value > 0x7fffffff))) {
      throw new Error('PLY integer is outside its declared type.')
    }
    return value
  }
  const coordinates = new Float64Array(vertexCount * 3)
  const min: [number, number, number] = [Infinity, Infinity, Infinity]
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity]
  for (let i = 0; i < coordinates.length; i++) {
    const value = read('double')
    if (Math.abs(value) > 100_000_000) throw new Error('Terrain coordinates exceed the supported metre-based range.')
    coordinates[i] = value
    const axis = i % 3
    min[axis] = Math.min(min[axis], value)
    max[axis] = Math.max(max[axis], value)
  }
  const indices = new Uint32Array(faceCount * 3)
  for (let face = 0; face < faceCount; face++) {
    for (const property of faceProperties) {
      const count = property.list ? read('uchar') : 1
      if (property.name === 'vertex_indices' && count !== 3) throw new Error('Only triangular terrain faces are supported.')
      for (let item = 0; item < count; item++) {
        const value = read(property.type)
        if (property.name !== 'vertex_indices') continue
        if (!Number.isInteger(value) || value < 0 || value >= vertexCount) throw new Error('PLY face references a missing vertex.')
        indices[face * 3 + item] = value
      }
    }
    const a = indices[face * 3]
    const b = indices[face * 3 + 1]
    const c = indices[face * 3 + 2]
    const area = (coordinates[b * 3] - coordinates[a * 3]) * (coordinates[c * 3 + 1] - coordinates[a * 3 + 1])
      - (coordinates[b * 3 + 1] - coordinates[a * 3 + 1]) * (coordinates[c * 3] - coordinates[a * 3])
    if (area === 0) throw new Error('PLY contains a degenerate terrain triangle.')
  }
  if (ascii) while (offset < bytes.length && whitespace(bytes[offset])) offset++
  if (offset !== bytes.length) throw new Error('PLY contains unexpected trailing data.')
  const origin: [number, number, number] = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, min[2]]
  const positions = new Float32Array(coordinates.length)
  for (let vertex = 0; vertex < vertexCount; vertex++) {
    const at = vertex * 3
    positions[at] = coordinates[at] - origin[0]
    positions[at + 1] = coordinates[at + 2] - origin[2]
    positions[at + 2] = -(coordinates[at + 1] - origin[1])
  }
  return { positions, indices, origin, bounds: { min, max }, ignoredFaceProperties: faceProperties.slice(1).map(property => property.name) }
}

export async function verifyTerrainHash(buffer: ArrayBuffer, expected: string): Promise<void> {
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))
  const actual = Array.from(hash, byte => byte.toString(16).padStart(2, '0')).join('')
  if (actual !== expected) throw new Error('Mesh SHA-256 does not match the metadata. Select the matching mesh and JSON files.')
}
