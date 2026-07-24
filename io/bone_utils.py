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
        elif len(no_sfx) == 0:
            # Exotic all-suffixed spliced pair: the canonical is the single bone
            # whose parent shares the same native index (the deepest of the
            # pair).  Require it to be unambiguous — MORE than one such bone
            # means the user duplicated a skeleton bone into several
            # independently-named new bones (e.g. cape/cloth chains), which is
            # NOT a clone group and must not be spliced.
            same_parent = [b for b in grp
                           if b.parent is not None
                           and b.parent.get("native_bone_index") is not None
                           and int(b.parent.get("native_bone_index")) == idx]
            if len(same_parent) == 1:
                target = same_parent[0]
        # len(no_sfx) >= 2: multiple independently-named bones share one native
        # index — a "duplicated a skeleton bone into new bones" shape (Shift+D
        # copies the native props), not offset clones.  Abstain so nothing gets
        # spliced/reparented; find_duplicate_native_index_bones flags them and
        # LOL_OT_ConvertToNewBone strips the stale props.
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


def find_duplicate_native_index_bones(armature_obj):
    """Bones carrying a native_bone_index copied from another bone (Shift+D).

    Blender copies pose-bone custom props when a bone is duplicated, so a
    duplicate of a skeleton bone inherits the original's native_bone_index and
    bind data.  When such a duplicate is used as a fresh new bone — a cape,
    cloth or accessory chain root — that stale index makes the exporter mistake
    it for the original joint: it re-plants the duplicate onto the original's
    native bind (collapsing it to the wrong position/orientation) instead of
    exporting it at its authored placement.

    Returns a list of (bone_name, shared_index, canonical_name) for every stale
    duplicate that is NOT already handled as a recognised offset clone.  The
    canonical is the genuine owner of the index — the offset-clone target if
    there is one, otherwise the bone still sitting in its native hierarchy slot.
    LOL_OT_ConvertToNewBone strips the stale props from these so they export as
    real new joints.  A native index owned by a single bone is never flagged.
    """
    clones = detect_offset_clones(armature_obj)

    groups = {}
    for pb in armature_obj.pose.bones:
        idx = pb.get("native_bone_index")
        if idx is not None:
            groups.setdefault(int(idx), []).append(pb)

    flagged = []
    for idx, grp in groups.items():
        if len(grp) < 2:
            continue

        grp_clone_names = {b.name for b in grp if b.name in clones}
        grp_targets = {clones[n] for n in grp_clone_names}

        canonical = None
        # 1. The offset-clone target, when this is a recognised clone group.
        for b in grp:
            if b.name in grp_targets:
                canonical = b
                break
        # 2. Otherwise the bone still parented under its native parent — the one
        #    that never moved out of its original skeleton slot.
        if canonical is None:
            for b in grp:
                cur = b.parent.name if b.parent else ""
                if cur == b.get("native_parent", ""):
                    canonical = b
                    break
        # 3. Fallbacks: a bone without a numeric suffix, else the first.
        if canonical is None:
            no_sfx = [b for b in grp if not _NUMERIC_SUFFIX.search(b.name)]
            canonical = no_sfx[0] if no_sfx else grp[0]

        for b in grp:
            if b is canonical or b.name in grp_clone_names:
                continue
            flagged.append((b.name, idx, canonical.name))
    return flagged


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
