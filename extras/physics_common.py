"""
physics_common.py — Shared utilities for Wiggle-based auto-physics.

Used by boobs_physics.py and hair_physics.py.
Contains: armature helpers, wiggle setup/teardown, collision data and correction,
and batch processing utilities.
"""

import bpy
import os
import math
import bmesh
from mathutils import Vector, Matrix, Quaternion as MQuat


# ---------------------------------------------------------------------------
#  Math helpers
# ---------------------------------------------------------------------------

def lerp(a, b, t):
    return a + (b - a) * t


def lerp_exp(a, b, t):
    """Exponential (log-space) interpolation. Both a and b must be positive.
    Produces perceptually uniform steps when values span multiple orders of
    magnitude (e.g. damping 12 → 0.08).
    """
    return math.exp(math.log(a) + (math.log(b) - math.log(a)) * t)


# ---------------------------------------------------------------------------
#  Armature / context utilities
# ---------------------------------------------------------------------------

def find_armature(context):
    """Return the best armature in the scene.
    Prefers LoL-tagged armatures (lol_skl_filepath / lol_skn_filepath),
    then falls back to the active object, then to any armature in the scene.
    """
    if context.active_object and context.active_object.type == 'ARMATURE':
        arm = context.active_object
        if arm.get("lol_skl_filepath") or arm.get("lol_skn_filepath"):
            return arm

    for obj in context.scene.objects:
        if obj.type == 'ARMATURE':
            if obj.get("lol_skl_filepath") or obj.get("lol_skn_filepath"):
                return obj

    for obj in context.scene.objects:
        if obj.type == 'ARMATURE':
            return obj

    return None


def get_animations_folder(armature_obj):
    """Return the animations/ subfolder derived from the armature's SKL/SKN path."""
    if not armature_obj:
        return None
    skl_path = armature_obj.get("lol_skl_filepath") or armature_obj.get("lol_skn_filepath")
    if not skl_path:
        return None
    folder = os.path.join(os.path.dirname(skl_path), "animations")
    return folder if os.path.isdir(folder) else None


def ensure_object_mode(context):
    """Safely switch to object mode without raising on poll failure."""
    try:
        if context.active_object and context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except RuntimeError:
        pass


def select_armature(context, armature_obj):
    """Deselect all, select and activate armature_obj (switches to object mode first)."""
    ensure_object_mode(context)
    bpy.ops.object.select_all(action='DESELECT')
    armature_obj.select_set(True)
    context.view_layer.objects.active = armature_obj


# ---------------------------------------------------------------------------
#  Wiggle 2 setup / teardown
# ---------------------------------------------------------------------------

def ensure_physics_registered():
    """Register the Wiggle 2 engine if it hasn't been already."""
    if not hasattr(bpy.types.Scene, 'wiggle_enable'):
        try:
            from . import physics
            physics.register()
        except Exception as e:
            raise RuntimeError(
                f"Cannot enable jiggle physics — Wiggle 2 failed to load: {e}\n"
                "Make sure 'League Physics' is enabled in addon preferences."
            )


def configure_wiggle_bones(context, armature_obj, bone_names, params):
    """Apply a pre-computed Wiggle 2 params dict to the listed bones.

    params keys: stiff, damp, gravity, mass, stretch, chain.

    Switches to pose mode internally so Wiggle 2 update callbacks can access
    selected_pose_bones. Returns the list of bone names actually configured.
    """
    ensure_physics_registered()
    select_armature(context, armature_obj)
    bpy.ops.object.mode_set(mode='POSE')

    context.scene.wiggle_enable = True
    armature_obj.wiggle_enable = True
    armature_obj.wiggle_mute = False
    armature_obj.wiggle_freeze = False

    # Deselect all bones once (avoids expensive operator call per bone).
    for bone in armature_obj.data.bones:
        bone.select = False

    configured = []
    prev_bone = None
    for bname in bone_names:
        if not bname:
            continue
        pb = armature_obj.pose.bones.get(bname)
        if not pb:
            continue
        # Only one bone selected at a time so update callbacks iterate safely.
        if prev_bone:
            prev_bone.select = False
        pb.bone.select = True
        armature_obj.data.bones.active = pb.bone

        pb.wiggle_tail    = True
        pb.wiggle_head    = False
        pb.wiggle_mute    = False
        pb.wiggle_enable  = True
        pb.wiggle_stiff   = params['stiff']
        pb.wiggle_damp    = params['damp']
        pb.wiggle_gravity = params['gravity']
        pb.wiggle_mass    = params['mass']
        pb.wiggle_stretch = params['stretch']
        pb.wiggle_chain   = params['chain']
        configured.append(bname)
        prev_bone = pb.bone

    from . import physics
    try:
        physics.build_list()
    except Exception:
        pass

    return configured


