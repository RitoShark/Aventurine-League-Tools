"""
UV Transform Operators
Maya-style UV editing controls for the UV/Image editor:
  - Move selected UVs by a set amount in a direction
  - Scale selected UVs by a factor (U / V / both axes)
  - Rotate selected UVs by an angle (CW / CCW)
  - Snap selected UVs to a corner of UV space at half size (the original tool)

Blender 5.0 compatibility
--------------------------
Blender 5.0 removed ``bmesh.types.BMLoopUV.select`` (and ``select_edge``).
Per-loop UV vertex selection now lives directly on the loop:
  - read:  ``loop.uv_select_vert``
  - write: ``loop.uv_select_vert_set(bool)``
The UV *coordinate* accessor (``loop[uv_layer].uv``) is unchanged.
``_uv_vert_selected()`` below picks the right path at runtime so the same
code works on Blender 4.x and 5.x+.
"""

import math

import bpy
import bmesh
from bpy.types import Operator, PropertyGroup
from bpy.props import FloatProperty, BoolProperty, EnumProperty
from mathutils import Vector


# ---------------------------------------------------------------------------
# Version-robust selection access
# ---------------------------------------------------------------------------

def _uv_vert_selected(loop, uv_data):
    """Return True if this loop's UV vertex is selected.

    Blender 5.0+ exposes ``loop.uv_select_vert``; earlier versions store the
    flag on the per-loop UV data (``uv_data.select``).
    """
    # Blender 5.0+
    if hasattr(loop, "uv_select_vert"):
        return loop.uv_select_vert
    # Blender 4.x and earlier
    try:
        return uv_data.select
    except AttributeError:
        return False


def _get_selected_uv_loops(context):
    """Resolve the active mesh's bmesh, UV layer and selected UV loops.

    Returns a tuple ``(obj, bm, uv_layer, selected_loops)`` or ``None`` when
    there is nothing valid to operate on. The caller is responsible for
    calling ``bmesh.update_edit_mesh`` after editing.
    """
    obj = context.active_object
    if not obj or obj.type != 'MESH':
        return None

    if not obj.data.uv_layers.active:
        return None

    # Ensure we're in edit mode
    if obj.mode != 'EDIT':
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')

    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    uv_layer = bm.loops.layers.uv.active
    if not uv_layer:
        return None

    selected_loops = []
    for face in bm.faces:
        if face.hide:
            continue
        for loop in face.loops:
            uv_data = loop[uv_layer]
            if _uv_vert_selected(loop, uv_data):
                selected_loops.append(loop)

    if not selected_loops:
        return None

    return obj, bm, uv_layer, selected_loops


def _selection_center(selected_loops, uv_layer):
    """Bounding-box center of the selected UVs (used as scale/rotate pivot)."""
    mins = Vector((float('inf'), float('inf')))
    maxs = Vector((float('-inf'), float('-inf')))
    for loop in selected_loops:
        uv = loop[uv_layer].uv
        mins.x = min(mins.x, uv.x)
        mins.y = min(mins.y, uv.y)
        maxs.x = max(maxs.x, uv.x)
        maxs.y = max(maxs.y, uv.y)
    return (mins + maxs) * 0.5


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class LOL_UVTransformProperties(PropertyGroup):
    """Settings for the Maya-style UV transform tools."""

    move_amount: FloatProperty(
        name="Move Amount",
        description="Distance to move selected UVs (in UV units, 1.0 = full tile)",
        default=0.1,
        min=0.0,
        soft_max=1.0,
        precision=4,
    )

    rotate_degrees: FloatProperty(
        name="Angle",
        description="Rotation angle in degrees, around the selection center",
        default=90.0,
        precision=2,
    )

    scale_factor: FloatProperty(
        name="Scale",
        description="Scale factor applied around the selection center",
        default=2.0,
        precision=4,
    )

    prevent_negative_scale: BoolProperty(
        name="Prevent Negative Scale",
        description="Clamp the scale factor so it can never flip/mirror the UVs",
        default=True,
    )


# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------

