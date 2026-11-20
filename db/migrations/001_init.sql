-- Fauxel schema v1 (Postgres 16 + PostGIS + pgvector). See docs/specs/platform-contract.md.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  description text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE buildings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name text NOT NULL,
  UNIQUE (project_id, name)
);

CREATE TABLE levels (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  building_id uuid REFERENCES buildings(id) ON DELETE SET NULL,
  name text NOT NULL,
  elevation_m double precision NOT NULL DEFAULT 0,
  UNIQUE (project_id, name)
);

CREATE TABLE zones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  level_id uuid REFERENCES levels(id) ON DELETE CASCADE,
  name text NOT NULL,
  geom geometry(PolygonZ, 0),
  UNIQUE (project_id, level_id, name)
);
CREATE INDEX zones_geom_idx ON zones USING gist (geom);

CREATE TABLE bim_models (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name text NOT NULL,
  source text NOT NULL CHECK (source IN ('ifc','revit','navisworks','speckle','aps')),
  file_key text,
  version integer NOT NULL DEFAULT 1,
  host_version text,
  shared_transform double precision[],
  element_count integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE bim_elements (
  element_uid uuid PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  model_id uuid REFERENCES bim_models(id) ON DELETE SET NULL,
  revit_unique_id text,
  ifc_global_id text,
  navis_item_guid text,
  aps_id text,
  name text,
  category text,
  ifc_class text,
  family_type text,
  trade text NOT NULL DEFAULT 'other'
    CHECK (trade IN ('structural','arch_interior','hvac','electrical','plumbing_fp','other')),
  level_id uuid REFERENCES levels(id) ON DELETE SET NULL,
  zone_id uuid REFERENCES zones(id) ON DELETE SET NULL,
  aabb_min double precision[3],
  aabb_max double precision[3],
  centroid geometry(PointZ, 0),
  mesh_key text,
  material text,
  expected_qty double precision,
  qty_unit text
);
CREATE UNIQUE INDEX bim_elements_revit_uq ON bim_elements (project_id, revit_unique_id) WHERE revit_unique_id IS NOT NULL;
CREATE UNIQUE INDEX bim_elements_ifc_uq ON bim_elements (project_id, ifc_global_id) WHERE ifc_global_id IS NOT NULL;
CREATE UNIQUE INDEX bim_elements_navis_uq ON bim_elements (project_id, navis_item_guid) WHERE navis_item_guid IS NOT NULL;
CREATE INDEX bim_elements_level_idx ON bim_elements (level_id);
CREATE INDEX bim_elements_zone_idx ON bim_elements (zone_id);
CREATE INDEX bim_elements_trade_idx ON bim_elements (project_id, trade);
CREATE INDEX bim_elements_centroid_idx ON bim_elements USING gist (centroid);

CREATE TABLE capture_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  level_id uuid REFERENCES levels(id) ON DELETE SET NULL,
  captured_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL DEFAULT 'upload' CHECK (source IN ('insta360','openspace','upload')),
  camera_model text,
  status text NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded','aligned','processed','failed')),
  -- p_bim = scale * R * p_slam + t ; row-major 4x4 holding the rigid part [R|t]
  transform_bim_from_slam double precision[16],
  scale double precision,
  rmse_m double precision,
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trajectory_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  capture_id uuid NOT NULL REFERENCES capture_sessions(id) ON DELETE CASCADE,
  seq integer NOT NULL,
  x double precision NOT NULL, y double precision NOT NULL, z double precision NOT NULL,
  qw double precision NOT NULL, qx double precision NOT NULL, qy double precision NOT NULL, qz double precision NOT NULL,
  t_sec double precision
);
CREATE INDEX trajectory_nodes_capture_idx ON trajectory_nodes (capture_id, seq);

