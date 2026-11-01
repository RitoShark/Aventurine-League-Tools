"""SKN material order, independent of Blender object selection and slot order."""
import json

ORDER = 'lol_skn_material_order'
SOURCE = 'lol_skn_source_material_order'


def unique(names):
    result = []
    seen = set()
    for name in names:
        name = str(name)
        if name.casefold() not in seen:
            result.append(name)
            seen.add(name.casefold())
    return result


def read(owner, key):
    if owner is None or key not in owner:
        return []
    try:
        value = json.loads(owner[key])
        if not isinstance(value, list) or any(not isinstance(n, str) for n in value):
            raise ValueError()
        return unique(value)
    except (TypeError, ValueError):
        raise ValueError(f'{owner.name}: invalid saved SKN material order')


def remember_source(obj, arm, names):
    text = json.dumps(unique(names), ensure_ascii=False)
    obj[SOURCE] = text
    # Adding a replacement mesh to an existing armature must not overwrite its
    # source order or the artist's explicit ordering.
    if arm is not None and SOURCE not in arm:
        arm[SOURCE] = text


def material_name(obj, index):
    material = obj.data.materials[index] if index is not None and index < len(obj.data.materials) else None
    return str(material.get('lol_material_name', material.name)) if material else obj.name


def names_in_meshes(meshes):
    names = []
    for obj in meshes:
        if obj.type != 'MESH' or not obj.data.polygons:
            continue
        if obj.data.materials:
            used = {p.material_index for p in obj.data.polygons}
            names.extend(material_name(obj, i) for i in range(len(obj.data.materials)) if i in used)
        else:
            names.append(obj.name)
    return unique(names)


def preference(arm, meshes):
    names = read(arm, ORDER) + read(arm, SOURCE)
    for obj in meshes:
        names.extend(read(obj, SOURCE))
    return unique(names)


def current_order(arm, meshes):
    meshes = list(meshes)
    names = names_in_meshes(meshes)
    ranks = {name.casefold(): i for i, name in enumerate(preference(arm, meshes))}
    return sorted(names, key=lambda name: ranks.get(name.casefold(), len(ranks)))


def set_order(arm, names):
    arm[ORDER] = json.dumps(unique(names), ensure_ascii=False)


def reorder_submeshes(submeshes, arm, meshes):
    """Repack the vertex/index ranges along with their submesh records.

    write_skin flattens buffers in list order, so sorting only the table would
    point indices at the wrong vertices. All vertex fields travel together.
    Duplicate material names form one ordering group, retaining their input order.
    """
    ranks = {name.casefold(): i for i, name in enumerate(preference(arm, meshes))}
    result = sorted(submeshes, key=lambda sm: ranks.get(sm.get('order_name', sm['name']).casefold(), len(ranks)))
    vertex_start = index_start = 0
    for sm in result:
        old_start = sm['vertex_start']
        sm['indices'] = [i - old_start + vertex_start for i in sm['indices']]
        sm['vertex_start'] = vertex_start
        sm['index_start'] = index_start
        vertex_start += sm['vertex_count']
        index_start += sm['index_count']
    return result
