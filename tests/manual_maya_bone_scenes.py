"""Import exported fixtures through unmodified lol_maya, verify and save Maya scenes."""
import argparse,json,math,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--lolmaya',type=Path,required=True)
p.add_argument('--bake-reference',type=Path,help='Riot ANM to sample through Maya before building Blender fixtures')
p.add_argument('--source-skl',type=Path)
a=p.parse_args();a.root.mkdir(parents=True,exist_ok=True)
import maya.standalone
maya.standalone.initialize(name='python')
from maya import cmds
from maya.api import OpenMaya as om
sys.path.insert(0,str(a.lolmaya));import lol_maya as lol
if a.bake_reference:
    if not a.source_skl:
        p.error('--source-skl is required with --bake-reference')
    cmds.file(new=True,force=True)
    skl=lol.SKL();skl.read(a.source_skl.as_posix());skl.flip();skl.load()
    clip=lol.ANM();clip.read(a.bake_reference.as_posix());clip.flip()
    cmds.currentUnit(time='ntsc' if clip.fps < 59 else 'ntscf')
    cmds.playbackOptions(min=1,max=round(clip.duration*clip.fps),animationStartTime=0,animationEndTime=round(clip.duration*clip.fps))
    cmds.currentTime(0);clip.load(delchannel=True)
    baked=lol.ANM();baked.dump();baked.flip();baked.write((a.root/'maya_baked_reference.anm').as_posix())
    print('Maya reference baked:',baked.frame_count,'frames')
    maya.standalone.uninitialize()
    sys.exit(0)
manifest=json.loads((a.root/'manifest.json').read_text());report={'maya':cmds.about(version=True),'cases':[]}
for case in manifest['cases']:
    cmds.file(new=True,force=True);cmds.currentUnit(linear='cm',time='ntsc');cmds.currentTime(0)
    skl=lol.SKL();skl.read((a.root/case['skl']).as_posix());skl.flip();skl.load()
    skn=lol.SKN();skn.read((a.root/case['skn']).as_posix());skn.flip();skn.load(skl,sepmat=False)
    cmds.playbackOptions(min=1,max=case['frames'],animationStartTime=0,animationEndTime=case['frames'])
    cmds.currentTime(0)
    anm=lol.ANM();anm.read((a.root/case['anm']).as_posix());anm.flip();anm.load(delchannel=True)
    meshes=cmds.ls(type='mesh',noIntermediate=True,long=True)
    assert len(meshes)==1,meshes
    sel=om.MSelectionList();sel.add(meshes[0]);meshfn=om.MFnMesh(sel.getDagPath(0))
    expected=json.loads((a.root/case['expected']).read_text())
    max_head=0.;max_linear=0.;max_vertex=0.;worst_joint=None;worst_vertex=None
    poses={}
    for sample in expected:
        frame=sample['frame'];cmds.currentTime(frame,edit=True)
        points=meshfn.getPoints(om.MSpace.kWorld)
        pose={}
        for name,goal in sample['joints'].items():
            actual=cmds.xform(name,q=True,matrix=True,worldSpace=True)
            # Maya uses row vectors; Blender capture uses column vectors.
            actual=[actual[c*4+r] for r in range(4) for c in range(4)]
            head_error=math.sqrt(sum((actual[i]-goal[i])**2 for i in (3,7,11)))
            linear=max(abs(actual[r*4+c]-goal[r*4+c]) for r in range(3) for c in range(3))
            if head_error>max_head:max_head=head_error;worst_joint=[name,frame]
            max_linear=max(max_linear,linear)
            pose[name]=[actual[i] for i in (3,7,11)]
        poses[str(frame)]=pose
        for index,goal in sample['vertices'].items():
            point=points[int(index)];error=math.sqrt(sum((point[i]-goal[i])**2 for i in range(3)))
            if error>max_vertex:max_vertex=error;worst_vertex=[int(index),frame]
    result={'name':case['name'],'frames_checked':len(expected),'joint_count':len(skl.joints),
        'vertex_probes':case['vertex_probes'],'max_joint_head_error_cm':max_head,'max_joint_linear_error':max_linear,
        'max_mesh_vertex_error_cm':max_vertex,'worst_joint':worst_joint,'worst_vertex':worst_vertex,
        'animation':'Blender exported full clip' if case['name'] in ('02_wider_shoulders','03_animated_cape') else 'Untouched Riot run clip'}
    report['cases'].append(result);print('CASE RESULT',json.dumps(result),flush=True)
    (a.root/case['name']/'maya_joint_positions.json').write_text(json.dumps(poses))
    # Materials are presentation only; validation above uses actual skin deformation.
    for mat,relative in case['textures'].items():
        if not cmds.objExists(mat):continue
        texture=cmds.shadingNode('file',asTexture=True,name=mat+'_Diffuse')
        place=cmds.shadingNode('place2dTexture',asUtility=True)
        cmds.connectAttr(place+'.outUV',texture+'.uvCoord',force=True)
        cmds.connectAttr(place+'.outUvFilterSize',texture+'.uvFilterSize',force=True)
        cmds.setAttr(texture+'.fileTextureName',str((a.root/relative).resolve()).replace('\\','/'),type='string')
        cmds.connectAttr(texture+'.outColor',mat+'.color',force=True)
        if 'hair' in relative.lower():cmds.connectAttr(texture+'.outTransparency',mat+'.transparency',force=True)
        cmds.setAttr(mat+'.diffuse',1)
        cmds.setAttr(mat+'.ambientColor',.25,.25,.25,type='double3')
    if cmds.objExists('AuditCape'):
        cmds.setAttr('AuditCape.color',.05,.72,.28,type='double3')
        cmds.setAttr('AuditCape.ambientColor',.1,.25,.12,type='double3')
    layer=cmds.createDisplayLayer(cmds.ls(type='joint'),name='Skeletons',noRecurse=True)
    cmds.setAttr(layer+'.visibility',True)
    cmds.currentTime(9);cmds.select(clear=True)
    bbox=cmds.exactWorldBoundingBox(meshes);cx=(bbox[0]+bbox[3])/2;cy=(bbox[1]+bbox[4])/2;cz=(bbox[2]+bbox[5])/2
    cmds.setAttr('persp.translate',cx,cy+60,cz+440,type='double3');cmds.setAttr('persp.rotate',-8,0,0,type='double3')
    cmds.file(rename=str(a.root/case['name']/'review.ma'));cmds.file(save=True,type='mayaAscii')
