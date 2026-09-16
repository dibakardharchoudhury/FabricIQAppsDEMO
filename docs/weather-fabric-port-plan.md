# Weather data to Fabric porting plan

## Existing implementation

The code in `weather/` is a local Aurora proof of concept split into four stages:

1. `list_aurora_collection.py` and `list_gridhd_data.py` call Microsoft weather STAC endpoints with an API key in the query string. They inspect collection and item metadata but do not paginate.
2. `download_latest_aurora_tp.py` requests one signed Aurora item, opens its Azure Blob Zarr asset with `adlfs` and `xarray`, selects `scaled_total_precipitation_1h_0`, and writes 29 global CSV grids at six-hour intervals through 168 hours.
3. `extract_aurora_tp_timeseries.py` finds the nearest grid cell for three points, handles 0-360 longitudes, reverses Aurora's log precipitation transform, and writes a point time series.
4. Plot and animation scripts consume the CSV files for local visualization.

Important findings:

- The downloader labels raw transformed values as metres. Physical precipitation is only recovered later with `exp(value + log(0.001)) - 0.001`. Fabric must decode before persistence and store canonical millimetres.
- The latest-item request relies on an implicit API order. The Fabric adapter requests `sortby=-datetime` explicitly.
- HTTP calls use timeouts but no retry, rate-limit, or `Retry-After` handling. The Fabric adapter retries 429 and transient 5xx responses.
- The local flow materializes full global grids as CSV, then rereads them. Fabric reads signed Zarr lazily and persists only relevant point and area results plus raw STAC lineage.
- API keys come from environment variables locally. Fabric retrieves the key from Azure Key Vault with `notebookutils.credentials.getSecret`.
- Required non-default packages are `xarray`, `zarr<3`, `adlfs`, `shapely`, and `pyproj`. Pin them in a Fabric Environment rather than installing them on every scheduled run.

## Target architecture

Fabric calls this storage item a Lakehouse. The three notebooks are deployable Fabric Git items:

- `Weather_001_create_lakehouse` initializes source-neutral Delta dimensions, facts, and audit tables in an attached Lakehouse.
- `Weather_002_fetch_area_weather` implements a 72-hour Aurora adapter for precipitation, surface pressure, temperature, relative humidity, dew point, solar radiation, average wind speed, wind gust and wind direction, and maintains facility operating-area geometry.
- `Weather_003_fetch_ukmet` implements UKMet Global Spot forecasts and Land Observations for the same facility locations and canonical tables.
- `Weather_020_area_calculations` reads canonical forecasts after all adapters finish and rebuilds area metrics independently by vendor, variable, issue, valid time, and forecast type.

Logical layers:

- Bronze: immutable STAC item JSON under `Files/weather/bronze/stac/<collection>/` and, for future JSON APIs, original response pages.
- Silver: canonical `weather_observations` and `weather_forecasts`, normalized to UTC, WGS84, and canonical units.
- Gold: `weather_area_metrics` with overlap-weighted depth, volume, coverage, method, and source lineage.

The model separates source, variable, location, area, ingestion run, observations, forecasts, and area metrics. Forecast and observation facts are date-partitioned. Delta merge keys are documented in the setup notebook and make reruns idempotent.

## Area calculation

The shared aggregation stage uses each area's representative facility location so every point-based vendor follows one contract. Scalar values use the representative-location mean, gusts use maximum, and wind direction uses a circular mean. Rows remain separate by `source_id` and `forecast_type`; vendors and deterministic/ensemble products are never combined.

Convert representative rainfall depth to area rainfall volume:

$$
V = \frac{R}{1000} A
$$

where $R$ is millimetres and $A$ is the geodesic area in square metres. The stored aggregation method makes this representative-point approximation explicit. A future gridded aggregate can use cell overlap weighting as another method without changing the canonical metric key.

## Multi-source adapter contract

Each additional adapter should:

1. Discover pages/items using explicit date, bounding-box, and forecast-horizon parameters.
2. Save raw metadata or response pages in Bronze without secrets or signed URL query strings in logs.
3. Normalize records to the observation or forecast schema while preserving source item IDs and run IDs.
4. Convert units centrally, validate ranges and coordinates, and reject unknown variable mappings.
5. Merge by the canonical natural key and finish the ingestion audit record.

Implement GridHD next from collection `mai-gridhd-eu-core-v1.2` after confirming its variables and temporal semantics. MAI adapters use the shared `mai-weather-api-key` Key Vault secret. UKMet Global Spot and Land Observations use `ukmet-global-spot-api-key` and `ukmet-land-observations-api-key`; all secret values remain in Key Vault.

## Incremental loading and quality

- Use the latest successful source reference time as a watermark, but allow a configurable replay window for corrected forecasts and delayed observations.
- Validate UTC timestamps, latitude/longitude ranges, nonnegative precipitation, expected lead hours, finite values, duplicate natural keys, and plausible source-specific ranges.
- Mark a run `started`, then `succeeded` or `failed`; production hardening should wrap execution so exceptions also update the audit row.
- Preserve forecast issues rather than overwriting older issues. Consumers can select the latest issue per valid time.
- Keep API endpoint and secret name as pipeline parameters; keep the secret value only in Key Vault.

## Orchestration and tests

1. Attach the same Lakehouse and Fabric Environment to all three notebooks.
2. Run the setup notebook once per environment. `03_Pipe_Weather` then runs Aurora, UKMet, and `Weather_020_area_calculations` every four hours in that order.
3. Load points from `silver_facilities`, optionally filter by facility IDs and active equipment, and generate one geodesic 20 km aggregation area per station. Pass horizon, interval, endpoint, Key Vault URI, and secret name as notebook parameters.
4. Add retry policy and alerts at the pipeline level in addition to HTTP retries.
5. Unit-test precipitation decoding, longitude wrapping, nearest-cell selection, polygon validation, overlap area, depth-to-volume conversion, and merge-key deduplication.
6. Integration-test with a fixed small STAC item and a small polygon before enabling the global latest-item schedule.

## Delivery sequence

1. Create or attach a Weather Lakehouse and configure the Fabric Environment dependencies.
2. Store the Aurora and UKMet API keys in Key Vault and grant the pipeline run identity secret-read access.
3. Run `Weather_001_create_lakehouse` and verify all eight tables.
4. Run `Weather_002_fetch_area_weather` with one point and one small polygon; compare the point result with the local extractor.
5. Validate area coverage, weighted millimetres, and cubic metres against an independently calculated sample.
6. Run `Weather_003_fetch_ukmet`, then `Weather_020_area_calculations`, and verify separate Aurora/UKMet metrics for every forecast type. Provisioning creates and enables the four-hour `03_Pipe_Weather` schedule.
7. Add a GridHD adapter after confirming its product semantics.