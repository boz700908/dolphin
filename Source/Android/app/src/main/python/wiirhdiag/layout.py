'''Native registered drawing layouts. Evidence: game-api.md, Native Drawing Manager.'''

import struct

from .errors import DiagnosticError
from .panes import render_candidate


class LayoutHandler:
    '''Read registered owners, native pane links and bounded text, never focus guesses.'''

    def __init__(self, data):
        self._data = data

    def inspect(self, snapshot, supported_only=False, manual_owners=()):
        '''Return drawing owners with native paths; malformed observations are explicit.'''
        def require(condition, detail, **context):
            if not condition:
                raise DiagnosticError('layout_invalid', detail=detail, context=context)

        def word(address):
            require(address % 4 == 0 and snapshot.contains(address, 4), 'word bounds/alignment')
            return struct.unpack('>I', snapshot.read(address, 4))[0]

        manager = word(self._data.assist['manager_global'])
        if manager == 0:
            return {'owners': [], 'phase': 'manager_not_initialized'}
        require(word(manager) == self._data.assist['manager_vtable'], 'manager virtual table')
        header = snapshot.read(manager, 0x58)
        count, capacity = header[0x51], header[0x50]
        require(count <= capacity, 'manager count exceeds capacity')
        table = word(manager + 0x54)
        require(count == 0 or snapshot.contains(table, count * 4), 'owner array bounds')
        owners, all_roots = [], set()
        for index in range(count):
            owner = word(table + index * 4)
            require(snapshot.contains(owner, 0x1c), 'owner bounds')
            draw = snapshot.read(owner + 0x11, 1)[0]
            require(draw in (0, 1), 'owner draw-enable is not boolean',
                    manager=manager, owner_table=table, owner_index=index, owner=owner,
                    owner_vtable=word(owner), observed_draw=draw)
            layout = word(owner + 4)
            require(layout != 0, 'registered owner has null layout')
            require(word(layout) == self._data.assist['layout_vtable'], 'nw4r layout type')
            root = word(layout + 0x10)
            if root == 0:
                require(draw == 0, 'enabled owner has null root')
                continue
            require(root not in all_roots, 'root registered by multiple owners')
            all_roots.add(root)
            if (not draw and owner not in manual_owners) or (supported_only and
                    str(word(owner)) not in self._data.assist['features']):
                continue
            pending, visited, panes = [(root, 0, True)], set(), []
            while pending:
                address, parent, ancestor_draw = pending.pop()
                require(address not in visited, 'pane link cycle/repetition')
                visited.add(address)
                require(snapshot.contains(address, 0xd8), 'pane header bounds')
                raw = snapshot.read(address, 0xd8)
                vtable = word(address)
                for slot, function in ((0x14, self._data.assist['pane_draw']),
                                       (0x10, self._data.assist['pane_calculate']),
                                       (0x3c, self._data.assist['pane_find'])):
                    require(word(vtable + slot) == function, 'unsupported pane virtual method')
                require(word(address + 0x0c) == parent, 'parent mismatch')
                encoded = raw[0xbc:0xcd]
                require(b'\0' in encoded, 'pane name terminator')
                name_bytes = encoded.split(b'\0', 1)[0]
                require(all(32 <= c <= 126 for c in name_bytes), 'non-ASCII pane name')
                name = name_bytes.decode('ascii')
                if address == root:
                    require(name == 'RootPane', 'native root name')
                path_draw = ancestor_draw and bool(raw[0xbb] & 1)
                pane = {'address': address, 'name': name, 'vtable': vtable,
                        'parent': parent, 'flags': raw[0xbb], 'local_alpha': raw[0xb8],
                        'computed_alpha': raw[0xb9], 'draw_path': path_draw,
                        'drawing': path_draw and raw[0xb9] != 0, 'text': ''}
                if vtable == self._data.profile['textbox_vtable']:
                    require(snapshot.contains(address, 0x100), 'TextBox header bounds')
                    text_header = snapshot.read(address, 0x100)
                    pointer = struct.unpack_from('>I', text_header, 0xd8)[0]
                    capacity_bytes, used = struct.unpack_from('>HH', text_header, 0xfc)
                    size = (used + 1) * 2
                    require(pointer % 2 == 0 and capacity_bytes % 2 == 0 and size <= capacity_bytes,
                            'text capacity/alignment')
                    require(snapshot.contains(pointer, size), 'text bounds')
                    text_bytes = snapshot.read(pointer, size)
                    require(text_bytes[-2:] == b'\0\0', 'text terminator')
                    codes = list(struct.unpack('>%dH' % used, text_bytes[:-2])) if used else []
                    pane['text'], pane['markers'] = render_candidate(codes, self._data.mapping,
                                                                     self._data.blank_codes)
                    pane['font'] = struct.unpack_from('>I', text_header, 0xe4)[0]
                    pane['raw_buffer_hex'] = text_bytes.hex()
                panes.append(pane)
                sentinel = address + 0x14
                link, previous, children, links = word(sentinel), sentinel, [], set()
                while link != sentinel:
                    require(link not in links, 'child cycle')
                    links.add(link)
                    require(word(link + 4) == previous, 'child backward link')
                    children.append((link - 4, address, path_draw))
                    previous, link = link, word(link)
                require(word(sentinel + 4) == previous, 'child tail link')
                pending.extend(reversed(children))
            owners.append({'owner': owner, 'owner_vtable': word(owner), 'layout': layout,
                           'root': root, 'panes': panes})
        return {'owners': owners, 'phase': 'registered_layouts'}