def clear_wiggle_from_bones(context, armature_obj, bone_names):
    """Disable Wiggle 2 on the given bones and freeze the armature.

    Freezing prevents the Wiggle 2 frame-change handler from overriding
    baked keyframes during timeline scrubbing or loop playback.
    """
    ensure_physics_registered()
    select_armature(context, armature_obj)
    bpy.ops.object.mode_set(mode='POSE')

    # Deselect all bones once (avoids expensive operator call per bone).
    for bone in armature_obj.data.bones:
        bone.select = False

    prev_bone = None
    for bname in bone_names:
        if not bname:
            continue
        pb = armature_obj.pose.bones.get(bname)
        if not pb:
            continue
        if prev_bone:
            prev_bone.select = False
        pb.bone.select = True
        armature_obj.data.bones.active = pb.bone
        pb.wiggle_tail   = False
        pb.wiggle_head   = False
        pb.wiggle_enable = False
        prev_bone = pb.bone

    from . import physics
    try:
        physics.build_list()
    except Exception:
        pass

    # Disable on the armature so the handler no longer fires on frame changes.
    armature_obj.wiggle_enable = False


def strip_physics_keyframes(action, bone_names):
    """Remove all F-curves for the given bones from action.

    Called before re-baking so a new preview at a different intensity starts
    from a clean slate rather than stacking on the previous bake.
    """
    bone_set = set(b for b in bone_names if b)
    to_remove = [
        fc for fc in action.fcurves
        if 'pose.bones["' in fc.data_path
        and _parse_bone_name(fc.data_path) in bone_set
    ]
    for fc in to_remove:
        action.fcurves.remove(fc)


def _parse_bone_name(data_path):
    """Extract bone name from a pose bone data path. Returns '' on failure."""
    try:
        return data_path.split('pose.bones["')[1].split('"]')[0]
    except Exception:
        return ''


# ---------------------------------------------------------------------------
#  Collision — bone candidate lists and radius helpers
# ---------------------------------------------------------------------------

# Per-bone-type capsule radius factors.  Radius = world_len × factor × scale.
# Rationale: a bone is much thinner than it is long. For a 25 cm upper arm
# the actual arm radius is ~4 cm → factor ~0.16. Slightly larger values give
# a buffer for fast motion. global_scale=1.0 is the calibrated default.
_BONE_TYPE_FACTORS = {
    'upperarm': 0.22,
    'lowerarm': 0.20,
    'elbow':    0.35,
    'clavicle': 0.20,
    'shoulder': 0.22,
    'hand':     0.18,
    'spine':    0.28,
    'pelvis':   0.32,
    'neck':     0.30,
    'head':     0.55,
    'default':  0.20,
}

# Boobs: lateral arm bones that physically press against the breast.
# NOTE: spine/chest are intentionally NOT listed. The breast bones are
# children of the chest — adding the chest as a collider makes the post-bake
# correction push the breast AWAY from its own parent, producing a float/
# detach glitch. Torso self-clipping is handled by the simulation stability
# fixes (iterations, damping, spike smoothing), not by adding colliders.
COLLISION_BONE_CANDIDATES = [
    'L_Clavicle',        'R_Clavicle',
    'L_Shoulder',        'R_Shoulder',
    'Bip001 L Clavicle', 'Bip001 R Clavicle',
    'Bip001_L_Clavicle', 'Bip001_R_Clavicle',
    'L_UpperArm',        'R_UpperArm',
    'Bip001 L UpperArm', 'Bip001 R UpperArm',
    'Bip001_L_UpperArm', 'Bip001_R_UpperArm',
    'L_LowerArm',        'R_LowerArm',
    'L_Forearm',         'R_Forearm',
    'L_Arm',             'R_Arm',
    'Bip001 L Forearm',  'Bip001 R Forearm',
    'Bip001_L_Forearm',  'Bip001_R_Forearm',
    'L_Elbow',           'R_Elbow',
    'L_Hand',            'R_Hand',
    'Bip001 L Hand',     'Bip001 R Hand',
    'Bip001_L_Hand',     'Bip001_R_Hand',
]

# Hair: full upper-body — spine, neck, head, shoulders, arms.
HAIR_COLLISION_BONE_CANDIDATES = [
    'Spine',             'Spine1',            'Spine2',            'Spine3',
    'Bip001 Spine',      'Bip001 Spine1',     'Bip001 Spine2',     'Bip001 Spine3',
    'Bip001_Spine',      'Bip001_Spine1',     'Bip001_Spine2',     'Bip001_Spine3',
    'Neck',              'Neck1',
    'Bip001 Neck',       'Bip001_Neck',
    'Head',
    'Bip001 Head',       'Bip001_Head',
    'L_Clavicle',        'R_Clavicle',
    'L_Shoulder',        'R_Shoulder',
    'Bip001 L Clavicle', 'Bip001 R Clavicle',
    'Bip001_L_Clavicle', 'Bip001_R_Clavicle',
    'L_UpperArm',        'R_UpperArm',
    'Bip001 L UpperArm', 'Bip001 R UpperArm',
    'Bip001_L_UpperArm', 'Bip001_R_UpperArm',
    'L_Elbow',           'R_Elbow',
    'L_LowerArm',        'R_LowerArm',
    'L_Forearm',         'R_Forearm',
    'Bip001 L Forearm',  'Bip001 R Forearm',
    'Bip001_L_Forearm',  'Bip001_R_Forearm',
]


