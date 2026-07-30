# Hydro Operations Fabric App

A React, Leaflet, and Rayfin application for hydropower monitoring and maintenance in Microsoft Fabric.

## Data ownership

The app does not copy Hydro reference or telemetry data.

- Lakehouse: authoritative `silver_facilities`, `silver_systems`, `silver_equipment`, `silver_instruments`, and `silver_signal_master` metadata, exposed without copying through the Fabric `Hydro_STID_API` GraphQL item.
- Eventhouse: authoritative `OPCUAEvents(event_time, opcua_node_id, value, quality)` telemetry.
- Rayfin SQL: only mutable app records such as work orders, notifications, acknowledgements, notes, stream runs, and shift handovers.

Rayfin records reference Lakehouse/Eventhouse objects by `equipmentId`, `instrumentId`, and `opcuaNodeId`.

## Local preview

```powershell
npm install
npm run typecheck
npm run lint
npx vite
```

Without Fabric environment values, the app shows explicit disconnected states. It does not generate representative source values or Data Agent answers.

## Fabric configuration

Use Node 24 for Rayfin CLI commands. Copy `rayfin/.env.example` to `rayfin/.env` and populate the GraphQL endpoint, KQL cluster/database, Entra SPA client ID, optional Data Agent MCP URL, stream pipeline item GUID, and workspace name.

The SPA app registration needs delegated `GraphQLApi.Execute.All`, Azure Data Explorer, and Fabric API access. Users grant GraphQL access interactively and also need the corresponding Fabric workspace and KQL database permissions. No client secret or password sign-in is used.

```powershell
npm run rayfin:db
npm run rayfin:up
```

## Zero-copy composed reads

Fabric exposes the Rayfin SQL database and Lakehouse SQL analytics endpoint through separate endpoints, so cross-database views are not supported. `Hydro_STID_API` provides governed reads over the existing Lakehouse SQL analytics endpoint. The app queries each authoritative store independently and joins records in the application by `equipmentId`, `instrumentId`, and `opcuaNodeId`; no reference or telemetry rows are replicated into Rayfin SQL.

Eventhouse is queried separately and merged by `opcua_node_id`, avoiding a false cross-store GraphQL relationship.

The map uses only the facility-level latitude and longitude present in `silver_facilities`. The source has no device coordinates, engineering geometry, turbine subtype, or 3D model, so the app does not infer them.
