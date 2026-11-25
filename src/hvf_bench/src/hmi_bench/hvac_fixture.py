"""HVAC startup data selected by the local vehicle profile."""
from .fixtures import element


RESOURCES = ('airOutlets', 'rows', 'zones', 'valueIndications', 'airQualities',
    'views', 'switchControls', 'fieldControls', 'parkheaterSettings',
    'intelligentFunctions', 'switchIndications', 'airQualityChartIndications',
    'macroFunctions', 'voiceControlDialogues', 'seats', 'generalSettings',
    'valueControls', 'fieldIndications')


def objects():
    """Front climate controls backed by the original writable RSI resources.

    References contain only id/name/uri so rows/zones never create JSON cycles.
    References are shared by the desktop and its temperature/seat overlays.
    """
    from .car_hvac import objects as captured_objects
    captured = captured_objects()
    if captured is not None:
        return captured

    values = {name: [] for name in RESOURCES}

    def add(resource, name, **fields):
        value = element('hvac', resource, 'Bench ' + name, **fields)
        values[resource].append(value)
        return value

    def ref(value):
        return {key: value[key] for key in ('id', 'name', 'uri')}

    def switch(name, state='on', configuration=('on', 'off')):
        return add('switchControls', name, switchValue=state,
                   switchValueConfiguration=list(configuration))

    def level(name, current, minimum=1.0, maximum=3.0, resolution=1.0, **fields):
        return add('valueControls', name, currentValue=current, minValue=minimum,
                   maxValue=maximum, resolution=resolution, **fields)

    activation = switch('front row activation')
    ac = switch('AC')
    sync = switch('sync', 'off')
    auto = switch('automatic mode')
    distribution = {key: ref(switch(key, state)) for key, state in (
        ('airDistributionBody', 'on'), ('airDistributionFootwell', 'off'),
        ('airDistributionUp', 'off'))}
    style = switch('automatic intensity', 'medium', ('weak', 'medium', 'strong'))
    row = add('rows', 'front row', location='frontRow', systemActivation=ref(activation))
    zones = []
    for side, temperature in (('leftSide', 22.0), ('rightSide', 23.0)):
        control = add('valueControls', side + ' setpoint', currentValue=temperature,
            minValue=16.0, maxValue=29.5, resolution=0.5, unitTemperature='C',
            specialStateConfiguration=['low', 'high'])
        state = add('switchIndications', side + ' climatisation state',
            switchValue='cooling', switchValueConfiguration=['heating', 'venting', 'cooling', 'unknown'])
        eco = add('switchIndications', side + ' eco state',
            switchValue='off', switchValueConfiguration=['on', 'off'])
        zone = add('zones', side, location=side, memberOfRow=ref(row),
            temperature=ref(control), climatisationState=ref(state), ecoModeState=ref(eco))
        zone.update(distribution, climatisationStyle=ref(style))
        seat = add('seats', side + ' seat', memberOfZone=ref(zone),
            seatHeatingState=ref(switch(side + ' seat heating', 'off')),
            seatHeatingLevel=ref(level(side + ' seat heating level', 1.0,
                specialState='off', specialStateConfiguration=['on', 'off'])),
            seatVentilationState=ref(switch(side + ' seat ventilation', 'off')),
            seatVentilationLevel=ref(level(side + ' seat ventilation level', 1.0,
                specialState='off', specialStateConfiguration=['on', 'off'])))
        zone['seatClimatisationSetup'] = ref(seat)
        zones.append(zone)
    fan = add('valueControls', 'fan level', currentValue=3.0, minValue=1.0,
        maxValue=7.0, resolution=1.0, specialStateConfiguration=['off'])
    full = add('zones', 'full row', location='fullRow', memberOfRow=ref(row),
        autoMode=ref(auto), autoModeAirVolume=ref(auto), autoModeAirDistribution=ref(auto),
        airVolumeManual=ref(fan), airVolumeRegulated=ref(fan),
        climatisationStyle=ref(style), **distribution)
    intelligent = add('intelligentFunctions', 'front intelligent functions', **{
        key: ref(switch(key, 'off')) for key in ('defogging', 'fastCooling',
        'fastHeating', 'feetCooling', 'feetHeating', 'freshAir', 'handsHeating',
        'pollutionProtection', 'standardAuto')})
    full['intelligentFunctions'] = ref(intelligent)
    zones.append(full)
    row['zones'] = [ref(zone) for zone in zones]
    # The stock client publishes these attributes per row even when visiting
    # left/right zones. Missing fields there would overwrite the full-row data.
    for zone in zones:
        zone.update(autoMode=ref(auto), autoModeAirVolume=ref(auto),
                    autoModeAirDistribution=ref(auto), airVolumeManual=ref(fan),
                    airVolumeRegulated=ref(fan))
    add('generalSettings', 'general settings', aC=ref(ac), sync=ref(sync), **{
        key: ref(switch(key, 'off')) for key in ('aCMax', 'airCirculationAutomatic',
        'airCirculationManual', 'frontWindowHeater', 'frontWindowHeaterAutomatic',
        'rearWindowHeater', 'defrost', 'maxDefrost', 'airCleaningState',
        'airCleaningIonisation')})
    return values
