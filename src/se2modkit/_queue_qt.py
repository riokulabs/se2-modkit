"""The Qt window behind `se2mod queue gui` (see queue_gui for what it shows; the formatting lives there)."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import gpu as gpumod
from . import jobqueue as Q
from . import queue_gui as G

STATE_COLORS = {"running": "#37b24d", "queued": "#4dabf7", "done": None, "failed": "#f03e3e", "timeout": "#f76707",
                "cancelled": "#f59f00", "lost": "#f76707"}

STYLE = """
QFrame#card { background: palette(base); border: 1px solid palette(midlight); border-radius: 10px; }
QLabel#title { font-size: 15pt; font-weight: 600; }
QLabel#gpuname { font-weight: 600; }
QLabel#muted { color: palette(placeholder-text); }
QLabel#pill { border-radius: 10px; padding: 3px 12px; font-weight: 600; color: white; }
QProgressBar { border: none; border-radius: 6px; background: palette(alternate-base); min-height: 16px;
               max-height: 16px; text-align: center; }
QProgressBar::chunk { border-radius: 6px; background: #37b24d; }
QProgressBar#vram::chunk { background: #228be6; }
QTreeWidget { border: 1px solid palette(midlight); border-radius: 8px; padding: 2px; }
QPlainTextEdit { border: 1px solid palette(midlight); border-radius: 8px; padding: 4px; }
QPushButton { padding: 5px 12px; }
"""


def _icon(*names: str) -> QtGui.QIcon:
    for name in names:
        icon = QtGui.QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QtGui.QIcon()


def _use_desktop_icons() -> None:
    """Pip-installed Qt doesn't load the desktop's own theme plugin, so point it at an installed icon theme."""
    if not QtGui.QIcon.fromTheme("media-playback-pause").isNull():
        return
    for theme in ("breeze-dark", "breeze", "Adwaita", "Papirus", "hicolor"):
        if any((Path(base) / "icons" / theme).is_dir() for base in ("/usr/share", "/usr/local/share",
                                                                     str(Path.home() / ".local/share"))):
            QtGui.QIcon.setThemeName(theme)
            if not QtGui.QIcon.fromTheme("media-playback-pause").isNull():
                return


class GpuCard(QtWidgets.QFrame):
    def __init__(self):
        super().__init__(objectName="card")
        lay = QtWidgets.QGridLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(6)
        self.name = QtWidgets.QLabel(objectName="gpuname")
        self.addr = QtWidgets.QLabel(objectName="muted")
        head = QtWidgets.QHBoxLayout()
        head.addWidget(self.name)
        head.addStretch(1)
        head.addWidget(self.addr)
        lay.addLayout(head, 0, 0, 1, 2)
        self.load = QtWidgets.QProgressBar(maximum=100)
        self.vram = QtWidgets.QProgressBar(maximum=1000, objectName="vram")
        lay.addWidget(QtWidgets.QLabel("Load"), 1, 0)
        lay.addWidget(self.load, 1, 1)
        lay.addWidget(QtWidgets.QLabel("VRAM"), 2, 0)
        lay.addWidget(self.vram, 2, 1)
        self.sensors = QtWidgets.QLabel(objectName="muted")
        self.jobs = QtWidgets.QLabel()
        lay.addWidget(self.sensors, 3, 0, 1, 2)
        lay.addWidget(self.jobs, 4, 0, 1, 2)
        lay.setColumnStretch(1, 1)

    def show_stats(self, g: gpumod.GpuStats, jobs: list[Q.JobRecord]) -> None:
        s = G.gpu_summary(g, jobs)
        self.name.setText(f"GPU {g.index} · {g.name or 'GPU'}")
        self.addr.setText(g.addr.removeprefix("0000:"))
        self.load.setValue(g.busy or 0)
        self.load.setFormat(s["busy"])
        self.vram.setValue(int(10 * G.vram_percent(g)))
        self.vram.setFormat(s["vram"])
        self.sensors.setText(s["sensors"] or "no sensor readings")
        busy = s["jobs"] != "no queue job"
        self.jobs.setText(("● " if busy else "○ ") + (s["jobs"] if busy else "idle"))
        self.jobs.setStyleSheet("color: #37b24d;" if busy else "")


class QueueWindow(QtWidgets.QMainWindow):
    def __init__(self, store: Q.Store, settings: Q.Settings, devices: list[tuple[int, str, str]],
                 blender: Path | None = None):
        super().__init__()
        self.store, self.settings, self.devices, self.blender = store, settings, devices, blender
        self.selected: int | None = None
        self.sampler = G.JobSampler()
        self.shown_log: tuple[int, int] | None = None
        self.setWindowTitle("se2mod queue")
        self.resize(1180, 820)

        body = QtWidgets.QWidget()
        self.setCentralWidget(body)
        root = QtWidgets.QVBoxLayout(body)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)

        # header: title, queue state, runner
        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Job queue", objectName="title")
        self.pill = QtWidgets.QLabel(objectName="pill")
        self.runner = QtWidgets.QLabel(objectName="muted")
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(self.pill)
        head.addSpacing(10)
        head.addWidget(self.runner)
        head.addStretch(1)
        root.addLayout(head)

        # GPU cards side by side
        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(12)
        self.cards = []
        for _ in devices:
            card = GpuCard()
            cards.addWidget(card, 1)
            self.cards.append(card)
        if not devices:
            note = QtWidgets.QLabel("GPU readings aren't available on this system.", objectName="muted")
            cards.addWidget(note)
        root.addLayout(cards)

        # controls
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)
        self.pause_btn = QtWidgets.QPushButton()
        self.pause_btn.clicked.connect(self.toggle_pause)
        bar.addWidget(self.pause_btn)
        self.blender_btn = QtWidgets.QPushButton()
        self.blender_btn.setIcon(_icon("blender", "applications-graphics"))
        self.blender_btn.clicked.connect(self.toggle_blender)
        bar.addWidget(self.blender_btn)
        bar.addSpacing(14)
        self.move_btns = []
        for label, where, icons, key in (("Top", "top", ("go-top",), "Ctrl+Home"),
                                         ("Up", "up", ("go-up",), "Ctrl+Up"),
                                         ("Down", "down", ("go-down",), "Ctrl+Down"),
                                         ("Bottom", "bottom", ("go-bottom",), "Ctrl+End")):
            btn = QtWidgets.QPushButton(_icon(*icons), label)
            btn.setToolTip(f"Move the selected waiting job ({key})")
            btn.clicked.connect(lambda _=False, w=where: self.move(w))
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=lambda w=where: self.move(w))
            bar.addWidget(btn)
            self.move_btns.append(btn)
        bar.addSpacing(14)
        self.cancel_btn = QtWidgets.QPushButton(_icon("process-stop", "dialog-cancel", "edit-delete"), "Cancel job")
        self.cancel_btn.setToolTip("Cancel the selected job (Delete)")
        self.cancel_btn.clicked.connect(self.cancel)
        QtGui.QShortcut(QtGui.QKeySequence("Delete"), self, activated=self.cancel)
        bar.addWidget(self.cancel_btn)
        bar.addStretch(1)
        self.message = QtWidgets.QLabel(objectName="muted")
        bar.addWidget(self.message)
        bar.addSpacing(14)
        self.prefs = QtCore.QSettings("riokulabs", "se2mod-queue")
        self.show_done = QtWidgets.QCheckBox("Show finished")
        self.show_done.setToolTip(f"Also list the last {G.FINISHED_SHOWN} finished jobs")
        self.show_done.setChecked(self.prefs.value("show_finished", False, type=bool))
        self.show_done.toggled.connect(self.on_show_done)
        bar.addWidget(self.show_done)
        clear = QtWidgets.QPushButton(_icon("edit-clear-history", "edit-clear-all", "edit-clear"), "Clear finished")
        clear.setToolTip(f"Delete finished jobs and their logs (jobs that ended in the last {Q.CLEAR_GRACE:g} s "
                         "stay, so a tool waiting on one still sees it)")
        clear.clicked.connect(self.clear_finished)
        bar.addWidget(clear)
        root.addLayout(bar)

        # jobs over log
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(len(G.COLUMNS))
        self.tree.setHeaderLabels([G.HEADINGS[c] for c in G.COLUMNS])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        for i, width in enumerate((56, 230, 64, 110, 44, 64, 70, 70, 76, 44, 260)):
            self.tree.setColumnWidth(i, width)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemSelectionChanged.connect(self.on_select)
        split.addWidget(self.tree)

        logbox = QtWidgets.QWidget()
        log_lay = QtWidgets.QVBoxLayout(logbox)
        log_lay.setContentsMargins(0, 6, 0, 0)
        self.log_title = QtWidgets.QLabel("Log", objectName="muted")
        self.log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.log.setPlaceholderText("Select a job to follow its output.")
        log_lay.addWidget(self.log_title)
        log_lay.addWidget(self.log)
        split.addWidget(logbox)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.timer = QtCore.QTimer(self, interval=G.REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    # actions ------------------------------------------------------------------------------------------------------

    def say(self, text: str) -> None:
        self.message.setText(text)

    def toggle_pause(self) -> None:
        paused = not self.store.paused
        self.store.set_paused(paused)
        if not paused and self.store.queued():
            Q.ensure_runner(self.store)
        self.say("Paused: running jobs carry on, nothing new starts." if paused else "Resumed.")
        self.refresh()

    def toggle_blender(self) -> None:
        live = self.store.live_blender()
        if live is None:
            if self.blender is None:
                self.say("No Blender found: set it with SE2_BLENDER or config blender.")
                return
            try:
                job = self.store.submit_blender(self.blender)
            except Q.QueueError as e:
                self.say(str(e))
                return
            Q.ensure_runner(self.store)
            self.say(f"Blender is job {job.id}, next in line" + ("; resume the queue to start it." if
                                                                  self.store.paused else "."))
        else:
            if live.state == "running":
                answer = QtWidgets.QMessageBox.question(
                    self, "Stop Blender", "Close the live Blender?\n\nIt saves the open file first (a file that was "
                                          "never saved goes to ~/.cache/se2modkit/live-blender).")
                if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                    return
            self.store.cancel([live.id])
            self.say("Closing Blender." if live.state == "running" else "Blender session cancelled.")
        self.refresh()

    def move(self, where: str) -> None:
        ids = [j.id for j in self.store.queued()]
        if self.selected not in ids:
            self.say("Select a waiting job to move it.")
            return
        here = ids.index(self.selected)
        to = {"top": 0, "up": here - 1, "down": here + 1, "bottom": len(ids) - 1}[where]
        try:
            self.store.move(self.selected, max(0, to))
            self.say("")
        except Q.QueueError as e:
            self.say(str(e))
        self.refresh()

    def cancel(self) -> None:
        job = None if self.selected is None else self.store.load(self.selected)
        if job is None or job.final:
            self.say("Select a waiting or running job to cancel it.")
            return
        if job.state == "running":
            answer = QtWidgets.QMessageBox.question(
                self, "Cancel running job",
                f"Stop job {job.id} {job.name}?\n\nIt gets SIGTERM, then SIGKILL after {Q.kill_grace(job):g} s.")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.store.cancel([job.id])
        self.say(f"Cancelled job {job.id}." if job.state == "queued" else f"Stopping job {job.id}.")
        self.refresh()

    def on_show_done(self, checked: bool) -> None:
        self.prefs.setValue("show_finished", checked)
        self.refresh()

    def clear_finished(self) -> None:
        if self.selected is not None and (job := self.store.load(self.selected)) is not None and job.final:
            self.selected = None
        self.say(f"Cleared {self.store.clear_finished()} finished jobs.")
        self.refresh()

    def on_select(self) -> None:
        items = self.tree.selectedItems()
        self.selected = int(items[0].text(0)) if items else None
        self.shown_log = None
        self.refresh()

    # refresh ------------------------------------------------------------------------------------------------------

    def _update_log(self, jobs: dict[int, Q.JobRecord]) -> None:
        if self.selected is None:
            return
        job = jobs.get(self.selected)
        self.log_title.setText(f"Log · job {self.selected} {job.name if job else ''}")
        path = self.store.log_path(self.selected)
        try:
            stamp = (self.selected, path.stat().st_mtime_ns)
        except OSError:
            stamp = (self.selected, 0)
        if stamp == self.shown_log:
            return
        bar = self.log.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        first = self.shown_log is None or self.shown_log[0] != self.selected
        self.log.setPlainText(G.tail(path) or "(no output yet)")
        if at_end or first:
            bar.setValue(bar.maximum())
        self.shown_log = stamp

    def refresh(self) -> None:
        try:
            now = time.time()
            jobs = self.store.all()
            by_id = {j.id: j for j in jobs}
            rows = G.ordered_jobs(jobs, G.FINISHED_SHOWN if self.show_done.isChecked() else 0)
            places = {j.id: i + 1 for i, j in enumerate(j for j in rows if j.state == "queued")}
            usage = self.sampler.sample(jobs)

            self.tree.blockSignals(True)
            scroll = self.tree.verticalScrollBar().value()
            self.tree.clear()
            muted = self.palette().color(QtGui.QPalette.ColorRole.PlaceholderText)
            for job in rows:
                item = QtWidgets.QTreeWidgetItem(list(G.job_row(self.store, job, places.get(job.id), now,
                                                                usage.get(job.id))))
                color = STATE_COLORS.get(job.state)
                if job.state == "done" and job.exit == 0:
                    for c in range(len(G.COLUMNS)):
                        item.setForeground(c, muted)
                elif color:
                    item.setForeground(3, QtGui.QColor(color))
                if job.state == "running":
                    font = item.font(1)
                    font.setBold(True)
                    item.setFont(1, font)
                self.tree.addTopLevelItem(item)
                if job.id == self.selected:
                    item.setSelected(True)
            self.tree.verticalScrollBar().setValue(scroll)
            self.tree.blockSignals(False)

            paused = self.store.paused
            self.pill.setText("Paused" if paused else "Running")
            self.pill.setStyleSheet(f"background: {'#f59f00' if paused else '#2f9e44'};")
            self.pause_btn.setText("Resume queue" if paused else "Pause queue")
            live = next((j for j in jobs if j.kind == "blender" and not j.final), None)
            self.blender_btn.setText("Start Blender" if live is None else
                                     "Stop Blender" if live.state == "running" else "Cancel Blender")
            self.blender_btn.setToolTip("A live, windowed Blender as a queue job: it waits its turn, then holds the "
                                        "GPU slot until it closes. Agents drive it through the MCP add-on.")
            self.blender_btn.setEnabled(live is not None or self.blender is not None)
            self.pause_btn.setIcon(_icon("media-playback-start") if paused else _icon("media-playback-pause"))
            self.runner.setText(G.runner_text(self.store, jobs, self.settings).split("·", 1)[1].strip())

            selected = by_id.get(self.selected) if self.selected is not None else None
            for btn in self.move_btns:
                btn.setEnabled(selected is not None and selected.state == "queued" and not selected.cancel)
            self.cancel_btn.setEnabled(selected is not None and not selected.final)

            for card, stats in zip(self.cards, gpumod.read_stats(self.devices)):
                card.show_stats(stats, jobs)
            self._update_log(by_id)
        except Exception as e:  # keep the window alive through a half-written job file or a vanished GPU
            self.say(f"refresh failed: {e}")


def run(store: Q.Store, settings: Q.Settings, blender: Path | None = None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["se2mod queue"])
    app.setApplicationName("se2mod queue")
    app.setDesktopFileName("se2mod-queue")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    _use_desktop_icons()
    win = QueueWindow(store, settings, gpumod.describe() if gpumod.supported() else [], blender)
    win.show()
    return app.exec()
