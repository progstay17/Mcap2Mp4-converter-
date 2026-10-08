"""GUI PySide6 (PRD M3): antrean, tabel, progres/kecepatan/ETA, Jeda/Lanjut/Batal, pemilih lokasi output,
penampil metadata, laporan, i18n 3 bahasa (berlaku langsung tanpa restart).

GUI hanya pengendali: konversi berjalan di proses terpisah lewat batch.run (spawn), jadi jendela tidak membeku
dan pembatalan bersih. Semua teks dari i18n (FR-81).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, QTimer, Signal

from . import __version__, batch, cli, i18n, metadata, report, settings
from .i18n import t

STATUS_COLORS = {"pending": "#6b7280", "running": "#2563eb", "success": "#15803d", "skipped": "#6b7280",
                 "incomplete": "#b45309", "out_of_sync": "#b45309", "failed": "#b91c1c"}
COLS = ["col.source", "col.size", "col.status", "col.progress", "col.message"]


def fmt_size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_dur(sec) -> str:
    if sec is None or sec != sec or sec < 0 or sec > 10**7:
        return "—"
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def asset_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return root / "assets" / name


def open_location(path: str):
    p = Path(path)
    if os.name == "nt" and p.exists():
        subprocess.Popen(f'explorer /select,"{p}"')
    else:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p.parent if p.exists() else p)))


# ------------------------------------------------------------------ model tabel
class FileModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.index_of: dict[str, int] = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = [{"rel": r["source"], "size": r.get("source_bytes") or 0, "status": r.get("status", "pending"),
                      "progress": 1.0 if r.get("status") == "skipped" else 0.0, "result": r,
                      "detail": cli.describe_result(r) if r.get("status") in ("failed", "skipped", "incomplete") else ""}
                     for r in rows]
        self.index_of = {r["rel"]: i for i, r in enumerate(self.rows)}
        self.endResetModel()

    def update(self, rel, **kw):
        i = self.index_of.get(rel)
        if i is None:
            return
        self.rows[i].update(kw)
        self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLS) - 1))

    def retranslate(self):
        for r in self.rows:
            res = r.get("result") or {}
            if res.get("status") in ("failed", "success", "skipped", "incomplete", "out_of_sync"):
                r["detail"] = cli.describe_result(res) if r["status"] != "success" else cli.describe_result(res)
        self.headerDataChanged.emit(Qt.Horizontal, 0, len(COLS) - 1)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLS) - 1))

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return t(COLS[section])
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = self.rows[index.row()], index.column()
        if role == Qt.DisplayRole:
            return [r["rel"], fmt_size(r["size"]), t("status." + r["status"]), "", r["detail"].lstrip(": ")][c]
        if role == Qt.ForegroundRole and c == 2:
            return QtGui.QBrush(QtGui.QColor(STATUS_COLORS.get(r["status"], "#000")))
        if role == Qt.TextAlignmentRole and c == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.UserRole:
            return r["progress"]
        if role == Qt.ToolTipRole:
            return r["detail"] or r["rel"]
        return None


class ProgressDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):
        row = index.model().rows[index.row()]
        if row["status"] in ("pending", "failed", "incomplete", "out_of_sync"):
            return
        opt = QtWidgets.QStyleOptionProgressBar()
        opt.rect = option.rect.adjusted(4, 4, -4, -4)
        opt.minimum, opt.maximum = 0, 100
        opt.progress = int(row["progress"] * 100)
        opt.text = f"{opt.progress}%"
        opt.textVisible = True
        opt.state = QtWidgets.QStyle.State_Enabled
        QtWidgets.QApplication.style().drawControl(QtWidgets.QStyle.CE_ProgressBar, opt, painter)


# ------------------------------------------------------------------ thread latar
class Runner(QThread):
    ev_start = Signal(str)
    ev_progress = Signal(str, int, int)
    ev_done = Signal(dict)
    ev_warning = Signal(str, dict)
    run_finished = Signal(list, dict)
    run_failed = Signal(str, dict)

    def __init__(self, s, control):
        super().__init__()
        self.s, self.control = s, control

    def _ev(self, kind, data):
        if kind == "start":
            self.ev_start.emit(data[2])
        elif kind == "progress":
            self.ev_progress.emit(*data)
        elif kind == "done":
            self.ev_done.emit(data[2])
        elif kind == "warning":
            self.ev_warning.emit(*data)

    def run(self):
        try:
            results, summary = batch.run(self.s, self._ev, self.control)
            self.run_finished.emit(results, summary)
        except batch.BatchError as exc:
            self.run_failed.emit(exc.code, exc.kw)
        except Exception as exc:                                   # noqa: BLE001
            self.run_failed.emit("internal_error", {"detail": f"{type(exc).__name__}: {exc}"})


class Scanner(QThread):
    scanned = Signal(list)
    failed = Signal(str, dict)

    def __init__(self, s):
        super().__init__()
        self.s = s

    def run(self):
        try:
            self.scanned.emit(batch.dry_run(self.s))
        except batch.BatchError as exc:
            self.failed.emit(exc.code, exc.kw)
        except Exception as exc:                                   # noqa: BLE001
            self.failed.emit("internal_error", {"detail": f"{type(exc).__name__}: {exc}"})


# ------------------------------------------------------------------ penampil metadata
def _fill(parent, key, val):
    item = QtWidgets.QTreeWidgetItem(parent, [str(key), ""])
    if isinstance(val, dict):
        for k, v in val.items():
            _fill(item, k, v)
    elif isinstance(val, list):
        for i, v in enumerate(val):
            label = v.get("topic") if isinstance(v, dict) and v.get("topic") else i
            _fill(item, label, v)
    else:
        item.setText(1, "" if val is None else str(val))
    return item


class MetadataView(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        self.filter = QtWidgets.QLineEdit()
        self.filter.setClearButtonEnabled(True)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.hint = QtWidgets.QLabel()
        self.hint.setWordWrap(True)
        lay.addWidget(self.filter)
        lay.addWidget(self.hint)
        lay.addWidget(self.tree)
        self.filter.textChanged.connect(self.apply_filter)
        self.doc = None
        self.out_path = None
        self.retranslate()

    def retranslate(self):
        self.filter.setPlaceholderText(t("gui.meta.filter"))
        self.tree.setHeaderLabels([t("col.topic"), t("col.message")] if False else ["Tag", "Value"])
        if self.doc is not None:
            self.show_doc(self.doc, self.out_path)
        else:
            self.hint.setText(t("gui.meta.none"))

    def clear(self, msg_key="gui.meta.none"):
        self.doc = None
        self.tree.clear()
        self.hint.setText(t(msg_key))
        self.hint.setVisible(True)

    def show_doc(self, doc, out_path):
        self.doc, self.out_path = doc, out_path
        self.tree.clear()
        self.hint.setVisible(False)
        g = lambda k: _fill(self.tree.invisibleRootItem(), t(k), None)           # noqa: E731
        item = g("gui.meta.g_file")
        _fill(item, "source", doc.get("source"))
        for v in doc.get("video", []):
            _fill(item, "output", v.get("output"))
        _fill(item, "generator", doc.get("generator"))
        mc = doc.get("mcap") or {}
        _fill(g("gui.meta.g_mcap"), "", None)
        self.tree.takeTopLevelItem(self.tree.topLevelItemCount() - 1)
        node = g("gui.meta.g_mcap")
        for k, v in mc.items():
            _fill(node, k, v)
        h265 = g("gui.meta.g_h265")
        mp4 = g("gui.meta.g_mp4")
        for v in doc.get("video", []):
            _fill(h265, v.get("topic"), v.get("bitstream"))
            _fill(mp4, v.get("topic"), v.get("mp4"))
        if doc.get("exiftool"):
            ex = g("gui.meta.g_exif")
            for topic, tags in doc["exiftool"].items():
                tn = _fill(ex, topic, None)
                groups = {}
                for k, v in (tags or {}).items():
                    grp, _, name = k.partition(":")
                    groups.setdefault(grp if name else "—", {})[name or grp] = v
                for grp, kv in groups.items():
                    _fill(tn, grp, kv)
        if doc.get("sync"):
            _fill(g("gui.meta.g_sync"), "", doc["sync"])
        if doc.get("warnings"):
            _fill(g("gui.meta.g_warn"), "", doc["warnings"])
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(i in (0, 1))
        self.tree.resizeColumnToContents(0)
        self.apply_filter(self.filter.text())

    def apply_filter(self, text):
        text = text.strip().lower()

        def visit(item):
            own = text in item.text(0).lower() or text in item.text(1).lower()
            any_child = False
            for i in range(item.childCount()):
                any_child |= visit(item.child(i))
            show = not text or own or any_child
            item.setHidden(not show)
            if text and any_child:
                item.setExpanded(True)
            return show
        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        m = QtWidgets.QMenu(self)
        a1 = m.addAction(t("gui.meta.copy"))
        a2 = m.addAction(t("gui.meta.open_location"))
        act = m.exec(self.tree.viewport().mapToGlobal(pos))
        if act is a1:
            QtWidgets.QApplication.clipboard().setText(item.text(1) or item.text(0))
        elif act is a2 and self.out_path:
            open_location(self.out_path)


# ------------------------------------------------------------------ jendela utama
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.cfg_path = cfg_path or settings.user_config_path()
        self.s = self._load_settings()
        i18n.set_language(self.s.get("language") or "id")
        self.state = "idle"                    # idle | scanning | running | paused | cancelling
        self.control = None
        self.runner = None
        self.scanner = None
        self.last_results: list[dict] = []
        self.last_summary: dict | None = None
        self.last_settings: dict | None = None
        self.run_t0 = 0.0
        self.paused_since = None
        self.paused_total = 0.0
        self.done_count = 0
        self.total_bytes = 0
        self.done_bytes = 0
        self.skipped_bytes = 0
        self.tray = None
        self._status_spec = None
        self.resize(1100, 760)
        icon = asset_path("icon.png")
        if icon.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon)))
        self._build()
        self.apply_settings(self.s)
        self.retranslate()
        self._set_state("idle")
        self._status("gui.msg.ready")
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)

    # ---- pengaturan
    def _load_settings(self):
        try:
            return settings.load(self.cfg_path)
        except Exception:                                      # file rusak -> bawaan
            return settings.load()

    def collect_settings(self) -> dict:
        s = settings.load()
        s.update(self.s)
        s["metadata"] = dict(self.s["metadata"], sidecar_json=self.cb_sidecar.isChecked())
        s["source_dir"] = self.ed_src.text().strip()
        s["output_dir"] = self.ed_out.text().strip()
        s["output_mode"] = self.cmb_mode.currentData()
        s["mirror_structure"] = self.cb_mirror.isChecked()
        s["recursive"] = self.cb_recursive.isChecked()
        s["fps_mode"] = self.cmb_fps.currentData()
        s["fps_value"] = self.ed_fps.text().strip() or None
        s["sync"] = {"policy": self.cmb_sync.currentData(), "tolerance_frames": self.s["sync"]["tolerance_frames"]}
        s["verify"] = self.cmb_verify.currentData()
        s["co64"] = self.cmb_co64.currentData()
        s["workers"] = self.sp_workers.value()
        s["reports"] = {"formats": [k for k, cb in self.cb_reports.items() if cb.isChecked()],
                        "csv_delimiter": self.s["reports"]["csv_delimiter"], "language": self.cmb_rlang.currentData()}
        s["language"] = self.cmb_lang.currentData()
        return s

    def apply_settings(self, s):
        def pick(cmb, val):
            i = cmb.findData(val)
            cmb.setCurrentIndex(max(0, i))
        self.ed_src.setText(s.get("source_dir", ""))
        self.ed_out.setText(s.get("output_dir", ""))
        pick(self.cmb_mode, s.get("output_mode", "folder"))
        self.cb_mirror.setChecked(s.get("mirror_structure", True))
        self.cb_recursive.setChecked(s.get("recursive", True))
        pick(self.cmb_fps, s.get("fps_mode", "timestamp"))
        self.ed_fps.setText("" if s.get("fps_value") is None else str(s["fps_value"]))
        pick(self.cmb_sync, s["sync"]["policy"])
        pick(self.cmb_verify, s.get("verify", "quick"))
        pick(self.cmb_co64, s.get("co64", "auto"))
        self.sp_workers.setValue(int(s.get("workers", 2)))
        self.cb_sidecar.setChecked(s["metadata"].get("sidecar_json", True))
        for k, cb in self.cb_reports.items():
            cb.setChecked(k in s["reports"]["formats"])
        pick(self.cmb_rlang, s["reports"].get("language"))
        pick(self.cmb_lang, s.get("language") or "id")
        self._mode_changed()

    def save_settings(self):
        try:
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            settings.save(self.collect_settings(), self.cfg_path)
        except OSError:
            pass

    # ---- bangun UI
    def _build(self):
        c = QtWidgets.QWidget()
        self.setCentralWidget(c)
        root = QtWidgets.QVBoxLayout(c)

        top = QtWidgets.QGridLayout()
        self.lb_src, self.lb_out, self.lb_lang = QtWidgets.QLabel(), QtWidgets.QLabel(), QtWidgets.QLabel()
        self.ed_src, self.ed_out = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        self.bt_src, self.bt_out = QtWidgets.QPushButton(), QtWidgets.QPushButton()
        self.cmb_mode = QtWidgets.QComboBox()
        self.cb_mirror, self.cb_recursive = QtWidgets.QCheckBox(), QtWidgets.QCheckBox()
        self.cmb_lang = QtWidgets.QComboBox()
        for code in i18n.available():
            self.cmb_lang.addItem(i18n.t("lang.name", code), code)
        top.addWidget(self.lb_src, 0, 0)
        top.addWidget(self.ed_src, 0, 1, 1, 3)
        top.addWidget(self.bt_src, 0, 4)
        top.addWidget(self.lb_lang, 0, 5)
        top.addWidget(self.cmb_lang, 0, 6)
        top.addWidget(self.lb_out, 1, 0)
        top.addWidget(self.cmb_mode, 1, 1)
        top.addWidget(self.ed_out, 1, 2, 1, 2)
        top.addWidget(self.bt_out, 1, 4)
        top.addWidget(self.cb_recursive, 2, 1)
        top.addWidget(self.cb_mirror, 2, 2, 1, 2)
        top.setColumnStretch(3, 1)
        root.addLayout(top)

        self.gb = QtWidgets.QGroupBox()
        g = QtWidgets.QGridLayout(self.gb)
        self.lb_fps, self.lb_fpsv, self.lb_sync, self.lb_verify = (QtWidgets.QLabel() for _ in range(4))
        self.lb_co64, self.lb_workers, self.lb_reports, self.lb_rlang = (QtWidgets.QLabel() for _ in range(4))
        self.cmb_fps, self.cmb_sync, self.cmb_verify, self.cmb_co64 = (QtWidgets.QComboBox() for _ in range(4))
        self.cmb_rlang = QtWidgets.QComboBox()
        self.ed_fps = QtWidgets.QLineEdit()
        self.ed_fps.setMaximumWidth(120)
        self.sp_workers = QtWidgets.QSpinBox()
        self.sp_workers.setRange(1, max(1, (os.cpu_count() or 2)))
        self.cb_sidecar = QtWidgets.QCheckBox()
        self.cb_reports = {k: QtWidgets.QCheckBox(k.upper()) for k in ("csv", "json", "xlsx", "html")}
        g.addWidget(self.lb_fps, 0, 0); g.addWidget(self.cmb_fps, 0, 1)
        g.addWidget(self.lb_fpsv, 0, 2); g.addWidget(self.ed_fps, 0, 3)
        g.addWidget(self.lb_sync, 0, 4); g.addWidget(self.cmb_sync, 0, 5)
        g.addWidget(self.lb_verify, 1, 0); g.addWidget(self.cmb_verify, 1, 1)
        g.addWidget(self.lb_co64, 1, 2); g.addWidget(self.cmb_co64, 1, 3)
        g.addWidget(self.lb_workers, 1, 4); g.addWidget(self.sp_workers, 1, 5)
        rep = QtWidgets.QHBoxLayout()
        for cb in self.cb_reports.values():
            rep.addWidget(cb)
        g.addWidget(self.lb_reports, 2, 0); g.addLayout(rep, 2, 1, 1, 3)
        g.addWidget(self.lb_rlang, 2, 4); g.addWidget(self.cmb_rlang, 2, 5)
        g.addWidget(self.cb_sidecar, 3, 0, 1, 3)
        root.addWidget(self.gb)

        bar = QtWidgets.QHBoxLayout()
        self.bt_scan, self.bt_start, self.bt_pause, self.bt_cancel = (QtWidgets.QPushButton() for _ in range(4))
        self.bt_open, self.bt_report = QtWidgets.QPushButton(), QtWidgets.QPushButton()
        self.bt_start.setObjectName("primary")
        for b in (self.bt_scan, self.bt_start, self.bt_pause, self.bt_cancel):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.bt_open)
        bar.addWidget(self.bt_report)
        root.addLayout(bar)

        self.tabs = QtWidgets.QTabWidget()
        self.model = FileModel()
        self.table = QtWidgets.QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(3, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        for col, w in ((1, 90), (2, 120), (3, 140)):
            hh.setSectionResizeMode(col, QtWidgets.QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.meta = MetadataView()
        self.tabs.addTab(self.table, "")
        self.tabs.addTab(self.log, "")
        self.tabs.addTab(self.meta, "")
        root.addWidget(self.tabs, 1)

        foot = QtWidgets.QHBoxLayout()
        self.pb = QtWidgets.QProgressBar()
        self.pb.setRange(0, 1000)
        self.lb_stat = QtWidgets.QLabel()
        foot.addWidget(self.pb, 1)
        foot.addWidget(self.lb_stat)
        root.addLayout(foot)
        self.statusBar().showMessage("")

        self.bt_src.clicked.connect(lambda: self._browse(self.ed_src, "gui.msg.pick_source"))
        self.bt_out.clicked.connect(lambda: self._browse(self.ed_out, "gui.msg.pick_output"))
        self.cmb_mode.currentIndexChanged.connect(self._mode_changed)
        self.cmb_fps.currentIndexChanged.connect(self._mode_changed)
        self.cmb_lang.currentIndexChanged.connect(self._lang_changed)
        self.bt_scan.clicked.connect(self.do_scan)
        self.bt_start.clicked.connect(self.do_start)
        self.bt_pause.clicked.connect(self.do_pause)
        self.bt_cancel.clicked.connect(self.do_cancel)
        self.bt_open.clicked.connect(self.do_open_output)
        self.bt_report.clicked.connect(self.do_export)
        self.table.selectionModel().currentRowChanged.connect(self._row_selected)
        self.table.doubleClicked.connect(lambda idx: self._open_row(idx.row()))

    # ---- teks
    def retranslate(self):
        self.setWindowTitle(f"{t('app.title')} — {t('gui.about', version=__version__)}")
        self.lb_src.setText(t("label.source") + ":")
        self.lb_out.setText(t("label.output") + ":")
        self.lb_lang.setText(t("label.language") + ":")
        for b in (self.bt_src, self.bt_out):
            b.setText(t("btn.browse"))
        for key, val in (("folder", "gui.output_mode.folder"), ("beside", "gui.output_mode.beside"), ("ask", "gui.output_mode.ask")):
            self._relabel(self.cmb_mode, key, t(val))
        self.cb_mirror.setText(t("gui.mirror"))
        self.cb_recursive.setText(t("gui.recursive"))
        self.gb.setTitle(t("gui.options"))
        for lb, k in ((self.lb_fps, "gui.fps_mode"), (self.lb_fpsv, "gui.fps_value"), (self.lb_sync, "gui.sync"),
                      (self.lb_verify, "gui.verify"), (self.lb_co64, "gui.co64"), (self.lb_workers, "gui.workers"),
                      (self.lb_reports, "gui.reports"), (self.lb_rlang, "gui.report_lang")):
            lb.setText(t(k) + ":")
        for cmb, items in ((self.cmb_fps, ("timestamp", "session", "fraction", "integer")),
                           (self.cmb_sync, ("strict", "warn", "trim")), (self.cmb_verify, ("off", "quick", "full")),
                           (self.cmb_co64, ("auto", "always", "off"))):
            pre = {self.cmb_fps: "gui.fps.", self.cmb_sync: "gui.sync.", self.cmb_verify: "gui.verify.",
                   self.cmb_co64: "gui.co64."}[cmb]
            for it in items:
                self._relabel(cmb, it, t(pre + it))
        self._relabel(self.cmb_rlang, None, t("gui.same_as_ui"))
        for code in i18n.available():
            self._relabel(self.cmb_rlang, code, i18n.t("lang.name", code))
        self.cb_sidecar.setText(t("gui.sidecar"))
        self.bt_scan.setText(t("btn.scan"))
        self.bt_start.setText(t("btn.start"))
        self.bt_pause.setText(t("btn.resume") if self.state == "paused" else t("btn.pause"))
        self.bt_cancel.setText(t("btn.cancel"))
        self.bt_open.setText(t("btn.open_output"))
        self.bt_report.setText(t("btn.export_report"))
        for i, k in enumerate(("gui.tab.files", "gui.tab.log", "gui.tab.metadata")):
            self.tabs.setTabText(i, t(k))
        self.model.retranslate()
        self.meta.retranslate()
        if self._status_spec:
            self.statusBar().showMessage(t(self._status_spec[0], **self._status_spec[1]))
        self._tick()

    def _relabel(self, cmb, data, text):
        if cmb.count() == 0 or cmb.findData(data) < 0:
            cmb.addItem(text, data)
        else:
            cmb.setItemText(cmb.findData(data), text)

    def _lang_changed(self):
        code = self.cmb_lang.currentData()
        if code:
            i18n.set_language(code)
            self.retranslate()

    def _mode_changed(self):
        mode = self.cmb_mode.currentData()
        self.ed_out.setEnabled(mode == "folder" and self.state in ("idle",))
        self.bt_out.setEnabled(mode == "folder" and self.state in ("idle",))
        self.cb_mirror.setEnabled(mode == "folder" and self.state == "idle")
        self.ed_fps.setEnabled(self.cmb_fps.currentData() in ("fraction", "integer") and self.state == "idle")

    # ---- pembantu
    def _browse(self, edit, key):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, t(key), edit.text() or str(Path.home()))
        if d:
            edit.setText(d)

    def _msg(self, text, title_key="gui.msg.error"):
        QtWidgets.QMessageBox.warning(self, t(title_key), text)

    def _batch_error_text(self, code, kw):
        key = f"msg.{code}"
        txt = t(key, **kw) if t(key) != key else (t(f"err.{code}") if t(f"err.{code}") != f"err.{code}" else code)
        return txt + (f"\n{kw['detail']}" if kw.get("detail") else "")

    def _status(self, key, **kw):
        """Pesan status yang ikut diterjemahkan saat bahasa diganti."""
        self._status_spec = (key, kw)
        self.statusBar().showMessage(t(key, **kw))

    def _log(self, text):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")

    def _set_state(self, st):
        self.state = st
        idle, running, paused = st == "idle", st in ("running", "paused"), st == "paused"
        for w in (self.ed_src, self.bt_src, self.cmb_mode, self.cb_recursive, self.gb, self.cmb_lang if False else self.gb):
            w.setEnabled(idle)
        self.bt_scan.setEnabled(idle)
        self.bt_start.setEnabled(idle)
        self.bt_pause.setEnabled(running)
        self.bt_cancel.setEnabled(st in ("running", "paused"))
        self.bt_pause.setText(t("btn.resume") if paused else t("btn.pause"))
        self._mode_changed()

    # ---- pindai
    def do_scan(self):
        s = self.collect_settings()
        if not s["source_dir"]:
            return self._msg(t("gui.msg.src_empty"))
        if s["output_mode"] == "ask":
            s["output_mode"] = "beside"                       # pratinjau saja; lokasi ditanyakan saat Mulai
        self._set_state("scanning")
        self._status("gui.msg.scanning")
        self.scanner = Scanner(s)
        self.scanner.scanned.connect(self._scanned)
        self.scanner.failed.connect(self._scan_failed)
        self.scanner.start()

    def _scanned(self, rows):
        self.model.set_rows(rows)
        self._set_state("idle")
        total = sum(r.get("source_bytes") or 0 for r in rows)
        if rows:
            self._status("gui.msg.scan_done", n=len(rows), size=fmt_size(total))
        else:
            self._status("gui.msg.nothing")
        self._log(t(*self._status_spec[:1], **self._status_spec[1]))

    def _scan_failed(self, code, kw):
        self._set_state("idle")
        self._status("gui.msg.ready")
        self._msg(self._batch_error_text(code, kw))

    # ---- jalankan
    def do_start(self):
        s = self.collect_settings()
        if not s["source_dir"]:
            return self._msg(t("gui.msg.src_empty"))
        if s["output_mode"] == "ask":
            d = QtWidgets.QFileDialog.getExistingDirectory(self, t("gui.msg.pick_output"), s["output_dir"] or str(Path.home()))
            if not d:
                return
            s["output_dir"], s["output_mode"] = d, "folder"
            self.ed_out.setText(d)
            self.cmb_mode.setCurrentIndex(self.cmb_mode.findData("folder"))
        if s["fps_mode"] in ("fraction", "integer"):
            from fractions import Fraction
            try:
                if Fraction(s["fps_value"] or "") <= 0:
                    raise ValueError
                if s["fps_mode"] == "integer":
                    s["fps_value"] = int(Fraction(s["fps_value"]))
            except (ValueError, ZeroDivisionError):
                return self._msg(t("msg.bad_fps"))
        try:
            batch.validate(s)
            entries = batch.scan(s)
        except batch.BatchError as exc:
            return self._msg(self._batch_error_text(exc.code, exc.kw))
        if not entries:
            return self._msg(t("gui.msg.nothing"), "app.title")
        self.model.set_rows([{"source": e.rel, "source_bytes": e.size, "status": "pending"} for e in entries])
        self.total_bytes = sum(e.size for e in entries)
        self.done_bytes = self.skipped_bytes = self.done_count = 0
        self.run_t0 = time.monotonic()
        self.paused_total, self.paused_since = 0.0, None
        self.last_settings = s
        self.control = batch.RunControl()
        self.runner = Runner(s, self.control)
        self.runner.ev_start.connect(self._ev_start)
        self.runner.ev_progress.connect(self._ev_progress)
        self.runner.ev_done.connect(self._ev_done)
        self.runner.ev_warning.connect(lambda code, kw: self._log(t(f"msg.{code}", **kw)))
        self.runner.run_finished.connect(self._run_finished)
        self.runner.run_failed.connect(self._run_failed)
        self.save_settings()
        self._log(t("cli.found", n=len(entries), size=fmt_size(self.total_bytes)))
        self._set_state("running")
        self.tabs.setCurrentIndex(0)
        self.timer.start()
        self.runner.start()

    def _ev_start(self, rel):
        self.model.update(rel, status="running", progress=0.0)

    def _ev_progress(self, rel, done, total):
        self.model.update(rel, progress=(done / total) if total else 0.0)

    def _ev_done(self, res):
        rel = res["source"]
        i = self.model.index_of.get(rel)
        size = self.model.rows[i]["size"] if i is not None else 0
        self.done_count += 1
        self.done_bytes += size
        if res["status"] == "skipped":
            self.skipped_bytes += size
        self.model.update(rel, status=res["status"], progress=1.0 if res["status"] in ("success", "skipped") else self.model.rows[i]["progress"],
                          detail=cli.describe_result(res), result=res)
        self._log(f"{rel}: {t('status.' + res['status'])}{cli.describe_result(res, verbose=True)}")
        self._tick()

    def _run_finished(self, results, summary):
        self.timer.stop()
        self.last_results, self.last_summary = results, summary
        cancelled = summary.get("cancelled")
        self._set_state("idle")
        c = summary["counts"]
        if cancelled:
            self._status("gui.msg.cancelled")
        else:
            self._status("cli.summary", success=c["success"], skipped=c["skipped"], incomplete=c["incomplete"],
                         out_of_sync=c["out_of_sync"], failed=c["failed"], size=fmt_size(summary["total_source_bytes"]),
                         secs=summary["elapsed_s"])
        self._log(t(self._status_spec[0], **self._status_spec[1]))
        self.pb.setValue(1000 if not cancelled else self.pb.value())
        self.lb_stat.setText("")
        if results and self.last_settings:
            try:
                rep = self.last_settings["reports"]
                if rep["formats"]:
                    paths = report.export(results, summary, batch.reports_base(self.last_settings) / "reports",
                                          rep["formats"], rep["csv_delimiter"], rep.get("language") or self.last_settings["language"])
                    for p in paths:
                        self._log(t("gui.msg.report_saved", path=p))
            except Exception as exc:                            # noqa: BLE001
                self._log(f"report: {exc}")
        if not cancelled:
            self._notify(c)
        self.runner = None

    def _run_failed(self, code, kw):
        self.timer.stop()
        self._set_state("idle")
        self._status("gui.msg.ready")
        self._msg(self._batch_error_text(code, kw))
        self.runner = None

    def _notify(self, counts):
        if self.isActiveWindow():
            return
        try:
            if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
                if self.tray is None:
                    self.tray = QtWidgets.QSystemTrayIcon(self.windowIcon(), self)
                    self.tray.show()
                self.tray.showMessage(t("gui.notify.title"), t("gui.notify.body", success=counts["success"] + counts["skipped"],
                                                               failed=counts["failed"] + counts["out_of_sync"] + counts["incomplete"]))
        except Exception:                                       # noqa: BLE001
            pass

    # ---- kendali
    def do_pause(self):
        if self.state == "running":
            self.control.pause.set()
            self.paused_since = time.monotonic()
            self._set_state("paused")
            self._status("gui.msg.paused")
        elif self.state == "paused":
            self.control.pause.clear()
            if self.paused_since:
                self.paused_total += time.monotonic() - self.paused_since
            self.paused_since = None
            self._set_state("running")

    def do_cancel(self, confirm=True):
        if self.state not in ("running", "paused"):
            return
        if confirm and QtWidgets.QMessageBox.question(self, t("btn.cancel"), t("gui.msg.confirm_cancel")) != QtWidgets.QMessageBox.Yes:
            return
        self.control.cancel.set()
        self.control.pause.clear()
        self._set_state("cancelling")
        self.bt_cancel.setEnabled(False)
        self.bt_pause.setEnabled(False)
        self._status("gui.msg.cancelling")

    def _tick(self):
        if self.state not in ("running", "paused", "cancelling") or not self.total_bytes:
            return
        partial = sum(r["size"] * r["progress"] for r in self.model.rows if r["status"] == "running")
        finished = sum(r["size"] for r in self.model.rows if r["status"] not in ("pending", "running"))
        done = finished + partial
        self.pb.setValue(int(1000 * done / self.total_bytes))
        now = time.monotonic()
        paused_now = (now - self.paused_since) if self.paused_since else 0.0
        elapsed = max(0.001, now - self.run_t0 - self.paused_total - paused_now)
        work = max(0.0, done - self.skipped_bytes)
        speed = work / elapsed
        remaining = max(0.0, self.total_bytes - done)
        eta = remaining / speed if speed > 1 and self.done_count < len(self.model.rows) else None
        self.lb_stat.setText(f"{t('gui.overall')}: {int(100 * done / self.total_bytes)}%   {t('gui.speed')}: {fmt_size(speed)}/s   "
                             f"{t('gui.eta')}: {fmt_dur(eta)}")
        if self.state == "running":
            self._status("gui.msg.running", done=self.done_count, n=len(self.model.rows))

    # ---- keluaran
    def do_open_output(self):
        s = self.last_settings or self.collect_settings()
        d = s["source_dir"] if s["output_mode"] == "beside" else s["output_dir"]
        if d and Path(d).exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d))
        else:
            self._msg(t("gui.msg.no_output_folder"))

    def do_export(self):
        if not self.last_results or not self.last_summary:
            return self._msg(t("gui.msg.no_results"), "app.title")
        s = self.last_settings or self.collect_settings()
        formats = self.collect_settings()["reports"]["formats"] or ["csv"]
        paths = report.export(self.last_results, self.last_summary, batch.reports_base(s) / "reports", formats,
                              s["reports"]["csv_delimiter"], self.cmb_rlang.currentData() or self.cmb_lang.currentData())
        for p in paths:
            self._log(t("gui.msg.report_saved", path=p))
        self._status("gui.msg.report_saved", path=paths[0].parent)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(paths[0].parent)))

    # ---- metadata
    def _row_selected(self, cur, _prev):
        if cur.isValid():
            self._show_meta(cur.row())

    def _open_row(self, row):
        self.tabs.setCurrentIndex(2)
        self._show_meta(row)

    def _find_sidecar(self, res):
        if res.get("sidecar") and Path(res["sidecar"]).exists():
            return Path(res["sidecar"])
        for o in res.get("outputs") or []:
            d = Path(o["path"]).parent
            stem = Path(res["source"]).stem
            for cand in (d / f"{stem}.metadata.json", *sorted(d.glob(f"{stem}*.metadata.json"))):
                if cand.exists():
                    return cand
        return None

    def _show_meta(self, row):
        res = self.model.rows[row].get("result") or {}
        if not res.get("outputs"):
            return self.meta.clear("gui.meta.none")
        sc = self._find_sidecar(res)
        if not sc:
            return self.meta.clear("gui.meta.missing")
        try:
            doc = json.loads(sc.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self.meta.clear("gui.meta.missing")
        self.meta.show_doc(doc, res["outputs"][0]["path"])

    def closeEvent(self, ev):
        if self.state in ("running", "paused", "cancelling") and self.runner is not None:
            if QtWidgets.QMessageBox.question(self, t("btn.cancel"), t("gui.msg.confirm_close")) != QtWidgets.QMessageBox.Yes:
                ev.ignore()
                return
            self.control.cancel.set()
            self.control.pause.clear()
            self.runner.wait(30000)
        for th in (self.scanner,):
            if th is not None:
                th.wait(5000)
        self.save_settings()
        ev.accept()


STYLE = """
QPushButton { padding: 6px 14px; }
QPushButton#primary { background: #2563eb; color: white; border: 1px solid #1d4ed8; border-radius: 4px; font-weight: 600; }
QPushButton#primary:disabled { background: #93b4f5; border-color: #93b4f5; }
QGroupBox { margin-top: 8px; font-weight: 600; } QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
"""


def create_app(argv=None):
    if getattr(sys, "frozen", False):                      # pastikan plugin Qt ditemukan di paket PyInstaller
        plug = Path(getattr(sys, "_MEIPASS", "")) / "PySide6" / "plugins"
        if plug.exists():
            os.environ.setdefault("QT_PLUGIN_PATH", str(plug))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or sys.argv)
    app.setStyle("Fusion")
    f = QtGui.QFont()
    f.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Yu Gothic UI", "Noto Sans CJK SC", "Sans Serif"])
    f.setPointSize(9)
    app.setFont(f)
    app.setStyleSheet(STYLE)
    return app


def selftest(src: str, out: str, png: str) -> int:
    """Uji mandiri headless untuk paket terpasang: konversi nyata lewat GUI, tangkapan layar, hasil ke <png>.json."""
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = create_app([])
    win = MainWindow(cfg_path=Path(tempfile.mkdtemp()) / "settings.json")
    win.show()
    win.ed_src.setText(src)
    win.ed_out.setText(out)
    win.cmb_verify.setCurrentIndex(win.cmb_verify.findData("full"))
    win.cmb_sync.setCurrentIndex(win.cmb_sync.findData("trim"))
    win.bt_start.click()
    t0 = time.time()
    while (win.state != "idle" or win.last_summary is None) and time.time() - t0 < 240:
        app.processEvents()
        time.sleep(0.02)
    win.grab().save(png)
    res = {"counts": win.last_summary["counts"] if win.last_summary else None,
           "language": i18n.get_language() if hasattr(i18n, "get_language") else None,
           "start_button": win.bt_start.text(), "rows": len(win.model.rows)}
    Path(png + ".json").write_text(json.dumps(res), encoding="utf-8")
    win.close()
    return 0 if win.last_summary else 1


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) >= 5 and argv[1] == "--selftest":
        return selftest(argv[2], argv[3], argv[4])
    app = create_app(argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
