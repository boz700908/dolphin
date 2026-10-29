'''Live narration inside the emulator, based on native drawing ownership.

The reader runs in the emulator's own process, so it takes the addresses of the
emulated console's RAM from the emulator itself, asks the in-game menu for
commands, and speaks through the platform's text-to-speech service.

Evidence and scope: docs/game-api.md, Native Drawing Manager Evidence.
'''

from hashlib import sha256
import json
from pathlib import Path
import time

from .bridge import AssistRuntime
from .data import DiagnosticData
from .channel import ChannelHandler
from .errors import DiagnosticError
from .font import FontHandler
from .layout import LayoutHandler
from .graphics import SelectionStatsHandler
from .notice import DiagnosticNoticeHandler
from .selection_status import SelectionStatusHandler
from .completion_credits import CompletionCreditsHandler
from .grading import GradingHandler
from .dual_grading import DualGradingHandler
from .endless_result import EndlessResultHandler
from .karate import KarateHandler
from .samurai import SamuraiHandler
from .scene import SceneHandler
from .kungfu_state import KungfuStateHandler
from .kungfu_cues import KungfuCueHandler
from .audio import AudioHandler, NOTICE_FILENAME, NOTICE_KEY, write_notice_wave
from .pause import PauseHandler
from .two_player import TwoPlayerSelectionHandler
from .record_selector import RecordSelectorHandler
from .tutorial import TutorialHandler
from .tags import TextTagsHandler
from .file_selection import FileSelectionHandler
from .native_menus import CafeHandler, MusicLetterHandler, LetterHandler, ExtraGameMenuHandler, DeleteHandler
from .native_text import HihatHandler, LyricHandler
from .loc import Loc
from .logger import DebugLogger
from .memory import MemoryHandler
from .process_lifetime import ProcessLifetimeHandler, GameConnectionHandler
from .speech import SpeechHandler


