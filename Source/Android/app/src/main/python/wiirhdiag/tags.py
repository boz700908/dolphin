"""Verified speech-only presentation tags. Raw diagnostics keep the original bytes."""

import struct

from .graphics import require, word
from .loc import Loc
from .panes import render_candidate


class TextTagsHandler:
    """Decode reviewed presentation tags proven by the native tag processor."""

    def __init__(self, data):
        self._profile = data.assist['text_tags']
        self._mapping = data.mapping
        self._blank_codes = data.blank_codes
        self._allowed = {bytes.fromhex(tag) for tag in self._profile['presentation_tags']}

    def speech_text(self, snapshot, pane):
        """Decode tagged dialogue; preserve the established untagged glyph policy."""
        raw = bytes.fromhex(pane['raw_buffer_hex'])
        if any(raw[offset:offset + 2] == b'\x00\x1a' for offset in range(0, len(raw), 2)):
            return self.read(snapshot, pane)
        return Loc.Get('assist_text', text=pane['text'].strip())

    def read(self, snapshot, pane):
        """Reject unknown/truncated tags and verify the actual TextBox processor."""
        raw = bytes.fromhex(pane['raw_buffer_hex'])
        require(len(raw) % 2 == 0 and raw[-2:] == b'\0\0', 'tagged text terminator')
        processor = word(snapshot, pane['address'] + self._profile['offset'])
        require(processor != 0 and word(snapshot, processor) == self._profile['vtable'],
                'text tag processor binding')
        codes = []
        offset = 0
        while offset < len(raw) - 2:
            code = struct.unpack_from('>H', raw, offset)[0]
            if code == 0x1a:
                require(offset + 4 <= len(raw) - 2, 'truncated text tag header')
                length = raw[offset + 2]
                require(length >= 6 and length % 2 == 0 and offset + length <= len(raw) - 2,
                        'text tag length')
                require(raw[offset:offset + length] in self._allowed,
                        'unsupported text presentation tag',
                        tag_hex=raw[offset:offset + length].hex(), tag_offset=offset,
                        pane=pane['name'], pane_address=pane['address'],
                        raw_buffer_hex=pane['raw_buffer_hex'])
                offset += length
            else:
                codes.append(code)
                offset += 2
        text, markers = render_candidate(codes, self._mapping, self._blank_codes)
        require(all(marker['kind'] in ('confirmed_button_glyph', 'confirmed_symbol_glyph') for marker in markers),
                'tagged text contains unsupported glyph/control',
                markers=markers, pane=pane['name'], pane_address=pane['address'],
                raw_buffer_hex=pane['raw_buffer_hex'])
        return Loc.Get('assist_text', text=text.strip())
