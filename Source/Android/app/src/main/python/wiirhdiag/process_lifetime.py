'''Track whether the emulated game is still running, without affecting it.'''

from .bridge import AssistRuntime
from .loc import Loc


class ProcessLifetimeHandler:
    '''Treat the emulator's guest RAM being gone as the game having ended.'''

    def __init__(self):
        self._bound = False

    def bind(self):
        '''Remember that a validated game was seen; nothing to open.'''
        self._bound = True

    def exited(self):
        '''Report the game as gone once its guest RAM is no longer readable.'''
        return self._bound and not AssistRuntime.gameRamReady()

    def close(self):
        '''Forget the observation; never stop the emulator.'''
        self._bound = False


class GameConnectionHandler:
    '''Publish connected, exited and waiting states to the reading loop.'''

    def __init__(self, lifetime, logger):
        self._lifetime = lifetime
        self._logger = logger
        self._state = None

    def attach(self, pid):
        '''Report whether this verified capture establishes a new connection.'''
        changed = self._state != 'attached'
        if changed:
            self._lifetime.bind()
            self._state = 'attached'
        return changed

    def poll(self):
        '''Report exit only for a game that was attached.'''
        if self._state != 'attached' or not self._lifetime.exited():
            return False
        self._lifetime.close()
        self._state = 'waiting'
        self._logger.event('Attached game ended')
        return True

    def waiting(self):
        '''Treat an absent game as expected waiting, emitting only changes.'''
        if self._state == 'waiting':
            return False
        self._state = 'waiting'
        self._logger.event('Waiting for game')
        return True
