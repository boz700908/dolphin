"""Native guide subtitles. Authority: game-api.md, Ordinary Endless and Rap Roles."""

from .graphics import message, require, word
from .tags import TextTagsHandler


class RapHandler:
    """Read the non-player rapper's own text pane independently of responses."""

    def __init__(self, data):
        self._profile = data.assist['rap']
        self._tags = TextTagsHandler(data)

    def read(self, snapshot, owner):
        """Validate native text bindings and narrate the visible guide pane only."""
        profile = self._profile
        require(owner['owner_vtable'] == profile['owner_vtable'], 'native Rap layout type')
        names = {}
        for pane in owner['panes']:
            require(pane['name'] not in names, 'duplicate native Rap pane')
            names[pane['name']] = pane
        for name, offset in profile['bindings'].items():
            require(name in names, 'native Rap text pane missing', pane=name)
            require(word(snapshot, owner['owner'] + offset) == names[name]['address'],
                    'native Rap text binding differs', pane=name)
        pane = names[profile['guide_pane']]
        if not pane['drawing'] or not pane['text'].strip():
            return [], set()
        return [message(owner, 'rap:guide', self._tags.speech_text(snapshot, pane),
                        pane['name'], 'rap')], {pane['font']}
