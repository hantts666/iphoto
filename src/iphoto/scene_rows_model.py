"""Stable object delegates within one analyzed catalog; changed rows update in place."""

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class SceneRowsModel(QAbstractListModel):
    ROW_ROLE = Qt.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._revision = -1

    def roleNames(self):
        return {self.ROW_ROLE: b"sceneRow"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if role == self.ROW_ROLE and index.isValid() and 0 <= index.row() < len(self._rows):
            return self._rows[index.row()]
        return None

    def replace(self, rows, revision):
        if revision != self._revision or [row["id"] for row in rows] != [row["id"] for row in self._rows]:
            self.beginResetModel()
            self._rows, self._revision = rows, revision
            self.endResetModel()
            return
        for position, row in enumerate(rows):
            if row != self._rows[position]:
                self._rows[position] = row
                index = self.index(position, 0)
                self.dataChanged.emit(index, index, [self.ROW_ROLE])
