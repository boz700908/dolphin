"""Shared tutorial text reading. Evidence: game-api.md, Tutorial Families."""

from .graphics import TutorialHintsHandler, message, require
from .tags import TextTagsHandler
from .rap import RapHandler
from .police_text import PoliceTextHandler


class TutorialHandler:
    """Gather current instructions across verified native tutorial layout families."""

    def __init__(self, data):
        self._hints = TutorialHintsHandler(data)
        self._features = data.assist['features']
        self._tags = TextTagsHandler(data)
        self._rap = RapHandler(data)
        self._police = PoliceTextHandler(data)
        self._rapProfile = data.assist['rap']

    def read(self, snapshot, owners):
        """Read shared instructions, the native Rap guide and permanent hints once."""
        active, candidates, rows, fonts = [], [], [], set()
        for owner in owners:
            feature = self._features.get(str(owner['owner_vtable']))
            if feature is None or feature['kind'] != 'tutorial':
                continue
            active.append(owner)
            if owner['owner_vtable'] == self._rapProfile['owner_vtable']:
                rap_rows, rap_fonts = self._rap.read(snapshot, owner)
                rows.extend(rap_rows)
                fonts.update(rap_fonts)
                continue
            for pane in owner['panes']:
                if pane['name'] in feature['texts'] and pane['drawing'] and pane['text'].strip():
                    if not self._police.ready(snapshot, owner, pane):
                        continue
                    candidates.append((owner, pane))
        require(len(candidates) <= 1, 'multiple drawing tutorial instructions',
                candidates=[{'owner': owner['owner'], 'owner_vtable': owner['owner_vtable'],
                             'pane': pane['name'], 'pane_address': pane['address'],
                             'text': pane['text'], 'raw_buffer_hex': pane['raw_buffer_hex'],
                             'draw_path': pane['draw_path'],
                             'computed_alpha': pane['computed_alpha']}
                            for owner, pane in candidates] if len(candidates) > 1 else [])
        if candidates:
            owner, pane = candidates[0]
            fonts.add(pane['font'])
            rows.append(message(owner, 'tutorial:instruction',
                                self._tags.speech_text(snapshot, pane),
                                pane['name'], 'tutorial'))
        if active:
            rows.extend(self._hints.read(snapshot, active[0]))
        return rows, fonts
