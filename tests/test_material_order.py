"""SKN submesh ordering integration coverage."""
import json
import struct
import hashlib
from test_backend import bpy, rs, packed, import_skn, import_skl, export_skn, export_skl
from aventurine.io import material_order as order
from aventurine.tools import material_order as ui


class MaterialOrderChecks:
    def material_fixture(self, names=('zOpaque','aGlass','mDetail')):
        count=len(names)
        source=rs.Skn.new(
            positions=packed('f',[(100*i+x,y,0) for i in range(count) for x,y in [(0,0),(20,0),(0,20)]]),
            normals=packed('f',[(0,0,1)]*(count*3)),
            uvs=packed('f',[(0,0),(1,0),(0,1)]*count),
            blend_indices=packed('I',[(0,1,0,0)]*(count*3)),
            blend_weights=packed('f',[(.75,.25,0,0)]*(count*3)),
            indices=packed('I',[(3*i,3*i+1,3*i+2) for i in range(count)]),
            colors=bytes([11,73,139,200]*(count*3)),
            tangents=packed('f',[(1,0,0,1)]*(count*3)),flags=2,
            submeshes=[rs.Submesh(name,3*i,3,3*i,3) for i,name in enumerate(names)])
        path=self.directory/'ordered.skn';source.to_path(str(path))
        joints,palette=import_skl.read_skl(self.skeleton());arm=import_skl.create_armature(joints)
        obj=import_skn.create_mesh(*import_skn.read_skn(path),'Model',arm,joints,palette)
        return arm,obj,source

    def test_material_limit_allows_31_exported_sections_and_ignores_unused_slots(self):
        names=tuple(f'material_{i:02}' for i in range(31))
        arm,obj,source=self.material_fixture(names)
        obj.data.materials.append(bpy.data.materials.new('unused_slot'))
        self.assertEqual(len(obj.data.materials),32)
        exported,_=self.export_materials(arm,[obj])
        self.assertEqual([sm.name for sm in exported.submeshes],list(names))
        self.assertEqual(exported.vertex_count,93)
        self.assertEqual(len(exported.indices)//4,93)

    def test_material_limit_imports_32_but_rejects_export_without_overwriting(self):
        arm,obj,source=self.material_fixture(tuple(f'material_{i:02}' for i in range(32)))
        self.assertEqual(len(source.submeshes),32)
        self.assertEqual(len(obj.data.materials),32)
        self.assertEqual(len(obj.data.polygons),32)
        mesh=obj.data
        path=self.directory/'existing.skn';path.write_bytes(b'keep existing file')
        with self.assertRaisesRegex(ValueError,r'32 submeshes.*maximum supported is 31'):
            export_skn.write_skn_multi(path,[obj],arm)
        self.assertEqual(path.read_bytes(),b'keep existing file')
        self.assertIs(obj.data,mesh)
        self.assertEqual(len(obj.data.materials),32)
        self.assertEqual(len(obj.data.polygons),32)

    def export_materials(self,arm,meshes):
        path=self.directory/'export.skn'
        _,_,palette=export_skn.write_skn_multi(path,meshes,arm)
        export_skl.write_skl(path.with_suffix('.skl'),arm,influences=palette)
        return rs.Skn.from_path(str(path)),rs.Skl.from_path(str(path.with_suffix('.skl')))

    def split_materials(self,arm,obj):
        bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);bpy.context.view_layer.objects.active=obj
        bpy.ops.object.mode_set(mode='EDIT');bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.separate(type='MATERIAL');bpy.ops.object.mode_set(mode='OBJECT')
        meshes=ui.meshes_for(bpy.context,arm)
        bpy.context.view_layer.objects.active=arm
        return meshes

    def test_material_order_preserves_source_after_splitting_and_reversed_selection(self):
        arm,obj,source=self.material_fixture();meshes=self.split_materials(arm,obj)
        exported,_=self.export_materials(arm,list(reversed(meshes)))
        self.assertEqual([sm.name for sm in exported.submeshes],[sm.name for sm in source.submeshes])

    def test_material_order_repacks_all_vertex_fields_and_palette(self):
        arm,obj,source=self.material_fixture()
        order.set_order(arm,['mDetail','aGlass','zOpaque'])
        exported,skl=self.export_materials(arm,[obj])
        self.assertEqual([s.name for s in exported.submeshes],['mDetail','aGlass','zOpaque'])
        self.assertEqual(exported.flags,2)
        old={s.name:s for s in source.submeshes}
        for sm in exported.submeshes:
            original=old[sm.name]
            for field,width,fmt in [('positions',12,'<3f'),('normals',12,'<3f'),('uvs',8,'<2f'),('tangents',16,'<4f')]:
                before=getattr(source,field)[original.vertex_start*width:(original.vertex_start+3)*width]
                after=getattr(exported,field)[sm.vertex_start*width:(sm.vertex_start+3)*width]
                for a,b in zip(struct.iter_unpack(fmt,after),struct.iter_unpack(fmt,before)):
                    self.assertVector(a,b,places=4 if field=='positions' else 5)
            self.assertEqual(exported.colors[sm.vertex_start*4:(sm.vertex_start+3)*4],source.colors[original.vertex_start*4:(original.vertex_start+3)*4])
            indices=list(struct.iter_unpack('<I',exported.indices))[sm.index_start:sm.index_start+sm.index_count]
            self.assertTrue(all(sm.vertex_start<=i[0]<sm.vertex_start+sm.vertex_count for i in indices))
        # Geometry reordered, not reassigned to different bones.
        for slots,weights in zip(struct.iter_unpack('<4I',exported.blend_indices),struct.iter_unpack('<4f',exported.blend_weights)):
            by_name={skl.joints[skl.influences[s]].name:w for s,w in zip(slots,weights) if w}
            self.assertAlmostEqual(by_name['child'],.75)
            self.assertAlmostEqual(by_name['root'],.25)

    def test_material_order_actual_move_reference_and_reset_operators(self):
        arm,obj,source=self.material_fixture();bpy.context.view_layer.objects.active=arm
        ui.register()
        try:
            self.assertEqual(bpy.ops.lol_material_order.move(armature_name=arm.name,material_name='aGlass',direction='UP'),{'FINISHED'})
            self.assertEqual(order.current_order(arm,[obj]),['aGlass','zOpaque','mDetail'])
            # Reference uses differently cased names, a missing material, and no Glass.
            ref=rs.Skn.new(positions=source.positions,normals=source.normals,uvs=source.uvs,
                blend_indices=source.blend_indices,blend_weights=source.blend_weights,indices=source.indices,
                submeshes=[rs.Submesh(n,3*i,3,3*i,3) for i,n in enumerate(['MDETAIL','missing','ZOPAQUE'])])
            path=self.directory/'reference.skn';ref.to_path(str(path));before=hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(bpy.ops.lol_material_order.reference(filepath=str(path),armature_name=arm.name),{'FINISHED'})
            self.assertEqual(order.current_order(arm,[obj]),['mDetail','zOpaque','aGlass'])
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
            exported,_=self.export_materials(arm,[obj])
            self.assertEqual([s.name for s in exported.submeshes],['mDetail','zOpaque','aGlass'])
            self.assertEqual(bpy.ops.lol_material_order.reset(),{'FINISHED'})
            self.assertEqual(order.current_order(arm,[obj]),['zOpaque','aGlass','mDetail'])
        finally:ui.unregister()

    def test_material_order_subset_new_materials_and_duplicate_names(self):
        arm,obj,source=self.material_fixture();meshes=self.split_materials(arm,obj)
        order.set_order(arm,['mDetail','aGlass','zOpaque'])
        glass=next(o for o in meshes if order.names_in_meshes([o])==['aGlass'])
        detail=next(o for o in meshes if order.names_in_meshes([o])==['mDetail'])
        # An extra object using the same material stays adjacent, without lost geometry.
        extra=glass.copy();extra.data=glass.data.copy();bpy.context.scene.collection.objects.link(extra)
        new=glass.copy();new.data=glass.data.copy();bpy.context.scene.collection.objects.link(new)
        mat=bpy.data.materials.new('newGlow');new.data.materials.clear();new.data.materials.append(mat)
        exported,_=self.export_materials(arm,[new,glass,detail,extra])
        self.assertEqual([s.name for s in exported.submeshes],['mDetail','aGlass','aGlass','newGlow'])
        self.assertEqual(exported.vertex_count,12)
        self.assertEqual(order.current_order(arm,[new,glass,detail,extra]),['mDetail','aGlass','newGlow'])

    def test_material_order_survives_blend_serialization(self):
        arm,obj,source=self.material_fixture();order.set_order(arm,['mDetail','zOpaque','aGlass'])
        path=self.directory/'order.blend'
        bpy.data.libraries.write(str(path),{arm,obj})
        with bpy.data.libraries.load(str(path)) as (source_data,target):target.objects=[arm.name]
        loaded=target.objects[0]
        self.assertEqual(order.read(loaded,order.ORDER),['mDetail','zOpaque','aGlass'])

    def test_material_order_invalid_metadata_does_not_overwrite_export(self):
        arm,obj,source=self.material_fixture();arm[order.ORDER]='bad json'
        path=self.directory/'untouched.skn';path.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'invalid saved SKN material order'):
            export_skn.write_skn_multi(path,[obj],arm)
        self.assertEqual(path.read_bytes(),b'keep')

    def test_material_order_reference_matches_no_names_leaves_order_unchanged(self):
        arm,obj,source=self.material_fixture();order.set_order(arm,['aGlass','mDetail','zOpaque'])
        before=arm[order.ORDER]
        ref=rs.Skn.new(positions=source.positions,normals=source.normals,uvs=source.uvs,
            blend_indices=source.blend_indices,blend_weights=source.blend_weights,indices=source.indices,
            submeshes=[rs.Submesh('unrelated',0,9,0,9)])
        path=self.directory/'unrelated.skn';ref.to_path(str(path))
        from types import SimpleNamespace
        messages=[]
        operator=SimpleNamespace(filepath=str(path),armature_name=arm.name,report=lambda level,text:messages.append(text))
        self.assertEqual(ui.LOL_OT_MaterialOrderReference.execute(operator,bpy.context),{'CANCELLED'})
        self.assertEqual(arm[order.ORDER],before)
        self.assertIn('No material names match',messages[0])
