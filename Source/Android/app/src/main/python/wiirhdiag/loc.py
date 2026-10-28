'''Keep every reader-facing string in localization.'''

import json
from pathlib import Path


class Loc:
    '''Resolve required strings without masking missing keys.'''

    _strings = None

    @classmethod
    def Get(cls, key, **values):
        '''Format one required localization entry.'''
        if cls._strings is None:
            path = Path(__file__).resolve().parent / 'locales/zh-CN.json'
            cls._strings = json.loads(path.read_text(encoding='utf-8'))
        return cls._strings[key].format(**values)
