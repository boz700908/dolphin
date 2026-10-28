'''Kungfu cue playback, mixed by the platform alongside the game audio.'''
import math
from pathlib import Path
import struct

from .bridge import AssistRuntime
from .errors import DiagnosticError

# The launcher version generates its notification tone instead of shipping a
# sound asset, and names the file the same way so both sides agree on it.
NOTICE_FILENAME = 'diagnostic-notice.wav'
NOTICE_KEY = 'notice:0'

# The tone is a fixed short chime, not a message: 0.22 s of two tones together,
# faded at both ends so it does not click.
NOTICE_RATE = 22050
NOTICE_SECONDS = .22


def write_notice_wave(directory):
    '''Write the notification tone beside the cue sounds, returning its path.

    The header is assembled here rather than through the wave module, because
    the format is one fixed choice and nothing reads it back.
    '''
    frames = bytearray()
    for index in range(int(NOTICE_RATE * NOTICE_SECONDS)):
        at = index / NOTICE_RATE
        envelope = min(1.0, at / .015, (NOTICE_SECONDS - at) / .035)
        sample = .15 * envelope * (math.sin(2 * math.pi * 660 * at) +
                                   .35 * math.sin(2 * math.pi * 990 * at))
        frames.extend(struct.pack('<h', int(sample * 32767)))

    # Canonical RIFF/WAVE header for one mono 16-bit stream.
    header = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + len(frames), b'WAVE',
                         b'fmt ', 16, 1, 1, NOTICE_RATE, NOTICE_RATE * 2, 2, 16,
                         b'data', len(frames))
    path = Path(directory) / NOTICE_FILENAME
    path.write_bytes(header + bytes(frames))
    return path


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
