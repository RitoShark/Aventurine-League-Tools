"""Optional real-asset audit, run in background Blender with factory startup.

Set AVENTURINE_SERAPHINE_SKN to the original SKN (matching SKL beside it).
AVENTURINE_AUDIT_OUTPUT optionally selects an output directory; otherwise use
an isolated temporary directory. Source assets and animations are never written.
Raw struct parsing validates binary fields independently of the Rust reader.
"""
import sys, json, struct, math, os, tempfile
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from test_backend import bpy, rs, import_skn, import_skl, export_skn, export_skl
from mathutils import Matrix, Quaternion, Vector
OUT = Path(os.environ.get('AVENTURINE_AUDIT_OUTPUT') or tempfile.mkdtemp(prefix='aventurine-format-audit-'))
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = Path(os.environ['AVENTURINE_SERAPHINE_SKN'])
if OUT.resolve() == SOURCE.parent.resolve():
    raise ValueError('Audit output must be separate from source assets')

def skn(path):
    raw = path.read_bytes()
    magic, major, minor, count = struct.unpack_from('<IHHI', raw)
    assert magic == 0x00112233
    submeshes=[]
    for i in range(count):
        n,sv,nv,si,ni=struct.unpack_from('<64s4I',raw,12+80*i)
        submeshes.append((n.rstrip(b'\0').decode(),sv,nv,si,ni))
    off=12+80*count
    if major==4:
        flags, ni, nv, stride, kind = struct.unpack_from('<5I',raw,off)
        bounds=struct.unpack_from('<10f',raw,off+20)
        off+=60
        if flags&1:
            prefix_len=struct.unpack_from('<H',raw,off)[0];off+=2+prefix_len
    else:
        ni,nv=struct.unpack_from('<2I',raw,off); off+=8
        flags=kind=0;stride=52;bounds=None
    indices=struct.unpack_from('<%dH'%ni,raw,off);off+=ni*2
    verts=[struct.unpack_from('<3f4B4f3f2f',raw,off+i*stride) for i in range(nv)]
    assert all(math.isfinite(x) for v in verts for x in v)
    lo=[min(v[a] for v in verts) for a in range(3)]
    hi=[max(v[a] for v in verts) for a in range(3)]
    if bounds:
        assert all(bounds[a]<=lo[a]+1e-4 and bounds[3+a]>=hi[a]-1e-4 for a in range(3))
        sphere_error=max(math.dist(v[:3],bounds[6:9])-bounds[9] for v in verts)
        assert sphere_error<1e-4
    assert sum(s[2] for s in submeshes)==nv
    assert sum(s[4] for s in submeshes)==ni
    for _,sv,nvsub,si,nisub in submeshes:
        assert all(sv<=idx+(sv if flags&2 else 0)<sv+nvsub for idx in indices[si:si+nisub])
    return {'version':[major,minor], 'vertices':nv,'indices':ni,'materials':len(submeshes),'flags':flags,'stride':stride,'vertex_type':kind,'bounds':bounds,'actual_min':lo,'actual_max':hi,'max_sphere_excess':sphere_error if bounds else None,'max_weight_sum_error':max(abs(sum(v[7:11])-1) for v in verts),'tail_bytes':len(raw)-(off+nv*stride)},verts

def elf(name):
    h=0
    for b in name.lower().encode():
        h=(h<<4)+b; g=h&0xf0000000
        if g:h^=g>>24
        h &= ~g
    return h

def skl(path):
    raw=path.read_bytes()
    size,magic,version,flags,count,ninf=struct.unpack_from('<IIIHHI',raw)
    assert size==len(raw) and magic==0x22fd4fc3 and version==0
    jo,lookup,inf,no,ao,names=struct.unpack_from('<6i',raw,20)
    def string(at):return raw[at:raw.index(b'\0',at)].decode() if at else ''
    joints={};local={};inverse={}
    def mat(f):
        t,s,q=f[:3],f[3:6],f[6:10]
        return Matrix.Translation(Vector(t))@Quaternion((q[3],*q[:3])).to_matrix().to_4x4()@Matrix.Diagonal((*s,1))
    for i in range(count):
        off=jo+i*100
        jf,jid,parent,pad,h,radius=struct.unpack_from('<HhhHIf',raw,off)
        f=struct.unpack_from('<20f',raw,off+16)
        name=string(off+96+struct.unpack_from('<i',raw,off+96)[0])
        assert h==elf(name)
        joints[jid]={'name':name,'parent':parent,'flags':jf,'radius':radius,'hash':h}
        local[jid]=mat(f[:10]);inverse[jid]=mat(f[10:])
    table=[struct.unpack_from('<hHI',raw,lookup+i*8) for i in range(count)]
    assert len(set(t[0] for t in table))==count
    assert all(joints[jid]['hash']==h for jid,_,h in table)
    assert [t[2] for t in table]==sorted(t[2] for t in table)
    palette=list(struct.unpack_from('<%dH'%ninf,raw,inf))
    assert all(i in joints for i in palette)
    globals={}
    def globalmat(i,chain=()):
        assert i not in chain
        if i not in globals:
            parent=joints[i]['parent']
            globals[i]=local[i] if parent==-1 else globalmat(parent,chain+(i,))@local[i]
        return globals[i]
    err=max(abs((globalmat(i)@inverse[i])[r][c]-(r==c)) for i in joints for r in range(4) for c in range(4))
    return {'joints':count,'influences':len(palette),'identity_palette':palette==list(range(count)), 'palette':palette,'flags':flags,'name':string(no),'asset':string(ao),'inverse_bind_max_error':err,'hash_lookup_valid':True},joints

bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
joints,palette=import_skl.read_skl(SOURCE.with_suffix('.skl'))
arm=import_skl.create_armature(joints)
obj=import_skn.create_mesh(*import_skn.read_skn(SOURCE),'Seraphine',arm,joints,palette)
bpy.context.view_layer.update()
report={}
for label,scale in [('roundtrip',1.0),('scaled',1.25)]:
    path=OUT/(label+'.skn')
    _,_,pal=export_skn.write_skn_multi(str(path),[obj],arm,model_scale=scale)
    export_skl.write_skl(str(path.with_suffix('.skl')),arm,influences=pal,model_scale=scale)
    a,vs=skn(path);b,js=skl(path.with_suffix('.skl'))
    assert b['inverse_bind_max_error']<0.001
    assert all(slot<len(pal) for v in vs for slot,w in zip(v[3:7],v[7:11]) if w>0)
    report[label]={'skn':a,'skl':b}
export_skl.write_skl(str(OUT/'standalone.skl'),arm)
report['original']={'skn':skn(SOURCE)[0],'skl':skl(SOURCE.with_suffix('.skl'))[0]}
assert skl(SOURCE.with_suffix('.skl'))[1] == skl(OUT/'roundtrip.skl')[1]
report['joint_metadata_and_hierarchy_match']=True
report['standalone_skl']=skl(OUT/'standalone.skl')[0]
report['standalone_palette_matches_source']=report['standalone_skl']['palette']==report['original']['skl']['palette']
# Verify vertex attributes and material triangle coverage against the original.
from collections import defaultdict, Counter
src = rs.Skn.from_path(str(SOURCE))
dst = rs.Skn.from_path(str(OUT/'roundtrip.skn'))
def buffers(skin, palette):
    p=list(struct.iter_unpack('<3f',skin.positions)); n=list(struct.iter_unpack('<3f',skin.normals))
    uv=list(struct.iter_unpack('<2f',skin.uvs)); w=list(struct.iter_unpack('<4f',skin.blend_weights))
    b=list(struct.iter_unpack('<4I',skin.blend_indices)); ii=[v[0] for v in struct.iter_unpack('<I',skin.indices)]
    weights=[{palette[slot]:weight for slot,weight in zip(slots,weights) if weight>0} for slots,weights in zip(b,w)]
    return p,n,uv,weights,ii
sp,sn,suv,sw,si=buffers(src,report['original']['skl']['palette'])
dp,dn,duv,dw,di=buffers(dst,report['roundtrip']['skl']['palette'])
def key(point):return tuple(round(v*1000) for v in point)
bypos=defaultdict(list)
for i,point in enumerate(sp):bypos[key(point)].append(i)
def difference(i,j):
    return max(*(abs(a-b) for a,b in zip(dp[i]+dn[i]+duv[i],sp[j]+sn[j]+suv[j])),
               *(abs(dw[i].get(b,0)-sw[j].get(b,0)) for b in set(dw[i])|set(sw[j])))
from itertools import product
matches=[]
for i,point in enumerate(dp):
    k=key(point)
    candidates=[j for offset in product((-1,0,1),repeat=3) for j in bypos[tuple(k[a]+offset[a] for a in range(3))]]
    matches.append(min(candidates,key=lambda j:difference(i,j)))
max_attribute_error=max(difference(i,j) for i,j in enumerate(matches))
def triangles(mesh,positions,indices):
    result=Counter()
    for sm in mesh.submeshes:
        for i in range(sm.index_start,sm.index_start+sm.index_count,3):
            result[(sm.name, tuple(sorted(key(positions[idx]) for idx in indices[i:i+3])))]+=1
    return result
worst=max(range(len(matches)),key=lambda i:difference(i,matches[i]))
j=matches[worst]

errors={'position':max(max(abs(a-b) for a,b in zip(dp[i],sp[j])) for i,j in enumerate(matches)),
        'normal':max(max(abs(a-b) for a,b in zip(dn[i],sn[j])) for i,j in enumerate(matches)),
        'uv':max(max(abs(a-b) for a,b in zip(duv[i],suv[j])) for i,j in enumerate(matches)),
        'weights':max(max(abs(dw[i].get(b,0)-sw[j].get(b,0)) for b in set(dw[i])|set(sw[j])) for i,j in enumerate(matches))}
from aventurine.io.export_skn import _corner_normals
imported_normals=_corner_normals(obj.data)
imported_normal_error=max(max(abs(a-b) for a,b in zip((n.x,-n.z,n.y),sn[loop.vertex_index])) for n,loop in zip(imported_normals,obj.data.loops))
errors['normal_after_blender_import']=imported_normal_error
errors['max_normal_angle_degrees']=max(math.degrees(math.acos(max(-1,min(1,Vector(dn[i]).normalized().dot(Vector(sn[j]).normalized()))))) for i,j in enumerate(matches))
assert errors['position']<1e-4 and errors['uv']<1e-6 and errors['weights']<1e-6
print('ERRORS',errors)
report['attribute_errors']=errors
assert triangles(src,sp,si)==triangles(dst,[sp[j] for j in matches],di)
report['source_comparison']={'max_vertex_attribute_error':max_attribute_error,'material_triangles_match':True}
(OUT/'audit.json').write_text(json.dumps(report,indent=2))
print('AUDIT PASSED', json.dumps({'source_comparison':report['source_comparison'],'standalone_palette_matches_source':report['standalone_palette_matches_source']}))

print('Report:', OUT/'audit.json')
