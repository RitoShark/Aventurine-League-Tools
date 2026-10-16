import bpy
import os
import mathutils
import math
from ..utils.binary_utils import Hash
from ..utils.format_data import write_animation
from . import import_skl
from . import bone_utils

def write_anm(filepath, armature_obj, fps=30.0, disable_scaling=False, disable_transforms=False, flip=False, adapt_to_edits=False, visual_mode=False, use_scene_range=False, clean_names=True, anim_scale=1.0, apply_object_transform=True):
    """Sample every joint in the same current-parent frames as the paired SKL."""
    from .skeleton import ExportPlan
    if not armature_obj.animation_data or not armature_obj.animation_data.action:
        raise ValueError('No animation data found on armature')
    if anim_scale <= 0 or fps <= 0:
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
    if max(len(vec_palette), len(quat_palette)) > 65535:
        raise ValueError('Animation exceeds the ANM palette limit; shorten the frame range')
    write_animation(filepath, fps, joint_data, vec_palette,
                    [mathutils.Quaternion((q[3], q[0], q[1], q[2])) for q in quat_palette])
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

    # Palette indices are stored as uint16, so the format caps each palette at 65535.
    if len(vec_palette) > 65535 or len(quat_palette) > 65535:
        raise Exception(
            f"Animation too long for the ANM format: {len(vec_palette)} translation/scale "
            f"values and {len(quat_palette)} rotations exceed the 65535 palette limit. "
            f"Shorten the animation ({frame_count} frames)."
        )

    joint_data = {h: [] for h in sorted_hashes}
    for h, t_id, s_id, r_id in frame_entries:
        joint_data[h].append((t_id, s_id, r_id))
    write_animation(filepath, out_fps, joint_data, vec_palette, quat_palette)

    return True


def save(operator, context, filepath, target_armature=None, disable_scaling=False, disable_transforms=False, flip=False, adapt_to_edits=False, use_scene_range=True, clean_names=True, anim_scale=1.0, apply_object_transform=True):
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
        write_anm(filepath, armature_obj, fps, disable_scaling, disable_transforms, flip, adapt_to_edits=adapt_to_edits, use_scene_range=use_scene_range, clean_names=clean_names, anim_scale=anim_scale, apply_object_transform=apply_object_transform)
        operator.report({'INFO'}, f"Exported ANM: {filepath}")
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
