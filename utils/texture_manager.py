import bpy
import os
import glob
from .backend import require_ritoshark
from .format_data import materials_from_bin, textures_from_bin
import numpy as np

def load_tex_image(path):
    texture = require_ritoshark().Tex.from_path(str(path))
    pixels = np.frombuffer(texture.rgba_f32, dtype='<f4')
    image = bpy.data.images.new(name=os.path.basename(path), width=texture.width,
                                height=texture.height, alpha=True)
    try:
        image.pixels.foreach_set(pixels)
        image['lol_source_path'] = str(path)
        image.pack()
    except Exception:
        bpy.data.images.remove(image)
        raise
    return image


def parse_materials_bin(bin_path):
    return materials_from_bin(require_ritoshark().read_bin(str(bin_path)))


def _detect_skin_folder_name(skn_path):
    """
    Detect the skin folder name from the SKN path.
    Returns the normalized skin folder name (e.g., 'skin0', 'skin3') or None if not detected.
    Handles both 'base' (maps to 'skin0') and 'skinX' folder names.
    Normalizes numbers: skin01 -> skin1, skin007 -> skin7
    """
    norm_path = os.path.normpath(skn_path)
    parts = norm_path.split(os.sep)

    # Look for skin folder patterns in the path
    for i, part in enumerate(parts):
        part_lower = part.lower()
        # Check for "base" folder (maps to skin0)
        if part_lower == 'base':
            # Verify this looks like a skin folder (parent might be "skins")
            if i > 0 and parts[i-1].lower() == 'skins':
                return 'skin0'
            # Or it's just named "base" as a skin folder
            return 'skin0'
        # Check for "skinX" pattern
        if part_lower.startswith('skin') and len(part_lower) > 4:
            # Verify remainder is a number
            remainder = part_lower[4:]
            if remainder.isdigit():
                # Normalize: skin01 -> skin1, skin007 -> skin7
                normalized_num = str(int(remainder))
                return f'skin{normalized_num}'

    return None

def find_bin_and_read(skn_path):
    # Try to find a bin file nearby
    start_folder = os.path.dirname(skn_path)
    norm_path = os.path.normpath(skn_path)
    path_sep = os.sep

    # Handle mixed separators
    if '/' in norm_path and '\\' in norm_path:
        norm_path = norm_path.replace('/', '\\')

    parts = norm_path.split(path_sep)

    # Detect skin folder name from path for later use
    target_skin = _detect_skin_folder_name(skn_path)
    if target_skin:
        print(f"Aventurine: Detected skin folder: {target_skin}")

    # Find "assets" and "characters" in the path
    assets_idx = None
    chars_idx = None
    for i, part in enumerate(parts):
        if part.lower() == 'assets':
            assets_idx = i
        if part.lower() == 'characters':
            chars_idx = i

    if assets_idx is not None and chars_idx is not None and chars_idx > assets_idx:
        # Base path is everything before "assets"
        base_path = path_sep.join(parts[:assets_idx])

        # Extract character name and skin folder
        # Path pattern: .../characters/{charname}/skins/{skinX}/file.skn
        if chars_idx + 3 < len(parts):
            char_name = parts[chars_idx + 1]
            skins_folder = parts[chars_idx + 2]
            skin_folder = parts[chars_idx + 3]

            if skins_folder.lower() == 'skins':
                # Map "base" to "skin0"
                if skin_folder.lower() == 'base':
                    skin_folder = 'skin0'
                else:
                    # Normalize: skin01 -> skin1, skin007 -> skin7
                    skin_folder_lower = skin_folder.lower()
                    if skin_folder_lower.startswith('skin') and len(skin_folder_lower) > 4:
                        remainder = skin_folder_lower[4:]
                        if remainder.isdigit():
                            skin_folder = f'skin{int(remainder)}'
                        else:
                            skin_folder = skin_folder_lower
                    else:
                        skin_folder = skin_folder_lower

                # Try exact match first
                bin_path = os.path.join(base_path, 'data', 'characters', char_name, 'skins', f'{skin_folder}.bin')
                if os.path.exists(bin_path):
                    print(f"Aventurine: Found BIN at structured path: {bin_path}")
                    return bin_path

                # Only fall back to skin0.bin if we're actually looking for skin0
                if skin_folder == 'skin0':
                    bin_path_skin0 = os.path.join(base_path, 'data', 'characters', char_name, 'skins', 'skin0.bin')
                    if os.path.exists(bin_path_skin0):
                        return bin_path_skin0

                # Try any skin*.bin in the skins folder, preferring the target skin
                skins_data_folder = os.path.join(base_path, 'data', 'characters', char_name, 'skins')
                if os.path.exists(skins_data_folder):
                    bins = glob.glob(os.path.join(skins_data_folder, 'skin*.bin'))
                    if bins:
                        # Sort to prefer the target skin
                        bins.sort(key=lambda x: 0 if skin_folder in os.path.basename(x).lower() else 1)
                        return bins[0]

    # Fallback: Search nearby folders
    search_folders = [start_folder]

    for folder_path in search_folders:
        folder = folder_path
        for _ in range(5):
            if not os.path.exists(folder):
                parent = os.path.dirname(folder)
                if parent == folder: break
                folder = parent
                continue

            bins = glob.glob(os.path.join(folder, "skin*.bin")) \
                 + glob.glob(os.path.join(folder, "skins", "skin*.bin"))

            # Sort to prefer the target skin we detected from the path
            if target_skin:
                bins.sort(key=lambda x: 0 if target_skin in os.path.basename(x).lower() else 1)
            else:
                # No target skin detected, fall back to skin0 as default
                bins.sort(key=lambda x: 0 if 'skin0' in os.path.basename(x).lower() else 1)

            if bins:
                print(f"Aventurine: Found BIN via fallback search: {bins[0]}")
                return bins[0]

            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent

    return None

