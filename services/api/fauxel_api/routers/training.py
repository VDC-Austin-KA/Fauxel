"""Training data for the CV trade models (M4): verified observations and tangent-plane crops of the panorama."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .. import media
from ..db import get_conn
from ..process import bim_from_cam, load_triangles, transformer_for
from ..vision.model_slot import crop_for_mask
from ..vision.projection import ElementGeom, project_elements
from .deps import get_project

router = APIRouter()
MAX_DIST_M = 15.0


@router.get("/projects/{pid}/training-samples")
def training_samples(pid: uuid.UUID, since: Optional[datetime] = None, source: Literal["human", "cv", "import"] = "human",
                     limit: int = Query(1000, ge=1, le=20000), conn=Depends(get_conn)):
    """Verified observations that point at a frame, with the element's trade and the projected polygon/bbox
    (from the CV detection of the same frame/element; null when none exists). Fetch the image with GET /training/crop."""
    get_project(conn, pid)
    rows = conn.execute(
        "SELECT o.id AS observation_id, o.frame_id, f.capture_id, o.element_uid, e.trade, e.ifc_class, o.state, o.percent, "
        "o.confidence, o.rework, o.observed_at, d.polygon, d.pixel_bbox AS bbox "
        "FROM element_observations o JOIN panorama_frames f ON f.id = o.frame_id JOIN bim_elements e ON e.element_uid = o.element_uid "
        "LEFT JOIN LATERAL (SELECT polygon, pixel_bbox FROM detections d WHERE d.frame_id = o.frame_id AND d.element_uid = o.element_uid "
        "ORDER BY d.created_at DESC LIMIT 1) d ON true "
        "WHERE e.project_id = %s AND o.source = %s AND o.percent IS NOT NULL AND (%s::timestamptz IS NULL OR o.observed_at > %s) "
        "ORDER BY o.observed_at LIMIT %s", (pid, source, since, since, limit)).fetchall()
    return rows


@router.get("/training/crop")
def training_crop(request: Request, frame_id: uuid.UUID, element_uid: uuid.UUID, size: int = Query(384, ge=64, le=1024),
                  conn=Depends(get_conn)):
    """JPEG tangent-plane (rectilinear, level) crop of the panorama centred on the element."""
    f = conn.execute(
        "SELECT f.*, n.x, n.y, n.z, n.qw, n.qx, n.qy, n.qz FROM panorama_frames f "
        "LEFT JOIN trajectory_nodes n ON n.id = f.node_id WHERE f.id = %s", (frame_id,)).fetchone()
    if not f:
        raise HTTPException(404, "frame not found")
    if f["x"] is None:
        raise HTTPException(409, "frame has no pose: PUT /captures/{id}/trajectory or POST /captures/{id}/align first")
    cap = conn.execute("SELECT * FROM capture_sessions WHERE id = %s", (f["capture_id"],)).fetchone()
    e = conn.execute("SELECT element_uid, aabb_min, aabb_max, mesh_key FROM bim_elements WHERE element_uid = %s AND project_id = %s",
                     (element_uid, cap["project_id"])).fetchone()
    if not e:
        raise HTTPException(404, "element not found in the frame's project")
    storage = request.app.state.storage
    try:
        img = media.decode_image(storage.get(f["preview_key"] or f["image_key"]))
    except (KeyError, ValueError):
        raise HTTPException(404, "frame image not available")
    geom = ElementGeom(str(e["element_uid"]), tuple(e["aabb_min"]), tuple(e["aabb_max"]), load_triangles(storage, e["mesh_key"]))
    T = bim_from_cam(f, transformer_for(cap))
    h, w = img.shape[:2]
    vis = project_elements(T, [geom], w, h, max_dist_m=MAX_DIST_M)
    if not vis:
        raise HTTPException(422, "element is not visible in this frame")
    crop = crop_for_mask(img, vis[0].mask, size=size)
    return Response(media.encode_jpeg(crop, 92), media_type="image/jpeg")
