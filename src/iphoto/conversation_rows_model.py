"""Incremental QML model for long conversation histories."""

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class ConversationRowsModel(QAbstractListModel):
    ROW_ROLE = Qt.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def roleNames(self):
        return {self.ROW_ROLE: b"messageRow"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if role == self.ROW_ROLE and index.isValid() and 0 <= index.row() < len(self._rows):
            return self._rows[index.row()]
        return None

    def replace(self, messages):
        self.beginResetModel()
        self._rows = list(messages)
        self.endResetModel()

    def append(self, message):
        position = len(self._rows)
        self.beginInsertRows(QModelIndex(), position, position)
        self._rows.append(message)
        self.endInsertRows()

    def refresh(self, message_id):
        for position, message in enumerate(self._rows):
            if message["id"] == message_id:
                index = self.index(position, 0)
                self.dataChanged.emit(index, index, [self.ROW_ROLE])
                return
