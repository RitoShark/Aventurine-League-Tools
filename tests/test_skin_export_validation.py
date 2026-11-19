"""SKN combinations must preserve metadata and never silently bind to root."""
import base64
import struct
from types import SimpleNamespace

from test_backend import bpy, rs, import_skn, import_skl, export_skn


class SkinExportValidationChecks:
    def _weight_fixture(self):
        joints, palette = import_skl.read_skl(self.skeleton())
        arm = import_skl.create_armature(joints, name='Target', bone_orient='NATIVE')
        path = self.directory / 'weight-source.skn'
        self.skin().to_path(str(path))
        obj = import_skn.create_mesh(*import_skn.read_skn(path), 'Body', arm, joints, palette)
        bpy.context.view_layer.update()
        return arm, obj

    def _mesh_copy(self, obj):
        copied = obj.copy()
        copied.data = obj.data.copy()
        bpy.context.scene.collection.objects.link(copied)
        return copied

    def _select_for_skin_export(self, *objects):
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]

    def test_skn_empty_and_zero_end_tabs_combine_in_both_orders(self):
        arm, obj = self._weight_fixture()
        other = self._mesh_copy(obj)
        obj['lol_skn_trailing'] = ''
        original_data = [obj.data, other.data]
        for objects in ([obj, other], [other, obj]):
            path = self.directory / 'mixed-tails.skn'
            export_skn.write_skn_multi(path, objects, arm)
            result = rs.Skn.from_path(str(path))
            self.assertEqual(result.trailing, bytes(12))
            self.assertEqual(result.vertex_count, 6)
            self.assertEqual(len(result.indices) // 4, 6)
            self.assertEqual(result.flags, 0)
            self.assertEqual(result.prefix_block, b'')
            self.assertEqual([obj.data, other.data], original_data)
            self.assertEqual(obj['lol_skn_trailing'], '')
            self.assertEqual(base64.b64decode(other['lol_skn_trailing']), bytes(12))

    def test_skn_identical_end_tabs_remain_byte_exact(self):
        arm, obj = self._weight_fixture()
        other = self._mesh_copy(obj)
        for tail in (b'', bytes(12), b'opaque-tail'):
            with self.subTest(tail=tail):
                for mesh in (obj, other):
                    mesh['lol_skn_trailing'] = base64.b64encode(tail).decode('ascii')
                path = self.directory / 'identical-tails.skn'
                export_skn.write_skn_multi(path, [obj, other], arm)
                self.assertEqual(rs.Skn.from_path(str(path)).trailing, tail)

    def test_skn_custom_metadata_conflicts_still_reject_before_writing(self):
        arm, obj = self._weight_fixture()
        other = self._mesh_copy(obj)
        original = {key: other[key] for key in ('lol_skn_flags', 'lol_skn_prefix', 'lol_skn_trailing')}
        cases = [('lol_skn_flags', 4),
                 ('lol_skn_prefix', base64.b64encode(b'opaque').decode('ascii')),
                 ('lol_skn_trailing', base64.b64encode(bytes(24)).decode('ascii')),
                 ('lol_skn_trailing', base64.b64encode(bytes(11) + b'\x01').decode('ascii'))]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                for prop, previous in original.items():
                    other[prop] = previous
                other[key] = value
                path = self.directory / 'conflicting.skn'
                with self.assertRaisesRegex(ValueError, 'incompatible SKN file metadata'):
                    export_skn.write_skn_multi(path, [obj, other], arm)
                self.assertFalse(path.exists())

    def test_skn_missing_bone_weights_block_pair_without_overwriting(self):
        arm, obj = self._weight_fixture()
        obj.vertex_groups['child'].name = 'Scarf_Missing'
        self._select_for_skin_export(obj)
        original_mesh = obj.data
        mesh_count = len(bpy.data.meshes)
        path = self.directory / 'existing.skn'
        path.write_bytes(b'original mesh')
        path.with_suffix('.skl').write_bytes(b'original skeleton')
        messages = []
        operator = SimpleNamespace(report=lambda level, message: messages.append((level, message)))
        result = export_skn.save(operator, bpy.context, str(path), target_armature=arm)
        self.assertEqual(result, {'CANCELLED'})
        error = next(message for level, message in messages if level == {'ERROR'})
        for text in ('3 exported vertices', 'Scarf_Missing', arm.name, 'reweight'):
            self.assertIn(text, error)
        self.assertEqual(path.read_bytes(), b'original mesh')
        self.assertEqual(path.with_suffix('.skl').read_bytes(), b'original skeleton')
        self.assertEqual(obj.data, original_mesh)
        self.assertEqual(len(bpy.data.meshes), mesh_count)

    def test_skn_join_does_not_hide_missing_bone_weights(self):
        arm, obj = self._weight_fixture()
        scarf = self._mesh_copy(obj)
        scarf.vertex_groups['child'].name = 'Scarf_Missing'
        self._select_for_skin_export(obj, scarf)
        bpy.ops.object.join()
        path = self.directory / 'joined.skn'
        with self.assertRaisesRegex(ValueError, '3 exported vertices.*Scarf_Missing'):
            export_skn.write_skn_multi(path, [obj], arm)
        self.assertFalse(path.exists())

    def test_skn_reweighting_missing_groups_allows_export(self):
        arm, obj = self._weight_fixture()
        obj.vertex_groups['child'].name = 'Scarf_Missing'
        obj.vertex_groups['root'].add(list(range(3)), 1, 'REPLACE')
        obj.vertex_groups.remove(obj.vertex_groups['Scarf_Missing'])
        path = self.directory / 'reweighted.skn'
        warnings = []
        _, _, palette = export_skn.write_skn_multi(path, [obj], arm, export_warnings=warnings)
        result = rs.Skn.from_path(str(path))
        self.assertEqual(palette, [0])
        self.assertEqual(warnings, [])
        self.assertEqual(list(struct.iter_unpack('<4f', result.blend_weights)), [(1, 0, 0, 0)] * 3)

    def test_skn_unbound_face_vertices_reject_but_loose_vertices_do_not(self):
        arm, obj = self._weight_fixture()
        obj.data.vertices.add(1)  # Not referenced by a face, so not exported.
        path = self.directory / 'loose.skn'
        export_skn.write_skn_multi(path, [obj], arm)
        self.assertEqual(rs.Skn.from_path(str(path)).vertex_count, 3)
        obj.vertex_groups.clear()
        with self.assertRaisesRegex(ValueError, '3 exported vertices with no positive bone weights'):
            export_skn.write_skn_multi(self.directory / 'unbound.skn', [obj], arm)
        self.assertFalse((self.directory / 'unbound.skn').exists())

    def test_skn_partial_unmatched_weights_report_warning_in_export_operator(self):
        arm, obj = self._weight_fixture()
        obj.vertex_groups.new(name='ModifierMask').add([0, 1, 2], .5, 'REPLACE')
        self._select_for_skin_export(obj)
        messages = []
        operator = SimpleNamespace(report=lambda level, message: messages.append((level, message)))
        path = self.directory / 'with-mask.skn'
        self.assertEqual(export_skn.save(operator, bpy.context, str(path), target_armature=arm), {'FINISHED'})
        warnings = [message for level, message in messages if level == {'WARNING'}]
        self.assertEqual(len(warnings), 1)
        self.assertIn('ModifierMask', warnings[0])
        self.assertIn('ignored weighted groups', warnings[0])
        skeleton = rs.Skl.from_path(str(path.with_suffix('.skl')))
        result = rs.Skn.from_path(str(path))
        self.assertEqual([skeleton.joints[i].name for i in skeleton.influences], ['child'])
        self.assertEqual(list(struct.iter_unpack('<4f', result.blend_weights)), [(1, 0, 0, 0)] * 3)

    def test_skn_empty_and_zero_weight_groups_do_not_warn(self):
        arm, obj = self._weight_fixture()
        obj.vertex_groups.new(name='Unused')
        obj.vertex_groups.new(name='ZeroMask').add([0, 1, 2], 0, 'REPLACE')
        warnings = []
        export_skn.write_skn_multi(self.directory / 'zero-groups.skn', [obj], arm, export_warnings=warnings)
        self.assertEqual(warnings, [])

    def test_skn_validation_uses_export_aliases_and_exact_bone_names(self):
        arm, obj = self._weight_fixture()
        obj.vertex_groups['child'].name = 'child.001'
        warnings = []
        _, _, palette = export_skn.write_skn_multi(self.directory / 'alias.skn', [obj], arm, export_warnings=warnings)
        self.assertEqual(palette, [1])
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        bone = arm.data.edit_bones.new('child.001')
        bone.head, bone.tail = (0, 0, 1), (0, 0, 2)
        bone.parent = arm.data.edit_bones['root']
        bpy.ops.object.mode_set(mode='OBJECT')
        _, _, palette = export_skn.write_skn_multi(self.directory / 'exact.skn', [obj], arm, export_warnings=warnings)
        self.assertEqual(palette, [2])
        self.assertEqual(warnings, [])
