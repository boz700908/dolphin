"""Read native endless result artwork and bound decimal sprites.

Authority: docs/game-api.md, Endless Result Verified Bindings and Artwork.
"""

import math
import struct

from .graphics import require, word, message
from .loc import Loc
from .sprites import SpriteHandler


class EndlessResultHandler(SpriteHandler):
    """Interpret reviewed artwork only in verified native result scenes."""

    def __init__(self, data):
        self._profile = data.assist['endless_result']
        self._engine = data.assist['tutorial_graphics']
        self._textures = data.endless_textures
        super().__init__(self._engine, self._profile['sprite_registry'], 'endless')

    def _texture(self, snapshot, manager, bank, kind, verified):
        if bank in verified:
            return
        binding = manager + bank * 0x20
        flags = snapshot.read(binding + 0xc, 3)
        require(flags == b'\x01\x00\x00', 'endless artwork bank texture mode')
        require(snapshot.read(binding + 0x10, 2) == b'\x00\x00', 'endless texture index')
        palette = word(snapshot, binding + 4)
        require(snapshot.read(palette, 8) == bytes.fromhex('0020af3000000001'),
                'endless texture palette differs from review')
        descriptor = word(snapshot, palette + 8)
        require(word(snapshot, descriptor + 4) == 0, 'endless unexpected indexed texture')
        header = word(snapshot, descriptor)
        expected = bytes.fromhex(self._profile['textures'][kind]['header_hex'])
        require(snapshot.read(header, 8) == expected, 'endless texture header differs from review')
        height, width = struct.unpack('>HH', expected[:4])
        require(snapshot.read(binding + 0x12, 4) == struct.pack('>HH', width, height),
                'endless texture dimensions')
        image = word(snapshot, header + 8)
        require(snapshot.read(image, len(self._textures[kind])) == self._textures[kind],
                'endless artwork texture differs from reviewed source')
        verified.add(bank)

    def _cell(self, snapshot, manager, address, bank, animation, kind, verified, registered, draw_parent=False, include_transparent_key=False):
        if address not in registered:
            return None
        raw = self._sprite(snapshot, address, manager)
        require(raw[0xc] == bank and struct.unpack_from('>H', raw, 0xe)[0] == animation,
                'endless sprite bank/animation')
        if not raw[0xa7] or (not draw_parent and not raw[0xa6]):
            return None
        self._texture(snapshot, manager, bank, kind, verified)
        binding = manager + bank * 0x20
        cell_count, animation_count = struct.unpack('>HH', snapshot.read(binding + 0x16, 4))
        require(animation < animation_count, 'endless animation bounds')
        table = word(snapshot, binding + 0x20)
        entry = table + animation * 8
        count = struct.unpack('>H', snapshot.read(entry, 2))[0]
        keys = word(snapshot, entry + 4)
        artwork = self._profile['artwork' if kind == 'score' else 'permanent_artwork']
        source = artwork[str(animation)]
        require(count == len(source), 'endless animation key count')
        for index, key in enumerate(source):
            # Native allocation does not initialize the last three padding bytes.
            require(snapshot.read(keys + index * 24, 21) == bytes.fromhex(key['key_hex'])[:21],
                    'endless animation differs from reviewed source')
        frame = struct.unpack_from('>f', raw, 0x44)[0]
        require(math.isfinite(frame) and frame >= 0, 'endless animation frame')
        order = list(reversed(source)) if raw[0xab] else source
        elapsed, selected = 0, order[-1]
        for key in order:
            elapsed += key['frames']
            if elapsed > frame:
                selected = key
                break
        if not (draw_parent or include_transparent_key) and bytes.fromhex(selected['key_hex'])[20] == 0:
            return None
        require(selected['cell'] < cell_count, 'endless cell bounds')
        cell_table = word(snapshot, binding + 0x1c)
        entry = cell_table + selected['cell'] * 8
        count = struct.unpack('>H', snapshot.read(entry, 2))[0]
        objects = word(snapshot, entry + 4)
        require(count == len(selected['objects_hex']), 'endless cell object count')
        visible = False
        for index, encoded in enumerate(selected['objects_hex']):
            expected = bytes.fromhex(encoded)
            observed = snapshot.read(objects + index * 32, 32)
            x, y = struct.unpack_from('>hh', expected, 12)
            require(observed[:10] == expected[:10] and
                    observed[10:14] == struct.pack('>hh', x - 512, y - 512) and
                    observed[16:31] == expected[16:31],
                    'endless artwork geometry differs from reviewed source')
            visible |= bool(observed[30])
        return selected if visible or draw_parent else None

    def _independent(self, snapshot, address, manager, registered):
        if address not in registered:
            return
        raw = self._sprite(snapshot, address, manager)
        require(raw[0xc2] == 0 or word(snapshot, address + 0xb0) == 0,
                'endless independent sprite unexpectedly draws under parent')

    def _counter(self, snapshot, scene, spec, field, manager, verified, registered):
        counter = word(snapshot, scene + spec[field])
        digits = spec['digits']
        base = word(snapshot, counter)
        if base not in registered:
            return None
        first = spec['current_first_id' if field == 'current_offset' else 'best_first_id']
        require(snapshot.read(counter + 0x10, 4) == struct.pack('>BBH', digits, 0, first),
                'endless counter digit configuration')
        base, table = word(snapshot, counter), word(snapshot, counter + 4)
        self._independent(snapshot, base, manager, registered)
        parent = self._cell(snapshot, manager, base, self._profile['permanent_bank'],
                            self._profile['base_id'], 'permanent', verified, registered, draw_parent=True)
        pointers = [word(snapshot, table + i * 4) for i in range(digits)]
        require(len(set(pointers)) == digits, 'duplicate endless counter digit binding')
        # Validate the actual native child chain used to draw counter digits.
        children, previous, child = set(), 0, word(snapshot, base + 0xb4)
        while child:
            require(child in pointers and child in registered and child not in children,
                    'endless counter child ownership')
            raw = self._sprite(snapshot, child, manager)
            require(word(snapshot, child + 0xb0) == base and
                    word(snapshot, child + 0xbc) == previous and
                    raw[0xc2] == 1 and snapshot.read(child + 0xc0, 2) == b'\x00\x00',
                    'endless counter child draw binding')
            children.add(child)
            previous, child = child, word(snapshot, child + 0xb8)
        require(children == set(pointers), 'endless counter digit not in native draw chain')
        if parent is None:
            return None
        require(len(parent['objects_hex']) == 1, 'endless counter base object count')
        shown, started = [], False
        for address in pointers:
            raw = self._sprite(snapshot, address, manager)
            animation = struct.unpack_from('>H', raw, 0xe)[0]
            require(first <= animation < first + 10, 'endless digit animation outside decimal range')
            visible = self._cell(snapshot, manager, address, spec['bank'], animation, 'score', verified, registered)
            if visible is None:
                require(not started, 'hidden digit inside displayed endless score')
            else:
                started = True
                shown.append(str(animation - first))
        return int(''.join(shown)) if shown else None

    def read(self, snapshot, scene):
        """Read visible results, native new-record artwork and actual A availability."""
        scene_address = scene['scene']
        scene_type = word(snapshot, scene_address)
        spec = self._profile['scenes'].get(str(scene_type))
        if spec is None:
            return []
        if 'mode_offset' in spec:
            mode = snapshot.read(scene_address + spec['mode_offset'], 1)[0]
            require(mode in (0, 1), 'endless scene mode flag')
            if not mode:
                return []
        manager = word(snapshot, self._engine['manager_global'])
        require(manager and word(snapshot, manager) == self._engine['manager_vtable'],
                'endless sprite manager type')
        registered = self._registry(snapshot, manager)
        verified, rows = set(), []
        owner = {'owner_vtable':scene_type}
        game_over = word(snapshot, scene_address + self._profile['game_over_offset'])
        record = word(snapshot, scene_address + self._profile['record_offset'])
        for address in (game_over, record):
            self._independent(snapshot, address, manager, registered)
        shown = self._cell(snapshot, manager, game_over, spec['bank'],
                           self._profile['game_over_id'], 'score', verified, registered)
        if shown is not None:
            score = self._counter(snapshot, scene_address, spec, 'current_offset', manager, verified, registered)
            best = self._counter(snapshot, scene_address, spec, 'best_offset', manager, verified, registered)
            if spec.get('best_label_from_draw_list'):
                labels = [address for address in registered
                          if snapshot.read(address + 0xc, 1)[0] == spec['best_label_bank'] and
                          snapshot.read(address + 0xe, 2) == struct.pack('>H', self._profile['best_label_id'])]
                require(len(labels) <= 1, 'multiple native endless best-label sprites')
                label = labels[0] if labels else 0
            else:
                label = word(snapshot, scene_address + spec['best_label_offset'])
            self._independent(snapshot, label, manager, registered)
            best_label = self._cell(snapshot, manager, label, spec['best_label_bank'],
                                    self._profile['best_label_id'], 'score', verified, registered)
            if score is not None and best is not None and best_label is not None:
                rows.append(message(owner, 'endless:result', Loc.Get('endless_result', score=score, best=best),
                                    ['score_game_over', 'current_score', 'personal_best'], 'endless_result'))
            continuation = word(snapshot, self._profile['continue_global'])
            require(continuation in registered, 'endless native continuation not registered')
            raw = self._sprite(snapshot, continuation, manager)
            animation = struct.unpack_from('>H', raw, 0xe)[0]
            require(animation in self._profile['continue_ids'], 'endless continuation animation')
            self._independent(snapshot, continuation, manager, registered)
            if self._cell(snapshot, manager, continuation, self._profile['permanent_bank'],
                          animation, 'permanent', verified, registered, include_transparent_key=(animation == 1)) is not None:
                rows.append(message(owner, 'endless:continue', Loc.Get('endless_continue'),
                                    'permanent_next', 'endless_result'))
        if self._cell(snapshot, manager, record, spec['bank'], self._profile['record_id'],
                      'score', verified, registered) is not None:
            rows.append(message(owner, 'endless:record', Loc.Get('endless_record'),
                                'score_hiscore', 'endless_result'))
        return rows
