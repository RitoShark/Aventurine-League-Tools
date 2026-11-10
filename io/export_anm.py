import bpy
import mathutils
import math
from ..utils.binary_utils import Hash
from ..utils.backend import require_ritoshark
from ..utils.format_data import write_animation
from . import import_skl


def _animation_skeleton(plan, joint_data, vectors, rotations):
    """Give motion validation the exact hierarchy and names used by this export.

    Every joint has an animation track, so bind poses and skinning are not used
    by the validator. First-frame transforms provide a consistent reference.
    This skeleton stays in memory and never changes or writes the user's SKL.
    """
    rs = require_ritoshark()
    joints = []
    for pb in plan.bones:
        t, s, r = joint_data[Hash.elf(plan.names[pb.name])][0]
        q = rotations[r]
        joints.append(rs.Joint(
            plan.names[pb.name], plan.ids[pb.name],
            plan.ids[pb.parent.name] if pb.parent else -1,
            0.0, tuple(vectors[t]), tuple(vectors[s]), (q.x, q.y, q.z, q.w),
            (0, 0, 0), (1, 1, 1), (0, 0, 0, 1)))
    return rs.Skl.new(joints, [])


def export_message(result, filepath):
    if result.fallbacks:
        return ({'WARNING'}, f'Exported ANM as {result.format.upper()} instead of '
                f'{result.requested_format.upper()} to preserve motion: {filepath}. '
                'Fallback details are in the console.')
    return {'INFO'}, f'Exported ANM ({result.format.upper()}): {filepath}'


def write_anm(filepath, armature_obj, fps=30.0, disable_scaling=False, disable_transforms=False, flip=False, adapt_to_edits=False, visual_mode=False, use_scene_range=False, clean_names=True, anim_scale=1.0, apply_object_transform=True, export_format='v5'):
    """Sample every joint in the same current-parent frames as the paired SKL."""
    from .skeleton import ExportPlan
    if not armature_obj.animation_data or not armature_obj.animation_data.action:
        raise ValueError('No animation data found on armature')
    if not math.isfinite(anim_scale) or not math.isfinite(fps) or anim_scale <= 0 or fps <= 0:
        raise ValueError('Animation scale and FPS must be positive')
    plan = ExportPlan(armature_obj, clean_names)
    plan.validate_offset_bind()
    action = armature_obj.animation_data.action
    start = max(1, int(action.frame_range[0]))
    end = max(start, int(action.frame_range[1]))
    if use_scene_range:
        start = max(start, bpy.context.scene.frame_start)
        end = min(end, bpy.context.scene.frame_end)
        if end < start:
            raise ValueError('Scene frame range does not overlap the animation')
    vec_palette, quat_palette = [], []
    vec_map, quat_map = {}, {}
    def add(value, palette, lookup):
        value = tuple(float(x) for x in value)
        if value not in lookup:
            lookup[value] = len(palette)
            palette.append(value)
        return lookup[value]
    joint_data = {Hash.elf(plan.names[pb.name]): [] for pb in plan.bones}
    scale = (1.0 if disable_scaling else import_skl.EXPORT_SCALE) * anim_scale
    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    previous = {}
    try:
        for frame in range(start, end+1):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            locals = plan.locals(plan.globals(pose=True, disable_transforms=disable_transforms,
                                             apply_object_transform=apply_object_transform))
            for pb in plan.bones:
                t, r, s = locals[pb.name].decompose()
                t *= scale
                if flip:
                    t.x = -t.x
                    r.y, r.z = -r.y, -r.z
                if pb.name in previous and r.dot(previous[pb.name]) < 0:
                    r.negate()
                previous[pb.name] = r.copy()
                joint_data[Hash.elf(plan.names[pb.name])].append((
                    add(t, vec_palette, vec_map), add(s, vec_palette, vec_map),
                    add((r.x, r.y, r.z, r.w), quat_palette, quat_map)))
    finally:
        scene.frame_set(old_frame, subframe=old_subframe)
    rotations = [mathutils.Quaternion((q[3], q[0], q[1], q[2])) for q in quat_palette]
    skeleton = _animation_skeleton(plan, joint_data, vec_palette, rotations)
    return write_animation(filepath, fps, joint_data, vec_palette, rotations,
                           export_format=export_format, skeleton=skeleton)


def write_anm_from_data(filepath, anm_data, fps=None, disable_scaling=False, export_format='v5', skeleton=None):
    """Write ANMData using the selected format and validated fallback policy.

    Translations in anm_data.tracks[*].poses[*].translation are expected to be
    in import-scale units (IMPORT_SCALE = 0.01 was applied at read time);
    this function scales them back to game units (x EXPORT_SCALE = 100).

    An optional Rust skeleton enables hierarchy-aware motion validation.
    """
    out_fps  = fps if fps is not None else anm_data.fps
    if not math.isfinite(out_fps) or out_fps <= 0:
        raise ValueError('Animation FPS must be positive')
    frame_count = anm_data.frame_count

    tracks_dict  = {t.joint_hash: t for t in anm_data.tracks}
    sorted_hashes = sorted(tracks_dict.keys())

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

    joint_data = {h: [] for h in sorted_hashes}
    for h, t_id, s_id, r_id in frame_entries:
        joint_data[h].append((t_id, s_id, r_id))
    return write_animation(filepath, out_fps, joint_data, vec_palette, quat_palette,
                           export_format=export_format, skeleton=skeleton)


def save(operator, context, filepath, target_armature=None, disable_scaling=False, disable_transforms=False, flip=False, adapt_to_edits=False, use_scene_range=True, clean_names=True, anim_scale=1.0, apply_object_transform=True, export_format='v5'):
    armature_obj = target_armature

    if not armature_obj:
        armature_obj = context.active_object
        if not armature_obj or armature_obj.type != 'ARMATURE':
            armature_obj = next((o for o in context.scene.objects if o.type == 'ARMATURE'), None)

    if not armature_obj:
        operator.report({'ERROR'}, "No Armature found")
        return {'CANCELLED'}

    try:
        fps = context.scene.render.fps / context.scene.render.fps_base
        result = write_anm(filepath, armature_obj, fps, disable_scaling, disable_transforms, flip, adapt_to_edits=adapt_to_edits, use_scene_range=use_scene_range, clean_names=clean_names, anim_scale=anim_scale, apply_object_transform=apply_object_transform, export_format=export_format)
        operator.report(*export_message(result, filepath))
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
