'''Immutable views of captured game RAM; all address resolution is bounded.'''

from .errors import DiagnosticError

# Dolphin standard Wii configuration; primary evidence in game-api.md.
MEM1_BASE = 0x80000000
MEM1_SIZE = 0x01800000
MEM1_ALLOCATION = 0x02000000
L1_ALLOCATION = 0x00040000
MEM2_BASE = 0x90000000
MEM2_SIZE = 0x04000000
MEM2_FILE_OFFSET = MEM1_ALLOCATION + L1_ALLOCATION
MAPPING_SIZE = MEM2_FILE_OFFSET + MEM2_SIZE


class Snapshot:
    '''Read only within one of the two captured emulated RAM ranges.'''

    def __init__(self, mem1, mem2):
        self.regions = [(MEM1_BASE, mem1), (MEM2_BASE, mem2)]

    def contains(self, address, length):
        '''Test a whole range without crossing a memory boundary.'''
        return length >= 0 and any(base <= address and address + length <= base + len(data)
                                  for base, data in self.regions)

    def read(self, address, length):
        '''Return immutable bytes or report an out-of-range error.'''
        for base, data in self.regions:
            if length >= 0 and base <= address and address + length <= base + len(data):
                offset = address - base
                return data[offset:offset + length]
        raise DiagnosticError('outside_snapshot')
