"""
Shared bake utilities for Wiggle-based auto-physics (boobs, hair).

Design:
  The physics simulation (Wiggle 2) produces correct frame-by-frame results.
  The ONLY post-bake work needed is:
    1) Set LINEAR interpolation  (prevent BEZIER micro-overshoots)
    2) Fix quaternion sign flips (prevent q/-q 180-degree pops)
    3) Snap loop seam            (last frame = first frame for loops)
    4) Clean frame 0             (LoL T-pose bind frame)

  All four are simple, independent passes over the F-curves.
  No velocity mirroring, no boundary blending, no SLERP — those were
  over-engineered and introduced more bugs than they fixed.
"""

import math

from .physics_common import get_action_fcurves


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _parse_bone_name(data_path):
    """Extract bone name from 'pose.bones["Name"].property'."""
    try:
        return data_path.split('pose.bones["')[1].split('"]')[0]
    except Exception:
        return ''


# ---------------------------------------------------------------------------
#  Loop detection
# ---------------------------------------------------------------------------

# Bones to CHECK for loop detection — only stable body bones.
# Physics-driven bones (cape, hair, tail) can differ at start/end
# even in a true loop, so they must be excluded.
# Buffbones (VFX helpers) are also excluded.
_LOOP_CHECK_KW = (
    'spine', 'pelvis',
    'l_hip', 'r_hip',
    'l_clavicle', 'r_clavicle',
    'l_shoulder', 'r_shoulder',
    'l_elbow', 'r_elbow',
    'l_hand', 'r_hand',
    'head', 'neck',
)


def _detect_animation_loops(action, frame_start, frame_end):
    """True if the animation loops (first frame ≈ last frame).

    Only checks stable body bones from _LOOP_CHECK_KW.
    Ignores buffbones. Handles quaternion q/-q aliases.
    If none of the check bones exist in the rig, returns False.
    """
    starts = {}
    ends = {}
    found = False

    for fc in (get_action_fcurves(action) or []):
        dp = fc.data_path
        if 'pose.bones["' not in dp:
            continue
        bn = _parse_bone_name(dp).lower()
        if 'buffbone' in bn:
            continue
        if not any(k in bn for k in _LOOP_CHECK_KW):
            continue

        idx = fc.array_index
        if idx >= 4:
            continue

        starts.setdefault(dp, [0.0]*4)[idx] = fc.evaluate(frame_start)
        ends.setdefault(dp, [0.0]*4)[idx] = fc.evaluate(frame_end)
        found = True

    if not found:
        return False

    worst = 0.0
    for dp in starts:
        s, e = starts[dp], ends[dp]
        if 'rotation_quaternion' in dp:
            ms = math.sqrt(sum(v*v for v in s))
            me = math.sqrt(sum(v*v for v in e))
            if ms > 1e-3 and me > 1e-3:
                dot = sum((s[i]/ms)*(e[i]/me) for i in range(4))
                d = 1.0 - abs(dot)
            else:
                d = max(abs(s[i]-e[i]) for i in range(4))
        else:
            d = max(abs(s[i]-e[i]) for i in range(min(len(s),3)))
        worst = max(worst, d)

    is_loop = worst < 0.05
    print(f"[LOOP DETECT] worst_diff={worst:.4f} checked={len(starts)} bones → {'LOOP' if is_loop else 'NOT LOOP'}")
    return is_loop


# ---------------------------------------------------------------------------
#  1) LINEAR interpolation
# ---------------------------------------------------------------------------

def _set_linear_interpolation(action, bone_names):
    """Set all physics bone keyframes to LINEAR interp, CONSTANT extrap."""
    bone_set = set(b for b in bone_names if b)
    for fc in (get_action_fcurves(action) or []):
        if 'pose.bones["' not in fc.data_path:
            continue
        if _parse_bone_name(fc.data_path) not in bone_set:
            continue
        fc.extrapolation = 'CONSTANT'
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.update()


# ---------------------------------------------------------------------------
#  2) Quaternion sign continuity
# ---------------------------------------------------------------------------

