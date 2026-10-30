'''Announce localized messages through the platform's own speech service.'''

from .bridge import AssistRuntime
from .loc import Loc


class SpeechHandler:
    '''Speak through Android text-to-speech; never block the reading loop.'''

    def __init__(self, logger, enabled=True):
        self._logger = logger
        self._enabled = enabled

    def say(self, key, flush=False, waits=False, **values):
        '''Localize one message and hand it to the speech queue.

        Queued, not awaited: the platform serialises utterances itself, so the
        reading loop keeps its rate no matter how long a message takes.

        flush asks for the message to replace whatever is audible instead of
        queueing behind it. The caller sets it only for a message the player
        asked for by pressing something, because that message is the answer to
        the press and the sentence still being read describes the moment before
        it.

        waits says that this one message stays on screen until a press moves it
        on, so a press may end it. It is asked of each message and never of the
        screen: a lesson can show a dialogue box and the round's own hints at
        the same time, and only the box is what a press answers.
        '''
        text = Loc.Get(key, **values)
        if not self._enabled:
            return
        AssistRuntime.speak(text, flush, waits)

    def clear(self):
        '''Drop messages that have not started, so stale text is not read.'''
        AssistRuntime.clearSpeech()

    def close(self):
        '''Leave queued speech alone; the platform finishes it.'''
