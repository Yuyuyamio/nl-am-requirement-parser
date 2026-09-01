"""Fill only shallow gaps over an existing solid base, never tall supports."""
from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import box

from am_print_executor.local_anchored_growth import section_region


def build_near_base_gap_fills(source, observations, *, layer_height_mm=.12,
                             margin_mm=.4, protected_base_clearance_mm=.6):
    """Return candidate volumes for the original fidelity gate and real slicer.

    Broad but shallow air starts cannot be supported by a point-anchored cone.
    These fills embed their entire footprint in two existing base sections.
    They never touch the protected bottom slab, extend the footprint, or reach
    high appendages. They are small retained contour changes, not breakaway
    supports; the repair receipt reports them separately.
    """
    if not np.isfinite([layer_height_mm,margin_mm,protected_base_clearance_mm]).all() or min(layer_height_mm,margin_mm) <= 0 or protected_base_clearance_mm < .6:
        raise ValueError('invalid_near_base_fill_dimensions')
    z0 = float(source.bounds[0,2])
    maximum_top = z0 + min(2.4,float(source.extents[2])*.08)
    bottom = z0 + protected_base_clearance_mm + layer_height_mm*.5
    embed_top = bottom + layer_height_mm
    if maximum_top <= embed_top:
        return [], []
    anchor = section_region(source,bottom).intersection(section_region(source,embed_top)).buffer(-.025)
    if anchor.is_empty:
        return [], []
    parts, records = [], []
    for c in sorted(observations,key=lambda c:(c.z_min_mm,c.cluster_id)):
        if c.xy_bounds_mm is None or not set(c.kinds).issubset({'unsupported_extrusion_region','unsupported_layer_island'}):
            continue
        top = c.z_max_mm + layer_height_mm*2
        if top > maximum_top or c.z_min_mm <= embed_top:
            continue
        bounds = np.asarray(c.xy_bounds_mm,dtype=float)
        footprint = box(*(bounds[0]-margin_mm),*(bounds[1]+margin_mm)).intersection(anchor)
        if footprint.is_empty:
            continue
        polygons = list(footprint.geoms) if footprint.geom_type == 'MultiPolygon' else [footprint]
        for polygon in polygons:
            if polygon.geom_type != 'Polygon' or polygon.area <= 1e-8:
                continue
            part = trimesh.creation.extrude_polygon(polygon,height=top-bottom,engine='earcut')
            part.apply_translation((0,0,bottom))
            parts.append(part)
            records.append({'observation_id':c.cluster_id,'kind':'retained_shallow_base_gap_fill',
                'bottom_z_mm':bottom,'top_z_mm':top,'maximum_allowed_top_z_mm':maximum_top,
                'footprint_area_mm2':float(polygon.area),'fully_embedded_footprint':True,
                'temporary_support':False})
    return parts, records
