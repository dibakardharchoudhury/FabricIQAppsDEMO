import { useMemo } from 'react'
import type { Instrument, TelemetryReading } from '../../services/fabric'
import { FacilityContext } from '../components/FacilityContext'
import { SelectedSignalCards, SelectedSignalPanel } from '../components/telemetry/SelectedSignalPanel'
import { TelemetryDetail } from '../components/telemetry/TelemetryDetail'
import { TelemetryEmptyPanel, type Tone } from '../components/telemetry/TelemetryCards'
import { TelemetryFilterBar } from '../components/telemetry/TelemetryFilterBar'
import { TelemetryStatusBar, TelemetryStatusCards, type TelemetryStatusSummary } from '../components/telemetry/TelemetryStatusBar'
import { TelemetryToolbar } from '../components/telemetry/TelemetryToolbar'
import { TelemetryTree } from '../components/telemetry/TelemetryTree'
import { SignalTablePanel } from '../components/telemetry/SignalTablePanel'
import { buildTelemetryTree, pathToSignal } from '../components/telemetry/telemetryTreeModel'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'
import { useTelemetryExplorerMode, useTreeExpansion } from '../hooks/useTelemetryExplorerMode'
import { useTelemetryHistory } from '../hooks/useTelemetryHistory'

const issueQualities = new Set(['bad', 'uncertain'])

const qualityTone = (reading?: TelemetryReading): Tone => {
  const quality = reading?.quality?.toLowerCase()
  if (!quality) return 'muted'
  return quality === 'bad' ? 'bad' : quality === 'uncertain' ? 'warn' : 'good'
}

export function TelemetryPage() {
  const data = useHydroOperationsData()
  const { mode, setMode } = useTelemetryExplorerMode()
  const { assetId: selectedAssetId, signalId: selectedSignalId, range } = data.telemetryExplorerSelection

  const asset = data.facilityEquipment.find(item => item.equipment_id === selectedAssetId) ?? data.facilityEquipment[0]
  const signals = useMemo<Instrument[]>(
    () => data.facilityInstruments.filter(item => item.equipment_id === asset?.equipment_id),
    [data.facilityInstruments, asset],
  )
  const signal = signals.find(item => item.instrument_id === selectedSignalId) ?? signals[0]

  const readings = useMemo(() => new Map(data.telemetry.map(item => [item.opcuaNodeId, item])), [data.telemetry])
  const readingOf = (opcuaNodeId: string) => readings.get(opcuaNodeId)
  const history = useTelemetryHistory(signal?.opcua_node_id, range)

  const stations = useMemo(() => data.stid ? buildTelemetryTree(data.stid) : [], [data.stid])
  const revealPath = useMemo(() => pathToSignal(stations, signal?.instrument_id), [stations, signal])
  const expansion = useTreeExpansion(revealPath)

  const selectedAssetTelemetry = data.mappedTelemetry.filter(item => item.instrument?.equipment_id === asset?.equipment_id)
  const qualityIssueCount = selectedAssetTelemetry.filter(item => issueQualities.has((item.reading.quality ?? '').toLowerCase())).length

  const blocker = data.stidState !== 'connected'
    ? { title: 'STID not connected', text: 'Use Administration to connect STID before exploring telemetry by asset and signal.' }
    : data.telemetryState !== 'connected'
      ? { title: 'Telemetry not connected', text: 'Use Administration to connect telemetry before viewing latest readings and history.' }
      : !data.facilityEquipment.length
        ? { title: 'No assets for this facility', text: 'The selected facility has no STID equipment records.' }
        : undefined

  const status: TelemetryStatusSummary = {
    telemetryStatus: data.telemetryStatus,
    telemetryStatusLabel: data.telemetryStatusLabel,
    telemetryAgeLabel: data.telemetryAgeLabel,
    liveSignalCount: selectedAssetTelemetry.length,
    assetLabel: asset?.tag ?? 'Selected turbine',
    qualityIssueCount,
  }
  const latest = signal ? readingOf(signal.opcua_node_id) : undefined
  const treeMode = mode === 'tree'

  const detail = <TelemetryDetail
    signal={signal}
    signals={signals}
    history={history}
    range={range}
    summary={treeMode
      // Tree mode drops the standalone status bar, so both card sets share one wrapping row.
      ? <section className="v2-telemetry-cards"><TelemetryStatusCards {...status} /><SelectedSignalCards signal={signal} latest={latest} /></section>
      : <SelectedSignalPanel signal={signal} latest={latest} />}
  >
    {/* The tree already lists every signal, so the table is filter-mode only. */}
    {!treeMode && <SignalTablePanel
      signals={signals}
      selectedSignalId={signal?.instrument_id}
      readingOf={readingOf}
      onSelectSignal={signalId => data.actions.updateTelemetryExplorerSelection({ signalId })}
    />}
  </TelemetryDetail>

  return <div className={`v2-domain-page v2-telemetry-page${treeMode ? ' is-wide' : ''}`}>
    {/* The tree already carries station and turbine selection. */}
    {!treeMode && <FacilityContext />}

    <TelemetryToolbar
      mode={mode}
      onModeChange={setMode}
      range={range}
      onRangeChange={next => data.actions.updateTelemetryExplorerSelection({ range: next })}
      filters={!treeMode && !blocker ? <TelemetryFilterBar
        assets={data.facilityEquipment}
        assetId={asset?.equipment_id}
        onAssetChange={id => data.actions.updateTelemetryExplorerSelection({ assetId: id, signalId: undefined })}
        signals={signals}
        signalId={signal?.instrument_id}
        onSignalChange={signalId => data.actions.updateTelemetryExplorerSelection({ signalId })}
      /> : undefined}
    />

    {!treeMode && <TelemetryStatusBar {...status} />}

    {blocker ? <TelemetryEmptyPanel title={blocker.title} text={blocker.text} />
      : treeMode
        ? <div className="v2-telemetry-layout">
            <TelemetryTree
              stations={stations}
              selectedSignalId={signal?.instrument_id}
              handlers={{
                isExpanded: expansion.isExpanded,
                onToggle: expansion.toggle,
                onSelectSignal: (treeAssetId, treeSignalId, facilityId) => data.actions.selectTelemetrySignal(facilityId, treeAssetId, treeSignalId),
                toneOf: instrument => qualityTone(readingOf(instrument.opcua_node_id)),
              }}
            />
            <div className="v2-telemetry-detail-column">{detail}</div>
          </div>
        : detail}
  </div>
}
