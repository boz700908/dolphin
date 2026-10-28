"""Native cafe, music, letter and extra-game readers.

Evidence: docs/game-api.md, Batch UI Evidence. No guessed focus or heap globals.
"""

from .graphics import message, require, word
from .loc import Loc


class NativeMenuHandler:
    """Validate the active native manager against its exact embedded bindings."""

    def __init__(self, profile):
        self._profile = profile

    def focus(self, snapshot, owner, names, binding):
        """Return the selected binding, or the native no-focus state."""
        profile = self._profile
        manager = word(snapshot, owner['owner'] + binding['manager_offset'])
        require(word(snapshot, manager) == profile['manager_vtable'], 'menu manager type')
        count = snapshot.read(manager + 0x14, 1)[0]
        require(count == len(binding['buttons']), 'menu button count')
        suspended = snapshot.read(manager + 0x0c, 1)[0]
        require(suspended in (0, 1), 'menu suspension flag')
        if suspended:
            return None
        index = snapshot.read(manager + 0x1c, 1)[0]
        table = word(snapshot, manager + 0x10)
        # Native focus255 can retain the last selected flags during transitions.
        if index == 255:
            return None
        require(index < count, 'menu focus index')
        for number, button_binding in enumerate(binding['buttons']):
            button = word(snapshot, table + number * 4)
            require(button == owner['owner'] + binding['button_offset'] + number * binding.get('button_stride', 0x38),
                    'menu button ownership')
            require(word(snapshot, button) == button_binding.get('vtable', profile['button_vtable']), 'menu button type')
            name = button_binding['pane']
            require(name in names, 'menu button pane missing')
            require(word(snapshot, button + 4) == names[name]['address'], 'menu button pane binding')
            # Native pointer updates publish current without updating selected+0x34.
            # Those retained presentation flags are not a focus invariant (game-api.md).
            if number == index:
                require(snapshot.read(button + 0x24, 1)[0] == 1, 'selected menu button disabled')
                require(names[name]['drawing'], 'selected menu button not drawing')
        return binding['buttons'][index]

    def label(self, owner, names, binding, kind):
        """Read bound drawable text, or verified native arrow artwork."""
        label_name = binding['label_pane']
        require(label_name in names, 'menu label pane missing')
        pane = names[label_name]
        parent = pane
        while parent['address'] != names[binding['pane']]['address'] and parent['parent']:
            matches = [p for p in names.values() if p['address'] == parent['parent']]
            require(len(matches) == 1, 'menu label ancestor missing')
            parent = matches[0]
        require(parent['address'] == names[binding['pane']]['address'], 'menu label ownership')
        # Fading native labels are a supported absence, never stale speech.
        if not pane['drawing']:
            return [], set()
        if 'label_key' in binding:
            require(pane['vtable'] == self._profile['picture_vtable'], 'menu artwork type')
            text, fonts = Loc.Get(binding['label_key']), set()
        else:
            require(pane.get('font') and pane['text'].strip(), 'selected menu label missing text')
            text, fonts = Loc.Get('assist_text', text=pane['text'].strip()), {pane['font']}
        return [message(owner, kind + ':focus', text, binding['pane'], kind)], fonts


def drawing_text(owner, names, pane_names, key, kind, loc_key='assist_text'):
    """Compose supported drawable text while retaining every used font for verification."""
    fonts, parts, used = set(), [], []
    for name in pane_names:
        require(name in names, 'message pane missing: ' + name)
        pane = names[name]
        if pane['drawing'] and pane['text'].strip():
            require(pane.get('font'), 'message font missing')
            fonts.add(pane['font'])
            parts.append(pane['text'].strip())
            used.append(name)
    if not parts:
        return [], fonts
    return [message(owner, key, Loc.Get(loc_key, text='\n'.join(parts)), used, kind)], fonts


class CafeHandler:
    """Read the cafe focus and native dialogue, excluding retained menu labels."""

    def __init__(self, profile):
        self._profile = profile
        self._menu = NativeMenuHandler(profile)

    def read(self, snapshot, owner, names):
        """Select only the manager used by the target's current cafe state."""
        state = word(snapshot, owner['owner'] + 0x1c)
        if state == 0:
            binding = self._menu.focus(snapshot, owner, names, self._profile['cafe'])
            return self._menu.label(owner, names, binding, 'cafe') if binding else ([], set())
        if state != 2:
            return [], set()
        rows, fonts = drawing_text(owner, names, ['T_talk_msg_00'], 'cafe:dialogue', 'cafe')
        enabled = snapshot.read(owner['owner'] + 0x28a, 1)[0]
        require(enabled in (0, 1), 'cafe choice enable flag')
        if enabled:
            count = snapshot.read(owner['owner'] + 0x289, 1)[0]
            require(str(count) in self._profile['cafe_choices'], 'cafe choice count')
            binding = self._menu.focus(snapshot, owner, names, self._profile['cafe_choices'][str(count)])
            if binding:
                choice, choice_fonts = self._menu.label(owner, names, binding, 'cafe_choice')
                rows.extend(choice)
                fonts.update(choice_fonts)
        return rows, fonts


