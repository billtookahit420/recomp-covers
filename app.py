#!/usr/bin/env python3
"""Recomp Covers - GUI for the family cover fetcher (covers.py).

Two modes:
  * Packages (auto-detect): point it at a folder of extracted recomp
    packages - PSX, NES, GB, SNES, GBA, mixed is fine. Each package's
    system, title and art state are detected; missing covers are
    fetched into artwork/<key>/front.png, and for the cartridge
    systems the launcher's own assets/img/boxart.tga is healed too.
  * ROM folder: pick a system and point it at a folder of ROMs; art
    lands in <folder>/artwork/<rom name>/front.png. Works for every
    system, including the ones whose converter tools are still
    coming (N64, Genesis, Virtual Boy, Master System, Game Gear, DS).
"""

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,
                               QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPlainTextEdit,
                               QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

import covers


class Bridge(QObject):
    log = Signal(str)
    row = Signal(int, str)
    done = Signal()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Recomp Covers")
        self.resize(820, 540)
        self.bridge = Bridge()
        self.bridge.log.connect(self._log)
        self.bridge.row.connect(self._set_status)
        self.bridge.done.connect(lambda: self.fetch_btn.setEnabled(True))
        self.entries = []

        root_edit_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(
            "Folder of extracted packages, or a ROM folder…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        scan = QPushButton("Scan")
        scan.clicked.connect(self._scan)
        self.mode = QComboBox()
        self.mode.addItem("Packages (auto-detect)", None)
        for key, info in covers.SYSTEMS.items():
            if key != "psx":
                self.mode.addItem(f"ROM folder — {info['label']}", key)
        root_edit_row.addWidget(self.path_edit)
        root_edit_row.addWidget(browse)
        root_edit_row.addWidget(self.mode)
        root_edit_row.addWidget(scan)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Package / Game", "System", "Title / Serial", "Cover"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        action_row = QHBoxLayout()
        self.replace_box = QCheckBox("Replace covers that already exist")
        self.fetch_btn = QPushButton("Fetch missing covers")
        self.fetch_btn.clicked.connect(self._fetch)
        action_row.addWidget(self.replace_box)
        action_row.addStretch()
        action_row.addWidget(self.fetch_btn)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        lay = QVBoxLayout()
        lay.addLayout(root_edit_row)
        lay.addWidget(self.table)
        lay.addLayout(action_row)
        lay.addWidget(QLabel("Log"))
        lay.addWidget(self.log_view)
        w = QWidget()
        w.setLayout(lay)
        self.setCentralWidget(w)

    # ------------------------------------------------------------ helpers
    def _log(self, msg):
        self.log_view.appendPlainText(msg)

    def _set_status(self, row, text):
        item = self.table.item(row, 3)
        if item:
            item.setText(text)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose folder")
        if d:
            self.path_edit.setText(d)
            self._scan()

    # -------------------------------------------------------------- scan
    def _scan(self):
        root = self.path_edit.text().strip()
        if not root:
            self._log("Pick a folder first.")
            return
        system = self.mode.currentData()
        if system:
            self.entries = covers.scan_roms(root, system)
            what = f"{len(self.entries)} ROM(s)"
        else:
            self.entries = covers.find_packages(root)
            what = f"{len(self.entries)} package entrie(s)"
        self.table.setRowCount(0)
        for e in self.entries:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(e["dir"].name))
            label = (covers.SYSTEMS[e["system"]]["label"]
                     if e["system"] else "unknown")
            self.table.setItem(r, 1, QTableWidgetItem(label))
            self.table.setItem(r, 2, QTableWidgetItem(e["key"]))
            self.table.setItem(r, 3,
                               QTableWidgetItem(covers.entry_state(e)))
        self.table.resizeColumnsToContents()
        self._log(f"Scanned {root}: {what}.")

    # ------------------------------------------------------------- fetch
    def _fetch(self):
        if not self.entries:
            self._log("Scan a folder first.")
            return
        replace = self.replace_box.isChecked()
        self.fetch_btn.setEnabled(False)
        entries = list(self.entries)

        def work():
            for row, e in enumerate(entries):
                if covers.entry_state(e) == "have" and not replace:
                    self.bridge.row.emit(row, "have")
                    continue
                self.bridge.log.emit(
                    f"== {e['dir'].name} [{e['system']}] {e['key']}")
                status = covers.fill_entry(
                    e, replace=replace,
                    log=lambda m: self.bridge.log.emit("   " + m))
                self.bridge.row.emit(row, status)
                self.bridge.log.emit(f"   {e['key']}: {status}")
            self.bridge.log.emit("Done.")
            self.bridge.done.emit()

        threading.Thread(target=work, daemon=True).start()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
