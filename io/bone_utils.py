"""Shared export-time helpers for bone naming and duplicated-bone detection."""
import re

_NUMERIC_SUFFIX = re.compile(r'\.\d+$')
# Anything Maya (and a clean League pipeline) won't accept in a node/joint name.
_ILLEGAL_NAME_CHARS = re.compile(r'[^A-Za-z0-9_]')


def clean_export_bone_name(name):
    """Strip Blender's numeric duplicate suffix (.001, .002 ...) ONLY.

    Non-numeric dot suffixes (Hand.L, Bip01.Spine) are part of the bone's
    identity and must survive export unchanged — stripping at the first dot
    collapses left/right bones into one joint hash and corrupts the export.
    """
    return _NUMERIC_SUFFIX.sub('', name)


def sanitize_illegal_chars(name):
    """Replace every character that isn't a letter, digit, or underscore with '_'.

    Skeletons imported from FBX/other games often carry names like
    'bip001-pelvis' or 'Bip001 Spine' (dashes, spaces). Those characters are
    ILLEGAL in Maya node names — Maya silently renames them on import, which
    desyncs the joint hierarchy and changes the bone-name hashes the game and
    .anm files rely on. Applied consistently across SKL/SKN/ANM so the hashes
    stay matched. A name that's already clean (typical League bones) is returned
    unchanged, so this is a no-op for standard skins.
    """
    return _ILLEGAL_NAME_CHARS.sub('_', name)


def detect_offset_clones(armature_obj):
    """Only explicitly created offset parents are controls; names imply no role."""
    return {pb.name: pb['lol_offset_parent_for'] for pb in armature_obj.pose.bones
            if pb.get('lol_offset_parent_for')}


def find_duplicate_native_index_bones(armature_obj):
    from .skeleton import native_owners
    owners = native_owners(armature_obj)
    return [(pb.name, int(pb['native_bone_index']), owners[int(pb['native_bone_index'])].name)
            for pb in armature_obj.pose.bones if pb.get('native_bone_index') is not None
            and owners[int(pb['native_bone_index'])].name != pb.name]


def find_unbaked_pose_offsets(armature_obj):
    # Static offsets are included in the paired frame-zero mesh/skeleton bake.
    return []