def _bone_capsule_radius(bone_name, world_len, global_scale):
    """Capsule radius for a bone: world_len × per-type factor × global_scale.
    Minimum 0.01 m so short helper bones still produce usable capsules.
    """
    bl = bone_name.lower()
    if any(k in bl for k in ('upperarm', 'upper_arm', 'upper arm')):
        f = _BONE_TYPE_FACTORS['upperarm']
    elif any(k in bl for k in ('lowerarm', 'lower_arm', 'forearm')):
        f = _BONE_TYPE_FACTORS['lowerarm']
    elif 'elbow' in bl:
        f = _BONE_TYPE_FACTORS['elbow']
    elif any(k in bl for k in ('clavicle', 'clav')):
        f = _BONE_TYPE_FACTORS['clavicle']
    elif 'shoulder' in bl:
        f = _BONE_TYPE_FACTORS['shoulder']
    elif 'hand' in bl:
        f = _BONE_TYPE_FACTORS['hand']
    elif any(k in bl for k in ('spine', 'torso', 'chest')):
        f = _BONE_TYPE_FACTORS['spine']
    elif any(k in bl for k in ('pelvis', 'hip')):
        f = _BONE_TYPE_FACTORS['pelvis']
    elif 'neck' in bl:
        f = _BONE_TYPE_FACTORS['neck']
    elif 'head' in bl:
        f = _BONE_TYPE_FACTORS['head']
    else:
        f = _BONE_TYPE_FACTORS['default']
    return max(0.01, world_len * f * global_scale)


def find_default_collision_bones(armature_obj):
    """Return arm/elbow/hand bones that exist in armature.

    First checks COLLISION_BONE_CANDIDATES by exact name, then does a
    keyword scan for any bones with 'arm', 'elbow', 'forearm', 'hand',
    'clavicle', or 'shoulder' in the name to catch rigs with non-standard
    naming (e.g. cf_j_arm00_L).  Buffbones are always excluded.
    """
    if not armature_obj:
        return []
    existing = {b.name for b in armature_obj.pose.bones}

    # Exact-name matches first.
    found = [n for n in COLLISION_BONE_CANDIDATES
             if n in existing and 'buffbone' not in n.lower()]
    found_set = set(found)

    # Keyword fallback: catch arm bones with non-standard names.
    _KW = ('upperarm', 'upper_arm', 'lowerarm', 'lower_arm', 'forearm',
           'clavicle', 'shoulder')
    for bname in sorted(existing):
        if bname in found_set or 'buffbone' in bname.lower():
            continue
        bl = bname.lower()
        if any(kw in bl for kw in _KW):
            found.append(bname)

    return found


def find_default_hair_collision_bones(armature_obj):
    """Return spine/head/shoulder/arm bones from HAIR_COLLISION_BONE_CANDIDATES that exist."""
    if not armature_obj:
        return []
    existing = {b.name for b in armature_obj.pose.bones}
    return [n for n in HAIR_COLLISION_BONE_CANDIDATES
            if n in existing and 'buffbone' not in n.lower()]


def precompute_collision_radii(armature_obj, coll_bone_names):
    """Pre-compute {bone_name: radius} once for batch processing.

    Radii are static (bone_length × per-type factor). Compute them once
    before the animation loop and pass as precomputed_radii to
    post_bake_collision_correct() to skip re-computing every animation.
    """
    arm_mw = armature_obj.matrix_world
    radii = {}
    for n in coll_bone_names:
        cpb = armature_obj.pose.bones.get(n)
        if cpb:
            wl = (arm_mw @ cpb.tail - arm_mw @ cpb.head).length
            radii[n] = _bone_capsule_radius(n, wl, 1.0)
    return radii


