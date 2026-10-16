"""Run in Blender, like test_backend.py. Optional LEMON_FORMATS points at pyRitoFile."""
import importlib
import os
from pathlib import Path
import struct
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from test_backend import BackendTests, bpy, rs, packed, import_skn, import_skl, export_skn, export_skl
from aventurine.utils import texture_manager


class FormatSweepTests(BackendTests):
    def test_texture_failures_are_reported_in_blender(self):
        from aventurine.utils import texture_manager
        messages = []
        operator = types.SimpleNamespace(report=lambda level, message: messages.append((level, message)))
        texture_manager.report_texture_issues(operator, ['Missing texture: hair.tex', 'Texture decode failed: body.tex'])
        self.assertEqual(messages[0][0], {'WARNING'})
        self.assertIn('hair.tex', messages[0][1])
        self.assertIn('decode failed', messages[0][1])

    def test_scb_rust_export_preserves_colors_and_legacy_version(self):
        from aventurine.io import import_scb, export_scb
        for version, colors in [((2, 2), None), ((3, 2), bytes([7, 63, 129, 210] * 3))]:
            source = rs.Scb.new('test', packed('f', [(0, 0, 0), (1, 0, 0), (0, 1, 0)]),
                [rs.ScbFace('body.part', (0, 1, 2), ((0, 0), (1, 0), (0, 1)))],
                central=(12, 23, 34), colors=colors, version=version)
            path = self.directory / 'source.scb'
            source.to_path(str(path))
            data = import_scb.read_scb(path)
            mesh = import_scb.create_mesh(data)
            obj = bpy.data.objects.new('static', mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj['lol_scb_central'] = data['central']
            bpy.context.view_layer.update()
            exporter = types.SimpleNamespace(collect_vertex_colors=lambda *args: export_scb.ExportSCB.collect_vertex_colors(None, *args))
            out = self.directory / 'output.scb'
            export_scb.ExportSCB.export_scb(exporter, bpy.context, obj, str(out), 1, None)
            result = rs.Scb.from_path(str(out))
            self.assertEqual(result.version, version)
            self.assertEqual(result.colors, colors)
            self.assertEqual(result.central, source.central)
            self.assertEqual(list(struct.iter_unpack('<3f', result.positions)), list(struct.iter_unpack('<3f', source.positions)))

    @unittest.skipUnless(os.environ.get('AVENTURINE_SERAPHINE_SKN'), 'Optional real Seraphine export')
    def test_real_seraphine_scb_files_roundtrip(self):
        from aventurine.io import import_scb, export_scb
        from pathlib import Path
        paths = sorted(Path(os.environ['AVENTURINE_SERAPHINE_SKN']).parent.rglob('*.scb'))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(file=path.name):
                source = rs.Scb.from_path(str(path))
                self.assertEqual(source.to_bytes(), path.read_bytes())
                data = import_scb.read_scb(path)
                mesh = import_scb.create_mesh(data)
                obj = bpy.data.objects.new(path.stem, mesh)
                bpy.context.scene.collection.objects.link(obj)
                obj['lol_scb_central'] = data['central']
                obj['lol_scb_flag'] = data['scb_flag']
                bpy.context.view_layer.update()
                exporter = types.SimpleNamespace(collect_vertex_colors=lambda *args: export_scb.ExportSCB.collect_vertex_colors(None, *args))
                out = self.directory / path.name
                if len(data['indices']) != len(data['source_indices']) and data['trailing']:
                    with self.assertRaisesRegex(ValueError, 'face layout'):
                        export_scb.ExportSCB.export_scb(exporter, bpy.context, obj, str(out), 1, None)
                    continue
                export_scb.ExportSCB.export_scb(exporter, bpy.context, obj, str(out), 1, None)
                result = rs.Scb.from_path(str(out))
                # Blender's matrix operations may change +0 to -0; coordinates must still match exactly.
                self.assertEqual(list(struct.iter_unpack('<3f', result.positions)), list(struct.iter_unpack('<3f', source.positions)))
                self.assertEqual(result.colors, source.colors)
                self.assertEqual(result.trailing, source.trailing)
                self.assertEqual(result.flags, source.flags)
                self.assertEqual(result.central, source.central)
                self.assertEqual([f.indices for f in result.faces], [f.indices for f in source.faces])
                self.assertEqual([f.material for f in result.faces], [f.material for f in source.faces])
                for actual, expected in zip(result.faces, source.faces):
                    for a, e in zip(actual.uvs, expected.uvs):
                        self.assertVector(a, e)

    def test_hashed_bin_textures_load_and_missing_override_stays_untextured(self):
        folder = self.directory / 'assets/jade/characters/test/skins/base'
        folder.mkdir(parents=True)
        bin_path = self.directory / 'data/characters/test/skins/skin0.bin'
        bin_path.parent.mkdir(parents=True)
        body = 'assets/jade/characters/test/skins/base/body.tex'
        hair = 'assets/jade/characters/test/skins/base/hair.tex'
        missing = 'assets/jade/characters/test/skins/base/missing.tex'
        for name, pixel in [('body.tex', bytes([0, 0, 255, 255])), ('hair.tex', bytes([255, 0, 0, 255]))]:
            (folder / name).write_bytes(struct.pack('<IHHBBBB', 0x00584554, 1, 1, 0, 20, 0, 0) + pixel)
        bin_path.write_bytes(rs.text_to_bin_bytes('''#PROP_text
type: string = "PROP"
version: u32 = 3
linked: list[string] = {}
entries: map[hash,embed] = {
  0x00000001 = 0x00000002 {
    0x45ff5904: embed = 0x00000003 {
      0x3c6468f4: file = "BODY"
      0x24725910: list[embed] = {
        0x00000004 {
          0xaad7612c: string = "Hair"
          0x3c6468f4: file = "HAIR"
        }
        0x00000004 {
          0xaad7612c: string = "Missing"
          0x3c6468f4: file = "MISSING"
        }
      }
    }
  }
  0x00000005 = 0xff9d3409 {
    0x0a6f0eb5: list[embed] = {
      0x00000006 {
        0xb311d4ef: string = "Diffuse_Texture"
        0xf0a363e3: file = "HAIR"
      }
    }
  }
}
'''.replace('BODY', body.upper()).replace('HAIR', hair.upper()).replace('MISSING', missing.upper())))
        # These really are numeric file references coming from the Rust parser.
        raw = rs.read_bin(str(bin_path))
        self.assertEqual(raw['entries'][1][0x45ff5904][0x3c6468f4], rs.wad_hash(body))
        self.assertEqual(texture_manager.parse_bin_for_textures(bin_path),
                         {'BASE': body, 'Hair': hair, 'Missing': None})
        material = texture_manager.parse_materials_bin(bin_path)['materials'][0]
        self.assertEqual(material['samplers'][0]['texture'], hair)
        skn_path = folder / 'test.skn'
        self.skin().to_path(str(skn_path))
        obj = import_skn.create_mesh(*import_skn.read_skn(skn_path), 'HashedTextures')
        for name in ('Hair', 'Missing'):
            mat = bpy.data.materials.new(name)
            mat['lol_material_name'] = name
            mat.use_nodes = True
            obj.data.materials.append(mat)
        texture_manager.import_textures(obj, str(skn_path))
        images = [[n.image for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]
                  for mat in obj.data.materials]
        self.assertEqual([len(items) for items in images], [1, 1, 0])
        self.assertVector(images[0][0].pixels[:], (1, 0, 0, 1))
        self.assertVector(images[1][0].pixels[:], (0, 0, 1, 1))
        self.assertTrue(images[0][0].packed_file)

    def test_hash_lookup_stays_in_export_and_handles_converted_textures(self):
        local = self.directory / 'local/assets/shared'
        local.mkdir(parents=True)
        source = self.directory / 'local/data/skin0.bin'
        source.parent.mkdir(parents=True)
        source.touch()
        (local / 'body.dds').touch()
        neighboring = self.directory / 'neighbor/assets/shared'
        neighboring.mkdir(parents=True)
        (neighboring / 'hair.tex').touch()
        references = texture_manager._TextureReferences(source)
        self.assertEqual(references.resolve(rs.wad_hash('ASSETS/Shared/body.tex')), 'assets/shared/body.dds')
        self.assertIsNone(references.resolve(rs.wad_hash('assets/shared/hair.tex')))
        (local / 'body.tex').touch()
        references = texture_manager._TextureReferences(source)
        self.assertEqual(references.resolve(rs.wad_hash('assets/shared/body.tex')), 'assets/shared/body.tex')
        self.assertEqual(references.resolve('legacy/path.tex'), 'legacy/path.tex')

    @unittest.skipUnless(os.environ.get('AVENTURINE_SERAPHINE_SKN'), 'Optional real Seraphine export')
    def test_real_seraphine_import_loads_all_six_materials(self):
        path = os.environ['AVENTURINE_SERAPHINE_SKN']
        messages = []
        operator = types.SimpleNamespace(report=lambda levels, message: messages.append(message))
        result = import_skn.load(operator, bpy.context, path, auto_load_textures=True)
        self.assertEqual(result, {'FINISHED'}, messages)
        obj = next(obj for obj in bpy.context.scene.objects if obj.type == 'MESH')
        expected = {'Body_Mat': 'body', 'Mic': 'body', 'Hair': 'hair', 'Bar': 'hair',
                    'Plane': 'hair', 'UltSpeaker': 'ultspeaker'}
        self.assertEqual(len(obj.data.materials), 6)
        for material in obj.data.materials:
            images = [n.image for n in material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image]
            self.assertEqual(len(images), 1, material.name)
            source = Path(images[0]['lol_source_path']).name
            self.assertEqual(source, f"seraphine_base_{expected[material['lol_material_name']]}_tx_cm.tex")
            self.assertTrue(images[0].packed_file)

    def skin(self, **kwargs):
        params = dict(
            positions=packed('f', [(10, 20, 30), (110, 20, 30), (10, 120, 30)]),
            normals=packed('f', [(0, 0, 1)] * 3),
            uvs=packed('f', [(0, 0), (1, 0), (0, 1)]),
            blend_indices=packed('I', [(0, 0, 0, 0)] * 3),
            blend_weights=packed('f', [(1, 0, 0, 0)] * 3),
            indices=packed('I', [(0, 1, 2)]),
            submeshes=[rs.Submesh('body.part.001', 0, 3, 0, 3)])
        params.update(kwargs)
        return rs.Skn.new(**params)

    def roundtrip_skin(self, source):
        joints, influences = import_skl.read_skl(self.skeleton())
        armature = import_skl.create_armature(joints, bone_orient='NATIVE')
        path = self.directory / 'source.skn'
        source.to_path(str(path))
        indices, vertices, submeshes = import_skn.read_skn(path)
        obj = import_skn.create_mesh(indices, vertices, submeshes, 'skin', armature, joints, influences)
        bpy.context.view_layer.update()
        out = self.directory / 'out.skn'
        export_skn.write_skn_multi(str(out), [obj], armature)
        return rs.Skn.from_path(str(out)), out, obj, armature

    def test_colors_tangents_metadata_and_material_name(self):
        colors = bytes([7, 63, 129, 210, 50, 99, 30, 180, 255, 0, 100, 255])
        tangents = packed('f', [(1, 0, 0, -1)] * 3)
        source = self.skin(colors=colors, tangents=tangents, flags=3,
                           prefix_block=b'prefix', trailing=b'custom-tail')
        out, _, obj, armature = self.roundtrip_skin(source)
        self.assertEqual(out.colors, colors)
        self.assertEqual(list(struct.iter_unpack('<4f', out.tangents)), list(struct.iter_unpack('<4f', tangents)))
        self.assertEqual(out.flags, 3)
        self.assertEqual(out.prefix_block, b'prefix')
        self.assertEqual(out.trailing, b'custom-tail')
        self.assertEqual(out.submeshes[0].name, 'body.part.001')
        self.assertEqual(out.indices, source.indices)
        for actual, expected in zip(struct.iter_unpack('<3f', out.positions), struct.iter_unpack('<3f', source.positions)):
            self.assertVector(actual, expected, places=4)
        with self.assertRaisesRegex(ValueError, 'visual pose'):
            export_skn.write_skn_multi(str(self.directory / 'visual.skn'), [obj], armature, use_visual_pose=True)

    def test_relative_indices_rebased_for_second_submesh(self):
        source = self.skin(
            positions=packed('f', [(0, 0, 0)] * 6), normals=packed('f', [(0, 0, 1)] * 6),
            uvs=packed('f', [(0, 0)] * 6), blend_indices=packed('I', [(0, 0, 0, 0)] * 6),
            blend_weights=packed('f', [(1, 0, 0, 0)] * 6), indices=packed('I', [(0, 1, 2), (3, 4, 5)]),
            submeshes=[rs.Submesh('one', 0, 3, 0, 3), rs.Submesh('two', 3, 3, 3, 3)], flags=2)
        self.assertEqual(rs.Skn.from_bytes(source.to_bytes()).indices, packed('I', [(0, 1, 2, 3, 4, 5)]))
        # Header: magic/version/count + two ranges + flags/counts/layout/bounds.
        self.assertEqual(struct.unpack_from('<6H', source.to_bytes(), 12 + 160 + 60), (0, 1, 2, 0, 1, 2))

    def test_tangents_regenerate_after_mesh_edit(self):
        source = self.skin(colors=bytes([255] * 12), tangents=packed('f', [(1, 0, 0, 1)] * 3))
        _, _, obj, arm = self.roundtrip_skin(source)
        original_basis = obj['lol_skn_tangent_basis']
        obj.data.vertices[1].co.z += 0.5
        obj.data.update()
        self.assertNotEqual(export_skn.tangent_basis_signature(obj.data), original_basis)
        path = self.directory / 'edited.skn'
        export_skn.write_skn_multi(str(path), [obj], arm)
        tangent = next(struct.iter_unpack('<4f', rs.Skn.from_path(str(path)).tangents))
        self.assertGreater(abs(tangent[1]), 0.1)
        self.assertAlmostEqual(sum(v*v for v in tangent[:3]), 1, places=5)

    def test_invalid_geometry_is_rejected_before_writing(self):
        for kwargs in ({'positions': packed('f', [(float('nan'), 0, 0)] * 3)},
                       {'colors': b'bad'}, {'tangents': packed('f', [(1, 0, 0, 1)] * 3)},
                       {'prefix_block': b'prefix'},
                       {'submeshes': [rs.Submesh('body', 1, 2, 0, 3)]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(rs.WriteError):
                self.skin(**kwargs)

    def test_degenerate_triangle_does_not_shift_materials(self):
        source = self.skin(indices=packed('I', [(0, 0, 1), (0, 1, 2)]),
                           submeshes=[rs.Submesh('skip', 0, 3, 0, 3), rs.Submesh('keep', 0, 3, 3, 3)])
        path = self.directory / 'degenerate.skn'
        source.to_path(str(path))
        obj = import_skn.create_mesh(*import_skn.read_skn(path), 'degenerate')
        self.assertEqual(len(obj.data.polygons), 1)
        self.assertEqual(obj.data.polygons[0].material_index, 1)

    def test_standalone_skl_preserves_palette_with_added_and_renamed_bones(self):
        joints, palette = import_skl.read_skl(self.skeleton())
        arm = import_skl.create_armature(joints, bone_orient='NATIVE')
        arm.pose.bones['child'].name = 'renamed_child'
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        extra = arm.data.edit_bones.new('cape')
        extra.head, extra.tail = (0, 0, 1), (0, 0, 2)
        extra.parent = arm.data.edit_bones['root']
        bpy.ops.object.mode_set(mode='OBJECT')
        out = self.directory / 'standalone.skl'
        export_skl.write_skl(out, arm)
        result = rs.Skl.from_path(str(out))
        self.assertEqual(result.influences, palette)
        self.assertEqual(len(result.joints), 3)
        self.assertEqual(result.joints[result.influences[0]].name, 'renamed_child')
        # A later paired export becomes the palette for subsequent separate exports.
        export_skl.write_skl(out, arm, influences=[2, 0])
        export_skl.write_skl(out, arm)
        self.assertEqual(rs.Skl.from_path(str(out)).influences, [2, 0])

    def test_standalone_palette_remaps_ids_and_rejects_removed_influence(self):
        path = self.skeleton()
        original = rs.Skl.from_path(str(path))
        rs.Skl.new(original.joints, [1]).to_path(str(path))
        joints, _ = import_skl.read_skl(path)
        arm = import_skl.create_armature(joints, bone_orient='NATIVE')
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        arm.data.edit_bones.remove(arm.data.edit_bones['root'])
        bpy.ops.object.mode_set(mode='OBJECT')
        out = self.directory / 'renumbered.skl'
        export_skl.write_skl(out, arm)
        result = rs.Skl.from_path(str(out))
        self.assertEqual(result.influences, [0])
        self.assertEqual(result.joints[0].name, 'child')
        bpy.ops.object.mode_set(mode='EDIT')
        arm.data.edit_bones.remove(arm.data.edit_bones['child'])
        bpy.ops.object.mode_set(mode='OBJECT')
        out = self.directory / 'missing.skl'
        with self.assertRaisesRegex(ValueError, 'palette'):
            export_skl.write_skl(out, arm)
        self.assertFalse(out.exists())

    def test_legacy_blend_palette_recovery_checks_source_identity(self):
        path = self.skeleton()
        joints, palette = import_skl.read_skl(path)
        arm = import_skl.create_armature(joints, bone_orient='NATIVE')
        del arm['lol_skin_palette']
        arm['lol_skl_filepath'] = str(path)
        out = self.directory / 'legacy.skl'
        export_skl.write_skl(out, arm)
        self.assertEqual(rs.Skl.from_path(str(out)).influences, palette)
        del arm['lol_skin_palette']
        arm['lol_source_joints'] = '{"0": "unrelated", "1": "child"}'
        with self.assertRaisesRegex(ValueError, 'no longer matches'):
            export_skl.write_skl(out, arm)
        arm['lol_skl_filepath'] = str(self.directory / 'missing.skl')
        with self.assertRaisesRegex(ValueError, 'palette is missing'):
            export_skl.write_skl(out, arm)

    def test_skn_only_keeps_slots_and_requires_pair_for_new_weighted_bones(self):
        source = self.skin(blend_indices=packed('I', [(1, 0, 0, 0)] * 3))
        _, _, obj, arm = self.roundtrip_skin(source)
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        messages = []
        op = types.SimpleNamespace(report=lambda levels, msg: messages.append(msg))
        out = self.directory / 'mesh_only.skn'
        result = export_skn.save(op, bpy.context, str(out), export_skl_file=False, target_armature=arm)
        self.assertEqual(result, {'FINISHED'}, messages)
        slots = list(struct.iter_unpack('<4I', rs.Skn.from_path(str(out)).blend_indices))
        self.assertTrue(all(v[0] == 1 for v in slots))  # source palette [child, root]
        self.assertFalse(out.with_suffix('.skl').exists())
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        extra = arm.data.edit_bones.new('cape')
        extra.head, extra.tail = (0, 0, 0), (0, 0, 1)
        bpy.ops.object.mode_set(mode='OBJECT')
        obj.vertex_groups.new(name='cape').add([0], 1, 'REPLACE')
        failed = self.directory / 'missing_palette.skn'
        result = export_skn.save(op, bpy.context, str(failed), export_skl_file=False, target_armature=arm)
        self.assertEqual(result, {'CANCELLED'})
        self.assertIn('Export SKN and SKL together', messages[-1])
        self.assertFalse(failed.exists())
        # Paired export can safely introduce the new influence.
        result = export_skn.save(op, bpy.context, str(failed), target_armature=arm)
        self.assertEqual(result, {'FINISHED'}, messages)
        paired = rs.Skl.from_path(str(failed.with_suffix('.skl')))
        export_skl.write_skl(self.directory / 'later.skl', arm)
        self.assertEqual(rs.Skl.from_path(str(self.directory / 'later.skl')).influences, paired.influences)

    def test_raw_skn_bounds_follow_edited_and_scaled_geometry(self):
        _, _, obj, arm = self.roundtrip_skin(self.skin())
        obj.data.vertices[0].co.x -= 3
        obj.data.update()
        out = self.directory / 'bounds.skn'
        export_skn.write_skn_multi(str(out), [obj], arm, model_scale=1.5)
        raw = out.read_bytes()
        self.assertEqual(struct.unpack_from('<HH', raw, 4), (4, 1))
        count = struct.unpack_from('<I', raw, 8)[0]
        header = 12 + count * 80
        flags, ni, nv, stride, kind = struct.unpack_from('<5I', raw, header)
        self.assertEqual(flags, 0)
        bounds = struct.unpack_from('<10f', raw, header + 20)
        start = header + 60 + ni * 2
        points = [struct.unpack_from('<3f', raw, start + i * stride) for i in range(nv)]
        self.assertVector(bounds[:3], tuple(min(p[a] for p in points) for a in range(3)))
        self.assertVector(bounds[3:6], tuple(max(p[a] for p in points) for a in range(3)))
        self.assertGreater(bounds[3], 400)  # verifies the edit and export scaling
        for point in points:
            self.assertLessEqual(sum((point[a] - bounds[6+a])**2 for a in range(3))**0.5, bounds[9] + 1e-4)

    def test_skeleton_metadata_roundtrip(self):
        path = self.skeleton()
        joint = rs.Joint('root', 0, -1, 5.5, (0, 0, 0), (1, 1, 1), (0, 0, 0, 1),
                         (0, 0, 0), (1, 1, 1), (0, 0, 0, 1), flags=7)
        rs.Skl.new([joint], [0], name='skeleton-name', asset='asset/path', flags=9).to_path(str(path))
        joints, _ = import_skl.read_skl(path)
        arm = import_skl.create_armature(joints, bone_orient='NATIVE')
        out = self.directory / 'out.skl'
        export_skl.write_skl(str(out), arm)
        result = rs.Skl.from_path(str(out))
        self.assertEqual((result.name, result.asset, result.flags), ('skeleton-name', 'asset/path', 9))
        self.assertEqual((result.joints[0].flags, result.joints[0].radius), (7, 5.5))

    def test_skin_lookup_does_not_choose_another_skin(self):
        folder = self.directory / 'ASSETS/Characters/Test/Skins/Skin1'
        folder.mkdir(parents=True)
        other = self.directory / 'data/characters/Test/skins/skin10.bin'
        other.parent.mkdir(parents=True)
        other.touch()
        self.assertIsNone(texture_manager.find_bin_and_read(str(folder / 'skin.skn')))
        target = other.with_name('skin1.bin')
        target.touch()
        self.assertEqual(Path(texture_manager.find_bin_and_read(str(folder / 'skin.skn'))), target)

    def test_exact_asset_path_wins_over_matching_basename(self):
        skn = self.directory / 'assets/characters/test.skn'
        skn.parent.mkdir(parents=True)
        (skn.parent / 'texture.dds').touch()
        exact = self.directory / 'assets/shared/texture.dds'
        exact.parent.mkdir(parents=True)
        exact.touch()
        resolved = Path(texture_manager.resolve_texture_path(str(skn), 'assets/shared/texture.dds'))
        # Windows runners use RUNNER~1 for TEMP, while resolve() expands it to
        # runneradmin. Compare file identity rather than the spelling of its path.
        self.assertTrue(resolved.samefile(exact))
        self.assertFalse(resolved.samefile(skn.parent / 'texture.dds'))

    def test_linked_bin_legacy_sampler_and_cycle(self):
        # Exercise the actual Rust BIN serializer/parser, including a dependency cycle.
        def write(path, entries, linked):
            path.write_bytes(rs.write_bin_bytes(dict(version=3, is_patch=False, patch_header=bytes(8),
                                                     entries=entries, linked=linked, patches=[])))
        root = self.directory / 'skin.bin'
        linked = self.directory / 'materials.bin'
        # Constructing the exact typed document is covered in library tests; use the
        # text parser here so this test also exercises shader/material BIN syntax.
        text = '''#PROP_text
type: string = "PROP"
version: u32 = 3
linked: list[string] = { "skin.bin" }
entries: map[hash,embed] = {
  0x00000002 = 0xff9d3409 {
    0x0a6f0eb5: list[embed] = {
      0x00000003 {
        0x02e7fb4c: string = "Diffuse_Texture"
        0xf0a363e3: string = "linked.tex"
      }
    }
  }
}
'''
        linked.write_bytes(rs.text_to_bin_bytes(text))
        write(root, [], ['materials.bin'])
        merged = texture_manager.read_bin_with_links(root)
        from aventurine.utils.format_data import textures_from_bin
        merged['entries'][1] = {0x45ff5904: {0x24725910: [{0xaad7612c: 'eyes', 0xd2e4d060: 2}]}}
        self.assertEqual(textures_from_bin(merged), {'eyes': 'linked.tex'})
        self.assertEqual(len(texture_manager.parse_materials_bin(root)['materials']), 1)

    def test_dds_image_rows_channels_and_alpha(self):
        header = [124, 0x100F, 2, 1, 4, 0, 1] + [0] * 11
        header += [32, 0x41, 0, 32, 0xff0000, 0xff00, 0xff, 0xff000000]
        header += [0x1000, 0, 0, 0, 0]
        path = self.directory / 'colors.dds'
        path.write_bytes(b'DDS ' + struct.pack('<31I', *header) + bytes([30, 20, 10, 255, 70, 90, 130, 128]))
        image = texture_manager.load_tex_image(path)
        self.addCleanup(bpy.data.images.remove, image)
        self.assertVector(image.pixels[:4], (130/255, 90/255, 70/255, 128/255), places=4)
        self.assertVector(image.pixels[4:], (10/255, 20/255, 30/255, 1), places=4)
        self.assertTrue(image.packed_file)

    def test_sco_units_pivot_and_face_materials_roundtrip(self):
        from aventurine.io import import_sco, export_sco
        source = self.directory / 'static.sco'
        source.write_text('''[ObjectBegin]
Name= static
CentralPoint= 10 20 30
PivotPoint= 1 2 3
Verts= 4
0 0 0
100 0 0
0 100 0
100 100 0
Faces= 2
3 0 1 2 body.part 0 0 1 0 0 1
3 1 3 2 eyes.part 1 0 1 1 0 1
[ObjectEnd]
''')
        data = import_sco.read_sco(source)
        obj = import_sco.create_mesh_and_obj(bpy.context, data)
        self.assertFalse(any(o.type == 'ARMATURE' for o in bpy.context.scene.objects))
        bpy.context.view_layer.update()
        out = self.directory / 'out.sco'
        exporter = types.SimpleNamespace(find_pivot_bone=lambda *args: None)
        export_sco.ExportSCO.export_sco(exporter, bpy.context, obj, str(out), 1.0)
        result = import_sco.read_sco(out)
        self.assertEqual(result['materials'], ['body.part', 'eyes.part'])
        for actual, expected in zip(result['vertices'], data['vertices']):
            self.assertVector(actual, expected)
        self.assertVector(result['central'], data['central'])
        self.assertVector(result['pivot'], data['pivot'])

    def test_scb_tail_name_materials_and_reference_offsets(self):
        from aventurine.io import import_scb, export_scb
        source = rs.Scb.new('object-name', packed('f', [(10, 20, 30), (100, 20, 30), (10, 120, 30), (100, 120, 30)]), [
            rs.ScbFace('body.part', (0, 1, 2), ((0, 0), (1, 0), (0, 1))),
            rs.ScbFace('eyes.part', (1, 3, 2), ((1, 0), (1, 1), (0, 1)))])
        raw = bytearray(source.to_bytes())
        struct.pack_into('<I', raw, 148, 5)  # VCP plus local origin/pivot.
        tail = bytes(range(18)) + struct.pack('<6f', 1, 2, 3, 4, 5, 6)
        raw.extend(tail)
        path = self.directory / 'static.scb'
        path.write_bytes(raw)
        data = import_scb.read_scb(path)
        mesh = import_scb.create_mesh(data)
        obj = bpy.data.objects.new('static', mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj['lol_scb_central'] = data['central']
        obj['lol_scb_flag'] = data['scb_flag']
        bpy.context.view_layer.update()
        out = self.directory / 'out.scb'
        exporter = types.SimpleNamespace(collect_vertex_colors=lambda *args: None)
        export_scb.ExportSCB.export_scb(exporter, bpy.context, obj, str(out), 1.0, None)
        result = import_scb.read_scb(out)
        self.assertEqual(result['file_name'], 'object-name')
        self.assertEqual(result['materials'], ['body.part', 'eyes.part'])
        self.assertEqual(result['trailing'], tail)
        self.assertEqual(result['scb_flag'], 5)
        self.assertEqual(len(rs.Scb.from_path(str(out)).faces), 2)
        # The optional riot.scb reference reader previously sought absolute offsets.
        reference = export_scb.ExportSCB.read_riot_scb(exporter, str(path))
        self.assertIsNotNone(reference)
        self.assertVector(reference['central'], tuple(rs.Scb.from_bytes(bytes(raw)).central))
        mesh.polygons[0].flip()
        mesh.update()
        bpy.context.view_layer.update()
        with self.assertRaisesRegex(ValueError, 'face layout'):
            export_scb.ExportSCB.export_scb(exporter, bpy.context, obj, str(out), 1.0, None)

    def test_scb_vertex_colors_keep_bytes(self):
        from aventurine.io import import_scb, export_scb
        positions = packed('f', [(0, 0, 0), (1, 0, 0), (0, 1, 0)])
        raw = bytearray(rs.Scb.new('colors', positions, [rs.ScbFace('body', (0, 1, 2), ((0, 0), (1, 0), (0, 1)))]).to_bytes())
        struct.pack_into('<I', raw, 176, 1)
        colors = bytes([7, 63, 129, 210] * 3)
        raw[180 + len(positions):180 + len(positions)] = colors
        path = self.directory / 'colors.scb'
        path.write_bytes(raw)
        mesh = import_scb.create_mesh(import_scb.read_scb(path))
        rgba = export_scb.ExportSCB.collect_vertex_colors(None, mesh, 3)
        self.assertEqual(rgba, [(129, 63, 7, 210)] * 3)

    @unittest.skipUnless(os.environ.get('LEMON_FORMATS'), 'Set LEMON_FORMATS to the reference pyRitoFile directory')
    def test_lemon_independent_reader_bounds_colors_tangents(self):
        package = types.ModuleType('lemon_reference')
        package.__path__ = [os.environ['LEMON_FORMATS']]
        sys.modules['lemon_reference'] = package
        reference = importlib.import_module('lemon_reference.skn')
        source = self.skin(colors=bytes([7, 63, 129, 255] * 3), tangents=packed('f', [(1, 0, 0, 1)] * 3))
        _, path, _, _ = self.roundtrip_skin(source)
        mesh = reference.SKN().read(str(path))
        self.assertEqual(mesh.vertex_size, 72)
        self.assertVector(tuple(mesh.bounding_box[0]), (10, 20, 30))
        self.assertVector(tuple(mesh.bounding_box[1]), (110, 120, 30), places=4)
        center, radius = mesh.bounding_sphere
        import math
        for vertex in mesh.vertices:
            self.assertLessEqual(math.dist(tuple(vertex.position), tuple(center)), radius + 1e-4)
            self.assertEqual(tuple(vertex.color), (7, 63, 129, 255))
            self.assertVector(tuple(vertex.tangent), (1, 0, 0, 1))

    def _bone_rig(self):
        joints, _ = import_skl.read_skl(self.skeleton())
        return import_skl.create_armature(joints, bone_orient='VISUAL')

    def _bone_clip(self, arm):
        from aventurine.io import import_anm
        from aventurine.utils.binary_utils import Hash
        tracks = []
        for name, translation in [('root', (0, 0, 0)), ('child', (0, 100, 0))]:
            frames = [rs.AnimFrame(i/30, (0, 0, 0, 1),
                      (translation[0]+i*10, translation[1], translation[2]), (1+i*.1,)*3)
                      for i in range(3)]
            tracks.append(rs.AnimTrack(Hash.elf(name), frames))
        path = self.directory / 'motion.anm'
        rs.Anm.new(30, tracks).to_path(str(path))
        clip = import_anm.read_anm(path)
        arm.animation_data_create()
        arm.animation_data.action = bpy.data.actions.new('motion')
        import_anm.apply_anm(clip, arm)
        return clip

    def _add_joint(self, arm, name='cape', parent='root'):
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        b = arm.data.edit_bones.new(name)
        b.head, b.tail = (.3, .2, .5), (.3, .2, .9)
        b.parent = arm.data.edit_bones[parent] if parent else None
        bpy.ops.object.mode_set(mode='OBJECT')
        return arm.pose.bones[name]

    def test_duplicate_joint_appended_and_animation_kept(self):
        from aventurine.io import export_anm
        from aventurine.utils.binary_utils import Hash
        arm = self._bone_rig()
        self._bone_clip(arm)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.armature.select_all(action='DESELECT')
        b = arm.data.edit_bones['root']
        b.select = b.select_head = b.select_tail = True
        arm.data.edit_bones.active = b
        bpy.ops.armature.duplicate()
        duplicate = arm.data.edit_bones.active
        duplicate.head.x += .4
        duplicate.tail.x += .4
        bpy.ops.object.mode_set(mode='OBJECT')
        pb = arm.pose.bones['root.001']
        pb.location = (.2, .1, 0)
        pb.keyframe_insert(data_path='location', frame=3)
        skl, anm = self.directory/'out.skl', self.directory/'out.anm'
        export_skl.write_skl(skl, arm)
        export_anm.write_anm(anm, arm)
        result = rs.Skl.from_path(str(skl))
        self.assertEqual([j.name for j in result.joints], ['root', 'child', 'root_001'])
        self.assertIn(Hash.elf('root_001'), [t.joint_hash for t in rs.Anm.from_path(str(anm)).tracks])
        self.assertGreater(abs(result.joints[2].local_translation[0]), 30)
        self.assertIsNone(arm.pose.bones['root'].parent)

    def test_inserted_parent_keeps_stock_translation_and_scale(self):
        from aventurine.io import import_anm
        arm = self._bone_rig()
        clip = self._bone_clip(arm)
        bpy.context.scene.frame_set(3)
        before = arm.pose.bones['child'].matrix.copy()
        self._add_joint(arm, 'helper')
        bpy.ops.object.mode_set(mode='EDIT')
        arm.data.edit_bones['child'].parent = arm.data.edit_bones['helper']
        bpy.ops.object.mode_set(mode='OBJECT')
        arm.animation_data.action = bpy.data.actions.new('reimport')
        import_anm.apply_anm(clip, arm)
        bpy.context.scene.frame_set(3)
        after = arm.pose.bones['child'].matrix
        for actual, expected in zip(after, before):
            self.assertVector(actual, expected, places=4)
        self.assertVector(arm.pose.bones['child'].scale, (1.2,)*3)

    def test_explicit_offset_requires_bind_bake_and_keeps_stock_local(self):
        from aventurine.tools.offset_bones import add_offset_parent, apply_offset_bind
        from aventurine.io import export_anm
        from aventurine.utils.binary_utils import Hash
        arm = self._bone_rig()
        self._bone_clip(arm)
        bpy.context.scene.frame_set(0)
        control = add_offset_parent(arm, 'child')
        control.location.x = .2
        with self.assertRaisesRegex(ValueError, 'Apply Offset to Bind Pose'):
            export_skl.write_skl(self.directory/'unbaked.skl', arm)
        apply_offset_bind(arm)
        export_skl.write_skl(self.directory/'offset.skl', arm)
        export_anm.write_anm(self.directory/'offset.anm', arm)
        source = rs.Anm.from_path(str(self.directory/'motion.anm'))
        actual = rs.Anm.from_path(str(self.directory/'offset.anm'))
        child_hash = Hash.elf('child')
        expected_track = next(t for t in source.tracks if t.joint_hash == child_hash)
        actual_track = next(t for t in actual.tracks if t.joint_hash == child_hash)
        for expected, frame in zip(expected_track.frames, actual_track.frames):
            self.assertVector(frame.translation, expected.translation, places=3)
            self.assertVector(frame.scale, expected.scale, places=4)
        joints = rs.Skl.from_path(str(self.directory/'offset.skl')).joints
        control_joint = next(j for j in joints if j.name == 'child_Offset')
        self.assertVector(control_joint.local_scale, (1,1,1), places=4)
        self.assertVector(control_joint.local_rotation[:3], (0,0,0), places=4)

    def test_custom_track_reimports_and_insertion_preserves_other_keys(self):
        from aventurine.io import import_anm, export_anm
        arm = self._bone_rig()
        self._bone_clip(arm)
        cape = self._add_joint(arm)
        for frame in range(1,4):
            cape.location = (.1*frame, .05*frame, 0)
            cape.keyframe_insert(data_path='location', frame=frame)
        bpy.context.scene.frame_set(3)
        expected = cape.matrix.copy()
        path = self.directory/'cape.anm'
        export_anm.write_anm(path, arm)
        clip = import_anm.read_anm(path)
        arm.animation_data.action = bpy.data.actions.new('new')
        cape.location = (9,8,7)
        cape.keyframe_insert(data_path='location', frame=50)
        import_anm.apply_anm(clip, arm, frame_offset=10)
        bpy.context.scene.frame_set(13)
        for actual, original in zip(cape.matrix, expected):
            self.assertVector(actual, original, places=4)
        bpy.context.scene.frame_set(50)
        self.assertVector(cape.location, (9,8,7))
        curves = arm.animation_data.action.fcurves
        fc = curves.find(cape.path_from_id('location'), index=0)
        self.assertEqual({round(k.co.x) for k in fc.keyframe_points}, {11,12,13,50})

    def test_export_rejects_clean_name_hash_collision(self):
        from aventurine.io.skeleton import ExportPlan
        arm = self._bone_rig()
        self._add_joint(arm, 'cape.001')
        self._add_joint(arm, 'cape_001')
        with self.assertRaisesRegex(ValueError, 'same exported hash'):
            ExportPlan(arm)

    def test_offset_bind_blended_skin_matches_exported_skinning(self):
        import mathutils
        from aventurine.tools.offset_bones import add_offset_parent, apply_offset_bind
        from aventurine.io import export_anm
        from aventurine.io.skeleton import P
        arm = self._bone_rig()
        self._bone_clip(arm)
        source = rs.Skn.new(
            positions=packed('f', [(0,50,0), (20,50,0), (0,70,0)]),
            normals=packed('f', [(0,0,1)]*3), uvs=packed('f', [(0,0),(1,0),(0,1)]),
            blend_indices=packed('I', [(0,1,0,0)]*3),
            blend_weights=packed('f', [(.5,.5,0,0)]*3),
            indices=packed('I', [(0,1,2)]), submeshes=[rs.Submesh('body',0,3,0,3)])
        path = self.directory/'weighted.skn'
        source.to_path(str(path))
        indices, vertices, submeshes = import_skn.read_skn(path)
        joints, influences = import_skl.read_skl(self.skeleton())
        obj = import_skn.create_mesh(indices,vertices,submeshes,'weighted',arm,joints,influences)
        bpy.context.scene.frame_set(0)
        control = add_offset_parent(arm,'child')
        control.location.x = .2
        apply_offset_bind(arm)
        _, _, palette = export_skn.write_skn_multi(self.directory/'out.skn',[obj],arm)
        export_skl.write_skl(self.directory/'out.skl',arm,influences=palette)
        export_anm.write_anm(self.directory/'out.anm',arm)
        skl = rs.Skl.from_path(str(self.directory/'out.skl'))
        anm = rs.Anm.from_path(str(self.directory/'out.anm'))
        skn = rs.Skn.from_path(str(self.directory/'out.skn'))
        def matrix(t,r,s):
            return mathutils.Matrix.LocRotScale(mathutils.Vector(t),mathutils.Quaternion((r[3],*r[:3])),mathutils.Vector(s))
        tracks = {t.joint_hash:t for t in anm.tracks}
        globals = {}
        def global_for(i):
            if i not in globals:
                joint=skl.joints[i];frame=tracks[joint.hash].frames[2]
                local=matrix(frame.translation,frame.rotation,frame.scale)
                globals[i]=global_for(joint.parent_id)@local if joint.parent_id>=0 else local
            return globals[i]
        skins = [global_for(j.id) @ matrix(j.inverse_bind_translation,j.inverse_bind_rotation,j.inverse_bind_scale) for j in skl.joints]
        bpy.context.scene.frame_set(3)
        bpy.context.view_layer.update()
        evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
        try:
            expected=[P.inverted()@v.co*100 for v in mesh.vertices]
            rests=[P.inverted()@v.co*100 for v in obj.data.vertices]
            rows=zip(struct.iter_unpack('<3f',skn.positions),struct.iter_unpack('<4I',skn.blend_indices),struct.iter_unpack('<4f',skn.blend_weights))
            for point, slots, weights in rows:
                point=mathutils.Vector(point)
                original=min(range(len(rests)),key=lambda i:(rests[i]-point).length)
                actual=sum((skins[palette[slot]]@point*w for slot,w in zip(slots,weights) if w),mathutils.Vector())
                self.assertLess((actual-expected[original]).length,.001)
        finally:
            evaluated.to_mesh_clear()

    def test_offset_companion_preserves_independent_root_motion(self):
        import mathutils
        from aventurine.io import import_anm, export_anm
        from aventurine.io.skeleton import ExportPlan
        from aventurine.tools.offset_bones import add_offset_parent, apply_offset_bind, add_offset_companion
        from aventurine.utils.binary_utils import Hash
        joints = [
            rs.Joint('root',0,-1,2.1,(0,0,0),(1,1,1),(0,0,0,1),(0,0,0),(1,1,1),(0,0,0,1)),
            rs.Joint('child',1,0,2.1,(0,100,0),(1,1,1),(0,0,0,1),(0,-100,0),(1,1,1),(0,0,0,1)),
            rs.Joint('child_Snap',2,-1,2.1,(20,100,0),(1,1,1),(0,0,0,1),(-20,-100,0),(1,1,1),(0,0,0,1)),
        ]
        path=self.directory/'companion.skl';rs.Skl.new(joints,[0,1,2]).to_path(str(path))
        imported,_=import_skl.read_skl(path)
        arm=import_skl.create_armature(imported,bone_orient='VISUAL')
        tracks=[]
        for name,translation in [('root',(0,0,0)),('child',(0,100,0)),('child_Snap',(20,100,0))]:
            frames=[]
            for i in range(3):
                rotation=mathutils.Quaternion((0,0,1),i*.4 if name=='root' else i*.13)
                position=(translation[0]+i*7,translation[1]+i*3,translation[2])
                frames.append(rs.AnimFrame(i/30,(rotation.x,rotation.y,rotation.z,rotation.w),position,(1,1,1)))
            tracks.append(rs.AnimTrack(Hash.elf(name),frames))
        clip_path=self.directory/'source.anm';rs.Anm.new(30,tracks).to_path(str(clip_path))
        clip=import_anm.read_anm(clip_path);import_anm.apply_anm(clip,arm)
        original={}
        for frame in range(4):
            bpy.context.scene.frame_set(frame)
            original[frame]={pb.name:pb.matrix.copy() for pb in arm.pose.bones}
        bpy.context.scene.frame_set(0)
        control=add_offset_parent(arm,'child')
        # Ordinary offset creation must not force an animation-rewrite workflow.
        self.assertIsNone(arm.pose.bones['child_Snap'].parent)
        self.assertFalse(any(pb.constraints for pb in arm.pose.bones))
        add_offset_companion(arm,control.name,'child_Snap')
        self.assertEqual(arm.pose.bones['child_Snap'].parent.get('lol_offset_companion_of'),control.name)
        control.location=(.2,.1,0)
        apply_offset_bind(arm)
        expected={}
        for frame in range(4):
            bpy.context.scene.frame_set(frame)
            delta=arm.pose.bones['child'].matrix.translation-original[frame]['child'].translation
            actual=arm.pose.bones['child_Snap'].matrix
            goal=original[frame]['child_Snap'].copy();goal.translation+=delta
            for row_a,row_b in zip(actual,goal):self.assertVector(row_a,row_b,places=5)
            expected[frame]=ExportPlan(arm).globals(pose=True)
        # Reimporting an untouched clip keeps the live displacement constraints.
        arm.animation_data.action=bpy.data.actions.new('fresh source clip')
        import_anm.apply_anm(clip,arm)
        for frame in range(4):
            bpy.context.scene.frame_set(frame)
            for name,matrix in ExportPlan(arm).globals(pose=True).items():
                for actual,goal in zip(matrix,expected[frame][name]):self.assertVector(actual,goal,places=5)
        # Full exported clips also round-trip without applying the offset twice.
        out=self.directory/'linked.anm';export_anm.write_anm(out,arm)
        arm.animation_data.action=bpy.data.actions.new('exported clip')
        import_anm.apply_anm(import_anm.read_anm(out),arm)
        for frame in range(1,4):
            bpy.context.scene.frame_set(frame)
            for name,matrix in ExportPlan(arm).globals(pose=True).items():
                for actual,goal in zip(matrix,expected[frame][name]):self.assertVector(actual,goal,places=4)


if __name__ == '__main__':
    # The base suite is inherited so a single run also checks existing behavior.
    del BackendTests
    unittest.main()
