# fauxel_openbim (M1.2 BCF, M1.6 Speckle)

Router: `services/api/fauxel_api/routers/openbim.py`. Works with zero Autodesk licenses.

```
pip install -e "connectors/ifc-speckle-bcf[speckle,test]"
FAUXEL_TEST_ADMIN_URL=postgresql://fauxel@localhost:55432/postgres pytest connectors/ifc-speckle-bcf/tests
```
- BCF: read 2.0/2.1/3.0 (hardened: no DOCTYPE/ENTITY, zip size limits), write 2.1 (3.0 writer is `fauxel_api.qc.bcf`). XSD tests download buildingSMART schemas (`-m network`, skip offline).
- Speckle: specklepy `receive3`/`send3` (Bundle data model, server 2026.9). Token: request body > stored (`PUT /integrations/speckle/token`, Fernet) > `FAUXEL_SPECKLE_TOKEN`. Not tested against a live server.
- Endpoints: `POST /projects/{pid}/speckle/import`, `.../speckle/push-progress`, `.../bcf/import` (multipart, `dry_run`, `require_match`), `GET .../issues.bcf21`.
