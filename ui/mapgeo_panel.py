import bpy
from bpy.types import Panel
from . import icons


class LOL_PT_MapGeoPanel(Panel):
    """Dedicated N-panel tab for League map geometry (.mapgeo).

    Kept in its own toggle-able tab (opt-in via addon preferences) because
    map-making is a niche workflow and we don't want to clutter the main panel.
    """
    bl_label = "Aventurine MapGeo"
    bl_idname = "VIEW3D_PT_lol_mapgeo"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Aventurine MapGeo'

    def draw_header(self, context):
        self.layout.label(text="", icon_value=icons.get_icon("icon_55"))

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="MAPGEO (Map Geometry)", icon='WORLD')
        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator("import_scene.mapgeo", text="Import", icon='IMPORT')
        row.operator("export_scene.mapgeo", text="Export", icon='EXPORT')

        # Surface the imported source so users know Original bucket grids will be
        # carried through on export (and warn when they won't).
        src = context.scene.get('mapgeo_source')
        version = context.scene.get('mapgeo_version')
        if src:
            info = box.box()
            info.label(text=f"Imported source: v{version}" if version else "Imported source loaded",
                       icon='CHECKMARK')
            info.label(text="Export copies the original bucket grids.", icon='INFO')
        else:
            box.label(text="No imported source — grids export disabled.", icon='INFO')


_CLASSES = (LOL_PT_MapGeoPanel,)


def register():
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
