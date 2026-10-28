'''What the assist says about failures, as the launcher version has always done.

A failure here is not a single event but an episode. Every area of the assist
that can fail on its own -- the reading of guest RAM, the emulator's process,
the cue playback -- keeps one, and an episode is reported the moment the failure
changes and not again while the same failure keeps happening, however long that
is. When the area works once more the episode is over, and that is said too,
because a player who was told that reading had stopped has to be told that it
started again. An emulator that is simply not running ends every episode without
a word: it is expected, not broken. A failure the assist cannot survive is
reported separately as fatal, so it is not mistaken for one it waits out.

The launcher version shows these reports in its own status area; here the only
way to the player is speech, so each one is read out through the same localized
text the launcher version uses.
'''

# The text each kind of report is read from.
FAILED_KEY = 'gui_diagnostic'
RECOVERED_KEY = 'assist_recovered'
FATAL_KEY = 'gui_fatal'


class DiagnosticNoticeHandler:
    '''One episode per area, opened by a failure and closed by a recovery.'''

    def __init__(self, speech, logger):
        self._speech = speech
        self._logger = logger

        # area -> the (type, text) of the failure being reported there.
        self._states = {}

    def _say(self, key, **values):
        if self._speech is not None:
            self._speech.say(key, **values)

    def failure(self, area, error):
        '''Record a failure, reading out one report per episode.'''
        signature = type(error).__name__, str(error)
        if self._states.get(area) == signature:
            # The same failure still happening: the player already knows.
            return

        self._states[area] = signature
        self._logger.event('Diagnostic failed', area=area, error=str(error),
                           key=getattr(error, 'key', None),
                           values=getattr(error, 'values', {}))
        self._say(FAILED_KEY, detail=str(error))

    def recover(self, area):
        '''Close an area's episode, announcing that it is over.'''
        if self._states.pop(area, None) is None:
            return

        self._logger.event('Diagnostic recovered', area=area)
        self._say(RECOVERED_KEY)

    def cancel(self):
        '''End every episode without a word, for an emulator that is not running.

        Returns whether anything was being reported, so the caller knows whether
        the state it is publishing is news.
        '''
        if not self._states:
            return False

        self._logger.event('Diagnostic episodes ended with game absence',
                           areas=list(self._states))
        self._states.clear()
        return True

    def fatal(self, error):
        '''Report a failure the assist does not survive, as opposed to a episode.'''
        self._logger.event('Fatal assistant error', error=str(error),
                           key=getattr(error, 'key', None),
                           values=getattr(error, 'values', {}))
        self._say(FATAL_KEY, detail=str(error))
