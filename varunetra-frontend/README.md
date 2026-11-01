# VARUNETRA Marine Environment Tracking

VARUNETRA is a MapLibre frontend for marine observation and environmental tracking. The map uses Carto Dark Matter as its base and is prepared for Sentinel-1 SAR tiles, AIS ship positions, global atmospheric wind streamlines, hydrodynamic ocean currents, and SAR oil-slick polygons.

## Map layers

- **Sentinel-1 SAR** — raster tiles served through `/api/sentinel/tiles/{z}/{x}/{y}` when the tile provider is configured. The map checks `/api/sentinel/status` first and shows an in-app setup notice instead of issuing failing tile requests when it is not configured.
- **AIS ships** — instanced WebGL vessel layer, ready for the future AIS stream integration.
- **Wind** — NOAA GFS wind fields rendered as animated streamlines.
- **Ocean currents** — WebGL streamline renderer accepting gridded `u`/`v` velocity fields.
- **SAR oil slicks** — empty GeoJSON polygon source ready for backend updates.

The AIS and ocean-current layers are frontend placeholders; no AIS or current-data API is connected yet. Placeholder route folders are present at `/api/ais-stream` and `/api/ocean-currents`.

## Sentinel-1 tile configuration

Configure `SENTINEL_1_TILE_URL` on the server as an HTTPS XYZ tile URL template containing `{z}`, `{x}`, and `{y}`. Provider credentials can be included in this server-only value; the browser requests the same-origin tile proxy and does not receive the upstream URL.

## Development

```powershell
npm install
npm run dev
```

The MapLibre worker is prepared by the existing `predev` and `prebuild` scripts. The Carto base-map resources are requested through `/api/proxy-tiles`; global wind fields are provided by `/api/wind`.

## Validation

```powershell
npm test
npm run lint
npm run build
```
