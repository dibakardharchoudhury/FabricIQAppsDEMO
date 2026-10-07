import { queryStid } from '../fabric'
import { createWorkOrder, initializeRayfin, listWorkOrders, type AppUser, type WorkOrderRecord } from '../rayfin'
import { createApprovalStore, type ProposalEdits } from './approvalStore'
import type { WorkOrderProposal } from './orchestration'

export const workOrderApprovals = createApprovalStore<WorkOrderRecord>()

export async function validateWorkOrderTarget(proposal: WorkOrderProposal) {
  const data = await queryStid()
  if (!data?.equipment.some(asset => asset.equipment_id === proposal.equipmentId)) {
    throw new Error('The proposed equipment could not be verified in the current workspace.')
  }
  if ((proposal.instrumentId || proposal.opcuaNodeId) && !data.instruments.some(instrument =>
    instrument.equipment_id === proposal.equipmentId
    && (!proposal.instrumentId || instrument.instrument_id === proposal.instrumentId)
    && (!proposal.opcuaNodeId || instrument.opcua_node_id === proposal.opcuaNodeId))) {
    throw new Error('The proposed signal does not belong to the selected equipment.')
  }
}

export async function approveWorkOrder(id: string, edits: ProposalEdits) {
  let user: AppUser | null = null
  return workOrderApprovals.approve(id, edits, async proposal => {
    user = await initializeRayfin()
    if (!user) throw new Error('Sign in to the operational database before approving this draft.')
    await validateWorkOrderTarget(proposal)
    const open = (await listWorkOrders()).filter(order => order.equipmentId === proposal.equipmentId
      && !['completed', 'cancelled'].includes(order.status.toLowerCase()))
    if (open.some(order => order.title.trim().toLowerCase() === proposal.title.trim().toLowerCase())) {
      throw new Error('An open work order with this title already exists for this equipment. Review it before creating another.')
    }
  }, proposal => {
    if (!user) throw new Error('The operational database identity was not verified.')
    return createWorkOrder(user, proposal)
  })
}
