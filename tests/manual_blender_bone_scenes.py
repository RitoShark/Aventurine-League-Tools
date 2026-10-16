"""Build the requested Blender -> League files -> Maya visual fixtures."""
import argparse, importlib.util, json, math, shutil, sys
from pathlib import Path
import bpy
from mathutils import Matrix, Quaternion, Vector, kdtree
argv = sys.argv[sys.argv.index('--')+1:]
p=argparse.ArgumentParser()
p.add_argument('--addon',type=Path,required=True);p.add_argument('--assets',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--reference-anm',type=Path)
a=p.parse_args(argv);a.output.mkdir(parents=True,exist_ok=True)
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('aventurine',a.addon/'__init__.py')
addon=importlib.util.module_from_spec(spec);sys.modules['aventurine']=addon;spec.loader.exec_module(addon)
from aventurine.io import import_skn, import_anm, export_skn, export_anm
from aventurine.io.skeleton import ExportPlan, P
from aventurine.tools.offset_bones import add_offset_parent, apply_offset_bind, add_offset_companion, find_snap_roots
class Reporter:
    def report(self,level,text): print(level,text)
op=Reporter();pi=P.inverted();F=Matrix.Diagonal((-1,1,1,1))
source_skn=a.assets/'seraphine_base.skn';source_anm=a.assets/'animations/seraphin_run.anm'
shutil.copy2(source_anm,a.output/'riot_run.anm')
textures=a.output/'textures';textures.mkdir(exist_ok=True)
manifest={'champion':'Seraphine base','animation':'seraphin_run.anm','blender':bpy.app.version_string,'base_motion':str(a.reference_anm or source_anm),'cases':[]}

def clear():
    if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)

def cape(arm):
    bpy.context.view_layer.objects.active=arm;bpy.ops.object.mode_set(mode='EDIT')
    bones=arm.data.edit_bones;parent=bones['Spine2'];top=parent.head+Vector((0,.25,.12))
    names=[]
    for i in range(3):
        b=bones.new('Cape_'+str(i+1));b.head=top+Vector((0,.06*i,-.32*i));b.tail=top+Vector((0,.06*(i+1),-.32*(i+1)));b.parent=parent if i==0 else bones[names[-1]];names.append(b.name)
    for side in (-1,1):
        b=bones.new('Cape_Pin_'+('L' if side<0 else 'R'));b.head=top+Vector((.2*side,0,0));b.tail=b.head+Vector((0,.1,-.1));b.parent=parent
    # Exercise actual Shift+D metadata copying as well as fresh additions.
    bpy.ops.armature.select_all(action='DESELECT');original=bones['Spine1'];original.select=original.select_head=original.select_tail=True;bones.active=original
    bpy.ops.armature.duplicate();extra=bones.active;extra.name='Cape_Charm';extra.head+=Vector((0,.4,0));extra.tail+=Vector((0,.4,0))
    bpy.ops.object.mode_set(mode='OBJECT')
    verts=[];faces=[];rows=9;columns=7
    for row in range(rows):
        u=row/(rows-1)
        for col in range(columns):
            verts.append(tuple(top+Vector(((col/(columns-1)-.5)*(.65+.4*u), .08+.18*u, -.96*u))))
    for row in range(rows-1):
        for col in range(columns-1):
            n=row*columns+col;faces.append((n,n+1,n+columns+1,n+columns))
    data=bpy.data.meshes.new('AuditCape');data.from_pydata(verts,[],faces);data.update()
    uv=data.uv_layers.new(name='UVMap')
    for loop in data.loops:
        row,col=divmod(loop.vertex_index,columns);uv.data[loop.index].uv=(col/(columns-1),row/(rows-1))
    obj=bpy.data.objects.new('AuditCape',data);bpy.context.collection.objects.link(obj);obj.parent=arm
    mod=obj.modifiers.new('Armature','ARMATURE');mod.object=arm
    groups=[obj.vertex_groups.new(name=n) for n in names]
    for row in range(rows):
        t=row/(rows-1)*2;low=min(2,int(t));high=min(2,low+1);fraction=t-low
        ids=list(range(row*columns,(row+1)*columns));groups[low].add(ids,1-fraction,'REPLACE')
        if high!=low and fraction:groups[high].add(ids,fraction,'REPLACE')
    mat=bpy.data.materials.new('AuditCape');mat.diffuse_color=(.06,.65,.32,1);mat['lol_material_name']='AuditCape';data.materials.append(mat)
    return names

