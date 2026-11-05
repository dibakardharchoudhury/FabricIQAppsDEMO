import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'
import {
  MAX_TERRAIN_BYTES, MAX_TERRAIN_TRIANGLES, MAX_TERRAIN_VERTICES,
  parseRasputinPly, parseTerrainManifest, verifyTerrainHash,
} from '../src/ui-shared/terrainModel.ts'
import { importTerrain } from '../src/ui-shared/terrainImport.ts'
import { V1_TABS, resolveV1Tab } from '../src/ui-v1/navigation.tsx'
import { V2_TABS, resolveV2Tab } from '../src/ui-v2/navigation.tsx'

const manifest = {
  schemaVersion: 1, catchmentName: 'Test catchment (synthetic)', crs: 'EPSG:25833',
  elevationUnit: 'm', verticalDatum: 'Test datum', source: 'Synthetic unit-test coordinates',
  attribution: 'Test fixture only; not operational data', generatedAt: '2026-11-05T10:00:00Z',
  meshSha256: 'a'.repeat(64), toleranceMetres: 5,
}
const points = [[430000.001, 6800000.001, 100], [430000.002, 6800000.001, 101], [430000.001, 6800000.002, 102]]
const header = (format = 'ascii', extra = '') => `ply\nformat ${format} 1.0\ncomment synthetic unit test\nelement vertex 3\nproperty double x\nproperty double y\nproperty double z\nelement face 1\nproperty list uchar uint vertex_indices\n${extra}end_header\n`
const ascii = (face = '3 0 1 2', extra = '') => header('ascii', extra) + points.map(point => point.join(' ')).join('\n') + `\n${face}\n`
const encode = (text: string) => new TextEncoder().encode(text).buffer
function binary(extra = false): ArrayBuffer {
  const prefix = new TextEncoder().encode(header('binary_little_endian', extra ? 'property int land_cover_code\n' : ''))
  const data = new ArrayBuffer(prefix.length + 72 + 13 + (extra ? 4 : 0))
  new Uint8Array(data).set(prefix)
  const view = new DataView(data)
  let at = prefix.length
  for (const point of points) for (const value of point) { view.setFloat64(at, value, true); at += 8 }
  view.setUint8(at++, 3)
  for (const index of [0, 1, 2]) { view.setUint32(at, index, true); at += 4 }
  if (extra) view.setInt32(at, 311, true)
  return data
}

test('both layouts expose the same lazy terrain page independently of the energy map', () => {
  for (const tabs of [V1_TABS, V2_TABS]) assert.equal(tabs.find(tab => tab.id === 'terrain')?.label, 'Catchment Terrain')
  assert.equal(resolveV1Tab('terrain').title, 'Catchment Terrain')
  assert.equal(resolveV2Tab('terrain').title, 'Catchment Terrain')
})

test('metadata retains explicit identity, projection, datum, credits and tolerance', () => {
  assert.deepEqual(parseTerrainManifest(JSON.stringify(manifest)), manifest)
  for (const crs of ['EPSG:25828', 'EPSG:25838', 'EPSG:32601', 'EPSG:32660', 'EPSG:32701', 'EPSG:32760']) {
    assert.equal(parseTerrainManifest(JSON.stringify({ ...manifest, crs })).crs, crs)
  }
})

test('metadata refuses missing provenance, unsupported projections, non-metre elevations and malformed dates', () => {
  for (const key of ['catchmentName', 'verticalDatum', 'source', 'attribution', 'generatedAt', 'meshSha256']) {
    assert.throws(() => parseTerrainManifest(JSON.stringify({ ...manifest, [key]: '' })))
  }
  for (const change of [
    { schemaVersion: 2 }, { crs: 'EPSG:4326' }, { crs: 'EPSG:3857' }, { crs: 'EPSG:32661' },
    { elevationUnit: 'ft' }, { meshSha256: 'not-a-hash' }, { toleranceMetres: 0 }, { toleranceMetres: '5' },
    { generatedAt: 'yesterday' }, { generatedAt: '2026-02-30T10:00:00Z' }, { generatedAt: '2026-11-05' },
  ]) assert.throws(() => parseTerrainManifest(JSON.stringify({ ...manifest, ...change })))
  for (const value of [null, [], 'text', 1]) assert.throws(() => parseTerrainManifest(JSON.stringify(value)))
})

test('ASCII and binary PLY retain sub-centimetre UTM differences before GPU conversion', () => {
  const a = parseRasputinPly(encode(ascii()))
  const b = parseRasputinPly(binary())
  assert.deepEqual(a, b)
  assert.deepEqual(Array.from(a.indices), [0, 1, 2])
  assert.ok(Math.abs((a.positions[3] - a.positions[0]) - 0.001) < 1e-8)
  assert.ok(Math.abs((a.positions[8] - a.positions[2]) + 0.001) < 1e-8, 'northing becomes negative display Z')
  assert.equal(a.positions[1], 0)
  assert.equal(a.positions[4], 1)
  assert.equal(a.positions[7], 2)
  assert.deepEqual(a.bounds, { min: points[0], max: [430000.002, 6800000.002, 102] })
})

test('CRLF ASCII headers and a flat elevation surface are valid', () => {
  const text = ascii().replace('6800000.001 101', '6800000.001 100').replace('6800000.002 102', '6800000.002 100')
  const mesh = parseRasputinPly(encode(text.replaceAll('\n', '\r\n')))
  assert.equal(mesh.bounds.max[2], 100)
})

