"""Keep dialogs modal to their owner while independent player tools stay usable."""
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox


def exec_for_window(dialog):
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    return dialog.exec()


def pick_files(dialog_type, parent, title, file_filter, *, directory="", multiple=False):
    dialog = dialog_type(parent, title, directory, file_filter)
    mode = dialog_type.FileMode.ExistingFiles if multiple else dialog_type.FileMode.ExistingFile
    dialog.setFileMode(mode)
    try:
        if exec_for_window(dialog) == QDialog.DialogCode.Accepted and not sip.isdeleted(dialog):
            return dialog.selectedFiles()
        return []
    finally:
        if not sip.isdeleted(dialog):
            dialog.deleteLater()


def prompt_text(parent, title, label, *, choices=None):
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    if choices is not None:
        dialog.setComboBoxItems(choices)
        dialog.setComboBoxEditable(False)
    try:
        if exec_for_window(dialog) == QDialog.DialogCode.Accepted and not sip.isdeleted(dialog):
            return dialog.textValue(), True
        return "", False
    finally:
        if not sip.isdeleted(dialog):
            dialog.deleteLater()


def show_message(parent, icon, title, text, buttons=QMessageBox.StandardButton.Ok, default=None):
    dialog = QMessageBox(icon, title, text, buttons, parent)
    if default is not None:
        dialog.setDefaultButton(default)
    try:
        return exec_for_window(dialog)
    finally:
        if not sip.isdeleted(dialog):
            dialog.deleteLater()
