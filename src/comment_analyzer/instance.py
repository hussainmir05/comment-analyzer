"""One running copy per data folder.

Two copies would fight over data.db and the browser profile folders, so a second launch hands
its link (or "" to just bring the window forward) to the running copy and exits.
"""

import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QEventLoop, QLockFile, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


class SingleInstance(QObject):
    message_received = Signal(str)

    def __init__(self, data_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        digest = hashlib.sha256(str(data_dir.resolve()).lower().encode()).hexdigest()[:16]
        self.server_name = f"CommentAnalyzer-{digest}"
        self._lock = QLockFile(str(data_dir / "instance.lock"))
        self._lock.setStaleLockTime(0)  # never stale by age; a crashed owner is found by its PID
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """True if no other copy runs; this copy then listens for messages."""
        if not self._lock.tryLock(100):
            return False
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.server_name):
            QLocalServer.removeServer(self.server_name)  # left behind by a crash
            if not server.listen(self.server_name):
                log.warning("Can't listen for other launches: %s", server.errorString())
        server.newConnection.connect(self._accept)
        self._server = server
        return True

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()

    def send(self, message: str, timeout_ms: int = 3000) -> bool:
        """Hand a message to the running copy. Returns False if it couldn't be reached."""
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(timeout_ms):
            return False
        # On Windows the write only happens inside an event loop (waitForBytesWritten returns
        # False at once), so run one until the socket has sent everything and disconnected.
        loop = QEventLoop()
        socket.disconnected.connect(loop.quit)
        QTimer.singleShot(timeout_ms, loop.quit)
        socket.write((message + "\n").encode("utf-8"))
        socket.disconnectFromServer()  # sends pending data first
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            loop.exec()
        return socket.state() == QLocalSocket.LocalSocketState.UnconnectedState

    def _accept(self) -> None:
        assert self._server is not None
        while (socket := self._server.nextPendingConnection()) is not None:
            buffer = bytearray()

            def read(sock: QLocalSocket = socket, buf: bytearray = buffer) -> None:
                buf.extend(sock.readAll().data())
                if b"\n" in buf:
                    sock.readyRead.disconnect()
                    message = bytes(buf).split(b"\n", 1)[0].decode("utf-8", "replace")
                    sock.disconnectFromServer()
                    sock.deleteLater()
                    log.info("Another launch handed over %s", "a link" if message else "focus")
                    self.message_received.emit(message)

            socket.readyRead.connect(read)
            if socket.bytesAvailable():
                read()
