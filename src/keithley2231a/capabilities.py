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

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "instrument_native": self.instrument_native,
            "driver_exposed": self.driver_exposed,
            "evidence": self.evidence,
            "constraints": list(self.constraints),
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
    schema_version=1,
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
        InstrumentFeature("source_step_query", True, True, "simulated_only", ("not qualified for arbitrary firmware",)),
        InstrumentFeature("hardware_triggered_setpoint", True, False, "documented_only", ("unsafe unqualified output mutation",)),
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
        "Native operation is not an authorization to enable output or to bypass profiles.",
        "A controller must recheck live topology, profile and physical OFF gates.",
        "Readings are not simultaneous; no calibrated I-V assertion is made.",
        "Observed Aux firmware revision may fluctuate; this snapshot does not certify it.",
    ),
)


def get_capabilities() -> InstrumentCapabilities:
    """Retrieve static metadata without constructing or talking to a device."""
    return KEITHLEY_2231A_CAPABILITIES
