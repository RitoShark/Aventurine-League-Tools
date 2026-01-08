import bpy
from bpy.types import Operator
from bpy.props import BoolProperty


class MESH_OT_show_normals(Operator):
    """Toggle face orientation overlay (show normals)"""
    bl_idname = "mesh.show_normals"
    bl_label = "Show Face Orientation"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def execute(self, context):
        # Toggle face orientation overlay
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.overlay.show_face_orientation = not space.overlay.show_face_orientation
                        status = "enabled" if space.overlay.show_face_orientation else "disabled"
                        self.report({'INFO'}, f"Face orientation display {status}")
                        return {'FINISHED'}
        
        self.report({'WARNING'}, "No 3D View found")
        return {'CANCELLED'}


class MESH_OT_recalculate_normals_outside(Operator):
    """Recalculate normals to point outward"""
    bl_idname = "mesh.recalculate_normals_outside"
    bl_label = "Outside"
    bl_options = {'REGISTER', 'UNDO'}

    # NO poll() method - button will always show, controlled by UI

    def execute(self, context):
        # Check if in edit mode
        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "Must be in Edit Mode")
            return {'CANCELLED'}
        
        # Use Blender's native recalculate normals (outside)
        bpy.ops.mesh.normals_make_consistent(inside=False)
        self.report({'INFO'}, "Normals recalculated outside")
        return {'FINISHED'}


class MESH_OT_recalculate_normals_inside(Operator):
    """Recalculate normals to point inward"""
    bl_idname = "mesh.recalculate_normals_inside"
    bl_label = "Inside"
    bl_options = {'REGISTER', 'UNDO'}

    # NO poll() method - button will always show, controlled by UI

    def execute(self, context):
        # Check if in edit mode
        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "Must be in Edit Mode")
            return {'CANCELLED'}
        
        # Use Blender's native recalculate normals (inside)
        bpy.ops.mesh.normals_make_consistent(inside=True)
        self.report({'INFO'}, "Normals recalculated inside")
        return {'FINISHED'}


class MESH_OT_flip_normals(Operator):
    """Flip/reverse normals of selected faces"""
    bl_idname = "mesh.flip_normals_selected"
    bl_label = "Reverse Normals"
    bl_options = {'REGISTER', 'UNDO'}

    # NO poll() method - button will always show, controlled by UI

    def execute(self, context):
        # Check if in edit mode
        if context.mode != 'EDIT_MESH':
            self.report({'WARNING'}, "Must be in Edit Mode")
            return {'CANCELLED'}
        
        # Flip normals
        bpy.ops.mesh.flip_normals()
        self.report({'INFO'}, "Normals reversed")
        return {'FINISHED'}


# Registration
classes = (
    MESH_OT_show_normals,
    MESH_OT_recalculate_normals_outside,
    MESH_OT_recalculate_normals_inside,
    MESH_OT_flip_normals,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