class UV_OT_move_directional(Operator):
    """Move selected UVs by the set amount in a direction"""
    bl_idname = "uv.lol_move_directional"
    bl_label = "Move UVs"
    bl_description = "Move selected UVs by the set amount in the chosen direction"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=(
            ('UP', "Up", "Move +V"),
            ('DOWN', "Down", "Move -V"),
            ('LEFT', "Left", "Move -U"),
            ('RIGHT', "Right", "Move +U"),
            ('UP_LEFT', "Up Left", "Move -U +V"),
            ('UP_RIGHT', "Up Right", "Move +U +V"),
            ('DOWN_LEFT', "Down Left", "Move -U -V"),
            ('DOWN_RIGHT', "Down Right", "Move +U -V"),
        ),
        default='RIGHT',
    )

    def execute(self, context):
        result = _get_selected_uv_loops(context)
        if result is None:
            return {'CANCELLED'}
        obj, bm, uv_layer, selected_loops = result

        amount = context.scene.lol_uv_transform.move_amount
        offsets = {
            'UP': Vector((0.0, amount)),
            'DOWN': Vector((0.0, -amount)),
            'LEFT': Vector((-amount, 0.0)),
            'RIGHT': Vector((amount, 0.0)),
            'UP_LEFT': Vector((-amount, amount)),
            'UP_RIGHT': Vector((amount, amount)),
            'DOWN_LEFT': Vector((-amount, -amount)),
            'DOWN_RIGHT': Vector((amount, -amount)),
        }
        offset = offsets.get(self.direction, Vector((0.0, 0.0)))

        for loop in selected_loops:
            loop[uv_layer].uv = loop[uv_layer].uv + offset

        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------

class UV_OT_scale(Operator):
    """Scale selected UVs by the set factor around their center"""
    bl_idname = "uv.lol_scale"
    bl_label = "Scale UVs"
    bl_description = "Scale selected UVs by the set factor around their center"
    bl_options = {'REGISTER', 'UNDO'}

    axis: EnumProperty(
        name="Axis",
        items=(
            ('BOTH', "Both", "Scale on U and V"),
            ('U', "U", "Scale on U only"),
            ('V', "V", "Scale on V only"),
        ),
        default='BOTH',
    )

    def execute(self, context):
        result = _get_selected_uv_loops(context)
        if result is None:
            return {'CANCELLED'}
        obj, bm, uv_layer, selected_loops = result

        props = context.scene.lol_uv_transform
        factor = props.scale_factor
        if props.prevent_negative_scale and factor < 0.0:
            factor = abs(factor)

        sx = factor if self.axis in {'BOTH', 'U'} else 1.0
        sy = factor if self.axis in {'BOTH', 'V'} else 1.0

        pivot = _selection_center(selected_loops, uv_layer)
        for loop in selected_loops:
            uv = loop[uv_layer].uv
            loop[uv_layer].uv = Vector((
                (uv.x - pivot.x) * sx + pivot.x,
                (uv.y - pivot.y) * sy + pivot.y,
            ))

        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Rotate
# ---------------------------------------------------------------------------

class UV_OT_rotate(Operator):
    """Rotate selected UVs by the set angle around their center"""
    bl_idname = "uv.lol_rotate"
    bl_label = "Rotate UVs"
    bl_description = "Rotate selected UVs by the set angle around their center"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=(
            ('CCW', "Counter-Clockwise", "Rotate counter-clockwise"),
            ('CW', "Clockwise", "Rotate clockwise"),
        ),
        default='CCW',
    )

    def execute(self, context):
        result = _get_selected_uv_loops(context)
        if result is None:
            return {'CANCELLED'}
        obj, bm, uv_layer, selected_loops = result

        degrees = context.scene.lol_uv_transform.rotate_degrees
        if self.direction == 'CW':
            degrees = -degrees
        angle = math.radians(degrees)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        pivot = _selection_center(selected_loops, uv_layer)
        for loop in selected_loops:
            uv = loop[uv_layer].uv
            dx = uv.x - pivot.x
            dy = uv.y - pivot.y
            loop[uv_layer].uv = Vector((
                dx * cos_a - dy * sin_a + pivot.x,
                dx * sin_a + dy * cos_a + pivot.y,
            ))

        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Corners (original tool — now 5.0-compatible via shared helpers)
