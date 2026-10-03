"""Penulis MP4 streaming: mdat ditulis langsung, moov di akhir, co64 + mdat largesize opsional (auto/always/off), stts per-frame (timescale 90000, RLE), creation_time native. Port: write_mp4.

PRD: FR-11, FR-12, FR-13, FR-44. Belum diimplementasikan (milestone M1/M2)."""

def not_implemented():
    raise NotImplementedError("mp4_writer: lihat docs/PORTING_NOTES.md")
