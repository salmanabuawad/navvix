import argparse, json, math
from pathlib import Path
import ezdxf
import numpy as np
import matplotlib.pyplot as plt
from ezdxf import units

def pts(e):
    out=[]
    try:
        t=e.dxftype()
        if t=="LINE":
            out=[(e.dxf.start.x,e.dxf.start.y),(e.dxf.end.x,e.dxf.end.y)]
        elif t=="LWPOLYLINE":
            out=[(p[0],p[1]) for p in e.get_points()]
        elif t=="POLYLINE":
            out=[(v.dxf.location.x,v.dxf.location.y) for v in e.vertices]
        elif t=="DIMENSION":
            for a in ["defpoint","defpoint2","defpoint3"]:
                if hasattr(e.dxf,a):
                    p=getattr(e.dxf,a); out.append((p.x,p.y))
        elif t in ("TEXT","MTEXT","INSERT"):
            p=e.dxf.insert; out=[(p.x,p.y)]
    except Exception:
        pass
    return out

def bbox(ps):
    xs=[p[0] for p in ps]; ys=[p[1] for p in ps]
    return (min(xs),min(ys),max(xs),max(ys))

def intersects(a,b):
    return not (a[2]<b[0] or a[0]>b[2] or a[3]<b[1] or a[1]>b[3])

def copy_layers(src,dst):
    for l in src.layers:
        try:
            if l.dxf.name not in dst.layers:
                dst.layers.new(l.dxf.name, dxfattribs={"color": l.dxf.color})
        except Exception:
            pass

def isolate(input_dxf, output_dxf):
    doc=ezdxf.readfile(str(input_dxf)); msp=doc.modelspace()
    rec=[]
    for e in msp:
        p=pts(e)
        if not p: continue
        b=bbox(p)
        rec.append({"e":e,"t":e.dxftype(),"b":b,"c":((b[0]+b[2])/2,(b[1]+b[3])/2),"w":b[2]-b[0],"h":b[3]-b[1]})
    allp=[]
    for r in rec:
        b=r["b"]; allp += [(b[0],b[1]),(b[2],b[3])]
    gb=bbox(allp); gw=gb[2]-gb[0]; gh=gb[3]-gb[1]
    seeds=[r for r in rec if r["t"] in ("LINE","LWPOLYLINE","POLYLINE","INSERT") and max(r["w"],r["h"])>1]
    centers=np.array([r["c"] for r in seeds],float)
    eps=max(200.0,min(gw,gh)*0.025)
    vis=np.zeros(len(seeds),bool); clusters=[]
    for i in range(len(seeds)):
        if vis[i]: continue
        st=[i]; vis[i]=True; comp=[]
        while st:
            j=st.pop(); comp.append(j)
            d=np.sqrt(((centers-centers[j])**2).sum(axis=1))
            for k in np.where(d<=eps)[0]:
                if not vis[k]:
                    vis[k]=True; st.append(int(k))
        clusters.append(comp)
    infos=[]
    for cid,comp in enumerate(clusters):
        xs=[]; ys=[]; types={}
        for idx in comp:
            r=seeds[idx]; b=r["b"]
            xs += [b[0],b[2]]; ys += [b[1],b[3]]
            types[r["t"]]=types.get(r["t"],0)+1
        b=(min(xs),min(ys),max(xs),max(ys)); bw=b[2]-b[0]; bh=b[3]-b[1]
        cx=(b[0]+b[2])/2; cy=(b[1]+b[3])/2
        nx=(cx-gb[0])/max(1,gw); ny=(cy-gb[1])/max(1,gh)
        aspect=max(bw,bh)/max(1,min(bw,bh))
        complexity=types.get("LINE",0)+1.8*types.get("LWPOLYLINE",0)+1.8*types.get("POLYLINE",0)
        table_penalty=0.12 if nx>0.70 or aspect>3.2 else 1
        frame_penalty=0.08 if bw>gw*.70 and bh>gh*.65 else 1
        central=max(.15,1.3-abs(nx-.43)*2.2-abs(ny-.52)*1.1)
        area=(bw*bh)/max(1,gw*gh)
        score=complexity*central*max(.2,min(1.5,area/.12))*table_penalty*frame_penalty
        if len(comp)<12: score*=.1
        infos.append({"id":cid,"bbox":b,"score":score,"types":types,"count":len(comp)})
    infos.sort(key=lambda x:x["score"], reverse=True)
    mb=infos[0]["bbox"]; mw=mb[2]-mb[0]; mh=mb[3]-mb[1]
    pad=max(100,min(mw,mh)*.07)
    eb=(mb[0]-pad,mb[1]-pad,mb[2]+pad,mb[3]+pad)
    nd=ezdxf.new(dxfversion=doc.dxfversion); nd.units=doc.units; copy_layers(doc,nd); nm=nd.modelspace()
    copied=0
    for r in rec:
        if intersects(r["b"],eb):
            if r["w"]>gw*.70 and r["h"]>gh*.50: continue
            try: nm.add_entity(r["e"].copy()); copied+=1
            except Exception: pass
    nd.saveas(str(output_dxf))
    return {"global_bbox":gb,"main_bbox":mb,"expanded_bbox":eb,"copied":copied,"clusters":infos[:10]}

