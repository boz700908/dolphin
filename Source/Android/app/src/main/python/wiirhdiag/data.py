'''Load hash-bound diagnostic data without depending on an online service.'''

import json
from hashlib import sha256
from pathlib import Path

from .errors import DiagnosticError


class DiagnosticData:
    '''Verify the package's mapping, text-name list and executable fingerprints.'''

    def __init__(self, directory):
        directory = Path(directory)
        manifest = directory / 'manifest.json'
        if not manifest.is_file():
            raise DiagnosticError('data_missing', path=str(manifest))
        self.manifest = json.loads(manifest.read_text(encoding='utf-8'))
        for name, expected in self.manifest['files'].items():
            path = directory / name
            if not path.is_file():
                raise DiagnosticError('data_missing', path=str(path))
            if sha256(path.read_bytes()).hexdigest() != expected:
                raise DiagnosticError('data_mismatch', path=str(path))
        self.profile = json.loads((directory / 'profile.json').read_text(encoding='utf-8'))
        artifact = json.loads((directory / 'chinese-glyph-map.json').read_text(encoding='utf-8'))
        self.mapping = {int(row['source_code'][2:], 16): row['unicode'] for row in artifact['entries']}
        self.blank_codes = set(artifact['blank_source_codes'])
        self.names = self.profile['text_pane_names']

    def identity(self, mem1):
        '''Check exact build fingerprints without assigning menu/game state.'''
        if mem1[:6] != b'SOMJ01':
            raise DiagnosticError('wrong_game', game=mem1[:6].hex())
        for signature in self.profile['signatures']:
            offset = signature['address'] - 0x80000000
            expected = bytes.fromhex(signature['bytes_hex'])
            if mem1[offset:offset + len(expected)] != expected:
                raise DiagnosticError('wrong_signature')
