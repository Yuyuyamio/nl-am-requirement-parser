"""Add model material beneath measured unsupported paths, independent of names.

This is an explicit structural redesign, not the fidelity-limited GPR route.
Only manifold unions are used; the original surface is never voxel rebuilt.
"""
from __future__ import annotations

import numpy as np
import trimesh
from dataclasses import dataclass
from shapely.geometry import Point, Polygon, box
from shapely.ops import nearest_points

from am_print_executor.geometry_fidelity_gate import normalized_boolean_copy, mesh_validation
from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.local_anchored_growth import section_region


@dataclass(frozen=True)
class SurfaceDefect:
    cluster_id: str
    z_min_mm: float
    z_max_mm: float
    xy_bounds_mm: tuple
    kinds: tuple = ('unsupported_extrusion_region',)


def surface_overhang_defects(source, observations, *, slope_xy_per_z=.65, bottom_clearance_mm=.4):
    """Continuous face normals also cover defects hidden by one raster phase.

    These are measurements of original triangles, not a voxel surface rebuild.
    Downward faces that expand faster than the design slope are covered across
    their entire XY/Z extent, regardless of Bambu's placement or feature name.
    """
    cutoff=-slope_xy_per_z/np.sqrt(1+slope_xy_per_z**2)
    indices=np.flatnonzero(source.face_normals[:,2]<cutoff-1e-5)
    result=[]
    for index in indices:
        triangle=source.triangles[index]
        lower,upper=triangle.min(axis=0),triangle.max(axis=0)
        if upper[2]<=source.bounds[0,2]+bottom_clearance_mm: continue
        # Keep changes local to measured defects; unrelated holes and safe
        # two-ended bridges are not filled merely because they face down.
        if not any(c.xy_bounds_mm is not None
                   and lower[2]<=c.z_max_mm+.4 and upper[2]>=c.z_min_mm-.4
                   and np.all(upper[:2]>=np.asarray(c.xy_bounds_mm)[0]-.6)
                   and np.all(lower[:2]<=np.asarray(c.xy_bounds_mm)[1]+.6)
                   for c in observations): continue
        result.append(SurfaceDefect(f'surface_{index:06d}',float(lower[2]),float(upper[2]),
                      (tuple(lower[:2]),tuple(upper[:2]))))
    return result


