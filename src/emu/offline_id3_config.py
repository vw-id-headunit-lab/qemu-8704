




import hashlib,json,os,re,zipfile
from pathlib import Path

PROFILE='id3-known-coding-v1'
KEY='020301006300000002000'
MASK='00000000000000'
VALUE='00000000000000'
FILE='/vw/hmi/config/diagApi.properties'

def selected(environ=None):
    env=os.environ if environ is None else environ
    value=env.get('OEM_8704_ID3_CONFIG','')
    if value:raise ValueError('Vehicle-specific presets are excluded from the public preview')
    if value and (value!=PROFILE or env.get('OEM_8704_OFFLINE_DEVELOPMENT')!='android-private-unencrypted-v1'):
        raise ValueError('ID.3 coding fixture requires the exact explicit offline profile')
    return bool(value)

def extend_stage(script):
    if script.count('_JAVA_OPTIONS=')!=1 or script.count(r'[Service]\n')!=1:
        raise ValueError('Require one original HMI service/options block')
                                                                               
                                                                           
    return script

def compact_bootargs(text):
                                                                           
                                                                                 
    validate_bootargs(text)
    return text

def validate_bootargs(text):
                                                                           
                                                                            
    suffix=' systemd.mask=smack-setup.service'
    pending=''if suffix.strip()in text.split()else suffix
    size=len((text+pending+' boot_cpus=0,1,2').encode('ascii'))+1
    if size>2048:raise ValueError('ID.3 staging exceeds original 2048-byte final command line')
    return size

def install(early,output):
    enabled=selected()
    import linux_data_overlay as overlay
    original_reuse=overlay.reuse
    def gated_reuse(base,media,out,previous,*,weston_plan=None):
        prior=json.loads((Path(previous)/'linux-ivi-data.json').read_bytes())
        if prior.get('id3_known_coding') and not enabled:
            raise ValueError('An ID.3-configured private seed requires explicit -Id3Vehicle; no silent configuration inheritance')
        return original_reuse(base,media,out,previous,weston_plan=weston_plan)
    overlay.reuse=gated_reuse
    if not enabled:return False
    root=Path(__file__).resolve().parents[2]
    archive=root/'var/inputs/packages/ICAS3CN_HMI.ICAS_CN_2025121-0317/31/ICAS_CN.zip'
    with zipfile.ZipFile(archive)as z:
        raw=z.read('hmi/config/diagApi.properties')
        for kind in ['Data','Scenes']:
            if not any(n.startswith('hmi/Resources/skin0/'+kind+'/'+KEY+'/')for n in z.namelist()):
                raise ValueError('User-corresponding ID.3 original resource absent')
    for name in ['coding.mask','coding.value']:
        match=re.findall(rb'^'+name.encode().replace(b'.',rb'\.')+rb'=(0+)\r?$',raw,re.M)
        if len(match)!=1 or len(match[0])!=50:raise ValueError('Original 25-byte coding overrides differ from the zero baseline')
    import linux_data_overlay as overlay
    from offline_id3_media import masked_file,provision
    changed=masked_file(raw,MASK,VALUE)
    parent=early.stage_hmi_start_grace
    row={'profile':PROFILE,'known_value':VALUE,'known_mask':MASK,'expected_resource_key':KEY,
         'original_diag_sha256':hashlib.sha256(raw).hexdigest(),'private_file':FILE,
         'source':'User-supplied private configuration; unknown bits not inferred',
         'merge_path':'Original PropertyFileManager -> DiagnosisDataProvider -> CodingMib3 -> AslTargetSystemFeaturesAndCoding -> ConfigurationManager',
         'resource_key_forced':False,'full_25byte_factory_coding_claimed':False,
         'eeprom_or_vehicle_written':False,'factory_security_claimed':False,
         'configuration_delivery':'private-original-path-leaf-v2',
         'runtime_file_label':'hmi::sub','original_service_preserved':True,
         'runtime_applied_verified':False,'resource_directory_selected_verified':False}
    parent_reuse=overlay.reuse
    def reuse(base,media,out,previous,*,weston_plan=None):
        result=parent_reuse(base,media,out,previous,weston_plan=weston_plan)
        receipt=provision(result,media,out,raw,changed)
        result['id3_known_coding']=receipt
        row['private_media']=receipt
        return result
    overlay.reuse=reuse
    def stage(**kw):
                                                                              
                                                                            
        result=extend_stage(parent(**kw))
        (Path(output)/'linux-id3-config.json').write_text(json.dumps(row,indent=2)+'\n',encoding='utf8')
        return result
    early.stage_hmi_start_grace=stage
    return True
