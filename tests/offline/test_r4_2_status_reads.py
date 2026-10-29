"""R4.2-A: read-only documented SCPI, without consuming event latches."""
from __future__ import annotations
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError, TransportError
from test_driver import FakeTransport


class ReadOnlyTransport(FakeTransport):
    def __init__(self):
        super().__init__()
        self.module = '2231,1.04'
        self.masks = {'*ESE?': '33', '*SRE?': '128',
                      'STAT:QUES:ENAB?': '17', 'STAT:OPER:ENAB?': '255'}
        self.bad_reply = None

    def query(self, cmd):
        if cmd == 'SYST:MODU?' or cmd in self.masks:
            self.cmds.append(cmd)
            if not self.remote:
                raise AssertionError('R4 diagnostic bypassed Remote')
            return self.bad_reply if self.bad_reply is not None else (
                self.module if cmd == 'SYST:MODU?' else self.masks[cmd])
        return super().query(cmd)


class StatusReadOnlyTests(unittest.TestCase):
    def test_module_and_enable_masks_exact_order_without_event_reset(self):
        wire = ReadOnlyTransport()
        psu = Keithley2231A(wire)
        self.assertEqual(psu.module_info(), '2231,1.04')
        for name, value in [('standard_event', 33), ('service_request', 128),
                            ('questionable', 17), ('operation', 255)]:
            self.assertEqual(psu.status_enable_mask(name), value)
        self.assertEqual(wire.cmds, [
            '*IDN?', 'SYST:REM', 'SYST:MODU?', '*ESE?', '*SRE?',
            'STAT:QUES:ENAB?', 'STAT:OPER:ENAB?'])
        self.assertFalse(psu.armed)
        self.assertFalse(psu.faulted)
        self.assertNotIn('OUTP', ' '.join(wire.cmds))
        self.assertTrue(all(':EVEN' not in x and x != '*ESR?' and x != '*CLS'
                            for x in wire.cmds))

    def test_invalid_register_rejected_without_any_scpi(self):
        wire = ReadOnlyTransport()
        psu = Keithley2231A(wire)
        for name in ('*ESR?', 'operation:EVEN', 'STAT:QUES:ENAB?', 'module',
                     '', 'Operation', None, 5, True, ('operation',), 'operation\n*CLS'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                psu.status_enable_mask(name)
        self.assertEqual(wire.cmds, [])

    def test_module_revision_is_not_device_identity(self):
        wire = ReadOnlyTransport()
        wire.module = '2231,0.01'
        psu = Keithley2231A(wire, expected_serial='TEST')
        self.assertEqual(psu.module_info(), '2231,0.01')
        self.assertIn('2231A-30-3', psu.instrument_id)
        self.assertEqual(psu.instrument_key[2], 'TEST')
        self.assertEqual(wire.cmds.count('*IDN?'), 1)

    def test_module_query_does_not_change_on_or_profile(self):
        wire = ReadOnlyTransport()
        # Pretend a channel is physically ON: this read must not change it.
        wire.values[1]['CHAN:OUTP'] = 1
        psu = Keithley2231A(wire)
        self.assertEqual(psu.module_info(), '2231,1.04')
        self.assertEqual(wire.values[1]['CHAN:OUTP'], 1)
        self.assertFalse(any(c.startswith('OUTP ') or c.startswith('CHAN:OUTP ')
                             for c in wire.cmds))

    def test_malformed_module_latches_unknown_without_next_command(self):
        for response in ('', ' \t ', '2231\t1.04', 'X' * 129):
            with self.subTest(response=response):
                wire = ReadOnlyTransport()
                wire.module = response
                psu = Keithley2231A(wire)
                with self.assertRaises(InstrumentError):
                    psu.module_info()
                trace = wire.cmds[:]
                with self.assertRaises(SafetyError):
                    psu.module_info()
                self.assertEqual(wire.cmds, trace)
                self.assertTrue(psu.faulted)

    def test_bad_numeric_mask_is_terminal_no_retry(self):
        for response in ('NaN', '1.0', '-1', '256', '00100.0', '', '1e2', '99999'):
            with self.subTest(response=response):
                wire = ReadOnlyTransport()
                wire.bad_reply = response
                psu = Keithley2231A(wire)
                with self.assertRaises(InstrumentError):
                    psu.status_enable_mask('operation')
                trace = wire.cmds[:]
                with self.assertRaises(SafetyError):
                    psu.status_enable_mask('standard_event')
                self.assertEqual(wire.cmds, trace)

    def test_transport_failure_does_not_send_additional_commands(self):
        wire = ReadOnlyTransport()
        wire.failed_query = 'SYST:MODU?'
        psu = Keithley2231A(wire)
        # Simulate actual I/O break on an otherwise valid transport.
        orig = wire.query
        def fail(cmd):
            if cmd == 'SYST:MODU?':
                wire.cmds.append(cmd)
                raise OSError('lost reply')
            return orig(cmd)
        wire.query = fail
        with self.assertRaises(OSError):
            psu.module_info()
        trace = wire.cmds[:]
        with self.assertRaises(SafetyError):
            psu.module_info()
        self.assertEqual(wire.cmds, trace)

    def test_fresh_session_no_accidental_status_clear(self):
        wire = ReadOnlyTransport()
        psu = Keithley2231A(wire)
        self.assertEqual(psu.status_enable_mask('questionable'), 17)
        self.assertEqual(wire.cmds, ['*IDN?', 'SYST:REM', 'STAT:QUES:ENAB?'])
        self.assertNotIn('SYST:ERR?', wire.cmds)

    def test_faulted_session_rejects_even_when_remote(self):
        wire = ReadOnlyTransport()
        psu = Keithley2231A(wire)
        self.assertEqual(psu.module_info(), '2231,1.04')
        psu._latch_fault()  # model an external semantic fault, no new SCPI
        trace = wire.cmds[:]
        with self.assertRaises(SafetyError):
            psu.status_enable_mask('operation')
        self.assertEqual(wire.cmds, trace)


if __name__ == '__main__':
    unittest.main()
