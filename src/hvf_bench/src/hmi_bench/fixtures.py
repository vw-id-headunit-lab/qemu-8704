"""Small synthetic external-service data. No shipped JSON/spec is redistributed."""
import json
from uuid import NAMESPACE_URL, uuid5


LANGUAGE_TAGS = {"en_US": "en-us", "zh_CN": "zh-cn"}


def identifier(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "urn:mib3-hmi-bench:" + name))


def element(service: str, resource: str, name: str, **fields) -> dict:
    identity = identifier(service + "/" + resource + "/" + name)
    return {"id": identity, "name": name, "uri": f"/{service}/{resource}/{identity}", **fields}


def language_values(language: str = "en_US") -> dict:
    if not isinstance(language, str) or language not in LANGUAGE_TAGS:
        raise ValueError("Unsupported startup language; choose en_US or zh_CN")
    settings = [element("language", "settings", tag, language=tag, isSDS=False,
                        isVisible=True, voiceType="female") for tag in LANGUAGE_TAGS.values()]
    selected = next(setting for setting in settings if setting["language"] == LANGUAGE_TAGS[language])
    return {
        "language/settings.json": settings,
        "language/requestedSettings.json": [],
        "language/changeTransactions.json": [],
        "language/strategies.json": [element("language", "strategies", "Local language",
                                             main=selected, default=selected, fallback=selected)],
        "language/components.json": [element("language", "components", "onboardUserInterface",
                                             type="graphicalUserInterface", state="idle", currentSetting=selected)],
    }


def payloads(language: str = "en_US") -> dict[str, bytes]:
    user = element("useraccount", "users", "Local guest", authenticationLevel="level0",
                   idpUserId="local-guest", userId="local-guest", userRole="anonymousGuestUser",
                   isLocked=False, isSpinReset=False, securityMode=False, sortOrder=0,
                   tokenStatus="ok", lastSync="2000-01-01T00:00:00.000+00:00")
    values = {
        **language_values(language),
        "useraccount/users.json": [user],
        "useraccount/userContexts.json": [element("useraccount", "userContexts", "vehicle",
            activeUser=user, identifiedUser=user, atneMove=False, cancelAtneMoving=False,
            ffbKeyAllocation="off", functionMasterAvailable=True, isFallBackMode=False,
            isHMIReady=False, multipleUsage=False, physicalSettingsAvailable=False,
            hasPrimary=False, syncAvailable=False, vulSize=1)],
        "useraccount/processes.json": [], "useraccount/qrCodes.json": [], "useraccount/userInfos.json": [],
        "onlineservicedisclaimer/disclaimer.json": [element("onlineservicedisclaimer", "disclaimer",
            "Local offline status", reminderstate=False, result="deny",
            disclaimertext="Local offline bench. Online services are not enabled.")],
        "PermissionManagement/permissions.json": [element("PermissionManagement", "permissions",
            "Local permission state", locationService=False, micService=False)],
        "PermissionManagement/popups.json": [],
        # The original HMI creates its client and entry points through the original
        # mockup POST handlers. Never predeclare the HMI available or invent intents.
        "displaymanagement/displays.json": [element("displaymanagement", "displays", "mainunit",
            controlClient="HMI", width=1920, height=1080)],
        "displayintentcenter/clients.json": [],
        "displayintentcenter/entryPoints.json": [],
        "displayintentcenter/displayIntents.json": [],
    }
    from .hvac_fixture import objects as hvac_objects
    values.update({'hvac/%s.json' % name: rows for name, rows in hvac_objects().items()})
    from .vehicle_fixture import objects as vehicle_objects, power_objects
    values['vehicledynamics/vehicleStates.json'] = vehicle_objects()
    values['vehicledynamics/powermanagement.json'] = power_objects()
    from .wifi_fixture import objects as wifi_objects
    values.update({'wifi/%s.json' % name: rows for name, rows in wifi_objects().items()})
    from .host_clock import time_objects
    values.update(time_objects())
    return {name: (json.dumps(value, indent=2) + "\n").encode() for name, value in values.items()}
