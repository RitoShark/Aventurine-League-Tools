"""ANM animation importer - Matrix-based robust transform conversion"""
import bpy
import mathutils
import math
import os
from ..utils.binary_utils import Hash
from ..utils.backend import require_ritoshark
from . import import_skl


class ANMPose:
    __slots__ = ('translation', 'rotation', 'scale')
    
    def __init__(self):
        self.translation = None
        self.rotation = None
        self.scale = None


class ANMTrack:
    __slots__ = ('joint_hash', 'poses')
    
    def __init__(self, joint_hash):
        self.joint_hash = joint_hash
        self.poses = {} # f -> ANMPose


class ANMData:
    __slots__ = ('fps', 'duration', 'tracks', 'frame_count')
    
    def __init__(self):
        self.fps = 30.0
        self.duration = 0.0
        self.tracks = []
        self.frame_count = 0


def read_anm(filepath):
    source = require_ritoshark().Anm.from_path(str(filepath))
    anm = ANMData()
    anm.fps = source.fps
    anm.frame_count = source.frame_count
    anm.duration = source.frame_count / source.fps
    for item in source.tracks:
        track = ANMTrack(item.joint_hash)
        for frame in item.frames:
            pose = ANMPose()
            x, y, z, w = frame.rotation
            pose.rotation = mathutils.Quaternion((w, x, y, z))
            pose.translation = mathutils.Vector(frame.translation) * import_skl.IMPORT_SCALE
            pose.scale = mathutils.Vector(frame.scale)
            track.poses[int(round(frame.time * source.fps))] = pose
        anm.tracks.append(track)
    return anm


