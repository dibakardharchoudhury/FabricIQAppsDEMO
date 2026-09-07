# Digital Twin Tree View / Filter Plan

## Goal

Add the same **Tree / Filter** browsing choice used by Real Time Telemetry to the Digital Twin screen.

- **Filter mode** keeps the current facility tabs and asset dropdown.
- **Tree mode** replaces those selectors with a left-hand hierarchy and keeps the selected asset's summary and 3D model on the right.
- The selected facility and asset remain synchronized when switching modes or moving between Telemetry and Digital Twin.

This document is the implementation plan only. No application code is changed as part of this planning step.

## Current behavior

### Telemetry reference

Telemetry already provides the target interaction pattern:

- `src/ui-shared/components/telemetry/TelemetryViewToggle.tsx` renders the Tree / Filter segmented control.
- `src/ui-shared/hooks/useTelemetryExplorerMode.ts` persists the selected mode and manages expanded tree nodes.
- `src/ui-shared/components/telemetry/telemetryTreeModel.ts` builds a sorted Station → Asset → Sensor type → Signal hierarchy.
- `src/ui-shared/components/telemetry/TelemetryTree.tsx` renders the accessible expandable tree panel.
- `src/ui-shared/pages/TelemetryPage.tsx` hides facility/filter selectors in tree mode, permits cross-facility selection, and displays the tree beside the detail content.
- `src/ui-shared/styles/app.css` contains reusable toggle, tree-row, wide-layout, sticky-panel, and responsive rules.

### Digital Twin today

`src/ui-shared/pages/DigitalTwinPage.tsx` currently:

1. Uses the shared `FacilityContext` tabs.
2. Uses a single asset dropdown scoped to the selected facility.
3. Derives instrument readings, health, model metadata, and the 3D viewer from `data.selectedAsset`.
4. Has no browsing-mode state or hierarchical asset navigator.

The detail unit on this screen is an **asset**, not an individual signal. Therefore the Digital Twin tree should select assets directly rather than add signal leaves that cannot change the displayed detail.

## Proposed UX

### Shared toolbar

Add a Digital Twin toolbar immediately below the page heading/context position:

- Left: **Tree / Filter** segmented control, matching Telemetry labels, icons, order, active styling, and keyboard behavior.
- Filter mode: show the existing asset selector in the same toolbar.
- Tree mode: show only the mode control; the tree owns facility and asset selection.

Persist the Digital Twin mode independently from Telemetry under a dedicated local-storage key. Default to `filter` to preserve existing behavior for current users.

### Filter mode

Preserve the current screen with minimal visual movement:

- Show `FacilityContext`.
- Show Tree / Filter toggle and asset dropdown.
- Keep summary cards, 3D model, health legend, expand action, fallbacks, and model metadata unchanged.

### Tree mode

Use a two-column layout equivalent to Telemetry:

- Left: sticky **Asset tree** panel.
- Right: the existing selected-asset summary and 3D model panel.
- Hide `FacilityContext` and the asset dropdown because they duplicate the tree.
- Remove the normal reading-width cap so the model retains enough horizontal space.
- On narrow screens, stack the tree above the detail and cap its height, following Telemetry's responsive behavior.

Tree shape:

```text
Station
├─ Turbine / asset
├─ Turbine / asset
└─ Turbine / asset
```

Tree behavior:

- Station rows expand/collapse and display their asset count.
- Asset rows are leaves and select the Digital Twin asset.
- The selected asset row uses `aria-current` and the existing active-row styling.
- The active asset's station is expanded by default.
- User collapse/expand choices override automatic expansion during the current mount.
- Stations and assets sort with the same locale-aware numeric ordering used by Telemetry.
- Assets are shown even when model metadata or instruments are missing; selecting one must expose the existing detailed fallback message rather than silently remove the asset from navigation.
- A compact status dot may represent aggregate asset health using the existing `twinStatus()` precedence. The accessible row text/metadata must also communicate status so color is not the only indicator.

## State and data flow

### Mode state

Create a Digital Twin-specific mode hook with the same contract as `useTelemetryExplorerMode()`:

- Type: `'tree' | 'filter'`.
- Storage key: `hydro.digital-twin.explorer-mode.v1`.
- Safe fallback when storage is unavailable.

