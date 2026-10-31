"""Static, read-only 2231A-30-3 capabilities; no SCPI I/O or bench logic.

Instrument-native capability is distinct from library exposure and qualification.
This snapshot describes a model, not the live instrument's current state.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AccuracySpec:
    """Nominal +/- (fraction_of_reading * |reading| + fixed_floor).

    The specification applies only under stated environmental/calibration
    conditions. This is NOT an error bound verified for a connected device.
    """
    fraction_of_reading: float
    fixed_floor: float
    unit: str
    nominal_temperature_c: tuple[float, float]

    def nominal_absolute_bound(self, reading: float) -> float:
        """Calculate a published nominal bound; do not certify measurements."""
        import math
        if isinstance(reading, bool) or not isinstance(reading, (int, float)) or not math.isfinite(reading):
            raise ValueError("reading must be finite number")
        return self.fraction_of_reading * abs(reading) + self.fixed_floor

    def to_dict(self) -> dict:
        return {
            "fraction_of_reading": self.fraction_of_reading,
            "fixed_floor": self.fixed_floor,
            "unit": self.unit,
            "nominal_temperature_c": list(self.nominal_temperature_c),
            "calibrated_here": False,
        }


@dataclass(frozen=True)
class ChannelCapability:
    number: int
    rated_voltage_v: float
    rated_current_a: float
    voltage_setting_step_v: float
    current_setting_step_a: float

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "rated_voltage_v": self.rated_voltage_v,
            "rated_current_a": self.rated_current_a,
            "voltage_setting_step_v": self.voltage_setting_step_v,
            "current_setting_step_a": self.current_setting_step_a,
        }


@dataclass(frozen=True)
class HardwareEvidence:
    """One bounded historical observation, not live device authorization.

    An empty collection on a feature means that detailed per-channel evidence
    has not been modeled here; it does NOT negate older recorded observations.
    """
    channels: tuple[int, ...]
    operation: str
    output_state: str  # OFF | POWERED
    regulation: str | None  # CV | CC | None
    observation: str  # digital_readback | analog_steady_state
    reference: str

    def __post_init__(self) -> None:
        if (not self.channels or any(type(ch) is not int or ch not in (1, 2, 3)
                                     for ch in self.channels) or
                len(set(self.channels)) != len(self.channels)):
            raise ValueError('hardware evidence must name distinct valid channels')
        if self.output_state not in ('OFF', 'POWERED'):
            raise ValueError('invalid output state in evidence')
        if self.regulation not in (None, 'CV', 'CC'):
            raise ValueError('invalid regulation in evidence')
        if self.output_state == 'OFF' and self.regulation is not None:
            raise ValueError('OFF-only evidence cannot assert CV/CC regulation')
        if self.observation not in ('digital_readback', 'analog_steady_state'):
            raise ValueError('invalid hardware evidence observation')
        if not self.operation or not self.reference:
            raise ValueError('evidence needs operation and provenance')

    def to_dict(self) -> dict:
        return {
            'channels': list(self.channels),
            'operation': self.operation,
            'output_state': self.output_state,
            'regulation': self.regulation,
            'observation': self.observation,
            'reference': self.reference,
        }


@dataclass(frozen=True)
class InstrumentFeature:
    """Distinguish hardware, guarded driver API and evidence scope.

    evidence is NOT a firmware-agnostic certification. 'bounded_hw' denotes
    a historically reviewed scenario only, not every command/operating point.
    """
    name: str
    instrument_native: bool
    driver_exposed: bool
    evidence: str  # bounded_hw | simulated_only | documented_only | absent
    constraints: tuple[str, ...] = ()
    hardware_evidence: tuple[HardwareEvidence, ...] = ()

    def __post_init__(self) -> None:
        if self.hardware_evidence and (self.evidence != 'bounded_hw' or
                                       not self.instrument_native or
                                       not self.driver_exposed):
            raise ValueError('bounded instrument evidence requires exposed native feature')

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "instrument_native": self.instrument_native,
            "driver_exposed": self.driver_exposed,
            "evidence": self.evidence,
            "constraints": list(self.constraints),
            "hardware_evidence": [e.to_dict() for e in self.hardware_evidence],
        }


@dataclass(frozen=True)
class InstrumentCapabilities:
    model: str
    schema_version: int
    channels: tuple[ChannelCapability, ...]
    features: tuple[InstrumentFeature, ...]
    voltage_readback_nominal_accuracy: AccuracySpec
    current_readback_nominal_accuracy: AccuracySpec
    readback_current_nominal_resolution_a: float
    notes: tuple[str, ...]

    def channel(self, number: int) -> ChannelCapability:
        if type(number) is not int:
            raise ValueError("channel number must be integer")
        for channel in self.channels:
            if channel.number == number:
                return channel
        raise ValueError(f"no such channel: {number}")

    def feature(self, name: str) -> InstrumentFeature:
        if not isinstance(name, str):
            raise ValueError("feature name must be a string")
        for feature in self.features:
            if feature.name == name:
                return feature
        raise ValueError(f"unknown capability: {name}")

    def to_dict(self) -> dict:
        """Fresh JSON-compatible copy; caller mutations cannot alter snapshot."""
        return {
            "schema_version": self.schema_version,
            "model": self.model,
            "channels": [x.to_dict() for x in self.channels],
            "features": [x.to_dict() for x in self.features],
            "readback": {
                "voltage_nominal_accuracy": self.voltage_readback_nominal_accuracy.to_dict(),
                "current_nominal_accuracy": self.current_readback_nominal_accuracy.to_dict(),
                "current_nominal_resolution_a": self.readback_current_nominal_resolution_a,
                "readings_atomic": False,
            },
            "notes": list(self.notes),
        }


KEITHLEY_2231A_CAPABILITIES = InstrumentCapabilities(
    model="Keithley 2231A-30-3",
    schema_version=2,
    channels=(
        ChannelCapability(1, 30.0, 3.0, 0.01, 0.001),
        ChannelCapability(2, 30.0, 3.0, 0.01, 0.001),
        ChannelCapability(3, 5.0, 3.0, 0.01, 0.001),
    ),
    features=(
        InstrumentFeature("channel_select", True, True, "bounded_hw"),
        InstrumentFeature("voltage_setpoint", True, True, "bounded_hw", ("armed safety profile",)),
        InstrumentFeature("current_limit_setpoint", True, True, "bounded_hw", ("armed safety profile",)),
        InstrumentFeature("output_enable", True, True, "bounded_hw", ("operator wiring checks", "armed safety profile")),
        InstrumentFeature("voltage_readback", True, True, "bounded_hw", ("sequential sampling",)),
        InstrumentFeature("current_readback", True, True, "bounded_hw", ("not suitable for high-accuracy milliamp I-V",)),
        InstrumentFeature("channel_timer", True, True, "bounded_hw", ("profile and wiring dependent",)),
        InstrumentFeature("tracking", True, True, "bounded_hw", ("CH1/CH2 behavior, bounded scopes",)),
        InstrumentFeature("series", True, True, "bounded_hw", ("physical jumpers required", "no universal CC qualification")),
        InstrumentFeature("parallel", True, True, "bounded_hw", ("physical jumpers required", "branch sharing not verified")),
        InstrumentFeature(
            "source_step_query", True, True, "bounded_hw",
            ("Independent profile; register reads are not a license to step",
             "VOLT:STEP? reviewed on all channels at bounded low-power CV / OFF cases"),
            (HardwareEvidence((1, 2, 3), "voltage_step_readback", "OFF", None,
                              "digital_readback", "docs/reviews/R5_D3_ALL_CHANNEL_STEP_HW_REVIEW_2026-10-31.md"),
             HardwareEvidence((1, 2, 3), "current_step_readback", "OFF", None,
                              "digital_readback", "docs/reviews/R4_5_D_CURRENT_BATCH_HW_REVIEW_2026-10-30.md"))),
        InstrumentFeature(
            "source_step_off_configuration", True, True, "bounded_hw",
            ("All outputs OFF, independent control profile, strict 10mV/1mA grid",
             "Readback confirmation is digital, not analog qualification"),
            (HardwareEvidence((1, 2, 3), "voltage_step_configuration", "OFF", None,
                              "digital_readback", "docs/reviews/R5_D3_ALL_CHANNEL_STEP_HW_REVIEW_2026-10-31.md"),
             HardwareEvidence((1, 2, 3), "current_step_configuration", "OFF", None,
                              "digital_readback", "docs/reviews/R4_5_D_CURRENT_BATCH_HW_REVIEW_2026-10-30.md"))),
        InstrumentFeature(
            "source_step_off_mutation", True, True, "bounded_hw",
            ("OFF-only UP/DOWN by default", "One verified increment per call",
             "Digital preset changes do not establish analog behavior"),
            (HardwareEvidence((1, 2, 3), "voltage_step_up_down", "OFF", None,
                              "digital_readback", "docs/reviews/R5_D3_ALL_CHANNEL_STEP_HW_REVIEW_2026-10-31.md"),
             HardwareEvidence((1, 2, 3), "current_step_up_down", "OFF", None,
                              "digital_readback", "docs/reviews/R4_5_D_CURRENT_BATCH_HW_REVIEW_2026-10-30.md"))),
        InstrumentFeature(
            "source_step_powered_qualification", True, True, "bounded_hw",
            ("Qualification context only, not general powered STEP API",
             "Low-power powered CV/CC STEP independently reviewed on CH1/CH2/CH3; no full-range guarantee",
             "Selected-channel profile <=3.5V, <=0.15A, <=0.60W; other outputs OFF",
             "External load, operator approval and physical OFF required",
             "CV/CC observations only at reviewed low-power points",
             "Does not qualify general powered STEP APIs, arbitrary transients or calibrated accuracy"),
            (HardwareEvidence((1,), "voltage_step_up_down", "POWERED", "CV",
                              "analog_steady_state", "docs/reviews/R4_5_E_C_CH1_CV_FIVE_POINT_HW_REVIEW_2026-10-30.md"),
             HardwareEvidence((1,), "current_step_up_down", "POWERED", "CC",
                              "analog_steady_state", "docs/reviews/R4_5_E_C_CH1_CC_FIVE_POINT_HW_REVIEW_2026-10-30.md"),
             HardwareEvidence((1, 2, 3), "voltage_step_up_down", "POWERED", "CV",
                              "analog_steady_state", "docs/reviews/R5_D3_ALL_CHANNEL_STEP_HW_REVIEW_2026-10-31.md"),
             HardwareEvidence((1, 2, 3), "current_step_up_down", "POWERED", "CC",
                              "analog_steady_state", "docs/reviews/R5_D3_ALL_CHANNEL_STEP_HW_REVIEW_2026-10-31.md"))),
        InstrumentFeature("trigger_preset_readback", True, True, "bounded_hw",
                          ("CH1/CH2/CH3 OFF-only via independent control profile",
                           "VOLT:TRIG?/CURR:TRIG? observed on all 3 channels, 0 V / 0 A",
                           "Does not authorize trigger writes, coupling, event or output enable"),
                          (HardwareEvidence((1, 2, 3), "trigger_preset_readback", "OFF", None,
                                            "digital_readback",
                                            "docs/reviews/R5_E2_TRIGGER_READ_HW_REVIEW_2026-10-31.md"),)),
        InstrumentFeature("trigger_preset_off_staging", True, True, "bounded_hw",
                          ("CH1/CH2/CH3 independent OFF-only control profile",
                           "VOLT:TRIG/CURR:TRIG write/read/restore observed on all channels",
                           "Not atomic; output enable remains blocked in staging session",
                           "No bus-trigger or analog response qualification"),
                          (HardwareEvidence((1, 2, 3), "trigger_preset_off_staging", "OFF", None,
                                            "digital_readback",
                                            "docs/reviews/R5_E4_TRIGGER_WRITE_HW_REVIEW_2026-10-31.md"),)),
        InstrumentFeature("neutral_trigger_bus_off_candidate", True, True, "simulated_only",
                          ("Exactly one software-tested *TRG after all three trigger presets match active settings",
                           "All CH1/CH2/CH3 OFF; independent profile, no terminals connected",
                           "One-shot guarded dispatch, no automatic retry or coupling assumption",
                           "NO physical event tested; no setpoint switching or analog/timing qualification")),
        InstrumentFeature("hardware_triggered_setpoint", True, False, "documented_only",
                          ("VOLT:TRIG/CURR:TRIG OFF-only staging is separately qualified",
                           "*TRG, coupling, timing and actual output mutation NOT qualified")),
        InstrumentFeature("trigger_channel_coupling", True, False, "documented_only",
                          ("INST:COUP; not equivalent to output tracking",
                           "No verified synchronized switching or timing guarantee")),
        InstrumentFeature("bulk_apply_setpoints", True, False, "documented_only",
                          ("APPLy; no proven atomicity or faster safe behavior",
                           "No bypass of per-channel profiles")),
        InstrumentFeature("enable_channels_function", True, False, "documented_only",
                          ("OUTP:ENAB has different menu semantics from output_enable",
                           "High risk; do not expose or infer query form")),
        InstrumentFeature("native_iv_sweep", False, False, "absent", ("host-side example only",)),
        InstrumentFeature("atomic_voltage_current_sample", False, False, "absent", ("readings are sequential",)),
        InstrumentFeature("native_ethernet", False, False, "absent", ("serial UART; TCP requires external bridge",)),
        InstrumentFeature("safe_off_on_link_loss", False, False, "absent", ("external hardware interlock required",)),
    ),
    voltage_readback_nominal_accuracy=AccuracySpec(0.0006, 0.020, "V", (20.0, 30.0)),
    current_readback_nominal_accuracy=AccuracySpec(0.002, 0.010, "A", (20.0, 30.0)),
    readback_current_nominal_resolution_a=0.001,
    notes=(
        "Static model metadata: no SCPI, connection, or live-state validation.",
        "Readback specification is conditional; displayed digits are not guaranteed accuracy.",
        "Evidence labels are bounded to recorded scenarios, wiring and firmware.",
        "hardware_evidence is illustrative and non-exhaustive; no scope grants power control.",
        "Native operation is not an authorization to enable output or to bypass profiles.",
        "A controller must recheck live topology, profile and physical OFF gates.",
        "Readings are not simultaneous; no calibrated I-V assertion is made.",
        "Observed Aux firmware revision may fluctuate; this snapshot does not certify it.",
    ),
)


def get_capabilities() -> InstrumentCapabilities:
    """Retrieve static metadata without constructing or talking to a device."""
    return KEITHLEY_2231A_CAPABILITIES