def apply_anm(anm, armature_obj, frame_offset=0, flip=False, adapt_to_edits=False, skip_custom_parent_pin=False):
    """Apply ANM animation to armature using fast batch FCurve operations.

    Unified correction math that handles both moved and reparented bones:
      - V_global comes from stored native_matrix_local (stable corrections)
      - N_global comes from stored native_global_rest_mat (stable)
      - C_parent is looked up via stored native_parent (handles reparenting)
      - rest_v_local uses the CURRENT parent chain (handles moved bones)

    adapt_to_edits is kept for backwards compatibility but no longer changes
    behavior — the unified path handles both cases.
    """
    if armature_obj.type != 'ARMATURE':
        return

    # Read pose scales before any mode_set that could trigger re-evaluation.
    # Custom bones must keep their user-set scale so children's world positions
    # stay at the correct scaled offset.
    saved_pose_scales = {pb.name: (pb.scale.x, pb.scale.y, pb.scale.z)
                         for pb in armature_obj.pose.bones}

    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='POSE')

    # Bone map: Hash -> PoseBone
    bone_map = {}
    for bone in armature_obj.pose.bones:
        bone.rotation_mode = 'QUATERNION'
        h = Hash.elf(bone.name)
        bone_map[h] = bone

    # Set scene settings (only if not inserting at offset)
    scene = bpy.context.scene
    scene.render.fps = int(max(1, anm.fps))
    if frame_offset == 0:
        scene.frame_start = 0
        scene.frame_end = max(0, anm.frame_count)

    # Matrix P: X'=-x, Y'=-z, Z'=y
    P = mathutils.Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    P_inv = P.inverted()

    # --- 1. Reconstruct NATIVE Global Rest Pose Hierarchy ---
    native_global_rest = {}

    def get_native_global(pb):
        if pb.name in native_global_rest:
            return native_global_rest[pb.name]

        # Prefer stored global rest matrix — immune to parent chain edits
        stored = pb.get("native_global_rest_mat")
        if stored and len(stored) == 16:
            g_mat = mathutils.Matrix((
                stored[0:4], stored[4:8], stored[8:12], stored[12:16]
            ))
            native_global_rest[pb.name] = g_mat
            return g_mat

        # Fallback for user-created bones or old imports without stored globals:
        # use visual global rest so correction = identity
        native_global_rest[pb.name] = pb.bone.matrix_local.copy()
        return native_global_rest[pb.name]

    for pbone in armature_obj.pose.bones:
        get_native_global(pbone)

    # --- 2. Calculate Correction Matrices ---
    # V_global always comes from the stored native_matrix_local so corrections
    # stay stable regardless of bone edits (reparenting, moving). rest_v_local
    # further down uses the CURRENT bone positions so the animation still
    # adapts to moved bones.
    def _get_v_global(pb):
        stored = pb.get("native_matrix_local")
        if stored and len(stored) == 16:
            return mathutils.Matrix((
                stored[0:4], stored[4:8], stored[8:12], stored[12:16]
            ))
        return pb.bone.matrix_local

    corrections = {}

    for pbone in armature_obj.pose.bones:
        v_global = _get_v_global(pbone)
        n_global = native_global_rest[pbone.name]
        try:
            corrections[pbone.name] = n_global.inverted() @ v_global
        except ValueError:
            corrections[pbone.name] = mathutils.Matrix.Identity(4)

    # --- 3. Collect all keyframe data (first pass) ---
    tracks_dict = {t.joint_hash: t for t in anm.tracks}

    # Build the canonical set of native bone NAMES.
    # Each SKL bone has a unique native_bone_index. When the user duplicates a
    # native bone, the copy inherits that index — so two bones can share one.
    # Among duplicates we must keep the REAL native bone, not the custom
    # intermediate. Two tie-breaking rules, applied in priority order:
    #
    # 1. Suffix rule (primary): the bone WITHOUT a Blender suffix (.001/.002…)
    #    is preferred over one with a suffix — works for the common case where
    #    the custom intermediate is named "R_Clavicle.001".
    #
    # 2. Parent-index rule (secondary): when both candidates have the same
    #    suffix status, check which one's Blender parent shares the same
    #    native_bone_index.  The custom intermediate was duplicated from the
    #    native bone, so it inherits the same index.  The native bone was then
    #    reparented *under* that custom intermediate — so the native bone's
    #    Blender parent has the SAME native_bone_index as itself.  The custom
    #    intermediate's parent is the genuine native parent bone, which has a
    #    DIFFERENT index.  This requires no stored native_parent property and
    #    works regardless of whether the custom bone is named "R_Clavicle.001",
    #    "R_test", or anything else.

    def _parent_shares_index(pb):
        """True if pb's Blender parent has the same native_bone_index as pb.
        Indicates pb is the real native bone (reparented under the custom
        intermediate which was duplicated from it and shares its index)."""
        if pb.parent is None:
            return False
        p_idx = pb.parent.get("native_bone_index")
        m_idx = pb.get("native_bone_index")
        if p_idx is None or m_idx is None:
            return False
        return int(p_idx) == int(m_idx)

    _seen_idx: dict = {}   # idx -> (has_suffix: bool, name: str)
    native_bone_names: set = set()
    for _pb in armature_obj.pose.bones:
        _idx = _pb.get("native_bone_index")
        if _idx is None:
            continue
        _has_suffix = '.' in _pb.name
        if _idx not in _seen_idx:
            _seen_idx[_idx] = (_has_suffix, _pb.name)
            native_bone_names.add(_pb.name)
        else:
            _ex_has_suffix, _ex_name = _seen_idx[_idx]
            # Rule 1: no-suffix beats suffix (handles "R_Clavicle" vs "R_Clavicle.001")
            if not _has_suffix and _ex_has_suffix:
                native_bone_names.discard(_ex_name)
                _seen_idx[_idx] = (False, _pb.name)
                native_bone_names.add(_pb.name)
            elif _has_suffix == _ex_has_suffix:
                # Rule 2: same suffix status — use parent-index heuristic.
                # The native bone's parent is the custom intermediate (same index).
                # The custom intermediate's parent is the original native parent (different index).
                _ex_pb = armature_obj.pose.bones.get(_ex_name)
                _cur_is_native = _parent_shares_index(_pb)
                _ex_is_native  = bool(_ex_pb and _parent_shares_index(_ex_pb))
                if _cur_is_native and not _ex_is_native:
                    # Current is native (its parent is the custom intermediate); swap
                    native_bone_names.discard(_ex_name)
                    _seen_idx[_idx] = (_has_suffix, _pb.name)
                    native_bone_names.add(_pb.name)
                # else: keep existing (it's native, or both indeterminate — first seen wins)

    def _is_native_pb(pb):
        return pb.name in native_bone_names

    # Native bones whose DIRECT Blender parent is a custom bone.
    # Their children (e.g. R_Shoulder under R_Clavicle) need special
    # C_parent treatment in visual-batch mode.
    _has_custom_parent_set: set = set()
    for _pb in armature_obj.pose.bones:
        if _pb.name in native_bone_names:
            if _pb.parent is not None and not _is_native_pb(_pb.parent):
                _has_custom_parent_set.add(_pb.name)

    def _get_stored_ml(pb):
        """Return import-time armature-space matrix (native_matrix_local), or
        fall back to the current bone matrix if the property is absent."""
        stored = pb.get("native_matrix_local")
        if stored and len(stored) == 16:
            return mathutils.Matrix([stored[0:4], stored[4:8], stored[8:12], stored[12:16]])
        return pb.bone.matrix_local.copy()

    # Data structure: bone_keyframes[bone_name] = {
    #   'location': {frame: (x, y, z), ...},
    #   'rotation_quaternion': {frame: (w, x, y, z), ...},
    #   'scale': {frame: (x, y, z), ...}
    # }
    bone_keyframes = {}
    matched_count = 0

    # Pre-populate identity keyframes for bones that won't get full animation data.
    # Without explicit action channels, Blender falls back to whatever pbone.location /
    # pbone.scale is stored on the bone — which may be leftover from a previously
    # applied animation that had full position/scale tracks. That stale data causes
    # children to appear at wrong world positions.
    #
    # Custom (non-native) bones: full identity (loc=0, rot=identity, scale=saved)
    # Native bones whose Blender parent is a custom bone (is_custom_parent): only
    #   loc=0 and scale=1 are pinned — rotation will be filled by the main loop.
    id_frames = {0, max(1, anm.frame_count)}
    for pbone in armature_obj.pose.bones:
        if pbone.name not in native_bone_names:
            # Custom bone — full identity pin
            s = saved_pose_scales.get(pbone.name, (1.0, 1.0, 1.0))
            bone_keyframes[pbone.name] = {
                'location':             {f: (0.0, 0.0, 0.0) for f in id_frames},
                'rotation_quaternion':  {f: (1.0, 0.0, 0.0, 0.0) for f in id_frames},
                'scale':                {f: s for f in id_frames},
            }
        else:
            # Native bone with a custom bone as its direct Blender parent.
            # In normal import: only rotation is animated; loc/scale are locked to
            # rest to prevent stale stored pose values from displacing children.
            # In batch-visual mode (skip_custom_parent_pin=True): apply full TRS so
            # write_anm can compute v_local_anim = X_world⁻¹ @ B_world correctly.
            if pbone.parent is not None and not _is_native_pb(pbone.parent):
                if not skip_custom_parent_pin:
                    has_track = Hash.elf(pbone.name) in tracks_dict
                    bone_keyframes[pbone.name] = {
                        'location':             {f: (0.0, 0.0, 0.0) for f in id_frames},
                        'rotation_quaternion':  {} if has_track else {f: (1.0, 0.0, 0.0, 0.0) for f in id_frames},
                        'scale':                {f: (1.0, 1.0, 1.0) for f in id_frames},
                    }

    for pbone in armature_obj.pose.bones:
        pbone_hash = Hash.elf(pbone.name)
        track = tracks_dict.get(pbone_hash)
        if track:
            matched_count += 1

        # Skip custom (non-native) bones — already handled by identity pre-pass.
        if pbone.name not in native_bone_names:
            continue

        # ── Correction matrices ─────────────────────────────────────────────
        C_child = corrections[pbone.name]
        native_parent = pbone.get("native_parent", "")

        is_custom_parent = (pbone.parent is not None and
                            not _is_native_pb(pbone.parent))

        if is_custom_parent:
            # Walk up past all custom bones to find the first native ancestor.
            native_anc_pb = None
            cur = pbone.parent
            while cur is not None:
                if _is_native_pb(cur):
                    native_anc_pb = cur
                    break
                cur = cur.parent

            if native_anc_pb and native_anc_pb.name in corrections:
                C_parent = corrections[native_anc_pb.name]
                try:
                    rest_v_local = _get_stored_ml(native_anc_pb).inverted() @ _get_stored_ml(pbone)
                except ValueError:
                    rest_v_local = _get_stored_ml(pbone)
            elif native_parent and native_parent in corrections:
                C_parent = corrections[native_parent]
                np_pb = armature_obj.pose.bones.get(native_parent)
                if np_pb:
                    try:
                        rest_v_local = _get_stored_ml(np_pb).inverted() @ _get_stored_ml(pbone)
                    except ValueError:
                        rest_v_local = _get_stored_ml(pbone)
                else:
                    rest_v_local = _get_stored_ml(pbone)
            else:
                C_parent = mathutils.Matrix.Identity(4)
                rest_v_local = _get_stored_ml(pbone)
        else:
            # Normal path: prefer stored native_parent, fall back to current.
            if native_parent and native_parent in corrections:
                if skip_custom_parent_pin and native_parent in _has_custom_parent_set:
                    C_parent = mathutils.Matrix.Identity(4)
                else:
                    C_parent = corrections[native_parent]
            elif pbone.parent:
                if skip_custom_parent_pin and pbone.parent.name in _has_custom_parent_set:
                    C_parent = mathutils.Matrix.Identity(4)
                else:
                    C_parent = corrections[pbone.parent.name]
            else:
                C_parent = mathutils.Matrix.Identity(4)

        # Visual batch mode (skip_custom_parent_pin=True): override corrections
        # only for the bones that sit at the boundary of the custom-bone insertion.
        #
        # 1. is_custom_parent bones (e.g. R_Clavicle whose Blender parent is the
        #    inserted R_Clavicle.001): use identity C_parent/C_child so write_anm
        #    (visual_mode, also all-identity) sees the raw Blender-space local.
        #    rest_v_local is NOT changed — it stays M_ancestor⁻¹ @ M_bone, which
        #    makes write_anm export l'_clav = N_rest_local⁻¹ @ l_clav, exactly
        #    what the visual SKL expects.
        #
        # 2. Direct children of an is_custom_parent bone (e.g. R_Shoulder, whose
        #    native parent R_Clavicle is in _has_custom_parent_set): same identity
        #    treatment so the relative transform is captured correctly.
        #
        # All other bones keep their normal (non-identity) corrections: write_anm
        # receives a Blender pose derived with the same corrections, so they cancel
        # on export and the original game value is recovered.
        if is_custom_parent and skip_custom_parent_pin:
            C_parent = mathutils.Matrix.Identity(4)
            C_child = mathutils.Matrix.Identity(4)
        elif not is_custom_parent and skip_custom_parent_pin:
            _np_in_custom = (
                (native_parent and native_parent in _has_custom_parent_set) or
                (pbone.parent and pbone.parent.name in _has_custom_parent_set)
            )
            if _np_in_custom:
                C_parent = mathutils.Matrix.Identity(4)
                C_child = mathutils.Matrix.Identity(4)

        try:
            C_parent_inv = C_parent.inverted()
        except ValueError:
            C_parent_inv = mathutils.Matrix.Identity(4)

        # ── Rest local matrix (normal path) ─────────────────────────────────
        # Use stored import-time matrices so rest_v_local is consistent with the
        # native animation values (which also reference import-time positions).
        # This keeps the bind-frame delta = identity and ensures animation frames
        # produce the correct delta from rest, even after "apply pose as rest".
        if not is_custom_parent:
            if pbone.parent:
                try:
                    rest_v_local = _get_stored_ml(pbone.parent).inverted() @ _get_stored_ml(pbone)
                except ValueError:
                    rest_v_local = _get_stored_ml(pbone)
            else:
                rest_v_local = _get_stored_ml(pbone)
        try:
            rest_v_local_inv = rest_v_local.inverted()
        except ValueError:
            rest_v_local_inv = mathutils.Matrix.Identity(4)

        # Fallback values (native bind pose)
        nb_t = pbone.get("native_bind_t")
        if nb_t:
            def_t = mathutils.Vector(nb_t)
            def_r = mathutils.Quaternion(pbone.get("native_bind_r"))
            s_val = pbone.get("native_bind_s")
            def_s = mathutils.Vector(s_val) if s_val else mathutils.Vector((1,1,1))
        else:
            def_t = mathutils.Vector((0,0,0))
            def_r = mathutils.Quaternion((1,0,0,0))
            def_s = mathutils.Vector((1,1,1))

        # Initialize keyframe storage for this bone.
        # For is_custom_parent bones the pre-pass already wrote zeroed loc/scale;
        # reuse that dict so those anchor keyframes survive into the final output.
        if pbone.name in bone_keyframes:
            bone_data = bone_keyframes[pbone.name]
        else:
            bone_data = {
                'location': {},
                'rotation_quaternion': {},
                'scale': {}
            }

        def compute_basis(n_local_B):
            """Convert native local matrix to Blender basis values."""
            v_local = C_parent_inv @ n_local_B @ C_child
            basis_mat = rest_v_local_inv @ v_local
            return basis_mat.decompose()  # Returns (loc, rot, sca)

        # Only process bones that have animation tracks
        if not track:
            continue

        # Keyframe 0 (Bind Pose) - Only when creating new action
        if frame_offset == 0:
            lm_t = mathutils.Matrix.Translation((def_t.x, def_t.y, def_t.z))
            lm_r = def_r.to_matrix().to_4x4()
            lm_s = mathutils.Matrix.Diagonal((def_s.x, def_s.y, def_s.z, 1.0))
            n_bind_B = P @ (lm_t @ lm_r @ lm_s) @ P_inv

            loc, rot, sca = compute_basis(n_bind_B)
            if not is_custom_parent or skip_custom_parent_pin:
                bone_data['location'][0] = (loc.x, loc.y, loc.z)
            bone_data['rotation_quaternion'][0] = (rot.w, rot.x, rot.y, rot.z)
            if not is_custom_parent or skip_custom_parent_pin:
                bone_data['scale'][0] = (sca.x, sca.y, sca.z)

        # Process animation frames
        for f_id, pose in track.poses.items():
            n_t = pose.translation if (pose and pose.translation) else None
            n_r = pose.rotation if (pose and pose.rotation) else None
            n_s = pose.scale if (pose and pose.scale) else None

            # Use fallbacks for matrix construction
            cur_t = n_t if n_t is not None else def_t
            cur_r = n_r if n_r is not None else def_r
            cur_s = n_s if n_s is not None else def_s

            # Apply coordinate flip (mirrors export flip logic)
            if flip:
                cur_t = mathutils.Vector((-cur_t.x, cur_t.y, cur_t.z))
                cur_r = mathutils.Quaternion((cur_r.w, cur_r.x, -cur_r.y, -cur_r.z))

            # Build Native Matrix
            lm_t = mathutils.Matrix.Translation((cur_t.x, cur_t.y, cur_t.z))
            lm_r = cur_r.to_matrix().to_4x4()
            lm_s = mathutils.Matrix.Diagonal((cur_s.x, cur_s.y, cur_s.z, 1.0))
            l_mat = lm_t @ lm_r @ lm_s

            # Transform to Blender Space
            N_target_B = P @ l_mat @ P_inv

            loc, rot, sca = compute_basis(N_target_B)
            frame = frame_offset + f_id + 1

            # Only store keyframes for components that have data
            # For bones under a custom parent: skip location/scale so they stay at
            # their Blender rest position; only rotation follows the animation.
            if n_t is not None and (not is_custom_parent or skip_custom_parent_pin):
                bone_data['location'][frame] = (loc.x, loc.y, loc.z)
            if n_r is not None:
                bone_data['rotation_quaternion'][frame] = (rot.w, rot.x, rot.y, rot.z)
            if n_s is not None and (not is_custom_parent or skip_custom_parent_pin):
                bone_data['scale'][frame] = (sca.x, sca.y, sca.z)

        # Only store if there's actual keyframe data
        if any(bone_data[k] for k in bone_data):
            bone_keyframes[pbone.name] = bone_data

    # --- 3b. Fix quaternion sign continuity ---
    # The "smallest three" quaternion compression always reconstructs the
    # dropped component as positive (via sqrt), but different components may
    # be dropped at adjacent frames.  This can flip the overall quaternion
    # sign, causing Blender's interpolation to take the long path and
    # produce wild rotation artifacts at in-between frames.
    for bone_name, bdata in bone_keyframes.items():
        rots = bdata.get('rotation_quaternion')
        if not rots or len(rots) < 2:
            continue
        sorted_frames = sorted(rots.keys())
        prev_q = rots[sorted_frames[0]]
        for i in range(1, len(sorted_frames)):
            f = sorted_frames[i]
            cur_q = rots[f]
            dot = (prev_q[0] * cur_q[0] + prev_q[1] * cur_q[1]
                   + prev_q[2] * cur_q[2] + prev_q[3] * cur_q[3])
            if dot < 0:
                cur_q = (-cur_q[0], -cur_q[1], -cur_q[2], -cur_q[3])
                rots[f] = cur_q
            prev_q = cur_q


    # --- 4. Write keyframes - hybrid approach with Blender 5.0 compatibility ---
    # Use keyframe_insert once to create FCurves, then direct access for speed
    # Blender 5.0 moved fcurves to a new location in the layered action system

    action = armature_obj.animation_data.action
    total_keyframes = 0

    # Detect how to access fcurves based on Blender version/API
    fcurves_collection = None
    use_fast_path = False
    
    # Try different methods to access fcurves
    if hasattr(action, 'fcurves'):
        # Blender 4.3 and earlier - fcurves directly on action
        fcurves_collection = action.fcurves
        use_fast_path = True
    elif hasattr(action, 'layers') and len(action.layers) > 0:
        # Blender 4.4+ / 5.0+ - Layered/Slotted Actions
        # FCurves are now at: action.layers[0].strips[0].channelbag(slot).fcurves
        # But for armatures, we need to ensure we have the right slot binding
        try:
            layer = action.layers[0]
            if len(layer.strips) > 0:
                strip = layer.strips[0]
                # Get the appropriate slot - for armatures, usually the first or default
                if hasattr(action, 'slots') and len(action.slots) > 0:
                    slot = action.slots[0]
                    channelbag = strip.channelbag(slot)
                    if hasattr(channelbag, 'fcurves'):
                        fcurves_collection = channelbag.fcurves
                        use_fast_path = True
        except:
            # If anything fails, fall back to slow but safe method
            use_fast_path = False

    for bone_name, bone_data in bone_keyframes.items():

        pbone = armature_obj.pose.bones.get(bone_name)
        if not pbone:
            continue

        # Process each property type
        for prop_name, num_channels in [('location', 3), ('rotation_quaternion', 4), ('scale', 3)]:
            frames_dict = bone_data[prop_name]
            if not frames_dict:
                continue

            sorted_frames = sorted(frames_dict.keys())
            if not sorted_frames:
                continue

            if use_fast_path and fcurves_collection is not None:
                # ULTRA-FAST PATH: C-level memory copying via foreach_set
                data_path = f'pose.bones["{bone_name}"].{prop_name}'
                
                for i in range(num_channels):
                    fc = fcurves_collection.find(data_path, index=i)
                    if not fc:
                        fc = fcurves_collection.new(data_path, index=i, action_group=bone_name)
                        
                    if len(fc.keyframe_points):
                        # Insert replaces matching frames and preserves keys/handles
                        # outside the inserted clip. foreach_set requires the entire
                        # collection, so it is only safe on a new, empty curve.
                        for frame in sorted_frames:
                            fc.keyframe_points.insert(
                                float(frame), float(frames_dict[frame][i]), options={'FAST'})
                    else:
                        fc.keyframe_points.add(len(sorted_frames))
                        coords = [value for frame in sorted_frames
                                  for value in (float(frame), float(frames_dict[frame][i]))]
                        fc.keyframe_points.foreach_set('co', coords)
                    fc.update()
                    
                    total_keyframes += len(sorted_frames)
            else:
                # Slow path for Blender 5.0+: use keyframe_insert for all frames
                for frame in sorted_frames:
                    value = frames_dict[frame]
                    setattr(pbone, prop_name, value)
                    pbone.keyframe_insert(data_path=prop_name, frame=frame)
                    total_keyframes += num_channels

    # Blender 4.4/4.5 slotted actions: the legacy action.fcurves proxy creates
    # keyframes inside a slot, but doesn't auto-bind that slot to the armature.
    # Without this, the animation data exists but Blender never evaluates it.
    # See https://projects.blender.org/blender/blender/issues/135236
    anim_data = armature_obj.animation_data
    if anim_data and anim_data.action:
        if hasattr(anim_data, 'action_slot') and anim_data.action_slot is None:
            act = anim_data.action
            if hasattr(act, 'slots') and len(act.slots) > 0:
                anim_data.action_slot = act.slots[0]
        # Remember which mode was used to import this action so export can default to it
        anim_data.action["lol_adapt_to_edits"] = bool(adapt_to_edits)

    bpy.ops.object.mode_set(mode='OBJECT')

    # Force update and reset to start frame
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(bpy.context.scene.frame_start)


