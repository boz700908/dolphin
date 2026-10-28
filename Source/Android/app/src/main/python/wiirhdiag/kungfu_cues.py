"""Native volley-entry and target-position audio cues; no synthetic hit schedule.

Authority: game-api.md, Kungfu Native Volley Audio Cue Binding.
"""


from .graphics import require


class KungfuCueHandler:
    """Choose one cue for a new starter/spacing context, never each ordinary hit."""

    def __init__(self, profile):
        self._profile = profile
        self.reset()

    def reset(self):
        """Discard scene/lesson history after exit, failure, or helper suspension."""
        self._context = None
        self._seen = set()
        self._pair = None
        self._positions = None
        self._instruction = None

    def update(self, observed, metadata, items):
        """Return an evidence-bearing cue or None; native judgments remain authoritative."""
        if 'fields' not in observed:
            self.reset()
            return None
        fields = observed['fields']
        context = (metadata['pid'],observed['scene'],fields['mode'])
        if context != self._context or fields['round_state'] != self._profile['active_round_state']:
            self.reset()
            self._context = context
        flows = [flow for flow in observed['flows'] if 'instance' in flow]
        identities = {(flow['instance'],flow['code']) for flow in flows}
        if fields['round_state'] != self._profile['active_round_state']:
            self._seen = identities
            return None
        if observed['paused']:
            self._seen = identities
            return None
        instructions = tuple((row['text'],row['owner_vtable']) for row in items
                             if row['key'] == 'tutorial:instruction')
        if instructions and instructions != self._instruction:
            self._pair = None
        self._instruction = instructions
        positions = (fields['position_p1'],fields['position_p2'])
        distance = self._profile['positions'].get('%d,%d' % positions)
        positions_changed = self._positions is not None and positions != self._positions
        self._positions = positions
        pending = [row for row in observed['checkers'] if 'input' in row and
                   not row['resolved'] and row['input'] in (4,32) and
                   row['event'] == row['input'] and row['target_ticks']-row['elapsed_ticks'] > 0]
        new_flows = [flow for flow in flows if (flow['instance'],flow['code']) not in self._seen
                     and 'first' in flow and any(
                         row['input'] == flow['first']['input'] and
                         row['target_parameter'] == flow['first']['target_parameter'] and
                         row['motion_ticks'] == flow['first']['motion_ticks'] for row in pending)]
        # Keep currently allocated identities only; natural generation handles address reuse.
        if distance is None and fields['mode'] != self._profile['formal_timing']['mode']:
            return None  # Native intermediate targets have no proven distance label.
        source, player, remaining, evidence = None, None, None, {}
        formal = fields['mode'] == self._profile['formal_timing']['mode']
        if not formal and (positions_changed or (self._pair is not None and self._pair[1] != distance)) and pending:
            check = min(pending,key=lambda row:row['target_ticks']-row['elapsed_ticks'])
            player = self._profile['players'][str(check['input'])]
            remaining = check['target_ticks']-check['elapsed_ticks']
            source, evidence = 'native_position_change', {'checker':check['address']}
        elif new_flows:
            flow = min(new_flows,key=lambda row:row['first_pending_ticks'])
            if formal:
                plan = observed['planned_action']
                require(plan['routine'] == flow['code'] and plan['distance'] is not None,
                        'Kungfu new volley does not match native planned action',
                        flow=flow['address'],instance=flow['instance'],code=flow['code'],plan=plan)
                distance = plan['distance']
            player = self._profile['players'][str(flow['first']['input'])]
            remaining = flow['first_pending_ticks']
            source, evidence = 'native_volley_entry', {'flow':flow['address'],
                       'instance':flow['instance'],'code':flow['code']}
            if formal:
                evidence['planned_action'] = dict(plan)
        self._seen = {(flow['instance'],flow['code']) for flow in flows
                      if flow['instruction'] > 0 or (flow['instance'],flow['code']) in self._seen}
        if player is None or self._pair == (player,distance):
            return None
        self._pair = player,distance
        return {'player':player,'distance':distance,'source':source,
                'remaining_ticks':remaining,'positions':positions,**evidence}
