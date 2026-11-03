"""SKL name recovery and transactional Blender armature import checks."""
import struct
import types
from unittest.mock import patch

from test_backend import bpy, rs, import_skl, import_skn, export_skl


class SkeletonImportChecks:
    def padded_name_fixture(self, index=0, wrong_hash=False):
        path = self.skeleton()
        raw = bytearray(path.read_bytes())
        start = struct.unpack_from('<i', raw, 20)[0] + index * 100
        field = start + 96
        offset = struct.unpack_from('<i', raw, field)[0]
        self.assertEqual(raw[field + offset - 2:field + offset], b'\0\0')
        struct.pack_into('<i', raw, field, offset - 2)
        if wrong_hash:
            struct.pack_into('<I', raw, start + 8, 0x12345678)
        path.write_bytes(raw)
        self.skin().to_path(str(path.with_suffix('.skn')))
        return path

    def test_recovered_first_joint_imports_bound_mesh_and_exports_real_name(self):
        for orientation in ('VISUAL', 'NATIVE'):
            with self.subTest(orientation=orientation):
                path = self.padded_name_fixture()
                before = path.read_bytes()
                messages = []
                operator = types.SimpleNamespace(report=lambda levels, text: messages.append((levels, text)))
                self.assertEqual(import_skn.load(operator, bpy.context, str(path.with_suffix('.skn')),
                    split_by_material=True, auto_load_textures=False, bone_orient=orientation), {'FINISHED'}, messages)
                arm = bpy.context.scene.objects.get('Armature')
                self.assertIsNotNone(arm)
                self.assertEqual(arm.mode, 'OBJECT')
                self.assertEqual(arm.data.bones['child'].parent.name, 'root')
                self.assertEqual(arm.pose.bones['root']['native_source_name'], 'root')
                mesh = next(o for o in bpy.context.scene.objects if o.type == 'MESH')
                self.assertAlmostEqual(mesh.vertex_groups['child'].weight(0), 1)
                self.assertEqual(mesh.find_armature(), arm)
                self.assertFalse(any(levels == {'WARNING'} for levels, _ in messages))
                export = self.directory / 'recovered.skl'
                export_skl.write_skl(export, arm)
                exported = rs.Skl.from_path(str(export))
                self.assertEqual([j.name for j in exported.joints], ['root', 'child'])
                self.assertEqual(list(exported.influences), [1, 0])
                self.assertEqual(before, path.read_bytes())
                bpy.ops.object.select_all(action='SELECT')
                bpy.ops.object.delete(use_global=False)

    def test_unrecoverable_name_cancels_paired_import_without_scene_changes(self):
        bpy.ops.mesh.primitive_cube_add()
        previous = bpy.context.active_object
        for index, wrong_hash in ((0, True), (1, False)):
            with self.subTest(index=index):
                path = self.padded_name_fixture(index, wrong_hash)
                objects = set(bpy.data.objects)
                armatures = set(bpy.data.armatures)
                with self.assertRaisesRegex(ValueError, f'joint {index} .*empty name'):
                    import_skl.read_skl(path)
                messages = []
                operator = types.SimpleNamespace(report=lambda levels, text: messages.append((levels, text)))
                self.assertEqual(import_skn.load(operator, bpy.context, str(path.with_suffix('.skn')),
                    split_by_material=True, auto_load_textures=False), {'CANCELLED'})
                self.assertEqual(set(bpy.data.objects), objects)
                self.assertEqual(set(bpy.data.armatures), armatures)
                self.assertEqual(bpy.context.active_object, previous)
                self.assertEqual(bpy.context.mode, 'OBJECT')
                self.assertTrue(any(levels == {'ERROR'} and 'empty name' in text for levels, text in messages))
                self.assertFalse(any('context is incorrect' in text for _, text in messages))

    def test_armature_build_failure_restores_existing_edit_mode(self):
        bpy.ops.mesh.primitive_cube_add()
        previous = bpy.context.active_object
        for orientation in ('VISUAL', 'NATIVE'):
            with self.subTest(orientation=orientation):
                joints, _ = import_skl.read_skl(self.skeleton())
                # Simulate a failure after reading, while the new rig is in Edit Mode.
                joints[1].name = ''
                objects = set(bpy.data.objects)
                armatures = set(bpy.data.armatures)
                if previous.mode == 'OBJECT':
                    bpy.ops.object.mode_set(mode='EDIT')
                with self.assertRaises(KeyError):
                    import_skl.create_armature(joints, bone_orient=orientation)
                self.assertEqual(set(bpy.data.objects), objects)
                self.assertEqual(set(bpy.data.armatures), armatures)
                self.assertEqual(bpy.context.active_object, previous)
                self.assertEqual(bpy.context.mode, 'EDIT_MESH')
                self.assertEqual(list(bpy.context.selected_objects), [previous])
                self.assertEqual(len(previous.data.vertices), 8)

    def test_armature_metadata_failure_restores_existing_pose_mode(self):
        joints, _ = import_skl.read_skl(self.skeleton())
        previous = import_skl.create_armature(joints)
        bpy.ops.object.mode_set(mode='POSE')
        objects = set(bpy.data.objects)
        armatures = set(bpy.data.armatures)
        for orientation in ('VISUAL', 'NATIVE'):
            with self.subTest(orientation=orientation):
                with patch('aventurine.io.import_skl._store_native_props', side_effect=RuntimeError('metadata failure')):
                    with self.assertRaisesRegex(RuntimeError, 'metadata failure'):
                        import_skl.create_armature(joints, bone_orient=orientation)
                self.assertEqual(set(bpy.data.objects), objects)
                self.assertEqual(set(bpy.data.armatures), armatures)
                self.assertEqual(bpy.context.active_object, previous)
                self.assertEqual(bpy.context.mode, 'POSE')
                self.assertEqual(list(bpy.context.selected_objects), [previous])

    def test_new_armature_does_not_join_existing_armature_edit_session(self):
        joints, _ = import_skl.read_skl(self.skeleton())
        previous = import_skl.create_armature(joints)
        before = [(b.name, b.matrix_local.copy()) for b in previous.data.bones]
        bpy.ops.object.mode_set(mode='EDIT')
        result = import_skl.create_armature(joints)
        self.assertNotEqual(result, previous)
        self.assertEqual([(b.name, b.matrix_local) for b in previous.data.bones], before)
        self.assertEqual(bpy.context.mode, 'OBJECT')
        self.assertEqual(bpy.context.active_object, result)
        self.assertEqual(list(bpy.context.selected_objects), [result])

    def test_missing_optional_skl_still_imports_mesh(self):
        path = self.directory / 'without-skeleton.skn'
        self.skin().to_path(str(path))
        operator = types.SimpleNamespace(report=lambda *args: None)
        self.assertEqual(import_skn.load(operator, bpy.context, str(path),
            split_by_material=True, auto_load_textures=False), {'FINISHED'})
        self.assertEqual(len([o for o in bpy.context.scene.objects if o.type == 'MESH']), 1)
        self.assertFalse(any(o.type == 'ARMATURE' for o in bpy.context.scene.objects))