def extract_segments(doc):
    seg=[]
    for e in doc.modelspace():
        def add(x1,y1,x2,y2):
            dx=x2-x1; dy=y2-y1; L=math.hypot(dx,dy)
            if L<50: return
            if abs(dx)>=abs(dy)*8: ori="h"
            elif abs(dy)>=abs(dx)*8: ori="v"
            else: return
            seg.append((x1,y1,x2,y2,ori,L))
        try:
            t=e.dxftype()
            if t=="LINE":
                a,b=e.dxf.start,e.dxf.end; add(a.x,a.y,b.x,b.y)
            elif t=="LWPOLYLINE":
                p=[(q[0],q[1]) for q in e.get_points()]
                if e.closed and p: p.append(p[0])
                for a,b in zip(p,p[1:]): add(a[0],a[1],b[0],b[1])
            elif t=="POLYLINE":
                p=[(v.dxf.location.x,v.dxf.location.y) for v in e.vertices]
                if e.is_closed and p: p.append(p[0])
                for a,b in zip(p,p[1:]): add(a[0],a[1],b[0],b[1])
        except Exception: pass
    return seg

def cluster(vals,tol):
    vals=sorted(vals)
    if not vals: return []
    groups=[[vals[0]]]
    for v in vals[1:]:
        if abs(v-groups[-1][-1])<=tol: groups[-1].append(v)
        else: groups.append([v])
    return [sum(g)/len(g) for g in groups]

def prune(vals,mn,mx,maxn=10):
    vals=sorted(set([mn]+[v for v in vals if mn<v<mx]+[mx]))
    mingap=(mx-mn)*.045
    out=[vals[0]]
    for v in vals[1:]:
        if v-out[-1]>=mingap: out.append(v)
    vals=out
    if len(vals)>maxn:
        idx=[round(i*(len(vals)-1)/(maxn-1)) for i in range(maxn)]
        vals=[vals[int(i)] for i in idx]
    return vals

def add_dim(msp,p1,p2,base,angle,layer,style,minlen,created,kind):
    L=math.hypot(p2[0]-p1[0],p2[1]-p1[1])
    if L<minlen: return
    d=msp.add_linear_dim(base=base,p1=p1,p2=p2,angle=angle,dimstyle=style,dxfattribs={"layer":layer})
    d.render(); created.append({"kind":kind,"length":L,"p1":p1,"p2":p2,"base":base})