def compute_mesh_radii(armature_obj, bone_names, weight_threshold=0.05):
    """Compute capsule radii from actual skinned mesh vertex weights.

    For each bone: collect world-space distances from vertices with non-trivial
    weights, use the 85th-percentile as radius. Falls back to
    _bone_capsule_radius() when no skinned mesh is found.
    Handles hidden meshes and disabled armature modifiers correctly.
    """
    arm_mw = armature_obj.matrix_world

    # Build bone segments in world space (computed once, reused per mesh).
    segments = {}
    for bn in bone_names:
        pb = armature_obj.pose.bones.get(bn)
        if not pb:
            continue
        seg_a = arm_mw @ pb.head
        seg_b = arm_mw @ pb.tail
        ab    = seg_b - seg_a
        ab_sq = ab.dot(ab)
        segments[bn] = (seg_a, ab, ab_sq, ab.length)

    bone_dists = {bn: [] for bn in bone_names if bn in segments}

    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        if not any(m.type == 'ARMATURE' and m.object == armature_obj
                   for m in obj.modifiers):
            continue

        mesh   = obj.data
        obj_mw = obj.matrix_world

        for bn, dists in bone_dists.items():
            vg = obj.vertex_groups.get(bn)
            if not vg:
                continue
            vg_idx = vg.index
            seg_a, ab, ab_sq, _ = segments[bn]

            for v in mesh.vertices:
                # Fast weight lookup: walk v.groups (typically 1-4 entries).
                w = 0.0
                for ge in v.groups:
                    if ge.group == vg_idx:
                        w = ge.weight
                        break
                if w < weight_threshold:
                    continue
                v_w = obj_mw @ v.co
                if ab_sq < 1e-12:
                    dist = (v_w - seg_a).length
                else:
                    t       = max(0.0, min(1.0, (v_w - seg_a).dot(ab) / ab_sq))
                    closest = seg_a + t * ab
                    dist    = (v_w - closest).length
                dists.append(dist)

    radii = {}
    for bn in bone_names:
        dists = bone_dists.get(bn, [])
        if len(dists) >= 5:
            dists.sort()
            # 85th percentile: outer surface, ignoring stray outlier vertices.
            radii[bn] = max(0.005, dists[int(len(dists) * 0.85)])
        elif dists:
            radii[bn] = max(0.005, max(dists) * 0.9)
        elif bn in segments:
            radii[bn] = _bone_capsule_radius(bn, segments[bn][3], 1.0)
        else:
            radii[bn] = 0.04
    return radii


# ---------------------------------------------------------------------------
#  Post-bake collision correction
# ---------------------------------------------------------------------------

