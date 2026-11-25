import hashlib

ORIGINAL_SHA='1a8c929042165ff0b45c416bcb0d0171e68c9bf5ae9d70e248cec76b8a47f62d'
STARTUP_OFFSET=0x21148
STARTUP_GRACE_SECONDS=600

def modified(raw):
 if hashlib.sha256(raw).hexdigest()!=ORIGINAL_SHA:raise ValueError('Original MgrVCI identity differs')
 if hashlib.sha256(raw[0x210a8:0x21174]).hexdigest()!='74c88f7881b49fe6255ef33586e7f954c3cea78b789b67d4867e3fe3ad0c3261':raise ValueError('Original MgrTsk initial wait site differs')
 if raw[STARTUP_OFFSET:STARTUP_OFFSET+4]!=bytes.fromhex('e20180d2'):raise ValueError('Original finite15s initial wait differs')
 if hashlib.sha256(raw[0x2d718:0x2d874]).hexdigest()!='616427d28d3f2e0b0f2e827497acb4b2917d94ba91673580cadb48ac95f93094':raise ValueError('Original real monitor start differs')
 if hashlib.sha256(raw[0x2d874:0x2d914]).hexdigest()!='04897a2b4cf7a39e6741d83d2e950c35ae6da66be0623fdda63dbc75bb44b2ac':raise ValueError('Original running watchdog differs')
 out=bytearray(raw);out[STARTUP_OFFSET:STARTUP_OFFSET+4]=(0xd2800002|(STARTUP_GRACE_SECONDS<<5)).to_bytes(4,'little');return bytes(out)