def _fix_quaternion_continuity(action, bone_names):
    """Fix q/-q sign flips between adjacent quaternion keyframes.

    MUST be called AFTER every other F-curve modification (collision
    correction, loop snap, spike removal, etc). Any later write to
    a quaternion keyframe can re-introduce a sign flip.

    q and -q are the same rotation. But when Blender interpolates
    (even LINEAR) between q and -q it goes the long way around,
    producing a violent 180-degree pop visible as a glitch.
    """
    bone_set = set(b for b in bone_names if b)

    # Group quat fcurves: {data_path: {0:fc, 1:fc, 2:fc, 3:fc}}
    groups = {}
    for fc in (get_action_fcurves(action) or []):
        dp = fc.data_path
        if 'rotation_quaternion' not in dp or 'pose.bones["' not in dp:
            continue
        if _parse_bone_name(dp) not in bone_set:
            continue
        groups.setdefault(dp, {})[fc.array_index] = fc

    for dp, chs in groups.items():
        if len(chs) < 4:
            continue

        # Map frame -> keypoint for each channel
        maps = {c: {int(kp.co[0]): kp for kp in chs[c].keyframe_points}
                for c in range(4)}

        # Frames present in all 4 channels
        frames = sorted(
            set(maps[0]) & set(maps[1]) & set(maps[2]) & set(maps[3])
        )
        if len(frames) < 2:
            continue

        # Read all values
        vals = {}
        for f in frames:
            v = [maps[c][f].co[1] for c in range(4)]
            # Normalize
            m = math.sqrt(sum(x*x for x in v))
            if m > 1e-8:
                v = [x/m for x in v]
            vals[f] = v

        # Walk forward, flip sign when dot < 0
        prev = vals[frames[0]]
        for i in range(1, len(frames)):
            f = frames[i]
            cur = vals[f]
            dot = sum(prev[c]*cur[c] for c in range(4))
            if dot < 0.0:
                cur = [-x for x in cur]
                vals[f] = cur
            prev = cur

        # Write back
        for f in frames:
            v = vals[f]
            for c in range(4):
                maps[c][f].co[1] = v[c]

        for fc in chs.values():
            fc.update()


# ---------------------------------------------------------------------------
#  3) Loop seam
# ---------------------------------------------------------------------------

def _copy_loop_end_to_start(action, frame_start, frame_end, bone_names):
    """Copy frame_end's baked values to frame_start for a seamless loop.

    frame_end has the physics after a full settled cycle — it's the most
    correct value. frame_start can have a slight artifact from the
    preroll→bake wrap-around. Copying end→start fixes the first frame
    while keeping the last frame's correct physics intact.
    """
    bone_set = set(b for b in bone_names if b)
    for fc in (get_action_fcurves(action) or []):
        if 'pose.bones["' not in fc.data_path:
            continue
        if _parse_bone_name(fc.data_path) not in bone_set:
            continue
        kf = {int(kp.co[0]): kp for kp in fc.keyframe_points}
        last = kf.get(frame_end)
        if last is None:
            continue
        first = kf.get(frame_start)
        if first is None:
            first = fc.keyframe_points.insert(
                frame_start, last.co[1], options={'FAST', 'REPLACE'})
        first.co[1] = last.co[1]
        fc.update()


