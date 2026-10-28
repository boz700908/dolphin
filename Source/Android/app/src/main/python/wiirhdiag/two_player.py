"""Read target two-player selection through its native bindings.

Evidence: docs/game-api.md, Paged-menu Native Focus and Two-player Selection.
"""

import math
import struct

from .graphics import message, require, word
from .loc import Loc
from .native_menus import NativeMenuHandler, drawing_text


class TwoPlayerSelectionHandler:
    """Read native horizontal selection, return label and the current game card."""

    def __init__(self, data):
        self._profile = data.assist['native_menus']['two_player']
        self._menu = NativeMenuHandler(data.assist['native_menus'])
        self._saveProfile = data.assist['selection_stats']
        self._pictureVtable = data.assist['native_menus']['picture_vtable']
        self._numberSource = data.dual_number_source

    def read(self, snapshot, owner, names):
        """Exclude inactive/closing states and validate direct shared text bindings."""
        profile = self._profile
        state = word(snapshot, owner['owner'] + profile['state_offset'])
        if state in profile['remote_dialog']['states']:
            return self._remote_dialog(snapshot, owner, names, state)
        if state not in (0, 6, 7):
            return [], set()
        for binding in profile['texts']:
            require(binding['pane'] in names, 'two-player text pane missing')
            require(word(snapshot, owner['owner'] + binding['offset']) ==
                    names[binding['pane']]['address'], 'two-player text pane binding')
        if state == 0:
            binding = self._menu.focus(snapshot, owner, names, profile)
            rows, fonts = [], set()
            if binding:
                if 'label_pane' in binding:
                    rows, fonts = self._menu.label(owner, names, binding, 'two_player')
                else:
                    rows, fonts = drawing_text(owner, names, ['T_game_title_00'],
                                               'two_player:focus', 'two_player')
                    rows = self._achievement(snapshot, owner, names, binding, rows)
            rows.extend(self._counter(snapshot, owner, names))
            return rows, fonts
        return drawing_text(owner, names, [b['pane'] for b in profile['texts']],
                            'two_player:detail', 'two_player')

    def _achievement(self, snapshot, owner, names, binding, rows):
        """Decorate only the native title installed for the current game button."""
        if not rows:
            return rows
        profile = self._profile['achievement']
        buttons = self._profile['buttons']
        index = buttons.index(binding)
        if index >= profile['game_count']:
            return rows
        base = owner['owner']
        if (snapshot.read(base + profile['committed_offset'], 1)[0] != index or
                snapshot.read(profile['title_index_global'], 1)[0] != index):
            return []
        save = self._saveProfile
        manager = word(snapshot, save['save_global'])
        require(word(snapshot, manager) == save['save_vtable'], 'dual rank save manager type')
        slot_index = snapshot.read(manager + save['current_slot_offset'], 1)[0]
        require(slot_index < 4, 'dual rank current save slot')
        slot = manager + save['slot_offset'] + slot_index * save['slot_size']
        grade = snapshot.read(slot + profile['states_offset'] + index, 1)[0]
        require(grade in (2, 3, 4, 5), 'dual rank state for focusable game')
        medal_name = profile['medal_prefix'] + '%02d' % index
        require(medal_name in names, 'dual rank medal pane missing')
        medal = names[medal_name]
        require(word(snapshot, base + profile['medal_offset'] + index * 4) == medal['address'] and
                medal['parent'] == names[binding['pane']]['address'] and
                medal['vtable'] == self._pictureVtable, 'dual rank medal binding/type')
        # Native saved-grade artwork and its title are installed in separate calls.
        if medal['drawing'] != (grade == 4):
            return []
        key = profile['rank_keys'].get(str(grade))
        if key:
            rows[0]['text'] = Loc.Get('selection_game_status', title=rows[0]['text'],
                                      status=Loc.Get(key))
        return rows

    def _counter(self, snapshot, owner, names):
        """Read the native settled dual-medal animation and its reviewed resource."""
        profile = self._profile['medal_counter']
        require(profile['pane'] in names, 'dual medal counter pane missing')
        pane = names[profile['pane']]
        if not pane['drawing']:
            return []
        base = word(snapshot, owner['owner'] + 8)
        count = word(snapshot, owner['owner'] + 0x0c)
        offset = profile['offset']
        require(offset % 0x20 == 0 and offset // 0x20 < count, 'dual medal animation bounds')
        animation = base + offset
        require(word(snapshot, animation) == profile['animation_vtable'] and
                word(snapshot, animation + 4) == owner['layout'] and
                word(snapshot, animation + 8) == pane['address'], 'dual medal animation ownership/type')
        require(word(snapshot, animation + 0x1c) == 0, 'dual medal animation unexpectedly reversed')
        flags = snapshot.read(animation + 0x18, 4)
        require(all(flag in (0, 1) for flag in flags), 'dual medal animation flags')
        transform = word(snapshot, animation + 0x0c)
        require(word(snapshot, transform) == profile['transform_vtable'], 'dual medal transform type')
        resource = word(snapshot, transform + 0x0c)
        require(snapshot.read(resource, len(self._numberSource)) == self._numberSource,
                'dual medal animation resource differs from reviewed source')
        if flags[0] or flags[2]:
            return []
        frame = struct.unpack('>f', snapshot.read(animation + 0x10, 4))[0]
        applied = struct.unpack('>f', snapshot.read(transform + 0x10, 4))[0]
        require(math.isfinite(frame) and frame.is_integer() and 0 <= frame <= profile['maximum'],
                'dual medal counter frame')
        require(applied == frame, 'dual medal counter frame has not been applied')
        return [message(owner, 'two_player:stats', Loc.Get('dual_selection_stats', count=int(frame)),
                        profile['pane'], 'two_player')]

    def _remote_dialog(self, snapshot, owner, names, state):
        """Read native current-page text and its bound input instruction."""
        profile = self._profile['remote_dialog']
        parent = names[profile['parent']]
        panes = []
        for binding in profile['texts']:
            pane = names[binding['pane']]
            require(word(snapshot, owner['owner'] + binding['offset']) == pane['address'],
                    'two-player remote text binding', pane=binding['pane'])
            require(pane['parent'] == parent['address'],
                    'two-player remote text parent', pane=binding['pane'])
            panes.append(pane)
        if not parent['drawing'] or not panes[0]['drawing'] or not panes[0]['text'].strip():
            return [], set()
        used = [panes[0]]
        hint = panes[1]
        # Native state4 deliberately blinks the hint without changing its instruction.
        # Follow the displayed dialog parent, preserving native body/page hiding.
        if hint['text'].strip() and (hint['drawing'] or state == profile['blink_state']):
            used.append(hint)
        for pane in used:
            require(pane.get('font'), 'two-player remote message font', pane=pane['name'])
        text = Loc.Get('assist_text', text='\n'.join(p['text'].strip() for p in used))
        return [message(owner, 'two_player:remote_dialog', text,
                        [p['name'] for p in used], 'two_player')], {p['font'] for p in used}
