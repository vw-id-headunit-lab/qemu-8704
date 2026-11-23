"""Stationary vehicle signals consumed by the original VehicleDynamics client."""
from .fixtures import element


IGNITION_SIGNALS = ('keyContact', 'ignition', 'comfortReadyState')


def objects():
    return [element('vehicledynamics', 'vehicleStates', name,
                    switchValue='ON' if name == 'parkBrake' else 'OFF')
            for name in (*IGNITION_SIGNALS, 'drivingReady', 'parkBrake',
                         'productionMode', 'rvcState', 'transportMode')]


def control_properties():
    rows = {row['name']: row['uri'] for row in objects()}
    rows['power'] = power_objects()[0]['uri']
    return ''.join('%s=%s\n' % (name, rows[name]) for name in (*IGNITION_SIGNALS, 'power')).encode('ascii')


def power_objects():
    return [element('vehicledynamics', 'powermanagement', 'Bench power',
                    batterymanagments='noLimitation', powerstates='standby')]
