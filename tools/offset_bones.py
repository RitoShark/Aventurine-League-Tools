"""Explicit offset parents and duplicate conversion. Export never reparents bones."""
import bpy
import mathutils
from ..io import bone_utils
from ..io.skeleton import ExportPlan


def add_offset_parent(arm, target_name):
    """Insert a neutral control whose exported axes match the original parent.

    Moving this control in Pose Mode offsets the entire subtree. Its frame-zero
    pose is baked with the mesh and skeleton, so stock child animation locals
    still apply. Ordinary new joints do not receive this special export frame.
    """
    bpy.context.view_layer.objects.active = arm
    if arm.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    plan = ExportPlan(arm)
    target = arm.pose.bones[target_name]
    if target.parent and target.parent.get('lol_offset_parent_for') == target_name:
        raise ValueError(f'{target_name} already has an offset parent')
    rest = target.bone.matrix_local.copy()
    parent_rest = target.parent.bone.matrix_local.copy() if target.parent else mathutils.Matrix.Identity(4)
    parent_k = plan.corrections[target.parent.name] if target.parent else mathutils.Matrix.Identity(4)
    correction = rest.inverted() @ parent_rest @ parent_k
    bpy.ops.object.mode_set(mode='EDIT')
    bones = arm.data.edit_bones
    child = bones[target_name]
    control = bones.new(target_name + '_Offset')
    control.length = max(child.length * .6, .1)
    control.matrix = rest
    control.parent = child.parent
    child.use_connect = False
    child.parent = control
    name = control.name
    bpy.ops.object.mode_set(mode='POSE')
    pb = arm.pose.bones[name]
    pb['lol_offset_parent_for'] = target_name
    correction = pb.bone.matrix_local.inverted() @ parent_rest @ parent_k
    pb['lol_offset_correction'] = [v for row in correction for v in row]
    pb.rotation_mode = 'QUATERNION'
    for bone in arm.data.bones:
        bone.select = bone.name == name
    arm.data.bones.active = arm.data.bones[name]
    bpy.context.view_layer.update()
    for bone in arm.data.bones:
        bone.select = bone.name == name
    arm.data.bones.active = arm.data.bones[name]
    return arm.pose.bones[name]


def add_offset_companion(arm, control_name, joint_name):
    """Carry an independently animated root along with a translation offset.

    Two pose-space Copy Location constraints evaluate the displacement between
    the moved offset frame and its unmoved reference. The companion gets only
    that displacement, retaining the root's own animation and orientation.
    Exported ANM tracks bake the evaluated result; unchanged stock clips cannot
    drive this extra animated root control.
    """
    control = arm.pose.bones[control_name]
    joint = arm.pose.bones[joint_name]
    if joint.parent:
        raise ValueError(f'{joint_name}: an offset companion must be a separate root joint')
    if not control.get('lol_offset_parent_for') or control.get('lol_offset_companion_of'):
        raise ValueError('Select an ordinary offset parent as the displacement source')
    plan = ExportPlan(arm)
    parent_frame = (control.parent.bone.matrix_local @ plan.corrections[control.parent.name]
                    if control.parent else mathutils.Matrix.Identity(4))
    neutral_frame = parent_frame @ plan.corrections[control_name].inverted()
    reference_name = control.get('lol_offset_reference')
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    bones = arm.data.edit_bones
    if not reference_name:
        reference = bones.new(control_name + '_Reference')
        reference.length = bones[control_name].length
        reference.matrix = neutral_frame
        reference.parent = bones[control_name].parent
        reference.use_deform = False
        reference_name = reference.name
    companion = bones.new(joint_name + '_Offset')
    companion.head, companion.tail = (0, 0, 0), (0, .1, 0)
    companion.use_deform = False
    bones[joint_name].use_connect = False
    bones[joint_name].parent = companion
    companion_name = companion.name
    bpy.ops.object.mode_set(mode='POSE')
    arm.pose.bones[control_name]['lol_offset_reference'] = reference_name
    arm.pose.bones[reference_name]['lol_offset_reference_for'] = control_name
    pb = arm.pose.bones[companion_name]
    pb['lol_offset_parent_for'] = joint_name
    pb['lol_offset_companion_of'] = control_name
    pb['lol_offset_correction'] = [v for row in mathutils.Matrix.Identity(4) for v in row]
    pb.rotation_mode = 'QUATERNION'
    for target, negate in ((control_name, False), (reference_name, True)):
        constraint = pb.constraints.new('COPY_LOCATION')
        constraint.name = 'Offset displacement' if not negate else 'Subtract original frame'
        constraint.target = arm
        constraint.subtarget = target
        constraint.owner_space = constraint.target_space = 'POSE'
        constraint.use_offset = negate
        constraint.invert_x = constraint.invert_y = constraint.invert_z = negate
    arm['lol_requires_animation_export'] = True
    bpy.context.view_layer.update()
    return pb