test('land-cover codes and variable-length fractions are consumed and explicitly disclosed, never used as elevation', () => {
  assert.deepEqual(parseRasputinPly(binary(true)).ignoredFaceProperties, ['land_cover_code'])
  const mesh = parseRasputinPly(encode(ascii(
    '3 0 1 2 2 311 512 2 0.25 0.75 311 1 2 0.3 0.7 100 0',
    'property list uchar int land_cover_classes\nproperty list uchar float land_cover_fractions\nproperty int land_cover_code\nproperty uchar land_cover_filled\nproperty list uchar float dem_height_shares\nproperty int dem_height_first\nproperty float dem_nodata_share\n',
  )))
  assert.deepEqual(mesh.ignoredFaceProperties, ['land_cover_classes', 'land_cover_fractions', 'land_cover_code', 'land_cover_filled', 'dem_height_shares', 'dem_height_first', 'dem_nodata_share'])
  assert.equal(mesh.bounds.max[2], 102)
})

test('surface import rejects edges, unsupported formats, oversized declarations and duplicate properties', () => {
  for (const text of [
    ascii().replace('format ascii', 'format binary_big_endian'),
    ascii().replace('element face 1', 'element edge 1'),
    ascii().replace('element vertex 3', 'element vertex 500001'),
    ascii().replace('element face 1', 'element face 500001'),
    ascii().replace('element face 1', 'element face 0'),
    ascii().replace('property double x', 'property float x'),
    ascii().replace('property double y', 'property double x'),
    ascii().replace('property list uchar uint vertex_indices', 'property list uchar float vertex_indices'),
    ascii('3 0 1 2 1', 'property int vertex_indices\n'),
  ]) assert.throws(() => parseRasputinPly(encode(text)))
  assert.throws(() => parseRasputinPly(new ArrayBuffer(MAX_TERRAIN_BYTES + 1)), /32 MiB/)
  assert.throws(() => parseRasputinPly(encode('ply\n')), /header/)
})

test('invalid indices, polygons and degenerate triangles fail closed', () => {
  for (const face of ['3 0 1 3', '3 0 1 -1', '3 0 1 1.5', '3 0 0 2', '4 0 1 2 0']) {
    assert.throws(() => parseRasputinPly(encode(ascii(face))))
  }
  const collinear = ascii().replace('430000.001 6800000.002 102', '430000.003 6800000.001 102')
  assert.throws(() => parseRasputinPly(encode(collinear)), /degenerate/)
})

test('the exact 500,000-vertex and 500,000-triangle limits remain importable', () => {
  const prefix = new TextEncoder().encode(header('binary_little_endian')
    .replace('element vertex 3', `element vertex ${MAX_TERRAIN_VERTICES}`)
    .replace('element face 1', `element face ${MAX_TERRAIN_TRIANGLES}`))
  const data = new ArrayBuffer(prefix.length + MAX_TERRAIN_VERTICES * 24 + MAX_TERRAIN_TRIANGLES * 13)
  new Uint8Array(data).set(prefix)
  const view = new DataView(data)
  let at = prefix.length
  for (let vertex = 0; vertex < MAX_TERRAIN_VERTICES; vertex++) {
    for (const coordinate of [[0, 0, 100], [1, 0, 100], [0, 1, 100]][vertex % 3]) {
      view.setFloat64(at, coordinate, true)
      at += 8
    }
  }
  for (let face = 0; face < MAX_TERRAIN_TRIANGLES; face++) {
    view.setUint8(at++, 3)
    for (const index of [0, 1, 2]) { view.setUint32(at, index, true); at += 4 }
  }
  const mesh = parseRasputinPly(data)
  assert.equal(mesh.positions.length / 3, MAX_TERRAIN_VERTICES)
  assert.equal(mesh.indices.length / 3, MAX_TERRAIN_TRIANGLES)
})

test('truncated, non-finite, oversized numbers and trailing bodies are refused', () => {
  assert.throws(() => parseRasputinPly(binary().slice(0, -1)), /Truncated/)
  for (const token of ['NaN', 'Infinity', '1e999', '0x10', '1'.repeat(65)]) {
    assert.throws(() => parseRasputinPly(encode(ascii().replace('430000.001', token))))
  }
  const nonFinite = binary()
  new DataView(nonFinite).setFloat64(new TextEncoder().encode(header('binary_little_endian')).length, NaN, true)
  assert.throws(() => parseRasputinPly(nonFinite), /non-finite/)
  assert.throws(() => parseRasputinPly(encode(ascii() + 'junk')), /trailing/)
  assert.throws(() => parseRasputinPly(encode(ascii().slice(0, -4))), /Truncated/)
})

test('SHA-256 binds metadata to the exact complete mesh bytes', async () => {
  const buffer = binary()
  const expected = createHash('sha256').update(new Uint8Array(buffer)).digest('hex')
  await verifyTerrainHash(buffer, expected)
  await assert.rejects(verifyTerrainHash(buffer, '0'.repeat(64)), /does not match/)
})

test('file validation and cancellation happen before launching a worker', async () => {
  const mesh = new File([ascii()], 'test.ply')
  const metadata = new File([JSON.stringify(manifest)], 'test.json')
  const controller = new AbortController()
  await assert.rejects(importTerrain(new File(['x'], 'wrong.vtk'), metadata, controller.signal), /surface .ply/)
  await assert.rejects(importTerrain(mesh, new File(['x'], 'wrong.txt'), controller.signal), /metadata .json/)
  await assert.rejects(importTerrain(new File([], 'empty.ply'), metadata, controller.signal), /non-empty/)
  await assert.rejects(importTerrain(new File([new ArrayBuffer(MAX_TERRAIN_BYTES + 1)], 'too-big.ply'), metadata, controller.signal), /32 MiB/)
  await assert.rejects(importTerrain(mesh, new File([' '.repeat(16 * 1024 + 1)], 'too-big.json'), controller.signal), /16 KiB/)
  controller.abort()
  await assert.rejects(importTerrain(mesh, metadata, controller.signal), { name: 'AbortError' })
})
