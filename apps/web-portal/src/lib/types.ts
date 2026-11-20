// Types mirror docs/specs/platform-contract.md (v1).
export type InstallState =
  | "NOT_STARTED"
  | "FRAMED_OR_ROUGHED"
  | "INSTALLED"
  | "INSULATED_OR_FINISHED"
  | "DEVIATION_FLAGGED";

export const INSTALL_STATES: InstallState[] = [
  "NOT_STARTED",
  "FRAMED_OR_ROUGHED",
  "INSTALLED",
  "INSULATED_OR_FINISHED",
  "DEVIATION_FLAGGED",
];

export type Trade = "structural" | "arch_interior" | "hvac" | "electrical" | "plumbing_fp" | "other";
export const TRADES: Trade[] = ["structural", "arch_interior", "hvac", "electrical", "plumbing_fp", "other"];

export type Vec3 = [number, number, number];

export interface Health {
  status: string;
  db: string;
  storage: string;
  gpu: string | boolean;
}

export interface Project {
  id: string;
  name: string;
  address?: string;
  created_at: string;
  // Portfolio card extras (summary is also available from /progress/summary)
  percent_complete?: number;
  spi?: number;
  open_deviations?: number;
  last_capture?: string | null;
  sparkline?: number[];
}

export interface LbsZone { id: string; name: string; bbox?: [number, number, number, number]; polygon?: [number, number][] }
export interface LbsLevel { id: string; name: string; elevation_m: number; zones: LbsZone[] }
export interface Lbs { buildings: { id: string; name: string; levels: LbsLevel[] }[] }

export interface BimModel { id: string; project_id: string; name: string; source: "ifc" | "revit" | "navisworks" | "speckle" | "aps"; file_key?: string; version: number; created_at: string; element_count?: number }

export interface Element {
  element_uid: string;
  model_id: string;
  name: string;
  category: string;
  ifc_class: string;
  family_type: string;
  trade: Trade;
  level_id: string;
  zone_id: string;
  aabb_min: Vec3;
  aabb_max: Vec3;
  revit_unique_id?: string | null;
  ifc_global_id?: string | null;
  navis_item_guid?: string | null;
  state: InstallState;
  percent_complete: number;
  confidence: number;
  last_verified: string | null;
  evidence_frame_id: string | null;
}

export interface Observation {
  id: string;
  element_uid: string;
  capture_id: string | null;
  frame_id: string | null;
  source: "cv" | "human" | "import";
  state: InstallState;
  percent: number;
  confidence: number;
  occlusion_ratio: number;
  observed_at: string;
}

export interface ElementDetail extends Element {
  observations: Observation[];
  evidence_frames: { frame_id: string; capture_id: string; observed_at: string; pixel_bbox?: [number, number, number, number] }[];
}

export interface ObservationInput { state: InstallState; percent?: number; confidence?: number; frame_id?: string | null; source: "human"; rework?: boolean }

export interface CaptureSession {
  id: string;
  project_id: string;
  level_id: string;
  captured_at: string;
  source: "insta360" | "openspace" | "upload";
  camera_model: string;
  status: "uploaded" | "processing" | "ready" | "aligned" | "processed" | "failed";
  error?: string | null;
  transform_bim_from_slam?: number[] | null;
  scale?: number | null;
  rmse_m?: number | null;
  frame_count?: number;
}

export interface Frame {
  id: string;
  capture_id: string;
  node_id: string;
  seq: number;
  image_key: string;
  width: number;
  height: number;
  blur_score: number;
  taken_at: string;
  // node pose (T_bim_from_cam), meters
  x: number; y: number; z: number;
  qw: number; qx: number; qy: number; qz: number;
}

export interface OverlayPoly {
  element_uid: string;
  state: InstallState;
  percent: number;
  polygon: [number, number][];
  occlusion_ratio: number;
}

export interface AlignInput { points: { slam: Vec3; bim: Vec3 }[]; estimate_scale: boolean }
export interface AlignResult { capture_id: string; transform_bim_from_slam: number[]; scale: number; rmse_m: number; status: string }

