"""Offset-bone helpers: bake a clone's Pose Mode offset into its rest pose.

The offset-clone workflow (Shift+D a native bone, offset it, export) reads the
offset from the clone's REST pose. Pose Mode gives live visual feedback while
adjusting, but pose loc/rot never reaches the export — and Blender's own
"Apply (Selected) Pose as Rest" drags children's rests along once the clone is
spliced, silently cancelling the offset. This operator bakes the pose into the
clone's rest by writing the edit-bone data directly (children untouched),
splices the clone above its original if needed, and leaves the SCALE pose
channel alone — that channel is the clone's scale dial, read at export time.
"""
import bpy
import mathutils

from ..io import bone_utils


class LOL_OT_SetOffsetFromPose(bpy.types.Operator):
    """Bake the selected offset clone's Pose Mode position/rotation into its rest pose.
Children are untouched, and the clone's Scale stays in the pose channel (the scale dial)"""
    bl_idname = "lol_offset.set_from_pose"
    bl_label = "Set Offset from Pose"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        arm = context.active_object
        clones = bone_utils.detect_offset_clones(arm)
        if not clones:
            self.report({'ERROR'}, "No offset clone found. Shift+D a skeleton bone first "
                                   "(the duplicate keeps a name like 'Bone.001')")
            return {'CANCELLED'}

        # Resolve which clone to bake: active bone, unique selected, or the only one.
        clone_name = None
        active = context.active_pose_bone
        if active is not None and active.name in clones:
            clone_name = active.name
        elif active is not None and active.name in clones.values():
            matches = [c for c, t in clones.items() if t == active.name]
            if len(matches) == 1:
                clone_name = matches[0]
        if clone_name is None:
            selected = [pb.name for pb in (context.selected_pose_bones or []) if pb.name in clones]
            if len(selected) == 1:
                clone_name = selected[0]
        if clone_name is None and len(clones) == 1:
            clone_name = next(iter(clones))
        if clone_name is None:
            self.report({'ERROR'}, f"Select the clone to bake. Found: {', '.join(sorted(clones))}")
            return {'CANCELLED'}

        target_name = clones[clone_name]
        clone_pb = arm.pose.bones[clone_name]
        target_pb = arm.pose.bones[target_name]

        # The offset is the POSE DISPLACEMENT applied to the ORIGINAL bone's
        # placement — NOT the clone's total position. Users park the clone
        # aside in Edit Mode just to be able to select it; that parking move
        # must not leak into the export (it isn't part of the preview either:
        # while posing, the children follow only the pose displacement).
        posed_t, posed_r, _ps = clone_pb.matrix.decompose()
        posed_rigid = mathutils.Matrix.LocRotScale(posed_t, posed_r, None)
        try:
            delta_world = posed_rigid @ clone_pb.bone.matrix_local.inverted()
        except ValueError:
            delta_world = mathutils.Matrix.Identity(4)

        dw_t = delta_world.to_translation().length
        dw_r = delta_world.to_quaternion().angle
        dw_r = min(abs(dw_r), abs(6.2831853 - dw_r))
        has_pose_offset = dw_t > 1e-5 or dw_r > 1e-4

        if has_pose_offset:
            # New rest = original bone's rest displaced by the pose delta.
            t, r, _s2 = (delta_world @ target_pb.bone.matrix_local).decompose()
        else:
            # No pose displacement: keep whatever offset was authored in Edit
            # Mode untouched (only splice below if needed).
            t, r = None, None

        bpy.ops.object.mode_set(mode='EDIT')
        eb = arm.data.edit_bones
        e_clone = eb.get(clone_name)
        e_target = eb.get(target_name)
        spliced_now = False
        if e_clone is not None and e_target is not None and e_target.parent != e_clone:
            e_clone.parent = e_target.parent
            e_target.parent = e_clone
            e_target.use_connect = False
            spliced_now = True
        if e_clone is not None and t is not None:
            # Direct edit-bone write: bakes ONLY the clone's rest. Blender's
            # armature_apply would preserve children's parent-relative rests,
            # dragging the whole subtree along and cancelling the offset.
            e_clone.matrix = mathutils.Matrix.LocRotScale(t, r, None)
        bpy.ops.object.mode_set(mode='POSE')

        clone_pb = arm.pose.bones[clone_name]
        clone_pb.location = (0.0, 0.0, 0.0)
        if clone_pb.rotation_mode == 'QUATERNION':
            clone_pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        elif clone_pb.rotation_mode == 'AXIS_ANGLE':
            clone_pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        else:
            clone_pb.rotation_euler = (0.0, 0.0, 0.0)

        if t is not None:
            msg = f"Offset baked into rest pose of '{clone_name}' (pose displacement vs '{target_name}')"
        else:
            msg = f"No pose offset on '{clone_name}' — existing Edit Mode offset kept"
        if spliced_now:
            msg += f" (spliced above '{target_name}')"
        sc = clone_pb.scale
        if abs(sc.x - 1.0) + abs(sc.y - 1.0) + abs(sc.z - 1.0) > 1e-5:
            msg += f"; scale dial kept in pose: ({sc.x:.2f}, {sc.y:.2f}, {sc.z:.2f})"
        msg += ". Bones snap back visually — the offset lives in playback."
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(LOL_OT_SetOffsetFromPose)


def unregister():
    bpy.utils.unregister_class(LOL_OT_SetOffsetFromPose)
