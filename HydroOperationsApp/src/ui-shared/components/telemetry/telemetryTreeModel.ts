import type { Equipment, Facility, Instrument } from '../../../services/fabric'

export type TelemetrySignalNode = {
  kind: 'signal'
  id: string
  label: string
  instrument: Instrument
}

export type TelemetrySensorNode = {
  kind: 'sensor'
  id: string
  label: string
  children: TelemetrySignalNode[]
}

export type TelemetryAssetNode = {
  kind: 'asset'
  id: string
  label: string
  equipment: Equipment
  children: TelemetrySensorNode[]
}

export type TelemetryStationNode = {
  kind: 'station'
  id: string
  label: string
  facility: Facility
  children: TelemetryAssetNode[]
}

export type TelemetryTreeNode = TelemetryStationNode | TelemetryAssetNode | TelemetrySensorNode | TelemetrySignalNode

export type TelemetryTreeSource = {
  facilities: Facility[]
  equipment: Equipment[]
  instruments: Instrument[]
}

const byLabel = (a: { label: string }, b: { label: string }) => a.label.localeCompare(b.label, undefined, { numeric: true })
const sensorLabel = (instrumentType?: string) => instrumentType ? `${instrumentType.charAt(0).toUpperCase()}${instrumentType.slice(1)}` : 'Other'

function groupBy<T, K>(items: T[], key: (item: T) => K): Map<K, T[]> {
  const groups = new Map<K, T[]>()
  for (const item of items) {
    const group = groups.get(key(item))
    if (group) group.push(item)
    else groups.set(key(item), [item])
  }
  return groups
}

function buildSensors(instruments: Instrument[], assetId: string): TelemetrySensorNode[] {
  const groups = groupBy(instruments, instrument => instrument.instrument_type ?? '')
  return [...groups].map(([instrumentType, group]) => ({
    kind: 'sensor' as const,
    id: `${assetId}::${instrumentType || 'other'}`,
    label: sensorLabel(instrumentType),
    children: group
      .map(instrument => ({
        kind: 'signal' as const,
        id: instrument.instrument_id,
        label: instrument.tag ?? instrument.instrument_id,
        instrument,
      }))
      .sort(byLabel),
  })).sort(byLabel)
}

/** Station -> asset -> sensor type -> signal. Empty branches are dropped so the tree only shows navigable leaves. */
export function buildTelemetryTree({ facilities, equipment, instruments }: TelemetryTreeSource): TelemetryStationNode[] {
  const equipmentByFacility = groupBy(equipment, item => item.facility_id)
  const instrumentsByEquipment = groupBy(instruments, item => item.equipment_id)

  return facilities
    .map(facility => ({
      kind: 'station' as const,
      id: facility.facility_id,
      label: facility.facility_name || facility.facility_id,
      facility,
      children: (equipmentByFacility.get(facility.facility_id) ?? [])
        .map(item => ({
          kind: 'asset' as const,
          id: item.equipment_id,
          label: item.tag ?? item.equipment_id,
          equipment: item,
          children: buildSensors(instrumentsByEquipment.get(item.equipment_id) ?? [], item.equipment_id),
        }))
        .filter(asset => asset.children.length)
        .sort(byLabel),
    }))
    .filter(station => station.children.length)
    .sort(byLabel)
}

/** Ancestor ids of a signal, so the tree can reveal the active selection without the caller knowing the shape. */
export function pathToSignal(stations: TelemetryStationNode[], signalId?: string): string[] {
  if (!signalId) return []
  for (const station of stations) {
    for (const asset of station.children) {
      for (const sensor of asset.children) {
        if (sensor.children.some(signal => signal.id === signalId)) return [station.id, asset.id, sensor.id]
      }
    }
  }
  return []
}

export function countSignals(node: TelemetryStationNode | TelemetryAssetNode | TelemetrySensorNode): number {
  if (node.kind === 'sensor') return node.children.length
  return node.children.reduce((total, child) => total + countSignals(child), 0)
}
