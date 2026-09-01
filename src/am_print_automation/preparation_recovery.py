"""Resume obsolete failed preparation without regenerating or discarding history."""
from __future__ import annotations
from typing import Any, Mapping
from am_print_executor.preparation_version import PREPARATION_REVISION

PREPARATION_STAGES = (
    'bambu_slice', 'm3_printability', 'bambu_support_reslice', 'm3_support_printability',
    'bambu_regeneration_slice', 'm3_regeneration_printability',
    'bambu_regeneration_support_reslice', 'm3_regeneration_support_printability',
)


def can_recover_preparation(state: Mapping[str, Any]) -> bool:
    if state.get('status') not in {
        'needs_geometry_regeneration',
        'printability_blocked',
        'failed',
        'stopped',
    }:
        return False
    if state.get('preparation_revision') == PREPARATION_REVISION:
        return False
    stages = state.get('stages') or {}
    # No replay after any possible external side effect, even if the top-level
    # status was subsequently changed by a crash or another operation.
    for name in ('printer_upload', 'print_start'):
        record = stages.get(name) or {}
        if record.get('status') not in {None, 'pending', 'skipped'} or record.get('attempts', 0):
            return False
    if stages.get('m2_stl_handoff', {}).get('status') != 'completed':
        return False
    return any(
        stages.get(name, {}).get('status') == 'completed'
        and (result := stages[name].get('result') or {}).get('status') == 'blocked'
        and result.get('pipeline') in {
            'verified_support_orient_reslice_v3',
            'bambu_native_tree_support_v1',
        }
        and result.get('preparation_revision') != PREPARATION_REVISION
        for name in PREPARATION_STAGES if name.startswith('bambu_')
    )
