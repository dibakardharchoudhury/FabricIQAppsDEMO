import { CopilotExperience } from '../../components/CopilotExperience'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function CopilotPage() {
  const data = useHydroOperationsData()
  return <CopilotExperience
    messages={data.messages}
    busy={data.copilotBusy}
    selectedFacilityId={data.selectedFacility?.facility_id}
    facilities={data.facilities.map(facility => ({ id: facility.facility_id, name: facility.facility_name }))}
    assets={data.facilityEquipment.map(asset => ({ id: asset.equipment_id, label: asset.tag ?? asset.equipment_id }))}
    selectedAssetId={data.selectedAssetId}
    onSelectFacility={data.setSelectedFacilityId}
    onSelectAsset={data.setSelectedAssetId}
    onSend={question => void data.actions.sendCopilotQuestion(question)}
    onReset={data.actions.resetCopilot}
  />
}