def post_bake_collision_correct(context, armature_obj, bone_names, coll_bone_names,
                                 sphere_factor=1.0, max_rot_deg=None,
                                 precomputed_radii=None,
                                 self_coll_enabled=False, self_coll_scale=1.0):
    """Rotate jiggle bones away from collision capsules on every baked frame.

    Checks each jiggle bone's tail against the capsule on each collision bone
    and applies a rotation delta to push the tail clear. Writes the corrected
    quaternion directly into the action's F-curve keypoints.

    precomputed_radii: {name: radius} computed once for batch processing via
      precompute_collision_radii(). Skips per-call radius computation.

    max_rot_deg: per-frame cap on the correction angle. Use ~20 for hair
      chains (prevents violent jumps). None = no cap (breast bones).

    Midplane clamp: when there are 2+ jiggle bones the arm can only push each
      bone to the midpoint between them, preventing one from being swept into
      the other.

    Returns the number of frames on which at least one correction was applied.
    """
    scene  = context.scene
    action = armature_obj.animation_data.action
    if not action:
        return 0

    frame_start = int(action.frame_range[0])
    frame_end   = int(action.frame_range[1])
    armature_obj.wiggle_freeze = True

    arm_mw    = armature_obj.matrix_world
    arm_q     = arm_mw.to_quaternion()
    arm_q_inv = arm_q.inverted()

    # --- Resolve capsule radii (precomputed or computed now) ---
    coll_radii = {}
    for n in coll_bone_names:
        cpb = armature_obj.pose.bones.get(n)
        if not cpb:
            continue
        if precomputed_radii and n in precomputed_radii:
            coll_radii[n] = max(0.005, precomputed_radii[n] * sphere_factor)
        else:
            wl = (arm_mw @ cpb.tail - arm_mw @ cpb.head).length
            coll_radii[n] = _bone_capsule_radius(n, wl, sphere_factor)

    jiggle_pbs   = [pb for pb in (armature_obj.pose.bones.get(n) for n in bone_names if n) if pb]
    jiggle_pb_map = {pb.name: pb for pb in jiggle_pbs}
    use_midplane = len(jiggle_pbs) >= 2

    if not coll_radii and not self_coll_enabled:
        return 0

    # Pre-resolve collision pose bone references (constant across frames).
    coll_pose_bones = {}
    for n in coll_radii:
        cpb = armature_obj.pose.bones.get(n)
        if cpb:
            coll_pose_bones[n] = cpb

    # --- Index quaternion F-curves for the jiggle bones ---
    bone_set = set(b for b in bone_names if b)
    fc_map   = {}  # {bname: {array_index: fcurve}}
    for fc in action.fcurves:
        if 'pose.bones["' not in fc.data_path or 'rotation_quaternion' not in fc.data_path:
            continue
        bname = _parse_bone_name(fc.data_path)
        if bname not in bone_set:
            continue
        fc_map.setdefault(bname, {})[fc.array_index] = fc

    # Pre-build {frame: keypoint} maps for O(1) lookup per frame.
    # Without this, the inner keyframe search is O(N_frames) per frame,
    # giving O(N²) total — visible on long animations (200+ frames).
    kf_index = {
        bname: {
            ch: {int(kp.co[0]): kp for kp in fc.keyframe_points}
            for ch, fc in channels.items()
        }
        for bname, channels in fc_map.items()
    }

    changed_fcs    = set()
    frames_corrected = 0
    max_rad = math.radians(max_rot_deg) if max_rot_deg is not None else None

    # Natural breast separation used for self-collision threshold.
    min_breast_sep = 0.0
    if self_coll_enabled and use_midplane and len(jiggle_pbs) == 2:
        scene.frame_set(frame_start)
        nat_dist = (arm_mw @ jiggle_pbs[1].tail - arm_mw @ jiggle_pbs[0].tail).length
        if nat_dist > 0.005:
            min_breast_sep = nat_dist * 0.4 * self_coll_scale

    for frame in range(frame_start, frame_end + 1):
        scene.frame_set(frame)

        # Midplane recomputed each frame so it tracks the animated pose.
        mid_plane_pt = None
        mid_plane_n  = None
        if use_midplane:
            heads        = [arm_mw @ pb.head for pb in jiggle_pbs]
            mid_plane_pt = sum(heads, Vector()) / len(heads)
            diff = heads[-1] - heads[0]
            if diff.length > 1e-6:
                mid_plane_n = diff.normalized()

        frame_corrected = False

        # --- Body collision ---
        for bname in bone_names:
            if not bname or bname not in fc_map:
                continue
            pb = jiggle_pb_map.get(bname)
            if not pb:
                continue

            tail_w = arm_mw @ pb.tail
            head_w = arm_mw @ pb.head
            push_w = Vector((0., 0., 0.))

            for n, cap_r in coll_radii.items():
                cpb = coll_pose_bones.get(n)
                if not cpb:
                    continue
                seg_a = arm_mw @ cpb.head
                seg_b = arm_mw @ cpb.tail
                ab    = seg_b - seg_a
                ab_sq = ab.dot(ab)
                if ab_sq < 1e-12:
                    closest = seg_a
                else:
                    t       = max(0.0, min(1.0, (tail_w - seg_a).dot(ab) / ab_sq))
                    closest = seg_a + t * ab
                diff = tail_w - closest
                dist = diff.length
                if dist < cap_r:
                    pd      = diff.normalized() if dist > 1e-6 else Vector((0., 0., 1.))
                    push_w += pd * (cap_r - dist)

            if push_w.length < 1e-9:
                continue

            # Midplane clamp: arm cannot push a bone past the group midpoint.
            if mid_plane_n is not None:
                side_before = (tail_w - mid_plane_pt).dot(mid_plane_n)
                overshoot   = (tail_w + push_w - mid_plane_pt).dot(mid_plane_n)
                if side_before * overshoot < 0:
                    push_w -= mid_plane_n * overshoot

            new_tail_w = tail_w + push_w
            old_d = tail_w     - head_w
            new_d = new_tail_w - head_w
            if old_d.length < 1e-6 or new_d.length < 1e-6:
                continue
            old_n_v = old_d.normalized()
            new_n_v = new_d.normalized()
            if old_n_v.dot(new_n_v) > 0.99999:
                continue

            delta_arm = arm_q_inv @ old_n_v.rotation_difference(new_n_v) @ arm_q

            # Per-frame angle cap (prevents violent jumps in hair chains).
            if max_rad is not None:
                angle = 2.0 * math.acos(min(1.0, abs(delta_arm.w)))
                if angle > max_rad and angle > 1e-6:
                    # q and -q are the same rotation; when w < 0 the axis
                    # must be negated to get the short-arc direction.
                    sign = 1.0 if delta_arm.w >= 0.0 else -1.0
                    ax = Vector((delta_arm.x * sign,
                                 delta_arm.y * sign,
                                 delta_arm.z * sign))
                    if ax.length > 1e-6:
                        delta_arm = MQuat(ax.normalized(), max_rad)

            cur_mat   = pb.matrix.copy()
            new_q_arm = (delta_arm @ cur_mat.to_quaternion()).normalized()
            pb.matrix = Matrix.LocRotScale(cur_mat.to_translation(), new_q_arm, cur_mat.to_scale())
            new_L     = pb.rotation_quaternion.copy()

            bone_kf = kf_index.get(bname, {})
            for ch, val in enumerate((new_L.w, new_L.x, new_L.y, new_L.z)):
                fc = fc_map[bname].get(ch)
                if fc:
                    kp = bone_kf.get(ch, {}).get(frame)
                    if kp:
                        kp.co[1] = val
                        changed_fcs.add(fc)
            frame_corrected = True

        # --- Self-collision: push breast/jiggle bones apart ---
        if min_breast_sep > 1e-6:
            pb0   = jiggle_pbs[0]
            pb1   = jiggle_pbs[1]
            t0_w  = arm_mw @ pb0.tail
            t1_w  = arm_mw @ pb1.tail
            sep_v = t1_w - t0_w
            sep_d = sep_v.length
            if sep_d < min_breast_sep:
                push_dir = sep_v.normalized() if sep_d > 1e-6 else (
                    mid_plane_n if mid_plane_n else Vector((1., 0., 0.))
                )
                push_amt = (min_breast_sep - sep_d) * 0.5
                for pb, direction in ((pb0, -push_dir * push_amt),
                                      (pb1,  push_dir * push_amt)):
                    bname = pb.name
                    if bname not in fc_map:
                        continue
                    tw    = arm_mw @ pb.tail
                    hw    = arm_mw @ pb.head
                    new_tw = tw + direction
                    old_d  = tw - hw
                    new_d  = new_tw - hw
                    if old_d.length < 1e-6 or new_d.length < 1e-6:
                        continue
                    old_n_v = old_d.normalized()
                    new_n_v = new_d.normalized()
                    if old_n_v.dot(new_n_v) > 0.99999:
                        continue
                    delta_arm = arm_q_inv @ old_n_v.rotation_difference(new_n_v) @ arm_q
                    cur_mat   = pb.matrix.copy()
                    new_q_arm = (delta_arm @ cur_mat.to_quaternion()).normalized()
                    pb.matrix = Matrix.LocRotScale(cur_mat.to_translation(), new_q_arm, cur_mat.to_scale())
                    new_L = pb.rotation_quaternion.copy()

                    bone_kf = kf_index.get(bname, {})
                    for ch, val in enumerate((new_L.w, new_L.x, new_L.y, new_L.z)):
                        fc = fc_map[bname].get(ch)
                        if fc:
                            kp = bone_kf.get(ch, {}).get(frame)
                            if kp:
                                kp.co[1] = val
                                changed_fcs.add(fc)
                    frame_corrected = True

        if frame_corrected:
            frames_corrected += 1

    for fc in changed_fcs:
        fc.update()

    return frames_corrected


