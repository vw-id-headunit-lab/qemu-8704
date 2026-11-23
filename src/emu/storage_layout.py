





import argparse
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import original_8704_vm as vm
import vlm_platform as platform


def parse_pit(raw):
    if len(raw)<28 or struct.unpack_from('<I',raw)[0]!=0x12349876:
        raise ValueError('Require Samsung PIT signature')
    count=struct.unpack_from('<I',raw,4)[0]
    if not 0<count<=256 or 28+count*132>len(raw):raise ValueError('Truncated/excessive PIT table')
    result=[]
                                                                        
                                                                        
    name_offset=32 if struct.unpack_from('<I',raw,8)[0] else 36
    for index in range(count):
        start=28+index*132;words=struct.unpack_from('<8I',raw,start)
        strings=[]
        for offset in (name_offset,64 if name_offset==32 else None,100):
            if offset is None:strings.append(None);continue
            value=raw[start+offset:start+offset+32]
            end=value.find(b'\0')
            if end<0:end=len(value)                                        
            strings.append(value[:end].decode('ascii'))
        row=dict(zip(('word0_uninterpreted','word1_uninterpreted','identifier','word3_uninterpreted','filesystem','block_offset','block_count','device_lun'),words))
        if name_offset==36:row['identifier']=0                                      
        if name_offset==32:row['word96_uninterpreted']=struct.unpack_from('<I',raw,start+96)[0]
        else:row['word32_uninterpreted']=struct.unpack_from('<I',raw,start+32)[0]
        row.update(index=index,name=strings[0],flash_filename=strings[1],option=strings[2])
        result.append(row)
    return {'header_words':list(struct.unpack_from('<7I',raw)),'name_offset':name_offset,'entries':result,'trailing_bytes':len(raw)-(28+count*132)}


def inventory():
    pit_path=platform.factory_file('boot-binary/sadk_hr80_diskmap_ufs.pit')
    xml_path=platform.factory_file('boot-binary/sadk_hr80_diskmap_ufs.xml')
    result=parse_pit(pit_path.read_bytes())
    xml_rows={int(row.attrib['ID']):row.attrib for row in ET.fromstring(xml_path.read_bytes()).findall('partition')}
    source_index={r['name'].removeprefix('./'):r for r in map(json.loads,(vm.ROOT/'var/inputs/analysis/cpu-extraction.jsonl').read_text(encoding='utf-8').splitlines())}
    comparisons=[];payloads=[];ordinals={}
    for row in result['entries']:
        config=xml_rows.get(row['identifier'])
        if config is None:
            row['xml_match']=False;continue
        row['xml']=config
        lun=int(config['Lun']);ordinals[lun]=ordinals.get(lun,0)+1
        row['xml_lun']=lun;row['xml_lun_order']=ordinals[lun]
        row['xml_match']=(row['name']==config['Name'] and row['block_count']==int(config['BlockLength'])
            and row['filesystem']==int(config['Filesystem']) and row['option']==config.get('Option',''))
        comparisons.append(row['xml_match'])
        path=config.get('Path','')
        source=source_index.get(path) or source_index.get(path+'.vw_cn')
        if source and row['block_count']:
            payloads.append({'name':row['name'],'index':row['index'],'identifier':row['identifier'],
                'payload':source['name'],'payload_bytes':source['size'],'pit_blocks':row['block_count'],
                'bytes_per_block_if_exact':source['size']//row['block_count'] if source['size']%row['block_count']==0 else None})
    result.update(pit_source=str(pit_path),pit_sha256=vm.sha(pit_path),xml_source=str(xml_path),xml_sha256=vm.sha(xml_path),
        xml_matched_entries=sum(comparisons),xml_compared_entries=len(comparisons),payload_size_comparisons=payloads,
        scope='Factory file layout only; runtime GPT/LUN enumeration and virtual-UFS remapping not verified')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve()
    if not output.is_relative_to(vm.ROOT/'var') or output.exists():raise ValueError('Require new scratch output')
    output.mkdir(parents=True)
    result=inventory();vm.save(output/'storage-layout.json',result)
    print(json.dumps({key:result[key] for key in ('xml_matched_entries','xml_compared_entries','scope')}))


if __name__=='__main__':main()