Move the generic `useTreeExpansion()` implementation out of the telemetry-specific hook into a shared hook module, then update Telemetry and Digital Twin to import it. This avoids Digital Twin depending on a telemetry-named module without changing behavior.

### Cross-facility asset selection

`setSelectedAssetId()` is intentionally scoped to the currently selected facility, so it cannot safely handle a tree click in another station. Add an atomic controller action such as:

```text
selectAsset(facilityId, assetId)
```

The action will:

1. Validate that the facility and asset relationship exists in current STID data.
2. Set the selected facility.
3. Store the asset under that facility in `selectedAssetIds`.
4. Persist both values through the existing setup persistence path.

Telemetry's `selectTelemetrySignal()` already demonstrates why facility and asset must move together. The new asset-level action should be reusable by Digital Twin and should not alter the Telemetry signal selection.

### Tree model

Create a focused Digital Twin tree model rather than overloading the signal-oriented Telemetry model:

- `DigitalTwinStationNode`: facility plus asset children.
- `DigitalTwinAssetNode`: equipment leaf.
- `buildDigitalTwinTree({ facilities, equipment })`.
- `pathToAsset(stations, assetId)` returning the ancestor station ID.
- Optional helpers for station asset count and aggregate health metadata.

Use all STID facilities and equipment as input, not only `facilityEquipment`, so Tree mode can navigate the complete estate.

## Planned file changes

### New files

1. `HydroOperationsApp/src/ui-shared/components/digitalTwin/digitalTwinTreeModel.ts`
   - Pure Station → Asset model builder, sorting, and active-path lookup.
2. `HydroOperationsApp/src/ui-shared/components/digitalTwin/DigitalTwinTree.tsx`
   - Expandable station branches and selectable asset leaves.
3. `HydroOperationsApp/src/ui-shared/components/digitalTwin/DigitalTwinViewToggle.tsx`
   - Digital Twin-labelled Tree / Filter control matching Telemetry.
4. `HydroOperationsApp/src/ui-shared/hooks/useDigitalTwinExplorerMode.ts`
   - Independent persisted mode.
5. `HydroOperationsApp/src/ui-shared/hooks/useTreeExpansion.ts`
   - Shared expansion behavior extracted from the Telemetry hook.

### Modified files

1. `HydroOperationsApp/src/ui-shared/hooks/useTelemetryExplorerMode.ts`
   - Remove the generic expansion implementation after extraction; keep Telemetry mode behavior unchanged.
2. `HydroOperationsApp/src/ui-shared/pages/TelemetryPage.tsx`
   - Update only the `useTreeExpansion` import.
3. `HydroOperationsApp/src/ui-shared/hooks/useHydroOperationsData.ts`
   - Add and expose the atomic cross-facility `selectAsset` action.
4. `HydroOperationsApp/src/ui-shared/pages/DigitalTwinPage.tsx`
   - Build the full asset tree, integrate mode/expansion state, add toolbar, and branch Filter versus Tree layout while retaining one shared detail rendering path.
5. `HydroOperationsApp/src/ui-shared/styles/app.css`
   - Generalize the wide-page selector where practical and add only Digital Twin-specific toolbar/layout rules; reuse existing `.v2-view-toggle` and `.v2-tree-*` styles.

## Implementation sequence

1. **Extract generic expansion state**
   - Move `useTreeExpansion()` and verify Telemetry remains behaviorally identical.
2. **Add Digital Twin mode and toggle**
   - Implement persisted Filter/Tree state and accessible control labels.
3. **Build the asset tree model**
   - Build from all STID facilities/equipment, retain assets without models/signals, sort deterministically, and calculate the reveal path.
4. **Add atomic asset selection**
   - Implement cross-facility selection in the shared data controller with relationship validation and persistence.
5. **Render the Digital Twin tree**
   - Add station branches, asset leaves, selected state, asset counts, health metadata, empty state, and semantic buttons.
6. **Refactor the page layout**
   - Extract/reuse the existing summary and model detail JSX so both modes render exactly the same detail state.
   - Filter mode keeps current selectors; Tree mode renders tree + detail.
