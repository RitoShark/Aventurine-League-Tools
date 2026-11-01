"""Commit a fitted rest pose without letting already-bound meshes snap back."""
import bpy
from ..io import skin_normals


def is_league_armature(arm):
    return bool(arm and arm.type == 'ARMATURE' and
                ('lol_source_joints' in arm or any('native_bone_index' in p for p in arm.pose.bones)))


def bound_meshes(context, arm):
    # Include hidden/excluded objects and meshes in other scenes using this same
    # armature object: its new rest affects all of them. Visibility is not binding.
    return [obj for obj in bpy.data.objects if obj.users_scene and obj.type == 'MESH'
            and any(m.type == 'ARMATURE' and m.object == arm for m in obj.modifiers)]


def _validate(arm, meshes):
    if arm.data.users > 1 or arm.library or arm.data.library:
        raise ValueError('Make the armature local and single-user before changing its rest pose')
    if arm.data.pose_position != 'POSE':
        raise ValueError('Switch the armature to Pose Position before applying its pose')
    for owner in (arm, arm.data):
        anim = owner.animation_data
        if anim and (anim.action or anim.drivers or any(not t.mute for t in anim.nla_tracks)):
            raise ValueError('Unlink the active action/NLA and drivers before fitting the rest pose; import animations again afterward')
    if any(not c.mute and c.influence for pb in arm.pose.bones for c in pb.constraints):
        raise ValueError('Bake or disable bone constraints before applying a fitted rest pose')
    for obj in meshes:
        if obj.library or obj.data.library or obj.data.shape_keys:
            raise ValueError(f'{obj.name}: use a local mesh without shape keys for the rest-pose bake')
        if obj.parent_type == 'BONE':
            raise ValueError(f'{obj.name}: bone parenting plus skinning is not supported by this rest-pose bake')
        modifiers = list(obj.modifiers)
        target = [m for m in modifiers if m.type == 'ARMATURE' and m.object == arm]
        if len(target) != 1 or not target[0].show_viewport or target[0].use_multi_modifier:
            raise ValueError(f'{obj.name}: enable one ordinary Armature modifier for this skeleton')
        if any(m.show_viewport for m in modifiers[:modifiers.index(target[0])]):
            raise ValueError(f'{obj.name}: apply modifiers before the Armature modifier first')
        if any(m.type == 'ARMATURE' and m != target[0] and m.show_viewport for m in modifiers):
            raise ValueError(f'{obj.name}: multiple active armatures cannot be rebound together')