def parse_bin_for_textures(bin_path):
    return textures_from_bin(require_ritoshark().read_bin(str(bin_path)))


def resolve_texture_path(skn_path, tex_asset_path):
    if not tex_asset_path: return None

    filename = os.path.basename(tex_asset_path)
    skn_dir = os.path.dirname(skn_path)

    # 1. Check same dir as SKN
    p = os.path.join(skn_dir, filename)
    if os.path.exists(p): return p

    # 2. Check recursive in skn_dir (useful if textures are in /Textures subfolder)
    # Limit depth to avoid infinite scan
    for root, dirs, files in os.walk(skn_dir):
        if filename in files:
            return os.path.join(root, filename)
        # Don't go too deep
        if root.count(os.sep) - skn_dir.count(os.sep) > 2:
            del dirs[:]

    # 3. Check up levels (parent folders)
    curr = skn_dir
    for _ in range(3):
        curr = os.path.dirname(curr)
        p = os.path.join(curr, filename)
        if os.path.exists(p): return p

    # 4. Try resolving the full asset path relative to the base folder
    # e.g., "ASSETS/Shared/Materials/UVAnimate/texture.tex" -> "{base}/assets/Shared/Materials/UVAnimate/texture.tex"
    tex_asset_normalized = tex_asset_path.replace('\\', '/').lower()
    if 'assets/' in tex_asset_normalized or tex_asset_normalized.startswith('assets'):
        # Find base path by going up from skn_path until we find "assets"
        norm_skn = os.path.normpath(skn_path)
        parts = norm_skn.split(os.sep)

        assets_idx = None
        for i, part in enumerate(parts):
            if part.lower() == 'assets':
                assets_idx = i
                break

        if assets_idx is not None:
            base_path = os.sep.join(parts[:assets_idx])

            # Normalize the asset path and construct full path
            # Handle both "ASSETS/..." and "assets/..." formats
            tex_parts = tex_asset_path.replace('\\', '/').split('/')
            # Find where "assets" starts in the texture path
            tex_assets_idx = None
            for i, part in enumerate(tex_parts):
                if part.lower() == 'assets':
                    tex_assets_idx = i
                    break

            if tex_assets_idx is not None:
                # Rebuild path from assets onwards, preserving original case in filesystem
                relative_path = '/'.join(tex_parts[tex_assets_idx:])
                full_path = os.path.join(base_path, relative_path.replace('/', os.sep))
                if os.path.exists(full_path):
                    return full_path

                # Try lowercase "assets" variant
                tex_parts_lower = tex_parts[tex_assets_idx:]
                tex_parts_lower[0] = 'assets'  # lowercase
                relative_path_lower = '/'.join(tex_parts_lower)
                full_path_lower = os.path.join(base_path, relative_path_lower.replace('/', os.sep))
                if os.path.exists(full_path_lower):
                    return full_path_lower

    return None