def find_snap_roots(arm, target_name):
    # Only recognize original Riot joints with an explicit matching _Snap name.
    # Arbitrary added bones are not inferred/reparented from naming conventions.
    plan = ExportPlan(arm)
    target = arm.pose.bones[target_name]
    subtree = {target.name} | {pb.name for pb in target.children_recursive}
    candidates = [pb.name for pb in plan.owners.values()
                  if pb.parent is None and pb.name.endswith('_Snap')
                  and pb.name[:-5] in subtree]
    return candidates


def apply_offset_bind(arm):
    """Bake static control offsets into mesh rest vertices and armature rest.

    Rebinding both is essential at vertices with weights across the offset boundary.
    Baking only the exported mesh would disagree with Blender's original inverse binds.
    """
    scene = bpy.context.scene
    old_frame = scene.frame_current
    bpy.context.view_layer.objects.active = arm
    if arm.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    meshes = [obj for obj in scene.objects if obj.type == 'MESH' and obj.find_armature() == arm]
    controls = [pb for pb in arm.pose.bones if pb.get('lol_offset_parent_for')]
    if not controls:
        raise ValueError('No explicit offset parents in this armature')
    # This operation is for a static bind edit, not baking an animated control.
    action = arm.animation_data.action if arm.animation_data else None
    if action and hasattr(action, 'fcurves'):
        paths = [pb.path_from_id() for pb in controls]
        if any(any(fc.data_path.startswith(path + '.') for path in paths) for fc in action.fcurves):
            raise ValueError('Offset controls have animation keys; use a static control for a bind edit')
    for obj in meshes:
        if obj.data.users > 1:
            raise ValueError(f'{obj.name}: make the mesh single-user before changing its bind pose')
        if obj.data.shape_keys:
            raise ValueError(f'{obj.name}: apply or remove shape keys before changing the bind pose')
        if any(mod.show_viewport and mod.type != 'ARMATURE' for mod in obj.modifiers):
            raise ValueError(f'{obj.name}: apply other modifiers before changing the bind pose')
    try:
        scene.frame_set(0)
        bpy.context.view_layer.update()
        # Frame zero is the bind pose. Reject unrelated posed native joints.
        for pb in arm.pose.bones:
            if pb not in controls and max(abs(pb.matrix_basis[r][c] - (1 if r == c else 0))
                                         for r in range(4) for c in range(4)) > 1e-4:
                raise ValueError(f'{pb.name} is posed at frame 0. Restore its bind pose first')
        for pb in controls:
            if pb.get('lol_offset_reference'):
                _, rotation, scale = pb.matrix_basis.decompose()
                if abs(rotation.w) < 1 - 1e-6 or max(abs(v - 1) for v in scale) > 1e-5:
                    raise ValueError(f'{pb.name}: linked root companions support translation offsets only')
        dg = bpy.context.evaluated_depsgraph_get()
        baked = {}
        for obj in meshes:
            evaluated = obj.evaluated_get(dg)
            mesh = evaluated.to_mesh()
            try:
                if len(mesh.vertices) != len(obj.data.vertices):
                    raise ValueError(f'{obj.name}: bind bake changed mesh topology')
                from ..io.export_skn import _corner_normals
                baked[obj.name] = ([v.co.copy() for v in mesh.vertices], _corner_normals(mesh))
            finally:
                evaluated.to_mesh_clear()
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.armature_apply(selected=False)
        bpy.ops.object.mode_set(mode='OBJECT')
        for obj in meshes:
            positions, normals = baked[obj.name]
            for vertex, position in zip(obj.data.vertices, positions):
                vertex.co = position
            obj.data.update()
            obj.data.normals_split_custom_set(normals)
        for pb in controls:
            pb['lol_offset_bind_applied'] = True
    finally:
        scene.frame_set(old_frame)
        bpy.context.view_layer.update()


