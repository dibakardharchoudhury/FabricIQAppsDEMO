import { Factory, Gauge } from 'lucide-react'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function FacilityContext() {
  const { facilities, selectedFacilityId, setSelectedFacilityId, facilityEquipment, selectedAssetId, setSelectedAssetId } = useHydroOperationsData()
  if (!facilities.length) return null

  return <section className="v2-facility-context" aria-label="Facility selector">
    <div className="v2-facility-options">{facilities.map(facility => {
        const active = facility.facility_id === selectedFacilityId
        return <button
          key={facility.facility_id}
          type="button"
          className={active ? 'v2-facility-chip active' : 'v2-facility-chip'}
          onClick={() => setSelectedFacilityId(facility.facility_id)}
          aria-pressed={active}
        >
          <Factory size={14} />
          <span><strong>{facility.facility_name}</strong><small>{facility.facility_id}</small></span>
        </button>
      })}</div>
    <label className="v2-context-asset"><Gauge size={15} /><span>Selected turbine</span><select value={selectedAssetId ?? ''} onChange={event => setSelectedAssetId(event.target.value)}>{facilityEquipment.map(asset => <option key={asset.equipment_id} value={asset.equipment_id}>{asset.tag ?? asset.equipment_id}</option>)}</select></label>
  </section>
}