class AssistData(DiagnosticData):
    '''Load separate assist assets without invalidating historical capture manifests.'''

    def __init__(self, directory):
        super().__init__(directory)
        directory = Path(directory)
        manifest = json.loads((directory / 'assist-manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['files'].items():
            path = directory / name
            if sha256(path.read_bytes()).hexdigest() != expected:
                raise DiagnosticError('data_mismatch', path=str(path))
        self.assist = json.loads((directory / 'assist-profile.json').read_text(encoding='utf-8'))
        self.audio_directory = directory
        self.font_source = (directory / 'font.bin').read_bytes()
        self.dual_number_source = (directory / 'dual-selection-number.bin').read_bytes()
        self.number_source = (directory / 'selection-number.bin').read_bytes()
        self.selection_reveal_source = (directory / 'selection-reveal.bin').read_bytes()
        self.dual_font_source = (directory / 'dual-grading-font.bin').read_bytes()
        self.lyric_font_source = (directory / 'lyric-font.bin').read_bytes()
        self.hihat_texture = (directory / self.assist['hihat']['demo']['texture']['filename']).read_bytes()
        self.karate_textures = {name:(directory / asset['filename']).read_bytes()
                                for name,asset in self.assist['karate']['textures'].items()}
        self.endless_textures = {name:(directory / asset['filename']).read_bytes()
                                 for name,asset in self.assist['endless_result']['textures'].items()}


class NarrationHandler:
    '''Track changes by content and owner type; no fixed heap address identity.'''

    def __init__(self):
        self._observed = {}
        self._spoken = {}
        self._persistent = set()
        self._last = []

    def reset(self):
        '''Clear state at process change, failure, pause or manual resume.'''
        self._observed.clear()
        self._spoken.clear()
        self._persistent.clear()
        self._last = []

    def update(self, items):
        '''Require two consecutive matching observations and announce only new items.'''
        present, ready = set(), []
        for row in items:
            key = row['key']
            present.add(key)
            if row.get('persist_on_gap'):
                self._persistent.add(key)
            value = (row['text'], row['owner_vtable'])
            before = self._observed.get(key)
            self._observed[key] = value
            if before == value and self._spoken.get(key) != value:
                self._spoken[key] = value
                ready.append(row)
        for key in set(self._observed) - present:
            self._observed.pop(key)
            if key not in self._persistent:
                self._spoken.pop(key, None)
        self._last = [row for row in items if self._spoken.get(row['key']) ==
                      (row['text'], row['owner_vtable'])]
        return ready

    def repeat(self):
        '''Return the current already-spoken message set, never departed-screen text.'''
        return list(self._last)


class ReadingHandler:
    '''Limit reading to observed feature families, rejecting ambiguity explicitly.'''

    def __init__(self, data):
        self._data = data
        self._font = FontHandler(data.font_source)
        self._stats = SelectionStatsHandler(data)
        self._selectionStatus = SelectionStatusHandler(data)
        self._scene = SceneHandler(data.assist['scene'])
        self._pause = PauseHandler(data)
        self._hihat = HihatHandler(data)
        self._lyrics = LyricHandler(data)
        self._tutorial = TutorialHandler(data)
        self._completionCredits = CompletionCreditsHandler(data)
        self._grading = GradingHandler(data)
        self._dualGrading = DualGradingHandler(data)
        self._endless = EndlessResultHandler(data)
        self._karate = KarateHandler(data)
        self._samurai = SamuraiHandler(data)
        self._files = FileSelectionHandler(data)
        profile = data.assist['native_menus']
        self._menus = {'cafe':CafeHandler(profile), 'lists':MusicLetterHandler(profile),
                       'letter':LetterHandler(profile), 'extras':ExtraGameMenuHandler(profile, TextTagsHandler(data)),
                       'two_player':TwoPlayerSelectionHandler(data),
                       'record_selector':RecordSelectorHandler(data)}
        self._delete = DeleteHandler()

    def reset(self):
        '''Reset native Samurai scene episode latches after process/pause recovery.'''
        self._samurai.reset()

    def read(self, snapshot):
        '''Gather drawing panes in supported target owner types; never guess focus.'''
        for signature in self._data.assist['signatures']:
            expected = bytes.fromhex(signature['bytes_hex'])
            if snapshot.read(signature['address'], len(expected)) != expected:
                raise DiagnosticError('wrong_signature')
        scene = self._scene.inspect(snapshot)
        if not scene['drawing']:
            return [], [], {'owners': [], 'phase': scene['phase'], 'scene': scene}
        pause = self._pause.inspect(snapshot, scene)
        lyrics = self._lyrics.inspect(snapshot, scene) if not pause else None
        manual = ((pause['owner'],) if pause and not pause['suppressed'] else
                  (lyrics['owner'],) if lyrics else ())
        layouts = LayoutHandler(self._data).inspect(snapshot, supported_only=True, manual_owners=manual)
        layouts['scene'] = scene
        if pause:
            layouts['pause'] = pause
            if pause['suppressed']:
                return [], [], layouts
            owners = [owner for owner in layouts['owners'] if owner['owner'] == pause['owner']]
            if len(owners) != 1:
                raise DiagnosticError('layout_invalid', detail='manual pause owner not registered')
            rows, fonts = self._pause.read(owners[0])
            for font in fonts:
                self._font.verify(snapshot, font)
            return rows, [], layouts
        items, descriptions, fonts = self._endless.read(snapshot, scene), [], set()
        items.extend(self._karate.read(snapshot, scene))
        items.extend(self._samurai.read(snapshot, scene))
        if lyrics:
            owners = [owner for owner in layouts['owners'] if owner['owner'] == lyrics['owner']]
            if len(owners) != 1:
                raise DiagnosticError('layout_invalid', detail='manual lyric owner not registered')
        supported = self._data.assist['features']
        for owner in layouts['owners']:
            feature = supported.get(str(owner['owner_vtable']))
            if feature is None:
                continue
            names = {}
            for pane in owner['panes']:
                if pane['name'] in names:
                    raise DiagnosticError('layout_invalid', detail='duplicate pane in supported layout')
                names[pane['name']] = pane
            for name in feature['required']:
                if name not in names:
                    raise DiagnosticError('layout_invalid', detail='supported owner missing ' + name)
            if feature['kind'] in self._data.assist['completion_credits']['scene_types']:
                text_items, text_fonts = self._completionCredits.read(
                    snapshot, owner, names, scene['scene'], feature['kind'])
                items.extend(text_items)
                fonts.update(text_fonts)
                continue
            if feature['kind'] == 'hihat':
                text_items, text_fonts = self._hihat.read(snapshot, owner, names, scene['scene'])
                items.extend(text_items)
                fonts.update(text_fonts)
                continue
            if feature['kind'] == 'lyrics':
                if lyrics and owner['owner'] == lyrics['owner']:
                    text_items, text_fonts = self._lyrics.read(snapshot, owner, names)
                    items.extend(text_items)
                    fonts.update(text_fonts)
                continue
            if feature['kind'] == 'tutorial':
                continue
            if feature['kind'] == 'dual_grading':
                items.extend(self._dualGrading.read(snapshot, owner, names, scene['scene']))
                continue
            if feature['kind'] == 'files':
                file_items, file_fonts = self._files.read(snapshot, owner, names)
                items.extend(file_items)
                fonts.update(file_fonts)
                delete_items, delete_fonts = self._delete.read(snapshot, owner, names)
                items.extend(delete_items)
                fonts.update(delete_fonts)
            if feature['kind'] in self._menus:
                menu_items, menu_fonts = self._menus[feature['kind']].read(snapshot, owner, names)
                items.extend(menu_items)
                fonts.update(menu_fonts)
            for name in feature['texts']:
                pane = names.get(name)
                if pane is None:
                    continue
                associated_description = (name == 'T_exposition_00' and
                                          names['T_game_title_00']['drawing'] and
                                          names['T_game_title_00']['text'])
                if (pane['drawing'] or associated_description) and pane['text'].strip():
                    fonts.add(pane['font'])
                    item = {'key':feature['kind'] + ':' + name, 'text':pane['text'].strip(),
                            'owner_vtable':owner['owner_vtable'], 'pane':name,
                            'kind':feature['kind']}
                    if name == 'T_exposition_00':
                        descriptions.append(item)
                        if pane['drawing']:
                            items.append(item)
                    else:
                        items.append(item)
            if feature['kind'] == 'selection':
                items = self._selectionStatus.decorate(snapshot, owner, names, items)
                items.extend(self._stats.read(snapshot, owner, names))
            if feature['kind'] == 'title':
                for item in items:
                    if item['kind'] == 'title':
                        item['text'] = Loc.Get('title_prompt', prompt=item['text'])
            if feature['kind'] == 'grading':
                comment_items = [item for item in items if item['kind'] == 'grading' and
                                 item['pane'] in ('T_Comment_00', 'T_Comment_01', 'T_Comment_02')]
                if comment_items:
                    items = [item for item in items if item not in comment_items]
                    # A single utterance keeps the simultaneous comments together.
                    combined = dict(comment_items[0])
                    combined['key'] = 'grading:comments'
                    combined['pane'] = [item['pane'] for item in comment_items]
                    combined['text'] = Loc.Get('grading_comments',
                                              comments=' '.join(item['text'] for item in comment_items))
                    insert = next((i + 1 for i, item in enumerate(items)
                                   if item['key'] == 'grading:T_Caption_00'), len(items))
                    items.insert(insert, combined)
                items.extend(self._grading.read(snapshot, owner, names))
        tutorial_items, tutorial_fonts = self._tutorial.read(snapshot, layouts['owners'])
        items.extend(tutorial_items)
        fonts.update(tutorial_fonts)
        keys = [row['key'] for row in items]
        if len(keys) != len(set(keys)):
            raise DiagnosticError('layout_invalid', detail='multiple drawing owners for same supported message')
        for font in fonts:
            self._font.verify(snapshot, font)
        return items, descriptions, layouts


# Screens that wait for the player to press a button: a dialogue line, a grade, a letter, a menu,
# the tutorial prompt, the game's own title and pause screens. A press on one of these is an
# answer, so the message it produces replaces whatever is still being read.
AWAITING_KINDS = frozenset((
    'title', 'selection', 'files', 'tutorial', 'cafe', 'letter', 'lists', 'extras',
    'two_player', 'record_selector', 'grading', 'dual_grading', 'endless_result',
    'ending', 'prologue', 'perfect_success', 'staff_credits', 'cast_credits', 'pause',
))


def awaiting_input(data, items, layouts):
    """Whether the screen just read is one that waits for the player to press something.

    This is read from the native layouts rather than timed: a screen either owns a supported
    layout that waits for a button or it does not, and the reader already knows which. Gameplay
    screens are never in the set, so a press during play can never cut narration short.
    """
    for row in items:
        if row.get('kind') in AWAITING_KINDS:
            return True
    features = data.assist['features']
    for owner in layouts.get('owners', ()):
        feature = features.get(str(owner['owner_vtable']))
        if feature is not None and feature['kind'] in AWAITING_KINDS:
            return True
    return False


# Menu commands the emulator posts; the same names as the Kotlin constants.
COMMAND_STOP = 'stop'
COMMAND_PAUSE = 'pause'
COMMAND_REPEAT = 'repeat'
COMMAND_DESCRIPTION = 'description'


def run(directory=None):
    """Read the running game until the menu asks the worker to stop.

    The narration data files are given by the host, which expands them out of
    the APK beside the game image. Falling back to a directory next to this
    package keeps the module runnable outside the app, against a staged copy.
    """
    logger = DebugLogger()
    # The window the launcher version reported through has no counterpart here,
    # so its messages go to the app log.
    channel = ChannelHandler()
    speech, audio, cues, kungfu_state = None, None, None, None
    notice, notice_audio, lifetime = None, None, None
    try:
        data = AssistData(Path(directory) if directory else
                          Path(__file__).resolve().parent / 'data')
        speech = SpeechHandler(logger)
        # The notification tone is generated rather than shipped as a sound
        # asset, as the launcher version generates it, and is written beside the
        # cue sounds for the platform to load with them. A directory that cannot
        # be written costs the tone and nothing else.
        try:
            write_notice_wave(data.audio_directory)
            notice_audio = AudioHandler(data.audio_directory,
                                        {'assets': {NOTICE_KEY: {'filename': NOTICE_FILENAME}}})
        except (DiagnosticError, OSError) as error:
            logger.event('Notification tone unavailable', error=str(error))
        notice = DiagnosticNoticeHandler(
            channel, logger,
            None if notice_audio is None else (lambda: notice_audio.play(0, 'notice')))
        audio = AudioHandler(data.audio_directory, data.assist['kungfu_audio'])
        # The cue sounds live next to the data files; the platform loads them
        # in the background and a cue that is not ready yet stays silent.
        AssistRuntime.prepareCues(str(data.audio_directory))
        memory, reader = MemoryHandler(data, logger), ReadingHandler(data)
        lifetime = ProcessLifetimeHandler()
        connection = GameConnectionHandler(lifetime, channel, logger, notice)
        kungfu_state = KungfuStateHandler(data, flows=True)
        cues = KungfuCueHandler(data.assist['kungfu_cues'])
        narration = NarrationHandler()
        paused, descriptions = False, []
        # A press is remembered until the message it produced is spoken, because the reader
        # confirms a change over two captures and the answer arrives a frame or two later.
        pressed = False
        channel.emit('ready', text=Loc.Get('gui_worker_ready'))
        while True:
            tick = time.monotonic()
            command = AssistRuntime.pollCommand()
            # The menu commands are pressed by the player with the same buttons as the game's own
            # screens, so their answers replace whatever is being read.
            if command == COMMAND_STOP:
                speech.say('assist_goodbye', flush=True)
                return 0
            if command == COMMAND_PAUSE:
                paused = not paused
                narration.reset(); reader.reset()
                cues.reset()
                audio.stop()
                descriptions = []
                if paused:
                    # Drop anything queued: after resuming it would be stale.
                    speech.clear()
                speech.say('assist_paused' if paused else 'assist_resumed', flush=True)
            elif command == COMMAND_REPEAT:
                batch = narration.repeat()
                if not batch:
                    speech.say('assist_no_message', flush=True)
                # Asked for, so the first line replaces the stale sentence; the rest of the
                # repetition queues behind it so every line is still read.
                for index, row in enumerate(batch):
                    speech.say('assist_text', text=row['text'], flush=index == 0)
            elif command == COMMAND_DESCRIPTION:
                if not descriptions:
                    speech.say('assist_no_description', flush=True)
                for index, row in enumerate(descriptions):
                    speech.say('assist_text', text=row['text'], flush=index == 0)
            # The game can end while narration is paused, so this always runs.
            try:
                emulator_exited = connection.poll()
                # The process can be watched again, so a failure to watch it is
                # over; connection.poll() has already ended it if the game ended.
                notice.recover('process')
            except DiagnosticError as error:
                notice.failure('process', error, time.monotonic())
                emulator_exited = False
            if emulator_exited:
                narration.reset(); reader.reset()
                cues.reset()
                audio.stop()
                descriptions = []
                pressed = False
            if paused:
                # Nothing on screen is being read, so there is nothing for a press to answer.
                AssistRuntime.setAwaitingInput(False)
                pressed = False
            if not paused:
                snapshot = None
                try:
                    snapshot, metadata = memory.capture()
                    if connection.attach(metadata['pid']):
                        narration.reset(); reader.reset()
                        cues.reset()
                        audio.stop()
                        speech.say('assist_attached')
                        channel.emit('attached', text=Loc.Get('assist_attached'),
                                     pid=metadata['pid'])
                    observed = None
                    try:
                        observed = kungfu_state.inspect(snapshot)
                    except DiagnosticError as cue_error:
                        # A round state that cannot be verified must not be
                        # guessed at, but the rest of the reading continues.
                        cues.reset()
                        audio.stop()
                        notice.failure('cues', cue_error, time.monotonic())
                    items, descriptions, layouts = reader.read(snapshot)
                    # Whether this screen waits for a button, told to the speech side so that a
                    # press can end the sentence in progress there and only there. The presses of
                    # the frame are taken here and count as an answer only on such a screen.
                    waiting = awaiting_input(data, items, layouts)
                    AssistRuntime.setAwaitingInput(waiting)
                    # Taken every frame either way: a press during play answers nothing, and once
                    # the game stops waiting the press it was waiting for is spent as well.
                    presses = AssistRuntime.takePresses()
                    pressed = waiting and (pressed or presses > 0)
                    if observed is not None:
                        try:
                            cue = cues.update(observed, metadata, items)
                            if ('fields' not in observed or observed.get('paused') or
                                    observed['fields']['round_state'] != 2):
                                audio.stop()
                            if cue is not None:
                                audio.play(cue['player'], cue['distance'])
                                logger.event('Kungfu audio cue', **cue)
                            # The cues followed this capture, so whatever was being
                            # reported about them is over.
                            notice.recover('cues')
                        except DiagnosticError as cue_error:
                            cues.reset()
                            audio.stop()
                            notice.failure('cues', cue_error, time.monotonic())
                    # This read worked, so the episode a failed one opened is
                    # over and is closed here.
                    notice.recover('reading')
                    batch = narration.update(items)
                    if batch:
                        # The first message of an answer replaces the stale sentence, because the
                        # press that asked for it has already ended that sentence; the rest of the
                        # batch queues behind it so nothing is skipped.
                        for index, row in enumerate(batch):
                            speech.say('assist_text', text=row['text'],
                                       flush=index == 0 and pressed)
                            logger.event('Live announcement', **row)
                        pressed = False
                    if logger.enabled:
                        logger.debug('Live layout observation', pid=metadata['pid'], items=items,
                                     owners=[{**{k: o[k] for k in ('owner', 'owner_vtable', 'root')},
                                              'readable_fields':[{k: p[k] for k in
                                                                  ('name', 'flags', 'computed_alpha',
                                                                   'draw_path', 'text')}
                                                                 for p in o['panes'] if p.get('font') or
                                                                 p['name'] in ('N_HI_00', 'N_OK_00',
                                                                               'N_NG_00', 'N_But_00')]}
                                             for o in layouts['owners']])
                except (DiagnosticError, OSError) as error:
                    # The game may have ended during this capture.
                    exited_during_capture = connection.poll()
                    absent = isinstance(error, DiagnosticError) and error.key == 'no_process'
                    if exited_during_capture or absent:
                        connection.waiting()
                        narration.reset(); reader.reset()
                        cues.reset()
                        audio.stop()
                        descriptions = []
                        AssistRuntime.setAwaitingInput(False)
                        pressed = False
                    else:
                        # Guest RAM may be mid-write; do not read stale state. The
                        # failure opens an episode, which is reported once however
                        # long it lasts and is closed by the first read that works.
                        notice.failure('reading', error, time.monotonic())
                        narration.reset(); reader.reset()
                        cues.reset()
                        audio.stop()
                        descriptions = []
                        # Nothing could be read, so nothing is known to be waiting, and a press
                        # made now must not resurface as an answer to the next screen read.
                        AssistRuntime.setAwaitingInput(False)
                        pressed = False
            time.sleep(max(0, 0.08 - (time.monotonic() - tick)))
    except (DiagnosticError, OSError) as error:
        # Nothing can be read at all any more, so this is not a failure the assist
        # waits out but the end of this run.
        if notice is not None:
            notice.fatal(error)
        else:
            logger.event('Fatal assistant error', error=str(error))
        return 1
    finally:
        # Teardown is reported per area while there is still something to report
        # it through, as the launcher version does.
        if lifetime is not None:
            try:
                lifetime.close()
            except DiagnosticError as error:
                if notice is not None:
                    notice.failure('process_shutdown', error, time.monotonic())
        if audio is not None:
            try:
                audio.stop()
            except DiagnosticError as error:
                if notice is not None:
                    notice.failure('audio_shutdown', error, time.monotonic())
        if notice_audio is not None:
            try:
                notice_audio.stop()
            except DiagnosticError as error:
                if notice is not None:
                    notice.failure('notice_shutdown', error, time.monotonic())