def generate(isolated, output):
    doc=ezdxf.readfile(str(isolated)); msp=doc.modelspace()
    seg=extract_segments(doc)
    xs=[]; ys=[]
    for x1,y1,x2,y2,ori,L in seg:
        xs += [x1,x2]; ys += [y1,y2]
    xlo,xhi=np.percentile(xs,[1,99]); ylo,yhi=np.percentile(ys,[1,99])
    xs2=[x for x in xs if xlo<=x<=xhi]; ys2=[y for y in ys if ylo<=y<=yhi]
    minx,miny,maxx,maxy=min(xs2),min(ys2),max(xs2),max(ys2)
    w=maxx-minx; h=maxy-miny; base=min(w,h)
    tol=max(20,base*.006)
    xv=[]; yv=[]
    for x1,y1,x2,y2,ori,L in seg:
        if not (minx-100<=x1<=maxx+100 and minx-100<=x2<=maxx+100 and miny-100<=y1<=maxy+100 and miny-100<=y2<=maxy+100): continue
        if ori=="h":
            xv += [x1,x2]; yv.append((y1+y2)/2)
        else:
            yv += [y1,y2]; xv.append((x1+x2)/2)
    xa=prune(cluster(xv,tol),minx,maxx,10)
    ya=prune(cluster(yv,tol),miny,maxy,10)
    doc.units=units.MM
    for layer in ["NAVVIX_V11_EXTERNAL","NAVVIX_V11_INTERNAL"]:
        if layer not in doc.layers: doc.layers.new(layer,dxfattribs={"color":7})
    if "NAVVIX_CLOSED_FILLED_ARROW" not in doc.blocks:
        blk=doc.blocks.new("NAVVIX_CLOSED_FILLED_ARROW"); blk.add_solid([(0,0),(1,.28),(1,-.28),(0,0)])
    style="NAVVIX_V11_DIM"; ds=doc.dimstyles.get(style) if style in doc.dimstyles else doc.dimstyles.new(style)
    ds.dxf.dimblk=ds.dxf.dimblk1=ds.dxf.dimblk2="NAVVIX_CLOSED_FILLED_ARROW"
    ds.dxf.dimasz=max(20,base*.008); ds.dxf.dimtxt=max(30,base*.010); ds.dxf.dimexo=max(8,base*.002); ds.dxf.dimexe=max(20,base*.006); ds.dxf.dimgap=max(8,base*.002); ds.dxf.dimtad=1; ds.dxf.dimjust=0
    created=[]; chain=max(250,base*.075); overall=max(500,base*.14); minlen=max(250,base*.035)
    for a,b in zip(xa,xa[1:]):
        add_dim(msp,(a,maxy),(b,maxy),((a+b)/2,maxy+chain),0,"NAVVIX_V11_EXTERNAL",style,minlen,created,"top_chain")
        add_dim(msp,(a,miny),(b,miny),((a+b)/2,miny-chain),0,"NAVVIX_V11_EXTERNAL",style,minlen,created,"bottom_chain")
    add_dim(msp,(minx,maxy),(maxx,maxy),((minx+maxx)/2,maxy+overall),0,"NAVVIX_V11_EXTERNAL",style,minlen,created,"top_overall")
    add_dim(msp,(minx,miny),(maxx,miny),((minx+maxx)/2,miny-overall),0,"NAVVIX_V11_EXTERNAL",style,minlen,created,"bottom_overall")
    for a,b in zip(ya,ya[1:]):
        add_dim(msp,(minx,a),(minx,b),(minx-chain,(a+b)/2),90,"NAVVIX_V11_EXTERNAL",style,minlen,created,"left_chain")
        add_dim(msp,(maxx,a),(maxx,b),(maxx+chain,(a+b)/2),90,"NAVVIX_V11_EXTERNAL",style,minlen,created,"right_chain")
    add_dim(msp,(minx,miny),(minx,maxy),(minx-overall,(miny+maxy)/2),90,"NAVVIX_V11_EXTERNAL",style,minlen,created,"left_overall")
    add_dim(msp,(maxx,miny),(maxx,maxy),(maxx+overall,(miny+maxy)/2),90,"NAVVIX_V11_EXTERNAL",style,minlen,created,"right_overall")
    # semantic internal cells
    internal=0
    cells=[]
    for a,b in zip(xa,xa[1:]):
        for c,d in zip(ya,ya[1:]):
            cw=b-a; ch=d-c; ar=(cw*ch)/max(1,w*h); asp=max(cw,ch)/max(1,min(cw,ch)); nx=((a+b)/2-minx)/w; ny=((c+d)/2-miny)/h
            kind="skip"
            if .14<nx<.86 and .14<ny<.86 and .025<=ar<=.18 and asp<3: kind="room"
            elif .14<nx<.86 and .14<ny<.86 and .025<=ar<=.14 and asp>=3: kind="corridor"
            cells.append({"bbox":(a,c,b,d),"kind":kind,"area_ratio":ar,"aspect":asp})
    for cell in [c for c in cells if c["kind"] in ("room","corridor")][:5]:
        a,c,b,d=cell["bbox"]; cw=b-a; ch=d-c; off=max(100,min(cw,ch)*.12)
        if internal>=10: break
        if cell["kind"]=="room":
            add_dim(msp,(a+50,c+off),(b-50,c+off),((a+b)/2,c+off),0,"NAVVIX_V11_INTERNAL",style,minlen*.7,created,"room_width"); internal+=1
            add_dim(msp,(b-off,c+50),(b-off,d-50),(b-off,(c+d)/2),90,"NAVVIX_V11_INTERNAL",style,minlen*.7,created,"room_height"); internal+=1
        else:
            if cw<ch: add_dim(msp,(a+50,c+off),(b-50,c+off),((a+b)/2,c+off),0,"NAVVIX_V11_INTERNAL",style,minlen*.7,created,"corridor_width")
            else: add_dim(msp,(b-off,c+50),(b-off,d-50),(b-off,(c+d)/2),90,"NAVVIX_V11_INTERNAL",style,minlen*.7,created,"corridor_width")
            internal+=1
    doc.saveas(str(output))
    return {"bbox":(minx,miny,maxx,maxy),"x_axes":xa,"y_axes":ya,"segments":len(seg),"dimensions":created,"internal_count":internal,"cells":cells}

