'''Announce localized messages through the platform's own speech service.'''

from .bridge import AssistRuntime
from .loc import Loc


class SpeechHandler:
    '''Speak through Android text-to-speech; never block the reading loop.'''

    def __init__(self, logger, enabled=True):
        self._logger = logger
        self._enabled = enabled

    def say(self, key, **values):
        '''Localize one message and hand it to the speech queue.'''
        text = Loc.Get(key, **values)
        if not self._enabled:
            return
        # Queued, not awaited: the platform serialises utterance itself, so the
        # reading loop keeps its rate no matter how long a message takes.
        AssistRuntime.speak(text)

    def clear(self):
        '''Drop messages that have not started, so stale text is not read.'''
        AssistRuntime.clearSpeech()

    def close(self):
        '''Leave queued speech alone; the platform finishes it.'''
