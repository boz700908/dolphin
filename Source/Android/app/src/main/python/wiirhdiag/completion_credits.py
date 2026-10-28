"""Native completion text, opening artwork and credit pages.

Authority: game-api.md, Perfect Completion, Native Prologue Artwork and Credits.
"""
from hashlib import sha256
import struct

from .graphics import message, require, word
from .loc import Loc
from .tags import TextTagsHandler


class CompletionCreditsHandler:
    """Follow registered native text channels and reviewed, displayed title artwork."""

    def __init__(self, data):
        self._profile = data.assist['completion_credits']
        self._tags = TextTagsHandler(data)

    def _picture(self, snapshot, names, spec):
        """Verify the exact source artwork bound to the current native Picture."""
        require(spec['pane'] in names and spec['parent'] in names,
                'completion artwork pane/parent missing', pane=spec['pane'])
        pane = names[spec['pane']]
        require(pane['vtable'] == self._profile['picture_vtable'] and
                pane['parent'] == names[spec['parent']]['address'],
                'completion artwork ownership/type', pane=spec['pane'])
        if not pane['drawing']:
            return False
        material = word(snapshot, pane['address'] + 0x28)
        require(word(snapshot, material) == self._profile['material_vtable'] and
                word(snapshot, material + 0x3c) >> 28 >= 1,
                'completion artwork material/texture count', pane=spec['pane'])
        texture = word(snapshot, material + 0x40)
        image = word(snapshot, texture)
        width, height = struct.unpack('>HH', snapshot.read(texture + 8, 4))
        require((width, height, word(snapshot, texture + 0x18) >> 28) ==
                (spec['width'], spec['height'], spec['format']),
                'completion artwork texture dimensions/format', pane=spec['pane'])
        require(sha256(snapshot.read(image, spec['length'])).hexdigest() == spec['payload_sha256'],
                'completion artwork differs from reviewed source', pane=spec['pane'])
        return True

    def _prologue(self, snapshot, owner, names):
        """Use the native loaded-resource path and the actual title's visible components."""
        raw = snapshot.read(self._profile['prologue_path'], self._profile['path_capacity'])
        require(b'\0' in raw, 'prologue resource path terminator')
        encoded = raw.split(b'\0', 1)[0]
        require(encoded.isascii(), 'prologue resource path encoding')
        path = encoded.decode('ascii')
        require(path in self._profile['variants'], 'unreviewed native prologue resource', path=path)
        variant = self._profile['variants'][path]
        if variant['text_panes']:
            panes = [names[name] for name in variant['text_panes'] if name in names]
            require(len(panes) == len(variant['text_panes']), 'prologue native TextBox missing')
            visible = [pane for pane in panes if pane['drawing'] and pane['text'].strip()]
            text = Loc.Get('completion_text_group', text=' '.join(
                self._tags.speech_text(snapshot, pane) for pane in visible))
            return ([message(owner, 'prologue:title', text, [p['name'] for p in visible], 'prologue')]
                    if text else []), {p['font'] for p in visible}
        # Check every component, even when an earlier component is still hidden.
        drawing = [self._picture(snapshot, names, spec) for spec in variant['pictures']]
        if not all(drawing):
            return [], set()
        return [message(owner, 'prologue:title', Loc.Get(variant['label_key']),
                        [p['pane'] for p in variant['pictures']], 'prologue')], set()

    def _credits(self, snapshot, owner, names, kind):
        """Read current native text-channel bindings, excluding hidden retained pages."""
        visible = []
        for name, binding in self._profile['bindings'][kind].items():
            pane = names[name]
            require(word(snapshot, owner['owner'] + binding['offset']) == pane['address'],
                    'credit native text ownership', pane=name)
            if not pane['drawing'] or not pane['text'].strip():
                continue
            # A channel can be unregistered during native teardown while its owner
            # remains in the layout registry until that teardown completes.
            active = word(snapshot, self._profile['text_registry'] + binding['channel'] * 4)
            if active == 0:
                continue
            require(active == pane['address'], 'credit native text channel mismatch', pane=name)
            if 'parent' in binding:
                parent = names[binding['parent']]
                require(pane['parent'] == parent['address'] and
                        word(snapshot, owner['owner'] + binding['parent_offset']) == parent['address'],
                        'cast credit native parent binding', pane=name)
            visible.append(pane)
        text = Loc.Get('completion_text_group', text=' '.join(
            self._tags.speech_text(snapshot, pane) for pane in visible))
        return ([message(owner, kind + ':page', text, [p['name'] for p in visible], kind)]
                if text else []), {p['font'] for p in visible}

    def read(self, snapshot, owner, names, scene, kind):
        """Read only feature owners belonging to the verified native scene family."""
        if word(snapshot, scene) != self._profile['scene_types'][kind]:
            return [], set()
        if kind == 'prologue':
            return self._prologue(snapshot, owner, names)
        if kind in ('staff_credits', 'cast_credits'):
            return self._credits(snapshot, owner, names, kind)
        pane = names['T_pft_00']
        if not pane['drawing'] or not pane['text'].strip():
            return [], set()
        heading = (Loc.Get('perfect_success_heading') if
                   self._picture(snapshot, names, self._profile['perfect_artwork']) else '')
        text = Loc.Get('perfect_success_message', heading=heading,
                       text=self._tags.speech_text(snapshot, pane))
        return [message(owner, 'perfect_success:message', text,
                        ['P_omedeto_00', 'T_pft_00'], 'perfect_success')], {pane['font']}
