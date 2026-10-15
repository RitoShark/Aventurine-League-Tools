"""
SCB Export Operator for V4
Native SCB (Static Object Binary) exporter for League of Legends
"""

import bpy
import os
from ..utils.backend import require_ritoshark
from ..utils.format_data import packed
import base64
import json
import math
from bpy.types import Operator
from bpy.props import StringProperty, FloatProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector


class ExportSCB(Operator, ExportHelper):
    """Export League of Legends SCB (Static Object Binary)"""
    bl_idname = "export_scene.scb"
    bl_label = "Export SCB"
    bl_description = "Export selected mesh as SCB static object"
    bl_options = {'REGISTER', 'UNDO'}
    
    filename_ext = ".scb"
    filter_glob: StringProperty(default="*.scb", options={'HIDDEN'})
    
    scale_factor: FloatProperty(
        name="Scale Factor",
        description="Scale factor for export (1.0 = same size as imported)",
        default=1.0,
        min=0.0001,
        max=100.0
    )
    
    use_riot_reference: BoolProperty(
        name="Use Riot Reference",
        description="Preserve central point and flags from riot.scb if available",
        default=True
    )
    
    check_existing: BoolProperty(
        name="Confirm Overwrite",
        description="Prompt before overwriting existing files",
        default=True
    )
    
    def invoke(self, context, event):
        # Set default filename from imported file if available
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            # Try to find mesh from selection
            for sel_obj in context.selected_objects:
                if sel_obj.type == 'MESH':
                    obj = sel_obj
                    break
        
        if obj:
            # Try stored filepath from import
            path = obj.get('lol_scb_filepath')
            if path:
                self.filepath = path
            elif 'lol_scb_original_name' in obj:
                # Fallback to old property
                original_name = obj['lol_scb_original_name']
                if bpy.data.filepath:
                    default_dir = os.path.dirname(bpy.data.filepath)
                else:
                    default_dir = os.path.expanduser("~")
                self.filepath = os.path.join(default_dir, f"{original_name}.scb")
        
        return super().invoke(context, event)
    
    def execute(self, context):
        # Get selected mesh
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object to export")
            return {'CANCELLED'}
        
        # Check for multiple meshes
        selected_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if len(selected_meshes) > 1:
            self.report({'ERROR'}, "Select only one mesh object")
            return {'CANCELLED'}
        
        try:
            # Check for riot.scb reference
            riot_data = None
            if self.use_riot_reference:
                riot_data = self.load_riot_reference(self.filepath)
            
            # Export SCB
            self.export_scb(context, obj, self.filepath, self.scale_factor, riot_data)
            
            self.report({'INFO'}, f"Successfully exported {os.path.basename(self.filepath)}")
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export SCB: {str(e)}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}
    
    def load_riot_reference(self, filepath):
        """Load riot.scb reference file if it exists"""
        dirname = os.path.dirname(filepath)
        basename = os.path.basename(filepath)
        
        # Try riot_{filename}.scb first
        path1 = os.path.join(dirname, f"riot_{basename}")
        if os.path.exists(path1):
            return self.read_riot_scb(path1)
        
        # Try riot.scb
        path2 = os.path.join(dirname, "riot.scb")
        if os.path.exists(path2):
            return self.read_riot_scb(path2)
        
        return None
    
    def read_riot_scb(self, path):
        """Read reference metadata through the same Rust SCB parser."""
        source = require_ritoshark().Scb.from_path(str(path))
        return {'central': Vector(source.central), 'scb_flag': source.flags}

    def collect_vertex_colors(self, mesh, vertex_count):
        """Return a per-vertex list of (r, g, b, a) bytes (0-255), or None.

        SCB stores exactly one color per vertex. A POINT-domain color
        attribute maps directly. Conflicting CORNER colors require split vertices.
        """
        if not mesh.color_attributes:
            return None

        ca = mesh.color_attributes.active_color or mesh.color_attributes[0]

        def to_bytes(col):
            return tuple(max(0, min(255, int(round(c * 255.0)))) for c in
                         (col[0], col[1], col[2], col[3]))

        if ca.domain == 'POINT':
            if len(ca.data) != vertex_count:
                raise ValueError('SCB vertex color count does not match the mesh')
            return [to_bytes(ca.data[i].color_srgb) for i in range(vertex_count)]

        if ca.domain == 'CORNER':
            colors = [None] * vertex_count
            for poly in mesh.polygons:
                for loop_idx in poly.loop_indices:
                    vi = mesh.loops[loop_idx].vertex_index
                    color = to_bytes(ca.data[loop_idx].color_srgb)
                    if colors[vi] is not None and colors[vi] != color:
                        raise ValueError('SCB stores one color per vertex; split vertices at color seams before exporting')
                    colors[vi] = color
            # Fill any unreferenced vertices with opaque white
            return [c if c is not None else (255, 255, 255, 255) for c in colors]

        return None

    def export_scb(self, context, obj, filepath, scale_factor, riot_data):
        """Export mesh to SCB format"""
        mesh = obj.data
        
        # Ensure mesh is evaluated (applies modifiers)
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.data
        
        # Get mesh data - vertices in Blender world space
        # Maya exporter gets vertices in world space and central as transform translation
        vertices_world = []
        for v in eval_mesh.vertices:
            world_pos = eval_obj.matrix_world @ v.co
            vertices_world.append(world_pos)
        
        # Determine central point (Blender space, unscaled). Priority:
        #   1. riot.scb reference (raw file coords -> Blender space)
        #   2. central stashed on the object at import time
        #   3. object origin in world space (pivot point) as a last resort
        # Storing absolute vertices (below) means the central never shifts the
        # geometry/bbox; this only controls the locator, so preserving the
        # original value keeps round-trips faithful.
        if riot_data and riot_data.get('central') is not None:
            rc = riot_data['central']  # raw file coords (cx, cy, cz)
            central_world = Vector((-rc.x, -rc.z, rc.y))
        elif 'lol_scb_central' in obj:
            c = obj['lol_scb_central']
            central_world = Vector((c[0], c[1], c[2]))
        else:
            central_world = eval_obj.matrix_world.translation
        
        # Scale to SCB units (before coordinate transform)
        vertices_scaled = [v * scale_factor for v in vertices_world]
        central_scaled = central_world * scale_factor
        
        # Transform central point to SCB coordinate system
        # Import expects: c_blender = (-cx, -cz, cy) where (cx, cy, cz) is in file
        # So if central_scaled = (x, y, z) in Blender space:
        #   We want: (-cx, -cz, cy) = (x, y, z) after import transform
        #   So: -cx = x => cx = -x
        #       -cz = y => cz = -y  
        #       cy = z => cy = z
        #   Therefore file has: (cx, cy, cz) = (-x, z, -y)
        central_scb = Vector((-central_scaled.x, central_scaled.z, -central_scaled.y))
        
        # Transform vertices to SCB coordinate system as ABSOLUTE positions
        # Do NOT subtract central - store absolute positions like Maya does
        vertices = []
        for v in vertices_scaled:
            # Transform vertex to SCB coordinate system (absolute position)
            v_scb = Vector((-v.x, v.z, -v.y))
            vertices.append(v_scb)
        
        # Central point in SCB format (absolute position)
        central = central_scb
        
        # Get UV layer
        if not eval_mesh.uv_layers.active:
            raise ValueError("Mesh has no UV coordinates")
        
        uv_layer = eval_mesh.uv_layers.active
        
        # Triangulate if needed using bmesh (preserves UVs)
        import bmesh
        bm = bmesh.new()
        bm.from_mesh(eval_mesh)
        
        # Ensure UV layer exists in bmesh
        uv_layer_bm = bm.loops.layers.uv.active
        if not uv_layer_bm:
            # Create UV layer if it doesn't exist
            uv_layer_bm = bm.loops.layers.uv.new("UVMap")
        
        # Triangulate
        bmesh.ops.triangulate(bm, faces=bm.faces)
        
        # Get faces and UVs from bmesh
        faces = []
        face_uvs = []
        face_materials = []
        
        # Update vertex indices to match our vertex list
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        
        # Collect face data
        for face in bm.faces:
            if len(face.verts) != 3:
                continue  # Skip non-triangles
            
            # Get vertex indices
            face_indices = [v.index for v in face.verts]
            faces.append(face_indices)
            material = eval_mesh.materials[face.material_index] if face.material_index < len(eval_mesh.materials) else None
            face_materials.append(material.get('lol_material_name', material.name) if material else 'lambert69')
            
            # Get UVs for this face (per-face format)
            face_uv = []
            for loop in face.loops:
                uv = loop[uv_layer_bm].uv
                # Flip V coordinate
                face_uv.append(Vector((uv.x, 1.0 - uv.y)))
            
            face_uvs.append(face_uv)
        
        bm.free()
        
        # Get material name
        material_name = 'lambert69'
        if eval_mesh.materials and eval_mesh.materials[0]:
            material_name = eval_mesh.materials[0].name
            if len(material_name) > 64:
                material_name = material_name[:64]
        
        # Get SCB flag
        scb_flag = 0  # New files have no optional trailing sections
        if riot_data:
            scb_flag = riot_data['scb_flag']
        elif 'lol_scb_flag' in obj:
            scb_flag = obj['lol_scb_flag']

        # Collect per-vertex colors if the mesh carries them, so we can
        # round-trip vertex_type=1 meshes instead of always writing type 0.
        # Vertices were built in eval_mesh.vertex order (triangulation keeps
        # vertex indices), so a POINT-domain color attribute aligns 1:1.
        vertex_colors = self.collect_vertex_colors(eval_mesh, len(vertices))

        trailing = base64.b64decode(mesh.get('lol_scb_trailing', ''), validate=True)
        file_name = mesh.get('lol_scb_name', obj.name).encode('utf-8')
        if len(file_name) > 128 or b'\0' in file_name:
            raise ValueError('SCB object name must fit in 128 UTF-8 bytes')
        if any(len(name.encode('utf-8')) > 64 or '\0' in name for name in face_materials):
            raise ValueError('SCB material names must fit in 64 UTF-8 bytes')
        if trailing:
            source = json.loads(mesh.get('lol_scb_indices', '[]'))
            if source != [index for face in faces for index in face]:
                raise ValueError('SCB contains opaque trailing data; changing its face layout is not supported')
        elif scb_flag & 1:
            raise ValueError('SCB declares per-corner colors but has no trailing color data')

        rs = require_ritoshark()
        colors = bytes(channel for r, g, b, a in vertex_colors for channel in (b, g, r, a)) if vertex_colors else None
        version = tuple(mesh.get('lol_scb_version', (3, 2)))
        # Legacy SCB containers cannot encode newly authored per-vertex colors.
        if colors is not None:
            version = (3, 2)
        result = rs.Scb.new(
            file_name.decode('utf-8'), packed('f', vertices),
            [rs.ScbFace(name, tuple(face), tuple(tuple(uv) for uv in face_uv))
             for name, face, face_uv in zip(face_materials, faces, face_uvs)],
            central=tuple(central), colors=colors, flags=scb_flag, trailing=trailing, version=version,
        )
        # Build all bytes before opening the destination, so validation cannot truncate it.
        from pathlib import Path
        Path(filepath).write_bytes(result.to_bytes())
