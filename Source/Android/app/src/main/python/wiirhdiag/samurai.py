"""Read and describe source-verified native Samurai story and ending artwork.

Authority: game-api.md, Samurai Native Artwork Binding and Implementation Authority.
"""
from hashlib import sha256
import math
import struct

from .graphics import message, require, word
from .loc import Loc
from .sprites import SpriteHandler


class SamuraiHandler(SpriteHandler):
    """Follow native current story ownership and actual drawn animation cells."""

    def __init__(self, data):
        self._profile = data.assist['samurai']
        self._home = data.assist['lyrics']
        super().__init__(data.assist['tutorial_graphics'],
                         data.assist['karate']['sprite_registry'], 'samurai')

    def _source(self, snapshot, manager, bank_id):
        bank = manager + bank_id * 0x20
        require(snapshot.read(bank + 0xc, 3) == b'\x01\x00\x00',
                'samurai bank texture mode', bank=bank_id)
        texture_index, width, height, cell_count, animation_count = struct.unpack(
            '>5H', snapshot.read(bank + 0x10, 10))
        palette = word(snapshot, bank + 4)
        require(snapshot.read(palette, 4) == bytes.fromhex('0020af30'),
                'samurai texture palette type')
        palette_count = word(snapshot, palette + 4)
        require(texture_index < palette_count, 'samurai texture index bounds')
        descriptor = word(snapshot, palette + 8) + texture_index * 8
        require(word(snapshot, descriptor + 4) == 0, 'samurai indexed texture unexpected')
        header = word(snapshot, descriptor)
        header_bytes = snapshot.read(header, 8)
        candidates = [spec for spec in self._profile['banks'][str(bank_id)]
                      if (spec['texture_index'], spec['palette_count'], spec['width'],
                          spec['height'], len(spec['cells']), len(spec['animations'])) ==
                      (texture_index, palette_count, width, height, cell_count, animation_count)
                      and bytes.fromhex(spec['header_hex']) == header_bytes]
        version = word(snapshot, self._profile['scenery']['native']['version_global'])
        if version in (0, 1, 2):
            expected_variant = 'ver1' if version == 2 else 'ver0'
            candidates = [spec for spec in candidates if spec['variant'] == expected_variant]
        require(candidates, 'samurai artwork metadata differs from reviewed sources', bank=bank_id)
        image = word(snapshot, header + 8)
        hashes = {}
        matches = []
        for spec in candidates:
            length = spec['length']
            if length not in hashes:
                hashes[length] = sha256(snapshot.read(image, length)).hexdigest()
            if hashes[length] == spec['payload_sha256']:
                matches.append(spec)
        require(matches, 'samurai artwork texture differs from reviewed sources', bank=bank_id)
        selected = matches[0]
        require(all((s['cells'], s['animations'], s['labels'], s['descriptions']) ==
                    (selected['cells'], selected['animations'], selected['labels'],
                     selected['descriptions']) for s in matches),
                'samurai artwork source ambiguous')
        return bank, selected

    def _keys(self, snapshot, bank, source, animation):
        require(animation < len(source['animations']), 'samurai animation bounds')
        keys = source['animations'][animation]
        require(keys, 'samurai animation has no keys')
        entry = word(snapshot, bank + 0x20) + animation * 8
        require(struct.unpack('>H', snapshot.read(entry, 2))[0] == len(keys),
                'samurai animation key count')
        address = word(snapshot, entry + 4)
        for index, encoded in enumerate(keys):
            # Native allocation leaves trailing key padding bytes uninitialized.
            require(snapshot.read(address + index * 24, 21) == bytes.fromhex(encoded)[:21],
                    'samurai animation keys differ from reviewed source')
        return keys

    def _objects(self, snapshot, bank, source, cell):
        require(cell < len(source['cells']), 'samurai cell bounds')
        objects = source['cells'][cell]
        entry = word(snapshot, bank + 0x1c) + cell * 8
        require(struct.unpack('>H', snapshot.read(entry, 2))[0] == len(objects),
                'samurai cell object count')
        address = word(snapshot, entry + 4)
        for index, encoded in enumerate(objects):
            expected = bytes.fromhex(encoded)
            observed = snapshot.read(address + index * 32, 32)
            ci = struct.unpack_from('>H', expected, 8)[0] + source['texture_index']
            x, y = struct.unpack_from('>hh', expected, 12)
            require(observed[:8] == expected[:8] and
                    observed[8:14] == struct.pack('>Hhh', ci, x - 512, y - 512) and
                    observed[16:31] == expected[16:31],
                    'samurai cell geometry differs from reviewed source')
        return objects

    def _current_art(self, snapshot, manager, raw, cache):
        bank_id = raw[0xc]
        if bank_id not in cache:
            cache[bank_id] = self._source(snapshot, manager, bank_id)
        bank, source = cache[bank_id]
        animation = struct.unpack_from('>H', raw, 0xe)[0]
        keys = self._keys(snapshot, bank, source, animation)
        frame = struct.unpack_from('>f', raw, 0x44)[0]
        require(math.isfinite(frame), 'samurai animation frame')
        # CellAnim uses -1.0 while a prepared animation is being switched in.
        # It has no stable visible key during that transition.
        if frame == -1.0:
            return None
        require(frame >= 0, 'samurai animation frame')
        order = list(reversed(keys)) if raw[0xab] else keys
        elapsed, selected = 0, bytes.fromhex(order[-1])
        for encoded in order:
            key = bytes.fromhex(encoded)
            elapsed += struct.unpack_from('>H', key, 2)[0]
            if elapsed > frame:
                selected = key
                break
        if selected[20] == 0:
            return None
        cell = struct.unpack_from('>H', selected)[0]
        objects = self._objects(snapshot, bank, source, cell)
        if not any((bytes.fromhex(obj)[30] * selected[20] * raw[0xa6] // 255 // 255) > 0
                   for obj in objects):
            return None
        return source, cell, animation

    def _label(self, snapshot, manager, raw, cache):
        art = self._current_art(snapshot, manager, raw, cache)
        if art is None:
            return None
        source, cell, _ = art
        caption_key = source['labels'].get(str(cell))
        require(len(source['descriptions']) == len(source['cells']),
                'samurai description cell map size')
        description_key = source['descriptions'][cell]
        if caption_key is None and description_key is None:
            return None
        caption = Loc.Get(caption_key) if caption_key is not None else ''
        if description_key is None:
            return caption
        return Loc.Get(self._profile['description_format_key'],
                       description=Loc.Get(description_key), caption=caption)

    def _visible_art(self, snapshot, manager, registered, sprite, cache):
        if sprite not in registered:
            return None
        raw = self._sprite(snapshot, sprite, manager)
        if not raw[0xa7] or not raw[0xa6]:
            return None
        require(raw[0xc2] == 0 or word(snapshot, sprite + 0xb0) == 0,
                'samurai scenery unexpectedly draws under parent')
        require(struct.unpack_from('>i', raw, 0x9c)[0] == -1,
                'samurai scenery texture override unexpected')
        matrix = struct.unpack('>12f', raw[0x54:0x84])
        require(all(math.isfinite(value) for value in matrix), 'samurai scenery matrix finite')
        if matrix[0] * matrix[5] - matrix[1] * matrix[4] == 0:
            return None
        return self._current_art(snapshot, manager, raw, cache)

    def reset(self):
        """Keep the handler stateless; narration owns the native-event latch."""
        return None

    def _scenery_rows(self, snapshot, scene, manager, registered, cache):
        profile = self._profile['scenery']
        native = profile['native']
        version = word(snapshot, native['version_global'])
        # Native loader uses tutorial0, ordinary1, sequel2; Remix entrances have
        # independently authored visuals and do not inherit these descriptions.
        if version not in (0, 1, 2):
            return []
        variant = 'ver1' if version == 2 else 'ver0'
        labels = profile['labels'][variant]
        rows = []

        def emit(key):
            row = message({'owner_vtable': self._profile['scene_vtable']},
                          'samurai:scenery:%08x:%s' % (scene, key), Loc.Get(labels[key]),
                          'samurai_scenery_' + key, 'samurai_scenery')
            # Keep the native event eligible for the two-sample reader gate.
            # NarrationHandler preserves its spoken value across a transient gap.
            row['persist_on_gap'] = True
            rows.append(row)

        background = self._visible_art(snapshot, manager, registered,
                                       word(snapshot, scene + native['background_offset']), cache)
        hero = self._visible_art(snapshot, manager, registered,
                                 word(snapshot, scene + native['hero_offset']), cache)
        if background is not None and hero is not None:
            require(background[0]['resource'] == ('samurai_bg2' if version == 0 else 'samurai_bg') and
                    background[0]['variant'] == variant and hero[0]['resource'] == 'samurai' and
                    hero[0]['variant'] == variant, 'samurai scenery native version/resource mismatch')
            emit('tutorial' if version == 0 else 'setting')
        if version == 0:
            return rows
        cloud = word(snapshot, scene + native['cloud_offset'])
        visible_cloud = False
        if cloud in registered:
            base = self._sprite(snapshot, cloud, manager)
            require(base[0xc] == 2 and struct.unpack_from('>H', base, 0xe)[0] ==
                    profile['native_animations']['cloud'], 'samurai cloud base binding')
            for sprite in sorted(registered):
                if word(snapshot, sprite + 0xb0) != cloud:
                    continue
                art = self._visible_art(snapshot, manager, registered, sprite, cache)
                if art is None:
                    continue
                require(art[0]['resource'] == 'samurai_enemy' and
                        art[0]['variant'] == variant and
                        art[2] in profile['native_animations']['cloud_parts'],
                        'samurai cloud child binding/source')
                visible_cloud = True
        if visible_cloud:
            emit('cloud')
        escape_visible = False
        if variant == 'ver0':
            for sprite in sorted(registered):
                raw = self._sprite(snapshot, sprite, manager)
                if raw[0xc] != 2 or struct.unpack_from('>H', raw, 0xe)[0] != profile['native_animations']['escape']:
                    continue
                art = self._visible_art(snapshot, manager, registered, sprite, cache)
                if art is None:
                    continue
                require(art[0]['resource'] == 'samurai_enemy' and
                        art[0]['variant'] == variant and art[2] == profile['native_animations']['escape'],
                        'samurai escape binding/source')
                escape_visible = True
            if escape_visible:
                emit('escape')
        recovered = self._visible_art(snapshot, manager, registered,
                                      word(snapshot, scene + native['recovered_offset']), cache)
        if recovered is not None:
            require(recovered[0]['resource'] == 'samurai_enemy' and
                    recovered[0]['variant'] == variant and
                    recovered[2] == profile['native_animations']['recovered'],
                    'samurai recovered item binding/source')
            emit('recovered')
        count = word(snapshot, scene + native['particle_count_offset'])
        require(count <= native['particle_capacity'], 'samurai active particle count')
        visible_kinds = set()
        for index in range(count):
            wrapper = word(snapshot, scene + native['particle_array_offset'] + index * 4)
            require(wrapper % 4 == 0 and snapshot.contains(wrapper, native['particle_size']) and
                    snapshot.contains(wrapper - 16, 16), 'samurai particle wrapper bounds')
            require(snapshot.read(wrapper - 16, 2) == bytes.fromhex(self._engine['used_tag']) and
                    word(snapshot, wrapper - 12) == native['particle_size'] and
                    word(snapshot, wrapper) == scene, 'samurai particle wrapper allocation/scene')
            sprite = word(snapshot, wrapper + 4)
            art = self._visible_art(snapshot, manager, registered, sprite, cache)
            if art is None:
                continue
            require(art[0]['resource'] == 'samurai_other_00' and art[0]['variant'] == variant,
                    'samurai particle artwork source')
            kind = profile['native_animations']['particles'].get(str(art[2]))
            require(kind is not None, 'samurai particle animation unreviewed')
            visible_kinds.add(kind)
        for kind in sorted(visible_kinds):
            emit(kind)
        rate = struct.unpack('>f', snapshot.read(scene + native['rain_rate_offset'], 4))[0]
        require(math.isfinite(rate) and rate >= 0, 'samurai native rain emission rate')
        if rate > 0:
            if 3 not in cache:
                cache[3] = self._source(snapshot, manager, 3)
            bank, source = cache[3]
            require(source['variant'] == variant, 'samurai rain source variant')
            keys = self._keys(snapshot, bank, source, profile['native_animations']['rain'])
            # The native generator defines the rain episode, including instants
            # between drops. Verify the source it emits; no retained-sprite latch.
            for encoded in keys:
                self._objects(snapshot, bank, source, int(encoded[:4], 16))
            emit('rain')
        return rows

    def read(self, snapshot, scene):
        """Read current drawn story, bound ending and registered temporary title."""
        profile = self._profile
        address = scene['scene']
        if word(snapshot, address) != profile['scene_vtable']:
            return []
        home = word(snapshot, self._home['home_global'])
        require(word(snapshot, home) == self._home['home_vtable'], 'samurai Home manager type')
        if snapshot.read(home + self._home['home_suppression_offset'], 1)[0]:
            return []
        manager = word(snapshot, self._engine['manager_global'])
        require(manager and word(snapshot, manager) == self._engine['manager_vtable'],
                'samurai sprite manager type')
        registered = self._registry(snapshot, manager)
        current = word(snapshot, address + profile['story_current_offset'])
        ending = word(snapshot, address + profile['ending_offset'])
        outgoing = word(snapshot, address + profile['story_outgoing_offset'])
        rows, cache = [], {}
        for sprite in sorted(registered):
            raw = self._sprite(snapshot, sprite, manager)
            bank = raw[0xc]
            animation = struct.unpack_from('>H', raw, 0xe)[0]
            if sprite == current:
                require(bank in range(6, 11), 'samurai current story bank')
                kind = 'story'
            elif sprite == ending:
                require(bank == 4 and animation == 1, 'samurai ending bank/animation')
                kind = 'ending'
            elif sprite != outgoing and bank == 4 and animation == 0:
                kind = 'title'
            else:
                continue
            if not raw[0xa7] or not raw[0xa6]:
                continue
            # Native draw handles a parent-bound base_draw sprite through its
            # parent's object traversal. These scene sprites draw independently.
            require(raw[0xc2] == 0 or word(snapshot, sprite + 0xb0) == 0,
                    'samurai artwork unexpectedly draws under parent')
            require(struct.unpack_from('>i', raw, 0x9c)[0] == -1,
                    'samurai artwork texture override unexpected')
            text = self._label(snapshot, manager, raw, cache)
            if text is not None:
                rows.append(message({'owner_vtable': profile['scene_vtable']},
                                    'samurai:' + kind, text,
                                    'samurai_' + kind, 'samurai'))
        rows.extend(self._scenery_rows(snapshot, address, manager, registered, cache))
        return rows
