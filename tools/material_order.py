"""Artist controls for SKN submesh/material export order."""
import bpy
from bpy.props import StringProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper
from ..io import material_order
from ..utils.backend import require_ritoshark


def active_armature(context):
    obj = context.active_object
    if obj is None:
        return None
    if obj.type == 'ARMATURE':
        return obj
    if obj.type == 'MESH':
        return obj.find_armature() or (obj.parent if obj.parent and obj.parent.type == 'ARMATURE' else None)
    return None


def meshes_for(context, arm):
    return [o for o in context.scene.objects if o.type == 'MESH'
            and (o.parent == arm or o.find_armature() == arm)]


class LOL_OT_MaterialOrderMove(bpy.types.Operator):
    """Move this material earlier or later in the exported SKN submesh list"""
    bl_idname = 'lol_material_order.move'
    bl_label = 'Move SKN Material'
    bl_options = {'REGISTER', 'UNDO'}
    armature_name: StringProperty(options={'HIDDEN'})
    material_name: StringProperty(options={'HIDDEN'})
    direction: EnumProperty(items=[('UP', 'Earlier', ''), ('DOWN', 'Later', '')])

    def execute(self, context):
        arm = context.scene.objects.get(self.armature_name)
        if arm is None or arm.type != 'ARMATURE':
            return {'CANCELLED'}
        try:
            names = material_order.current_order(arm, meshes_for(context, arm))
            index = next((i for i, n in enumerate(names) if n == self.material_name), -1)
            target = index + (-1 if self.direction == 'UP' else 1)
            if index < 0 or target < 0 or target >= len(names):
                return {'CANCELLED'}
            names[index], names[target] = names[target], names[index]
            material_order.set_order(arm, names)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class LOL_OT_MaterialOrderReset(bpy.types.Operator):
    """Restore imported SKN order; newly added materials follow it"""
    bl_idname = 'lol_material_order.reset'
    bl_label = 'Reset to Imported Order'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_armature(context) is not None

    def execute(self, context):
        arm = active_armature(context)
        if material_order.ORDER in arm:
            del arm[material_order.ORDER]
        return {'FINISHED'}


class LOL_OT_MaterialOrderReference(bpy.types.Operator, ImportHelper):
    """Match material names to a reference SKN's order; unmatched materials go last"""
    bl_idname = 'lol_material_order.reference'
    bl_label = 'Match Material Order to SKN'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.skn'
    filter_glob: StringProperty(default='*.skn', options={'HIDDEN'})
    armature_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return active_armature(context) is not None

    def invoke(self, context, event):
        self.armature_name = active_armature(context).name
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        arm = context.scene.objects.get(self.armature_name) if self.armature_name else active_armature(context)
        if arm is None or arm.type != 'ARMATURE':
            self.report({'ERROR'}, 'The target armature is no longer available')
            return {'CANCELLED'}
        try:
            source = require_ritoshark().Skn.from_path(self.filepath)
            names = material_order.current_order(arm, meshes_for(context, arm))
            by_name = {n.casefold(): n for n in names}
            matched = material_order.unique(by_name[s.name.casefold()] for s in source.submeshes if s.name.casefold() in by_name)
            if not matched:
                raise ValueError('No material names match this reference SKN')
            material_order.set_order(arm, matched + names)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Matched {len(matched)} material(s); unmatched materials follow')
        return {'FINISHED'}


class LOL_PT_MaterialOrder(bpy.types.Panel):
    bl_label = 'SKN Material Order'
    bl_idname = 'LOL_PT_material_order'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Misc LoL Tools'
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        from ..ui import icons
        self.layout.label(text='', icon_value=icons.get_icon('material_order'))

    def draw(self, context):
        arm = active_armature(context)
        layout = self.layout
        if arm is None:
            box = layout.box()
            box.label(text='Select an armature or a bound mesh', icon='INFO')
            return
        try:
            names = material_order.current_order(arm, meshes_for(context, arm))
        except ValueError as error:
            layout.label(text=str(error), icon='ERROR')
            layout.operator(LOL_OT_MaterialOrderReset.bl_idname)
            return
        box = layout.box()
        box.label(text='Export Order', icon='MATERIAL')
        box.label(text='Top to bottom = SKN export order')
        if not names:
            box.label(text='No bound mesh materials', icon='INFO')
        for i, name in enumerate(names):
            row = box.row(align=True)
            row.label(text=f'{i + 1}. {name}', icon='MATERIAL')
            for direction, enabled, icon in [('UP', i > 0, 'TRIA_UP'), ('DOWN', i + 1 < len(names), 'TRIA_DOWN')]:
                button = row.row(align=True)
                button.enabled = enabled
                op = button.operator(LOL_OT_MaterialOrderMove.bl_idname, text='', icon=icon)
                op.armature_name = arm.name
                op.material_name = name
                op.direction = direction

        layout.separator()
        box = layout.box()
        box.label(text='Reference Order', icon='FILE_FOLDER')
        row = box.row(align=True)
        row.scale_y = 1.2
        row.operator(LOL_OT_MaterialOrderReference.bl_idname, text='Match SKN...', icon='FILEBROWSER')
        row.operator(LOL_OT_MaterialOrderReset.bl_idname, text='Reset', icon='LOOP_BACK')


classes = (LOL_OT_MaterialOrderMove, LOL_OT_MaterialOrderReset, LOL_OT_MaterialOrderReference)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def register_panel():
    if not LOL_PT_MaterialOrder.is_registered:
        bpy.utils.register_class(LOL_PT_MaterialOrder)


def unregister_panel():
    if LOL_PT_MaterialOrder.is_registered:
        bpy.utils.unregister_class(LOL_PT_MaterialOrder)


def sync_panel(preferences=None):
    enabled = (preferences is None or
               (preferences.enable_animation_tools and preferences.enable_material_order))
    if enabled:
        register_panel()
    else:
        unregister_panel()


def unregister():
    unregister_panel()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
