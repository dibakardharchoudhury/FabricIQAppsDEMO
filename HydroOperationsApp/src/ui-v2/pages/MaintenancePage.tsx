import { FacilityContext } from '../components/FacilityContext'
import { PlaceholderCard } from '../components/PlaceholderCard'

export function MaintenancePage() {
  return <div className="v2-domain-page">
    <FacilityContext />
    <PlaceholderCard title="Work Orders & Maintenance" />
  </div>
}