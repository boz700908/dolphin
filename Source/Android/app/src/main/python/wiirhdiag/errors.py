'''Expected external errors must reach both the user and session log.'''

from .loc import Loc


class DiagnosticError(Exception):
    '''Carry a localized, actionable diagnostic error.'''

    def __init__(self, key, **values):
        super().__init__(Loc.Get(key, **values))
        self.key = key
        self.values = values