class LOL_OT_LinkOffsetCompanion(bpy.types.Operator):
    """Select a separate root and make its offset parent active to link their displacement"""
    bl_idname = 'lol_offset.link_companion'
    bl_label = 'Link Selected Root to Offset'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'POSE' and context.active_pose_bone is not None
                and bool(context.active_pose_bone.get('lol_offset_parent_for')))

    def execute(self, context):
        arm = context.active_object
        control = context.active_pose_bone.name
        names = [pb.name for pb in context.selected_pose_bones if pb.name != control]
        if not names or any(arm.pose.bones[name].parent for name in names):
            self.report({'ERROR'}, 'Select separate root joints and make the offset parent active')
            return {'CANCELLED'}
        try:
            for name in names:
                add_offset_companion(arm, control, name)
            self.report({'INFO'}, 'Roots linked. Apply Offset to Bind Pose and export updated ANMs.')
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class LOL_OT_ApplyOffsetBind(bpy.types.Operator):
    """Bake static offsets into the mesh and skeleton bind pose together"""
    bl_idname = 'lol_offset.apply_bind'
    bl_label = 'Apply Offset to Bind Pose'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        try:
            apply_offset_bind(context.active_object)
            self.report({'INFO'}, 'Mesh and skeleton bind pose updated; ready to export')
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class LOL_OT_SetOffsetFromPose(bpy.types.Operator):
    """Insert an offset control above the active bone; move it in Pose Mode"""
    bl_idname = 'lol_offset.set_from_pose'
    bl_label = 'Add Offset Parent'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        arm = context.active_object
        active = arm.data.edit_bones.active if arm.mode == 'EDIT' else arm.data.bones.active
        if active is None:
            self.report({'ERROR'}, 'Select the bone to offset')
            return {'CANCELLED'}
        try:
            pb = add_offset_parent(arm, active.name)
            roots = find_snap_roots(arm, pb.get('lol_offset_parent_for'))
            if roots:
                self.report({'WARNING'}, 'Separate roots stay outside this offset: ' + ', '.join(roots)
                            + '. Check their skin weights before using unchanged animations.')
            else:
                self.report({'INFO'}, f'Move {pb.name}, then Apply Offset to Bind Pose.')
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


# Native-skeleton data the importer stamps onto every bone.  A Shift+D
# duplicate inherits a copy of these; stripping them turns the duplicate back
# into a plain new bone that exports at its authored placement.
_STALE_NATIVE_PROPS = (
    "native_bone_index", "native_bind_t", "native_bind_r", "native_bind_s",
    "native_global_rest_mat", "native_parent", "native_matrix_local", "native_source_name",
)


class LOL_OT_ConvertToNewBone(bpy.types.Operator):
    """Clear copied source metadata from selected duplicate joints.
Duplicates already export as new joints; this removes their unused source tags."""
    bl_idname = "lol_bone.convert_to_new"
    bl_label = "Convert to New Bone(s)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        arm = context.active_object

        # Selected bone names, however the user is selecting (Pose or Edit Mode).
        names = [b.name for b in (context.selected_pose_bones or [])]
        if not names:
            names = [b.name for b in (getattr(context, "selected_editable_bones", None) or [])]
        if not names:
            names = [b.name for b in (getattr(context, "selected_bones", None) or [])]
        if not names:
            self.report({'ERROR'}, "Select the duplicated bone(s) to convert (Pose or Edit Mode)")
            return {'CANCELLED'}

        # Genuine stale duplicates (safe to convert); the canonical owner of each
        # native index is deliberately excluded so a real joint is never gutted.
        convertible = {n for n, _idx, _canon in
                       bone_utils.find_duplicate_native_index_bones(arm)}

        converted, protected, already = [], [], []
        for name in sorted(set(names)):
            pb = arm.pose.bones.get(name)
            if pb is None:
                continue
            if pb.get("native_bone_index") is None:
                already.append(name)          # already a plain new bone
                continue
            if name not in convertible:
                protected.append(name)         # real skeleton joint — leave it alone
                continue
            for key in _STALE_NATIVE_PROPS:
                if key in pb:
                    del pb[key]
            converted.append(name)

        if not converted:
            if protected:
                self.report({'WARNING'},
                            f"Nothing converted — {', '.join(protected)} "
                            f"{'is a' if len(protected) == 1 else 'are'} real skeleton "
                            f"joint(s) (the original), not a duplicate. Select the "
                            f"duplicated bone(s) instead.")
            elif already:
                self.report({'INFO'}, f"Already a new bone: {', '.join(already)}. Nothing to do.")
            else:
                self.report({'WARNING'}, "No convertible bones in the selection.")
            return {'CANCELLED'}

        msg = f"Converted {len(converted)} bone(s) to new joints: {', '.join(converted)}"
        if protected:
            msg += f"  (skipped real joint(s): {', '.join(protected)})"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(LOL_OT_SetOffsetFromPose)
    bpy.utils.register_class(LOL_OT_ApplyOffsetBind)
    bpy.utils.register_class(LOL_OT_LinkOffsetCompanion)
    bpy.utils.register_class(LOL_OT_ConvertToNewBone)


def unregister():
    bpy.utils.unregister_class(LOL_OT_ConvertToNewBone)
    bpy.utils.unregister_class(LOL_OT_LinkOffsetCompanion)
    bpy.utils.unregister_class(LOL_OT_ApplyOffsetBind)
    bpy.utils.unregister_class(LOL_OT_SetOffsetFromPose)
