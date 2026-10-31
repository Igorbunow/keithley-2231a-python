"""R5-E3 guarded trigger staging over production driver + POSIX virtual UART."""
import os
import unittest
from pathlib import Path
from keithley2231a import Keithley2231A, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'PTY required')
class TriggerStagingUART(unittest.TestCase):
    def setup_pair(self, **opts):
        peer = UARTPeer(profile_registers=True, trigger_registers=True, **opts)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.16)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport, debug=True)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        return dev, peer

    def test_three_channels_mutate_only_selected_trigger_registers_not_active_presets(self):
        dev, peer = self.setup_pair()
        before = dict(dev._expected_setpoints)
        for ch in (1, 2, 3):
            v, i = ch * .5, ch * .025
            result = dev.stage_trigger_presets_off(ch, v, i, confirmed_off_only=True)
            self.assertEqual(result.channel, ch)
            self.assertAlmostEqual(result.voltage_v, v)
            self.assertAlmostEqual(result.current_a, i)
            self.assertEqual(peer.channel_registers[ch]['VOLT:TRIG'], f'{v:.3f}')
            self.assertEqual(peer.channel_registers[ch]['CURR:TRIG'], f'{i:.3f}')
        self.assertEqual(before, dev._expected_setpoints)
        self.assertEqual(peer.rx.count('VOLT:TRIG?'), 3)
        self.assertEqual(peer.rx.count('CURR:TRIG?'), 3)
        self.assertFalse(any(x in ('*TRG','TRIG','OUTP 1','CHAN:OUTP 1') or
                             x.startswith('INST:COUP') for x in peer.rx))
        self.assertEqual(peer.unsupported, [])

    def test_output_enable_denied_after_stage_even_after_reprofile(self):
        dev, peer = self.setup_pair()
        dev.stage_trigger_presets_off(1, 1., .050, confirmed_off_only=True)
        with self.assertRaises(SafetyError): dev.output(1, True)
        self.assertNotIn('CHAN:OUTP 1',peer.rx)
        dev.emergency_off()
        dev.apply_profile(profile(),confirmed_wiring='independent')
        with self.assertRaises(SafetyError): dev.output(3, True)
        self.assertNotIn('CHAN:OUTP 1',peer.rx)

    def test_invalid_inputs_rejected_before_any_wire(self):
        dev, peer = self.setup_pair()
        before = list(peer.rx)
        for ch,v,i in ((0,1.,.05),(4,1.,.05),(True,1.,.05),
                       (1,float('nan'),.05),(1,1.001,.05),(1,1.,.0015),
                       (1,99.,.05),(3,3.31,.05),(1,1.,True),(1,1.,-.1)):
            with self.subTest(ch=ch,v=v,i=i), self.assertRaises((ValueError,SafetyError)):
                dev.stage_trigger_presets_off(ch,v,i,confirmed_off_only=True)
        with self.assertRaises(SafetyError): dev.stage_trigger_presets_off(1,1.,.05)
        self.assertEqual(before,peer.rx)

    def test_powered_rejected_no_trigger_writes(self):
        dev,peer=self.setup_pair()
        dev.output(1,True)
        with self.assertRaises(SafetyError):
            dev.stage_trigger_presets_off(1,1.,.05,confirmed_off_only=True)
        self.assertFalse(any(x.startswith('VOLT:TRIG ') for x in peer.rx))

    def test_wrong_selected_channel_rejected_without_staging(self):
        dev,peer=self.setup_pair()
        peer.overrides['INST:NSEL?']='1'
        with self.assertRaises(SafetyError):
            dev.stage_trigger_presets_off(2,1.,.05,confirmed_off_only=True)
        self.assertNotIn('VOLT:TRIG 1.000',peer.rx)

    def test_scpi_error_after_first_write_stops_second(self):
        dev,peer=self.setup_pair(fault_after='VOLT:TRIG 1.000')
        with self.assertRaises(Exception):
            dev.stage_trigger_presets_off(1,1.,.05,confirmed_off_only=True)
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('VOLT:TRIG 1.000'),1)
        self.assertNotIn('CURR:TRIG 0.050',peer.rx)

    def test_mismatched_readback_stops_second_write(self):
        dev,peer=self.setup_pair(response_overrides={'VOLT:TRIG?':'0.25'})
        with self.assertRaises(Exception):
            dev.stage_trigger_presets_off(1,1.,.05,confirmed_off_only=True)
        self.assertTrue(dev.faulted)
        self.assertNotIn('CURR:TRIG 0.050',peer.rx)

    def test_timeout_blocks_repeat_and_auto_shutdown_on_unknown_link(self):
        dev,peer=self.setup_pair(drop='VOLT:TRIG?')
        n_off=peer.rx.count('OUTP 0')
        with self.assertRaises(Exception):
            dev.stage_trigger_presets_off(1,1.,.05,confirmed_off_only=True)
        self.assertTrue(dev._link_uncertain)
        self.assertEqual(peer.rx.count('VOLT:TRIG?'),1)
        self.assertEqual(peer.rx.count('OUTP 0'),n_off)
        self.assertNotIn('CURR:TRIG 0.050',peer.rx)

    def test_diagnostic_writes_and_trigger_event_still_forbidden(self):
        dev,peer=self.setup_pair()
        dev.debug_bypass_profile=True
        before=list(peer.rx)
        for command in ('*TRG','TRIG','INST:COUP CH1.CH2','VOLT:TRIG 1.000',
                        'CURR:TRIG 0.050','SOUR:VOLT:TRIG 1.000'):
            with self.subTest(command=command),self.assertRaises(SafetyError):
                dev.diagnostic_write(command)
        self.assertEqual(before,peer.rx)

    def test_readback_still_works_after_off_only_staging(self):
        dev,peer=self.setup_pair()
        dev.stage_trigger_presets_off(3,1.5,.05,confirmed_off_only=True)
        result=dev.read_trigger_presets(3)
        self.assertAlmostEqual(result.voltage_v,1.5)
        self.assertAlmostEqual(result.current_a,.05)
        self.assertTrue(dev._trigger_staged_unqualified)

    def test_capabilities_exact_review_scope(self):
        caps=Keithley2231A.capabilities()
        read=caps.feature('trigger_preset_readback')
        staged=caps.feature('trigger_preset_off_staging')
        self.assertEqual(read.evidence,'bounded_hw')
        self.assertEqual(read.hardware_evidence[0].channels,(1,2,3))
        self.assertTrue((Path(__file__).resolve().parents[2] /
                         read.hardware_evidence[0].reference).is_file())
        # R5-E5 updates this assertion after independent R5-E4 OFF-only HW review.
        self.assertEqual(staged.evidence,'bounded_hw')
        self.assertEqual(staged.hardware_evidence[0].channels,(1,2,3))
        self.assertEqual(staged.hardware_evidence[0].output_state,'OFF')
        self.assertTrue((Path(__file__).resolve().parents[2] /
                         staged.hardware_evidence[0].reference).is_file())
        self.assertFalse(caps.feature('hardware_triggered_setpoint').driver_exposed)

if __name__ == '__main__': unittest.main()
