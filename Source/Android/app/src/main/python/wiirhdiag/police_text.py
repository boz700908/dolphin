"""Read Police SMS text after the game's own letter reveal completes.

Authority: docs/game-api.md, GUI Test Feedback: Native Reveal and Worker Output.
"""
from .graphics import require, word


class PoliceTextHandler:
    """Gate only the reviewed scene's bound shared message pane."""

    def __init__(self, data):
        self._profile = data.assist['police_text']

    def ready(self, snapshot, owner, pane):
        """Validate native source/count/pane ownership without guessing display timing."""
        profile = self._profile
        if owner['owner_vtable'] != profile['owner_vtable'] or pane['name'] != profile['pane']:
            return True
        manager = word(snapshot, profile['scene_global'])
        scene = word(snapshot, manager + 4)
        if word(snapshot, scene) != profile['scene_vtable']:
            return True
        require(word(snapshot, profile['pane_global']) == pane['address'],
                'Police message pane differs from native accessor')
        total = word(snapshot, scene + profile['length_offset'])
        revealed = word(snapshot, scene + profile['revealed_offset'])
        require(total < profile['buffer_capacity'] and revealed <= total,
                'Police message reveal length/count')
        if not total:
            return True
        if revealed != total:
            return False
        source = word(snapshot, scene + profile['source_offset'])
        require(source % 2 == 0 and snapshot.contains(source, (total + 1) * 2),
                'Police message source bounds')
        raw = snapshot.read(source, (total + 1) * 2)
        require(raw[-2:] == b'\0\0', 'Police message source terminator')
        # Count may be advanced just before the native setter applies the final text.
        # During that native update, retain no partial instruction.
        return bytes.fromhex(pane['raw_buffer_hex']) == raw
