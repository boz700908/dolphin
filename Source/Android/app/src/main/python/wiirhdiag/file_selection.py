"""Read native file focus and verified operation/identity menu labels.

Evidence: docs/game-api.md, Verified File Operation and Identity Menus,
and Unlock and File Confirmation Dialog Evidence.
"""

from .graphics import message, require, word
from .loc import Loc


class FileSelectionHandler:
    """Follow the active state's manager, excluding retained inactive focus."""

    def __init__(self, data):
        self._profile = data.assist['files']

    def _focus(self, snapshot, owner, names, state):
        profile = self._profile
        manager = word(snapshot, owner['owner'] + state['manager_offset'])
        require(word(snapshot, manager) == profile['manager_vtable'], 'file button manager type')
        count = snapshot.read(manager + 0x14, 1)[0]
        require(count == len(state['buttons']), 'file button count')
        suspended = snapshot.read(manager + 0x0c, 1)[0]
        require(suspended in (0, 1), 'menu suspension flag')
        if suspended:
            return None
        index = snapshot.read(manager + 0x1c, 1)[0]
        # -1 is the game's native no-selection state; never invent a selection.
        if index == 255:
            return None
        require(index < count, 'file focus index')
        table = word(snapshot, manager + 0x10)
        for number, binding in enumerate(state['buttons']):
            button = word(snapshot, table + number * 4)
            require(button == owner['owner'] + state['button_offset'] + number * 0x38,
                    'file button ownership')
            require(word(snapshot, button) == profile['button_vtable'], 'file button type')
            require(binding['pane'] in names, 'file button pane missing')
            require(word(snapshot, button + 4) == names[binding['pane']]['address'],
                    'file button pane binding')
            # Native pointer focus updates current without selected animation flags.
            # Authority: game-api.md, File Focus Correction, update/setter evidence.
            if number == index:
                require(snapshot.read(button + 0x24, 1)[0] == 1, 'selected file button disabled')
                require(names[binding['pane']]['drawing'], 'selected file button not drawing')
        return index

    def _texture(self, snapshot, names, name, parent):
        """Read TexMap0 through the native Picture and Material fields."""
        require(name in names and parent in names, 'file identity artwork missing')
        pane = names[name]
        require(pane['vtable'] == self._profile['picture_vtable'] and
                pane['parent'] == names[parent]['address'], 'file identity artwork ownership/type')
        material = word(snapshot, pane['address'] + 0x28)
        require(word(snapshot, material) == self._profile['material_vtable'],
                'file identity material type')
        require(word(snapshot, material + 0x3c) >> 28 >= 1, 'file identity texture count')
        texture = word(snapshot, material + 0x40)
        require(texture % 4 == 0 and snapshot.contains(texture, 28), 'file identity texture bounds')
        return snapshot.read(texture, 28)

    def _slot(self, snapshot, owner, names, index):
        suffix = '%02d' % index
        for name in ('N_data_' + suffix, 'N_first_' + suffix):
            require(name in names, 'file data/empty group missing')
        populated, empty = names['N_data_' + suffix]['drawing'], names['N_first_' + suffix]['drawing']
        require(populated != empty, 'file data/empty drawing ambiguity')
        profile = self._profile
        save = word(snapshot, profile['save_global'])
        require(word(snapshot, save) == profile['save_vtable'], 'file save manager type')
        identity = snapshot.read(save + profile['slot_offset'] + index * profile['slot_size'], 1)[0]
        require(empty == (identity == 255), 'file populated flag and artwork disagree')
        fonts = set()
        if empty:
            text = Loc.Get('file_empty', slot=index + 1)
        else:
            require('P_name_' + suffix in names and names['P_name_' + suffix]['drawing'],
                    'selected file identity not drawing')
            shown = self._texture(snapshot, names, 'P_name_' + suffix, 'N_data_' + suffix)
            if identity == profile['mii_identity']:
                # Native99 uses a portrait; a personal Mii name is not proven.
                identity_text = Loc.Get('file_identity_mii')
            else:
                require(0 <= identity < 15, 'unsupported native file identity')
                source = self._texture(snapshot, names, 'P_icon_name_%02d' % identity,
                                       'N_name_%02d' % identity)
                require(shown == source, 'file identity texture binding')
                identity_text = Loc.Get('file_identity_%02d' % identity)
            values = {}
            for field, prefix in (('flow', 'T_nori_num_'), ('medals', 'T_medal_num_')):
                name = prefix + suffix
                require(name in names, 'file statistic pane missing')
                pane = names[name]
                require(pane['drawing'] and pane['text'].isascii() and pane['text'].isdigit(),
                        'file statistics not drawable decimal text')
                fonts.add(pane['font'])
                values[field] = int(pane['text'])
            text = Loc.Get('file_summary', slot=index + 1, identity=identity_text, **values)
        return [message(owner, 'files:focus', text, 'N_file_btn_' + suffix, 'files')], fonts

    def _prompt(self, owner, names, state):
        """Use this native state's drawable prompt, excluding retained dialogs."""
        require(state['prompt'] in names, 'file menu prompt missing')
        prompt = names[state['prompt']]
        require(prompt['drawing'] and prompt.get('font') and prompt['text'].strip(),
                'file menu prompt not drawable text')
        if 'prompt_parent' in state:
            require(state['prompt_parent'] in names and
                    prompt['parent'] == names[state['prompt_parent']]['address'],
                    'file menu prompt ownership')
        return message(owner, 'files:prompt', Loc.Get('assist_text', text=prompt['text'].strip()),
                       state['prompt'], 'files'), prompt['font']

    def read(self, snapshot, owner, names):
        """Read verified active menu/dialog states through their native managers."""
        state = self._profile['states'].get(str(word(snapshot, owner['owner'] + 0x1c)))
        if state is None:
            return [], set()
        index = self._focus(snapshot, owner, names, state)
        if index is None:
            return [], set()
        if state['kind'] == 'slots':
            rows, fonts = self._slot(snapshot, owner, names, index)
            prompt, font = self._prompt(owner, names, state)
            fonts.add(font)
            return [prompt] + rows, fonts
        binding, fonts = state['buttons'][index], set()
        label = binding['label_pane']
        require(label in names, 'file menu label missing')
        pane = names[label]
        require(pane['parent'] == names[binding['pane']]['address'] and pane['drawing'],
                'file menu label ownership/drawing')
        if 'label_key' in binding:
            require(pane['vtable'] == self._profile['picture_vtable'], 'file menu artwork type')
            text = Loc.Get(binding['label_key'])
        else:
            require(pane.get('font') and pane['text'].strip(), 'file menu text missing')
            fonts.add(pane['font'])
            text = Loc.Get('assist_text', text=pane['text'].strip())
        rows = []
        if 'prompt' in state:
            prompt, font = self._prompt(owner, names, state)
            rows.append(prompt)
            fonts.add(font)
        rows.append(message(owner, 'files:focus', text, binding['pane'], 'files'))
        return rows, fonts
