'''Verify complete target font with only previously proven rebuild normalization.'''

from hashlib import sha256
import struct

from .errors import DiagnosticError


class FontHandler:
    '''Compare live resource against reviewed source before allowing narration.'''

    def __init__(self, source):
        self._source = source

    def verify(self, snapshot, font):
        '''Reject unknown font contents; pointers and alternative glyph are normalized.'''
        def require(condition, detail):
            if not condition:
                raise DiagnosticError('font_invalid', detail=detail)
        require(snapshot.contains(font, 0x18), 'font object bounds')
        resource, finf = struct.unpack('>II', snapshot.read(font + 0x10, 8))
        source = self._source
        require(snapshot.contains(resource, len(source)), 'resource bounds')
        live = snapshot.read(resource, len(source))
        require(live[:4] == b'RFNU' and live[4:16] == source[4:16], 'resource header')
        normalized = bytearray(live)
        normalized[:4] = source[:4]
        offset = struct.unpack_from('>H', source, 12)[0]
        for _ in range(struct.unpack_from('>H', source, 14)[0]):
            kind, size = struct.unpack_from('>4sI', source, offset)
            require(live[offset:offset + 8] == source[offset:offset + 8], 'block header')
            if kind == b'FINF':
                require(finf == resource + offset + 8, 'FINF object pointer')
                normalized[offset + 10:offset + 12] = source[offset + 10:offset + 12]
                fields = [offset + 16, offset + 20, offset + 24]
            elif kind == b'TGLP':
                fields = [offset + 28]
            elif kind == b'CWDH':
                fields = [offset + 12]
            elif kind == b'CMAP':
                fields = [offset + 16]
            else:
                raise DiagnosticError('font_invalid', detail='unexpected block type')
            for position in fields:
                original = struct.unpack_from('>I', source, position)[0]
                expected = resource + original if original else 0
                require(struct.unpack_from('>I', live, position)[0] == expected, 'rebuilt pointer')
                normalized[position:position + 4] = source[position:position + 4]
            offset += size
        require(offset == len(source), 'block traversal')
        require(normalized == source, 'font contents differ from reviewed resource')
        return sha256(live).hexdigest()