CREATE TABLE panorama_frames (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  capture_id uuid NOT NULL REFERENCES capture_sessions(id) ON DELETE CASCADE,
  node_id uuid REFERENCES trajectory_nodes(id) ON DELETE SET NULL,
  seq integer NOT NULL,
  image_key text NOT NULL,
  preview_key text,
  width integer NOT NULL,
  height integer NOT NULL,
  blur_score double precision,
  t_sec double precision,
  taken_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX panorama_frames_capture_idx ON panorama_frames (capture_id, seq);

CREATE TABLE element_status (
  element_uid uuid PRIMARY KEY REFERENCES bim_elements(element_uid) ON DELETE CASCADE,
  state text NOT NULL DEFAULT 'NOT_STARTED'
    CHECK (state IN ('NOT_STARTED','FRAMED_OR_ROUGHED','INSTALLED','INSULATED_OR_FINISHED','DEVIATION_FLAGGED')),
  percent_complete double precision NOT NULL DEFAULT 0,
  confidence double precision,
  last_verified timestamptz,
  evidence_frame_id uuid REFERENCES panorama_frames(id) ON DELETE SET NULL,
  rules_version integer,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX element_status_updated_idx ON element_status (updated_at);

CREATE TABLE element_observations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  element_uid uuid NOT NULL REFERENCES bim_elements(element_uid) ON DELETE CASCADE,
  capture_id uuid REFERENCES capture_sessions(id) ON DELETE CASCADE,
  frame_id uuid REFERENCES panorama_frames(id) ON DELETE SET NULL,
  source text NOT NULL CHECK (source IN ('cv','human','import')),
  state text NOT NULL,
  percent double precision,
  confidence double precision,
  occlusion_ratio double precision,
  rework boolean NOT NULL DEFAULT false,
  observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX element_observations_element_idx ON element_observations (element_uid, observed_at);
CREATE INDEX element_observations_capture_idx ON element_observations (capture_id);

CREATE TABLE progress_rules (
  id serial PRIMARY KEY,
  version integer NOT NULL,
  trade text NOT NULL,
  ifc_class text,            -- SQL LIKE pattern, NULL = trade default
  ladder jsonb NOT NULL      -- [{percent, state, label}]
);
CREATE INDEX progress_rules_version_idx ON progress_rules (version, trade);

CREATE TABLE detections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  frame_id uuid NOT NULL REFERENCES panorama_frames(id) ON DELETE CASCADE,
  element_uid uuid NOT NULL REFERENCES bim_elements(element_uid) ON DELETE CASCADE,
  state text NOT NULL,
  percent double precision,
  confidence double precision,
  occlusion_ratio double precision,
  view_quality double precision,
  pixel_bbox integer[4],
  detected_qty double precision,
  polygon jsonb,                         -- normalized [[u,v],...]
  feature jsonb,                         -- change-detection features for the next capture
  review_status text NOT NULL DEFAULT 'none'
    CHECK (review_status IN ('none','pending','resolved','superseded')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX detections_frame_idx ON detections (frame_id);
CREATE INDEX detections_element_idx ON detections (element_uid, created_at);
CREATE INDEX detections_review_idx ON detections (review_status) WHERE review_status = 'pending';

CREATE TABLE schedule_activities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  external_id text NOT NULL,
  name text NOT NULL,
  wbs text,
  trade text,
  zone_id uuid REFERENCES zones(id) ON DELETE SET NULL,
  zone_name text,
  planned_start date,
  planned_finish date,
  actual_start date,
  actual_finish date,
  budget_cost double precision,
  planned_qty double precision,
  qty_unit text,
  percent_complete double precision,
  UNIQUE (project_id, external_id)
);

CREATE TABLE activity_elements (
  activity_id uuid NOT NULL REFERENCES schedule_activities(id) ON DELETE CASCADE,
  element_uid uuid NOT NULL REFERENCES bim_elements(element_uid) ON DELETE CASCADE,
  PRIMARY KEY (activity_id, element_uid)
);

CREATE TABLE evm_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  as_of date NOT NULL,
  scope text NOT NULL CHECK (scope IN ('project','trade','zone')),
  scope_key text,
  pv double precision, ev double precision, ac double precision,
  spi double precision, cpi double precision,
  velocity_per_week double precision
);
CREATE INDEX evm_snapshots_idx ON evm_snapshots (project_id, as_of);

CREATE TABLE deviations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  element_uid uuid REFERENCES bim_elements(element_uid) ON DELETE SET NULL,
  frame_id uuid REFERENCES panorama_frames(id) ON DELETE SET NULL,
  kind text NOT NULL CHECK (kind IN ('positional','precover','missing','extra')),
  dx double precision, dy double precision, dz double precision,
  severity text NOT NULL DEFAULT 'medium' CHECK (severity IN ('low','medium','high','critical')),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX deviations_project_idx ON deviations (project_id, status);
CREATE INDEX deviations_element_idx ON deviations (element_uid);

CREATE TABLE api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  key_hash text NOT NULL,   -- "<salt>$<sha256 hex of salt+secret>"
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox_events (
  id uuid PRIMARY KEY,
  topic text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE INDEX outbox_unpublished_idx ON outbox_events (created_at) WHERE published_at IS NULL;

-- progress_rules v1 (contract "Default progress rules")
INSERT INTO progress_rules (version, trade, ifc_class, ladder) VALUES
 (1, 'arch_interior', 'IfcWall%', '[
   {"percent":0,"state":"NOT_STARTED","label":"Not started"},
   {"percent":25,"state":"FRAMED_OR_ROUGHED","label":"Track"},
   {"percent":50,"state":"FRAMED_OR_ROUGHED","label":"Studs"},
   {"percent":65,"state":"FRAMED_OR_ROUGHED","label":"MEP rough verified + insulation"},
   {"percent":80,"state":"INSTALLED","label":"Board hung"},
   {"percent":95,"state":"INSTALLED","label":"Taped"},
   {"percent":100,"state":"INSULATED_OR_FINISHED","label":"Painted"}]'),
 (1, 'arch_interior', NULL, '[
   {"percent":0,"state":"NOT_STARTED","label":"Not started"},
   {"percent":30,"state":"FRAMED_OR_ROUGHED","label":"Rough-in"},
   {"percent":80,"state":"INSTALLED","label":"Installed"},
   {"percent":100,"state":"INSULATED_OR_FINISHED","label":"Finished"}]'),
 (1, 'hvac', NULL, '[
   {"percent":0,"state":"NOT_STARTED","label":"Not started"},
   {"percent":30,"state":"FRAMED_OR_ROUGHED","label":"Hangers/supports"},
   {"percent":80,"state":"INSTALLED","label":"Installed"},
   {"percent":100,"state":"INSULATED_OR_FINISHED","label":"Insulated/trimmed"}]'),
 (1, 'plumbing_fp', NULL, '[
   {"percent":0,"state":"NOT_STARTED","label":"Not started"},
   {"percent":30,"state":"FRAMED_OR_ROUGHED","label":"Hangers/supports"},
   {"percent":80,"state":"INSTALLED","label":"Installed"},
   {"percent":100,"state":"INSULATED_OR_FINISHED","label":"Insulated/trimmed"}]'),
 (1, 'electrical', NULL, '[
   {"percent":0,"state":"NOT_STARTED","label":"Not started"},
   {"percent":30,"state":"FRAMED_OR_ROUGHED","label":"Hangers/supports"},
   {"percent":80,"state":"INSTALLED","label":"Installed"},
   {"percent":100,"state":"INSULATED_OR_FINISHED","label":"Insulated/trimmed"}]'),
 (1, 'structural', NULL, '[
   {"percent":0,"state":"NOT_STARTED","label":"Not started"},
   {"percent":40,"state":"FRAMED_OR_ROUGHED","label":"Formwork/rebar"},
   {"percent":100,"state":"INSTALLED","label":"Poured/erected"}]'),
 (1, 'other', NULL, '[
   {"percent":0,"state":"NOT_STARTED","label":"Not started"},
   {"percent":100,"state":"INSTALLED","label":"Installed"}]');
