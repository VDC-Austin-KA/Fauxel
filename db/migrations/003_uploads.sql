-- Direct-to-storage uploads, job queue, evidence attachments, field notes, raw capture support.

CREATE TABLE uploads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('capture','model','schedule','evidence')),
  filename text NOT NULL,
  content_type text NOT NULL,
  size bigint NOT NULL CHECK (size > 0),
  part_size integer NOT NULL,
  key text NOT NULL UNIQUE,
  storage_upload_id text NOT NULL,          -- S3 UploadId (or a random token for the fs backend)
  status text NOT NULL DEFAULT 'uploading' CHECK (status IN ('uploading','complete','aborted')),
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
CREATE INDEX uploads_project_idx ON uploads (project_id, kind, status);

CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
  kind text NOT NULL,                        -- capture.ingest | capture.process | model.ingest
  payload jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  attempts integer NOT NULL DEFAULT 0,
  result jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  locked_at timestamptz,
  finished_at timestamptz
);
CREATE INDEX jobs_queue_idx ON jobs (created_at) WHERE status = 'queued';

ALTER TABLE capture_sessions DROP CONSTRAINT capture_sessions_status_check;
ALTER TABLE capture_sessions ADD CONSTRAINT capture_sessions_status_check
  CHECK (status IN ('uploaded','processing','ready','aligned','processed','failed'));
ALTER TABLE capture_sessions ADD COLUMN telemetry jsonb;

CREATE TABLE deviation_attachments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  deviation_id uuid NOT NULL REFERENCES deviations(id) ON DELETE CASCADE,
  key text NOT NULL,
  filename text,
  content_type text NOT NULL,
  size bigint,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX deviation_attachments_idx ON deviation_attachments (deviation_id);

ALTER TABLE deviations DROP CONSTRAINT deviations_kind_check;
ALTER TABLE deviations ADD CONSTRAINT deviations_kind_check CHECK (kind IN ('positional','precover','missing','extra','note'));
ALTER TABLE deviations ADD COLUMN level_id uuid REFERENCES levels(id) ON DELETE SET NULL;
ALTER TABLE deviations ADD COLUMN x double precision;
ALTER TABLE deviations ADD COLUMN y double precision;