report['comparison_basis']='Blender evaluated poses; base motion sampled through Maya to isolate compressed-curve interpolation'
report['passed']=all(c['max_joint_head_error_cm'] < .01 and c['max_mesh_vertex_error_cm'] < .01 and c['max_joint_linear_error'] < .0001 for c in report['cases'])
(a.root/'maya_validation.json').write_text(json.dumps(report,indent=2))
# Put the four separately inspectable scenes together for a single open Maya window.
cmds.file(new=True,force=True);cmds.currentUnit(linear='cm',time='ntsc')
labels=['REFERENCE - RIOT RUN','1 - ADDED CAPE / RIOT RUN','2 - WIDER SHOULDERS / EXPORTED RUN','3 - ANIMATED CAPE / BLENDER RUN']
placements=[(-170,260,0),(170,260,0),(-170,0,0),(170,0,0)]
for case,label,pos in zip(manifest['cases'],labels,placements):
    ns='case'+case['name'][:2]
    nodes=cmds.file(str(a.root/case['name']/'review.ma'),i=True,namespace=ns,returnNewNodes=True)
    roots=[n for n in cmds.ls(assemblies=True,long=True) if n.split('|')[-1].startswith(ns+':') and not cmds.listRelatives(n,shapes=True,type='camera')]
    group=cmds.group(roots,name=ns+'_Review')
    # The skin output follows joint world transforms already. Avoid applying
    # the comparison group's translation a second time to the skinned mesh.
    for shape in cmds.listRelatives(group,allDescendents=True,type='mesh',fullPath=True) or []:
        if cmds.getAttr(shape+'.intermediateObject'):continue
        if not cmds.ls(cmds.listHistory(shape) or [],type='skinCluster'):continue
        transform=cmds.listRelatives(shape,parent=True,fullPath=True)[0]
        cmds.setAttr(transform+'.inheritsTransform',False)
    cmds.setAttr(group+'.translate',*pos,type='double3')
    locator=cmds.spaceLocator(name=ns+'_Label')[0];cmds.setAttr(locator+'.translate',pos[0],pos[1]+240,0,type='double3')
    annotation=cmds.annotate(locator,text=label,point=(pos[0],pos[1]+240,0));cmds.setAttr(annotation+'.displayArrow',False)
    cmds.setAttr(locator+'.visibility',False)
layout=[]
for case in manifest['cases']:
    ns='case'+case['name'][:2]
    offset=cmds.getAttr(ns+'_Review.translate')[0]
    shape=next(m for m in cmds.ls(ns+':*',type='mesh',long=True) if not cmds.getAttr(m+'.intermediateObject'))
    selection=om.MSelectionList();selection.add(shape);meshfn=om.MFnMesh(selection.getDagPath(0))
    mesh_error=joint_error=0.
    samples=json.loads((a.root/case['expected']).read_text())
    for sample in samples:
        cmds.currentTime(sample['frame'],edit=True)
        points=meshfn.getPoints(om.MSpace.kWorld)
        for index,expected in sample['vertices'].items():
            point=points[int(index)]
            mesh_error=max(mesh_error,math.dist([point[i]-offset[i] for i in range(3)],expected))
        for name,matrix in sample['joints'].items():
            point=cmds.xform(ns+':'+name,q=True,worldSpace=True,translation=True)
            joint_error=max(joint_error,math.dist([point[i]-offset[i] for i in range(3)],[matrix[i] for i in (3,7,11)]))
    layout.append(dict(name=case['name'],frames=len(samples),max_mesh_error_cm=mesh_error,max_joint_error_cm=joint_error))
report['assembled_scene']=layout
report['passed']=report['passed'] and all(c['max_mesh_error_cm']<.01 and c['max_joint_error_cm']<.01 for c in layout)
(a.root/'maya_validation.json').write_text(json.dumps(report,indent=2))
cmds.playbackOptions(min=1,max=manifest['cases'][0]['frames'],animationStartTime=0,animationEndTime=manifest['cases'][0]['frames'],playbackSpeed=1)
cmds.currentTime(9);cmds.select(clear=True)
cmds.setAttr('persp.translate',0,290,1050,type='double3');cmds.setAttr('persp.rotate',-4,0,0,type='double3')
cmds.setAttr('perspShape.focalLength',40)
cmds.file(rename=str(a.root/'ALL_TESTS.ma'));cmds.file(save=True,type='mayaAscii')
print('MAYA SCENES COMPLETE',a.root,flush=True)
maya.standalone.uninitialize()
if not report['passed']:
    raise AssertionError('Maya comparison exceeded tolerance; see maya_validation.json')
