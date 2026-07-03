"""
Structured stdout + stderr log handlers for the 'audit' logger.
Wired up in settings.LOGGING — see settings changes below.

  stdout  ← INFO-level records  (successful actions)
  stderr  ← WARNING-level records (failures / errors)
"""

import logging
import sys


class AuditStdoutHandler(logging.StreamHandler):
    """Writes successful (INFO) audit records to stdout."""

    def __init__(self):
        super().__init__(stream=sys.stdout)
        self.setLevel(logging.INFO)

    def emit(self, record):
        if record.levelno < logging.WARNING:
            super().emit(record)


class AuditStderrHandler(logging.StreamHandler):
    """Writes failure / error (WARNING+) audit records to stderr."""

    def __init__(self):
        super().__init__(stream=sys.stderr)
        self.setLevel(logging.WARNING)

    def emit(self, record):
        if record.levelno >= logging.WARNING:
            super().emit(record)
