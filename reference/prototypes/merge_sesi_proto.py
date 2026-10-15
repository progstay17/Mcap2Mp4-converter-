import time, hashlib
from pathlib import Path
from array import array
from mcap2mp4 import mcap_reader as R, source_adapters as sa, h265, mp4_writer, verify
files=["/mnt/user-data/uploads/ego_0014.mcap","/mnt/user-data/uploads/ego_0015.mcap"]
topic="/ego/camera/0"
out=Path("/tmp/merged_cam0.mp4")
t0=time.time()
tr=h265.HevcTrack(); w=mp4_writer.Mp4Writer(out); stamps=[]; seam=None; param_sets=[]
for fi,p in enumerate(files):
    local=h265.HevcTrack()
    for ch,msg in R.iter_messages(p, topics={topic}):
        fr=sa.parse_foxglove_video(msg.data)
        local.feed(fr.payload)
        r=tr.feed(fr.payload)
        if r is None: continue
        if fi==1 and seam is None: seam=w.count
        w.add_sample(r[0], r[1], fr.stamp_ns); stamps.append(fr.stamp_ns)
    param_sets.append({k:[hashlib.sha1(x).hexdigest()[:8] for x in v] for k,v in local.params.items()})
print("param sets per file:", param_sets, "-> identik:", param_sets[0]==param_sets[1])
print("param unik di hvcC gabungan:", {k:len(v) for k,v in tr.params.items()})
st=w.finish(tr.info, tr.params, None)
print("selesai %.1fs"%(time.time()-t0), {k:v for k,v in st.items() if k not in ("sample_sha256",)})
print("indeks sampel sambungan:", seam, "| selisih stamp di sambungan: %.3f ms"%((stamps[seam]-stamps[seam-1])/1e6),
      "| selisih terbesar seluruhnya: %.3f ms"%(max(b-a for a,b in zip(stamps,stamps[1:]))/1e6))
q=verify.quick(out); print("verify.quick:", q["ok"], q["errors"], q["frames"], "frame", round(q["duration_s"],3),"dtk keyframes:", q["keyframes"])
