'''What the assist reports about failures, as the launcher version reports it.

A failure here is not a single event but an episode. Every area that can fail on
its own -- the reading of guest RAM, the emulator's process, the cue playback --
keeps one, and an episode is recorded the moment the failure changes, and not
again while the same failure keeps happening, however long that is. When the
area works once more the episode is over, and that is recorded too. An emulator
that is simply not running ends every episode without a recovery being reported:
it is expected, not broken. A failure the assist cannot survive is reported
separately as fatal, so it is not mistaken for one it waits out.

Nothing here is spoken. In the launcher version these reports went to the
launcher's own window, and a build with no window sends them to the app log; the
one thing a player hears from this file is the notification tone, which is what
tells someone who cannot see a window that an area has stopped.
'''

import math

from .loc import Loc

# The text each report is written with, the launcher window's own.
FAILED_KEY = 'gui_diagnostic'
RECOVERED_KEY = 'assist_recovered'
FATAL_KEY = 'gui_fatal'

# How long a failure has to last before the notification tone sounds, once for
# each episode. The launcher version makes this a setting and defaults to this
# value. The tone is what tells a player who cannot see a window that an area
# has stopped, so a failure that is over within the grace is only recorded
# rather than breaking into the game audio for nothing.
NOTICE_GRACE = 1.5
MAX_NOTICE_GRACE = 30


class DiagnosticNoticeHandler:
    '''Delay only the notification tone, recording every change immediately.'''

    def __init__(self, channel, logger, tone=None, grace=NOTICE_GRACE):
        if not math.isfinite(grace) or grace < 0 or grace > MAX_NOTICE_GRACE:
            raise ValueError('Notification grace must be between zero and 30 seconds')
        self._channel = channel
        self._logger = logger
        self._tone = tone
        self._grace = grace

        # area -> when its episode started, the failure being recorded there, and
        # whether the tone has already sounded for it.
        self._states = {}

    def failure(self, area, error, now):
        '''Record a failure immediately, sounding once for a sustained episode.'''
        signature = type(error).__name__, str(error)
        state = self._states.get(area)
        if state is None:
            state = {'started': now, 'signature': None, 'sounded': False}
            self._states[area] = state

        if state['signature'] != signature:
            self._logger.event('Diagnostic failed', area=area, error=str(error),
                               key=getattr(error, 'key', None),
                               values=getattr(error, 'values', {}))
            self._channel.emit('diagnostic', area=area,
                               text=Loc.Get(FAILED_KEY, detail=str(error)))
            state['signature'] = signature

        if state['sounded'] or now - state['started'] < self._grace:
            return

        state['sounded'] = True
        if self._tone is not None:
            self._tone()
        self._channel.emit('diagnostic_sound', area=area)

    def recover(self, area):
        '''Retire one area's episode, reporting only its recovery.'''
        if area not in self._states:
            return
        del self._states[area]
        self._logger.event('Diagnostic recovered', area=area)
        self._channel.emit('recovered', area=area, text=Loc.Get(RECOVERED_KEY))

    def cancel(self):
        '''Retire every episode while the game is absent, without a recovery.'''
        ended = bool(self._states)
        if ended:
            self._logger.event('Diagnostic episodes ended with the game absent',
                               areas=list(self._states))
            self._states.clear()
        return ended

    def fatal(self, error):
        '''Report a failure the assist cannot survive as one it cannot survive.'''
        self._logger.event('Fatal assistant error', error=str(error))
        self._channel.emit('fatal', text=Loc.Get(FATAL_KEY, detail=str(error)))