for case in ('00_stock_reference','01_added_cape','02_wider_shoulders','03_animated_cape'):
    clear();folder=a.output/case;folder.mkdir(exist_ok=True)
    assert import_skn.load(op,bpy.context,str(source_skn),split_by_material=True,auto_load_textures=True,bone_orient='VISUAL')=={'FINISHED'}
    arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE');bpy.context.view_layer.objects.active=arm
    animation=import_anm.read_anm(a.reference_anm or source_anm);arm.animation_data_create();arm.animation_data.action=bpy.data.actions.new(case)
    import_anm.apply_anm(animation,arm);count=animation.frame_count
    extras=[]
    if 'cape' in case:extras=cape(arm)
    if case=='02_wider_shoulders':
        bpy.context.scene.frame_set(0)
        for name in ('L_Clavicle','R_Clavicle'):
            control=add_offset_parent(arm,name)
            # This fixture is explicitly the ANM-rewrite experiment, not stock-clip compatibility.
            for root_name in find_snap_roots(arm,name):
                add_offset_companion(arm,control.name,root_name)
            sign=1 if arm.data.bones[name].head_local.x>0 else -1
            control.location=control.bone.matrix_local.to_3x3().inverted()@Vector((.18*sign,0,0))
            extras.append(control.name)
        bpy.ops.object.mode_set(mode='OBJECT')
        apply_offset_bind(arm)
    if case=='03_animated_cape':
        for name in extras:
            pb=arm.pose.bones[name];pb.rotation_mode='QUATERNION';pb.rotation_quaternion=Quaternion();pb.keyframe_insert(data_path='rotation_quaternion',frame=0)
        for frame in range(1,count+1):
            bpy.context.scene.frame_set(frame)
            for i,name in enumerate(extras):
                pb=arm.pose.bones[name]
                angle=.35*math.sin((frame-1)/max(1,count-1)*2*math.pi-i*.7)
                pb.rotation_quaternion=Quaternion((1,0,0),angle);pb.keyframe_insert(data_path='rotation_quaternion',frame=frame)
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:obj.select_set(True)
    bpy.context.view_layer.objects.active=arm
    bpy.context.scene.frame_start=1;bpy.context.scene.frame_end=count;bpy.context.scene.frame_set(0)
    hierarchy={b.name:b.parent.name if b.parent else None for b in arm.pose.bones}
    assert export_skn.save(op,bpy.context,str(folder/'seraphine.skn'),target_armature=arm)=={'FINISHED'}
    assert hierarchy=={b.name:b.parent.name if b.parent else None for b in arm.pose.bones}
    export_anm.write_anm(str(folder/'blender_run.anm'),arm,fps=animation.fps,use_scene_range=True)
    plan=ExportPlan(arm)
    assert [b.name for b in plan.bones[:len(plan.owners)]]==[plan.owners[i].name for i in sorted(plan.owners)]
    # Capture texture files independently of the original export folder.
    texture_map={}
    for obj in meshes:
        for mat in obj.data.materials:
            if mat and mat.use_nodes:
                images=[n.image for n in mat.node_tree.nodes if n.type=='TEX_IMAGE' and n.image]
                if images:
                    image=images[0];name=Path(image.name).stem+'.png';dest=textures/name
                    image.save_render(str(dest));image.pack();texture_map[mat.get('lol_material_name',mat.name)]='textures/'+name
    # Map a spread of exported vertices back to Blender vertices at the mesh bind.
    bpy.context.scene.frame_set(0);bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
    source_points=[];source_ids=[]
    for obj in meshes:
        ev=obj.evaluated_get(dg);mesh=ev.to_mesh() if case=='02_wider_shoulders' else obj.data
        for v in mesh.vertices:
            point=pi@(obj.matrix_world@v.co)*100;source_points.append(point);source_ids.append((obj.name,v.index))
        if case=='02_wider_shoulders':ev.to_mesh_clear()
    kd=kdtree.KDTree(len(source_points))
    for i,point in enumerate(source_points):kd.insert(point,i)
    kd.balance()
    _,exported,submeshes=import_skn.read_skn(folder/'seraphine.skn')
    selected=set(range(0,len(exported),max(1,len(exported)//180)))
    for sm in submeshes:
        if 'AuditCape' in sm.name:selected.update(range(sm.vertex_start,sm.vertex_start+sm.vertex_count))
    from aventurine.io import import_skl
    exported_joints, palette = import_skl.read_skl(folder/'seraphine.skl')
    hand_indices = {'L': [], 'R': []}
    for side in hand_indices:
        subtree = {side+'_Clavicle'} | {b.name for b in arm.pose.bones[side+'_Clavicle'].children_recursive}
        subtree.add(side+'_Hand_Snap')
        for index, vertex in enumerate(exported):
            weighted = [(exported_joints[palette[slot]].name, weight)
                        for slot, weight in zip(vertex.influences, vertex.weights) if weight > 1e-6]
            if (weighted and all(name in subtree for name, weight in weighted)
                    and any(any(part in name for part in ('Hand','Thumb','Index','Middle','Ring','Pinky'))
                            for name, weight in weighted if weight > .1)):
                hand_indices[side].append(index)
        selected.update(hand_indices[side])
    mapped={};max_match=0
    for index in sorted(selected):
        _,found,distance=kd.find(Vector(exported[index].position));max_match=max(max_match,distance);mapped[index]=source_ids[found]
    assert max_match<.005, max_match
    samples=[]
    for frame in range(0,count+1):
        bpy.context.scene.frame_set(frame);bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
        globals=plan.globals(pose=True)
        matrices={}
        for name,m in globals.items():
            mm=F@m@F;mm.translation*=100;matrices[plan.names[name]]=[v for row in mm for v in row]
        verts={};cache={}
        for obj in meshes:
            ev=obj.evaluated_get(dg);mesh=ev.to_mesh();cache[obj.name]=[tuple(F@(pi@(obj.matrix_world@v.co))*100) for v in mesh.vertices];ev.to_mesh_clear()
        for index,(name,vi) in mapped.items():verts[str(index)]=cache[name][vi]
        samples.append({'frame':frame,'joints':matrices,'vertices':verts})
    (folder/'blender_expected.json').write_text(json.dumps(samples))
    # A readable Blender scene is useful when inspecting the authoring step.
    bpy.context.scene.frame_set(1);bpy.ops.object.select_all(action='DESELECT');arm.select_set(True);bpy.context.view_layer.objects.active=arm
    arm.show_in_front=True
    bpy.ops.wm.save_as_mainfile(filepath=str(folder/'source.blend'))
    manifest['cases'].append({'name':case,'frames':count,'extras':extras,'skn':case+'/seraphine.skn','skl':case+'/seraphine.skl',
        'anm':case+'/blender_run.anm' if case in ('02_wider_shoulders','03_animated_cape') else 'riot_run.anm',
        'expected':case+'/blender_expected.json','textures':texture_map,'hand_indices':hand_indices,'vertex_probes':len(mapped),'max_bind_match_error':max_match})
(a.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
print('BLENDER FIXTURES COMPLETE',a.output)