# ---------------------------------------------------------------------------
#  Batch processing helpers
# ---------------------------------------------------------------------------

def hide_meshes_for_batch(context):
    """Hide viewport meshes and disable armature modifiers for batch speed.

    Stops Blender from recalculating vertex deformations on every frame
    during preroll and baking, which is the main per-frame overhead.
    Returns (disabled_mods, hidden_objects) for restore_meshes_after_batch().
    """
    disabled_mods = []
    hidden_objs   = []
    for obj in context.scene.objects:
        if obj.type != 'MESH':
            continue
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.show_viewport:
                mod.show_viewport = False
                disabled_mods.append(mod)
        if not obj.hide_viewport:
            obj.hide_viewport = True
            hidden_objs.append(obj)
    return disabled_mods, hidden_objs


def restore_meshes_after_batch(disabled_mods, hidden_objs):
    """Undo the hiding done by hide_meshes_for_batch()."""
    for mod in disabled_mods:
        mod.show_viewport = True
    for obj in hidden_objs:
        obj.hide_viewport = False


# ---------------------------------------------------------------------------
#  Post-bake spike removal
# ---------------------------------------------------------------------------

def smooth_physics_spikes(action, bone_names, max_deg=20.0):
    """Remove isolated physics spikes by replacing outlier frames.

    A spike is a single frame where the rotation jumps far from the
    midpoint of its neighbors (prev + next). This catches glitches from
    spring overshoot without affecting legitimate fast movements.

    Unlike sequential clamping, this does NOT cascade — each frame is
    compared to its ORIGINAL neighbors, not to previously modified values.

    Returns the number of frames that were smoothed.
    """
    bone_set = set(b for b in bone_names if b)
    max_rad  = math.radians(max_deg)
    fixed    = 0

    # Group quaternion F-curves by bone.
    quat_fcs = {}
    for fc in action.fcurves:
        dp = fc.data_path
        if 'pose.bones["' not in dp or 'rotation_quaternion' not in dp:
            continue
        bname = _parse_bone_name(dp)
        if bname not in bone_set:
            continue
        quat_fcs.setdefault(bname, {})[fc.array_index] = fc

    for bname, channels in quat_fcs.items():
        if len(channels) < 4:
            continue

        kf_maps = {ch: {int(kp.co[0]): kp for kp in fc.keyframe_points}
                   for ch, fc in channels.items()}
        frames = sorted(kf_maps[0].keys())
        if len(frames) < 3:
            continue

        # Read ALL quaternion values first (before any modification).
        quats = {}
        for f in frames:
            if any(f not in kf_maps[c] for c in range(4)):
                continue
            q = MQuat((kf_maps[0][f].co[1], kf_maps[1][f].co[1],
                       kf_maps[2][f].co[1], kf_maps[3][f].co[1]))
            if q.magnitude > 1e-8:
                q.normalize()
            quats[f] = q

        # Detect spikes: interior frames far from the midpoint of neighbors.
        for i in range(1, len(frames) - 1):
            f      = frames[i]
            f_prev = frames[i - 1]
            f_next = frames[i + 1]
            if f not in quats or f_prev not in quats or f_next not in quats:
                continue

            prev_q = quats[f_prev]
            cur_q  = quats[f]
            next_q = quats[f_next]

            # Midpoint of neighbors (short-arc SLERP).
            nq = next_q.copy()
            if prev_q.dot(nq) < 0.0:
                nq.negate()
            mid = prev_q.slerp(nq, 0.5)
            if mid.magnitude > 1e-8:
                mid.normalize()

            # How far is this frame from the neighbor midpoint?
            dot = min(1.0, abs(cur_q.dot(mid)))
            spike_angle = 2.0 * math.acos(dot)

            if spike_angle > max_rad:
                # Replace with neighbor midpoint.
                for c, val in enumerate((mid.w, mid.x, mid.y, mid.z)):
                    kf_maps[c][f].co[1] = val
                quats[f] = mid
                fixed += 1

        for fc in channels.values():
            fc.update()

    return fixed


