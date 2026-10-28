"""Read native endless-record identities and their confirmation dialogs.

Authority: game-api.md, Native Record Selector and Confirmation.
"""

from .graphics import message, require, word
from .loc import Loc
from .native_menus import NativeMenuHandler
from .tags import TextTagsHandler


class RecordSelectorHandler:
    """Follow the selector's current native state, player and focus manager."""

    def __init__(self, data):
        self._profile = data.assist['record_selector']
        self._menu = NativeMenuHandler(data.assist['native_menus'])
        self._tags = TextTagsHandler(data)

    def _prompt(self, snapshot, owner, names, binding):
        """Use the bound current message and exclude hidden native dialog parents."""
        require(binding['pane'] in names and binding['parent'] in names,
                'record dialog prompt missing')
        pane, parent = names[binding['pane']], names[binding['parent']]
        require(pane['parent'] == parent['address'], 'record dialog prompt ownership')
        if 'offset' in binding:
            require(word(snapshot, owner['owner'] + binding['offset']) == pane['address'],
                    'record dialog prompt binding')
        if not parent['drawing'] or not pane['drawing'] or not pane['text'].strip():
            return None
        require(pane.get('font'), 'record dialog prompt font missing')
        return self._tags.speech_text(snapshot, pane), pane['font']

    def _page(self, snapshot, owner, names):
        """Validate native current-page text against the gallery's actual page word."""
        page = word(snapshot, owner['owner'] + self._profile['page_offset'])
        values, fonts = [], set()
        for name, offset in (('T_page_00', 0x744), ('T_page_02', 0x748)):
            require(name in names, 'record Mii page text missing')
            pane = names[name]
            require(word(snapshot, owner['owner'] + offset) == pane['address'] and
                    pane['parent'] == names['W_menu_mii_000']['address'],
                    'record Mii page binding')
            if not pane['drawing']:
                return None
            require(pane.get('font') and pane['text'].isascii() and pane['text'].isdigit(),
                    'record Mii page not drawable decimal text')
            values.append(int(pane['text']))
            fonts.add(pane['font'])
        require(0 <= page < values[1], 'record Mii page range')
        if values[0] != page + 1:
            return None
        return page + 1, fonts

    def _candidate(self, snapshot, player):
        """Use the native confirmed candidate, never the assistant's prior focus."""
        profile = self._profile
        mii = snapshot.read(profile['candidate_mii_global'] + player, 1)[0]
        require(mii in (0, 1), 'record candidate Mii flag')
        if mii:
            return Loc.Get('record_mii_selected')
        index = snapshot.read(profile['candidate_badge_global'] + player, 1)[0]
        require(index < 15, 'record candidate identity index')
        return Loc.Get('file_identity_%02d' % index)

    def read(self, snapshot, owner, names):
        """Read active badge, Mii, confirmation or native unavailable-message state."""
        profile = self._profile
        state = word(snapshot, owner['owner'] + profile['state_offset'])
        binding = profile['states'].get(str(state))
        # Dormant state0 retains drawable local panes belonging to old dialogs.
        if binding is None:
            return [], set()
        player = snapshot.read(owner['owner'] + profile['player_offset'], 1)[0]
        require(player in (0, 1), 'record selector player index')
        prompt_name = {3:'prompt', 6:'mii_prompt', 8:'question', 12:'notice'}[state]
        prompt = self._prompt(snapshot, owner, names, profile[prompt_name])
        if prompt is None:
            return [], set()
        focus = self._menu.focus(snapshot, owner, names, binding)
        if focus is None:
            return [], set()
        text, font = prompt
        fonts = {font}
        if state == 8:
            text = Loc.Get('record_confirmation', player=player + 1,
                           identity=self._candidate(snapshot, player), question=text)
        rows = [message(owner, 'record:prompt', text, profile[prompt_name]['pane'], 'record_selector')]
        if state == 6 and 'mii_index' in focus:
            page = self._page(snapshot, owner, names)
            if page is None:
                return [], set()
            fonts.update(page[1])
            label = Loc.Get('record_mii_item', page=page[0], item=focus['mii_index'] + 1)
            choices = [message(owner, 'record_selector:focus', label, focus['pane'], 'record_selector')]
            choice_fonts = set()
        else:
            choices, choice_fonts = self._menu.label(owner, names, focus, 'record_selector')
        fonts.update(choice_fonts)
        for choice in choices:
            choice['key'] = 'record:focus'
            choice['text'] = Loc.Get('record_player_choice', player=player + 1, identity=choice['text'])
        rows.extend(choices)
        return rows, fonts
