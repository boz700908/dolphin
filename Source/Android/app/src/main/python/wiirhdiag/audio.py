'''Kungfu cue playback, mixed by the platform alongside the game audio.'''
from pathlib import Path

from .bridge import AssistRuntime
from .errors import DiagnosticError


class AudioHandler:
    '''Play one short cue at a time; a new cue replaces the previous one.'''

    def __init__(self, directory, profile):
        # The asset table names one file per player and distance, because each
        # file already carries its player in the right channel.
        self._keys = set(profile['assets'])
        for key, asset in profile['assets'].items():
            path = Path(directory) / asset['filename']
            if not path.is_file():
                raise DiagnosticError('data_missing', path=str(path))
            if key not in self._keys:
                raise DiagnosticError('kungfu_audio_invalid', sound=key)

    def play(self, player, distance, asynchronous=True):
        '''Play the cue for one player at one distance.'''
        key = '%s:%d' % (distance, player)
        if key not in self._keys:
            raise DiagnosticError('kungfu_audio_invalid', sound=key)
        # Playback never blocks the reading loop: the platform mixes the cue
        # with the game audio and stops the previous one for us.
        AssistRuntime.playCue(key, False)

    def stop(self):
        '''Stop this application's cue playback only.'''
        AssistRuntime.stopCue()
