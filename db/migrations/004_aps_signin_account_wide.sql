-- Autodesk browser sign-in is account-wide (decision 2026-11-24): move an older project-scoped
-- 3-legged APS token to project_id NULL. Idempotent: does nothing once an account-wide row exists.
UPDATE integration_tokens SET project_id = NULL, updated_at = now()
WHERE id = (SELECT id FROM integration_tokens
            WHERE provider = 'aps' AND kind = 'three_legged' AND project_id IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1)
  AND NOT EXISTS (SELECT 1 FROM integration_tokens
                  WHERE provider = 'aps' AND kind = 'three_legged' AND project_id IS NULL);
