"""Saved selection ranks from the target's native focus and current save.

Authority: game-api.md, Selection Achievement Implementation Authority.
"""
from .graphics import require, word
from .loc import Loc


class SelectionStatusHandler:
    """Append the native saved rank to the existing current-game title event."""

    def __init__(self, data):
        self._profile = data.assist['selection_status']
        self._save = data.assist['selection_stats']

    def decorate(self, snapshot, owner, names, rows):
        """Keep one title announcement and exclude a title still bound to old focus."""
        title_rows = [row for row in rows if row['key'] == 'selection:T_game_title_00']
        if not title_rows:
            return rows
        require(len(title_rows) == 1, 'multiple selection game titles')
        profile = self._profile
        base = owner['owner']
        title_index = snapshot.read(profile['title_index_global'], 1)[0]
        # Native title index0..4 belongs to the extra-menu buttons, not saved games.
        if title_index < profile['first_game']:
            return rows
        state = word(snapshot, base + profile['state_offset'])
        card = state in profile['card_states']
        index = snapshot.read(base + profile['committed_offset' if card else 'focus_offset'], 1)[0]
        if index != title_index or not profile['first_game'] <= index < profile['game_end']:
            return [row for row in rows if row not in title_rows]
        manager = word(snapshot, base + profile['manager_offset'])
        require(word(snapshot, manager) == profile['manager_vtable'], 'selection rank manager type')
        require(snapshot.read(manager + 0x14, 1)[0] == profile['button_count'], 'selection rank button count')
        table = word(snapshot, manager + 0x10)
        button = base + profile['button_offset'] + index * profile['button_stride']
        require(word(snapshot, table + index * 4) == button, 'selection rank button ownership')
        require(word(snapshot, button) == profile['button_vtable'], 'selection rank button type')
        pane_name = 'N_game_btn_%02d' % (index - profile['first_game'])
        require(pane_name in names and word(snapshot, button + 4) == names[pane_name]['address'],
                'selection rank button pane binding')
        require(word(snapshot, base + profile['title_offset']) == names['T_game_title_00']['address'],
                'selection rank title pane binding')
        if not card and (word(snapshot, button + 8) != 1 or not names[pane_name]['drawing']):
            return [row for row in rows if row not in title_rows]
        require(snapshot.read(button + 0x24, 1)[0] == 1, 'selection rank selected button disabled')
        save = self._save
        manager = word(snapshot, save['save_global'])
        require(word(snapshot, manager) == save['save_vtable'], 'selection rank save manager type')
        slot_index = snapshot.read(manager + save['current_slot_offset'], 1)[0]
        require(slot_index < 4, 'selection rank current save slot')
        slot = manager + save['slot_offset'] + slot_index * save['slot_size']
        state = snapshot.read(slot + profile['states_offset'] + index - profile['first_game'], 1)[0]
        require(state in (2, 3, 4, 5), 'selection rank state for focusable game')
        if str(state) in profile['rank_keys']:
            title_rows[0]['text'] = Loc.Get('selection_game_status', title=title_rows[0]['text'],
                                          status=Loc.Get(profile['rank_keys'][str(state)]))
        remaining = self._challenge_remaining(snapshot, base, names, index, slot)
        if remaining is not None:
            title_rows[0]['text'] = Loc.Get('selection_challenge_game',
                                          title=title_rows[0]['text'], count=remaining)
        return rows

    def _challenge_remaining(self, snapshot, base, names, index, slot):
        """Follow the visible native challenge marker and its target binding."""
        profile = self._profile['challenge']
        target = snapshot.read(profile['target_global'], 1)[0]
        if target != index:
            return None
        pane = names.get(profile['pane'])
        require(pane is not None and word(snapshot, base + profile['pane_offset']) == pane['address'],
                'selection challenge marker ownership')
        # The game hides the marker when the target expires. Pending-notice+A6
        # is already cleared while the native challenge remains open.
        if not pane['drawing']:
            return None
        if (snapshot.read(slot + profile['saved_target_offset'], 1)[0] != index or
                word(snapshot, profile['button_pane_global']) !=
                names['N_game_btn_%02d' % (index - self._profile['first_game'])]['address']):
            return None
        remaining = snapshot.read(slot + profile['remaining_offset'], 1)[0]
        require(remaining <= len(profile['opportunities']), 'selection challenge remaining range')
        if remaining == 0:
            return None
        for binding in profile['opportunities']:
            picture = names.get(binding['pane'])
            require(picture is not None and
                    word(snapshot, base + binding['offset']) == picture['address'],
                    'selection challenge opportunity ownership')
            if picture['drawing'] != (remaining >= binding['threshold']):
                return None
        return remaining
