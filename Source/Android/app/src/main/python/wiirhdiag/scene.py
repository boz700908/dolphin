"""Native scene drawing lifetime. Evidence: game-api.md, Native Scene Lifecycle."""

from .graphics import require, word


class SceneHandler:
    """Gate layout traversal with the game's own scene draw dispatch."""

    def __init__(self, profile):
        self._profile = profile

    def inspect(self, snapshot):
        """Return native scene state without dereferencing layouts during teardown."""
        manager = word(snapshot, self._profile['global'])
        if manager == 0:
            return {'drawing': False, 'phase': 'scene_manager_not_initialized'}
        require(word(snapshot, manager) == self._profile['vtable'], 'game manager type')
        scene = word(snapshot, manager + 4)
        if scene == 0:
            return {'drawing': False, 'phase': 'scene_not_initialized'}
        state = word(snapshot, scene + 8)
        require(state in range(7), 'scene state outside native enum', scene=scene, state=state)
        return {'drawing': state in (2, 3, 4), 'phase': 'native_scene',
                'scene': scene, 'state': state}
