
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from abox_dsp_platform import mailbox0_contract

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'var/inputs/support/abox-dsp-native/xtensa-source/hw/xtensa'
OLD_SIM='7292bbc6fde70f0168d9be3c1c4d9c38d3ce0a5b07d2a365281b3da8dcd27c1c'
OLD_MAILBOX='f1ea89665237535192de779cfa2661ad9afab67300780f428211f206600371c2'
OLD=b'''            auto9_mb_create(OBJECT(machine), mailbox, true,
                            qdev_get_gpio_in(gic, 229 - 32),
                            qdev_get_gpio_in(gic, 234 - 32), NULL, NULL);'''
NEW=b'''            auto9_mb_create(OBJECT(machine), mailbox, true,
                            qdev_get_gpio_in(gic, 224 - 32),
                            qdev_get_gpio_in(gic, 229 - 32),
                            qdev_get_gpio_in(gic, 234 - 32), NULL, NULL, NULL);'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace_atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as tmp:
        tmp.write(data)
    temporary=Path(tmp.name)
    try:
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def plan(source, shared):
    sim=source/'sim.c'
    mailbox=source/'auto9-abox-mailbox-shared.inc.c'
    current=sim.read_bytes()
    ending=b'\r\n' if current.count(b'\r\n')==current.count(b'\n') else b'\n'
    old,new=(block.replace(b'\n',ending) for block in (OLD,NEW))
    if sha(current)==OLD_SIM and current.count(old)==1:
        result=current.replace(old,new,1)
    elif current.count(new)==1 and sha(current.replace(new,old,1))==OLD_SIM:
        result=current
    else:
        raise ValueError('Xtensa sim.c is not the verified source or bank-0 result')
    before=mailbox.read_bytes()
    if sha(before)!=OLD_MAILBOX and before!=shared:
        raise ValueError('Xtensa mailbox include is not the verified source or shared model')
    return ((sim,current,result),(mailbox,before,shared))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-core0',required=True,type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    contract=mailbox0_contract(args.original_core0)
    shared=Path(__file__).with_name('exynos-auto9-abox-mailbox.inc.c').read_bytes()
    changes=plan(SOURCE,shared)
    if args.apply:
        for path,before,after in changes:
            if path.read_bytes()!=before:
                raise ValueError('Private Xtensa source changed during preparation')
        for path,before,after in changes:
            if after!=before:
                replace_atomic(path,after)
    print(json.dumps(dict(contract=contract,applied=args.apply,files=[
        dict(path=str(path),before=sha(before),after=sha(after),changed=before!=after)
        for path,before,after in changes]),indent=2))


if __name__=='__main__':
    main()
