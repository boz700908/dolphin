"""Native single-player grade artwork. Evidence: game-api.md, Complete Grades."""

from .graphics import message, require, word
from .loc import Loc


class GradingHandler:
    """Read verified bound grade groups and their optional native prefix."""

    def __init__(self, data):
        self._keys = data.assist['rank_keys']
        self._profile = data.assist['grading']

    def read(self, snapshot, owner, names):
        """Announce only currently drawn artwork, preserving animation reveal order."""
        caption = names['T_Caption_00']
        if not caption['drawing'] or not caption['text']:
            return []
        for name, offset in self._profile['pane_offsets'].items():
            require(word(snapshot, owner['owner'] + offset) == names[name]['address'],
                    'grade pane ownership', owner=owner['owner'], pane=name, offset=offset)
        enabled = [name for name in self._keys if names[name]['drawing']]
        require(len(enabled) <= 1, 'multiple drawing grades')
        if not enabled:
            return []
        pane = enabled[0]
        rank = Loc.Get(self._keys[pane])
        prefix = self._profile['prefix_pane']
        used = [pane]
        if names[prefix]['drawing']:
            rank = Loc.Get('rank_with_prefix', prefix=Loc.Get('rank_but'), rank=rank)
            used.insert(0, prefix)
        return [message(owner, 'grading:rank', rank, used, 'grading')]
