import type { Equipment, Facility } from '../../../services/fabric'

export type DigitalTwinAssetNode = {
  kind: 'asset'
  id: string
  label: string
  equipment: Equipment
}

export type DigitalTwinStationNode = {
  kind: 'station'
  id: string
  label: string
  facility: Facility
  children: DigitalTwinAssetNode[]
}

export type DigitalTwinTreeSource = {
  facilities: Facility[]
  equipment: Equipment[]
}

const byLabel = (a: { label: string }, b: { label: string }) => a.label.localeCompare(b.label, undefined, { numeric: true })

/** Station -> asset. Assets remain visible even when model or signal metadata is unavailable. */
export function buildDigitalTwinTree({ facilities, equipment }: DigitalTwinTreeSource): DigitalTwinStationNode[] {
  const equipmentByFacility = new Map<string, Equipment[]>()
  for (const asset of equipment) {
    const group = equipmentByFacility.get(asset.facility_id)
    if (group) group.push(asset)
    else equipmentByFacility.set(asset.facility_id, [asset])
  }

  return facilities
    .map(facility => ({
      kind: 'station' as const,
      id: facility.facility_id,
      label: facility.facility_name || facility.facility_id,
      facility,
      children: (equipmentByFacility.get(facility.facility_id) ?? [])
        .map(asset => ({
          kind: 'asset' as const,
          id: asset.equipment_id,
          label: asset.tag ?? asset.equipment_id,
          equipment: asset,
        }))
        .sort(byLabel),
    }))
    .filter(station => station.children.length)
    .sort(byLabel)
}

export function pathToAsset(stations: DigitalTwinStationNode[], assetId?: string): string[] {
  if (!assetId) return []
  const station = stations.find(item => item.children.some(asset => asset.id === assetId))
  return station ? [station.id] : []
}