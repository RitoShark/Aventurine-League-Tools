import bpy
import os
import struct
import mathutils
import math
from ..utils.binary_utils import BinaryStream, Hash
from . import import_skl

def write_anm(filepath, armature_obj, fps=30.0, disable_scaling=False, disable_transforms=False, flip=False, adapt_to_edits=False, visual_mode=False):
    """Write Blender animation to ANM file (Uncompressed v4 format).

    Mirrors the unified import math:
      - V_global from stored native_matrix_local (stable corrections)
      - C_parent via stored native_parent (handles reparenting/custom bones)
      - animated local matrix computed against the CURRENT parent (handles moves)

    adapt_to_edits is kept for backwards compatibility but no longer changes
    behavior — the unified path handles both moved and reparented bones.
    """

    if not armature_obj.animation_data or not armature_obj.animation_data.action:
        raise Exception("No animation data found on armature")

    action = armature_obj.animation_data.action
    if disable_transforms:
        P = mathutils.Matrix.Identity(4)
        P_inv = mathutils.Matrix.Identity(4)
    else:
        P = mathutils.Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
        P_inv = P.inverted()
    
    # Sort bones by original import order (matching SKL export ordering)
    native_bones = []
    new_bones = []
    for b in armature_obj.pose.bones:
        idx = b.get("native_bone_index")
        if idx is not None:
            native_bones.append((int(idx), b))
        else:
            new_bones.append(b)
    native_bones.sort(key=lambda x: x[0])
    bones = [b for _, b in native_bones] + new_bones

    # In visual mode: build a set of custom-intermediate bone names to skip.
    # Custom intermediates are bones that share a native_bone_index with another
    # bone (they were duplicated from a native bone and inserted between it and its
    # parent).  The game keeps these at their SKL bind pose when no ANM track
    # exists, which is exactly correct — the native bone's visual transform is
    # already computed relative to the custom bone's rest position.  Including
    # custom bones with incorrectly computed transforms would break the animation.
    _visual_skip: set = set()
    if visual_mode:
        _idx_groups: dict = {}
        for _b in bones:
            _bidx = _b.get("native_bone_index")
            if _bidx is None:
                continue
            _idx_groups.setdefault(int(_bidx), []).append(_b)
        for _idx, _group in _idx_groups.items():
            if len(_group) < 2:
                continue
            # Rule 1: bone without a '.' in its name is the native one.
            _no_sfx  = [_b for _b in _group if '.' not in _b.name]
            _with_sfx = [_b for _b in _group if '.' in _b.name]
            if len(_no_sfx) == 1:
                for _b in _with_sfx:
                    _visual_skip.add(_b.name)
            else:
                # Rule 2: parent-index heuristic — the native bone's Blender parent
                # is the custom intermediate (same native_bone_index).
                _native_in_group = None
                for _b in _group:
                    if (_b.parent is not None and
                            _b.parent.get("native_bone_index") is not None and
                            int(_b.parent.get("native_bone_index")) == _idx):
                        _native_in_group = _b
                        break
                if _native_in_group is not None:
                    for _b in _group:
                        if _b is not _native_in_group:
                            _visual_skip.add(_b.name)

    # Frame range - skip frame 0 (bind pose) to match Maya's behavior
    # Import puts bind at frame 0, animation starts at frame 1
    frame_start = max(1, int(action.frame_range[0]))
    frame_end = int(action.frame_range[1])
    frame_count = max(1, frame_end - frame_start + 1)
    
    # Palettes for deduplication
    vec_palette = []
    vec_map = {}
    quat_palette = []
    quat_map = {}
    
    def add_to_vec_palette(v):
        key = (round(v.x, 6), round(v.y, 6), round(v.z, 6))
        if key not in vec_map:
            vec_map[key] = len(vec_palette)
            vec_palette.append(mathutils.Vector((v.x, v.y, v.z)))
        return vec_map[key]
    
    def add_to_quat_palette(q):
        q = q.normalized()
        key = (round(q.x, 6), round(q.y, 6), round(q.z, 6), round(q.w, 6))
        if key not in quat_map:
            quat_map[key] = len(quat_palette)
            quat_palette.append(mathutils.Quaternion((q.w, q.x, q.y, q.z)))
        return quat_map[key]

    # --- 1. Reconstruct Native Global Rest Hierarchy (MATCHING IMPORT) ---
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

    for pbone in bones:
        get_native_global(pbone)

    # --- 2. Calculate Correction Matrices (MATCHING IMPORT) ---
    # V_global always comes from stored native_matrix_local so corrections
    # stay stable regardless of bone edits.
    def _get_v_global(pb):
        stored = pb.get("native_matrix_local")
        if stored and len(stored) == 16:
            return mathutils.Matrix((
                stored[0:4], stored[4:8], stored[8:12], stored[12:16]
            ))
        return pb.bone.matrix_local

    corrections = {}
    if visual_mode:
        # Visual-mode export: no correction matrices. Every bone's animated
        # parent-relative transform is written as-is in League space.
        # This matches what the visual-pose SKL exporter bakes into the skeleton,
        # so the animations play back correctly against the visual skeleton ingame.
        for pbone in bones:
            corrections[pbone.name] = mathutils.Matrix.Identity(4)
    else:
        for pbone in bones:
            v_global = _get_v_global(pbone)  # Visual Global Rest
            n_global = native_global_rest[pbone.name]
            try:
                corrections[pbone.name] = n_global.inverted() @ v_global
            except ValueError:
                # Degenerate native global - use identity correction
                corrections[pbone.name] = mathutils.Matrix.Identity(4)

    # --- 3. Collect Frame Data ---
    joint_data = {}  # joint_hash -> list of (t_id, s_id, r_id) per frame

    # Detect NB (is_custom_parent) bones: native bones whose direct Blender parent
    # is a custom (non-native) bone inserted after SKL import.
    # These need an extra orientation correction on export that mirrors what the
    # importer does: the importer uses stored_ml(native_anc)^-1 @ stored_ml(NB)
    # as rest_v_local (not CI^-1 @ NB) and corrections[native_anc] as C_parent.
    # We store the native ancestor so we can reproduce that reference frame.
    _native_anim_parent = {}  # bone_name -> native ancestor PoseBone
    for _pb in bones:
        _np_name = _pb.get("native_parent", "")
        if _np_name and _pb.parent is not None and _pb.parent.name != _np_name:
            _np_pb = armature_obj.pose.bones.get(_np_name)
            if _np_pb is not None:
                _native_anim_parent[_pb.name] = _np_pb

    # ── CI rest-scale correction ─────────────────────────────────────────────
    # When a custom intermediate bone (CI, e.g. R_Clavicle.001) has been scaled
    # in rest pose its scale propagates through Blender's evaluation chain to all
    # native-bone descendants (NB, NC, GC …).  The visual position of each
    # descendant in Blender reflects this scale, but the game skeleton that will
    # receive the exported ANM has NO custom bone, so the exported translations
    # must be in native (un-scaled) units.
    #
    # Detection: walk from each bone up to its native ancestor.  Every bone in
    # that path whose Blender parent is the CI (i.e. an NB-type bone) exposes the
    # CI.  Collect all CIs into _ci_bones.
    #
    # Correction: at the REST FRAME (frame 0) compare each affected bone's
    # VISUAL scale (from pbone.matrix.decompose()[2]) to its NATIVE scale
    # (native_bind_s, or (1,1,1) if absent).  Record K = visual / native for
    # later division of the exported translation vector.

    # Step 1 – find all CI bones and their rest-pose scale.
    _ci_bones: dict = {}   # CI_name -> PoseBone
    for _nb_name, _anc_pb in _native_anim_parent.items():
        _nb_pb = armature_obj.pose.bones.get(_nb_name)
        if _nb_pb is None or _nb_pb.parent is None:
            continue
        # Every bone between NB's Blender parent and native_anc is a CI.
        _cur = _nb_pb.parent
        while _cur is not None and _cur.name != _anc_pb.name:
            _ci_bones[_cur.name] = _cur
            _cur = _cur.parent

    # Step 2 – evaluate poses at frame 0 to measure scale.
    _ci_scale_correction: dict = {}   # bone_name -> mathutils.Vector (Kx,Ky,Kz)

    _frame_before_diag = bpy.context.scene.frame_current
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()


    for _ci_name, _ci_pb in sorted(_ci_bones.items()):
        _ci_pose_scale = _ci_pb.scale.copy()
        _, _, _ci_ml_scale = _ci_pb.bone.matrix_local.decompose()
        _, _, _ci_vis_scale = _ci_pb.matrix.decompose()
        _ci_curr_pos = _ci_pb.bone.matrix_local.to_translation()
        # local-to-parent: reveals translational and scale offset introduced by CI
        if _ci_pb.parent:
            try:
                _ci_ltp = _ci_pb.parent.bone.matrix_local.inverted() @ _ci_pb.bone.matrix_local
                _ci_ltp_t, _ci_ltp_r, _ci_ltp_s = _ci_ltp.decompose()
            except Exception:
                pass

    # Step 3 – for every native bone exported, check if it has a CI ancestor
    #          and compute its scale ratio K from POSITION COMPARISON.
    #
    # Why position comparison?  When a CI bone is scaled in edit mode, Blender
    # encodes the scale as a positional shift — bone.matrix_local.decompose()[2]
    # always returns (1,1,1) for edit-mode scale.  The only reliable signal is
    # the difference between the bone's CURRENT armature-space position and the
    # ORIGINAL position stored in native_matrix_local at SKL import time.
    #
    # K[i] = curr_pos[i] / orig_pos[i]  (per-axis, safe division)
    #
    # This K is then used to divide the exported translation so that the
    # ANM plays back correctly on a skeleton without the CI bone.
    for _pbone in bones:
        if visual_mode and _pbone.name in _visual_skip:
            continue

        # Walk up the Blender parent chain to find any CI.
        _has_ci = False
        _cur = _pbone.parent
        while _cur is not None:
            if _cur.name in _ci_bones:
                _has_ci = True
                break
            _cur = _cur.parent

        if not _has_ci:
            continue

        # K = ratio of RELATIVE position (parent-space) now vs at SKL import.
        #
        # Why relative, not absolute?
        # If a scaled CI_A is above an unscaled CI_B, every bone below CI_A
        # (including CI_B's descendants) has a different absolute position.
        # But for CI_B's descendants, BOTH the native parent and the bone itself
        # shifted by the same CI_A delta, so (parent^{-1} @ bone) is UNCHANGED
        # → K = 1 → no spurious correction.  Only when CI sits directly between
        # this bone and its Blender parent does the relative position differ
        # → K ≠ 1 → correction is applied.
        _par = _pbone.parent
        if _par is not None:
            try:
                _curr_rel_mat = _par.bone.matrix_local.inverted() @ _pbone.bone.matrix_local
            except Exception:
                _curr_rel_mat = _pbone.bone.matrix_local.copy()

            _par_stored  = _par.get("native_matrix_local")
            _bone_stored = _pbone.get("native_matrix_local")
            if (_par_stored  and len(_par_stored)  == 16 and
                    _bone_stored and len(_bone_stored) == 16):
                _par_orig  = mathutils.Matrix([_par_stored[0:4],  _par_stored[4:8],
                                               _par_stored[8:12], _par_stored[12:16]])
                _bone_orig = mathutils.Matrix([_bone_stored[0:4],  _bone_stored[4:8],
                                               _bone_stored[8:12], _bone_stored[12:16]])
                try:
                    _orig_rel_mat = _par_orig.inverted() @ _bone_orig
                except Exception:
                    _orig_rel_mat = _bone_orig.copy()
            else:
                _orig_rel_mat = _curr_rel_mat  # no stored data → assume unchanged
        else:
            # Root bone — fall back to absolute comparison.
            _curr_rel_mat = _pbone.bone.matrix_local.copy()
            _bone_stored  = _pbone.get("native_matrix_local")
            if _bone_stored and len(_bone_stored) == 16:
                _orig_rel_mat = mathutils.Matrix([_bone_stored[0:4],  _bone_stored[4:8],
                                                  _bone_stored[8:12], _bone_stored[12:16]])
            else:
                _orig_rel_mat = _curr_rel_mat

        _curr_rel = _curr_rel_mat.to_translation()
        _orig_rel = _orig_rel_mat.to_translation()

        _Kx = (_curr_rel.x / _orig_rel.x) if abs(_orig_rel.x) > 1e-6 else 1.0
        _Ky = (_curr_rel.y / _orig_rel.y) if abs(_orig_rel.y) > 1e-6 else 1.0
        _Kz = (_curr_rel.z / _orig_rel.z) if abs(_orig_rel.z) > 1e-6 else 1.0
        _K  = mathutils.Vector((_Kx, _Ky, _Kz))
        _zero_axes = [ax for ax, v in zip('xyz', (_orig_rel.x, _orig_rel.y, _orig_rel.z))
                      if abs(v) <= 1e-6]

        _code_path = "NB" if _pbone.name in _native_anim_parent else \
                     "NC" if (_pbone.parent and _pbone.parent.name in _native_anim_parent) else \
                     "GP"


        _k_diff = (_K - mathutils.Vector((1.0, 1.0, 1.0))).length
        # NC bones use stored matrices, no K correction needed.
        # Threshold 2e-3: filters float drift, catches real CI scale corrections.
        if _code_path != "NC" and _k_diff > 2e-3:
            _ci_scale_correction[_pbone.name] = _K

    bpy.context.scene.frame_set(_frame_before_diag)

    current_frame_orig = bpy.context.scene.frame_current

    try:
        for f_idx in range(frame_count):
            frame = frame_start + f_idx
            bpy.context.scene.frame_set(frame)

            for pbone in bones:
                # Skip custom intermediate bones in visual mode — the game uses
                # their SKL bind pose when no ANM track is present, which is correct.
                if pbone.name in _visual_skip:
                    continue

                # Bone-to-bone local — same for every bone.
                anim_parent = pbone.parent
                if anim_parent:
                    try:
                        v_local_anim = anim_parent.matrix.inverted() @ pbone.matrix
                    except ValueError:
                        parent_mat = anim_parent.matrix.copy()
                        t, r, s = parent_mat.decompose()
                        min_scale = 0.00001
                        s_clamped = mathutils.Vector((
                            max(abs(s.x), min_scale) * (1 if s.x >= 0 else -1),
                            max(abs(s.y), min_scale) * (1 if s.y >= 0 else -1),
                            max(abs(s.z), min_scale) * (1 if s.z >= 0 else -1)
                        ))
                        parent_mat_safe = mathutils.Matrix.Translation(t) @ r.to_matrix().to_4x4() @ mathutils.Matrix.Diagonal(s_clamped.to_4d())
                        v_local_anim = parent_mat_safe.inverted() @ pbone.matrix
                else:
                    v_local_anim = pbone.matrix

                C_child = corrections[pbone.name]
                _native_anc = _native_anim_parent.get(pbone.name)
                # Also detect NC bones: their parent (NB) is a custom_parent bone.
                _parent_native_anc = (_native_anim_parent.get(pbone.parent.name)
                                      if pbone.parent else None)

                if pbone.parent:
                    C_parent = corrections.get(pbone.parent.name, mathutils.Matrix.Identity(4))
                else:
                    C_parent = mathutils.Matrix.Identity(4)

                # NB block: Blender parent is CI but game parent is native_anc.
                # Exact inverse of importer's compute_basis for is_custom_parent bones:
                #   basis_mat = (V_anc⁻¹ @ V_NB)⁻¹ @ C_anc⁻¹ @ n_local_B @ C_NB
                #   → n_local_B = C_anc @ (V_anc⁻¹ @ V_NB @ basis_mat) @ C_NB⁻¹
                # Since the importer pins loc=0 and scale=1, pbone.matrix_basis
                # is a pure rotation (the stored basis_mat rotation component).
                # In visual mode: skip — stored rest matrices no longer match the
                # edited rest pose.  Use the general path (live FK chain) instead.
                if _native_anc is not None and not visual_mode:
                    try:
                        anc_rest_inv = _get_v_global(_native_anc).inverted()
                        nb_stored_ml = _get_v_global(pbone)
                        v_local_anim = anc_rest_inv @ nb_stored_ml @ pbone.matrix_basis
                        C_parent = corrections.get(_native_anc.name, mathutils.Matrix.Identity(4))
                    except (ValueError, AttributeError):
                        pass  # fall through with CI-parent values

                # NC block: parent is NB (which is a custom_parent bone).
                # Exact inverse of importer's compute_basis for normal bones:
                #   basis_mat = (V_NB⁻¹ @ V_NC)⁻¹ @ C_NB⁻¹ @ n_local_B @ C_NC
                #   → n_local_B = C_NB @ (V_NB⁻¹ @ V_NC @ basis_mat) @ C_NC⁻¹
                # Using stored rest matrices directly instead of Blender's evaluated
                # NB.matrix avoids any CI-scale contamination in the pose chain.
                # In visual mode: skip for the same reason as the NB block above.
                elif _parent_native_anc is not None and not visual_mode:
                    try:
                        nb_stored_ml = _get_v_global(pbone.parent)
                        nc_stored_ml = _get_v_global(pbone)
                        v_local_anim = nb_stored_ml.inverted() @ nc_stored_ml @ pbone.matrix_basis
                    except (ValueError, AttributeError):
                        pass  # fall through with evaluated matrices
                else:
                    # General path — log if this bone has a CI ancestor
                    pass

                # ── CI scale correction (visual space) ─────────────────────
                # K is computed in Blender (visual) space, so the correction
                # must be applied to v_local_anim BEFORE the P coordinate
                # transform.  Applying it after (to game-space t) is a no-op
                # because visual Y maps to game Z, so K.y hits t.y ≈ 0.
                # Exempt: NB bones (translation pinned to native_bind_t below)
                #         NC bones (NC block uses stored matrices → already correct)
                # In visual mode: skip entirely — the live FK chain already
                # accounts for CI scale, so dividing by K would over-correct.
                _K_corr = _ci_scale_correction.get(pbone.name)
                if _K_corr is not None and _native_anc is None and _parent_native_anc is None and not visual_mode:
                    _t_va, _r_va, _s_va = v_local_anim.decompose()
                    _t_va_c = mathutils.Vector((
                        _t_va.x / _K_corr.x if abs(_K_corr.x) > 1e-8 else _t_va.x,
                        _t_va.y / _K_corr.y if abs(_K_corr.y) > 1e-8 else _t_va.y,
                        _t_va.z / _K_corr.z if abs(_K_corr.z) > 1e-8 else _t_va.z,
                    ))
                    v_local_anim = (mathutils.Matrix.Translation(_t_va_c) @
                                    _r_va.to_matrix().to_4x4() @
                                    mathutils.Matrix.Diagonal((*_s_va, 1.0)))

                try:
                    C_child_inv = C_child.inverted()
                except ValueError:
                    C_child_inv = mathutils.Matrix.Identity(4)

                n_local_B = C_parent @ v_local_anim @ C_child_inv
                n_local_L = P_inv @ n_local_B @ P
                t, r, s = n_local_L.decompose()

                # NB bones: pin translation to native rest value so the exported
                # track has constant T (game expects only rotation to animate).
                # In visual mode: skip — the visual rest T differs from native_bind_t
                # and we want the live animated translation to flow through.
                if _native_anc is not None and not visual_mode:
                    _nb_t = pbone.get("native_bind_t")
                    if _nb_t and len(_nb_t) >= 3:
                        t = mathutils.Vector((_nb_t[0], _nb_t[1], _nb_t[2]))

                # Apply coordinate correction for game compatibility
                # By default (flip disabled), export works correctly
                # When flip is enabled, apply coordinate correction for edge cases/debugging
                if flip:
                    t = mathutils.Vector((-t.x, t.y, t.z))
                    r = mathutils.Quaternion((r.w, r.x, -r.y, -r.z))

                # Add to palettes (scale translations back to game units)
                scale = 1.0 if disable_scaling else import_skl.EXPORT_SCALE
                t_id = add_to_vec_palette(t * scale)
                s_id = add_to_vec_palette(s)
                r_id = add_to_quat_palette(r)
                
                # Hash the bone name.
                # In visual mode: keep the full name including any .001 suffix so
                # custom intermediate bones (e.g. R_Clavicle.001) get their own
                # explicit ANM track.  Without this, R_Clavicle.001 would fall
                # back to whatever the viewer/game uses for missing bones — which
                # may be identity rather than the SKL bind pose, breaking the arm.
                # In normal mode: strip .001 so native duplicates collapse to the
                # same hash as the original bone (hash-collision fix below handles
                # data priority).
                if visual_mode:
                    bone_name = pbone.name
                else:
                    bone_name = pbone.name.split('.')[0] if '.' in pbone.name else pbone.name
                h = Hash.elf(bone_name)

                if h not in joint_data:
                    joint_data[h] = []

                if len(joint_data[h]) == f_idx:
                    joint_data[h].append((t_id, s_id, r_id))
                elif not visual_mode and '.' not in pbone.name and len(joint_data[h]) > f_idx:
                    # Non-visual mode: two bones share the same cleaned hash
                    # (e.g. "R_Clavicle" and "R_Clavicle.001").  The .001 duplicate
                    # was processed first and already wrote a slot for this frame.
                    # The no-suffix bone is the original native bone; its data wins.
                    joint_data[h][f_idx] = (t_id, s_id, r_id)
                    
    finally:
        bpy.context.scene.frame_set(current_frame_orig)

    # --- 4. Write Binary File ---
    sorted_hashes = sorted(joint_data.keys())
    
    with open(filepath, 'wb') as f:
        bs = BinaryStream(f)
        
        # Header
        bs.write_ascii('r3d2anmd')
        bs.write_uint32(4)  # version
        
        bs.write_uint32(0)  # filesize placeholder (offset 12)
        bs.write_uint32(0xBE0794D3, 0, 0)  # format token, unknown, flags
        
        bs.write_uint32(len(joint_data), frame_count)
        bs.write_float(1.0 / fps)  # frame duration
        
        bs.write_int32(0, 0, 0)  # tracks offset, asset name offset, time offset (unused in v4)
        
        vecs_offset_pos = bs.tell()
        bs.write_int32(64)  # vecs offset (always 64 for v4)
        bs.write_int32(0, 0)  # quats offset, frames offset (fill later)
        
        bs.stream.write(b'\x00' * 12)  # Padding to reach offset 64
        
        # Write vector palette
        for v in vec_palette:
            bs.write_float(v.x, v.y, v.z)
            
        quat_offset = bs.tell() - 12  # Subtract 12 as per v4 format spec
        
        # Write quaternion palette (x, y, z, w order)
        for q in quat_palette:
            bs.write_float(q.x, q.y, q.z, q.w)
            
        frame_offset = bs.tell() - 12
        
        # Write frame data: for each frame, for each joint
        for f_idx in range(frame_count):
            for h in sorted_hashes:
                frames = joint_data[h]
                if f_idx < len(frames):
                    t_id, s_id, r_id = frames[f_idx]
                else:
                    t_id, s_id, r_id = (0, 0, 0)
                    
                bs.write_uint32(h)
                bs.write_uint16(t_id, s_id, r_id, 0)  # t, s, r indices + padding
        
        # Patch file size
        total_size = bs.tell()
        bs.seek(12)
        bs.write_uint32(total_size)
        
        # Patch offsets
        bs.seek(vecs_offset_pos + 4)
        bs.write_int32(quat_offset, frame_offset)
    
    return True