def _snap_loop_seam(action, frame_start, frame_end, bone_names):
    """Copy frame_start keyframes to frame_end for a seamless loop.

    frame_end should NOT have been baked (the NLA bake stops at frame_end-1).
    This just inserts a keyframe at frame_end with the exact value from
    frame_start.  For quaternion channels the sign is chosen to be
    consistent with frame_end-1 so LINEAR interpolation doesn't cross zero.

    Non-loop animations never call this function — they are unaffected.
    """
    bone_set = set(b for b in bone_names if b)

    quat_groups = {}
    other_fcs = []

    for fc in (get_action_fcurves(action) or []):
        if 'pose.bones["' not in fc.data_path:
            continue
        if _parse_bone_name(fc.data_path) not in bone_set:
            continue
        if 'rotation_quaternion' in fc.data_path:
            quat_groups.setdefault(fc.data_path, {})[fc.array_index] = fc
        else:
            other_fcs.append(fc)

    # Non-quaternion: raw copy
    for fc in other_fcs:
        kf = {int(kp.co[0]): kp for kp in fc.keyframe_points}
        first = kf.get(frame_start)
        if first is None:
            continue
        last = kf.get(frame_end)
        if last is None:
            last = fc.keyframe_points.insert(
                frame_end, first.co[1], options={'FAST', 'REPLACE'})
        last.co[1] = first.co[1]
        fc.update()

    # Quaternion: copy with sign consistent to frame_end-1
    for dp, channels in quat_groups.items():
        if len(channels) < 4:
            for ch, fc in channels.items():
                kf = {int(kp.co[0]): kp for kp in fc.keyframe_points}
                first = kf.get(frame_start)
                if first is None:
                    continue
                last = kf.get(frame_end)
                if last is None:
                    last = fc.keyframe_points.insert(
                        frame_end, first.co[1], options={'FAST', 'REPLACE'})
                last.co[1] = first.co[1]
                fc.update()
            continue

        kf_maps = {c: {int(kp.co[0]): kp for kp in channels[c].keyframe_points}
                   for c in range(4)}

        first_vals = []
        for c in range(4):
            kp = kf_maps[c].get(frame_start)
            if kp is None:
                first_vals = None
                break
            first_vals.append(kp.co[1])
        if first_vals is None:
            continue

        # Sign consistent with frame_end-1
        prev_vals = []
        for c in range(4):
            kp = kf_maps[c].get(frame_end - 1)
            if kp is None:
                prev_vals.append(channels[c].evaluate(frame_end - 1))
            else:
                prev_vals.append(kp.co[1])

        dot = sum(first_vals[c] * prev_vals[c] for c in range(4))
        target = list(first_vals) if dot >= 0 else [-v for v in first_vals]

        for c in range(4):
            last = kf_maps[c].get(frame_end)
            if last is None:
                last = channels[c].keyframe_points.insert(
                    frame_end, target[c], options={'FAST', 'REPLACE'})
            last.co[1] = target[c]

        for fc in channels.values():
            fc.update()


def _force_loop_perfect_match(action, frame_start, frame_end, bone_names):
    """Final loop-seam enforcement — call as the VERY LAST step.

    Unlike _snap_loop_seam this does NO blending (the blend was already
    done in the bake function).  It just forces frame_end = frame_start
    with the correct quaternion sign, fixing any drift introduced by
    later passes like collision correction or quaternion continuity.
    """
    bone_set = set(b for b in bone_names if b)

    quat_groups = {}
    other_fcs = []
    for fc in (get_action_fcurves(action) or []):
        if 'pose.bones["' not in fc.data_path:
            continue
        if _parse_bone_name(fc.data_path) not in bone_set:
            continue
        if 'rotation_quaternion' in fc.data_path:
            quat_groups.setdefault(fc.data_path, {})[fc.array_index] = fc
        else:
            other_fcs.append(fc)

    # Non-quaternion: raw copy
    for fc in other_fcs:
        kf = {int(kp.co[0]): kp for kp in fc.keyframe_points}
        first = kf.get(frame_start)
        if first is None:
            continue
        last = kf.get(frame_end)
        if last is None:
            last = fc.keyframe_points.insert(
                frame_end, first.co[1], options={'FAST', 'REPLACE'})
        last.co[1] = first.co[1]
        fc.update()

    # Quaternion: copy with sign consistent to frame_end-1
    for dp, channels in quat_groups.items():
        if len(channels) < 4:
            for ch, fc in channels.items():
                kf = {int(kp.co[0]): kp for kp in fc.keyframe_points}
                first = kf.get(frame_start)
                if first is None:
                    continue
                last = kf.get(frame_end)
                if last is None:
                    last = fc.keyframe_points.insert(
                        frame_end, first.co[1], options={'FAST', 'REPLACE'})
                last.co[1] = first.co[1]
                fc.update()
            continue

        kf_maps = {c: {int(kp.co[0]): kp for kp in channels[c].keyframe_points}
                   for c in range(4)}

        first_vals = []
        for c in range(4):
            kp = kf_maps[c].get(frame_start)
            if kp is None:
                first_vals = None
                break
            first_vals.append(kp.co[1])
        if first_vals is None:
            continue

        # Sign consistent with frame_end-1
        prev_vals = []
        for c in range(4):
            kp = kf_maps[c].get(frame_end - 1)
            if kp is None:
                prev_vals.append(channels[c].evaluate(frame_end - 1))
            else:
                prev_vals.append(kp.co[1])

        dot = sum(first_vals[c] * prev_vals[c] for c in range(4))
        target = list(first_vals) if dot >= 0 else [-v for v in first_vals]

        for c in range(4):
            last = kf_maps[c].get(frame_end)
            if last is None:
                last = channels[c].keyframe_points.insert(
                    frame_end, target[c], options={'FAST', 'REPLACE'})
            last.co[1] = target[c]

        for fc in channels.values():
            fc.update()


