import bpy
import os
import mathutils

def sanitize_name(name):
    if not name: return "unnamed"
    return name.replace('\x00', '').strip()

def read_sco(filepath):
    """Read SCO file and return data"""
    with open(filepath, 'r') as f:
        lines = [l.strip() for l in f.readlines()]
        
    if not lines or lines[0] != '[ObjectBegin]':
        raise ValueError("Invalid SCO header")
        
    data = {
        'name': 'sco_mesh',
        'central': mathutils.Vector((0,0,0)),
        'pivot': None,
        'vertices': [],
        'indices': [],
        'uvs': [],
        'material': 'lambert1', 'materials': []
    }
    
    i = 1
    while i < len(lines):
        line = lines[i]
        parts = line.split()
        if not parts:
            i += 1
            continue
            
        if line.startswith('Name='):
            data['name'] = line.split('=')[1].strip()
            
        elif line.startswith('CentralPoint='):
            data['central'] = mathutils.Vector((float(parts[1]), float(parts[2]), float(parts[3])))
            
        elif line.startswith('PivotPoint='):
            data['pivot'] = mathutils.Vector((float(parts[1]), float(parts[2]), float(parts[3])))
            
        elif line.startswith('Verts='):
            count = int(parts[1])
            for _ in range(count):
                i += 1
                vp = lines[i].split()
                x, y, z = float(vp[0]), float(vp[1]), float(vp[2])
                data['vertices'].append(mathutils.Vector((-x, -z, y))) # Coordinate flip
                
        elif line.startswith('Faces='):
            count = int(parts[1])
            for f_idx in range(count):
                i += 1
                fp = lines[i].replace('\t', ' ').split()
                # Format: 3 v1 v2 v3 mat u1 v1 u2 v2 u3 v3
                if len(fp) < 11: raise ValueError('Truncated SCO face')
                
                idx = [int(fp[1]), int(fp[2]), int(fp[3])]
                # Skip degenerate
                if idx[0] == idx[1] or idx[1] == idx[2] or idx[0] == idx[2]:
                    continue
                    
                if len(fp) != 11 or int(fp[0]) != 3 or any(v < 0 or v >= len(data['vertices']) for v in idx):
                    raise ValueError('Invalid SCO face')
                data['indices'].extend(idx)
                data['materials'].append(fp[4])
                
                if f_idx == 0:
                    data['material'] = fp[4]
                    
                data['uvs'].append(mathutils.Vector((float(fp[5]), float(fp[6]))))
                data['uvs'].append(mathutils.Vector((float(fp[7]), float(fp[8]))))
                data['uvs'].append(mathutils.Vector((float(fp[9]), float(fp[10]))))
                
        i += 1

    # Transform Central/Pivot
    c = data['central']
    data['central'] = mathutils.Vector((-c.x, -c.z, c.y))
    if data['pivot']:
        p = data['pivot']
        data['pivot'] = mathutils.Vector((-p.x, -p.z, p.y))
        
    return data

def create_mesh_and_obj(context, data):
    mesh = bpy.data.meshes.new(data['name'])
    
    verts = [(v.x, v.y, v.z) for v in data['vertices']]
    
    faces = []
    face_uvs = []
    
    idx_list = data['indices']
    uv_list = data['uvs']
    
    for i in range(0, len(idx_list), 3):
        faces.append(idx_list[i:i+3])
        face_uvs.append(uv_list[i:i+3])
        
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    
    # UVs
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly_idx, poly in enumerate(mesh.polygons):
        uvs = face_uvs[poly_idx]
        for loop_idx, loop in enumerate(poly.loop_indices):
            u, v = uvs[loop_idx]
            uv_layer.data[loop].uv = (u, 1.0 - v)
            
    names = data.get('materials') or [data['material']] * len(mesh.polygons)
    slots = {}
    for polygon, name in zip(mesh.polygons, names):
        if name not in slots:
            slots[name] = len(mesh.materials)
            mat = bpy.data.materials.new(name)
            mat.use_nodes = True
            mat['lol_material_name'] = name
            mesh.materials.append(mat)
        polygon.material_index = slots[name]

    obj = bpy.data.objects.new(data['name'], mesh)
    context.collection.objects.link(obj)
    
    obj['lol_sco_central'] = list(data['central'])
    if data['pivot'] is not None:
        obj['lol_sco_pivot'] = list(data['pivot'])

    return obj

def load(operator, context, filepath):
    try:
        data = read_sco(filepath)
        obj = create_mesh_and_obj(context, data)
        
        # Store import path for export convenience
        obj["lol_sco_filepath"] = filepath
        obj["lol_sco_filename"] = os.path.basename(filepath)
        
        context.view_layer.objects.active = obj
        obj.select_set(True)
        
        operator.report({'INFO'}, f"Imported SCO: {data['name']}")
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed to load SCO: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
