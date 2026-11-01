import type { Map as MlMap } from 'maplibre-gl';
import { WindOverlay } from './wind-gl-layer';
import type { WindGrid, WindStyle } from './wind-layer';

/** Ocean-current vectors on the same geographic grid shape used by the wind renderer. */
export type OceanCurrentGrid = WindGrid;

const CURRENT_PALETTE = {
  base: [0.08, 0.62, 0.78] as [number, number, number],
  highlight: [0.35, 0.86, 1] as [number, number, number],
};

/** GPU streamline overlay for hydrodynamic u/v velocity fields. */
export class CurrentsOverlay {
  private readonly overlay: WindOverlay;

  constructor(map: MlMap) {
    this.overlay = new WindOverlay(map, {
      id: 'ocean-current-streamlines',
      palette: CURRENT_PALETTE,
    });
  }

  setField(field: OceanCurrentGrid | null): void {
    this.overlay.setField(field);
  }

  setStyle(style: Partial<WindStyle>): void {
    this.overlay.setStyle(style);
  }

  remove(): void {
    this.overlay.remove();
  }
}
