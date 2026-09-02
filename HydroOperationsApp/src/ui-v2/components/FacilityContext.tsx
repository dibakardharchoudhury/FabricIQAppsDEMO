import { Factory } from 'lucide-react'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function FacilityContext() {
  const { facilities, selectedFacilityId, setSelectedFacilityId } = useHydroOperationsData()
  if (facilities.length < 2) return null

  return <section className="v2-facility-context" aria-label="Facility selector">
    {facilities.map(facility => {
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
    })}
  </section>
}