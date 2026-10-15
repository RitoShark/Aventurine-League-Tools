import bpy
import os
import glob
import re
from pathlib import Path
from .backend import require_ritoshark
from .format_data import materials_from_bin, textures_from_bin
import numpy as np

def load_tex_image(path):
    rs = require_ritoshark()
    if str(path).lower().endswith('.dds'):
        texture = rs.Dds.from_path(str(path))
        pixels = (np.frombuffer(texture.rgba, dtype=np.uint8)
                  .reshape(texture.height, texture.width, 4)[::-1].astype(np.float32) / 255).ravel()
    else:
        texture = rs.Tex.from_path(str(path))
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


def _resolve_asset_path(source_path, asset_path):
    """Resolve extracted game paths before trying ambiguous basename fallbacks."""
    relative = Path(str(asset_path).replace('\\', '/'))
    if relative.is_absolute() or relative.drive or '..' in relative.parts:
        return None
    for parent in Path(source_path).resolve().parents:
        candidate = parent / relative
        if candidate.is_file():
            return str(candidate)
    return None


def read_bin_with_links(bin_path, issues=None):
    """Merge local dependencies first; the requested BIN owns conflicting keys."""
    documents = {}
    visiting = set()

    def read(path):
        path = Path(path).resolve()
        if path in visiting:
            return {}
        if path in documents:
            return documents[path]
        visiting.add(path)
        document = require_ritoshark().read_bin(str(path))
        entries = {}
        for linked in document.get('linked', []):
            resolved = _resolve_asset_path(path, linked)
            if resolved is None:
                message = f'Missing linked BIN: {linked}'
                print(f'Aventurine: {message}')
                if issues is not None:
                    issues.append(message)
                continue
            entries.update(read(resolved))
        entries.update(document.get('entries', {}))
        visiting.remove(path)
        documents[path] = entries
        return entries

    return {'entries': read(bin_path)}


class _TextureReferences:
    """Resolve BIN `file` hashes using paths in this extraction only.

    BIN file references and WAD paths use the same lowercase XXH64 hash.
    Build the index lazily, once per BIN import; do not read texture contents.
    """
    EXTENSIONS = {'.tex', '.dds', '.png', '.tga', '.jpg', '.jpeg'}

    def __init__(self, source_path, issues=None):
        self.issues = issues
        self.source = Path(source_path).resolve()
        self.index = None

    def _build_index(self):
        root = self.source.parent
        assets = None
        for parent in self.source.parents:
            if parent.name.casefold() in ('assets', 'data'):
                root = parent.parent
                assets = next((p for p in root.iterdir()
                               if p.is_dir() and p.name.casefold() == 'assets'), None)
                break
        # Flat extracts can contain hash-named files. Never recursively search
        # ancestors such as Documents or neighboring champion exports.
        paths = assets.rglob('*') if assets else root.iterdir()
        files = sorted(p for p in paths if p.is_file() and p.suffix.lower() in self.EXTENSIONS)
        index = {}
        hash_path = require_ritoshark().wad_hash
        for path in files:
            relative = path.relative_to(root).as_posix()
            index[hash_path(relative)] = relative
            if re.fullmatch(r'[0-9a-fA-F]{16}', path.stem):
                index.setdefault(int(path.stem, 16), relative)
        # Extraction tools may convert a referenced TEX to DDS/PNG. Exact path
        # matches win; an alias is used only when there is one candidate.
        aliases = {}
        for path in files:
            relative = path.relative_to(root)
            for extension in ('.tex', '.dds'):
                hashed = hash_path(relative.with_suffix(extension).as_posix())
                aliases.setdefault(hashed, set()).add(relative.as_posix())
        for hashed, candidates in aliases.items():
            if len(candidates) == 1:
                index.setdefault(hashed, next(iter(candidates)))
        self.index = index

    def resolve(self, reference):
        if isinstance(reference, str):
            return reference
        if type(reference) is not int or not 0 < reference < 2**64:
            return None
        if self.index is None:
            self._build_index()
        path = self.index.get(reference)
        if path is None:
            message = f'Unresolved BIN texture hash: {reference:016x}'
            print(f'Aventurine: {message}')
            if self.issues is not None:
                self.issues.append(message)
        return path


def parse_materials_bin(bin_path):
    document = materials_from_bin(read_bin_with_links(bin_path))
    references = _TextureReferences(bin_path)
    for material in document['materials']:
        for sampler in material['samplers']:
            sampler['texture'] = references.resolve(sampler['texture'])
    return document


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
                        bins = [x for x in bins if Path(x).stem.lower() == skin_folder]
                        if bins:
                            return sorted(bins)[0]

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
                bins = sorted(x for x in bins if Path(x).stem.lower() == target_skin)
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

def parse_bin_for_textures(bin_path, issues=None):
    references = _TextureReferences(bin_path, issues)
    return {name: references.resolve(reference)
            for name, reference in textures_from_bin(read_bin_with_links(bin_path, issues)).items()}


def resolve_texture_path(skn_path, tex_asset_path):
    if type(tex_asset_path) is int:
        tex_asset_path = _TextureReferences(skn_path).resolve(tex_asset_path)
    if not tex_asset_path: return None

    exact = _resolve_asset_path(skn_path, tex_asset_path)
    if exact:
        return exact
    filename = os.path.basename(tex_asset_path.replace('\\', '/'))
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
    issues = []
    bin_path = find_bin_and_read(skn_path)
    tex_map = {}
    if bin_path:
        tex_map = parse_bin_for_textures(bin_path, issues)
        if not tex_map:
            issues.append('BIN has no diffuse texture mappings; trying nearby textures')
    else:
        issues.append('No skin BIN found; trying nearby textures')

    # Debug: Show what the BIN parser found
    if tex_map:
        print(f"Aventurine: Found {len(tex_map)} texture mapping(s) in BIN:")
        for k, v in tex_map.items():
            print(f"  '{k}' -> {os.path.basename(v) if v else 'unresolved texture reference'}")

    # Map Blender materials to textures
    if not skn_object.data.materials:
        issues.append("Mesh has no materials for texture assignment")
        return issues

    # Cache for already loaded textures to avoid re-reading the same file
    loaded_textures = {}  # local_path -> bpy_image

    for mat in skn_object.data.materials:
        if mat is None:
            continue
        clean_name = mat.get('lol_material_name', re.sub(r'\.\d{3}$', '', mat.name))
        # An unresolved explicit override must not silently use the body texture.
        if clean_name in tex_map:
            tex_path_asset = tex_map[clean_name]
        elif mat.name in tex_map:
            tex_path_asset = tex_map[mat.name]
        else:
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

        if not local_path:
            issues.append(f'Missing texture for {clean_name}: {tex_path_asset or "no resolved BIN reference"}')

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
                if local_path.lower().endswith(('.tex', '.dds')):
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
                message = f'Texture decode failed for {os.path.basename(local_path)}: {error}'
                issues.append(message)
                print(f'Aventurine: {message}')
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

    return list(dict.fromkeys(issues))


def report_texture_issues(operator, issues):
    """Keep the import usable while making incomplete texture loading visible."""
    issues = list(dict.fromkeys(issues))
    if issues:
        for issue in issues:
            print(f'Aventurine: {issue}')
        summary = '; '.join(issues[:3])
        if len(issues) > 3:
            summary += f'; {len(issues) - 3} more details in the console'
        operator.report({'WARNING'}, summary)