def load(operator, context, filepath, create_new_action=True, insert_frame=0, flip=False, adapt_to_edits=False):
    armature_obj = context.active_object
    if not armature_obj or armature_obj.type != 'ARMATURE':
        for obj in context.scene.objects:
            if obj.type == 'ARMATURE':
                armature_obj = obj
                break
                
    if not armature_obj:
        operator.report({'ERROR'}, "No active armature found to apply animation to")
        return {'CANCELLED'}
        
    try:
        anm = read_anm(filepath)
        
        # Get action name from filename (without extension)
        action_name = os.path.splitext(os.path.basename(filepath))[0]
        
        if create_new_action:
            # Create a new action
            if not armature_obj.animation_data:
                armature_obj.animation_data_create()
            
            # Create new action with the ANM filename
            new_action = bpy.data.actions.new(name=action_name)
            armature_obj.animation_data.action = new_action
            
            # Apply animation starting at frame 0 (with +1 offset for bind pose)
            apply_anm(anm, armature_obj, frame_offset=0, flip=flip, adapt_to_edits=adapt_to_edits)
            
            # Store info on the action
            new_action["lol_anm_filepath"] = filepath
            new_action["lol_anm_filename"] = os.path.basename(filepath)
            
            operator.report({'INFO'}, f"Imported animation '{action_name}': {anm.frame_count} frames")
        else:
            # Insert into existing action at specified frame
            if not armature_obj.animation_data or not armature_obj.animation_data.action:
                operator.report({'ERROR'}, "No existing action to insert into. Use 'New Action' mode first.")
                return {'CANCELLED'}
            
            # Apply animation with frame offset
            apply_anm(anm, armature_obj, frame_offset=insert_frame, flip=flip, adapt_to_edits=adapt_to_edits)
            
            # Extend scene end frame if needed
            new_end = insert_frame + anm.frame_count
            if context.scene.frame_end < new_end:
                context.scene.frame_end = new_end
            
            operator.report({'INFO'}, f"Inserted '{action_name}' at frame {insert_frame}: {anm.frame_count} frames")
        
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed to load ANM: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