7. **Add responsive styling**
   - Reuse Telemetry dimensions and breakpoints; confirm the expanded 3D viewer still overlays correctly from either mode.
8. **Validate and capture evidence**
   - Run static checks and perform visual/keyboard checks in both modes and viewport sizes.

## Accessibility requirements

- Toggle remains a labelled button group with `aria-pressed` on each option.
- Station controls use native buttons and expose `aria-expanded`.
- Asset controls use native buttons and expose `aria-current="true"` for the selected asset.
- All icon-only actions retain accessible names and visible focus styling.
- Health cannot be conveyed only by dot color; include readable status text or an accessible label.
- Keyboard-only users can tab through the toggle, station controls, assets, and model actions without a trap.
- Focus remains predictable after selecting an asset; do not force focus into the 3D viewer.
- Tree and control contrast must meet WCAG 2.1 AA, reusing established theme tokens.

## Empty, loading, and error cases

- STID disconnected: retain the current full-page blocker; do not render an empty tree shell.
- No facilities/assets: retain the current “No assets” blocker.
- Station with no assets: omit it from the tree, consistent with Telemetry's empty-branch behavior.
- Asset with no instruments: keep it selectable and show the current no-instruments detail fallback.
- Asset with no 3D model or unsupported model format: keep it selectable and show the existing fallback.
- Model metadata disconnected: keep tree navigation available; detail shows the current metadata-unavailable state.
- Missing telemetry: render muted/no-data health without preventing asset selection.
- Persisted facility/asset no longer present: rely on the controller's existing first-valid facility/asset fallback; the tree reveal path may be empty until a valid asset resolves.

## Validation plan

### Automated checks

Run with the repository's required Node 24 wrapper:

1. `npm run typecheck`
2. `npm run lint`
3. `npm run build`

The project currently has no React component-test framework configured. Do not add a new test dependency solely for this feature. Keep tree-model logic pure so focused tests can be added later when the project adopts a UI test runner.

### Manual functional checks

1. Digital Twin opens in Filter mode for a user with no stored preference.
2. Tree / Filter choice survives reload and is independent from Telemetry's preference.
3. Filter mode preserves facility tabs, asset dropdown, summary values, model, health legend, model link, and expand/restore behavior.
4. Tree mode hides duplicate facility/asset filters and displays all stations and assets.
5. Selecting an asset in the current station updates summary, model, hotspots, health, and metadata.
6. Selecting an asset in another station atomically updates both facility and asset; switching back to Filter mode shows matching selectors.
7. Switching to Telemetry reflects the shared facility/asset selection without corrupting its selected signal/range.
8. Collapse/expand and active-path reveal behave consistently with Telemetry.
9. Assets without models, unsupported formats, instruments, or telemetry remain selectable and show the correct fallback.
10. Expanded model view opens and restores correctly from both browsing modes.
11. Narrow viewport stacks tree and detail without horizontal overflow.
12. Keyboard and screen-reader semantics match the accessibility requirements above.

### Visual evidence

Capture and store in `docs/img/`:

- Digital Twin Filter mode.
- Digital Twin Tree mode with a station expanded and an asset selected.
- Optional narrow-layout Tree mode if responsive styling changes materially.

## Acceptance criteria

- Digital Twin displays a Tree / Filter control visually consistent with Telemetry.
- Filter mode retains all existing Digital Twin behavior.
- Tree mode shows a Station → Asset navigator beside the unchanged Digital Twin detail.
- A tree selection can cross facilities and updates persisted facility/asset state atomically.
- Active selection is revealed, highlighted, keyboard operable, and screen-reader identifiable.
- Existing Telemetry tree behavior is unchanged after sharing the expansion hook.
- All current empty/model/telemetry fallbacks remain reachable.
- Typecheck, lint, and production build pass.
- Desktop and narrow layouts are visually verified and screenshots are added to `docs/img/`.

## Out of scope

- Changing the 3D model renderer or hotspot interaction.
- Selecting or focusing an individual signal from the Digital Twin tree.
- Adding search, multi-select, or new facility/asset filters.
- Changing STID, Eventhouse, Rayfin, or model metadata contracts.
- Introducing a new UI testing framework.
