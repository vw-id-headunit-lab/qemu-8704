from .fixtures import element


def objects():
    return {
        'accessPointManagers': [element('wifi', 'accessPointManagers', 'Virtual ID3 hotspot',
            event='none', numberOfConnectedClients=0, operatingFrequency='dualband',
            passphrase='MebBench_8704', qrCode='', security='wpa2', ssid='MEB-ID3-Bench',
            state='off', stateConfiguration='off', wpsEvent='none', wpsMode='off',
            wpsPin='', wpsRemainingTime=0)],
        'clientManagers': [element('wifi', 'clientManagers', 'Virtual ID3 WLAN client',
            event='clientModeOff', scanningStrategy='startup', state='off',
            stateConfiguration='off', wpsEvent='none', wpsMode='off', wpsPin='', wpsRemainingTime=0)],
        'clients': [],
        'networks': [],
    }