def import_textures(skn_object, skn_path):
    bin_path = find_bin_and_read(skn_path)
    tex_map = {}
    if bin_path:
        tex_map = parse_bin_for_textures(bin_path)

    # Debug: Show what the BIN parser found
    if tex_map:
        print(f"Aventurine: Found {len(tex_map)} texture mapping(s) in BIN:")
        for k, v in tex_map.items():
            print(f"  '{k}' -> {os.path.basename(v)}")

    # Map Blender materials to textures
    if not skn_object.data.materials:
        print("Aventurine: Object has no materials.")
        return

    # Cache for already loaded textures to avoid re-reading the same file
    loaded_textures = {}  # local_path -> bpy_image

    for mat in skn_object.data.materials:
        clean_name = mat.name.split('.')[0]
        tex_path_asset = tex_map.get(mat.name)
        if not tex_path_asset:
            tex_path_asset = tex_map.get(clean_name)

        if not tex_path_asset:
            tex_path_asset = tex_map.get('BASE')

        local_path = None
        if tex_path_asset:
            local_path = resolve_texture_path(skn_path, tex_path_asset)

        # Debug: Show material to texture mapping
        print(f"Aventurine: Material '{mat.name}' -> asset='{os.path.basename(tex_path_asset) if tex_path_asset else 'None'}' -> local='{local_path}'")
        
        # Fallback
        if not local_path and not tex_map:
             base_name = os.path.splitext(os.path.basename(skn_path))[0]
             potential_names = [f"{base_name}.tex", f"{base_name}.dds", f"{base_name}_TX_CM.tex", f"{base_name}_TX_CM.dds"]
             for name in potential_names:
                 local_path = resolve_texture_path(skn_path, name)
                 if local_path: break

        if local_path:
            # Check cache first
            if local_path in loaded_textures:
                print(f"Aventurine:   -> Using cached texture")
                bpy_image = loaded_textures[local_path]
                # Assign cached texture to this material
                if bpy_image and hasattr(mat, "node_tree") and mat.node_tree:
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links
                    
                    bsdf = None
                    for n in nodes:
                        if n.type == 'BSDF_PRINCIPLED':
                            bsdf = n
                            break
                    
                    if not bsdf:
                        nodes.clear()
                        output = nodes.new('ShaderNodeOutputMaterial')
                        output.location = (200, 0)
                        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
                        bsdf.location = (0, 0)
                        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

                    tex_node = nodes.new('ShaderNodeTexImage')
                    tex_node.location = (-300, 0)
                    tex_node.image = bpy_image

                    if not bsdf.inputs['Base Color'].is_linked:
                        links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                    if not bsdf.inputs['Alpha'].is_linked:
                        links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
                continue
            
            bpy_image = None
            temp_img = None
            try:
                if local_path.lower().endswith('.tex'):
                    bpy_image = load_tex_image(local_path)
                else:
                    temp_img = bpy.data.images.load(local_path, check_existing=False)
                    width, height = temp_img.size
                    pixels = np.empty(width * height * 4, dtype=np.float32)
                    temp_img.pixels.foreach_get(pixels)
                    bpy_image = bpy.data.images.new(
                        name=os.path.basename(local_path).rsplit('.', 1)[0],
                        width=width, height=height, alpha=True,
                    )
                    bpy_image.pixels.foreach_set(pixels)
                    bpy_image['lol_source_path'] = local_path
                    bpy_image.pack()
            except Exception as error:
                print(f'Aventurine: Failed to load texture: {error}')
                if bpy_image is not None:
                    bpy.data.images.remove(bpy_image)
                    bpy_image = None
            finally:
                if temp_img is not None:
                    bpy.data.images.remove(temp_img)

            if bpy_image:
                # Add to cache for reuse
                loaded_textures[local_path] = bpy_image
                
                if hasattr(mat, "node_tree") and mat.node_tree:
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links
                    
                    bsdf = None
                    for n in nodes:
                        if n.type == 'BSDF_PRINCIPLED':
                            bsdf = n
                            break
                    
                    if not bsdf:
                        nodes.clear()
                        output = nodes.new('ShaderNodeOutputMaterial')
                        output.location = (200, 0)
                        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
                        bsdf.location = (0, 0)
                        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

                    tex_node = nodes.new('ShaderNodeTexImage')
                    tex_node.location = (-300, 0)
                    tex_node.image = bpy_image

                    if not bsdf.inputs['Base Color'].is_linked:
                        links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                    if not bsdf.inputs['Alpha'].is_linked:
                        links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
