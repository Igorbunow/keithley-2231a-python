"""Existing offline wire fixture for hardware qualification runner stage tests.

This is NOT a production instrument adapter or evidence of physical behavior.
Only qualification-stage tests depend on this mutable SCPI test fixture.
"""

class FakeTransport:
    """Stateful minimal SCPI emulator; validates command sequences, no hardware."""
    def __init__(self):
        self.cmds = []
        self.remote = False
        self.active_ch = 1
        self.values = {ch: {'VOLT': 0., 'CURR': 0., 'VOLT:LIM': 30.,
                            'VOLT:LIM:STAT': 0., 'CHAN:OUTP': 0.,
                            'OUTP:TIM:DEL': 10., 'OUTP:TIM:STAT': 0.} for ch in (1,2,3)}
        self.flags = {'OUTP:SER': 0., 'OUTP:PARALLEL': 0., 'OUTP:TRAC': 0., 'OUTP:STAT:ALL': 0.}
        self.operation_status_masks = {ch: None for ch in (1, 2, 3)}
        self.questionable_status_masks = {ch: 0 for ch in (1, 2, 3)}
        self.failed_query = None
        self.closed = False
        self.tracking_ratio = None
        self.tracking_fault = False

    def write(self, cmd):
        self.cmds.append(cmd)
        if cmd in ('SYST:REM', 'SYST:RWLOCK'):
            self.remote = True
            return
        if cmd == 'SYST:LOC':
            self.remote = False
            return
        if not self.remote:
            raise RuntimeError('SCPI command requires Remote mode')
        arg = cmd.split(' ', 1)
        key = arg[0].upper()
        if key == 'INST:NSEL': self.active_ch = int(arg[1]); return
        if key in self.values[self.active_ch]:
            value = float(arg[1])
            self.values[self.active_ch][key] = value
            if (key == 'VOLT' and value and self.active_ch in (1, 2)
                    and self.flags['OUTP:TRAC'] and self.tracking_ratio is not None
                    and not self.tracking_fault):
                self.values[3 - self.active_ch]['VOLT'] = (
                    value * self.tracking_ratio if self.active_ch == 1 else value / self.tracking_ratio)
            return
        if key in self.flags:
            self.flags[key] = float(arg[1])
            if key == 'OUTP:TRAC' and float(arg[1]):
                v1, v2 = (self.values[ch]['VOLT'] for ch in (1, 2))
                self.tracking_ratio = v2 / v1 if v1 and v2 else None
            if key == 'OUTP:STAT:ALL':
                for ch in self.values: self.values[ch]['CHAN:OUTP'] = float(arg[1])
            return
        if key == 'OUTP':
            self.flags['OUTP:STAT:ALL'] = float(arg[1])
            for ch in self.values: self.values[ch]['CHAN:OUTP'] = float(arg[1])
            return
        if key in ('SYST:REM','SYST:RWLOCK','SYST:LOC','SYST:BEEP'): return
        raise AssertionError(f'Unexpected SCPI: {cmd}')

    def query(self, cmd):
        self.cmds.append(cmd)
        if not self.remote and cmd not in ('*IDN?', 'SYST:ERR?'):
            raise RuntimeError('SCPI query requires Remote mode')
        if cmd == self.failed_query: raise RuntimeError('simulated link failure')
        key = cmd.upper().removesuffix('?')
        if key == '*IDN': return 'Keithley instruments, 2231A-30-3, TEST, 1.04-1.04'
        if key == 'INST:NSEL': return str(self.active_ch)
        if key in self.values[self.active_ch]:
            val = self.values[self.active_ch][key]
            return str(int(val)) if key in ('VOLT:LIM:STAT','CHAN:OUTP','OUTP:TIM:STAT') else str(val)
        if key in self.flags: return str(int(self.flags[key]))
        if key == 'MEAS:VOLT': return str(self.values[self.active_ch]['VOLT'])
        if key == 'MEAS:CURR': return str(self.values[self.active_ch]['CURR']/2)
        if key.startswith('STAT:OPER:INST:ISUM') and key.endswith(':COND'):
            ch = int(key.split('ISUM')[1].split(':')[0])
            if self.operation_status_masks[ch] is not None:
                return str(self.operation_status_masks[ch])
            return '9' if self.values[ch]['CHAN:OUTP'] else '0'
        if key.startswith('STAT:QUES:INST:ISUM') and key.endswith(':COND'):
            ch = int(key.split('ISUM')[1].split(':')[0])
            return str(self.questionable_status_masks[ch])
        if key == 'SYST:ERR': return '0,No error'
        if key == 'SYST:VERS': return '1999.0'
        if key in ('*STB','STAT:QUES:COND','STAT:OPER:COND'): return '0'
        raise AssertionError(f'Unexpected query: {cmd}')

    def close(self): self.closed = True

    def set_debug_status_trace(self, observer): self.debug_status_trace = observer
