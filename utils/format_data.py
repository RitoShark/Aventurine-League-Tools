import struct

from .backend import require_ritoshark


def packed(code, rows):
    values = [value for row in rows for value in row]
    return struct.pack(f'<{len(values)}{code}', *values)


def write_skin(filepath, submeshes):
    rs = require_ritoshark()
    vertices = [vertex for submesh in submeshes for vertex in submesh['vertices']]
    rs.Skn.new(
        positions=packed('f', (v['pos'] for v in vertices)),
        normals=packed('f', (v['normal'] for v in vertices)),
        uvs=packed('f', (v['uv'] for v in vertices)),
        blend_indices=packed('I', (v['inf'] for v in vertices)),
        blend_weights=packed('f', (v['weight'] for v in vertices)),
        indices=packed('I', (sm['indices'] for sm in submeshes)),
        submeshes=[rs.Submesh(sm['name'], sm['vertex_start'], sm['vertex_count'],
                             sm['index_start'], sm['index_count']) for sm in submeshes],
    ).to_path(str(filepath))


def write_animation(filepath, fps, joint_data, vectors, rotations):
    rs = require_ritoshark()
    tracks = []
    for joint_hash, samples in sorted(joint_data.items()):
        frames = []
        for index, (translation, scale, rotation) in enumerate(samples):
            q = rotations[rotation]
            frames.append(rs.AnimFrame(index / fps, (q.x, q.y, q.z, q.w),
                                       tuple(vectors[translation]), tuple(vectors[scale])))
        tracks.append(rs.AnimTrack(joint_hash, frames))
    rs.Anm.new(fps, tracks).to_path(str(filepath))


def materials_from_bin(document):
    materials = []
    for key, entry in document['entries'].items():
        if entry.get('__class__') != 0xff9d3409:
            continue
        samplers = []
        for sampler in entry.get(0x0a6f0eb5, []) or []:
            if not isinstance(sampler, dict):
                continue
            path = sampler.get(0xf0a363e3, '')
            if path:
                samplers.append({'name': sampler.get(0xb311d4ef) or sampler.get(0x02e7fb4c, ''),
                                 'texture': path})
        materials.append({'key': f'{key:08x}', 'name': entry.get(0x8d39bde6, ''),
                          'samplers': samplers})
    return {'materials': materials}


def textures_from_bin(document):
    result = {}
    entries = document['entries']
    for entry in entries.values():
        skin = entry.get(0x45ff5904)
        if not isinstance(skin, dict):
            continue
        if skin.get(0x3c6468f4):
            result['BASE'] = skin[0x3c6468f4]
        for override in skin.get(0x24725910, []) or []:
            if not isinstance(override, dict):
                continue
            name = override.get(0xaad7612c, '')
            texture = override.get(0x3c6468f4, '')
            if not texture:
                material = entries.get(override.get(0xd2e4d060), {})
                for sampler in material.get(0x0a6f0eb5, []) or []:
                    if isinstance(sampler, dict) and sampler.get(0xb311d4ef) == 'Diffuse_Texture':
                        texture = sampler.get(0xf0a363e3, '')
                        if texture:
                            break
            if name and texture:
                result[name] = texture
    return result
