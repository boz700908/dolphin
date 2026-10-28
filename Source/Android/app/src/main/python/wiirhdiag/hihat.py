"""Read the native Hi-hat demo button sprite's reviewed Chinese artwork.

Authority: game-api.md, Native Hi-hat Demo Button Artwork.
"""
import math
import struct

from .graphics import message, require, word
from .loc import Loc
from .sprites import SpriteHandler


class HihatDemoHandler(SpriteHandler):
    """Follow the scene's actual play/cancel sprite and native draw lifetime."""

    def __init__(self, data):
        self._profile = data.assist['hihat']['demo']
        self._home = data.assist['lyrics']
        self._texture = data.hihat_texture
        super().__init__(data.assist['tutorial_graphics'], self._profile['sprite_registry'], 'Hi-hat demo')

    def _artwork(self, snapshot, manager):
        """Validate the first source texture used by both native hint cells."""
        profile = self._profile
        bank = manager + profile['bank'] * 0x20
        require(snapshot.read(bank + 0xc, 3) == b'\x01\x00\x00', 'Hi-hat demo texture mode')
        require(snapshot.read(bank + 0x10, 2) == b'\x00\x00', 'Hi-hat demo texture index')
        palette = word(snapshot, bank + 4)
        require(snapshot.read(palette, 8) == bytes.fromhex(profile['palette_header_hex']),
                'Hi-hat demo texture palette')
        descriptor = word(snapshot, palette + 8)
        require(word(snapshot, descriptor + 4) == 0, 'Hi-hat demo unexpected indexed texture')
        header = word(snapshot, descriptor)
        expected = bytes.fromhex(profile['texture']['header_hex'])
        require(snapshot.read(header, 8) == expected, 'Hi-hat demo texture header differs from review')
        height, width = struct.unpack('>HH', expected[:4])
        require(snapshot.read(bank + 0x12, 4) == struct.pack('>HH', width, height),
                'Hi-hat demo texture dimensions')
        image = word(snapshot, header + 8)
        require(snapshot.read(image, len(self._texture)) == self._texture,
                'Hi-hat demo texture differs from reviewed source')
        return bank

    def read(self, snapshot, owner, scene):
        """Read the visible bound artwork independently of pattern scrolling."""
        profile = self._profile
        home = word(snapshot, self._home['home_global'])
        require(word(snapshot, home) == self._home['home_vtable'], 'Hi-hat Home manager type')
        if snapshot.read(home + self._home['home_suppression_offset'], 1)[0]:
            return []
        manager = word(snapshot, self._engine['manager_global'])
        require(manager and word(snapshot, manager) == self._engine['manager_vtable'],
                'Hi-hat sprite manager type')
        registered = self._registry(snapshot, manager)
        sprite = word(snapshot, scene + profile['sprite_offset'])
        if sprite not in registered:
            return []
        raw = self._sprite(snapshot, sprite, manager)
        animation = struct.unpack_from('>H', raw, 0xe)[0]
        require(raw[0xc] == profile['bank'] and str(animation) in profile['animations'],
                'Hi-hat demo sprite bank/animation')
        require(raw[0xc2] == 0 or word(snapshot, sprite + 0xb0) == 0,
                'Hi-hat demo unexpectedly draws under parent')
        if not raw[0xa7] or not raw[0xa6]:
            return []
        bank = self._artwork(snapshot, manager)
        cells, animations = struct.unpack('>HH', snapshot.read(bank + 0x16, 4))
        require(animation < animations, 'Hi-hat demo animation bounds')
        entry = word(snapshot, bank + 0x20) + animation * 8
        source = profile['animations'][str(animation)]
        require(struct.unpack('>H', snapshot.read(entry, 2))[0] == len(source),
                'Hi-hat demo animation key count')
        keys = word(snapshot, entry + 4)
        for index, key in enumerate(source):
            # Native key allocation leaves three trailing padding bytes uninitialized.
            require(snapshot.read(keys + index * 24, 21) == bytes.fromhex(key['key_hex'])[:21],
                    'Hi-hat demo animation differs from reviewed source')
        frame = struct.unpack_from('>f', raw, 0x44)[0]
        require(math.isfinite(frame) and frame >= 0, 'Hi-hat demo animation frame')
        order = list(reversed(source)) if raw[0xab] else source
        elapsed, selected = 0, order[-1]
        for key in order:
            elapsed += key['frames']
            if elapsed > frame:
                selected = key
                break
        if bytes.fromhex(selected['key_hex'])[20] == 0:
            return []
        require(selected['cell'] < cells, 'Hi-hat demo cell bounds')
        entry = word(snapshot, bank + 0x1c) + selected['cell'] * 8
        require(struct.unpack('>H', snapshot.read(entry, 2))[0] == len(selected['objects_hex']),
                'Hi-hat demo cell object count')
        objects = word(snapshot, entry + 4)
        visible = False
        for index, encoded in enumerate(selected['objects_hex']):
            expected = bytes.fromhex(encoded)
            observed = snapshot.read(objects + index * 32, 32)
            x, y = struct.unpack_from('>hh', expected, 12)
            require(observed[:10] == expected[:10] and
                    observed[10:14] == struct.pack('>hh', x - 512, y - 512) and
                    observed[16:31] == expected[16:31],
                    'Hi-hat demo geometry differs from reviewed source')
            visible |= bool(observed[30])
        if not visible:
            return []
        return [message(owner, 'hihat:demo', Loc.Get(profile['labels'][str(animation)]),
                        'hihat_demo', 'hihat')]
