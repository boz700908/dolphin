"""Read allocation and draw-list authority shared by native CellAnim users.

Authority: game-api.md, Native Draw-list Lifetime and Karate Word Artwork.
"""
from .graphics import require, word


class SpriteHandler:
    """Validate native sprites before following the manager's current draw list."""

    def __init__(self, engine, registry, context):
        self._engine = engine
        self._registryProfile = registry
        self._context = context

    def _sprite(self, snapshot, address, manager):
        engine = self._engine
        heap = word(snapshot, manager + engine['heap_offset'])
        require(snapshot.read(heap, 4) == bytes.fromhex(engine['heap_magic']),
                self._context + ' sprite heap type')
        start, end = word(snapshot, heap + 0x18), word(snapshot, heap + 0x1c)
        header = address - engine['allocation_header']
        require(address % 4 == 0 and start <= header and address + engine['sprite_size'] <= end and
                snapshot.contains(address, engine['sprite_size']), self._context + ' sprite heap ownership',
                sprite=address, heap_start=start, heap_end=end)
        require(snapshot.read(header, 2) == bytes.fromhex(engine['used_tag']) and
                word(snapshot, header + 4) == engine['sprite_size'],
                self._context + ' sprite allocation not active', sprite=address)
        require(word(snapshot, address) == engine['animation_vtable'], self._context + ' sprite type')
        raw = snapshot.read(address, engine['sprite_size'])
        require(all(raw[offset] in (0, 1) for offset in (0xa7, 0xab, 0xc2)),
                self._context + ' sprite flags')
        return raw

    def _registry(self, snapshot, manager):
        offsets = self._registryProfile
        address = word(snapshot, manager + offsets['head_offset'])
        previous, registered = 0, set()
        while address:
            require(address not in registered, self._context + ' sprite registry cycle')
            self._sprite(snapshot, address, manager)
            require(word(snapshot, address + offsets['previous_offset']) == previous,
                    self._context + ' sprite registry backward link')
            registered.add(address)
            previous, address = address, word(snapshot, address + offsets['next_offset'])
        return registered
