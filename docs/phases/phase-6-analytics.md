# Phase 6 — analytics-engine

> Original design brief.

Act as a Senior Construction Controls Engineer and Backend Data Architect. Build Fauxel's `analytics-engine` in Python (FastAPI, Pandas/Polars, SQLAlchemy) to connect CV progress data with project schedules and budgets.

**Implement the following capabilities:**
1. **Schedule Ingestion & Auto-Mapping:**
   - Parse **Primavera P6 (`.xer` / `.xml`)**, **Microsoft Project (`.xml`)**, and **Navisworks TimeLiner** schedules.
   - Map Schedule Activity IDs to BIM Element GUIDs via WBS codes, Location Breakdown Structure (Level/Zone), Trade/CSI MasterFormat codes, or Navisworks Search Set rules.
2. **Earned Value Management (EVM) & Production Velocity:**
   - Compute daily/weekly metrics per Project, Building, Floor, Zone, Subcontractor, and Trade:
     - **Planned Value (PV)**, **Earned Value (EV)**, and **Schedule Variance (`SV = EV - PV`)**.
     - **Schedule Performance Index (`SPI = EV / PV`)** and **Cost Performance Index (`CPI`)** (via Forma Cost Management API or CSV budget import).
     - **True Production Rate:** Units installed per week (e.g., linear feet of duct/week, sq ft of drywall/week) vs. baseline required burn rate.
3. **Predictive Critical-Path Delay & Trade Stacking Alerts:**
   - Run Monte Carlo / linear velocity projections on active activities to forecast exact slip days if current trade production rates persist.
   - Detect **Trade Stacking / Bottlenecks** (e.g., Electrical rough-in lagging in Zone 3B, threatening to block Framing/Drywall crew mobilization in 9 days).
4. **Automated Executive Reporting:**
   - Expose REST/GraphQL endpoints and scheduled PDF/CSV/PowerBI Data Connector exports summarizing weekly progress deltas, top 5 schedule risks, and verified subcontractor pay-application percentages.
