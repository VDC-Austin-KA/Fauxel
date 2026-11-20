# Phase 8 — Field Capture Companion App

> Original design brief. Decision log: the reference camera is the ONE X2 over OSC (camera IP 192.168.42.1). Pro 2 needs the separate Insta360 Pro Camera API.

Act as a Senior Mobile & Offline-First Systems Engineer. Build the Fauxel Field Capture Companion App using React Native / Expo (TypeScript) for iOS and Android.

**Implement the complete field workflow:**
1. **Insta360 Camera Wi-Fi Control (OSC Protocol):**
   - Connect directly to Insta360 cameras via the **Open Spherical Camera (OSC) HTTP API**.
   - Automatically configure optimal construction capture settings upon connection: lock shutter speed (`1/120` to `1/500` indoors to eliminate motion blur), set 5.7K/60fps video or high-res HDR photo mode, and display live battery/storage telemetry.
2. **Floorplan-Guided Walk & Photo Pinning:**
   - Cache project 2D floorplan sheets (from ACC/Forma Sheets API or local PDF/SVG) offline on the device.
   - Allow the user to tap their starting grid intersection on the floorplan, press "Start Video Walk" or tap stationary photo pins, and visually track coverage across zones.
   - Support quick voice-to-text field notes and manual QC issue pinning during the walk.
3. **Resumable Chunked Upload & Cloud Sync:**
   - Automatically transfer `.insv`/`.insp` files from the Insta360 camera over Wi-Fi, compute SHA-256 checksums, and perform multipart resumable uploads (tus protocol / S3 multipart) to the Fauxel ingestion pipeline when back on job-site Wi-Fi/5G.
   - Push live processing notifications (WebSockets/Push) once VSLAM alignment and CV trade quantification finish.
