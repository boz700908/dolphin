"""Read verified native numeric animations and permanent tutorial artwork.

Evidence: docs/game-api.md, Verified Fix Paths. No OCR or US heap globals.
"""

import math
import struct

from .errors import DiagnosticError
from .loc import Loc


def require(condition, detail, **context):
    """Reject inconsistent mutable game data explicitly."""
    if not condition:
        raise DiagnosticError('layout_invalid', detail=detail, context=context)


def word(snapshot, address):
    """Read one aligned native pointer or scalar within captured RAM."""
    require(address % 4 == 0 and snapshot.contains(address, 4), 'native field bounds/alignment')
    return struct.unpack('>I', snapshot.read(address, 4))[0]


def message(owner, key, text, pane, kind):
    """Use the shared narration envelope for native graphic labels."""
    return {'key': key, 'text': text, 'owner_vtable': owner['owner_vtable'],
            'pane': pane, 'kind': kind}


class SelectionStatsHandler:
    """Follow the target layout's bound digit animation objects."""

    def __init__(self, data):
        self._profile = data.assist['selection_stats']
        self._numberSource = data.number_source
        self._revealSource = data.selection_reveal_source

    def read(self, snapshot, owner, names):
        """Read displayed digits only when their native panes are drawing."""
        profile = self._profile
        save = word(snapshot, profile['save_global'])
        require(word(snapshot, save) == profile['save_vtable'], 'selection save manager type')
        index = snapshot.read(save + profile['current_slot_offset'], 1)[0]
        require(index < 4, 'selection current save slot')
        slot = save + profile['slot_offset'] + index * profile['slot_size']
        if snapshot.read(slot + profile['result_pending_offset'], 1)[0]:
            return []
        # The game's reveal writes random decimal digits until this native counter ends.
        countdown = struct.unpack('>H', snapshot.read(profile['countdown_global'], 2))[0]
        if countdown:
            return []
        installed_flow = word(snapshot, profile['installed_flow_global'])
        base = word(snapshot, owner['owner'] + 8)
        count = word(snapshot, owner['owner'] + 0x0c)
        settling = profile['settling_animation']
        require(settling['pane'] in names and settling['offset'] // 0x20 < count,
                'selection settling animation bounds/pane')
        animation = base + settling['offset']
        require(word(snapshot, animation) == profile['animation_vtable'] and
                word(snapshot, animation + 4) == owner['layout'] and
                word(snapshot, animation + 8) == names[settling['pane']]['address'],
                'selection settling animation ownership/type')
        require(word(snapshot, animation + 0x1c) == 0,
                'selection settling animation unexpectedly reversed')
        flags = snapshot.read(animation + 0x18, 4)
        require(all(flag in (0, 1) for flag in flags), 'selection settling animation flags')
        transform = word(snapshot, animation + 0x0c)
        require(word(snapshot, transform) == profile['transform_vtable'],
                'selection settling transform type')
        resource = word(snapshot, transform + 0x0c)
        require(snapshot.read(resource, len(self._revealSource)) == self._revealSource,
                'selection settling animation resource differs from reviewed source')
        # Native Finish retires the blink binding only after the final matrix calculation.
        if flags[0] or flags[2]:
            return []
        observed = {}
        pending = False
        verified_resources = set()
        for field, bindings in profile['digits'].items():
            digits = []
            for binding in bindings:
                name, offset = binding['pane'], binding['offset']
                require(name in names, 'selection digit pane missing')
                pane = names[name]
                if not pane['drawing']:
                    return []
                require(offset % 0x20 == 0 and offset // 0x20 < count,
                        'digit animation array bounds')
                animation = base + offset
                require(word(snapshot, animation) == profile['animation_vtable'], 'digit animation type')
                require(word(snapshot, animation + 4) == owner['layout'] and
                        word(snapshot, animation + 8) == pane['address'], 'digit animation ownership')
                require(word(snapshot, animation + 0x1c) == 0, 'digit animation unexpectedly reversed')
                flags = snapshot.read(animation + 0x18, 4)
                require(all(flag in (0, 1) for flag in flags), 'digit animation flags')
                # Native Finish clears enabled only after Animate/CalculateMtx.
                pending |= bool(flags[0] or flags[2])
                frame = struct.unpack('>f', snapshot.read(animation + 0x10, 4))[0]
                transform = word(snapshot, animation + 0x0c)
                require(word(snapshot, transform) == profile['transform_vtable'], 'digit transform type')
                resource = word(snapshot, transform + 0x0c)
                if resource not in verified_resources:
                    require(snapshot.read(resource, len(self._numberSource)) == self._numberSource,
                            'number animation resource differs from reviewed source')
                    verified_resources.add(resource)
                applied = struct.unpack('>f', snapshot.read(transform + 0x10, 4))[0]
                digits.append((frame, applied))
            observed[field] = digits
        if pending:
            return []
        values = {}
        for field, digits in observed.items():
            for frame, applied in digits:
                require(math.isfinite(frame) and frame.is_integer() and 0 <= frame <= 9,
                        'decimal digit frame')
                require(applied == frame, 'digit frame has not been applied')
            values[field] = int(''.join(str(int(frame)) for frame, _ in digits))
        require(values['flow'] == installed_flow, 'selection digits differ from native final sense')
        return [message(owner, 'selection:stats', Loc.Get('selection_stats', **values),
                        [b['pane'] for v in profile['digits'].values() for b in v], 'selection_stats')]


