"""Native toy selection and manually drawn captions. Authority: game-api.md."""

import struct

from .font import FontHandler
from .hihat import HihatDemoHandler
from .graphics import message, require, word
from .loc import Loc
from .tags import TextTagsHandler


def text_codes(pane):
    """Preserve native UTF-16 units, omitting only null padding at the end."""
    raw = bytes.fromhex(pane['raw_buffer_hex'])
    require(len(raw) % 2 == 0 and raw[-2:] == b'\0\0', 'native text terminator')
    codes = list(struct.unpack('>%dH' % (len(raw) // 2), raw))
    while codes and codes[-1] == 0:
        codes.pop()
    return codes


class HihatHandler:
    """Read the current native pattern instead of the retained scrolling label."""

    def __init__(self, data):
        self._profile = data.assist['hihat']
        self._demo = HihatDemoHandler(data)
        self._tags = TextTagsHandler(data)

    def read(self, snapshot, owner, names, scene):
        """Use native selected index, scene binding and actually drawable text."""
        profile = self._profile
        require(owner['owner_vtable'] == profile['owner_vtable'], 'native Hi-hat layout type')
        require(word(snapshot, scene) == profile['scene_vtable'] and
                word(snapshot, scene + profile['scene_owner_offset']) == owner['owner'],
                'native Hi-hat scene binding')
        selected = word(snapshot, scene + profile['selected_offset'])
        require(selected < len(profile['patterns']), 'native Hi-hat selected pattern')
        expected_raw = bytes.fromhex(profile['patterns'][selected])
        expected = list(struct.unpack('>%dH' % (len(expected_raw) // 2), expected_raw))[:-1]
        current = []
        for name, offset in profile['bindings'].items():
            pane = names[name]
            require(word(snapshot, owner['owner'] + offset) == pane['address'] and pane.get('font'),
                    'native Hi-hat text binding', pane=name)
            if pane['drawing'] and text_codes(pane) == expected:
                current.append(pane)
        items = []
        if current:
            # Both animation copies, if visible, describe the same native selection.
            text = self._tags.speech_text(snapshot, current[0])
            items.append(message(owner, 'hihat:pattern', text, [pane['name'] for pane in current], 'hihat'))
        items.extend(self._demo.read(snapshot, owner, scene))
        return items, {pane['font'] for pane in current}


class LyricHandler:
    """Follow the game's explicit drawing of scene-owned lyric TextBoxes."""

    def __init__(self, data):
        self._profile = data.assist['lyrics']
        self._font = FontHandler(data.lyric_font_source)
        self._codes = set(self._profile['font_codes'])
        self._tags = TextTagsHandler(data)

    def inspect(self, snapshot, scene):
        """Expose only the registered owner drawn by reviewed native delegates."""
        profile = self._profile
        vtable = word(snapshot, scene['scene'])
        if str(vtable) not in profile['scenes']:
            return None
        require(word(snapshot, vtable + 0x1c) == profile['scenes'][str(vtable)],
                'native lyric draw delegate')
        home = word(snapshot, profile['home_global'])
        require(word(snapshot, home) == profile['home_vtable'], 'native Home manager type')
        if snapshot.read(home + profile['home_suppression_offset'], 1)[0]:
            return None
        owner = word(snapshot, scene['scene'] + profile['scene_offset'])
        if not owner:
            return None
        # Native teardown deletes registered layouts before clearing retained scene fields.
        registry = profile['registry']
        manager = word(snapshot, registry['manager_global'])
        require(manager and word(snapshot, manager) == registry['manager_vtable'],
                'native lyric layout manager type')
        count = snapshot.read(manager + registry['count_offset'], 1)[0]
        capacity = snapshot.read(manager + registry['capacity_offset'], 1)[0]
        require(count <= capacity, 'native lyric registry count exceeds capacity')
        table = word(snapshot, manager + registry['table_offset'])
        require(count == 0 or snapshot.contains(table, count * 4), 'native lyric registry bounds')
        registered = [word(snapshot, table + index * 4) for index in range(count)]
        if owner not in registered:
            return None
        require(word(snapshot, owner) == profile['owner_vtable'], 'native lyric owner type')
        return {'owner': owner}

    def read(self, snapshot, owner, names):
        """Read visible captions with their exact dedicated font, title with common font."""
        profile = self._profile
        require(owner['owner_vtable'] == profile['owner_vtable'], 'native lyric layout type')
        rows, common_fonts, verified = [], set(), set()
        for name, offset in profile['bindings'].items():
            pane = names[name]
            require(word(snapshot, owner['owner'] + offset) == pane['address'] and pane.get('font'),
                    'native lyric text binding', pane=name)
            if not pane['drawing']:
                continue
            codes = text_codes(pane)
            if not codes:
                continue
            if name in profile['subtitle_panes']:
                if pane['font'] not in verified:
                    self._font.verify(snapshot, pane['font'])
                    verified.add(pane['font'])
                require(all(code in self._codes for code in codes), 'unsupported native lyric font code',
                        pane=name, codes=codes, raw_buffer_hex=pane['raw_buffer_hex'])
                text = Loc.Get('assist_text', text=''.join(chr(code) for code in codes).strip())
            else:
                common_fonts.add(pane['font'])
                text = self._tags.speech_text(snapshot, pane)
            if text:
                rows.append(message(owner, 'lyrics:' + name, text, name, 'lyrics'))
        return rows, common_fonts
