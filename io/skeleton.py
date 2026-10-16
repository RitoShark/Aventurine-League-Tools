"""One joint identity, name and coordinate frame shared by SKL, SKN and ANM."""
import json
import bpy
from mathutils import Matrix
from . import bone_utils
from ..utils.binary_utils import Hash

P = Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def stored_matrix(pb, key):
    values = pb.get(key)
    return Matrix([values[i:i+4] for i in range(0, 16, 4)]) if values is not None and len(values) == 16 else None


def native_owners(arm):
    registry = json.loads(arm.get('lol_source_joints', '{}'))
    groups = {}
    for pb in arm.pose.bones:
        idx = pb.get('native_bone_index')
        if idx is not None:
            groups.setdefault(int(idx), []).append(pb)
    result = {}
    for idx, group in groups.items():
        original = registry.get(str(idx))
        matching = [pb for pb in group if pb.name == original] if original else []
        if not matching and len(group) == 1:
            matching = group
        if not matching and not original:
            matching = [pb for pb in group if pb.name == pb.get('native_source_name')]
            if not matching:
                # Legacy .blend files did not record original names.
                matching = [pb for pb in group if bone_utils.clean_export_bone_name(pb.name) == pb.name]
        if len(matching) != 1:
            raise ValueError(f'Ambiguous original joint {idx}: {", ".join(pb.name for pb in group)}. '
                             'Reimport the source SKL or clear native properties from the added duplicate.')
        result[idx] = matching[0]
    return result


def has_offset_parent(arm):
    return any(pb.get('lol_offset_parent_for') for pb in arm.pose.bones)


class ExportPlan:
    def __init__(self, arm, clean_names=True):
        self.arm = arm
        self.owners = native_owners(arm)
        self.native = {pb.name for pb in self.owners.values()}
        self.bones = [self.owners[i] for i in sorted(self.owners)]
        self.bones += [pb for pb in arm.pose.bones if pb.name not in self.native]
        self.ids = {pb.name: i for i, pb in enumerate(self.bones)}
        self.names = {}
        hashes = {}
        self.corrections = {}
        for pb in self.bones:
            name = bone_utils.sanitize_illegal_chars(pb.name) if clean_names else pb.name
            if not name or (clean_names and name[0].isdigit()):
                raise ValueError(f'Joint name must start with a letter or underscore: {pb.name!r}')
            h = Hash.elf(name)
            if h in hashes:
                raise ValueError(f'Joint names {hashes[h]!r} and {pb.name!r} have the same exported hash')
            hashes[h] = pb.name
            self.names[pb.name] = name
            k = Matrix.Identity(4)
            if pb.name in self.native:
                v = stored_matrix(pb, 'native_matrix_local')
                n = stored_matrix(pb, 'native_global_rest_mat')
                if v is not None and n is not None:
                    k = v.inverted() @ n
            target = pb.get('lol_offset_parent_for')
            if target:
                child = arm.pose.bones.get(target)
                if child is None or child.parent != pb:
                    raise ValueError(f'Offset parent {pb.name!r} must directly parent {target!r}')
                k = stored_matrix(pb, 'lol_offset_correction')
                if k is None:
                    raise ValueError(f'Offset parent {pb.name!r} has no neutral export frame')
            self.corrections[pb.name] = k

    def skin_palette(self):
        """Resolve slots of the imported or most recently exported SKN/SKL pair.

        Native identities survive Blender renames and joint reindexing. Added
        bones are tracked by name; a missing reference requires a paired export.
        """
        stored = self.arm.get('lol_skin_palette')
        if stored is None and self.owners:
            # Older .blend files did not store the palette. Recover it only from
            # a source SKL whose joint identities match this imported armature.
            from pathlib import Path
            from ..utils.backend import require_ritoshark
            path = self.arm.get('lol_skl_filepath', '')
            if not path or not Path(bpy.path.abspath(path)).is_file():
                raise ValueError('Skinning palette is missing. Reimport the source SKL or export SKN and SKL together.')
            source = require_ritoshark().Skl.from_path(bpy.path.abspath(path))
            registry = json.loads(self.arm.get('lol_source_joints', '{}'))
            for idx, pb in self.owners.items():
                name = registry.get(str(idx), pb.get('native_source_name', pb.name))
                if idx >= len(source.joints) or source.joints[idx].name != name:
                    raise ValueError('Source SKL no longer matches this rig. Reimport it or export SKN and SKL together.')
            index_by_id = {joint.id: i for i, joint in enumerate(source.joints)}
            refs = [{'native': index_by_id[i]} for i in source.influences]
        elif stored is None:
            return list(range(len(self.bones)))
        else:
            refs = json.loads(stored)
        result = []
        for ref in refs:
            pb = self.owners.get(ref['native']) if 'native' in ref else self.arm.pose.bones.get(ref['bone'])
            if pb is None:
                raise ValueError('A bone referenced by the skinning palette was removed or renamed. Export SKN and SKL together.')
            result.append(self.ids[pb.name])
        return result

    def remember_skin_palette(self, influences):
        native_ids = {pb.name: idx for idx, pb in self.owners.items()}
        refs = []
        for i in influences:
            name = self.bones[i].name
            refs.append({'native': native_ids[name]} if name in native_ids else {'bone': name})
        self.arm['lol_skin_palette'] = json.dumps(refs)

    def globals(self, pose=False, disable_transforms=False, apply_object_transform=True):
        # The same K is used for bind and animation, including visual-pose exports.
        # Choosing a different bind pose must not also change animation axes.
        obj = self.arm.matrix_world if apply_object_transform else Matrix.Identity(4)
        p = Matrix.Identity(4) if disable_transforms else P
        pi = p.inverted()
        return {pb.name: pi @ obj @ (pb.matrix if pose else pb.bone.matrix_local)
                @ self.corrections[pb.name] @ p for pb in self.bones}

    def locals(self, globals):
        return {pb.name: globals[pb.parent.name].inverted() @ globals[pb.name]
                if pb.parent else globals[pb.name] for pb in self.bones}

    def validate_offset_bind(self):
        if not has_offset_parent(self.arm):
            return
        scene = bpy.context.scene
        frame, subframe = scene.frame_current, scene.frame_subframe
        try:
            scene.frame_set(0)
            bpy.context.view_layer.update()
            for pb in self.bones:
                if pb.get('lol_offset_parent_for') and max(
                        abs(pb.matrix_basis[r][c] - (1 if r == c else 0))
                        for r in range(4) for c in range(4)) > 1e-4:
                    raise ValueError(f'{pb.name}: use Apply Offset to Bind Pose before export '
                                     'so the mesh and skeleton share the new bind pose')
        finally:
            scene.frame_set(frame, subframe=subframe)

    def bind_globals(self, visual=False, **kwargs):
        self.validate_offset_bind()
        if not visual:
            return self.globals(**kwargs)
        scene = bpy.context.scene
        frame, subframe = scene.frame_current, scene.frame_subframe
        try:
            scene.frame_set(0)
            bpy.context.view_layer.update()
            return self.globals(pose=True, **kwargs)
        finally:
            scene.frame_set(frame, subframe=subframe)