def preview(dxf,png,pdf,crop):
    doc=ezdxf.readfile(str(dxf)); msp=doc.modelspace()
    minx,miny,maxx,maxy=crop; pad=max(maxx-minx,maxy-miny)*.09
    fig,ax=plt.subplots(figsize=(12,9),facecolor="white")
    for e in msp:
        try:
            t=e.dxftype()
            if t=="LINE":
                a,b=e.dxf.start,e.dxf.end
                if minx-pad<=a.x<=maxx+pad and miny-pad<=a.y<=maxy+pad: ax.plot([a.x,b.x],[a.y,b.y],color="black",lw=.6)
            elif t=="LWPOLYLINE":
                p=[(q[0],q[1]) for q in e.get_points()]
                if e.closed and p: p.append(p[0])
                p=[q for q in p if minx-pad<=q[0]<=maxx+pad and miny-pad<=q[1]<=maxy+pad]
                if len(p)>1: ax.plot([q[0] for q in p],[q[1] for q in p],color="black",lw=.6)
            elif t=="DIMENSION":
                p1,p2,base=e.dxf.defpoint2,e.dxf.defpoint3,e.dxf.defpoint
                ang=float(getattr(e.dxf,"angle",0) or 0)
                if abs(ang)<45:
                    ax.plot([p1.x,p2.x],[base.y,base.y],color="black",lw=.45); ax.text((p1.x+p2.x)/2,base.y,str(round(abs(p2.x-p1.x))),ha="center",va="bottom",fontsize=5)
                else:
                    ax.plot([base.x,base.x],[p1.y,p2.y],color="black",lw=.45); ax.text(base.x,(p1.y+p2.y)/2,str(round(abs(p2.y-p1.y))),ha="left",va="center",fontsize=5)
        except Exception: pass
    ax.set_xlim(minx-pad,maxx+pad); ax.set_ylim(miny-pad,maxy+pad); ax.set_aspect("equal"); ax.axis("off")
    fig.savefig(png,dpi=220,bbox_inches="tight",facecolor="white"); fig.savefig(pdf,dpi=300,bbox_inches="tight",facecolor="white"); plt.close(fig)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True); ap.add_argument("--output-dir",required=True)
    args=ap.parse_args()
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    iso=out/"isolated_main.dxf"; dim=out/"v11_dimensioned.dxf"
    iso_report=isolate(args.input,iso)
    gen=generate(iso,dim)
    preview(dim,out/"preview.png",out/"preview.pdf",gen["bbox"])
    report={"input":args.input,"isolated_dxf":str(iso),"dimensioned_dxf":str(dim),"preview_png":str(out/"preview.png"),"preview_pdf":str(out/"preview.pdf"),"isolation":iso_report,"generation":gen}
    (out/"semantic_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"status":"ok","dimensioned_dxf":str(dim),"preview_pdf":str(out/"preview.pdf"),"dimensions":len(gen["dimensions"]),"internal":gen["internal_count"]},ensure_ascii=False,indent=2))
if __name__=="__main__":
    main()
