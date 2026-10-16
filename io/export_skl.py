import bpy
import mathutils
import os
from ..utils.backend import require_ritoshark
from . import import_skl
from . import bone_utils

def write_skl(filepath, armature_obj, disable_scaling=False, disable_transforms=False, use_visual_pose=False,
              clean_names=True, apply_object_transform=True, model_scale=1.0, influences=None):
    """Write Blender armature to SKL file (Version 0)

    influences: the bone palette the paired SKN's vertex bytes index into, as a
        list of joint indices (palette index -> joint index).  export_skn passes
        the mesh's used-bone palette so skeletons with more than 256 joints stay
        exportable (only the bones a mesh weights count against the 256 cap).
        When None, preserve the imported or last exported skinning palette.
        Newly authored rigs without a stored palette use identity. The format
        supports 32768 joints; SKN vertex palette slots remain uint8.
    """

    from .skeleton import ExportPlan, has_offset_parent
    plan = ExportPlan(armature_obj, clean_names)
    if influences is None:
        influences = plan.skin_palette()
    globals = plan.bind_globals(use_visual_pose or has_offset_parent(armature_obj),
                               disable_transforms=disable_transforms,
                               apply_object_transform=apply_object_transform)
    locals = plan.locals(globals)
    rs = require_ritoshark()
    scale = (1.0 if disable_scaling else import_skl.EXPORT_SCALE) * model_scale
    if scale <= 0:
        raise ValueError('Model scale must be positive')
    joints = []
    for pb in plan.bones:
        t, r, s = locals[pb.name].decompose()
        it, ir, iss = globals[pb.name].inverted().decompose()
        joints.append(rs.Joint(
            plan.names[pb.name], plan.ids[pb.name],
            plan.ids[pb.parent.name] if pb.parent else -1,
            pb.get('lol_joint_radius', 2.1), tuple(t * scale), tuple(s),
            (r.x, r.y, r.z, r.w), tuple(it * scale), tuple(iss),
            (ir.x, ir.y, ir.z, ir.w), flags=pb.get('lol_joint_flags', 0)))
    rs.Skl.new(joints, influences,
               name=armature_obj.get('lol_skl_name', ''),
               asset=armature_obj.get('lol_skl_asset', ''),
               flags=armature_obj.get('lol_skl_flags', 0)).to_path(str(filepath))
    plan.remember_skin_palette(influences)

    return True

def save(operator, context, filepath, target_armature=None, disable_scaling=False, disable_transforms=False, use_visual_pose=False, clean_names=True, apply_object_transform=True, model_scale=1.0):
    armature_obj = target_armature

    if not armature_obj:
        armature_obj = context.active_object
        if not armature_obj or armature_obj.type != 'ARMATURE':
            # Try to find an armature in the scene
            armature_obj = next((obj for obj in context.scene.objects if obj.type == 'ARMATURE'), None)

    if not armature_obj:
        operator.report({'ERROR'}, "No Armature found in scene")
        return {'CANCELLED'}

    try:
        write_skl(filepath, armature_obj, disable_scaling, disable_transforms, use_visual_pose,
                  clean_names=clean_names, apply_object_transform=apply_object_transform, model_scale=model_scale)
        operator.report({'INFO'}, f"Exported SKL: {filepath}")
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