export interface ProgressSummary {
  percent_complete: number;
  // null until the project has a schedule / cost data (the API returns null, not 0)
  spi: number | null;
  cpi: number | null;
  ev: number | null;
  pv: number | null;
  open_deviations: number;
  projected_finish: string | null;
  planned_finish?: string;
  elements_total: number;
  elements_verified: number;
}

// planned_pct / level_id / zone_id: absent or null from the API until a schedule / zones exist
export interface TradeProgress { trade: Trade; percent_complete: number; elements: number; verified?: number; planned_pct?: number | null }
export interface ZoneProgress { zone_id: string | null; level_id?: string; trade?: Trade | null; percent_complete: number; elements: number }
export interface TimeseriesPoint { date: string; planned_pct: number | null; actual_pct: number; velocity?: Partial<Record<Trade, number>> }

export interface Activity {
  id: string;
  external_id: string;
  name: string;
  wbs: string;
  trade: Trade;
  zone_id: string | null;
  planned_start: string;
  planned_finish: string;
  actual_start: string | null;
  actual_finish: string | null;
  budget_cost: number;
  percent_complete: number;
  element_uids?: string[];
}

// velocity_per_week is not in the API's /evm rows (only in /forecast activities)
export interface EvmRow { scope: "trade" | "zone"; scope_key: string; pv: number; ev: number; ac: number; spi: number; cpi: number; velocity_per_week?: number; note?: string }

export interface ForecastRisk { activity_id: string; name: string; trade: Trade; slip_days: number; reason: string }
export interface StackingAlert { zone_id: string; week: string; trades: Trade[]; message: string }
export interface Forecast {
  projected_finish: string | null;
  planned_finish: string | null;
  slip_days: number;
  risks: ForecastRisk[];
  stacking: StackingAlert[];
}

export type Severity = "low" | "medium" | "high" | "critical";
export interface Deviation {
  id: string;
  element_uid: string;
  frame_id: string | null;
  kind: "positional" | "precover" | "missing" | "extra" | "note";
  dx: number; dy: number; dz: number;
  severity: Severity;
  status: "open" | "closed";
  note: string;
  created_at: string;
  element_name?: string;
  attachments?: Attachment[];
}

export interface WritebackElement {
  element_uid: string;
  revit_unique_id: string | null;
  ifc_global_id: string | null;
  navis_item_guid: string | null;
  percent_complete: number;
  status: InstallState;
  last_verified: string | null;
  confidence: number;
  evidence_url: string | null;
  color: string;
}
export interface Writeback { project_id: string; generated_at: string; rules_version: number; elements: WritebackElement[] }
export interface WritebackAck { host: "revit" | "navisworks"; applied: number; model: string; at?: string }

export interface PayApp {
  trade: Trade;
  sub: string;
  claimed_pct: number;
  verified_pct: number;
  contract_value: number;
  evidence_frame_id: string | null;
  evidence_capture_id: string | null;
}

export interface Job { id: string; kind?: string; status: "queued" | "running" | "done" | "failed"; result?: Record<string, unknown> | null; error?: string | null; attempts?: number }

export interface Attachment { id: string; url: string; content_type: string; filename?: string; created_at?: string }

export interface QueueItem {
  detection_id: string; element_uid: string; name: string; trade: Trade; ifc_class?: string | null;
  proposed_state: InstallState; proposed_percent: number; confidence: number; occlusion_ratio: number;
  polygon: [number, number][] | null; frame_id: string; capture_id: string; created_at?: string;
  current_state: InstallState; current_percent: number; image_url: string; ladder?: { percent: number; state: InstallState; label?: string }[];
}

export interface ApsStatus {
  configured: boolean; has_client_secret?: boolean; token_key_set?: boolean; webhook_secret_set?: boolean; callback_url?: string | null;
  three_legged: { connected: boolean; expires_at: string | null; has_refresh_token?: boolean; scopes?: string | null; project_scoped?: boolean };
  project_config?: ApsConfig;
}
export interface ApsConfig { hub_id?: string; aps_project_id?: string; aecdm_project_id?: string; publish_issues?: boolean; attachments_folder_id?: string; [k: string]: unknown }
export interface ApsHub { id: string; name: string; region?: string }
export interface ApsProject { id: string; name: string }
export interface ApsModel { item_id: string; name: string; file_type?: string; version_id: string; version_urn: string; folder?: string; size?: number }
