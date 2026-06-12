"""File handlers for drag-and-drop import functionality"""
import bpy


# bpy.types.FileHandler was introduced in Blender 4.1. On 4.0 and earlier,
# subclassing it at module load raises AttributeError and breaks addon install.
_HAS_FILE_HANDLER = hasattr(bpy.types, "FileHandler")


if _HAS_FILE_HANDLER:
    class FH_SKN_Import(bpy.types.FileHandler):
        """File handler for dragging .skn files into Blender"""
        bl_idname = "LOL_FH_skn_import"
        bl_label = "SKN File Handler"
        bl_import_operator = "import_scene.skn_dragdrop"
        bl_file_extensions = ".skn"

        @classmethod
        def poll_drop(cls, context):
            return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


    class FH_SKL_Import(bpy.types.FileHandler):
        """File handler for dragging .skl files into Blender"""
        bl_idname = "LOL_FH_skl_import"
        bl_label = "SKL File Handler"
        bl_import_operator = "import_scene.skl_dragdrop"
        bl_file_extensions = ".skl"

        @classmethod
        def poll_drop(cls, context):
            return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


    class FH_ANM_Import(bpy.types.FileHandler):
        """File handler for dragging .anm files into Blender"""
        bl_idname = "LOL_FH_anm_import"
        bl_label = "ANM File Handler"
        bl_import_operator = "import_scene.anm_dragdrop"
        bl_file_extensions = ".anm"

        @classmethod
        def poll_drop(cls, context):
            return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


    class FH_SCB_Import(bpy.types.FileHandler):
        """File handler for dragging .scb files into Blender"""
        bl_idname = "LOL_FH_scb_import"
        bl_label = "SCB File Handler"
        bl_import_operator = "import_scene.scb_dragdrop"
        bl_file_extensions = ".scb"

        @classmethod
        def poll_drop(cls, context):
            return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


    class FH_SCO_Import(bpy.types.FileHandler):
        """File handler for dragging .sco files into Blender"""
        bl_idname = "LOL_FH_sco_import"
        bl_label = "SCO File Handler"
        bl_import_operator = "import_scene.sco_dragdrop"
        bl_file_extensions = ".sco"

        @classmethod
        def poll_drop(cls, context):
            return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


    class FH_MAPGEO_Import(bpy.types.FileHandler):
        """File handler for dragging .mapgeo files into Blender"""
        bl_idname = "LOL_FH_mapgeo_import"
        bl_label = "MAPGEO File Handler"
        bl_import_operator = "import_scene.mapgeo_dragdrop"
        bl_file_extensions = ".mapgeo"

        @classmethod
        def poll_drop(cls, context):
            return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


    classes = (
        FH_SKN_Import,
        FH_SKL_Import,
        FH_ANM_Import,
        FH_SCB_Import,
        FH_SCO_Import,
        FH_MAPGEO_Import,
    )
else:
    classes = ()


def register():
    """Register all file handlers (no-op on Blender < 4.1)"""
    if not _HAS_FILE_HANDLER:
        print("[Aventurine] Skipping drag-and-drop file handlers (requires Blender 4.1+)")
        return
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    """Unregister all file handlers (no-op on Blender < 4.1)"""
    if not _HAS_FILE_HANDLER:
        return
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
