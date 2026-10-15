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
        self.assertEqual(Path(texture_manager.resolve_texture_path(str(skn), 'assets/shared/texture.dds')), exact)

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


if __name__ == '__main__':
    # The base suite is inherited so a single run also checks existing behavior.
    del BackendTests
    unittest.main()
