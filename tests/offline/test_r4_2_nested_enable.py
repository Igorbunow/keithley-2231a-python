"""R4.2-C: non-destructive nested enable-mask queries for one Keithley (fake only)."""
from __future__ import annotations
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError
from test_driver import FakeTransport, profile


class NestedEnableWire(FakeTransport):
    def __init__(self):
        super().__init__()
        self.masks = {}
        for prefix, offset in (('STAT:QUES:INST', 0), ('STAT:OPER:INST', 100)):
            self.masks[f'{prefix}:ENAB?'] = str(offset + 11)
            for ch in (1, 2, 3):
                self.masks[f'{prefix}:ISUM{ch}:ENAB?'] = str(offset + ch + 11)
        self.events = {'STAT:QUES:INST:EVEN?': '125',
                       'STAT:OPER:INST:EVEN?': '200'}
        self.bad_reply = None
        self.lost_reply = None

    def query(self, command):
        if command in self.masks:
            self.cmds.append(command)
            if not self.remote:
                raise AssertionError('nested diagnostic bypassed Remote')
            if command == self.lost_reply:
                raise OSError('synthetic reply lost after read')
            return self.bad_reply if self.bad_reply is not None else self.masks[command]
        return super().query(command)


class NestedEnableTests(unittest.TestCase):
    def test_all_eight_documented_masks_in_order_and_channel_unchanged(self):
        wire = NestedEnableWire()
        wire.active_ch = 2
        device = Keithley2231A(wire)
        expected = []
        for group, base in (('questionable', 'STAT:QUES:INST'),
                            ('operation', 'STAT:OPER:INST')):
            for ch in (None, 1, 2, 3):
                command = f'{base}:ENAB?' if ch is None else f'{base}:ISUM{ch}:ENAB?'
                expected.append(command)
                self.assertEqual(device.status_instrument_enable_mask(group, channel=ch),
                                 int(wire.masks[command]))
        self.assertEqual(wire.cmds, ['*IDN?', 'SYST:REM', *expected])
        self.assertEqual(wire.active_ch, 2)
        self.assertEqual(wire.events['STAT:QUES:INST:EVEN?'], '125')
        self.assertFalse(device.faulted)

    def test_exact_whitelists_reject_all_invalid_before_scpi(self):
        for group in ('', None, '*ESR?', 'STAT:QUES:INST:ENAB?', 'Questionable',
                      'operation;*CLS', 0, True, ('operation',)):
            with self.subTest(group=group):
                wire = NestedEnableWire()
                with self.assertRaises(ValueError):
                    Keithley2231A(wire).status_instrument_enable_mask(group)
                self.assertEqual(wire.cmds, [])
        for ch in (0, 4, -1, 1., True, False, '1', '', [],
                   (1,), '1;OUTP 1'):
            with self.subTest(channel=ch):
                wire = NestedEnableWire()
                with self.assertRaises(ValueError):
                    Keithley2231A(wire).status_instrument_enable_mask('operation', channel=ch)
                self.assertEqual(wire.cmds, [])

    def test_no_explicit_event_ack_or_output_check_needed_for_mask(self):
        wire = NestedEnableWire()
        wire.values[3]['CHAN:OUTP'] = 1
        device = Keithley2231A(wire)
        self.assertEqual(device.status_instrument_enable_mask('questionable', channel=3), 14)
        self.assertEqual(wire.values[3]['CHAN:OUTP'], 1)
        self.assertFalse(any(x in ' '.join(wire.cmds) for x in
                             ('CHAN:OUTP?', 'OUTP 0', 'OUTP 1', ':EVEN?', '*ESR?', '*CLS')))

    def test_no_change_of_live_profile_or_channel(self):
        wire = NestedEnableWire()
        device = Keithley2231A(wire)
        device.apply_profile(profile(), confirmed_wiring='independent')
        wire.active_ch = 3
        pre = list(wire.cmds)
        self.assertTrue(device.armed)
        self.assertEqual(device.status_instrument_enable_mask('operation', channel=1), 112)
        self.assertEqual(wire.cmds[len(pre):], ['STAT:OPER:INST:ISUM1:ENAB?'])
        self.assertEqual(wire.active_ch, 3)
        self.assertTrue(device.armed)
        self.assertFalse(device.faulted)

    def test_malformed_reply_latches_uncertain_and_forbids_retry(self):
        for reply in ('NaN', '-1', '256', '1e2', '1.0', '', 'true', ' 0x10 ', '∞', '99999'):
            with self.subTest(reply=reply):
                wire = NestedEnableWire()
                wire.bad_reply = reply
                device = Keithley2231A(wire)
                with self.assertRaises(InstrumentError):
                    device.status_instrument_enable_mask('operation', channel=2)
                self.assertTrue(device.faulted)
                self.assertEqual(wire.cmds.count('STAT:OPER:INST:ISUM2:ENAB?'), 1)
                previous = list(wire.cmds)
                with self.assertRaises(SafetyError):
                    device.status_instrument_enable_mask('questionable')
                self.assertEqual(wire.cmds, previous)

    def test_lost_reply_no_retry_and_no_recovery_on_same_link(self):
        wire = NestedEnableWire()
        wire.lost_reply = 'STAT:QUES:INST:ENAB?'
        device = Keithley2231A(wire)
        with self.assertRaises(OSError):
            device.status_instrument_enable_mask('questionable')
        self.assertTrue(device.faulted)
        previous = list(wire.cmds)
        with self.assertRaises(SafetyError):
            device.status_instrument_enable_mask('operation', channel=3)
        self.assertEqual(wire.cmds, previous)

    def test_already_faulted_refuses_without_io(self):
        wire = NestedEnableWire()
        device = Keithley2231A(wire)
        device._latch_fault(link_uncertain=True)
        with self.assertRaises(SafetyError):
            device.status_instrument_enable_mask('questionable', channel=1)
        self.assertEqual(wire.cmds, [])

    def test_no_enable_writes_on_any_query(self):
        wire = NestedEnableWire()
        device = Keithley2231A(wire)
        for group in ('questionable', 'operation'):
            for ch in (None, 1, 2, 3):
                device.status_instrument_enable_mask(group, channel=ch)
        self.assertTrue(all(c in ('*IDN?', 'SYST:REM') or c.endswith('?')
                            for c in wire.cmds))
        self.assertFalse(any(':EVEN' in c or c == '*ESR?' for c in wire.cmds))


if __name__ == '__main__':
    unittest.main()