def clamp_local_rotation_from_identity(action, bone_names, max_deg=45.0):
    """Cap how far each baked rotation_quaternion can be from identity.

    This is the ultimate safety net for physics blowups. If the simulation
    ever produced a rotation further than ``max_deg`` from the rest pose
    (identity for a freshly-imported bone), we slerp it back to identity
    by the fraction needed to bring it inside the cap.

    Used by breast bones: under fast character motion the spring can
    occasionally overshoot violently, writing a 60°+ rotation keyframe that
    visibly pops through the body. Clamping at 45° kills the pop without
    affecting the natural jiggle (normal breast jiggle is < 20°).

    Returns the number of keyframes that were clamped.
    """
    bone_set = set(b for b in bone_names if b)
    max_rad  = math.radians(max_deg)
    clamped  = 0

    # Group quaternion F-curves by bone.
    groups = {}
    for fc in action.fcurves:
        dp = fc.data_path
        if 'pose.bones["' not in dp or 'rotation_quaternion' not in dp:
            continue
        bname = _parse_bone_name(dp)
        if bname not in bone_set:
            continue
        groups.setdefault(bname, {})[fc.array_index] = fc

    for bname, channels in groups.items():
        if len(channels) < 4:
            continue

        kf_maps = {c: {int(kp.co[0]): kp for kp in channels[c].keyframe_points}
                   for c in range(4)}
        frames = sorted(
            set(kf_maps[0]) & set(kf_maps[1]) & set(kf_maps[2]) & set(kf_maps[3])
        )

        for f in frames:
            w = kf_maps[0][f].co[1]
            x = kf_maps[1][f].co[1]
            y = kf_maps[2][f].co[1]
            z = kf_maps[3][f].co[1]

            q = MQuat((w, x, y, z))
            m = q.magnitude
            if m < 1e-8:
                continue
            q = q * (1.0 / m)

            # Angle from identity quaternion (1,0,0,0): 2 * acos(|w|)
            dot = min(1.0, abs(q.w))
            angle = 2.0 * math.acos(dot)

            if angle <= max_rad:
                continue

            # Slerp from identity toward q by the fraction (max_rad / angle).
            # Identity quaternion is (1,0,0,0) — slerping just scales xyz down
            # and rebuilds w to keep unit length.
            # Sign-match q.w to +1 so the short arc is from identity.
            if q.w < 0.0:
                q = MQuat((-q.w, -q.x, -q.y, -q.z))

            identity = MQuat((1.0, 0.0, 0.0, 0.0))
            clamped_q = identity.slerp(q, max_rad / angle)

            kf_maps[0][f].co[1] = clamped_q.w
            kf_maps[1][f].co[1] = clamped_q.x
            kf_maps[2][f].co[1] = clamped_q.y
            kf_maps[3][f].co[1] = clamped_q.z
            clamped += 1

        for fc in channels.values():
            fc.update()

    return clamped


# ---------------------------------------------------------------------------
#  Real-time collision mesh helpers (shared by boobs & hair)
# ---------------------------------------------------------------------------

_COLL_TEMP_PREFIX = "__phys_coll"


