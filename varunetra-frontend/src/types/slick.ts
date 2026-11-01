export interface Slick {
  id: string;
  center: [number, number];
  kind?: string | null;
  areaKm2?: number | null;
  confidence?: number | null;
  detectedAt?: string | null;
  ageHours?: number | null;
  ageSigmaHours?: number | null;
  originLat?: number | null;
  originLon?: number | null;
  originRadiusKm?: number | null;
  originMmsi?: string | null;
  sceneId?: string | null;
  dataSource?: string | null;
  properties: Record<string, unknown>;
}

export interface Vessel {
  id: string;
  name: string;
  shipType: string;
  distanceKm: number;
  probability: number;
  position: [number, number];
}

export interface ReportedCulprit {
  id: number;
  spill_id: string;
  mmsi: number | null;
  vessel_name: string | null;
  imo: string | null;
  source_title: string | null;
  source_url: string | null;
  report_date: string | null;
  notes: string | null;
}

export interface AgeEstimate {
  hours: number;
  uncertaintyHours: number;
  confidenceBand: [number, number];
  windSpeedKnots: number;
  spreadRateKm2PerHour: number;
  shape: string;
}
