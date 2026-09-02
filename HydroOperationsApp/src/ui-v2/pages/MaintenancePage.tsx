import { useMemo, useState } from 'react'
import { AlertTriangle, ClipboardCheck, Package, Plus, Trash2, Wrench } from 'lucide-react'
import { FacilityContext } from '../components/FacilityContext'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

const statuses = ['Draft', 'Approved', 'Planned', 'Scheduled', 'Ready', 'In progress', 'On hold', 'Completed', 'Cancelled']
type Scope = 'asset' | 'facility' | 'all'

export function MaintenancePage() {
  const data = useHydroOperationsData()
  const [scope, setScope] = useState<Scope>('facility')
  const [assetId, setAssetId] = useState<string>()
  const selectedAsset = data.facilityEquipment.find(asset => asset.equipment_id === assetId) ?? data.facilityEquipment[0]
  const visibleOrders = useMemo(() => data.orders.filter(order => {
    if (scope === 'all') return true
    if (scope === 'asset') return order.equipmentId === selectedAsset?.equipment_id
    return data.facilityEquipment.some(asset => asset.equipment_id === order.equipmentId)
  }), [data.facilityEquipment, data.orders, scope, selectedAsset])
  const inspections = data.inspections.filter(item => item.equipmentId === selectedAsset?.equipment_id)
  const notifications = data.notifications.filter(item => item.equipmentId === selectedAsset?.equipment_id)
  const lowStock = data.spareParts.filter(part => part.quantityOnHand <= part.reorderLevel)

  return <div className="v2-domain-page">
    <FacilityContext />
    {data.notice && <div className="v2-notice"><AlertTriangle size={15} /><span>{data.notice}</span></div>}
    <section className="v2-page-head"><div><span className="v2-eyebrow">Maintenance Operations</span><h1>Work orders and maintenance</h1><p>Manage operational work and inspect readiness records from Rayfin SQL.</p></div><div className="v2-page-metrics"><strong>{data.facilityOpenOrders.length}</strong><span>open in facility</span><strong>{lowStock.length}</strong><span>parts below reorder</span></div></section>

    <section className="v2-maintenance-toolbar">
      <div className="v2-range-control" role="group" aria-label="Work order scope">{(['asset', 'facility', 'all'] as Scope[]).map(item => <button key={item} type="button" className={scope === item ? 'active' : ''} onClick={() => setScope(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}</div>
      <label><span>Selected asset</span><select value={selectedAsset?.equipment_id ?? ''} onChange={event => setAssetId(event.target.value)}>{data.facilityEquipment.map(asset => <option key={asset.equipment_id} value={asset.equipment_id}>{asset.tag ?? asset.equipment_id}</option>)}</select></label>
      <button className="v2-primary-action" type="button" disabled={!selectedAsset || Boolean(data.mutationKey)} onClick={() => selectedAsset && void data.actions.addWorkOrder(selectedAsset.equipment_id)}><Plus size={15} />Create work order</button>
    </section>

    <section className="v2-maintenance-grid">
      <article className="v2-data-panel v2-orders-panel"><div className="v2-panel-headline"><span className="v2-eyebrow">Work Queue</span><h2>{visibleOrders.length} work orders</h2></div>
        {data.operationsState !== 'connected' ? <Empty text="Sign in through Administration to load operational records." /> : !visibleOrders.length ? <Empty text="No work orders match this scope." /> : <div className="v2-order-list">{visibleOrders.map(order => <div className="v2-order-row" key={order.id}><span className={`v2-priority ${order.priority.toLowerCase()}`}><Wrench size={15} /></span><div><strong>{order.title}</strong><small>{order.workOrderNumber} · {order.equipmentId}</small></div><span className={`v2-priority-label ${order.priority.toLowerCase()}`}>{order.priority}</span><select aria-label={`Status for ${order.workOrderNumber}`} value={order.status} disabled={data.mutationKey === order.id} onChange={event => void data.actions.changeWorkOrderStatus(order.id, event.target.value)}>{(statuses.includes(order.status) ? statuses : [order.status, ...statuses]).map(status => <option key={status}>{status}</option>)}</select><button className="v2-icon-action danger" type="button" title="Delete work order" disabled={data.mutationKey === order.id} onClick={() => void data.actions.removeWorkOrder(order.id)}><Trash2 size={14} /></button></div>)}</div>}
      </article>

      <article className="v2-data-panel"><div className="v2-panel-headline"><span className="v2-eyebrow">Inspections</span><h2>{selectedAsset?.tag ?? 'Selected asset'}</h2></div>{!inspections.length ? <Empty text="No inspections for this asset." /> : <div className="v2-record-list">{inspections.map(item => <div key={item.id}><ClipboardCheck size={15} /><span><strong>{item.inspectionType}</strong><small>{new Date(item.inspectedAt).toLocaleDateString()} · {item.result}</small><p>{item.findings ?? 'No findings recorded.'}</p></span></div>)}</div>}</article>
      <article className="v2-data-panel"><div className="v2-panel-headline"><span className="v2-eyebrow">Notifications</span><h2>Asset alerts</h2></div>{!notifications.length ? <Empty text="No notifications for this asset." /> : <div className="v2-record-list">{notifications.map(item => <div key={item.id}><AlertTriangle size={15} /><span><strong>{item.summary}</strong><small>{new Date(item.reportedAt).toLocaleDateString()} · {item.status} · {item.severity}</small></span></div>)}</div>}</article>
    </section>

    <section className="v2-data-panel"><div className="v2-panel-headline"><span className="v2-eyebrow">Inventory</span><h2>Spare parts readiness</h2></div>{!data.spareParts.length ? <Empty text="No spare-parts inventory loaded." /> : <div className="v2-parts-table"><div className="head"><span>Part</span><span>Category</span><span>Location</span><span>On hand / reorder</span></div>{data.spareParts.map(part => { const isLow = part.quantityOnHand <= part.reorderLevel; return <div className={isLow ? 'low' : ''} key={part.id}><span><Package size={14} /><strong>{part.name}</strong><small>{part.partNumber}</small></span><span>{part.category}</span><span>{part.storageLocation}</span><span><strong>{part.quantityOnHand}</strong> / {part.reorderLevel}{isLow && <AlertTriangle size={13} />}</span></div> })}</div>}</section>
  </div>
}

function Empty({ text }: { text: string }) { return <div className="v2-inline-empty">{text}</div> }