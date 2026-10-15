import bpy
import os
import struct
from ..utils.backend import require_ritoshark
import base64
import json
import mathutils

def read_scb(filepath):
    """Decode the SCB container in Rust; adapt coordinates for Blender."""
    source = require_ritoshark().Scb.from_path(str(filepath))
    if source.version not in ((2, 1), (2, 2), (3, 1), (3, 2)):
        raise ValueError(f'Unsupported SCB version: {source.version}')
    vertices = [mathutils.Vector((-x, -z, y)) for x, y, z in struct.iter_unpack('<3f', source.positions)]
    colors = [(r / 255, g / 255, b / 255, a / 255)
              for b, g, r, a in struct.iter_unpack('4B', source.colors or b'')]
    indices, uvs, materials, source_indices = [], [], [], []
    for face in source.faces:
        source_indices.extend(face.indices)
        if len(set(face.indices)) != 3:
            continue
        indices.extend(face.indices)
        uvs.extend(mathutils.Vector(uv) for uv in face.uvs)
        materials.append(face.material)
    cx, cy, cz = source.central
    return {
        'name': os.path.splitext(os.path.basename(filepath))[0],
        'vertices': vertices, 'indices': indices, 'uvs': uvs, 'colors': colors,
        'material': materials[-1] if materials else 'lambert69',
        'central': mathutils.Vector((-cx, -cz, cy)),
        'scb_flag': source.flags, 'file_name': source.name, 'materials': materials,
        'trailing': source.trailing, 'source_indices': source_indices, 'version': source.version,
    }

def create_mesh(scb_data):
    """Create Blender mesh from SCB data"""
    mesh = bpy.data.meshes.new(scb_data['name'])
    
    # Vertices are already transformed to Blender space
    # Just need to build the list of tuples
    vertices = [(v.x, v.y, v.z) for v in scb_data['vertices']]
    
    # Faces
    face_count = len(scb_data['indices']) // 3
    faces = []
    face_uvs = []
    
    for i in range(face_count):
        idx = i * 3
        faces.append([
            scb_data['indices'][idx],
            scb_data['indices'][idx + 1],
            scb_data['indices'][idx + 2]
        ])
        
        face_uvs.append([
            scb_data['uvs'][idx],
            scb_data['uvs'][idx + 1],
            scb_data['uvs'][idx + 2]
        ])
    
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    
    # UVs
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for face_idx, face in enumerate(mesh.polygons):
        face_uv = face_uvs[face_idx]
        for loop_idx, loop in enumerate(face.loop_indices):
            uv = face_uv[loop_idx]
            uv_layer.data[loop].uv = (uv.x, 1.0 - uv.y) # Flip V
            
    # Per-vertex colors (POINT domain, indexed by vertex like the SCB file)
    colors = scb_data.get('colors')
    if colors and len(colors) == len(vertices):
        ca = mesh.color_attributes.new('Color', 'BYTE_COLOR', 'POINT')
        for item, color in zip(ca.data, colors):
            item.color_srgb = color

    slots = {}
    for polygon, name in zip(mesh.polygons, scb_data['materials']):
        if name not in slots:
            slots[name] = len(mesh.materials)
            mat = bpy.data.materials.new(name)
            mat.use_nodes = True
            mat['lol_material_name'] = name
            mesh.materials.append(mat)
        polygon.material_index = slots[name]
    mesh['lol_scb_version'] = scb_data['version']
    mesh['lol_scb_name'] = scb_data['file_name']
    mesh['lol_scb_trailing'] = base64.b64encode(scb_data['trailing']).decode('ascii')
    mesh['lol_scb_indices'] = json.dumps(scb_data['source_indices'])

    return mesh

def load(operator, context, filepath):
    try:
        scb_data = read_scb(filepath)
        mesh = create_mesh(scb_data)
        
        obj = bpy.data.objects.new(scb_data['name'], mesh)
        context.collection.objects.link(obj)
        
        # Store import path for export convenience
        obj["lol_scb_filepath"] = filepath
        obj["lol_scb_filename"] = os.path.basename(filepath)

        # Preserve central point (Blender space) and flag so export round-trips
        # them instead of resetting the locator to the object origin.
        central = scb_data['central']
        obj["lol_scb_central"] = [central.x, central.y, central.z]
        obj["lol_scb_flag"] = scb_data['scb_flag']
        
        # Set active
        context.view_layer.objects.active = obj
        obj.select_set(True)
        
        operator.report({'INFO'}, f"Imported SCB: {scb_data['name']}")
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed to load SCB: {str(e)}")
        return {'CANCELLED'}
