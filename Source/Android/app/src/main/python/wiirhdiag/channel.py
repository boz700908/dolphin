'''Where the launcher version put the messages it showed in its own window.

The window is gone. This build runs beside the game inside the app, which has no
status area to show and no one reading a screen of text, so the messages are
kept in the app log instead -- the place they can still be read back from after
a session, which in the launcher version was the window's own history.

They are deliberately not spoken. The spoken channel belongs to the game's own
narration, and a reader interrupted mid-sentence by a report about a broken area
ends up knowing less than one who hears the notification tone and the narration
that still works.
'''

from .bridge import AssistRuntime


class ChannelHandler:
    '''Write each message the launcher window used to show as one log line.'''

    def emit(self, kind, **values):
        '''Record one message and its fields, in the launcher's vocabulary.'''
        fields = ' '.join('%s=%s' % (key, value) for key, value in values.items())
        AssistRuntime.log('assist %s %s' % (kind, fields))
