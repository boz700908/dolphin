"""Shared read-only Kungfu sample, including verified native TickFlow objects.

Authority: game-api.md, Kungfu Native Volley Audio Cue Binding.
"""
import math
import struct

from .errors import DiagnosticError
from .graphics import require, word
from .pause import PauseHandler
from .scene import SceneHandler


class KungfuStateHandler:
    """Inspect once per acquired snapshot for both recorder and audio cues."""

    def __init__(self, data, flows=False):
        self._profile = data.assist['kungfu_trace']
        self._cueProfile = data.assist['kungfu_cues']
        self._scene = SceneHandler(data.assist['scene'])
        self._pause = PauseHandler(data)
        self._includeFlows = flows

    def _checkers(self, snapshot, manager, scene):
        profile = self._profile
        heap = word(snapshot, manager + profile['input_manager_heap_offset'])
        require(snapshot.read(heap, 4).hex() == profile['heap_magic'], 'Kungfu trace input heap type')
        start = word(snapshot, heap + profile['heap_start_offset'])
        end = word(snapshot, heap + profile['heap_end_offset'])
        require(start < end and snapshot.contains(start, end - start), 'Kungfu trace input heap bounds')
        address = word(snapshot, manager + profile['input_manager_head_offset'])
        seen, previous, rows = set(), 0, []
        while address:
            require(address not in seen, 'Kungfu trace checker list cycle', checker=address)
            seen.add(address)
            header = address - profile['allocation_header']
            require(address % 4 == 0 and start <= header and address + 12 <= end,
                    'Kungfu trace checker heap ownership', checker=address)
            require(snapshot.read(header, 2).hex() == profile['used_tag'],
                    'Kungfu trace checker allocation not active', checker=address)
            size = word(snapshot, header + 4)
            require(size >= 12 and address + size <= end and snapshot.contains(address, size),
                    'Kungfu trace checker allocation size', checker=address)
            base = snapshot.read(address, 12)
            vtable, following, backlink = struct.unpack('>3I', base)
            require(backlink == previous, 'Kungfu trace checker backlink', checker=address)
            row = {'address':address, 'vtable':vtable, 'allocation_header_hex':
                   snapshot.read(header, profile['allocation_header']).hex(), 'base_hex':base.hex()}
            if vtable == profile['checker_vtable']:
                require(size >= profile['checker_size'], 'Kungfu trace Kungfu checker size', checker=address)
                raw = snapshot.read(address, profile['checker_size'])
                fields = profile['checker_fields']
                require(struct.unpack_from('>I', raw, fields['scene'])[0] == scene,
                        'Kungfu trace checker belongs to a different scene', checker=address)
                row['raw_hex'] = raw.hex()
                for name, offset in fields.items():
                    if name in ('elapsed_ticks', 'target_ticks', 'motion_ticks'):
                        value = struct.unpack_from('>f', raw, offset)[0]
                        require(math.isfinite(value), 'Kungfu trace nonfinite checker time', checker=address)
                    elif name in ('resolved', 'input_count'):
                        value = raw[offset]
                    else:
                        value = struct.unpack_from('>I', raw, offset)[0]
                    row[name] = value
                require(row['resolved'] in (0, 1), 'Kungfu trace checker resolved flag', checker=address)
            rows.append(row)
            previous, address = address, following
        return rows

    def inspect(self, snapshot):
        profile = self._profile
        for signature in profile['signatures']:
            expected = bytes.fromhex(signature['bytes_hex'])
            if snapshot.read(signature['address'], len(expected)) != expected:
                raise DiagnosticError('wrong_signature')
        scene = self._scene.inspect(snapshot)
        result = dict(scene)
        if 'scene' not in scene:
            return result
        result['scene_vtable'] = word(snapshot, scene['scene'])
        if result['scene_vtable'] != profile['scene_vtable'] or not scene['drawing']:
            return result
        raw = snapshot.read(scene['scene'], profile['scene_sample_size'])
        fields = {}
        for name, offset in profile['scene_fields'].items():
            fields[name] = (struct.unpack_from('>I', raw, offset)[0]
                            if name in ('round_state', 'position_p1', 'position_p2') else raw[offset])
        manager = word(snapshot, profile['input_manager_global'])
        require(manager != 0, 'Kungfu trace active scene has no input manager')
        result.update({'scene_hex':raw.hex(), 'fields':fields, 'input_manager':manager,
                       'input_manager_hex':snapshot.read(manager, profile['input_manager_sample_size']).hex(),
                       'checkers':self._checkers(snapshot, manager, scene['scene'])})
        if self._includeFlows:
            result['paused'] = self._pause.inspect(snapshot, scene) is not None
            result['flows'] = self._flows(snapshot, scene['scene']) if fields['round_state'] == 2 else []
            timing = self._cueProfile['formal_timing']
            if fields['round_state'] == 2 and fields['mode'] == timing['mode']:
                result['planned_action'] = self._planned_action(snapshot,raw,fields)

        return result

    def _flows(self, snapshot, scene):
        """Validate allocated, scene-bound flows; retain native instance identities."""
        profile, heap_profile = self._cueProfile, self._profile
        for signature in profile['signatures']:
            expected = bytes.fromhex(signature['bytes_hex'])
            require(snapshot.read(signature['address'],len(expected)) == expected,
                    'Kungfu flow executable signature')
        manager = word(snapshot,profile['flow_manager_global'])
        require(manager != 0,'Kungfu active scene has no flow manager')
        require(word(snapshot,manager+8) == profile['flow_size'],'Kungfu flow configured size')
        heap = word(snapshot,manager+profile['flow_heap_offset'])
        require(snapshot.read(heap,4).hex() == heap_profile['heap_magic'],'Kungfu flow heap type')
        start = word(snapshot,heap+heap_profile['heap_start_offset'])
        end = word(snapshot,heap+heap_profile['heap_end_offset'])
        require(start < end and snapshot.contains(start,end-start),'Kungfu flow heap bounds')
        address = word(snapshot,manager+profile['flow_head_offset'])
        seen, instances, previous, rows = set(), set(), 0, []
        while address:
            require(address not in seen,'Kungfu flow list cycle',flow=address)
            seen.add(address)
            header = address-heap_profile['allocation_header']
            require(address % 4 == 0 and start <= header and address+12 <= end,
                    'Kungfu flow heap ownership',flow=address)
            require(snapshot.read(header,2).hex() == heap_profile['used_tag'],
                    'Kungfu flow allocation not active',flow=address)
            size = word(snapshot,header+4)
            require(size >= 12 and address+size <= end and snapshot.contains(address,size),
                    'Kungfu flow allocation size',flow=address)
            vtable, following, backlink = struct.unpack('>3I',snapshot.read(address,12))
            require(backlink == previous,'Kungfu flow backlink',flow=address)
            row = {'address':address,'vtable':vtable}
            if vtable == profile['flow_vtable']:
                require(size >= profile['flow_size'],'Kungfu flow object size',flow=address)
                raw = snapshot.read(address,profile['flow_size'])
                fields = profile['flow_fields']
                require(struct.unpack_from('>I',raw,fields['scene'])[0] == scene,
                        'Kungfu flow belongs to a different scene',flow=address)
                row['raw_hex'] = raw.hex()
                for name, offset in fields.items():
                    row[name] = struct.unpack_from('>f' if name == 'rest' else '>I',raw,offset)[0]
                require(math.isfinite(row['rest']),'Kungfu nonfinite flow rest',flow=address)
                require(row['instance'] not in instances,'Kungfu duplicate flow instance',flow=address)
                instances.add(row['instance'])
                routine = profile['scripts'].get(str(row['code']))
                if routine is not None:
                    require(snapshot.read(row['code'],routine['size']).hex() == routine['bytes_hex'],
                            'Kungfu volley bytecode signature',flow=address)
                    index = row['instruction']
                    require(str(index) in routine['rest_prefix'] or index == routine['size']//4,
                            'Kungfu volley instruction boundary',flow=address)
                    if index and index < routine['size']//4:
                        elapsed = routine['rest_prefix'][str(index)]-row['rest']
                        remaining = profile['ordinary_target_ticks']-elapsed
                        if remaining > 0:
                            row['first_pending_ticks'] = remaining
                            row['first'] = routine['first']
            rows.append(row)
            previous, address = address, following
        return rows

    def _planned_action(self, snapshot, scene_bytes, fields):
        """Read the game's exact one-step action query, including randomized rollover."""
        profile = self._cueProfile['formal_timing']
        for signature in profile['signatures'] + [profile['table']]:
            expected = bytes.fromhex(signature['bytes_hex'])
            require(snapshot.read(signature['address'],len(expected)) == expected,
                    'Kungfu native timing signature')
        table = profile['table']
        pattern, subpattern, step = fields['pattern'], fields['subpattern'], fields['step']
        def action_at(pattern,subpattern,step):
            require(0 <= pattern < table['pattern_count'] and
                    0 <= subpattern < table['subpattern_count'] and
                    0 <= step < table['step_count'],'Kungfu action table index',
                    pattern=pattern,subpattern=subpattern,step=step)
            index = (pattern*table['subpattern_count']+subpattern)*table['step_count']+step
            return word(snapshot,table['address']+index*4)
        # Native80019670 increments once, then tests the actual terminal entry.
        step += 1
        code = action_at(pattern,subpattern,step)
        if code == table['terminal']:
            subpattern = min(subpattern+1,table['subpattern_count']-1)
            pattern = scene_bytes[table['next_patterns_offset']+subpattern]
            step = 0
            code = action_at(pattern,subpattern,step)
        return {'code':code,'pattern':pattern,'subpattern':subpattern,'step':step,
                'routine':profile['dispatcher'].get(str(code)),
                'distance':profile['distances'].get(str(code))}
