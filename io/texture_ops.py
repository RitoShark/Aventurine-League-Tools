import bpy
import os
import numpy as np
from ..utils.texture_manager import load_tex_image


class LOL_OT_ReloadTextures(bpy.types.Operator):
    """Reloads all textures from their source TEX/DDS files"""
    bl_idname = "lol.reload_textures"
    bl_label = "Reload Textures"

    def execute(self, context):
        count = 0

        for img in list(bpy.data.images):
            source_path = img.get("lol_source_path")
            if not source_path or not os.path.exists(source_path):
                continue

            temp_img = None
            try:
                if source_path.lower().endswith('.tex'):
                    temp_img = load_tex_image(source_path)
                else:
                    temp_img = bpy.data.images.load(source_path, check_existing=False)

                # Get pixels and update existing image using fast numpy transfer
                width, height = temp_img.size

                if img.size[0] != width or img.size[1] != height:
                    img.scale(width, height)

                # Fast pixel transfer using foreach_get/foreach_set
                pixel_count = width * height * 4
                pixels = np.empty(pixel_count, dtype=np.float32)
                temp_img.pixels.foreach_get(pixels)
                img.pixels.foreach_set(pixels)

                img.pack()
                count += 1

            except Exception as e:
                print(f"Aventurine: Failed to reload {img.name}: {e}")
            finally:
                if temp_img is not None:
                    bpy.data.images.remove(temp_img)

        self.report({'INFO'}, f"Reloaded {count} textures")
        return {'FINISHED'}