_velocity_match_loop = _force_loop_perfect_match


# ---------------------------------------------------------------------------
#  4) T-pose frame 0 cleanup
# ---------------------------------------------------------------------------

def _clean_tpose_keyframes(action, bone_names):
    """Force frame 0 to identity pose (LoL bind/T-pose frame)."""
    bone_set = set(b for b in bone_names if b)
    for fc in (get_action_fcurves(action) or []):
        dp = fc.data_path
        if 'pose.bones["' not in dp:
            continue
        if _parse_bone_name(dp) not in bone_set:
            continue

        prop = dp.split('].')[-1] if '].' in dp else ''
        idx = fc.array_index

        if 'location' in prop:
            val = 0.0
        elif 'rotation_quaternion' in prop:
            val = 1.0 if idx == 0 else 0.0
        elif 'rotation_euler' in prop:
            val = 0.0
        elif 'scale' in prop:
            val = 1.0
        else:
            continue

        kp = fc.keyframe_points.insert(0, val, options={'FAST'})
        if kp is not None:
            kp.interpolation = 'CONSTANT'
        fc.update()


# ---------------------------------------------------------------------------
#  Non-loop helpers
# ---------------------------------------------------------------------------

def _restore_nonloop_start_to_tpose(action, frame_start, bone_names):
    """Non-loop: set frame_start to identity (physics starts from rest)."""
    bone_set = set(b for b in bone_names if b)
    for fc in (get_action_fcurves(action) or []):
        dp = fc.data_path
        if 'pose.bones["' not in dp:
            continue
        if _parse_bone_name(dp) not in bone_set:
            continue

        prop = dp.split('].')[-1] if '].' in dp else ''
        idx = fc.array_index

        if 'location' in prop:
            val = 0.0
        elif 'rotation_quaternion' in prop:
            val = 1.0 if idx == 0 else 0.0
        elif 'rotation_euler' in prop:
            val = 0.0
        elif 'scale' in prop:
            val = 1.0
        else:
            continue

        kp = fc.keyframe_points.insert(frame_start, val, options={'FAST'})
        if kp is not None:
            kp.interpolation = 'LINEAR'
        fc.update()


def _smooth_boundary_frames(action, frame_start, frame_end, bone_names,
                             smooth_range=3, smooth_ends='both'):
    """Blend boundary frames toward the anchor for smooth ease-in/out."""
    bone_set = set(b for b in bone_names if b)
    for fc in (get_action_fcurves(action) or []):
        if 'pose.bones["' not in fc.data_path:
            continue
        if _parse_bone_name(fc.data_path) not in bone_set:
            continue

        kf = {int(kp.co[0]): kp for kp in fc.keyframe_points}

        # Ease in from start
        anchor = kf.get(frame_start)
        if anchor:
            av = anchor.co[1]
            for i in range(1, smooth_range + 1):
                kp = kf.get(frame_start + i)
                if kp:
                    t = i / (smooth_range + 1)
                    kp.co[1] = av * (1.0 - t) + kp.co[1] * t

        # Ease out at end
        if smooth_ends == 'both':
            anchor = kf.get(frame_end)
            if anchor:
                av = anchor.co[1]
                for i in range(1, smooth_range + 1):
                    kp = kf.get(frame_end - i)
                    if kp:
                        t = i / (smooth_range + 1)
                        kp.co[1] = av * (1.0 - t) + kp.co[1] * t

        fc.update()


# ---------------------------------------------------------------------------
#  Legacy compat aliases
# ---------------------------------------------------------------------------

def _smooth_loop_closure_frames(action, frame_start, frame_end, bone_names,
                                smooth_range=2):
    """Legacy — kept for manual Wiggle bake compat."""
    _smooth_boundary_frames(action, frame_start, frame_end, bone_names,
                            smooth_range=smooth_range, smooth_ends='both')
