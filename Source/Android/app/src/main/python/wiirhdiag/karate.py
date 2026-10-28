"""Read the native Karate word sprite's currently displayed artwork.

Authority: game-api.md, Native Karate Word Artwork and Offline State Recovery.
"""
import math
import struct

from .graphics import require, word, message
from .loc import Loc
from .sprites import SpriteHandler


class KarateHandler(SpriteHandler):
    """Follow scene-owned word frames instead of estimating gameplay timing."""

    def __init__(self, data):
        self._profile = data.assist['karate']
        self._home = data.assist['lyrics']
        self._textures = data.karate_textures
        super().__init__(data.assist['tutorial_graphics'], self._profile['sprite_registry'], 'karate')

    def _artwork(self, snapshot, manager):
        profile = self._profile
        bank = manager + profile['bank'] * 0x20
        require(snapshot.read(bank + 0xc, 3) == b'\x01\x00\x00', 'karate artwork bank texture mode')
        require(snapshot.read(bank + 0x10, 2) == b'\x00\x00', 'karate texture index')
        palette = word(snapshot, bank + 4)
        require(snapshot.read(palette, 4) == bytes.fromhex(profile['palette_header_hex'])[:4],
                'karate native texture palette magic')
        palette_count = word(snapshot, palette + 4)
        variants = [(name, spec) for name, spec in profile['textures'].items()
                    if spec['palette_count'] == palette_count]
        require(variants, 'karate native texture palette count')
        descriptor = word(snapshot, palette + 8)
        require(word(snapshot, descriptor + 4) == 0, 'karate unexpected indexed texture')
        header = word(snapshot, descriptor)
        image = word(snapshot, header + 8)
        matches = []
        for name, spec in variants:
            require(snapshot.read(header, 8) == bytes.fromhex(spec['header_hex']),
                    'karate texture header differs from review')
            height, width = struct.unpack('>HH', bytes.fromhex(spec['header_hex'])[:4])
            require(snapshot.read(bank + 0x12, 4) == struct.pack('>HH', width, height),
                    'karate texture dimensions')
            if snapshot.read(image, len(self._textures[name])) == self._textures[name]:
                matches.append(spec)
        require(matches, 'karate artwork texture differs from reviewed sources')
        source = matches[0]['artwork']
        require(all(spec['artwork'] == source for spec in matches), 'ambiguous karate source geometry')
        return bank, source

    def read(self, snapshot, scene):
        """Read only native drawing-list members with enabled, verified artwork."""
        profile = self._profile
        address = scene['scene']
        if word(snapshot, address) != profile['scene_vtable']:
            return []
        home = word(snapshot, self._home['home_global'])
        require(word(snapshot, home) == self._home['home_vtable'], 'karate Home manager type')
        if snapshot.read(home + self._home['home_suppression_offset'], 1)[0]:
            return []
        manager = word(snapshot, self._engine['manager_global'])
        require(manager and word(snapshot, manager) == self._engine['manager_vtable'],
                'karate sprite manager type')
        registered = self._registry(snapshot, manager)
        sprite = word(snapshot, address + profile['sprite_offset'])
        if sprite not in registered:
            return []
        raw = self._sprite(snapshot, sprite, manager)
        require(raw[0xc] == profile['bank'] and struct.unpack_from('>H', raw, 0xe)[0] == profile['animation'],
                'karate word sprite bank/animation')
        require(raw[0xc2] == 0 or word(snapshot, sprite + 0xb0) == 0,
                'karate word unexpectedly draws under parent')
        if not raw[0xa7] or not raw[0xa6]:
            return []
        bank, source = self._artwork(snapshot, manager)
        cells, animations = struct.unpack('>HH', snapshot.read(bank + 0x16, 4))
        require(profile['animation'] < animations, 'karate animation bounds')
        entry = word(snapshot, bank + 0x20) + profile['animation'] * 8
        require(struct.unpack('>H', snapshot.read(entry, 2))[0] == len(source), 'karate animation key count')
        keys = word(snapshot, entry + 4)
        for index, key in enumerate(source):
            # Native key allocation leaves three trailing padding bytes uninitialized.
            require(snapshot.read(keys + index * 24, 21) == bytes.fromhex(key['key_hex'])[:21],
                    'karate word animation differs from reviewed source')
        frame = struct.unpack_from('>f', raw, 0x44)[0]
        require(math.isfinite(frame) and frame >= 0, 'karate word animation frame')
        order = list(reversed(source)) if raw[0xab] else source
        elapsed, selected = 0, order[-1]
        for key in order:
            elapsed += key['frames']
            if elapsed > frame:
                selected = key
                break
        if bytes.fromhex(selected['key_hex'])[20] == 0:
            return []
        require(selected['cell'] < cells, 'karate word cell bounds')
        entry = word(snapshot, bank + 0x1c) + selected['cell'] * 8
        require(struct.unpack('>H', snapshot.read(entry, 2))[0] == len(selected['objects_hex']),
                'karate word cell object count')
        objects = word(snapshot, entry + 4)
        visible = False
        for index, encoded in enumerate(selected['objects_hex']):
            expected = bytes.fromhex(encoded)
            observed = snapshot.read(objects + index * 32, 32)
            x, y = struct.unpack_from('>hh', expected, 12)
            require(observed[:10] == expected[:10] and
                    observed[10:14] == struct.pack('>hh', x - 512, y - 512) and
                    observed[16:31] == expected[16:31], 'karate word geometry differs from reviewed source')
            visible |= bool(observed[30])
        if not visible:
            return []
        key = profile['labels'].get(str(selected['cell']))
        require(key is not None, 'unreviewed karate word cell')
        return [message({'owner_vtable':profile['scene_vtable']}, 'karate:word', Loc.Get(key),
                        'karateman_Word', 'karate')]