def create_temp_collision_meshes(context, armature_obj, coll_bone_names,
                                  sphere_factor=1.0,
                                  coll_name=None,
                                  radius_overrides=None):
    """Create temporary icosphere colliders bone-parented to body bones.

    The spheres are wired into Wiggle 2's built-in ``closest_point_on_mesh``
    collision so the simulation itself prevents clipping — not a post-bake
    approximation.

    radius_overrides: optional {bone_type: factor} for specific bones
        (e.g. ``{'head': 0.90, 'neck': 0.50}``).  Falls back to
        ``_bone_capsule_radius()`` for unlisted bones.

    Returns ``(collection, [objects])`` for later cleanup.
    """
    if coll_name is None:
        coll_name = f"{_COLL_TEMP_PREFIX}_temp"

    # Clean up leftovers from a previous crashed run.
    old_coll = bpy.data.collections.get(coll_name)
    if old_coll:
        for obj in list(old_coll.objects):
            mesh_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh_data and mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
        bpy.data.collections.remove(old_coll)
    tmpl_name = f"{coll_name}_sphere"
    old_tmpl = bpy.data.meshes.get(tmpl_name)
    if old_tmpl and old_tmpl.users == 0:
        bpy.data.meshes.remove(old_tmpl)

    arm_mw  = armature_obj.matrix_world
    created = []

    collection = bpy.data.collections.new(coll_name)
    context.scene.collection.children.link(collection)

    # Shared unit icosphere — each object uses obj.scale for its radius.
    template_mesh = bpy.data.meshes.new(tmpl_name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    bm.to_mesh(template_mesh)
    bm.free()

    for bone_name in coll_bone_names:
        pb = armature_obj.pose.bones.get(bone_name)
        if not pb:
            continue
        bone_len = (arm_mw @ pb.tail - arm_mw @ pb.head).length
        if bone_len < 1e-6:
            continue

        # Resolve radius: per-type override or generic factor.
        if radius_overrides:
            bl = bone_name.lower()
            override = None
            for key, fac in radius_overrides.items():
                if key in bl:
                    override = fac
                    break
            if override:
                radius = max(0.01, bone_len * override * sphere_factor)
            else:
                radius = _bone_capsule_radius(bone_name, bone_len, sphere_factor)
        else:
            radius = _bone_capsule_radius(bone_name, bone_len, sphere_factor)

        # Evenly-spaced spheres along the bone (capsule approximation).
        num = max(2, min(4, int(bone_len / max(radius, 0.01)) + 1))
        placements = [(i / max(1, num - 1), radius) for i in range(num)]

        for i, (t, r) in enumerate(placements):
            obj_name = f"{coll_name}_{bone_name}_{i}"
            obj = bpy.data.objects.new(obj_name, template_mesh)
            obj.location = (0.0, 0.0, 0.0)
            obj.scale = (r, r, r)
            obj.display_type = 'BOUNDS'
            obj.hide_render = True
            collection.objects.link(obj)

            # Use Copy Location + Copy Rotation constraints, NOT
            # bone-parenting. Bone-parenting inherits the bone's scale
            # — any frame where the animated bone has a zero-scale axis
            # makes the sphere's world matrix singular, and Wiggle 2's
            # cmw.inverted() call in collide() raises ValueError:
            # "matrix does not have an inverse". The frame handler then
            # aborts every subsequent frame and physics freezes to the
            # end of the animation. The constraints copy position and
            # rotation only; the sphere keeps its own clean (r,r,r)
            # scale, so the world matrix is always invertible.
            cl = obj.constraints.new(type='COPY_LOCATION')
            cl.target      = armature_obj
            cl.subtarget   = bone_name
            cl.head_tail   = min(1.0, max(0.0, t))
            cl.use_offset  = False

            cr = obj.constraints.new(type='COPY_ROTATION')
            cr.target      = armature_obj
            cr.subtarget   = bone_name
            cr.use_offset  = False

            created.append(obj)

    if not created:
        bpy.data.collections.remove(collection)
        if template_mesh.users == 0:
            bpy.data.meshes.remove(template_mesh)
        return None, []

    return collection, created


def setup_wiggle_collision_props(armature_obj, bone_names, coll_collection,
                                  radius_factor=0.12, friction=0.3, bounce=0.0,
                                  sticky=0.005):
    """Wire Wiggle 2 collision on each physics bone.

    After this, the simulation's ``move() → collide()`` pushes bones away
    from the icosphere meshes in *coll_collection* in real time.
    """
    arm_mw = armature_obj.matrix_world
    for bname in bone_names:
        if not bname:
            continue
        pb = armature_obj.pose.bones.get(bname)
        if not pb:
            continue
        bone_len = (arm_mw @ pb.tail - arm_mw @ pb.head).length
        pb.wiggle_collider_type       = 'Collection'
        pb.wiggle_collider_collection = coll_collection
        pb.wiggle_radius   = max(0.008, bone_len * radius_factor)
        pb.wiggle_friction = friction
        pb.wiggle_bounce   = bounce
        pb.wiggle_sticky   = sticky


def clear_wiggle_collision_props(armature_obj, bone_names):
    """Reset Wiggle 2 collision properties on bones to defaults."""
    for bone in armature_obj.data.bones:
        bone.select = False
    for bname in bone_names:
        if not bname:
            continue
        pb = armature_obj.pose.bones.get(bname)
        if not pb:
            continue
        pb.wiggle_collider_type       = 'Object'
        pb.wiggle_collider_collection = None
        pb.wiggle_collider            = None
        pb.wiggle_radius              = 0
        pb.wiggle_friction            = 0.5
        pb.wiggle_bounce              = 0.5
        pb.wiggle_sticky              = 0


def cleanup_temp_collision_meshes(collection, objects):
    """Remove temporary collision icospheres and their collection."""
    for obj in objects:
        mesh_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh_data and mesh_data.users == 0:
            bpy.data.meshes.remove(mesh_data)
    if collection:
        bpy.data.collections.remove(collection)