class MusicLetterHandler:
    """Read native music/letter-list focus and separately displayed playback title."""

    def __init__(self, profile):
        self._profile = profile
        self._menu = NativeMenuHandler(profile)

    def read(self, snapshot, owner, names):
        """Use state0 list bindings; never turn retained selected flags into focus."""
        if word(snapshot, owner['owner'] + 0x1c) != 0:
            return [], set()
        rows, fonts = [], set()
        binding = self._menu.focus(snapshot, owner, names, self._profile['lists'])
        if binding:
            rows, fonts = self._menu.label(owner, names, binding, 'lists')
        playback, playback_fonts = drawing_text(owner, names, ['T_MusicTitle_00'],
                                                'lists:playing', 'lists')
        rows.extend(playback)
        fonts.update(playback_fonts)
        return rows, fonts


class LetterHandler:
    """Read a displayed letter as one title-and-body announcement and its focus."""

    def __init__(self, profile):
        self._profile = profile
        self._menu = NativeMenuHandler(profile)

    def read(self, snapshot, owner, names):
        """Follow native state0, title/body drawing and three navigation buttons."""
        if word(snapshot, owner['owner'] + 0x1c) != 0:
            return [], set()
        title, text = names['T_Title_00'], names['T_Text_00']
        rows, fonts = [], set()
        if title['drawing'] and text['drawing'] and title['text'].strip() and text['text'].strip():
            fonts.update((title['font'], text['font']))
            rows.append(message(owner, 'letters:body', Loc.Get('letter_body', title=title['text'].strip(),
                        text=text['text'].strip()), ['T_Title_00', 'T_Text_00'], 'letters'))
        binding = self._menu.focus(snapshot, owner, names, self._profile['letter'])
        if binding:
            focus, focus_fonts = self._menu.label(owner, names, binding, 'letter_navigation')
            rows.extend(focus)
            fonts.update(focus_fonts)
        return rows, fonts


class ExtraGameMenuHandler:
    """Read actual extra-game selection and the current native introduction."""

    def __init__(self, profile, tags):
        self._profile = profile
        self._menu = NativeMenuHandler(profile)
        self._tags = tags

    def _medals(self, snapshot, names):
        pairs = []
        for prefix in self._profile['medal_prefixes']:
            pair = [names['T_' + prefix + suffix + '_00'] for suffix in ('Medal', 'HvMedal')]
            if any(pane['drawing'] for pane in pair):
                require(all(pane['drawing'] for pane in pair), 'medal pair drawing disagreement')
                pairs.append(pair)
        require(len(pairs) <= 1, 'multiple drawing medal categories')
        if not pairs:
            return [], set(), []
        pair = pairs[0]
        return [self._tags.read(snapshot, pane) for pane in pair], {p['font'] for p in pair}, [
            p['name'] for p in pair]

    def read(self, snapshot, owner, names):
        """Use state0 focus or state3 introduction; exclude background menu text."""
        state = word(snapshot, owner['owner'] + 0x1c)
        if state == 0:
            binding = self._menu.focus(snapshot, owner, names, self._profile['extras'])
            if not binding:
                return [], set()
            rows, fonts = self._menu.label(owner, names, binding, 'extras')
            if rows and binding['pane'].startswith('N_GameBtn_'):
                parts, medal_fonts, used = self._medals(snapshot, names)
                if parts:
                    rows[0]['text'] = Loc.Get('extra_medals', title=rows[0]['text'],
                                             requirement=parts[0], current=parts[1])
                    rows[0]['pane'] = [binding['pane']] + used
                    fonts.update(medal_fonts)
            return rows, fonts
        if state != 3:
            return [], set()
        title = names['T_Title_00']
        if not title['drawing'] or not title['text'].strip():
            return [], set()
        fonts, parts, used = {title['font']}, [title['text'].strip()], ['T_Title_00']
        require('T_AgbToy_00' in names, 'extra-game description pane missing')
        description = names['T_AgbToy_00']
        if description['drawing'] and description['text'].strip():
            parts.append(description['text'].strip())
            used.append(description['name'])
            fonts.add(description['font'])
        for label_name, score_name in self._profile['score_pairs']:
            require(label_name in names and score_name in names, 'extra-game score panes missing')
            label, score = names[label_name], names[score_name]
            if label['drawing'] and score['drawing'] and score['text'].strip():
                require(label['text'].strip(), 'extra-game score label missing')
                # Native cards deliberately use a localized no-record message.
                parts.append(Loc.Get('extra_score', label=self._tags.speech_text(snapshot, label),
                                     score=self._tags.speech_text(snapshot, score)))
                used.extend((label_name, score_name))
                fonts.update((label['font'], score['font']))
        control = names['T_comment_00']
        if control['drawing'] and control['text'].strip():
            parts.append(control['text'].strip())
            used.append(control['name'])
            fonts.add(control['font'])
        return [message(owner, 'extras:detail', Loc.Get('assist_text', text='\n'.join(parts)),
                        used, 'extras')], fonts


class DeleteHandler:
    """Read the native delete warning and return label without sending input."""

    def read(self, snapshot, owner, names):
        """Read only the verified state14, excluding retained confirmation panes."""
        if word(snapshot, owner['owner'] + 0x1c) != 14:
            return [], set()
        return drawing_text(owner, names, ['T_msg_00', 'T_back_btn_01'], 'delete:warning', 'delete')
