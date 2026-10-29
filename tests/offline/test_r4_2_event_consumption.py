"""R4.2-B: explicit, potentially read-to-clear status event access; fake wire only."""
from __future__ import annotations
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError
from test_driver import FakeTransport, profile


class EventWire(FakeTransport):
    queries = ('*ESR?', 'STAT:QUES:EVEN?', 'STAT:OPER:EVEN?')

    def __init__(self):
        super().__init__()
        self.events = dict(zip(self.queries, ('7', '129', '255')))
        self.bad_response = None
        self.event_reads = []

    def query(self, cmd):
        if cmd in self.events:
            self.cmds.append(cmd)
            if not self.remote:
                raise AssertionError('Event query was sent outside Remote')
            self.event_reads.append(cmd)
            result = self.events[cmd]
            self.events[cmd] = '0'  # Synthetic latch is consumed on query.
            return self.bad_response if self.bad_response is not None else result
        return super().query(cmd)


class EventConsumptionTests(unittest.TestCase):
    def test_one_explicit_query_for_each_register_and_clear_only_selected(self):
        wire = EventWire()
        dev = Keithley2231A(wire)
        for name, cmd, value in (
                ('standard_event', '*ESR?', 7),
                ('questionable', 'STAT:QUES:EVEN?', 129),
                ('operation', 'STAT:OPER:EVEN?', 255)):
            before = list(wire.event_reads)
            self.assertEqual(dev.consume_status_event(name, acknowledge_clear=True), value)
            self.assertEqual(wire.event_reads, before + [cmd])
            self.assertFalse(any(wire.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertEqual(wire.active_ch, 1)
        self.assertEqual(wire.event_reads, list(EventWire.queries))
        self.assertEqual(wire.events, {x: '0' for x in wire.events})
        self.assertFalse(dev.faulted)
        self.assertNotIn('*CLS', wire.cmds)
        self.assertNotIn('STAT:PRES', wire.cmds)
        self.assertNotIn('OUTP 0', wire.cmds)

    def test_negative_register_name_and_ack_refused_before_any_io(self):
        for name in ('*ESR?', 'operation:EVEN', 'STAT:OPER:EVEN?',
                     'questionable;*CLS', 'all', '', None, 0, True, ('operation',)):
            with self.subTest(name=name):
                wire = EventWire()
                dev = Keithley2231A(wire)
                with self.assertRaises(ValueError):
                    dev.consume_status_event(name, acknowledge_clear=True)
                self.assertEqual(wire.cmds, [])
        for ack in (None, False, 1, 'True', (), 0):
            with self.subTest(ack=ack):
                wire = EventWire()
                dev = Keithley2231A(wire)
                with self.assertRaises(ValueError):
                    dev.consume_status_event('standard_event', acknowledge_clear=ack)
                self.assertEqual(wire.cmds, [])

    def test_ack_keyword_is_mandatory(self):
        wire = EventWire()
        with self.assertRaises(TypeError):
            Keithley2231A(wire).consume_status_event('standard_event')
        self.assertEqual(wire.cmds, [])

    def test_on_each_channel_refuses_without_eating_event_or_changing_output(self):
        for ch in (1, 2, 3):
            with self.subTest(channel=ch):
                wire = EventWire()
                wire.values[ch]['CHAN:OUTP'] = 1
                dev = Keithley2231A(wire)
                with self.assertRaisesRegex(SafetyError, 'OFF'):
                    dev.consume_status_event('standard_event', acknowledge_clear=True)
                self.assertEqual(wire.events['*ESR?'], '7')
                self.assertEqual(wire.event_reads, [])
                self.assertEqual(wire.values[ch]['CHAN:OUTP'], 1)
                self.assertEqual(wire.active_ch, 1)
                self.assertFalse(dev.faulted)  # Known ON => refusal, not link fault.

    def test_fresh_profile_free_only_and_armed_profile_refused(self):
        wire = EventWire()
        dev = Keithley2231A(wire)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        before = wire.cmds[:]
        with self.assertRaisesRegex(SafetyError, 'profile-free'):
            dev.consume_status_event('questionable', acknowledge_clear=True)
        self.assertEqual(wire.cmds, before)
        self.assertEqual(wire.event_reads, [])
        self.assertTrue(dev.armed)

    def test_faulted_session_refused_without_any_io(self):
        wire = EventWire()
        dev = Keithley2231A(wire)
        dev._latch_fault()
        with self.assertRaisesRegex(SafetyError, 'Faulted'):
            dev.consume_status_event('operation', acknowledge_clear=True)
        self.assertEqual(wire.cmds, [])

    def test_bad_off_preflight_reply_does_not_touch_latch(self):
        wire = EventWire()
        orig = wire.query
        def bad(cmd):
            return 'unknown' if cmd == 'CHAN:OUTP?' else orig(cmd)
        wire.query = bad
        dev = Keithley2231A(wire)
        with self.assertRaises(InstrumentError):
            dev.consume_status_event('standard_event', acknowledge_clear=True)
        self.assertEqual(wire.event_reads, [])
        self.assertEqual(wire.events['*ESR?'], '7')
        self.assertTrue(dev.faulted)
        before = wire.cmds[:]
        with self.assertRaises(SafetyError):
            dev.consume_status_event('standard_event', acknowledge_clear=True)
        self.assertEqual(wire.cmds, before)

    def test_preflight_transport_loss_is_terminal_and_does_not_clear_event(self):
        wire = EventWire()
        original = wire.query
        def fail(cmd):
            if cmd == 'CHAN:OUTP?':
                wire.cmds.append(cmd)
                raise OSError('broken during preflight')
            return original(cmd)
        wire.query = fail
        dev = Keithley2231A(wire)
        with self.assertRaises(OSError):
            dev.consume_status_event('operation', acknowledge_clear=True)
        self.assertTrue(dev.faulted)
        self.assertEqual(wire.event_reads, [])
        self.assertEqual(wire.events['STAT:OPER:EVEN?'], '255')
        trace = wire.cmds[:]
        with self.assertRaises(SafetyError):
            dev.consume_status_event('operation', acknowledge_clear=True)
        self.assertEqual(wire.cmds, trace)

    def test_bad_consumed_event_is_terminal_no_retry(self):
        for reply in ('NaN', '-1', '256', '1.1', '1e2', '', 'True', '99999'):
            with self.subTest(reply=reply):
                wire = EventWire()
                wire.bad_response = reply
                dev = Keithley2231A(wire)
                with self.assertRaises(InstrumentError):
                    dev.consume_status_event('standard_event', acknowledge_clear=True)
                self.assertEqual(wire.event_reads, ['*ESR?'])
                self.assertEqual(wire.events['*ESR?'], '0')  # Gone on wire.
                before = wire.cmds[:]
                with self.assertRaises(SafetyError):
                    dev.consume_status_event('standard_event', acknowledge_clear=True)
                self.assertEqual(wire.cmds, before)

    def test_lost_consumed_reply_cannot_be_retried(self):
        wire = EventWire()
        original = wire.query
        def lose(cmd):
            result = original(cmd)
            if cmd == 'STAT:QUES:EVEN?':
                raise OSError('reply lost after the event register cleared')
            return result
        wire.query = lose
        dev = Keithley2231A(wire)
        with self.assertRaises(OSError):
            dev.consume_status_event('questionable', acknowledge_clear=True)
        self.assertTrue(dev.faulted)
        self.assertEqual(wire.event_reads, ['STAT:QUES:EVEN?'])
        self.assertEqual(wire.events['STAT:QUES:EVEN?'], '0')
        trace = wire.cmds[:]
        with self.assertRaises(SafetyError):
            dev.consume_status_event('questionable', acknowledge_clear=True)
        self.assertEqual(wire.cmds, trace)

    def test_normal_status_mask_read_does_not_consume_events(self):
        # Exercise the previous R4.2-A API using this same synthetic transport.
        wire = EventWire()
        orig = wire.query
        def status(cmd):
            if cmd == '*ESE?':
                wire.cmds.append(cmd)
                return '3'
            return orig(cmd)
        wire.query = status
        dev = Keithley2231A(wire)
        self.assertEqual(dev.status_enable_mask('standard_event'), 3)
        self.assertEqual(wire.event_reads, [])
        self.assertEqual(wire.events['*ESR?'], '7')

    def test_no_extra_status_error_polls_or_clear_after_consuming(self):
        wire = EventWire()
        dev = Keithley2231A(wire)
        self.assertEqual(dev.consume_status_event('questionable', acknowledge_clear=True), 129)
        self.assertEqual(wire.cmds[-1], 'STAT:QUES:EVEN?')
        self.assertNotIn('SYST:ERR?', wire.cmds)
        self.assertNotIn('STAT:OPER:EVEN?', wire.cmds)
        self.assertEqual(wire.events['STAT:OPER:EVEN?'], '255')


if __name__ == '__main__':
    unittest.main()
