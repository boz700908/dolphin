"""Native dual-player grade reading. Evidence: game-api.md, Two-player Grading."""

import struct

from .font import FontHandler
from .graphics import message, require, word
from .loc import Loc
from .panes import render_candidate


class DualGradingHandler:
    """Follow dedicated font, bound sprites and native artwork reveal."""

    def __init__(self, data):
        self._profile = data.assist['dual_grading']
        self._font = FontHandler(data.dual_font_source)
        self._mapping = {int(code): text for code, text in self._profile['font_mapping'].items()}

    def _text(self, snapshot, pane):
        """Decode reviewed glyphs only after exact embedded-font verification."""
        raw = bytes.fromhex(pane['raw_buffer_hex'])
        codes = list(struct.unpack('>%dH' % (len(raw) // 2), raw))
        while codes and codes[-1] == 0:
            codes.pop()
        if not codes:
            return ''
        self._font.verify(snapshot, pane['font'])
        require(all(code in self._mapping for code in codes), 'unreviewed dual grading glyph')
        text, markers = render_candidate(codes, self._mapping, set())
        require(not markers, 'dual grading text control')
        pane['text'] = text
        pane['markers'] = markers
        return Loc.Get('assist_text', text=text)

    def _scores(self, snapshot, owner, scene):
        """Use final native score fields after both native numeric counters finish."""
        profile = self._profile
        counters = snapshot.read(scene + profile['counter_offset'], 2)
        require(all(value in (0, 1) for value in counters), 'dual score counter flags')
        if any(counters):
            return []
        scores = []
        for binding in profile['scores']:
            sprite = word(snapshot, scene + binding['ones_offset'])
            require(snapshot.contains(sprite, 0xc4), 'dual score sprite bounds')
            require(word(snapshot, sprite) == profile['sprite_vtable'], 'dual score sprite type')
            require(snapshot.read(sprite + 0x0c, 1)[0] == profile['score_bank'] and
                    int.from_bytes(snapshot.read(sprite + 0x0e, 2), 'big') == profile['score_animation'],
                    'dual score sprite bank/animation')
            enabled = snapshot.read(sprite + 0xa7, 1)[0]
            require(enabled in (0, 1), 'dual score sprite drawing flag')
            if not enabled or not snapshot.read(sprite + 0xa6, 1)[0]:
                return []
            value = word(snapshot, scene + binding['value_offset'])
            scores.append(value)
        return [message(owner, 'dual_grading:scores',
                        Loc.Get('dual_grading_scores', player1=scores[0], player2=scores[1]),
                        'native_score_sprites', 'dual_grading')]

    def read(self, snapshot, owner, names, active_scene):
        """Read native stages without replaying game logic or delaying announcements."""
        profile = self._profile
        scene = word(snapshot, owner['owner'] + profile['scene_offset'])
        require(scene == active_scene and word(snapshot, scene) == profile['scene_vtable'],
                'dual grading scene ownership')
        for name, offset in profile['pane_offsets'].items():
            require(word(snapshot, owner['owner'] + offset) == names[name]['address'],
                    'dual grading pane ownership')
        for name, parent in profile['picture_parents'].items():
            require(names[name]['vtable'] == profile['picture_vtable'] and
                    names[name]['parent'] == names[parent]['address'],
                    'dual grading artwork ownership/type')
        rows = self._scores(snapshot, owner, scene)
        for name in profile['texts']:
            pane = names[name]
            if pane['drawing']:
                text = self._text(snapshot, pane)
                if text:
                    rows.append(message(owner, 'dual_grading:' + name, text, name, 'dual_grading'))
        drawn = [name for name in profile['rank_pictures'] if names[name]['drawing']]
        require(len(drawn) <= 1, 'multiple dual grading artworks')
        if drawn:
            name = drawn[0]
            rows.append(message(owner, 'dual_grading:rank', Loc.Get(profile['rank_pictures'][name]),
                                name, 'dual_grading'))
        for name, key in profile['optional_pictures'].items():
            if names[name]['drawing']:
                rows.append(message(owner, 'dual_grading:' + name, Loc.Get(key), name, 'dual_grading'))
        return rows
