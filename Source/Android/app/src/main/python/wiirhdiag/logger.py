'''Session events, kept in logcat instead of on the device.'''

from datetime import datetime, timezone

from .bridge import AssistRuntime


class DebugLogger:
    '''Preserve errors even when detailed debugging is disabled.'''

    def __init__(self, path=None, enabled=False, event_enabled=True):
        # path is accepted for the call sites' sake; nothing is written to it.
        self.path = path
        self.enabled = enabled
        self.event_enabled = event_enabled

    def event(self, name, **details):
        '''Write one deliberate acquisition/error event.'''
        if not self.event_enabled:
            return
        fields = ' '.join('%s=%s' % (key, value) for key, value in details.items())
        AssistRuntime.log('%s %s %s' % (datetime.now(timezone.utc).isoformat(), name, fields))

    def debug(self, name, **details):
        '''Skip formatting when verbose logging is off.'''
        if self.enabled:
            self.event(name, **details)
