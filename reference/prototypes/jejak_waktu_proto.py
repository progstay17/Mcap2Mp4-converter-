import struct, time
from mcap2mp4 import mcap_reader as R
def chunk_index(path):
    import os
    size=os.path.getsize(path)
    with open(path,"rb") as f:
        f.seek(size-R._TAIL_LEN); tail=f.read(R._TAIL_LEN)
        ss,_,_=struct.unpack_from("<QQI",tail,9)
        f.seek(ss); pos=ss; end=size-R._TAIL_LEN; out=[]
        while pos<end:
            h=f.read(9); op=h[0]; (ln,)=struct.unpack_from("<Q",h,1); rec=f.read(ln); pos+=9+ln
            if op==R.OP_CHUNK_INDEX:
                t0,t1,off,clen=struct.unpack_from("<QQQQ",rec,0); out.append((t0,t1,off,clen))
    return out
def read_chunk(path, off):
    with open(path,"rb") as f:
        f.seek(off); h=f.read(9); (ln,)=struct.unpack_from("<Q",h,1); rec=f.read(ln)
    o=16; usize,o=R.u64(rec,o); o+=4; comp,o=R.mstr(rec,o); inner,o=R.mbytes64(rec,o)
    return list(R._iter_inner(R._decompress(comp,inner,usize)))
def topic_range(path, topic):
    t=time.time(); ci=sorted(chunk_index(path)); chans={}
    def scan(chunk, first):
        res=None
        for op,rec in read_chunk(path,chunk[2]):
            if op==R.OP_CHANNEL:
                c=R.parse_channel(rec); chans[c.id]=c
            elif op==R.OP_MESSAGE:
                cid=struct.unpack_from("<H",rec,0)[0]; lt=struct.unpack_from("<Q",rec,6)[0]
                if chans.get(cid) and chans[cid].topic==topic:
                    res=lt if (first and res is None) else (res if first else lt)
        return res
    # channel records ada di chunk pertama (atau di summary); ambil dari summary agar aman
    s=R.read_summary(path); chans.update(s.channels)
    first=next((v for c in ci if (v:=scan(c,True)) is not None),None)
    last=next((v for c in reversed(ci) if (v:=scan(c,False)) is not None),None)
    return first,last,len(ci),time.time()-t
for n in ("ego_0014","ego_0015"):
    for tp in ("/ego/camera/0","/ego/camera/1"):
        f,l,nc,dt=topic_range(f"/mnt/user-data/uploads/{n}.mcap",tp)
        print(f"{n} {tp}: awal={f/1e9:.6f} akhir={l/1e9:.6f}  ({nc} chunk, hanya 2 chunk dibaca, {dt*1000:.0f} ms)")
