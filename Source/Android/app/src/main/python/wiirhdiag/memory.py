'''Read the emulated console's RAM through the emulator's own mapping.

The addresses come from the emulator, so nothing here scans process memory,
opens a shared mapping or guesses a region: the guest RAM the game is running
in is read as it is. See docs/game-api.md, Native Drawing Manager Evidence.
'''

import ctypes
import os
import struct
from datetime import datetime, timezone
from time import perf_counter

from .bridge import AssistRuntime
from .errors import DiagnosticError
from .snapshot import MEM1_SIZE, Snapshot


class GuestRegion:
    '''A window onto guest RAM; a slice copies only the bytes asked for.'''

    def __init__(self, address, size):
        self._address = address
        self._size = size

    def __len__(self):
        return self._size

    def __getitem__(self, index):
        '''Return bytes for a slice, matching the byte string the readers expect.'''
        if isinstance(index, slice):
            start, stop, step = index.indices(self._size)
            if step != 1:
                raise TypeError('guest RAM slices must be contiguous')
            return ctypes.string_at(self._address + start, stop - start)
        if index < 0:
            index += self._size
        if not 0 <= index < self._size:
            raise IndexError(index)
        return ctypes.string_at(self._address + index, 1)


class MemoryHandler:
    '''Validate the running game once per capture, then expose its RAM.'''

    def __init__(self, data, logger):
        self._data = data
        self._logger = logger
        self._validation = None

    def _validate(self, mem1):
        '''Check the running build without assigning menu or game state.'''
        head = mem1[:0x100]
        if head[:6] != b'SOMJ01':
            raise DiagnosticError('wrong_game', game=head[:6].hex())
        # Boot metadata: the translation is supported only at 24 MiB of MEM1.
        # MEM1 placement itself is taken from the emulator, not assumed.
        if struct.unpack_from('>I', head, 0x28)[0] != MEM1_SIZE:
            raise DiagnosticError('ram_header')
        if struct.unpack_from('>I', head, 0xf0)[0] != MEM1_SIZE:
            raise DiagnosticError('ram_header')

    def capture(self):
        '''Return a snapshot of both regions for this iteration.'''
        address1, size1 = AssistRuntime.mem1Address(), AssistRuntime.mem1Size()
        address2, size2 = AssistRuntime.mem2Address(), AssistRuntime.mem2Size()
        if not address1 or not address2 or size1 < MEM1_SIZE:
            raise DiagnosticError('no_process')
        started = datetime.now(timezone.utc).isoformat()
        timer = perf_counter()
        # Both regions are read in place; nothing is copied up front, so a
        # capture costs only the bytes the readers actually look at.
        mem1, mem2 = GuestRegion(address1, size1), GuestRegion(address2, size2)
        self._validate(mem1)
        self._data.identity(mem1)
        if self._logger.enabled or self._validation != (address1, size1):
            self._logger.event('Guest RAM attached', mem1=hex(address1), size=size1)
            self._validation = (address1, size1)
        metadata = {'pid': os.getpid(), 'started_utc': started,
                    'finished_utc': datetime.now(timezone.utc).isoformat(),
                    'read_seconds': perf_counter() - timer, 'game_id': 'SOMJ01',
                    'acquisition': 'Guest RAM read in place through the emulator',
                    'coherence': 'Non-atomic sequential reads; frame coherence unverified.',
                    'mem1_size': size1, 'mem2_size': size2}
        return Snapshot(mem1, mem2), metadata
