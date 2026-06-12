"""Shared export-time helpers for bone naming and duplicated-bone detection."""
import re

_NUMERIC_SUFFIX = re.compile(r'\.\d+$')


def clean_export_bone_name(name):
    """Strip Blender's numeric duplicate suffix (.001, .002 ...) ONLY.

    Non-numeric dot suffixes (Hand.L, Bip01.Spine) are part of the bone's
    identity and must survive export unchanged — stripping at the first dot
    collapses left/right bones into one joint hash and corrupts the export.
    """
    return _NUMERIC_SUFFIX.sub('', name)


def detect_offset_clones(armature_obj):
    """Find duplicated-native-bone "offset holder" clones.

    Returns {clone_name: target_native_name}.

    Two flavors exist:
    - Shift+D duplicates: Blender copies pose-bone custom props, so the clone
      SHARES native_bone_index with the original.  The canonical bone is the
      one without a numeric suffix (Rule 1), or — when both carry suffixes —
      the one whose parent shares the same index, i.e. the child of a spliced
      pair (Rule 2, matching apply_anm / the visual export machinery).
    - Fresh bones named '<native>.NNN' (e.g. extruded or renamed), which carry
      no native props at all.
    """
    groups = {}
    for pb in armature_obj.pose.bones:
        idx = pb.get("native_bone_index")
        if idx is not None:
            groups.setdefault(int(idx), []).append(pb)

    clones = {}
    for idx, grp in groups.items():
        if len(grp) < 2:
            continue
        no_sfx = [b for b in grp if not _NUMERIC_SUFFIX.search(b.name)]
        target = None
        if len(no_sfx) == 1:
            target = no_sfx[0]
        else:
            for b in grp:
                p = b.parent
                if (p is not None and p.get("native_bone_index") is not None
                        and int(p.get("native_bone_index")) == idx):
                    target = b
                    break
        if target is None:
            continue
        for b in grp:
            if b is not target:
                clones[b.name] = target.name

    for pb in armature_obj.pose.bones:
        if pb.get("native_bone_index") is not None or pb.name in clones:
            continue
        m = _NUMERIC_SUFFIX.search(pb.name)
        if not m:
            continue
        target_pb = armature_obj.pose.bones.get(pb.name[:m.start()])
        if target_pb is not None and target_pb.get("native_bone_index") is not None:
            clones[pb.name] = target_pb.name
    return clones


def find_unbaked_pose_offsets(armature_obj):
    """Offset clones carrying a Pose Mode position/rotation.

    Pose loc/rot on a clone never reaches the export (the offset is read from
    the rest pose; only the SCALE pose channel is sanctioned as the scale
    dial), so any such state is a user mistake worth warning about.
    Returns a list of clone bone names.
    """
    unbaked = []
    for clone_name in detect_offset_clones(armature_obj):
        pb = armature_obj.pose.bones.get(clone_name)
        if pb is None:
            continue
        basis = pb.matrix_basis
        loc = basis.to_translation()
        rot_angle = basis.to_quaternion().angle
        if loc.length > 1e-5 or min(abs(rot_angle), abs(6.2831853 - rot_angle)) > 1e-3:
            unbaked.append(clone_name)
    return unbaked
