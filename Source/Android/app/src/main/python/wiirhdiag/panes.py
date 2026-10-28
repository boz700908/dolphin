'''Inspect TextBox candidates in immutable RAM snapshots.

Offsets/types are backed by docs/game-api.md. This tool does not infer focus,
visibility, lifetime or font selection from a name or successful buffer read.
'''

import struct

from .loc import Loc

NAME_OFFSET = 0xbc
TEXT_POINTER_OFFSET = 0xd8
FONT_POINTER_OFFSET = 0xe4
CAPACITY_OFFSET = 0xfc
USED_OFFSET = 0xfe
HEADER_LENGTH = 0x100

# Target tutorial and information-card evidence confirms both button glyphs.
# Evidence: docs/game-api.md, User Fix Evidence.
CONFIRMED_BUTTON_GLYPH_KEYS = {0xe042: 'button_a', 0xe043: 'button_b'}
# Exact common-font bitmap reviewed; emitted text still requires runtime font identity.
# Evidence: game-api.md, Ordinary Endless, Native Draw-list Lifetime and Rap Roles.
CONFIRMED_SYMBOL_GLYPH_KEYS = {0xe017: 'glyph_heart'}


def render_candidate(codes, mapping, blank_codes):
    '''Omit only a zero-valued suffix from speech; retain embedded controls.'''
    text, markers = [], []
    end = len(codes)
    while end and codes[end - 1] == 0:
        end -= 1
    for code in codes[:end]:
        if code in CONFIRMED_BUTTON_GLYPH_KEYS:
            key = CONFIRMED_BUTTON_GLYPH_KEYS[code]
            text.append(Loc.Get(key))
            markers.append({'kind': 'confirmed_button_glyph', 'code_unit': code, 'label_key': key})
        elif code in CONFIRMED_SYMBOL_GLYPH_KEYS:
            key = CONFIRMED_SYMBOL_GLYPH_KEYS[code]
            text.append(Loc.Get(key))
            markers.append({'kind': 'confirmed_symbol_glyph', 'code_unit': code, 'label_key': key})
        elif code in mapping:
            text.append(mapping[code])
        elif code in blank_codes:
            text.append(Loc.Get('blank_marker', hexcode='%04X' % code))
            markers.append({'kind': 'blank_font_slot', 'code_unit': code})
        elif code in {9, 10, 13}:
            text.append(chr(code))
        elif code < 32 or code == 0x7f or 0xd800 <= code <= 0xdfff:
            text.append(' ' + Loc.Get('control_marker', hexcode='%04X' % code) + ' ')
            markers.append({'kind': 'control_or_unpaired_surrogate', 'code_unit': code})
        elif 0xe000 <= code <= 0xf8ff:
            text.append(' ' + Loc.Get('private_marker', hexcode='%04X' % code) + ' ')
            markers.append({'kind': 'private_use_glyph', 'code_unit': code})
        else:
            text.append(chr(code))
    return ''.join(text), markers


class PaneHandler:
    '''Keep all structurally validated candidates and every rejected name match.'''

    def __init__(self, data):
        self._data = data

    def inspect(self, snapshot):
        '''Search exact extracted TXT1 names, validate type, then bounded buffers.'''
        candidates, rejected = [], []
        vtable = self._data.profile['textbox_vtable']
        for name in self._data.names:
            encoded = name.encode('ascii') + b'\0'
            for region_base, memory in snapshot.regions:
                offset = memory.find(encoded)
                while offset >= 0:
                    name_address = region_base + offset
                    object_address = name_address - NAME_OFFSET
                    reason = None
                    if object_address % 4 or not snapshot.contains(object_address, HEADER_LENGTH):
                        reason = 'Object alignment or header bounds'
                    else:
                        header = snapshot.read(object_address, HEADER_LENGTH)
                        if struct.unpack_from('>I', header)[0] != vtable:
                            reason = 'Not the verified target TextBox vtable'
                        else:
                            pointer = struct.unpack_from('>I', header, TEXT_POINTER_OFFSET)[0]
                            capacity = struct.unpack_from('>H', header, CAPACITY_OFFSET)[0]
                            used = struct.unpack_from('>H', header, USED_OFFSET)[0]
                            length = (used + 1) * 2
                            if pointer % 2 or capacity % 2 or capacity < length:
                                reason = 'Buffer alignment, capacity or used length'
                            elif not snapshot.contains(pointer, length):
                                reason = 'Text buffer outside captured RAM'
                            else:
                                raw = snapshot.read(pointer, length)
                                if raw[-2:] != b'\0\0':
                                    reason = 'Missing terminator at recorded used length'
                                else:
                                    codes = list(struct.unpack('>%dH' % used, raw[:-2])) if used else []
                                    text, controls = render_candidate(codes, self._data.mapping, self._data.blank_codes)
                                    meaningful_end = len(codes)
                                    while meaningful_end and codes[meaningful_end - 1] == 0:
                                        meaningful_end -= 1
                                    candidates.append({
                                        'name': name, 'object_address': object_address, 'name_address': name_address,
                                        'vtable': vtable, 'text_pointer': pointer, 'capacity_bytes': capacity,
                                        'used_characters': used, 'font_pointer': struct.unpack_from('>I', header, FONT_POINTER_OFFSET)[0],
                                        'raw_header_hex': header.hex(), 'raw_buffer_hex': raw.hex(),
                                        'raw_code_units': codes, 'recovered_text_candidate': text,
                                        'trailing_zero_code_units': len(codes) - meaningful_end,
                                        'diagnostic_markers': controls,
                                        'scope': 'Structurally validated candidate; visibility/lifetime/focus unverified',
                                        'font_scope': 'Reviewed Chinese mapping applied provisionally; live font identity unverified'})
                    if reason is not None:
                        rejected.append({'name': name, 'name_address': name_address, 'reason': reason})
                    offset = memory.find(encoded, offset + 1)
        candidates.sort(key=lambda row: (not bool(row['recovered_text_candidate']), row['name'], row['object_address']))
        return {'scope': 'Diagnostic candidates only', 'candidates': candidates, 'rejected_name_matches': rejected}


def pointer_references(snapshot, candidates):
    '''Record aligned references to found objects/buffers without assigning meaning.'''
    targets = {r['object_address'] for r in candidates} | {r['text_pointer'] for r in candidates}
    found = {str(target): [] for target in sorted(targets)}
    if not targets:
        return found
    for base, memory in snapshot.regions:
        for offset, (value,) in enumerate(struct.iter_unpack('>I', memory)):
            if value in targets:
                found[str(value)].append(base + offset * 4)
    return found


def compare_candidates(previous, current):
    '''Describe address/text changes; never classify them as selection changes.'''
    old = {(r['object_address'], r['name']): r for r in previous}
    new = {(r['object_address'], r['name']): r for r in current}
    return {'added': [new[k] for k in sorted(new.keys() - old.keys())],
            'absent': [old[k] for k in sorted(old.keys() - new.keys())],
            'changed_buffers': [{'object_address': k[0], 'name': k[1],
                                 'previous_raw_buffer_hex': old[k]['raw_buffer_hex'],
                                 'current_raw_buffer_hex': new[k]['raw_buffer_hex']}
                                for k in sorted(old.keys() & new.keys())
                                if old[k]['raw_buffer_hex'] != new[k]['raw_buffer_hex']]}