def write_anm_from_data(filepath, anm_data, fps=None, disable_scaling=False):
    """Write pre-corrected ANMData directly to a v4 uncompressed ANM file.

    Translations in anm_data.tracks[*].poses[*].translation are expected to be
    in import-scale units (IMPORT_SCALE = 0.01 was applied at read time);
    this function scales them back to game units (x EXPORT_SCALE = 100).

    This is used by the visual-armature batch processing path, which applies
    bone-frame corrections directly to the raw pose data without touching the
    Blender FK chain, ensuring translations and scales are preserved correctly
    for is_custom_parent bones.
    """
    out_fps  = fps if fps is not None else anm_data.fps
    frame_count = anm_data.frame_count

    tracks_dict  = {t.joint_hash: t for t in anm_data.tracks}
    sorted_hashes = sorted(tracks_dict.keys())
    joint_count  = len(sorted_hashes)

    vec_palette, vec_map   = [], {}
    quat_palette, quat_map = [], {}

    def add_vec(v):
        key = (round(v.x, 6), round(v.y, 6), round(v.z, 6))
        if key not in vec_map:
            vec_map[key] = len(vec_palette)
            vec_palette.append(mathutils.Vector((v.x, v.y, v.z)))
        return vec_map[key]

    def add_quat(q):
        q = q.normalized()
        key = (round(q.x, 6), round(q.y, 6), round(q.z, 6), round(q.w, 6))
        if key not in quat_map:
            quat_map[key] = len(quat_palette)
            # Store as (w,x,y,z) so write order matches write_anm convention
            quat_palette.append(mathutils.Quaternion((q.w, q.x, q.y, q.z)))
        return quat_map[key]

    t_scale = 1.0 if disable_scaling else import_skl.EXPORT_SCALE

    zero_vec  = mathutils.Vector((0.0, 0.0, 0.0))
    one_vec   = mathutils.Vector((1.0, 1.0, 1.0))
    id_quat   = mathutils.Quaternion((1.0, 0.0, 0.0, 0.0))

    # Collect all frame entries
    frame_entries = []  # list of (hash, t_id, s_id, r_id)
    for f_idx in range(frame_count):
        for h in sorted_hashes:
            pose = tracks_dict[h].poses.get(f_idx)
            t = pose.translation if (pose and pose.translation is not None) else zero_vec
            s = pose.scale       if (pose and pose.scale       is not None) else one_vec
            r = pose.rotation    if (pose and pose.rotation    is not None) else id_quat

            t_id = add_vec(t * t_scale)
            s_id = add_vec(s)
            r_id = add_quat(r)
            frame_entries.append((h, t_id, s_id, r_id))

    with open(filepath, 'wb') as f:
        bs = BinaryStream(f)

        bs.write_ascii('r3d2anmd')
        bs.write_uint32(4)                       # version
        bs.write_uint32(0)                       # filesize placeholder (offset 12)
        bs.write_uint32(0xBE0794D3, 0, 0)        # format token, unknown, flags
        bs.write_uint32(joint_count, frame_count)
        bs.write_float(1.0 / out_fps)
        bs.write_int32(0, 0, 0)                  # tracks/asset/time offsets (v4 unused)

        vecs_offset_pos = bs.tell()
        bs.write_int32(64)                       # vecs always start at 64
        bs.write_int32(0, 0)                     # quats/frames offsets (filled later)
        bs.stream.write(b'\x00' * 12)            # padding to offset 64

        for v in vec_palette:
            bs.write_float(v.x, v.y, v.z)

        quat_offset = bs.tell() - 12

        for q in quat_palette:
            bs.write_float(q.x, q.y, q.z, q.w)  # write as x,y,z,w

        frame_offset = bs.tell() - 12

        for h, t_id, s_id, r_id in frame_entries:
            bs.write_uint32(h)
            bs.write_uint16(t_id, s_id, r_id, 0)

        total_size = bs.tell()
        bs.seek(12)
        bs.write_uint32(total_size)
        bs.seek(vecs_offset_pos + 4)
        bs.write_int32(quat_offset, frame_offset)

    return True


def save(operator, context, filepath, target_armature=None, disable_scaling=False, disable_transforms=False, flip=False, adapt_to_edits=False):
    armature_obj = target_armature

    if not armature_obj:
        armature_obj = context.active_object
        if not armature_obj or armature_obj.type != 'ARMATURE':
            armature_obj = next((o for o in context.scene.objects if o.type == 'ARMATURE'), None)

    if not armature_obj:
        operator.report({'ERROR'}, "No Armature found")
        return {'CANCELLED'}

    try:
        fps = context.scene.render.fps
        write_anm(filepath, armature_obj, fps, disable_scaling, disable_transforms, flip, adapt_to_edits=adapt_to_edits)
        operator.report({'INFO'}, f"Exported ANM: {filepath}")
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
