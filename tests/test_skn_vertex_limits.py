"""Exercise SKN index limits through real Blender geometry and the native writer."""
import base64
import struct
from types import SimpleNamespace

from test_backend import bpy, rs, import_skl, export_skn
from aventurine.io import material_order, skin_normals


class SkinVertexLimitChecks:
    def _large_mesh_fixture(self, counts):
        joints, _ = import_skl.read_skl(self.skeleton())
        arm = import_skl.create_armature(joints, bone_orient='NATIVE')
        objects = []
        for part, count in enumerate(counts):
            name = f'part{part}'
            positions = []
            for i in range(count):
                triangle, corner = divmod(i, 3)
                positions.append(((triangle % 200)*2 + (corner == 1),
                                  (triangle // 200)*2 + (corner == 2), part*10))
            faces = [(i, i+1, i+2) for i in range(0, count-2, 3)]
            if count % 3:
                faces.append((count-3, count-1, count-2))
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(positions, [], faces)
            uv = mesh.uv_layers.new(name='UVMap')
            uv.data.foreach_set('uv', [0.0] * (len(mesh.loops)*2))
            skin_normals.store(mesh, [(0, 0, 1)] * len(mesh.loops))
            mat = bpy.data.materials.new(name)
            mat['lol_material_name'] = name
            mesh.materials.append(mat)
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj.parent = arm
            obj.vertex_groups.new(name='root').add(list(range(count)), 1, 'REPLACE')
            obj['lol_skn_flags'] = 0
            objects.append(obj)
        bpy.context.view_layer.update()
        return arm, objects

    def _section_payloads(self, skin):
        indices = [v[0] for v in struct.iter_unpack('<I', skin.indices)]
        buffers = [(skin.positions, 12), (skin.normals, 12), (skin.uvs, 8),
                   (skin.blend_indices, 16), (skin.blend_weights, 16)]
        result = {}
        for sm in skin.submeshes:
            vertex_end = sm.vertex_start + sm.vertex_count
            corners = indices[sm.index_start:sm.index_start + sm.index_count]
            self.assertTrue(all(sm.vertex_start <= i < vertex_end for i in corners))
            result[sm.name] = ([buf[sm.vertex_start*stride:vertex_end*stride]
                                for buf, stride in buffers],
                               [i-sm.vertex_start for i in corners])
        return result

    def test_skn_large_combined_export_auto_relative_and_material_order(self):
        arm, objects = self._large_mesh_fixture([40002, 40002])
        original_data = [obj.data for obj in objects]
        path = self.directory / 'large.skn'
        _, count, palette = export_skn.write_skn_multi(path, objects, arm)
        skin = rs.Skn.from_path(str(path))
        self.assertEqual((count, skin.vertex_count, skin.flags, palette), (80004, 80004, 2, [0]))
        self.assertEqual([s.vertex_count for s in skin.submeshes], [40002, 40002])
        self.assertEqual(max(i[0] for i in struct.iter_unpack('<I', skin.indices)), 80003)
        # Inspect actual uint16 disk indices, not just the reader's resolved values.
        disk = path.read_bytes()
        offset = 12 + 2*80 + 60
        self.assertEqual(max(struct.unpack_from('<80004H', disk, offset)), 40001)
        self.assertEqual(rs.Skn.from_bytes(disk).to_bytes(), disk)
        expected = self._section_payloads(skin)
        material_order.set_order(arm, ['part1', 'part0'])
        export_skn.write_skn_multi(path, objects, arm)
        reordered = rs.Skn.from_path(str(path))
        self.assertEqual([s.name for s in reordered.submeshes], ['part1', 'part0'])
        self.assertEqual(reordered.flags, 2)
        self.assertEqual(self._section_payloads(reordered), expected)
        self.assertEqual([obj['lol_skn_flags'] for obj in objects], [0, 0])
        self.assertEqual([obj.data for obj in objects], original_data)

    def test_skn_65536_boundary_and_uv_split_overflow_preserves_output_pair(self):
        arm, objects = self._large_mesh_fixture([65536])
        obj = objects[0]
        path = self.directory / 'boundary.skn'
        export_skn.write_skn_multi(path, objects, arm)
        skin = rs.Skn.from_path(str(path))
        self.assertEqual((skin.vertex_count, skin.flags), (65536, 0))
        self.assertEqual(max(i[0] for i in struct.iter_unpack('<I', skin.indices)), 65535)
        # Splitting one reused corner takes the exported count to 65537 even
        # though Blender still reports 65536 vertices.
        obj.data.uv_layers.active.data[obj.data.polygons[-1].loop_start].uv.x = .25
        before = path.read_bytes()
        path.with_suffix('.skl').write_bytes(b'keep original skeleton')
        self._select_for_skin_export(obj)
        messages = []
        operator = SimpleNamespace(report=lambda level, text: messages.append((level, text)))
        self.assertEqual(export_skn.save(operator, bpy.context, str(path), target_armature=arm), {'CANCELLED'})
        error = next(text for level, text in messages if level == {'ERROR'})
        for fragment in ('part0', '65,537', '65,536 per submesh', 'UV/normal', 'Split'):
            self.assertIn(fragment, error)
        self.assertEqual(len(obj.data.vertices), 65536)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.with_suffix('.skl').read_bytes(), b'keep original skeleton')

    def test_skn_mixed_index_encodings_combine_without_losing_metadata(self):
        arm, obj = self._weight_fixture()
        other = self._mesh_copy(obj)
        for base_flags in (0, 1):
            prefix = b'opaque prefix' if base_flags else b''
            obj['lol_skn_flags'] = base_flags
            other['lol_skn_flags'] = base_flags | 2
            for mesh in (obj, other):
                mesh['lol_skn_prefix'] = base64.b64encode(prefix).decode('ascii')
                mesh['lol_skn_trailing'] = base64.b64encode(b'opaque tail').decode('ascii')
            for objects in ([obj, other], [other, obj]):
                with self.subTest(flags=base_flags, first=objects[0].name):
                    path = self.directory / 'mixed-indices.skn'
                    export_skn.write_skn_multi(path, objects, arm)
                    skin = rs.Skn.from_path(str(path))
                    self.assertEqual((skin.vertex_count, skin.flags), (6, base_flags | 2))
                    self.assertEqual(skin.prefix_block, prefix)
                    self.assertEqual(skin.trailing, b'opaque tail')
                    self._section_payloads(skin)
                    self.assertEqual(obj['lol_skn_flags'], base_flags)
                    self.assertEqual(other['lol_skn_flags'], base_flags | 2)