class TutorialHintsHandler:
    """Follow native permanent globals and validate their current heap allocations."""

    def __init__(self, data):
        self._profile = data.assist['tutorial_graphics']

    def _bindings(self, snapshot, manager):
        """Validate direct engine-owned hints without depending on particle order."""
        profile = self._profile
        loaded = snapshot.read(manager + profile['bank'] * 0x20 + profile['bank_loaded_offset'], 1)[0]
        require(loaded in (0, 1), 'permanent bank loaded flag')
        if not loaded:
            return None
        heap = word(snapshot, manager + profile['heap_offset'])
        require(snapshot.read(heap, 4) == bytes.fromhex(profile['heap_magic']), 'sprite heap type')
        start, end = word(snapshot, heap + 0x18), word(snapshot, heap + 0x1c)
        require(start < end and snapshot.contains(start, end - start), 'sprite heap bounds')
        bindings = set()
        for field in ('skip_global', 'caption_global', 'number_global'):
            global_address = profile[field]
            address = word(snapshot, global_address)
            header = address - profile['allocation_header']
            context = {'global_address':global_address, 'sprite':address, 'heap':heap,
                       'heap_start':start, 'heap_end':end}
            require(address % 4 == 0 and start <= header and
                    address + profile['sprite_size'] <= end, 'permanent sprite heap ownership', **context)
            require(snapshot.read(header, 2) == bytes.fromhex(profile['used_tag']) and
                    word(snapshot, header + 4) == profile['sprite_size'],
                    'permanent sprite allocation not active', **context)
            require(address not in bindings, 'duplicate permanent sprite binding', **context)
            bindings.add(address)
        return bindings

    def _cell(self, snapshot, manager, members, global_address, expected_id):
        profile = self._profile
        address = word(snapshot, global_address)
        require(address in members, 'tutorial sprite not allocated')
        raw = snapshot.read(address, 0xc4)
        require(word(snapshot, address) == profile['animation_vtable'], 'tutorial sprite type',
                global_address=global_address, sprite=address, observed_vtable=word(snapshot, address))
        require(raw[0x0c] == profile['bank'] and struct.unpack_from('>H', raw, 0x0e)[0] == expected_id,
                'tutorial sprite bank/animation')
        require(raw[0xa7] in (0, 1) and raw[0xab] in (0, 1), 'tutorial sprite flags')
        if not raw[0xa7] or not raw[0xa6]:
            return None
        bank = manager + profile['bank'] * 0x20
        table = word(snapshot, bank + 0x20)
        entry = table + expected_id * 8
        count = struct.unpack('>H', snapshot.read(entry, 2))[0]
        keys = word(snapshot, entry + 4)
        source = profile['animations'][str(expected_id)]
        require(count == len(source), 'tutorial animation key count')
        for index, key in enumerate(source):
            # Native allocation leaves the last three padding bytes uninitialized.
            require(snapshot.read(keys + index * 24, 21) == bytes.fromhex(key['key_hex'])[:21],
                    'tutorial artwork animation differs from reviewed source')
        frame = struct.unpack_from('>f', raw, 0x44)[0]
        require(math.isfinite(frame), 'tutorial animation frame')
        order = list(reversed(source)) if raw[0xab] else source
        elapsed, selected = 0, order[-1]
        for key in order:
            elapsed += key['frames']
            if elapsed > frame:
                selected = key
                break
        encoded = bytes.fromhex(selected['key_hex'])
        if encoded[20] == 0:
            return None
        cell_table = word(snapshot, bank + 0x1c)
        cell_entry = cell_table + selected['cell'] * 8
        object_count = struct.unpack('>H', snapshot.read(cell_entry, 2))[0]
        objects = word(snapshot, cell_entry + 4)
        require(object_count == len(selected['objects_hex']), 'tutorial cell object count')
        for index, object_hex in enumerate(selected['objects_hex']):
            expected = bytes.fromhex(object_hex)
            observed = snapshot.read(objects + index * 32, 32)
            x, y = struct.unpack_from('>hh', expected, 12)
            require(observed[:10] == expected[:10] and
                    observed[10:14] == struct.pack('>hh', x - 512, y - 512) and
                    observed[16:31] == expected[16:31], 'tutorial cell geometry differs from reviewed source')
        return selected['cell']

    def read(self, snapshot, owner):
        """Read native skip availability and the rendered remaining number/unit."""
        profile = self._profile
        manager = word(snapshot, profile['manager_global'])
        require(manager != 0 and word(snapshot, manager) == profile['manager_vtable'], 'sprite manager type')
        members = self._bindings(snapshot, manager)
        if members is None:
            return []
        skip = self._cell(snapshot, manager, members, profile['skip_global'], profile['skip_id'])
        caption = self._cell(snapshot, manager, members, profile['caption_global'], profile['caption_id'])
        number = self._cell(snapshot, manager, members, profile['number_global'], profile['number_id'])
        items = []
        if skip is not None:
            items.append(message(owner, 'tutorial:skip', Loc.Get('tutorial_skip'), 'permanent_skip', 'tutorial'))
        if caption is not None and number is not None:
            unit = profile['caption_keys'].get(str(caption))
            digit = profile['digit_cells'].get(str(number))
            require(unit is not None and digit is not None, 'unreviewed remaining artwork')
            items.append(message(owner, 'tutorial:remaining', Loc.Get(unit, count=digit),
                                 'permanent_remain_number', 'tutorial'))
        return items