def add_model_buttresses(mesh, observations, *, layer_height_mm=.2,
                         margin_mm=.6, minimum_width_mm=1.2,
                         slope_xy_per_z=.65, maximum_added_volume_ratio=.2,
                         critical_regions=()):
    """Cover observations individually, avoiding a large merged bounding box.

    Each four-sided gusset has a fully embedded lower footprint. Its corners
    grow no faster than the explicit slope. If no local anchor exists, a
    vertical rib may start inside the existing base. Every candidate still
    requires a real slice with removable support disabled.
    """
    source=normalized_boolean_copy(mesh)
    if not mesh_validation(source)['valid'] or source.body_count != 1:
        raise ValueError('buttresses_require_one_connected_solid')
    if not inspect_flat_printing_base(source)['base_flatness_passed']:
        raise ValueError('buttresses_require_planar_base')
    if min(layer_height_mm,margin_mm,minimum_width_mm,slope_xy_per_z)<=0 or slope_xy_per_z>1:
        raise ValueError('invalid_buttress_dimensions')
    z0,zmax=source.bounds[:,2]
    signs=np.array([[-1,-1],[1,-1],[1,1],[-1,1]])
    cache={}
    def section(z):
        key=round(float(z),6)
        if key not in cache: cache[key]=section_region(source,key)
        return cache[key]
    base_z=z0+min(.4,layer_height_mm*2)
    foundation=section(base_z)
    parts=[]; records=[]
    if observations:
        observations=[*observations,*surface_overhang_defects(source,observations,slope_xy_per_z=slope_xy_per_z)]
    # A stable order makes identical inputs reproduce identical geometry.
    ordered=sorted(observations,key=lambda c:(c.z_min_mm,c.xy_bounds_mm or (),c.cluster_id))
    for cluster in ordered:
        if cluster.xy_bounds_mm is None or not set(cluster.kinds).issubset({
            'unsupported_extrusion_region','unsupported_layer_island','unsafe_bridge'}):
            raise ValueError('unsupported_buttress_defect_family')
        bounds=np.asarray(cluster.xy_bounds_mm,dtype=float)
        center=bounds.mean(axis=0)
        half=np.maximum((bounds[1]-bounds[0])/2+margin_mm,minimum_width_mm/2)
        top_xy=center+signs*half
        top_z=min(cluster.z_max_mm+2*layer_height_mm,zmax)
        anchor_half=minimum_width_mm/2
        part=None; record={'observation_id':cluster.cluster_id,'top_z_mm':float(top_z)}
        for depth in np.arange(layer_height_mm,float(top_z-z0)+layer_height_mm,layer_height_mm):
            upper_z=cluster.z_min_mm-depth
            lower_z=upper_z-layer_height_mm
            if lower_z<base_z: break
            lower,upper=section(lower_z),section(upper_z)
            available=lower.intersection(upper).buffer(-anchor_half*1.42-.05)
            if available.is_empty: continue
            anchor=np.asarray(nearest_points(available,Point(center))[0].coords[0])
            bottom_xy=anchor+signs*anchor_half
            footprint=Polygon(bottom_xy)
            if not lower.covers(footprint) or not upper.covers(footprint): continue
            shift=np.linalg.norm(top_xy-bottom_xy,axis=1).max()
            if shift>slope_xy_per_z*(top_z-lower_z): continue
            vertices=np.vstack([np.c_[bottom_xy,np.full(4,lower_z)],np.c_[top_xy,np.full(4,top_z)]])
            part=trimesh.convex.convex_hull(vertices)
            record.update(kind='body_anchored_gusset',bottom_z_mm=float(lower_z),
                          anchor_xy_mm=anchor.tolist(),slope_xy_per_z=float(shift/(top_z-lower_z)),
                          fully_embedded_anchor=True)
            break
        if part is None:
            footprint=box(*(center-half),*(center+half))
            footprint=footprint.intersection(foundation.buffer(-.05))
            if footprint.is_empty or footprint.geom_type!='Polygon' or top_z<=base_z:
                raise ValueError('no_safe_model_anchor_for_buttress')
            part=trimesh.creation.extrude_polygon(footprint,top_z-base_z,engine='earcut')
            part.apply_translation([0,0,base_z])
            record.update(kind='base_anchored_rib',bottom_z_mm=float(base_z),
                          slope_xy_per_z=0.,fully_embedded_anchor=True)
        for region in critical_regions:
            protected=np.asarray(region.get('bounds_mm'),dtype=float)
            if protected.shape!=(2,3) or not np.isfinite(protected).all() or np.any(protected[1]<=protected[0]):
                raise ValueError('unlocalized_critical_region')
            if np.all(part.bounds[1]>=protected[0]) and np.all(part.bounds[0]<=protected[1]):
                raise ValueError('buttress_intersects_critical_region')
        records.append(record); parts.append(part)
    if not parts: return source,{'method':'model_buttresses','changed':False,'parts':[]}
    candidate=trimesh.boolean.union([source,*parts],engine='manifold',check_volume=True)
    candidate=normalized_boolean_copy(candidate)
    # Overlapping gussets can enclose tiny air pockets that were not in the
    # single-shell source. Fill those pockets additively; never discard an
    # external component or a source cavity to make topology checks pass.
    void_fill_volume=0.
    if candidate.body_count>1:
        shells=candidate.split(only_watertight=False)
        outer=[s for s in shells if s.volume>0]
        inner=[s for s in shells if s.volume<0 and s.is_watertight]
        if len(outer)==1 and len(inner)==len(shells)-1:
            void_fill_volume=sum(-s.volume for s in inner)
            if void_fill_volume<=source.volume*.001:
                fillers=[]
                for shell in inner:
                    shell.invert()
                    # Prove enclosure instead of treating winding alone as proof.
                    overlap=trimesh.boolean.intersection([outer[0],shell],engine='manifold')
                    if abs(overlap.volume-shell.volume)>1e-4:
                        raise ValueError('unproven_enclosed_buttress_void')
                    for region in critical_regions:
                        bounds=np.asarray(region['bounds_mm'])
                        if np.all(shell.bounds[1]>=bounds[0]) and np.all(shell.bounds[0]<=bounds[1]):
                            raise ValueError('void_fill_intersects_critical_region')
                    fillers.append(shell)
                candidate=normalized_boolean_copy(trimesh.boolean.union([candidate,*fillers],engine='manifold'))
    ratio=float((candidate.volume-source.volume)/source.volume)
    if not mesh_validation(candidate)['valid'] or candidate.body_count!=1:
        raise ValueError('buttress_union_invalid:'+str(mesh_validation(candidate)))
    if ratio<=0 or ratio>maximum_added_volume_ratio:
        raise ValueError('buttress_volume_budget_exceeded')
    if not np.allclose(candidate.bounds,source.bounds,atol=1e-4):
        raise ValueError('buttress_changed_external_dimensions')
    if not inspect_flat_printing_base(candidate)['base_flatness_passed']:
        raise ValueError('buttress_damaged_planar_base')
    return candidate,{'method':'model_buttresses','changed':True,'fidelity_limited_gpr':False,
                      'design_change':True,'source_surface_rebuilt':False,
                      'added_volume_ratio':ratio,'new_enclosed_void_fill_mm3':float(void_fill_volume),'parts':records}


def individual_defects(gate_report, artifact):
    """Use real issue bounds in the mesh frame, without anatomical heuristics."""
    from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame
    from am_print_executor.printability_blocker_clustering import cluster_gate_blockers
    mapped=gate_report_in_geometry_frame(gate_report,artifact)
    result=[]
    from dataclasses import replace
    for layer in mapped.get('dangerous_layers',[]):
        for issue in layer.get('issues',[]):
            clusters=cluster_gate_blockers({'status':'blocked','dangerous_layers':[
                {'z_mm':layer['z_mm'],'issues':[issue]}]})
            for cluster in clusters:
                result.append(replace(cluster,cluster_id=f'defect_{len(result):04d}'))
    return result