# ---------------------------------------------------------------------------

class UV_CORNER_OT_top_left(Operator):
    """Move selected UVs to top left corner and scale to half size"""
    bl_idname = "uv.corner_top_left"
    bl_label = "UV Top Left"
    bl_description = "Moves selected UVs to top left corner and makes them half the size"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return move_uvs_to_corner(context, corner='top_left')

    def invoke(self, context, event):
        return self.execute(context)


class UV_CORNER_OT_top_right(Operator):
    """Move selected UVs to top right corner and scale to half size"""
    bl_idname = "uv.corner_top_right"
    bl_label = "UV Top Right"
    bl_description = "Moves selected UVs to top right corner and makes them half the size"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return move_uvs_to_corner(context, corner='top_right')

    def invoke(self, context, event):
        return self.execute(context)


class UV_CORNER_OT_bottom_left(Operator):
    """Move selected UVs to bottom left corner and scale to half size"""
    bl_idname = "uv.corner_bottom_left"
    bl_label = "UV Bottom Left"
    bl_description = "Moves selected UVs to bottom left corner and makes them half the size"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return move_uvs_to_corner(context, corner='bottom_left')

    def invoke(self, context, event):
        return self.execute(context)


class UV_CORNER_OT_bottom_right(Operator):
    """Move selected UVs to bottom right corner and scale to half size"""
    bl_idname = "uv.corner_bottom_right"
    bl_label = "UV Bottom Right"
    bl_description = "Moves selected UVs to bottom right corner and makes them half the size"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return move_uvs_to_corner(context, corner='bottom_right')

    def invoke(self, context, event):
        return self.execute(context)


def move_uvs_to_corner(context, corner='top_left'):
    """
    Move selected UVs to a corner and scale them to half size.

    This matches the Maya functionality:
    1. Pivot to center (0.5, 0.5) and scale to 0.5x
    2. Translate to the appropriate corner

    Corner final positions:
    - top_left: (0.25, 0.75)
    - top_right: (0.75, 0.75)
    - bottom_left: (0.25, 0.25)
    - bottom_right: (0.75, 0.25)
    """
    result = _get_selected_uv_loops(context)
    if result is None:
        return {'CANCELLED'}
    obj, bm, uv_layer, selected_loops = result

    # Step 1: Scale around center (0.5, 0.5) to 0.5x
    pivot = Vector((0.5, 0.5))
    scale = 0.5
    for loop in selected_loops:
        uv = loop[uv_layer].uv.copy()
        loop[uv_layer].uv = (uv - pivot) * scale + pivot

    # Step 2: Translate to corner (offsets from center after scaling)
    corner_offsets = {
        'top_left': Vector((-0.25, 0.25)),
        'top_right': Vector((0.25, 0.25)),
        'bottom_left': Vector((-0.25, -0.25)),
        'bottom_right': Vector((0.25, -0.25)),
    }
    offset = corner_offsets.get(corner, Vector((0.0, 0.0)))
    for loop in selected_loops:
        loop[uv_layer].uv = loop[uv_layer].uv + offset

    bmesh.update_edit_mesh(obj.data)

    corner_names = {
        'top_left': 'Top Left',
        'top_right': 'Top Right',
        'bottom_left': 'Bottom Left',
        'bottom_right': 'Bottom Right',
    }
    print(f"Moved UV -> {corner_names.get(corner, corner)}")
    return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    LOL_UVTransformProperties,
    UV_OT_move_directional,
    UV_OT_scale,
    UV_OT_rotate,
    UV_CORNER_OT_top_left,
    UV_CORNER_OT_top_right,
    UV_CORNER_OT_bottom_left,
    UV_CORNER_OT_bottom_right,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lol_uv_transform = bpy.props.PointerProperty(type=LOL_UVTransformProperties)


def unregister():
    if hasattr(bpy.types.Scene, "lol_uv_transform"):
        del bpy.types.Scene.lol_uv_transform
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
