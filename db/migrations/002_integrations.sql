-- Integrations (M1.2 / M1.5 / M1.6): APS (Forma/ACC), Speckle, BCF.

-- OAuth tokens, encrypted at rest (Fernet, key from FAUXEL_TOKEN_KEY). One row per (provider, kind, scope-project).
CREATE TABLE integration_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL CHECK (provider IN ('aps','speckle')),
  kind text NOT NULL CHECK (kind IN ('two_legged','three_legged','pat')),
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,   -- NULL = installation-wide
  subject text,                                                  -- e.g. Autodesk user id / server host
  scopes text,
  access_token_enc text NOT NULL,
  refresh_token_enc text,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX integration_tokens_uq
  ON integration_tokens (provider, kind, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(subject, ''));

-- Pending 3-legged logins: state (CSRF) + PKCE verifier (encrypted).
CREATE TABLE oauth_states (
  state text PRIMARY KEY,
  provider text NOT NULL,
  project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
  code_verifier_enc text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Per-project integration settings (hub id, project id, issue subtype map, ...). Secrets never go here.
CREATE TABLE project_integrations (
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  provider text NOT NULL,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, provider)
);

-- Fauxel deviation <-> external issue (APS issue, BCF topic, ...). UNIQUE(deviation_id, provider) = idempotent push.
CREATE TABLE external_issue_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  deviation_id uuid NOT NULL REFERENCES deviations(id) ON DELETE CASCADE,
  provider text NOT NULL,
  external_id text NOT NULL,
  external_project text,
  display_id text,
  attachments jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{attachmentId, storageUrn, displayName}]
  pushpin boolean NOT NULL DEFAULT false,
  synced_status text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deviation_id, provider),
  UNIQUE (provider, external_project, external_id)
);

-- Generic id mapping for non-issue entities: location nodes -> levels/zones, assets -> elements, model versions.
CREATE TABLE external_entity_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  provider text NOT NULL,
  entity_type text NOT NULL CHECK (entity_type IN ('building','level','zone','element','model_version')),
  fauxel_id uuid,                         -- buildings/levels/zones/elements (element_uid)/bim_models id
  external_id text NOT NULL,
  extra jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (project_id, provider, entity_type, external_id)
);
CREATE INDEX external_entity_links_fauxel_idx ON external_entity_links (project_id, provider, entity_type, fauxel_id);

-- Webhook delivery log + dedupe (APS delivers at least once, so duplicates occur).
CREATE TABLE integration_webhook_events (
  event_key text PRIMARY KEY,
  provider text NOT NULL,
  project_id uuid REFERENCES projects(id) ON DELETE SET NULL,
  event text,
  resource text,
  status text NOT NULL DEFAULT 'received',   -- received | imported | ignored | failed
  detail text,
  received_at timestamptz NOT NULL DEFAULT now()
);