def _bake_mesh(context, obj, arm):
    """Evaluate only this skinning modifier on a visible, disposable copy.

    Other modifiers are retained, not baked twice. A separate output mesh keeps
    attributes, weights, UVs and viewport shading, even for shared source data.
    """
    from ..io.export_skn import _corner_normals
    probe = obj.copy()
    probe_data = obj.data.copy()
    probe.data = probe_data
    output = None
    try:
        context.scene.collection.objects.link(probe)
        probe.hide_viewport = False
        probe.hide_set(False)
        probe.matrix_world = obj.matrix_world.copy()
        for modifier in list(probe.modifiers):
            if modifier.type != 'ARMATURE' or modifier.object != arm:
                probe.modifiers.remove(modifier)
        file_normals = skin_normals.prepare_export_mesh(
            probe_data, imported='lol_skn_flags' in obj)
        context.view_layer.update()
        evaluated = probe.evaluated_get(context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            if len(mesh.vertices) != len(obj.data.vertices) or len(mesh.loops) != len(obj.data.loops):
                raise ValueError(f'{obj.name}: armature evaluation changed topology')
            positions = [v.co.copy() for v in mesh.vertices]
            normals = _corner_normals(mesh) if file_normals or obj.data.has_custom_normals else None
        finally:
            evaluated.to_mesh_clear()
        output = obj.data.copy()
        for vertex, co in zip(output.vertices, positions):
            vertex.co = co
        output.update()
        if file_normals and not obj.data.has_custom_normals:
            skin_normals.store(output, normals)
        elif normals is not None:
            output.normals_split_custom_set(normals)
        return output
    except Exception:
        if output is not None:
            bpy.data.meshes.remove(output)
        raise
    finally:
        bpy.data.objects.remove(probe, do_unlink=True)
        bpy.data.meshes.remove(probe_data)


def apply_preserving_meshes(context, arm):
    """Full-armature static rest commit, in one undoable operator transaction."""
    if context.mode != 'POSE' or context.active_object != arm:
        raise ValueError('Select the armature in Pose Mode')
    if any(obj != arm for obj in context.objects_in_mode):
        raise ValueError('Use Pose Mode on one armature at a time when committing its rest pose')
    meshes = bound_meshes(context, arm)
    _validate(arm, meshes)
    staged = []
    backup = None
    bases = {p.name: p.matrix_basis.copy() for p in arm.pose.bones}
    original_armature = arm.data
    committed = False
    try:
        for obj in meshes:
            staged.append((obj, obj.data, _bake_mesh(context, obj, arm)))
        backup = arm.data.copy()
        result = bpy.ops.pose.armature_apply(selected=False)
        if result != {'FINISHED'}:
            raise ValueError('Blender could not apply the rest pose')
        context.view_layer.update()
        # Constraints/actions have been rejected, so all deformation must now be
        # neutral. Never silently bake a shape twice if Blender cannot reset it.
        if any(max(abs(p.matrix_basis[r][c] - (r == c)) for r in range(4) for c in range(4)) > 1e-5
               for p in arm.pose.bones):
            raise ValueError('The pose did not reset completely; mesh binding was not changed')
        from ..io.skeleton import ExportPlan
        plan = ExportPlan(arm)
        plan.validate_bind_transforms(plan.bind_globals())
        for obj, original, output in staged:
            obj.data = output
        context.view_layer.update()
        committed = True
        return len(meshes)
    finally:
        if not committed:
            for obj, original, output in staged:
                obj.data = original
                bpy.data.meshes.remove(output)
            if backup is not None:
                # Native Apply mutates the armature datablock in place. Restore a
                # full copy on failure, including bone metadata and rest axes.
                arm.data = backup
                for p in arm.pose.bones:
                    p.matrix_basis = bases[p.name]
                if original_armature.users == 0:
                    bpy.data.armatures.remove(original_armature)
                backup = None
        if backup is not None:
            bpy.data.armatures.remove(backup)
        context.view_layer.update()


class POSE_OT_apply_rest_keep_meshes(bpy.types.Operator):
    """Keep already-bound meshes in their posed shape, including hidden meshes; unbound meshes stay unchanged"""
    bl_idname = 'pose.aventurine_apply_rest'
    bl_label = 'Apply Pose as Rest Pose (Keep Bound Meshes)'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and is_league_armature(context.active_object)

    def execute(self, context):
        try:
            count = apply_preserving_meshes(context, context.active_object)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Applied rest pose and preserved {count} bound mesh(es)')
        return {'FINISHED'}


_original_draw = None


def _draw_apply(self, context):
    if not is_league_armature(context.active_object):
        return _original_draw(self, context)
    layout = self.layout
    layout.operator(POSE_OT_apply_rest_keep_meshes.bl_idname)
    # Partial rest changes are deliberately Blender's original operation.
    layout.operator('pose.armature_apply', text='Apply Selected as Rest Pose (Bones Only)').selected = True
    layout.operator('pose.visual_transform_apply')
    layout.separator()
    layout.operator('object.assign_property_defaults').process_bones = True


def register():
    global _original_draw
    bpy.utils.register_class(POSE_OT_apply_rest_keep_meshes)
    menu = bpy.types.VIEW3D_MT_pose_apply
    # Respect other add-ons' appended menu callbacks and restore the exact base
    # draw function on disable; no global operator replacement or scene handler.
    callbacks = getattr(menu.draw, '_draw_funcs', None)
    if callbacks is not None:
        _original_draw = callbacks[0]
        callbacks[0] = _draw_apply
    else:
        _original_draw = menu.draw
        menu.draw = _draw_apply


def unregister():
    menu = bpy.types.VIEW3D_MT_pose_apply
    callbacks = getattr(menu.draw, '_draw_funcs', None)
    if callbacks is not None and _draw_apply in callbacks:
        callbacks[callbacks.index(_draw_apply)] = _original_draw
    elif menu.draw == _draw_apply:
        menu.draw = _original_draw
    bpy.utils.unregister_class(POSE_OT_apply_rest_keep_meshes)
