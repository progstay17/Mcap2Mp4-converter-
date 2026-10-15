"""Tab Pemutar (PySide6) — M7+M8: penjelajah file, playlist, satu/dua kamera kiri-kanan, sesi virtual.

Semua logika perintah ada di sync.py/controller.py/playlist.py/library.py; di sini hanya tampilan & pintasan.
Video digambar sendiri dari frame libVLC (vmem) di FrameView: tidak butuh plugin keluaran video/GPU, jalan di RDP/VPS,
dan letterbox (rasio aspek terjaga). libVLC dimuat malas saat berkas dibuka; bila tidak ada, tab menampilkan pesan."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import i18n
from .controller import SHORTCUTS
from .engine import SPEEDS, Engine, EngineOptions, PlayerUnavailable, format_time
from .autoconvert import convert_recording
from .library import recording_for_files, recording_for_path, scan_folder
from .model import Part, Recording
from .playlist import MODES, Playlist
from .sync import RecordingPlayer

t = i18n.t
SLIDER_MAX = 10000
VIDEO_EXT = (".mp4", ".m4v", ".mov")
HISTORY_MAX = 10


class JumpSlider(QtWidgets.QSlider):
    """Klik di alur langsung melompat ke posisi itu (seekbar)."""
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            v = QtWidgets.QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(ev.position().x()), self.width())
            self.setValue(v)
            self.sliderPressed.emit()
            self.sliderReleased.emit()
        super().mousePressEvent(ev)


class FrameView(QtWidgets.QWidget):
    """Satu kamera: menggambar frame terbaru dari sink engine dengan letterbox."""
    doubleClicked = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.engine = None
        self._img: QtGui.QImage | None = None
        self._serial = -1

    def bind(self, engine):
        self.engine = engine
        self._img, self._serial = None, -1
        self.update()

    def refresh(self):
        sink = getattr(self.engine, "sink", None)
        fr = sink.latest() if sink is not None else None
        if fr is None or fr[4] == self._serial:
            return
        buf, w, h, pitch, serial = fr
        self._img = QtGui.QImage(buf, w, h, pitch, QtGui.QImage.Format_RGB32).copy()     # salin: buffer VLC terus ditulis
        self._serial = serial
        self.update()

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtCore.Qt.black)
        if self._img is not None and not self._img.isNull():
            r = self.rect()
            s = self._img.size().scaled(r.size(), QtCore.Qt.KeepAspectRatio)
            target = QtCore.QRect(QtCore.QPoint((r.width() - s.width()) // 2, (r.height() - s.height()) // 2), s)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            p.drawImage(target, self._img)

    def mouseDoubleClickEvent(self, ev):
        self.doubleClicked.emit()


def _fmt_rec(r: Recording) -> str:
    if not r.converted:
        return f"{r.title}  —  [{t('player.lib.unconverted')}]  ({len(r.source_files)})"
    bits = [format_time(r.duration_s * 1000) if r.duration_s else "?", t("player.lib.cams", n=r.camera_count)]
    if r.has_audio:
        bits.append("♪")
    if r.part_count > 1:
        bits.append(t("player.lib.parts", n=r.part_count))
    return f"{r.title}  —  " + " · ".join(bits)


class PlayerTab(QtWidgets.QWidget):
    metadataRequested = QtCore.Signal(str)

    def __init__(self, s: dict | None = None, engine_factory=None, state_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self.s = dict(s or {})
        self._factory = engine_factory
        self.state_dir = Path(state_dir) if state_dir else None
        self.rp: RecordingPlayer | None = None
        self.rec: Recording | None = None
        self.playlist = Playlist()
        self._session_cache: dict = {}
        self._dragging = False
        self._fs_host = None
        self._unavailable = False
        self._clock = time.monotonic
        self.settings_provider = None                # callable -> pengaturan konversi lengkap (diisi jendela utama)
        self._conv = None                            # {"rec", "thread", "result", "t0"}
        self._noframe_after = 3.0                    # detik sejak bagian dibuka tanpa frame -> peringatan
        self._opened_at = 0.0
        self._warned: set = set()
        self.view_mode = "both"                      # both | left | right
        self.swapped = False
        self._build()
        self.setAcceptDrops(True)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.frame_timer = QtCore.QTimer(self)
        self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self._refresh_frames)
        self.frame_timer.start()
        self.retranslate()
        self._enable(False)
        self._restore_state()

    # ------------------------------------------------------------ UI
    def _build(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(self.splitter)

        # --- panel kiri: Berkas | Playlist
        self.left = QtWidgets.QTabWidget()
        files = QtWidgets.QWidget()
        fl = QtWidgets.QVBoxLayout(files)
        row = QtWidgets.QHBoxLayout()
        self.cmb_folder = QtWidgets.QComboBox()
        self.cmb_folder.setEditable(False)
        self.cmb_folder.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_folder.setMinimumContentsLength(12)
        self.bt_folder = QtWidgets.QPushButton()
        row.addWidget(self.cmb_folder, 1)
        row.addWidget(self.bt_folder)
        fl.addLayout(row)
        self.model = QtWidgets.QFileSystemModel()
        self.model.setFilter(QtCore.QDir.AllDirs | QtCore.QDir.NoDotAndDotDot | QtCore.QDir.Drives)
        self.model.setRootPath("")
        self.tree = QtWidgets.QTreeView()
        self.tree.setModel(self.model)
        for c in (1, 2, 3):
            self.tree.hideColumn(c)
        self.tree.setHeaderHidden(True)
        self.lst = QtWidgets.QListWidget()
        self.lst.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.lst.setWordWrap(True)
        vs = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        vs.addWidget(self.tree)
        vs.addWidget(self.lst)
        vs.setSizes([140, 260])
        fl.addWidget(vs, 1)
        self.left.addTab(files, "")

        pl = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(pl)
        self.lst_pl = QtWidgets.QListWidget()
        self.lst_pl.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.lst_pl.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.lst_pl.setWordWrap(True)
        pv.addWidget(self.lst_pl, 1)
        r1 = QtWidgets.QHBoxLayout()
        self.bt_pl_add, self.bt_pl_rm, self.bt_pl_clear, self.bt_pl_save, self.bt_pl_load = (QtWidgets.QPushButton() for _ in range(5))
        for b in (self.bt_pl_add, self.bt_pl_rm, self.bt_pl_clear):
            r1.addWidget(b)
        pv.addLayout(r1)
        r2 = QtWidgets.QHBoxLayout()
        r2.addWidget(self.bt_pl_save)
        r2.addWidget(self.bt_pl_load)
        pv.addLayout(r2)
        self.cmb_mode = QtWidgets.QComboBox()
        for m in MODES:
            self.cmb_mode.addItem("", m)
        pv.addWidget(self.cmb_mode)
        self.left.addTab(pl, "")
        self.splitter.addWidget(self.left)

        # --- panel kanan: video + kontrol
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        self.video_box = QtWidgets.QWidget()
        self.video_lay = QtWidgets.QHBoxLayout(self.video_box)
        self.video_lay.setContentsMargins(0, 0, 0, 0)
        self.video_lay.setSpacing(2)
        self.views = [FrameView(), FrameView()]
        for v in self.views:
            v.doubleClicked.connect(self.toggle_fullscreen)
            self.video_lay.addWidget(v, 1)
        self.views[1].hide()
        self.lb_hint = QtWidgets.QLabel(alignment=QtCore.Qt.AlignCenter, parent=self.video_box)
        self.lb_hint.setStyleSheet("color:#aaa; background:transparent;")
        rv.addWidget(self.video_box, 1)

        sr = QtWidgets.QHBoxLayout()
        self.seek = JumpSlider(QtCore.Qt.Horizontal)
        self.seek.setRange(0, SLIDER_MAX)
        self.lb_time = QtWidgets.QLabel("00:00 / 00:00")
        self.lb_time.setMinimumWidth(130)
        sr.addWidget(self.seek, 1)
        sr.addWidget(self.lb_time)
        rv.addLayout(sr)

        bar = QtWidgets.QHBoxLayout()
        mk = QtWidgets.QPushButton
        self.bt_open, self.bt_play, self.bt_b30, self.bt_b5, self.bt_f5, self.bt_f30, self.bt_step = (mk() for _ in range(7))
        self.bt_mute, self.bt_snap, self.bt_full, self.bt_swap, self.bt_single = (mk() for _ in range(5))
        for b in (self.bt_play, self.bt_b30, self.bt_b5, self.bt_f5, self.bt_f30, self.bt_step):
            b.setMinimumWidth(48)
        self.bt_step.setMinimumWidth(84)
        self.cmb_speed = QtWidgets.QComboBox()
        self.cmb_speed.setEditable(True)
        self.cmb_speed.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.cmb_speed.setMaximumWidth(90)
        for sp in SPEEDS:
            self.cmb_speed.addItem(f"{sp:g}×", sp)
        self.cmb_speed.setCurrentIndex(SPEEDS.index(1.0))
        self.lb_speed = QtWidgets.QLabel()
        self.sl_vol = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sl_vol.setRange(0, 100)
        self.sl_vol.setMaximumWidth(100)
        self.sl_vol.setValue(int(self.s.get("player_volume", 80)))
        for w in (self.bt_open, self.bt_b30, self.bt_b5, self.bt_play, self.bt_f5, self.bt_f30, self.bt_step):
            bar.addWidget(w)
        bar.addSpacing(8)
        bar.addWidget(self.lb_speed)
        bar.addWidget(self.cmb_speed)
        bar.addStretch(1)
        for w in (self.bt_swap, self.bt_single, self.bt_mute, self.sl_vol, self.bt_snap, self.bt_full):
            bar.addWidget(w)
        rv.addLayout(bar)
        self.lb_status = QtWidgets.QLabel()
        self.lb_status.setStyleSheet("color:#555;")
        rv.addWidget(self.lb_status)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([int(x) for x in (self.s.get("player_splitter") or [300, 800])])

        # sinyal
        self.bt_open.clicked.connect(self.open_file_dialog)
        self.bt_folder.clicked.connect(self.open_folder_dialog)
        self.bt_play.clicked.connect(lambda: self._do("toggle_play"))
        self.bt_b30.clicked.connect(lambda: self._do("back_30"))
        self.bt_b5.clicked.connect(lambda: self._do("back_5"))
        self.bt_f5.clicked.connect(lambda: self._do("fwd_5"))
        self.bt_f30.clicked.connect(lambda: self._do("fwd_30"))
        self.bt_step.clicked.connect(lambda: self._do("step_frame"))
        self.bt_mute.clicked.connect(lambda: self._do("toggle_mute"))
        self.bt_snap.clicked.connect(lambda: self._do("snapshot"))
        self.bt_full.clicked.connect(self.toggle_fullscreen)
        self.bt_swap.clicked.connect(self.swap_cameras)
        self.bt_single.clicked.connect(self.cycle_view_mode)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.sl_vol.valueChanged.connect(self._volume_changed)
        self.cmb_speed.activated.connect(self._speed_picked)
        self.cmb_speed.lineEdit().editingFinished.connect(self._speed_typed)
        self.cmb_folder.activated.connect(lambda i: self.load_folder(self.cmb_folder.itemData(i)))
        self.tree.clicked.connect(lambda ix: self.load_folder(self.model.filePath(ix)))
        self.lst.itemDoubleClicked.connect(lambda it: self.play_recording(it.data(QtCore.Qt.UserRole)))
        self.lst.customContextMenuRequested.connect(self._lib_menu)
        self.lst_pl.itemDoubleClicked.connect(lambda it: self.play_playlist_row(self.lst_pl.row(it)))
        self.lst_pl.model().rowsMoved.connect(self._pl_reordered)
        self.bt_pl_add.clicked.connect(self._pl_add_selected)
        self.bt_pl_rm.clicked.connect(self._pl_remove)
        self.bt_pl_clear.clicked.connect(self._pl_clear)
        self.bt_pl_save.clicked.connect(self._pl_save_dialog)
        self.bt_pl_load.clicked.connect(self._pl_load_dialog)
        self.cmb_mode.activated.connect(lambda i: self.playlist.set_mode(self.cmb_mode.itemData(i)))
        for key, cmd in SHORTCUTS.items():
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda c=cmd: self._do(c))

    def retranslate(self):
        pairs = ((self.bt_open, "player.open"), (self.bt_b30, "player.back30"), (self.bt_b5, "player.back5"),
                 (self.bt_f5, "player.fwd5"), (self.bt_f30, "player.fwd30"), (self.bt_step, "player.step"),
                 (self.bt_snap, "player.snapshot"), (self.bt_full, "player.fullscreen"), (self.bt_swap, "player.swap"),
                 (self.bt_single, "player.single"))
        for b, k in pairs:
            b.setText(t(k))
            b.setToolTip(t("player.tip." + k.split(".")[1]))
        self.bt_play.setText(t("player.pause") if self._playing() else t("player.play"))
        self.bt_mute.setText(t("player.unmute") if self.rp and self.rp.effectively_muted else t("player.mute"))
        self.lb_speed.setText(t("player.speed") + ":")
        self.lb_hint.setText(t("player.err.no_vlc") if self._unavailable else t("player.no_file"))
        self.setToolTip(t("player.help"))
        self.left.setTabText(0, t("player.tab.files"))
        self.left.setTabText(1, t("player.tab.playlist"))
        self.bt_folder.setText(t("player.folder"))
        self.bt_pl_add.setText(t("player.pl.add"))
        self.bt_pl_rm.setText(t("player.pl.remove"))
        self.bt_pl_clear.setText(t("player.pl.clear"))
        self.bt_pl_save.setText(t("player.pl.save"))
        self.bt_pl_load.setText(t("player.pl.load"))
        for i, m in enumerate(MODES):
            self.cmb_mode.setItemText(i, t("player.mode." + m))
        self._refresh_lists()
        self.lb_hint.setGeometry(self.video_box.rect())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.lb_hint.setGeometry(self.video_box.rect())

    # ------------------------------------------------------------ penjelajah file
    def open_folder_dialog(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, t("player.folder"), self.s.get("player_last_dir") or "")
        if d:
            self.load_folder(d)

    def load_folder(self, folder):
        if not folder or not Path(folder).is_dir():
            return
        folder = str(Path(folder))
        self.s["player_last_dir"] = folder
        hist = [folder] + [h for h in (self.s.get("player_folders") or []) if h != folder]
        self.s["player_folders"] = hist[:HISTORY_MAX]
        self.cmb_folder.blockSignals(True)
        self.cmb_folder.clear()
        for h in self.s["player_folders"]:
            self.cmb_folder.addItem(h, h)
        self.cmb_folder.blockSignals(False)
        ix = self.model.index(folder)
        if ix.isValid():
            self.tree.setCurrentIndex(ix)
            self.tree.scrollTo(ix)
        self._library = scan_folder(folder, self._session_cache)
        self._refresh_lists()
        self.left.setCurrentIndex(0)

    def _refresh_lists(self):
        lib = getattr(self, "_library", [])
        self.lst.clear()
        for r in lib:
            it = QtWidgets.QListWidgetItem(_fmt_rec(r))
            it.setData(QtCore.Qt.UserRole, r)
            if not r.converted:
                it.setForeground(QtGui.QBrush(QtGui.QColor("#888")))
            self.lst.addItem(it)
        self.lst_pl.blockSignals(True)
        self.lst_pl.clear()
        for i, r in enumerate(self.playlist.items):
            it = QtWidgets.QListWidgetItem(("▶ " if i == self.playlist.index else "") + _fmt_rec(r))
            it.setData(QtCore.Qt.UserRole, r)
            self.lst_pl.addItem(it)
        self.lst_pl.blockSignals(False)
        self.cmb_mode.setCurrentIndex(MODES.index(self.playlist.play_mode))

    def _lib_menu(self, pos):
        it = self.lst.itemAt(pos)
        if it is None:
            return
        r: Recording = it.data(QtCore.Qt.UserRole)
        m = QtWidgets.QMenu(self)
        a_add = m.addAction(t("player.menu.add"))
        a_loc = m.addAction(t("player.menu.location"))
        a_copy = m.addAction(t("player.menu.copy"))
        a_meta = m.addAction(t("player.menu.metadata"))
        a_conv = m.addAction(t("player.menu.convert")) if not r.converted else None
        a_add.setEnabled(r.playable)
        a_meta.setEnabled(bool(r.parts and r.parts[0].sidecar))
        act = m.exec(self.lst.viewport().mapToGlobal(pos))
        first = (r.parts[0].paths[0] if r.parts else str(Path(getattr(self, "_library_dir", "")) / (r.source_files[0] if r.source_files else "")))
        if a_conv is not None and act is a_conv:
            self.convert_and_play(r)
        elif act is a_add:
            self.playlist_add(r)
        elif act is a_loc:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(self.s.get("player_last_dir", "")) if not r.parts else Path(first).parent)))
        elif act is a_copy:
            QtWidgets.QApplication.clipboard().setText(first)
        elif act is a_meta:
            self.metadataRequested.emit(r.parts[0].sidecar)

    # ------------------------------------------------------------ playlist
    def playlist_add(self, rec: Recording):
        if rec.playable:
            self.playlist.add(rec)
            self._refresh_lists()

    def _pl_add_selected(self):
        for it in self.lst.selectedItems() or ([self.lst.currentItem()] if self.lst.currentItem() else []):
            self.playlist_add(it.data(QtCore.Qt.UserRole))

    def _pl_remove(self):
        row = self.lst_pl.currentRow()
        if row >= 0:
            self.playlist.remove(row)
            self._refresh_lists()

    def _pl_clear(self):
        self.playlist.clear()
        self._refresh_lists()

    def _pl_reordered(self, *_):
        recs = [self.lst_pl.item(i).data(QtCore.Qt.UserRole) for i in range(self.lst_pl.count())]
        cur = self.playlist.current
        self.playlist.items = recs
        self.playlist.index = next((i for i, r in enumerate(recs) if r is cur), -1)
        QtCore.QTimer.singleShot(0, self._refresh_lists)

    def play_playlist_row(self, row):
        rec = self.playlist.select(row)
        if rec is not None:
            self.open_recording(rec)
            self._refresh_lists()

    def _pl_save_dialog(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, t("player.pl.save"), self.s.get("player_last_dir") or "", "Playlist (*.m2playlist)")
        if p:
            self.playlist.save(p, self.rp.time_ms / 1000 if self.rp else 0, self.rp.rate if self.rp else None)

    def _pl_load_dialog(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, t("player.pl.load"), self.s.get("player_last_dir") or "", "Playlist (*.m2playlist)")
        if p:
            self.playlist = Playlist.load(p)
            self._refresh_lists()

    def _state_file(self):
        return (self.state_dir / "last.m2playlist") if self.state_dir else None

    def _restore_state(self):
        f = self._state_file()
        if f and f.exists():
            try:
                self.playlist = Playlist.load(f)
                self.s.setdefault("player_speed", self.playlist.state.get("speed", 1.0))
            except (OSError, ValueError, KeyError):
                pass
        for h in reversed(self.s.get("player_folders") or []):
            self.cmb_folder.insertItem(0, h, h)
        self._refresh_lists()

    # ------------------------------------------------------------ pembukaan
    def _ensure_player(self) -> bool:
        if self.rp is not None:
            return True
        if self._factory:
            factory = self._factory
        else:
            from .engine import load_vlc
            try:
                load_vlc()                                  # cek ketersediaan libVLC sekali
            except PlayerUnavailable:
                self._unavailable = True
                self.retranslate()
                return False
            opts = EngineOptions(hw_decode=self.s.get("player_hw_decode", "auto"))
            factory = lambda: Engine(opts)                  # noqa: E731
        self.rp = RecordingPlayer(factory, threshold_ms=int(self.s.get("player_sync_threshold_ms", 40)),
                                  auto_mute=bool(self.s.get("player_mute_above_2x", True)))
        self.rp.volume = self.sl_vol.value()
        self.rp.rate = float(self.s.get("player_speed", 1.0))
        self.rp.on_part_change = lambda i: None
        return True

    def open_file_dialog(self):
        ps, _ = QtWidgets.QFileDialog.getOpenFileNames(self, t("player.dialog_open"), self.s.get("player_last_dir") or "", t("player.filter"))
        if ps:
            self.open_files(ps)

    def open_file(self, path):
        return self.open_recording(recording_for_path(path))

    def open_files(self, paths):
        """Satu berkas = rekaman lengkapnya (sidecar/nama kamera); dua berkas = kiri-kanan menurut urutan pilihan."""
        return self.open_recording(recording_for_files(paths))

    def _log(self, msg):
        if not self.state_dir:
            return
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self.state_dir / "player.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    def convert_and_play(self, rec: Recording):
        """Konversi rekaman MCAP di thread latar ke <folder>/mp4, lalu putar (diselesaikan di _tick)."""
        if self._conv is not None:
            self.lb_status.setText(t("player.convert.busy"))
            return False
        if not rec.folder or not rec.source_files:
            self.lb_status.setText(t("player.lib.unconverted_hint"))
            return False
        cfg = dict(self.settings_provider() if self.settings_provider else {})
        job = {"rec": rec, "result": None, "t0": self._clock()}

        def run():
            job["result"] = convert_recording(rec, cfg)

        job["thread"] = threading.Thread(target=run, name="autoconvert", daemon=True)
        self._conv = job
        self._log(f"konversi dimulai: {rec.title} {rec.source_files}")
        job["thread"].start()
        self.lb_status.setText(t("player.convert.running", title=rec.title, sec=0))
        return True

    def _poll_conversion(self):
        job = self._conv
        if job is None:
            return False
        if job["thread"].is_alive():
            self.lb_status.setText(t("player.convert.running", title=job["rec"].title, sec=int(self._clock() - job["t0"])))
            return True
        self._conv = None
        ok, rec, msg = job["result"] or (False, None, "?")
        self._log(f"konversi selesai ok={ok}: {msg}")
        if not ok:
            self.lb_status.setText(t("player.convert.failed", err=msg))
            return False
        if self.s.get("player_last_dir"):
            self.load_folder(self.s["player_last_dir"])         # daftar diperbarui: rekaman kini 'sudah dikonversi'
        self.play_recording(rec)
        return True

    def play_recording(self, rec: Recording):
        if not rec.converted:
            return self.convert_and_play(rec)
        if not rec.playable:
            self.lb_status.setText(t("player.lib.unconverted_hint"))
            return False
        if rec not in self.playlist.items:
            self.playlist.add(rec)
        self.playlist.select(self.playlist.items.index(rec))
        ok = self.open_recording(rec)
        self._refresh_lists()
        return ok

    def open_recording(self, rec: Recording, start_ms=0):
        if not self._ensure_player():
            return False
        missing = rec.missing_files()
        if missing:
            self.lb_status.setText(t("player.err.open", err=missing[0]))
            return False
        try:
            self.rp.open(rec, start_ms, autoplay=True)
        except PlayerUnavailable:
            self._unavailable = True
            self.rp.close()
            self.rp = None
            self.retranslate()
            return False
        except Exception as exc:
            self.lb_status.setText(t("player.err.open", err=str(exc)))
            return False
        self.rec = rec
        self._opened_at, self._warned = self._clock(), set()
        self._log(f"buka '{rec.title}' bagian={rec.part_count} kamera={rec.camera_count} berkas={[p for pt in rec.parts for p in pt.paths]}")
        self.s["player_last_dir"] = str(Path(rec.parts[0].paths[0]).parent)
        self.lb_hint.hide()
        self.rp.set_rate(float(self.s.get("player_speed", 1.0)))
        self._layout_views()
        self._sync_speed_box()
        self._enable(True)
        self.retranslate()
        return True

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for u in ev.mimeData().urls():
            p = u.toLocalFile()
            if os.path.isdir(p):
                self.load_folder(p)
                return
            if p.lower().endswith(VIDEO_EXT):
                self.open_file(p)
                return

    # ------------------------------------------------------------ tata letak kamera (FR-121)
    def _layout_views(self):
        n = len(self.rp.engines) if self.rp else 0
        order = [0, 1] if not self.swapped else [1, 0]
        shown = []
        for slot, v in enumerate(self.views):
            idx = order[slot] if slot < min(2, n) else None
            if idx is not None and idx < n:
                v.bind(self.rp.engines[idx])
                v.setVisible(self.view_mode == "both" or (self.view_mode == "left" and slot == 0) or (self.view_mode == "right" and slot == 1))
                shown.append(v)
            else:
                v.bind(None)
                v.hide()
        self.bt_swap.setEnabled(n >= 2)
        self.bt_single.setEnabled(n >= 2)

    def swap_cameras(self):
        self.swapped = not self.swapped
        self._layout_views()

    def cycle_view_mode(self):
        self.view_mode = {"both": "left", "left": "right", "right": "both"}[self.view_mode]
        self._layout_views()

    def _refresh_frames(self):
        if self.rp:
            for v in self.views:
                if v.isVisible():
                    v.refresh()

    # ------------------------------------------------------------ perintah
    def _playing(self):
        try:
            return bool(self.rp and self.rp.is_playing)
        except Exception:
            return False

    def _do(self, cmd):
        if cmd == "open":
            return self.open_folder_dialog()
        if cmd == "fullscreen":
            return self.toggle_fullscreen()
        if not self.rp or not self.rec:
            return
        c = self.rp
        {"toggle_play": c.toggle_play, "back_5": lambda: c.seek_rel(-5), "fwd_5": lambda: c.seek_rel(5),
         "back_30": lambda: c.seek_rel(-30), "fwd_30": lambda: c.seek_rel(30), "slower": c.slower, "faster": c.faster,
         "step_frame": c.step_frame, "toggle_mute": c.toggle_mute, "snapshot": self._snapshot}[cmd]()
        if cmd in ("slower", "faster"):
            self._sync_speed_box()
        self.retranslate()

    def _snapshot(self):
        first = Path(self.rec.parts[0].paths[0])
        out = self.rp.snapshot(first.parent, self.rec.title)
        self.lb_status.setText(t("player.snapshot_saved", path=out[0]) if out else t("player.snapshot_failed"))

    def _seek_released(self):
        self._dragging = False
        if self.rp and self.rec:
            self.rp.seek_fraction(self.seek.value() / SLIDER_MAX)
            self._layout_views()

    def _volume_changed(self, v):
        self.s["player_volume"] = v
        if self.rp:
            self.rp.set_volume(v)

    def _speed_picked(self, i):
        v = self.cmb_speed.itemData(i)
        if v is not None:
            self._apply_speed(float(v))

    def _speed_typed(self):
        txt = self.cmb_speed.lineEdit().text().replace("×", "").replace("x", "").replace(",", ".").strip()
        try:
            self._apply_speed(float(txt))
        except ValueError:
            self._sync_speed_box()

    def _apply_speed(self, v):
        v = self.rp.set_rate(v) if self.rp else max(0.25, min(8.0, v))
        self.s["player_speed"] = v
        self._sync_speed_box()
        self.retranslate()

    def _sync_speed_box(self):
        r = self.rp.rate if self.rp else float(self.s.get("player_speed", 1.0))
        self.s["player_speed"] = r
        i = self.cmb_speed.findData(r)
        self.cmb_speed.blockSignals(True)
        if i >= 0:
            self.cmb_speed.setCurrentIndex(i)
        else:
            self.cmb_speed.setEditText(f"{r:g}×")
        self.cmb_speed.blockSignals(False)

    def _enable(self, on):
        for w in (self.bt_play, self.bt_b30, self.bt_b5, self.bt_f5, self.bt_f30, self.bt_step, self.bt_mute, self.bt_snap,
                  self.seek, self.cmb_speed):
            w.setEnabled(on)

    # ------------------------------------------------------------ layar penuh
    def toggle_fullscreen(self):
        if self._fs_host is None:
            host = QtWidgets.QWidget(None, QtCore.Qt.Window)
            lay = QtWidgets.QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            self._fs_parent_layout = self.video_box.parentWidget().layout()
            self._fs_parent_layout.removeWidget(self.video_box)
            lay.addWidget(self.video_box)
            for key in ("F", "Esc"):
                sc = QtGui.QShortcut(QtGui.QKeySequence(key), host)
                sc.activated.connect(self.toggle_fullscreen)
            host.setStyleSheet("background:#000;")
            self._fs_host = host
            host.showFullScreen()
        else:
            host, self._fs_host = self._fs_host, None
            host.layout().removeWidget(self.video_box)
            self._fs_parent_layout.insertWidget(0, self.video_box, 1)
            self.video_box.show()
            host.close()
            host.deleteLater()

    # ------------------------------------------------------------ pembaruan berkala
    def _tick(self):
        converting = self._poll_conversion()
        if not self.rp or not self.rec:
            return
        try:
            self.rp.tick()
            st = self.rp.status()
        except Exception as exc:
            self.lb_status.setText(t("player.err.open", err=str(exc)))
            return
        if self.rp.finished:                                   # lanjut otomatis (FR-126)
            nxt = self.playlist.next() if self.playlist.items else None
            if nxt is not None and nxt.playable:
                self.open_recording(nxt)
                self._refresh_lists()
                return
        ln = max(1, st.get("length_ms", 1))
        if not self._dragging:
            self.seek.blockSignals(True)
            self.seek.setValue(int(SLIDER_MAX * min(1.0, st["time_ms"] / ln)))
            self.seek.blockSignals(False)
        self.lb_time.setText(f"{format_time(st['time_ms'])} / {format_time(st['length_ms'])}")
        self.bt_play.setText(t("player.pause") if self.rp.is_playing else t("player.play"))
        msg = t("player.stats", lost=st.get("lost", 0), rate=f"{self.rp.rate:g}")
        if st.get("parts", 1) > 1:
            msg += " | " + t("player.part", i=st["part"] + 1, n=st["parts"])
        if self.rp.effectively_muted and not self.rp.user_muted:
            msg += " | " + t("player.auto_muted")
        if self.rp.gaps_ms:
            msg += " | " + t("player.gap", ms=round(self.rp.gaps_ms[-1]))
        if not converting:                                    # selama konversi, baris status memuat kemajuan konversi
            self.lb_status.setText(msg)
        self._check_frames()
        if self.rp.active and len(self.views) and self.views[0].engine is not self.rp.engines[0 if not self.swapped else min(1, len(self.rp.engines) - 1)]:
            self._layout_views()                               # pergantian bagian: ikat ulang ke engine baru

    def _check_frames(self):
        """Diagnostik: kamera yang 'Playing' tetapi tak menghasilkan frame setelah beberapa detik dilaporkan di status & player.log."""
        if self._clock() - self._opened_at < self._noframe_after or not self.rp or not self.rp.active:
            return
        for i, e in enumerate(self.rp.engines):
            sink = getattr(e, "sink", None)
            if sink is None or i in self._warned:
                continue
            if sink.serial == 0 and e.state in ("Playing", "Paused", "Error", "Ended"):
                self._warned.add(i)
                detail = f"kamera {i + 1}: tanpa frame (state={e.state}, berkas={getattr(e, 'path', '?')})"
                self._log(detail)
                self.lb_status.setText(t("player.err.noframes", n=i + 1, state=e.state))

    # ------------------------------------------------------------ simpan
    def collect(self, s: dict) -> dict:
        """Nilai yang diingat antar-sesi (FR-133)."""
        s["player_volume"] = self.sl_vol.value()
        s["player_speed"] = self.rp.rate if self.rp else self.s.get("player_speed", 1.0)
        for k in ("player_last_dir", "player_folders"):
            if self.s.get(k):
                s[k] = self.s[k]
        s["player_splitter"] = self.splitter.sizes()
        return s

    def save_state(self):
        f = self._state_file()
        if f:
            try:
                self.playlist.save(f, self.rp.time_ms / 1000 if self.rp else self.playlist.state.get("position_s", 0.0),
                                   self.rp.rate if self.rp else self.s.get("player_speed", 1.0))
            except OSError:
                pass

    def shutdown(self):
        self._conv = None                           # thread latar daemon: dilepas tanpa menunggu
        self.timer.stop()
        self.frame_timer.stop()
        self.save_state()
        if self._fs_host is not None:
            self.toggle_fullscreen()
        if self.rp:
            self.rp.close()
            self.rp = None
