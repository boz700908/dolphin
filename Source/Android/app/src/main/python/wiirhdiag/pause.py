"""Native manual pause overlay. Evidence: game-api.md, Manual Pause Drawing."""

from .graphics import message, require, word
from .loc import Loc


class PauseHandler:
    """Follow the current scene's explicitly drawn pause owner and native labels."""

    def __init__(self, data):
        self._profile = data.assist['pause']

    def inspect(self, snapshot, scene):
        """Identify manual pause dispatch even when manager draw-enable is false."""
        profile = self._profile
        vtable = word(snapshot, scene['scene'])
        if word(snapshot, vtable + 8) != profile['late_draw']:
            return None
        owner = word(snapshot, scene['scene'] + profile['scene_offset'])
        if owner == 0:
            return None
        require(word(snapshot, owner) == profile['vtable'], 'native pause owner type')
        paused = snapshot.read(owner + profile['paused_offset'], 1)[0]
        require(paused in (0, 1), 'native pause flag')
        if not paused:
            return None
        phase = word(snapshot, owner + profile['phase_offset'])
        require(phase in (0, 1, 2), 'native pause phase')
        suppressed = snapshot.read(profile['suppression_global'], 1)[0]
        return {'owner':owner, 'phase':phase, 'suppressed':bool(suppressed)}

    def read(self, owner):
        """Compose the visible pause actions once; native A/Plus and Minus are fixed."""
        names = {pane['name']:pane for pane in owner['panes']}
        require(len(names) == len(owner['panes']), 'duplicate native pause pane')
        require(all(name in names for name in self._profile['texts']), 'native pause pane missing')
        panes = [names[name] for name in self._profile['texts']]
        if not all(pane['drawing'] for pane in panes):
            return [], set()
        require(all(pane.get('font') and pane['text'].strip() for pane in panes),
                'native pause text missing')
        title, resume, cancel = [pane['text'].strip() for pane in panes]
        text = Loc.Get('pause_summary', title=title, resume=resume, cancel=cancel)
        return [message(owner, 'pause:summary', text, self._profile['texts'], 'pause')], {
            pane['font'] for pane in panes}
