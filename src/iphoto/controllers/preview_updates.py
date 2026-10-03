"""Bounded preview cadence and safe intermediate parameter frames."""


def changed(self, *, parameter=False):
    previous = self._generation
    self._generation += 1
    if parameter:
        span = self._parameter_preview_span
        first = span[0] if span and span[1] == previous else self._generation
        self._parameter_preview_span = (first, self._generation)
        # Keep the scheduled deadline while input continues. The worker queues
        # already retain only the latest pending snapshot.
        if not self._timer.isActive():
            self._timer.start()
    else:
        self._parameter_preview_span = None
        self._timer.start()
    self.changed.emit()


def accepts(self, generation):
    """Allow completed frames only across uninterrupted parameter changes."""
    if generation == self._generation:
        return True
    span = getattr(self, "_parameter_preview_span", None)
    return bool(span and span[1] == self._generation and span[0] <= generation < span[1])
