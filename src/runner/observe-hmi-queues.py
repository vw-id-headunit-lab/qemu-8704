
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import struct

ABI_HASH='9805491bfa5b41b206b13bba61d228e51e8e129a74f677cbd3e545184e186545'
DISP='de/vw/mib/event/internal/dispatcher/'
THREAD='de/vw/mib/threads/internal/'
EVENT='de/vw/mib/event/internal/'
DESKTOP='de/vw/mib/desktop/internal/'
COMPOSITOR='de/vw/mib/view/internal/ViewCompositorImpl'
OSGI='de/vw/mib/osgi/internal/'
APP_ACT='de/vw/mib/app/framework/internal/Activator'
ASL='de/vw/mib/asl/framework/internal/startupv7r/'
ASL_IMPL=ASL+'api/impl/'
ASL_DSI='de/vw/mib/asl/framework/internal/framework/dsi/'
ASL_PERSIST='de/vw/mib/asl/framework/internal/persistence/'
PERSIST_IMPL=ASL_PERSIST+'internal/'
DIAG='de/vw/mib/asl/framework/internal/diagnosis/'
HSM='de/vw/mib/genericevents/hsm/'
ROOT_DSI=('org.dsi.ifc.persistence.DSIPersistence',
          'org.dsi.ifc.generalvehiclestates.DSIGeneralVehicleStates')
APP_GATES=dict(
    futureValues='Lde/vw/mib/future/FutureValues;',
    subscriptionHelperFactory='Lde/vw/mib/future/subscriptionhelper/SubscriptionHelperFactory;',
    appUIGateFactory='Lde/vw/mib/app/framework/AppUIGateFactory;',
    modelApiProxyFactory='Lde/vw/mib/app/appadapter/proxy/ModelApiProxyFactory;',
    aslClientApiRegistry='Lde/vw/mib/asl/clientapi/ASLClientAPIRegistry;',
    aslClientListFactory='Lde/vw/mib/asl/clientapi/list/ASLClientListFactory;',
    aslComponentRegistry='Lde/vw/mib/asl/framework/api/framework/component/ASLComponentRegistry;',
    hmiListDataFactory='Lde/vw/mib/list/HMIListDataFactory;',
    hmiListRegistry='Lde/vw/mib/list/HMIListRegistry;',
    functionBlockingUiService='Lde/vw/mib/functionblocking/FunctionBlockingUiService;',
    appFrameworkCioFactory='Lde/vw/mib/cio/app/framework/AppFrameworkCioFactory;',
    cioDiagService='Lde/vw/mib/cio/framework/diag/CioDiagService;',
    cioDictionary='Lde/vw/mib/cio/CioDictionary;',
    cioDispatcher='Lde/vw/mib/cio/CioDispatcher;',
    cioFactory='Lde/vw/mib/cio/CioFactory;',
    cioIdService='Lde/vw/mib/cio/framework/CioIdService;',
    contentProcessor='Lde/vw/mib/app/desktop/ContentProcessor;',
    desktopManager='Lde/vw/mib/desktop/DesktopManager;',
    frameworkEventDispatcher='Lde/vw/mib/event/dispatcher/FrameworkEventDispatcher;',
    startupEventDispatcher='Lde/vw/mib/event/dispatcher/StartupEventDispatcher;',
    statemachineEventDispatcher='Lde/vw/mib/event/dispatcher/StatemachineEventDispatcherExt;',
    threadSwitchingTarget='Lde/vw/mib/genericevents/ThreadSwitchingTarget;',
    threadSwitchingTargetHighPrio='Lde/vw/mib/genericevents/ThreadSwitchingTarget;',
    viewEventDispatcher='Lde/vw/mib/event/dispatcher/ViewEventDispatcher;',
    spellerRegistry='Lde/vw/mib/speller/SpellerRegistry;',
    rsiProxyFactory='Lde/vw/mib/rsi/RsiProxyFactory;')
ROLES={
    'UI-Thread':('ThreadWithWatchdog','MainDispatcher',
                 ('statemachineEventQueue','systemEventQueue','viewEventQueue')),
    'Startup-Thread':('ThreadWithoutWatchdog','StartupDispatcher',('queue',)),
}


def load_layout():
    raw=Path(__file__).with_name('oem-jvm-queue-abi.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ABI_HASH:
        raise ValueError('Original JVM SA layout manifest changed')
    return json.loads(raw)


class JVM:
    def __init__(self, read_u64, base, maps, layout):
        self.base=base
        self.maps=maps
        self.layout=layout
        self.words=lru_cache(maxsize=6144)(read_u64)
        self.read_count=0
        self.symbols=layout['symbols']
        text=layout['text_sample']
        if self.data(base,6)!=b'\x7fELF\x02\x01' or self.num(base+18,2)!=183:
            raise ValueError('Original loaded JVM is not ARM64 ELF')
        if self.data(base+text['va'],64)!=bytes.fromhex(text['bytes']):
            raise ValueError('Original loaded JVM text fingerprint does not match')
        self.table=self.num(base+self.symbols['gHotSpotVMStructs'])
        self.typetable=self.num(base+self.symbols['gHotSpotVMTypes'])
        for observed,name in ((self.table,'Structs'),(self.typetable,'Types')):
            if observed!=base+layout['table_virtual_address'][name]:
                raise ValueError('Original loaded SA table does not match the ELF binding')
        self.offsets={}
        for key,value in zip(('TypeNameOffset','FieldNameOffset','TypeStringOffset','IsStaticOffset',
                              'OffsetOffset','AddressOffset','ArrayStride'),(0,8,16,24,32,40,48)):
            observed=self.num(base+self.symbols['gHotSpotVMStructEntry'+key])
            if observed!=value:
                raise ValueError('Original SA structure geometry differs: '+key)
        row=self.typetable+40*layout['cp_size']['index']
        if self.cstr(self.num(row),14)!='ConstantPool' or self.num(row+32)!=layout['cp_size']['size']:
            raise ValueError('Original ConstantPool row differs')
        self.cp_size=layout['cp_size']['size']
        self.oop_width=4 if self.flag('UseCompressedOops') else 8
        self.klass_width=4 if self.flag('UseCompressedClassPointers') else 8
        self.oop_base,self.oop_shift=self.compression('oop',self.oop_width)
        self.klass_base,self.klass_shift=self.compression('klass',self.klass_width)
        self.klass_at=self.field('oopDesc::_metadata.'+
            ('_compressed_klass' if self.klass_width==4 else '_klass'))

    @lru_cache(maxsize=1024)
    def readable_page(self,page):
        return any(lo<=page<hi and flags&1 for lo,hi,flags,_ in self.maps)

    def data(self,a,n):
        if not 0x1000<=a<=a+n<=1<<39 or not 1<=n<=384:
            raise ValueError('JVM metadata read outside the authorized VM3 user range')
        if any(not self.readable_page(p) for p in range(a&~0xfff,((a+n-1)&~0xfff)+1,4096)):
            raise ValueError('JVM metadata crosses a non-readable VMA')
        self.read_count+=1
        if self.read_count>10000:
            raise ValueError('JVM metadata read count exceeded')
        words=b''.join(self.words(i).to_bytes(8,'little') for i in range(a&~7,a+n,8))
        return words[a&7:(a&7)+n]

    def num(self,a,n=8):
        return int.from_bytes(self.data(a,n),'little')

    def cstr(self,a,limit):
                                                                          
        if not self.base<=a<a+limit<=self.base+self.layout['loads'][0]['filesz']:
            raise ValueError('SA name outside original JVM text mapping')
        raw=self.data(a,limit)
        if b'\0' not in raw:
            raise ValueError('Unterminated SA name')
        return raw.split(b'\0',1)[0].decode('ascii')

    def field(self,name):
        if name in self.offsets:
            return self.offsets[name]
        expected=self.layout['structs'][name]
        a=self.table+48*expected['row_index']
        type_,field=name.split('::')
        if self.cstr(self.num(a),len(type_)+1)!=type_ or self.cstr(self.num(a+8),len(field)+1)!=field:
            raise ValueError('Original JVM SA field identity mismatch: '+name)
        static=self.num(a+24,4)
        if static!=expected['IsStatic']:
            raise ValueError('Original JVM SA static flag differs')
        if static:
            out=self.num(a+40)
            if out!=self.base+expected['Address']:
                raise ValueError('Original JVM SA static address differs')
        else:
            out=self.num(a+32)
            if not 0<=out<0x10000:
                raise ValueError('Original JVM SA offset outside struct bounds')
        self.offsets[name]=out
        return out

    def flag(self,name):
        d=self.layout['flags'][name]
        row=self.base+d['row']
        n=self.num(row+self.field('Flag::_name'))
        v=self.num(row+self.field('Flag::_addr'))
        if n!=self.base+d['name_va'] or v!=self.base+d['value_va'] or self.cstr(n,len(name)+1)!=name:
            raise ValueError('Original JVM compressed-reference flag does not match')
        value=self.num(v,1)
        if value not in (0,1):
            raise ValueError('Original JVM compression flag is not boolean')
        return bool(value)

    def compression(self,kind,width):
        if width==8:
            return 0,0
        base=self.num(self.field('Universe::_narrow_'+kind+'._base'))
        shift=self.num(self.field('Universe::_narrow_'+kind+'._shift'),4)
        if not 0<=base<1<<39 or base&7 or not 0<=shift<=6:
            raise ValueError('Invalid original JVM narrow reference format')
        return base,shift

    @lru_cache(maxsize=2048)
    def klass(self,oop):
        if not oop or oop&7:
            raise ValueError('Original Java object is null or unaligned')
        k=self.num(oop+self.klass_at,self.klass_width)
        if not k:
            raise ValueError('Original Java object has no class')
        return self.klass_base+(k<<self.klass_shift)

    @lru_cache(maxsize=256)
    def sym(self,a):
        length=self.num(a+self.field('Symbol::_length'),2)
        if not 0<length<=384:
            raise ValueError('Original JVM metadata symbol length is invalid')
        return self.data(a+self.field('Symbol::_body'),length).decode('utf-8','replace')

    @lru_cache(maxsize=512)
    def kname(self,klass):
        return self.sym(self.num(klass+self.field('Klass::_name')))

    def expect(self,oop,classname):
        k=self.klass(oop)
        if self.kname(k)!=classname:
            raise ValueError('Original Java object class does not match '+classname)
        return k

    @lru_cache(maxsize=512)
    def pool(self,k):
        cp=self.num(k+self.field('InstanceKlass::_constants'))
        cp_len=self.num(cp+self.field('ConstantPool::_length'),4)
        tags=self.num(cp+self.field('ConstantPool::_tags'))
        arr_length=self.field('Array<Klass*>::_length')
        tag_data=self.field('Array<u1>::_data')
        if (self.num(cp+self.field('ConstantPool::_pool_holder'))!=k or
                not 1<cp_len<=32768 or self.num(tags+arr_length,4)!=cp_len):
            raise ValueError('Original Java constant pool geometry differs')
        return cp,cp_len,tags+tag_data

    def resolved_class(self,owner,index,expected):
        cp,length,tags=self.pool(owner)
        if not 0<index<length or self.num(tags+index,1)!=7:
            raise ValueError('Original class constant not resolved: '+expected)
        klass=self.num(cp+self.cp_size+index*8)
        if self.kname(klass)!=expected:
            raise ValueError('Original class constant identity differs: '+expected)
        return klass

    @lru_cache(maxsize=64)
    def offsets_for(self,k,requested,static=False):
        d={n:s for n,s in requested}
        out={}
        if not d:
            return out
        cp,cp_len,tags=self.pool(k)
        count=self.num(k+self.field('InstanceKlass::_java_fields_count'),2)
        fs=self.num(k+self.field('InstanceKlass::_fields'))
        arr_length=self.field('Array<Klass*>::_length')
        data_off=self.field('Array<u2>::_data')
        if not 0<count<=256 or not 6*count<=self.num(fs+arr_length,4)<=4096:
            raise ValueError('Original Java field table ownership or extent differs')

        def utf8(index):
            if not 0<index<cp_len or self.num(tags+index,1)!=1:
                raise ValueError('Original Java field name has no UTF8 constant')
            return self.sym(self.num(cp+self.cp_size+index*8))

        for i in range(count):
            acc,ni,si,_,lo,hi=struct.unpack('<6H',self.data(fs+data_off+i*12,12))
            if bool(acc&0x8)!=static:
                continue
            name=utf8(ni)
            if name not in d:
                continue
            packed=(hi<<16)|lo
            if name in out or packed&3!=1 or utf8(si)!=d[name] or not 8<=packed>>2<4096:
                raise ValueError('Original Java field layout differs: '+name)
            out[name]=packed>>2
            if len(out)==len(d):
                return out
        raise ValueError('Required Java field metadata is absent: '+str(sorted(d.keys()-out.keys())))

    def values(self,oop,clazz,field_specs):
        k=self.klass(oop)
        object_size=self.num(k+self.field('Klass::_layout_helper'),4)&~1
                                                                                  
        for _ in range(8):
            if self.kname(k)==clazz:
                break
            k=self.num(k+self.field('Klass::_super'))
            if not k:
                break
        if not k or self.kname(k)!=clazz:
            raise ValueError('Original Java class hierarchy differs: '+clazz)
        offs=self.offsets_for(k,tuple(field_specs.items()))
        result={}
        for n,s in field_specs.items():
            isref=s.startswith(('L','['))
            width=self.oop_width if isref else {'Z':1,'B':1,'I':4,'J':8}[s]
            if not 8<=object_size<=4096 or offs[n]+width>object_size:
                raise ValueError('Original Java field exceeds object layout: '+n)
            value=self.num(oop+offs[n],width)
            result[n]=self.oop_base+(value<<self.oop_shift) if isref and value else value
        return result

    def ref(self,addr):
        n=self.num(addr,self.oop_width)
        return self.oop_base+(n<<self.oop_shift) if n else 0

    def bools(self,oop,clazz,*names):
        v=self.values(oop,clazz,{n:'Z' for n in names})
        if any(n not in (0,1) for n in v.values()):
            raise ValueError('Original HMI boolean field is not boolean')
        return {k:bool(n) for k,n in v.items()}

    def static_values(self,klass,specs):
        mirror=self.num(klass+self.field('Klass::_java_mirror'))
        mk=self.expect(mirror,'java/lang/Class')
                                                                      
        base=self.num(mk+self.field('Klass::_layout_helper'),4)&~1
        count=self.num(klass+self.field('InstanceKlass::_static_field_size'),4)
        if not 16<=base<=512 or not 0<count<=256:
            raise ValueError('Original class mirror layout differs')
        offsets=self.offsets_for(klass,tuple(specs.items()),True)
        out={}
        for n,off in offsets.items():
            isref=specs[n].startswith(('L','['))
            width=self.oop_width if isref else 1
            if not base<=off<=base+count*8+16-width or off%width:
                raise ValueError('Original static field outside its class mirror')
            out[n]=self.ref(mirror+off) if isref else self.num(mirror+off,1)
        return out

    def match_stock_string(self,oop,allowed):

        self.expect(oop,'java/lang/String')
        a=self.values(oop,'java/lang/String',dict(value='[C'))['value']
        k=self.expect(a,'[C')
        layout=self.num(k+self.field('Klass::_layout_helper'),4)
        header=(layout>>16)&0xff
        length=self.num(a+self.klass_at+self.klass_width,4)
        if layout>>30!=3 or not 16<=header<=24 or layout&63!=1 or not 0<=length<=384:
            raise ValueError('Original Java char array layout differs')
        keys=[s for s in allowed if len(s)==length]
        if not keys:
            return None
        raw=self.data(a+header,length*2)
        return next((s for s in keys if s.encode('utf-16le')==raw),None)

    def stock_map_get(self,oop,label):

        if not label.isascii():
            raise ValueError('Original HashMap key must be an ASCII allowlist constant')
        node='java/util/HashMap$Node'
        sig='[L'+node+';'
        row=self.values(oop,'java/util/HashMap',dict(table=sig,size='I'))
        if not row['table']:
            if row['size']:
                raise ValueError('Original HashMap has no table')
            return 0
        extent,header=self.object_array(row['table'],sig)
        if not 1<=extent<=2048 or extent&(extent-1) or row['size']>1024:
            raise ValueError('Original HashMap table differs from bounded layout')
        h=0
        for byte in label.encode('ascii'):
            h=(31*h+byte)&0xffffffff
        h^=h>>16
        item=self.ref(row['table']+header+(h&(extent-1))*self.oop_width)
        seen=set()
        while item:
            if item in seen or len(seen)>=min(64,row['size']):
                raise ValueError('Original HashMap bucket is changing or inconsistent')
            seen.add(item)
            n=self.values(item,node,dict(hash='I',key='Ljava/lang/Object;',
                                        value='Ljava/lang/Object;',next='L'+node+';'))
            if n['hash']==h and self.match_stock_string(n['key'],(label,)):
                return n['value']
            item=n['next']
        return 0

    def osgi_root_registrations(self,bundle):
        owner=self.expect(bundle,OSGI+'BundleInfo')
        cl=OSGI+'ServiceRegistry'
        k=self.resolved_class(owner,146,cl)
        registry=self.static_values(k,dict(INSTANCE='L'+cl+';'))['INSTANCE']
        services=self.values(registry,cl,dict(serviceMap='Ljava/util/Map;'))['serviceMap']
        result={}
        for name in ROOT_DSI:
            entry=self.stock_map_get(services,name)
            row=result[name]=dict(active_registrations=0,stock_instance_zero=False)
            if not entry:
                continue
            cow='java/util/concurrent/CopyOnWriteArrayList'
            arr_list=self.values(entry,cl+'$Entry',dict(services='L'+cow+';'))['services']
            sig='[Ljava/lang/Object;'
            arr=self.values(arr_list,cow,dict(array=sig))['array']
            extent,header=self.object_array(arr,sig)
            if not 0<=extent<=16:
                raise ValueError('Original OSGi service list exceeds observation bound')
            impl='com/lge/mib3oi/dsi/'+name.split('.ifc.',1)[1].replace('.','/')+'Impl'
            info=OSGI+'ServiceInfo'
            for i in range(extent):
                ref=self.ref(arr+header+i*self.oop_width)
                obj=self.values(ref,info,dict(registered='Z',serviceObject='Ljava/lang/Object;'))
                if obj['registered'] not in (0,1):
                    raise ValueError('Original OSGi registration flag is not boolean')
                if not obj['registered']:
                    continue
                row['active_registrations']+=1
                o=obj['serviceObject']
                if o and self.kname(self.klass(o))==impl and self.values(o,impl,dict(instanceId='I'))['instanceId']==0:
                    row['stock_instance_zero']=True
                    if name==ROOT_DSI[0]:
                        try:
                            row['native_subscription']=self.persistence_subscription_state(o,impl)
                        except (OSError,ValueError) as exc:
                            row['native_subscription']=dict(unverified=str(exc))
        return result

    def persistence_subscription_state(self,obj,impl):
        base='com/lge/mib3oi/dsi/'
        fields=self.values(obj,impl,dict(notifyListeners='L'+base+'NotifyListenerTable;',
                           rListener='Ljava/util/List;',dsi='L'+base+'DSIProvider;'))
        result=dict(notification_table_present=bool(fields['notifyListeners']),
                    response_listener_list_present=bool(fields['rListener']))
        owner=self.expect(obj,impl)
        result['original_diagnostic_source']=self.static_values(owner,dict(
            gFromDiagIndex='I',use_diag_from_native='Z',use_diag_from_mcu='Z',isReadBufferStarted='Z'))
        result['local_store']=self.values(obj,impl,dict(gLSMFileIndex='I',isWriteBufferStarted='Z'))
        if fields['rListener']:
            result['response_listener_count']=self.values(fields['rListener'],'java/util/ArrayList',dict(size='I'))['size']
        provider=fields['dsi']
        if not provider:
            return result
        kind=base+'DSIProvider'
        k=self.expect(provider,kind)
        static=self.static_values(k,dict(dsiprovider='L'+kind+';',handler='L'+base+'Handler;'))
        if static['dsiprovider']!=provider:
            raise ValueError('Original persistence object has a different DSI provider singleton')
        qclass=base+'MessageQueue'
        refs=self.values(provider,kind,dict(providerMsgQueue='L'+qclass+';'))
        queue=refs['providerMsgQueue']
        if static['handler']:
            handler=static['handler']
            if self.values(handler,base+'Handler',dict(queue='L'+qclass+';'))['queue']!=queue:
                raise ValueError('Original DSI provider handler has a different queue')
            result.update(self.bools(handler,base+'Handler','isNativeServiceStarted'))
        if not queue:
            return result
        queued,pending=self.dsi_message_queue(queue,lambda m:
            m['domainId']==52 and m['interfaceId']==1400 and m['apiId']==9999)
        result.update(provider_queue_depth=queued,persistence_subscription_pending=bool(pending))
        return result

    def dsi_message_queue(self,queue,match):
        base='com/lge/mib3oi/dsi/'
        qclass=base+'MessageQueue'
        clq='java/util/concurrent/ConcurrentLinkedQueue'
        node=clq+'$Node'
        node_sig='L'+node+';'
        real=self.values(queue,qclass,dict(queue='L'+clq+';'))['queue']
        cur=self.values(real,clq,dict(head=node_sig))['head']
        seen=set()
        queued=0
        matched=0
        while cur:
            if cur in seen or len(seen)>=128:
                raise ValueError('Original DSI message queue exceeds its observation bound')
            seen.add(cur)
            fields=self.values(cur,node,dict(item='Ljava/lang/Object;',next=node_sig))
            if fields['item']:
                queued+=1
                m=self.values(fields['item'],base+'Message',dict(domainId='J',interfaceId='I',apiId='I'))
                matched+=int(match(m))
            cur=fields['next']
        return queued,matched

    @staticmethod
    def is_persistence_welcome(fields):
        return fields['domainId']==52 and fields['interfaceId']==9000

    def dsi_listener_state(self,obj,worker_thread=None):
        base='com/lge/mib3oi/dsi/'
        handler=base+'ListenerHandler'
        provider=base+'DSIProvider'
        sig='L'+base+'MessageQueue;'
        cls=self.expect(obj,handler)
        owner=self.resolved_class(cls,100,provider)
        stock=self.static_values(owner,dict(dsiprovider='L'+provider+';',listenerhanlder='L'+handler+';'))
        if stock['listenerhanlder']!=obj or not stock['dsiprovider']:
            raise ValueError('Original DSI listener is not the provider singleton owner')
        q=self.values(obj,handler,dict(listenerMainQueue=sig))['listenerMainQueue']
        if not q or self.values(stock['dsiprovider'],provider,dict(listenerMsgQueue=sig))['listenerMsgQueue']!=q:
            raise ValueError('Original DSI listener is attached to a different queue')
        depth,welcomes=self.dsi_message_queue(q,self.is_persistence_welcome)
        result=dict(depth=depth,persistence_welcome_pending=welcomes)
        try:
            result['worker_8']=self.dsi_worker_state(obj,handler,worker_thread)
        except (OSError,ValueError) as exc:
            result['worker_8']=dict(unverified=str(exc))
        return result

    def dsi_worker_state(self,listener,handler,worker_thread):
                                                                                   
        base='com/lge/mib3oi/dsi/'
        concurrent='java/util/concurrent/'
        list_class='java/util/ArrayList'
        ref=self.values(listener,handler,dict(executorService='L'+list_class+';'))['executorService']
        sig='[Ljava/lang/Object;'
        fields=self.values(ref,list_class,dict(size='I',elementData=sig))
        count,header=self.object_array(fields['elementData'],sig)
        if not fields['size']==8<=count<=16:
            raise ValueError('Original listener executor list differs')
        wrapper=self.ref(fields['elementData']+header+7*self.oop_width)
        self.expect(wrapper,concurrent+'Executors$FinalizableDelegatedExecutorService')
        pool=self.values(wrapper,concurrent+'Executors$DelegatedExecutorService',
                         dict(e='L'+concurrent+'ExecutorService;'))['e']
        pool_class=concurrent+'ThreadPoolExecutor'
        queue=self.values(pool,pool_class,dict(workQueue='L'+concurrent+'BlockingQueue;'))['workQueue']
        qclass=concurrent+'LinkedBlockingQueue'
        atom=concurrent+'atomic/AtomicInteger'
        node=qclass+'$Node'
        node_sig='L'+node+';'
        fields=self.values(queue,qclass,dict(head=node_sig,count='L'+atom+';'))
        length=self.values(fields['count'],atom,dict(value='I'))['value']
        if not 0<=length<=128:
            raise ValueError('Original persistence worker exceeds observation bound')

        def task_matches(task):
            callable_=self.values(task,concurrent+'FutureTask',dict(callable='L'+concurrent+'Callable;'))['callable']
            runnable=self.values(callable_,concurrent+'Executors$RunnableAdapter',
                                 dict(task='Ljava/lang/Runnable;'))['task']
            m=self.values(runnable,base+'ListenerWorkerThread',dict(message='L'+base+'Message;'))['message']
            return self.is_persistence_welcome(self.values(m,base+'Message',dict(domainId='J',interfaceId='I')))

        cur=fields['head']
        seen=set()
        pending=0
        while cur:
            if cur in seen or len(seen)>length:
                raise ValueError('Original worker queue is inconsistent')
            fields=self.values(cur,node,dict(item='Ljava/lang/Object;',next=node_sig))
            if bool(fields['item'])!=bool(seen):
                raise ValueError('Original worker queue sentinel or item differs')
            if fields['item']:
                pending+=int(task_matches(fields['item']))
            seen.add(cur)
            cur=fields['next']
        if len(seen)!=length+1:
            raise ValueError('Original worker queue size differs')
        result=dict(depth=length,persistence_welcome_pending=pending,thread_observed=bool(worker_thread))
        if worker_thread:
            worker=self.values(worker_thread,'java/lang/Thread',dict(target='Ljava/lang/Runnable;'))['target']
            fields=self.values(worker,pool_class+'$Worker',{
                'this$0':'L'+pool_class+';','thread':'Ljava/lang/Thread;',
                'firstTask':'Ljava/lang/Runnable;','completedTasks':'J'})
            if fields['this$0']!=pool or fields['thread']!=worker_thread:
                raise ValueError('Original DSIWorker-8 has a different executor owner')
            result.update(completed_tasks=fields['completedTasks'],
                          first_task_is_welcome=bool(fields['firstTask'] and task_matches(fields['firstTask'])))
        return result

    def root_dsi_state(self,locator,startup_api):
        proxy_class=ASL_DSI+'proxy/DSIProxyTarget'
        proxy=self.values(locator,ASL_DSI+'DSIServiceLocator',dict(proxyTarget='L'+proxy_class+';'))['proxyTarget']
        try:
            persistence=self.persistence_proxy_state(proxy,proxy_class)
        except (OSError,ValueError) as exc:
            persistence=dict(unverified=str(exc))
        cow='de/vw/mib/collections/copyonwrite/CowArray'
        listeners=self.values(proxy,proxy_class,dict(serviceStateListeners='L'+cow+';'))['serviceStateListeners']
        pooled=self.values(listeners,cow,dict(currentElements='L'+cow+'$PooledArray;'))['currentElements']
        items=self.values(pooled,cow+'$PooledArray',dict(array='[Ljava/lang/Object;',size='I'))
        arr=items['array']
        extent,header=self.object_array(arr,'[Ljava/lang/Object;')
        if not 0<=items['size']<=extent<=128:
            raise ValueError('Original DSI listener array exceeds observation bound')
        dsi_class=ASL+'DsiStateService'
        owners=[]
        for i in range(items['size']):
            listener=self.ref(arr+header+i*self.oop_width)
            if listener and self.kname(self.klass(listener))==dsi_class+'$1':
                owners.append(self.values(listener,dsi_class+'$1',{'this$0':'L'+dsi_class+';'})['this$0'])
        if not owners:
            return dict(listener_registered=False,persistence_proxy=persistence)
        if len(owners)!=1 or not startup_api:
            raise ValueError('Original ASL DSI listener ownership is ambiguous')
        dsi=self.values(owners[0],dsi_class,dict(startedDsiServices='Ljava/util/Set;',
                        startupApiServiceImpl='L'+ASL_IMPL+'StartupAPIServiceImpl;'))
        if dsi['startupApiServiceImpl']!=startup_api:
            raise ValueError('Original DSI listener belongs to a different ASL API')
        backing=self.values(dsi['startedDsiServices'],'java/util/HashSet',dict(map='Ljava/util/HashMap;'))['map']
        node='java/util/HashMap$Node'
        sig='[L'+node+';'
        values=self.values(backing,'java/util/HashMap',dict(table=sig,size='I'))
        size,table=values['size'],values['table']
        keys={name:name+'[0]' for name in ROOT_DSI}
        found=set()
        seen=set()
        if table:
            extent,header=self.object_array(table,sig)
            if not 1<=extent<=1024 or not 0<=size<=512:
                raise ValueError('Original DSI started-set exceeds observation bound')
            for i in range(extent):
                entry=self.ref(table+header+i*self.oop_width)
                while entry:
                    if entry in seen or len(seen)>=size:
                        raise ValueError('Original DSI started-set is changing or inconsistent')
                    seen.add(entry)
                    fields=self.values(entry,node,dict(key='Ljava/lang/Object;',next='L'+node+';'))
                    key=self.match_stock_string(fields['key'],keys.values())
                    if key:
                        found.add(key)
                    entry=fields['next']
        if len(seen)!=size:
            raise ValueError('Original DSI started-set count differs')
        return dict(listener_registered=True,total_notifications=size,persistence_proxy=persistence,
                    root_instance_zero={name:key in found for name,key in keys.items()})

    def bitset_contains(self,oop,index):
        if not oop:
            return False
        values=self.values(oop,'java/util/BitSet',dict(words='[J',wordsInUse='I'))
        array=values['words']
        klass=self.expect(array,'[J')
        layout=self.num(klass+self.field('Klass::_layout_helper'),4)
        header=(layout>>16)&0xff
        length=self.num(array+self.klass_at+self.klass_width,4)
        used=values['wordsInUse']
        if layout>>30!=3 or header not in (16,24) or (layout&63)!=3 or not 0<=used<=length<=64:
            raise ValueError('Original BitSet backing layout differs')
        word,bit=divmod(index,64)
        return word<used and bool(self.num(array+header+8*word)&(1<<bit))

    def persistence_proxy_state(self,proxy,proxy_class):
                                                                                  
        slot,attr_bit=178,0
        skeleton=ASL_DSI+'skeleton/DSISkeletonTarget'
        maps='de/vw/mib/collections/'
        listener_sig='[L'+maps+'ObjectObjectMap;'
        bitset_sig='[Ljava/util/BitSet;'
        refs=self.values(proxy,proxy_class,dict(skeletonTarget='L'+skeleton+';',
                         registeredDSIServices='Ljava/util/BitSet;',
                         skeletonNotificationMasks=bitset_sig,listenerNotificationMasks=listener_sig))

        def entry(array,signature):
            count,header=self.object_array(array,signature)
            if not slot<count<=512:
                raise ValueError('Original persistence slot outside bounded array')
            return self.ref(array+header+slot*self.oop_width)

        def map_size(oop,clazz):
            if not oop:
                return 0
            size=self.values(oop,clazz,dict(size='I'))['size']
            if not 0<=size<=128:
                raise ValueError('Original persistence map exceeds observation bound')
            return size

        obj_sig='[Ljava/lang/Object;'
        services=self.values(refs['skeletonTarget'],skeleton,dict(dsiServices=obj_sig))['dsiServices']
        service=entry(services,obj_sig)
        impl='com/lge/mib3oi/dsi/persistence/DSIPersistenceImpl'
        if service and self.values(service,impl,dict(instanceId='I'))['instanceId']!=0:
            raise ValueError('Original persistence skeleton holds a different instance')
        result=dict(slot=slot,attr=1,service_in_skeleton=bool(service),
                    service_registered_in_proxy=self.bitset_contains(refs['registeredDSIServices'],slot),
                    attr_in_skeleton_mask=self.bitset_contains(entry(refs['skeletonNotificationMasks'],bitset_sig),attr_bit),
                    subscriber_count=map_size(entry(refs['listenerNotificationMasks'],listener_sig),maps+'ObjectObjectOptHashMap'))
        try:
            dp=ASL_DSI+'proxy/NotificationDatapool'
            klass=self.resolved_class(self.expect(proxy,proxy_class),939,dp)
            sig='[L'+maps+'ints/IntObjectMap;'
            matrix=self.static_values(klass,dict(NOTIF_DP_MX=sig))['NOTIF_DP_MX']
            cached=map_size(entry(matrix,sig),maps+'ints/IntObjectOptHashMap')
            if cached>1:
                raise ValueError('Original persistence has exactly one configured attribute')
            result['attr_in_proxy_datapool']=bool(cached)
        except (OSError,ValueError) as exc:
            result['datapool_unverified']=str(exc)
        return result

    def pers_hsm_state(self,framework_class,state_service):
        sync=self.static_values(framework_class,dict(
            persistence='Lde/vw/mib/asl/framework/api/persistence/AslPersistenceSyncApi;'))['persistence']
        if not sync:
            return dict(original_sync_api=False)
        owner=self.expect(sync,PERSIST_IMPL+'Persistence')
        main=PERSIST_IMPL+'main/'
        k=self.resolved_class(owner,29,main+'ApiPersMain')
        target=main+'PersistenceMainTarget'
        obj=self.static_values(k,dict(mMainTarget='L'+target+';'))['mMainTarget']
        if not obj:
            return dict(original_sync_api=True,target_constructed=False)
        state_sig='L'+HSM+'HsmState;'
        states=('Top','WaitingForDatabase','FlashOk','Idle','Diagnosis')
        fields=self.values(obj,target,dict({('mState'+s):state_sig for s in states},
            persistenceStateChangeService='Lde/vw/mib/asl/framework/api/persistence2/startup/PersistenceStateChangeService;'))
        if fields['persistenceStateChangeService']!=state_service:
            raise ValueError('Original persistence main target belongs to a different state service')
        hsm=self.values(obj,HSM+'TargetHsmImpl',dict(hsm='L'+HSM+'Hsm;'))['hsm']
        cur=self.values(hsm,HSM+'Hsm',dict(currentState=state_sig))['currentState']
        active=next((s for s in states if cur and cur==fields['mState'+s]),None)
        out=dict(original_sync_api=True,target_constructed=True,current_state=active,
                    current_state_object_present=bool(cur),**self.bools(obj,target,
                    'mIsEnabled','mIsDiagnosisOn','mIsDisabledDueToError'),
                    initial_profile_handler_constructed=self.bools(fields['mStateIdle'],
                        main+'StateIdle','startedInitialProfileTarget')['startedInitialProfileTarget'])
        try:
            db=PERSIST_IMPL+'db/';dk=self.resolved_class(owner,110,db+'ApiPersDbImpl')
            instance=self.static_values(dk,dict(instance='L'+db+'ApiPersDb;'))['instance']
            rq=self.values(instance,db+'ApiPersDbImpl',dict(mRequestMap='L'+db+'RequesterMap;'))['mRequestMap']
            mm=self.values(rq,db+'RequesterMap',dict(mMap='Lde/vw/mib/collections/ObjectObjectMultimap;'))['mMap']
            backing=self.values(mm,'de/vw/mib/collections/ObjectObjectMultimap',dict(backingMap='Lde/vw/mib/collections/ObjectObjectMap;'))['backingMap']
            present,value=self.opt_coding_entry(backing,db+'RequesterKey')
            out['coding_read_waiting_for_dsi_response']=present
            out['coding_read_requester_list_present']=bool(value)
        except (OSError,ValueError)as e:out['coding_request_unverified']=str(e)
        return out

    def stock_property_get(self,oop,label):

        if label not in ('coding.mask','coding.value'):
            raise ValueError('Only the two known diagnostic override fields are admitted')
        self.expect(oop,'java/util/Properties')
        node='java/util/Hashtable$Entry';sig='[L'+node+';'
        row=self.values(oop,'java/util/Hashtable',dict(table=sig,count='I'))
        extent,header=self.object_array(row['table'],sig)
        if not 1<=extent<=2048 or not 0<=row['count']<=1024:
            raise ValueError('Original bounded Properties table differs')
        h=0
        for byte in label.encode('ascii'):h=(31*h+byte)&0xffffffff
        item=self.ref(row['table']+header+((h&0x7fffffff)%extent)*self.oop_width)
        seen=set()
        while item:
            if item in seen or len(seen)>=min(64,row['count']):raise ValueError('Properties bucket links differ')
            seen.add(item)
            n=self.values(item,node,dict(hash='I',key='Ljava/lang/Object;',value='Ljava/lang/Object;',next='L'+node+';'))
            if n['hash']==h and self.match_stock_string(n['key'],(label,)):return n['value']
            item=n['next']
        return 0

    def stock_numeric_map_get(self,oop,kind,key):

        if (kind,key) not in (('Integer',0x02000000),('Long',1536),('Identifier',1536)):
            raise ValueError('Only the original coding namespace/key is admitted')
        node='java/util/HashMap$Node';sig='[L'+node+';'
        row=self.values(oop,'java/util/HashMap',dict(table=sig,size='I'))
        if not row['table']:
            if row['size']:raise ValueError('Numeric HashMap lacks its table')
            return 0
        extent,header=self.object_array(row['table'],sig)
        if not 1<=extent<=2048 or extent&(extent-1) or row['size']>1024:
            raise ValueError('Numeric HashMap exceeds bounded layout')
        h=key^(key>>32)
        if kind=='Identifier':h=(31*(31+h)+0x02000000)&0xffffffff
        h=(h^(h>>16))&0xffffffff
        item=self.ref(row['table']+header+(h&(extent-1))*self.oop_width);seen=set()
        while item:
            if item in seen or len(seen)>=min(64,row['size']):
                raise ValueError('Numeric HashMap bucket differs')
            seen.add(item)
            n=self.values(item,node,dict(hash='I',key='Ljava/lang/Object;',value='Ljava/lang/Object;',next='L'+node+';'))
            if n['hash']==h:
                if kind=='Identifier':
                    row=self.values(n['key'],DIAG+'config/util/Identifier',dict(mNamespace='I',mKey='J'))
                    if row==dict(mNamespace=0x02000000,mKey=1536):return n['value']
                else:
                    boxed=self.values(n['key'],'java/lang/'+kind,dict(value='I'if kind=='Integer'else'J'))['value']
                    if boxed==key:return n['value']
            item=n['next']
        return 0

    def bounded_byte_array(self,oop):
        k=self.expect(oop,'[B');layout=self.num(k+self.field('Klass::_layout_helper'),4)
        header=(layout>>16)&255;n=self.num(oop+self.klass_at+self.klass_width,4)
        if layout>>30!=3 or layout&63 or header not in (16,24)or not 0<=n<=384:
            raise ValueError('Original byte array exceeds bounded coding metadata')
        return self.data(oop+header,n)if n else b''

    def opt_coding_entry(self,oop,key_class):
        allowed=(DIAG+'config/util/Identifier',PERSIST_IMPL+'db/RequesterKey')
        if key_class not in allowed:raise ValueError('Only original coding identifier classes admitted')
        row=self.values(oop,'de/vw/mib/collections/ObjectObjectOptHashMap',dict(
            keys='[Ljava/lang/Object;',values='[Ljava/lang/Object;',valueStates='[B',size='I'))
        states=self.bounded_byte_array(row['valueStates'])
        n,kh=self.object_array(row['keys'],'[Ljava/lang/Object;');vn,vh=self.object_array(row['values'],'[Ljava/lang/Object;')
        if n!=vn or n!=len(states)or not 0<=row['size']<=n or states.count(2)!=row['size']or any(s>2 for s in states):
            raise ValueError('Original opt-map layout or filled count differs')
        found=[]
        for i,state in enumerate(states):
            if state!=2:continue
            key=self.ref(row['keys']+kh+i*self.oop_width)
            spec=dict(mNamespace='I',mKey='J')
            if key_class.endswith('RequesterKey'):spec['mMode']='B'
            identity=self.values(key,key_class,spec)
            if identity['mNamespace']==0x02000000 and identity['mKey']==1536 and identity.get('mMode',2)==2:
                found.append(self.ref(row['values']+vh+i*self.oop_width))
        if len(found)>1:raise ValueError('Duplicate original coding identifier')
        return bool(found),found[0]if found else 0

    def coding_backend_state(self,provider):
        refs=self.values(provider,DIAG+'DiagnosisDataProvider',dict(
            diagnosisValidityProvider='L'+DIAG+'DiagnosisValidityProviderImpl;',
            mapDiagnosisIdentifier2CachedData='Lde/vw/mib/collections/ObjectObjectMap;',
            requestedDiagnosisData='Ljava/util/Set;'))
        out={'namespace':0x02000000,'key':1536}
        valid=self.values(refs['diagnosisValidityProvider'],DIAG+'DiagnosisValidityProviderImpl',
                          dict(namespace2ValidKeys='Ljava/util/Map;'))['namespace2ValidKeys']
        keys=self.stock_numeric_map_get(valid,'Integer',0x02000000)
        out['valid_namespace_present']=bool(keys)
        if keys:
            keymap=self.values(keys,'java/util/HashSet',dict(map='Ljava/util/HashMap;'))['map']
            out['coding_data_valid']=bool(self.stock_numeric_map_get(keymap,'Long',1536))
        else:out['coding_data_valid']=False
        out['cached_identifier_count']=self.values(refs['mapDiagnosisIdentifier2CachedData'],
            'de/vw/mib/collections/ObjectObjectOptHashMap',dict(size='I'))['size']
        req=self.values(refs['requestedDiagnosisData'],'java/util/HashSet',dict(map='Ljava/util/HashMap;'))['map']
        out['requested_identifier_count']=self.values(req,'java/util/HashMap',dict(size='I'))['size']
        out['coding_requested']=bool(self.stock_numeric_map_get(req,'Identifier',1536))
        cached,access=self.opt_coding_entry(refs['mapDiagnosisIdentifier2CachedData'],DIAG+'config/util/Identifier')
        out['coding_cached']=cached
        if cached and access:
            raw=self.values(access,DIAG+'config/AccessImpl',dict(mData='[B'))['mData']
            data=self.bounded_byte_array(raw);out['cached_coding_bytes']=len(data)
            mask=bytes.fromhex('00000000000000');value=bytes.fromhex('00000000000000')
            out['cached_known_bits_match']=len(data)>=7 and all((a&m)==(b&m)for a,m,b in zip(data,mask,value))
        warehouse=self.values(provider,DIAG+'DiagnosisDataProvider',dict(classWarehouse='L'+DIAG+'config/util/ClassWarehouse;'))['classWarehouse']
        out['original_oilarge_warehouse']=self.kname(self.klass(warehouse))==DIAG+'variant/oilarge/ClassWarehouseImpl'
        out['scope']='Read-only original validity set and counts; no response status inferred or guest state changed'
        return out

    def coding_selection_state(self,provider):

        refs=self.values(provider,DIAG+'DiagnosisDataProvider',dict(
            propertyFileManager='L'+DIAG+'config/PropertyFileManager;',
            configurationManager='Lde/vw/mib/configuration/ConfigurationManager;'))
        out={'property_manager_present':bool(refs['propertyFileManager']),
             'configuration_manager_present':bool(refs['configurationManager'])}
        if refs['propertyFileManager']:
            props=self.values(refs['propertyFileManager'],DIAG+'config/PropertyFileManager',dict(mProperties='Ljava/util/Properties;'))['mProperties']
            matches={}
            for name,known in [('coding.mask','00000000000000'+'0'*36),('coding.value','00000000000000'+'0'*36)]:
                value=self.stock_property_get(props,name)
                matches[name]={'present':bool(value),'virtual_bench_override':bool(value and self.match_stock_string(value,(known,))),
                               'zero_override':bool(value and self.match_stock_string(value,('0'*50,)))}
            out['loaded_known_overrides']=matches
        if refs['configurationManager']:
            fields=self.values(refs['configurationManager'],'de/vw/mib/configuration/internal/ConfigurationManagerImpl',dict(
                encodedVehicleProject='Ljava/lang/String;',encodedVehicleTypeForSkin0='Ljava/lang/String;',
                fallbackVehicleTypeForSkin0='Ljava/lang/String;',encodedVehicleProjectOverridden='Z',encodedVehicleBrand='I'))
            allowed=('020301006300000002000','020000000000000000000','000000000000000000000')
            for name in ('encodedVehicleProject','encodedVehicleTypeForSkin0','fallbackVehicleTypeForSkin0'):
                out[name]={'present':bool(fields[name]),'matched_known_key':self.match_stock_string(fields[name],allowed)if fields[name]else None}
            out['forced_vehicle_project']=bool(fields['encodedVehicleProjectOverridden'])
            out['brand_is_vw']=fields['encodedVehicleBrand']==2
        out['scope']='Read-only pinned JVM fields, only known coding/key equality; no arbitrary guest strings or writes'
        return out

    def profile_diagnosis_state(self,api):
        impl=DIAG+'api/impl/'
        k=self.expect(api,impl+'ASLDiagnosisAPIImpl')
        pk=self.resolved_class(k,30,impl+'DiagnosisServicesProvider')
        service=self.static_values(pk,dict(services='L'+DIAG+'diagmode/DiagnosisServices;'))['services']
        if not service:
            return dict(services_constructed=False)
        ref=self.values(service,impl+'DiagnosisServicesImpl',dict(
            adaptionApi='Lde/vw/mib/asl/framework/api/diagnosis/AdaptionApi;'))['adaptionApi']
        out=dict(services_constructed=True,adaption_api_present=bool(ref))
        if ref:
            target=DIAG+'TargetDiagnosis'
            self.expect(ref,target)
            out['original_adaption_target']=True
            provider=self.values(ref,target,dict(diagnosisDataProviderMib3='L'+DIAG+'DiagnosisDataProvider;'))['diagnosisDataProviderMib3']
            out['diagnosis_data_provider_constructed']=bool(provider)
            if provider:
                out['coding_selection']=self.coding_selection_state(provider)
                try:out['coding_backend']=self.coding_backend_state(provider)
                except (ValueError,KeyError)as e:out['coding_backend']={'unverified':str(e)}
        return out

    def asl_startup_state(self,activator):
        k=self.expect(activator,ASL+'activators/AslActivator')
        out={}

        def observe(label,reader):
            try:
                out[label]=reader()
            except (OSError,ValueError) as exc:
                out[label]=dict(unverified=str(exc))

        def target_state():
            target=ASL+'AslTargetStartup'
            tk=self.resolved_class(k,266,target)
            obj=self.static_values(tk,dict(aslTargetStartupInstance='L'+target+';'))['aslTargetStartupInstance']
            if not obj:
                return dict(constructed=False)
            row=dict(constructed=True,**self.bools(obj,target,'isInitialized','startUpCalled'))
            raw=self.values(obj,target,dict(lastKnownHmiPowerState='I'))['lastKnownHmiPowerState']
            row['lastKnownHmiPowerState']=raw-(1<<32) if raw>>31 else raw
            try:
                mk=self.resolved_class(tk,424,ASL+'StartupServiceManager')
                flags=self.static_values(mk,dict(startupDone='Z',swdlPowerstateDetected='Z',
                                                nonCustomerSwdlIsInProgress='Z',isHighActivatorStarted='Z'))
                if any(value not in (0,1) for value in flags.values()):
                    raise ValueError('Original ASL startup flags are not boolean')
                row.update({name:bool(v) for name,v in flags.items()})
            except (OSError,ValueError) as exc:
                row['manager_flags_unverified']=str(exc)
            return row

        observe('target',target_state)
        pk=self.resolved_class(k,269,ASL_IMPL+'StartupServicesProvider')
        services=self.static_values(pk,dict(services='L'+ASL+'StartupServices;'))['services']
        out['services_constructed']=bool(services)
        if not services:
            return out
        refs=self.values(services,ASL_IMPL+'StartupServicesImpl',dict(
            startupStateService='L'+ASL_IMPL+'StartupStateServiceImpl;',
            startupAPI='L'+ASL_IMPL+'StartupAPIServiceImpl;',
            dsiProxy='Lde/vw/mib/asl/framework/api/dsiproxy/DSIProxy;',
            diagnosisAPI='Lde/vw/mib/asl/framework/api/diagnosis/ASLDiagnosisAPI;',
            persistenceApi='Lde/vw/mib/asl/framework/api/persistence/ASLPersistenceAPI;'))
        out['startup_api_constructed']=bool(refs['startupAPI'])
        state=ASL_IMPL+'StartupStateServiceImpl'
        observe('state',lambda:dict(self.values(refs['startupStateService'],state,dict(startupState='I')),
                                   **self.bools(refs['startupStateService'],state,'bundleLateLoadDone')))
        if refs['dsiProxy']:
            observe('dsi',lambda:self.root_dsi_state(refs['dsiProxy'],refs['startupAPI']))
        if refs['diagnosisAPI']:
            observe('profile_diagnosis',lambda:self.profile_diagnosis_state(refs['diagnosisAPI']))
        if refs['persistenceApi']:
            def persistence():
                api=self.expect(refs['persistenceApi'],ASL_PERSIST+'api/impl/ASLPersistenceAPIImpl')
                p=ASL_PERSIST+'internal/servicesprovider/PersistenceServices'
                provider=self.resolved_class(api,128,p+'Provider')
                obj=self.static_values(provider,dict(persistenceServices='L'+p+';'))['persistenceServices']
                if not obj:
                    return dict(constructed=False)
                api_start='Lde/vw/mib/asl/framework/api/persistence2/startup/'
                fields=self.values(obj,p+'Impl',dict(persistenceStartupService=api_start+'PersistenceStartupService;',
                    persistenceStateChangeService=api_start+'PersistenceStateChangeService;'))
                gate=fields['persistenceStartupService']
                row=dict(constructed=bool(gate))
                if gate:
                    row.update(self.bools(gate,PERSIST_IMPL+'startup/PersistenceStartupServiceImpl',
                                          'isInitialStartupProfileSet'))
                state=fields['persistenceStateChangeService']
                if state:
                    label=self.values(state,PERSIST_IMPL+'startup/PersistenceStateChangeServiceImpl',dict(
                        currentPersistenceState='Ljava/lang/String;'))['currentPersistenceState']
                    row['state_service_label']=self.match_stock_string(label,(
                        'uninitialized','WaitingForDatabase','FlashOk','Idle','Diagnosis')) if label else None
                try:
                    fw=self.resolved_class(k,47,'de/vw/mib/asl/framework/internal/framework/ServiceManager')
                    row['main_target']=self.pers_hsm_state(fw,state)
                except (OSError,ValueError) as exc:
                    row['main_target']=dict(unverified=str(exc))
                return row
            observe('persistence',persistence)
        return out

    def lsc_bullhorn_target(self,ref):

        target='de/vw/mib/asl/framework/internal/lsc/AslTargetLscTNGBullhorn'
        k=self.klass(ref)
        name=self.kname(k)
        if name==target:
            return ref,'direct'
        proxy='java/lang/reflect/Proxy'
        leaf=name.rsplit('/',1)[-1]
        number=leaf.removeprefix('$Proxy')
        base=self.num(k+self.field('Klass::_super'))
        if (not leaf.startswith('$Proxy') or not number.isascii() or not number.isdecimal()
                or not base or self.kname(base)!=proxy):
            raise ValueError('Original LSC service is not the bound Java async proxy')
        h=self.values(ref,proxy,dict(h='Ljava/lang/reflect/InvocationHandler;'))['h']
        handler=THREAD+'AsyncServiceFactoryImpl$AsyncServiceImpl'
        self.expect(h,handler)
        service=self.values(h,handler,dict(service='Ljava/lang/Object;'))['service']
        self.expect(service,target)
        return service,'original_async_proxy'

    def lsc_prerequisites(self,bullhorn):

        native='de/vw/mib/asl/framework/internal/'
        pkg=native+'impl/lsc/initial/'
        trigger=pkg+'InitialLscTrigger'
        prereq=pkg+'InitialLscPrerequisit'
        abt=native+'impl/lsc/AbtDimensionsHandler'
        bk=self.expect(bullhorn,native+'lsc/AslTargetLscTNGBullhorn')
        sent=self.static_values(bk,dict(initialLscWasSent='Z'))['initialLscWasSent']
        if sent not in (0,1):
            raise ValueError('Original initial LSC flag is not boolean')
        result=dict(status='observed',initial_lsc_sent=bool(sent))
                                                                             
        try:
            tk=self.resolved_class(bk,1304,trigger)
                                                                         
                                                                      
            try:
                pk=self.resolved_class(tk,58,prereq)
            except (OSError,ValueError):
                pk=self.resolved_class(bk,1092,prereq)
        except (OSError,ValueError) as exc:
            result['trigger_unverified']=str(exc)
            return result
        obj=self.static_values(tk,dict(INSTANCE='L'+trigger+';'))['INSTANCE']
        self.expect(obj,trigger)
        fields=self.values(obj,trigger,dict(prerequisits='Ljava/util/List;',initialLscCall='Ljava/lang/Runnable;',
                                            timeoutTimer='Lde/vw/mib/timer/ThreadedTimer;'))
        pending=fields['prerequisits']
        self.expect(pending,'java/util/ArrayList')
        items=self.values(pending,'java/util/ArrayList',dict(size='I',elementData='[Ljava/lang/Object;'))
        size,storage=items['size'],items['elementData']
        cap,header=self.object_array(storage,'[Ljava/lang/Object;')
        if not 0<=size<=6 or not size<=cap<=32:
            raise ValueError('Original LSC prerequisite list size outside its contract')
        names=('CONTROLLER','DEFAULT_LANGUAGE','ABT_DIMENSIONS','DEFAULT_SKIN','VISIBLE_LANGUAGES','INITIAL_LANGUAGE')
        identities=self.static_values(pk,{n:'L'+prereq+';' for n in names})
        if not all(identities.values()) or len(set(identities.values()))!=len(names):
            raise ValueError('Original LSC prerequisite enum identity differs')
        inverse={v:k for k,v in identities.items()}
        members=[self.ref(storage+header+i*self.oop_width) for i in range(size)]
        if any(v not in inverse for v in members) or len(set(members))!=len(members):
            raise ValueError('Original LSC prerequisite list member is not a unique stock enum')
        result.update(pending=[inverse[v] for v in members],initial_callback_present=bool(fields['initialLscCall']),
                      timeout_timer_present=bool(fields['timeoutTimer']),fallback_triggered=[n for n in names
                          if self.bools(identities[n],prereq,'fallbackTriggered')['fallbackTriggered']])
        try:
            ak=self.resolved_class(bk,470,abt)
            ao=self.static_values(ak,dict(INSTANCE='L'+abt+';'))['INSTANCE']
            self.expect(ao,abt)
            result['abt_dimensions_present']=bool(self.values(ao,abt,dict(
                abtDimensions='Lde/vw/mib/display/AbtDimensions;'))['abtDimensions'])
        except (OSError,ValueError) as exc:
            result['abt_dimensions_unverified']=str(exc)
        return result

    def lsc_state(self,activator):

        pkg='de/vw/mib/lsctng/internal/'
        cpkg=pkg+'classloader/'
        lsc=pkg+'LscControllerImpl'
        loader=cpkg+'CachingClassloader'
        lang='de/vw/mib/language/Language'
        lsig='L'+lang+';'
        cow='de/vw/mib/collections/copyonwrite/CowList'
        csig='L'+cow+';'
        act=pkg+'Activator'
        self.expect(activator,act)
        obj=self.values(activator,act,dict(lscController='L'+lsc+';',skinClassLoader='L'+loader+';',
                            services='L'+pkg+'Services;',
                            appLscListener='Lde/vw/mib/lsctng/AppLscListener;'))
        out=dict(status='observed',app_listener_present=bool(obj['appLscListener']),
                 controller_present=bool(obj['lscController']),loader_present=bool(obj['skinClassLoader']),
                 **self.bools(activator,act,'lscControllerRegistered'))
        if obj['services']:
            bullhorn=self.values(obj['services'],pkg+'Services',dict(
                aslLscBullhorn='Lde/vw/mib/lsctng/AslLscBullhorn;'))['aslLscBullhorn']
            out['asl_bullhorn_present']=bool(bullhorn)
            if bullhorn:
                try:
                    target,binding=self.lsc_bullhorn_target(bullhorn)
                    out['bullhorn_binding']=binding
                    out['initial_prerequisites']=self.lsc_prerequisites(target)
                except (OSError,ValueError) as exc:
                    out['initial_prerequisites']=dict(status='unverified',reason=str(exc))
        controller=obj['lscController']
        if not controller:
            return out
        self.expect(controller,lsc)
        ctl=self.values(controller,lsc,dict(lastRqLanguage=lsig,rqLanguage=lsig,lastRqSkin='I',rqSkin='I',
                                            lscHighPrioListenerMediators=csig))
        def signed32(v):
            return v-(1<<32) if v & (1<<31) else v

        def language(ref):
            if not ref:
                return dict(state='null')
            k=self.expect(ref,lang)
            names=('NO_LANG','ZH_CN','EN_GB','EN_US','DE_DE')
            known=self.static_values(k,{n:lsig for n in names})
            if not known['NO_LANG']:
                raise ValueError('Original NO_LANG enum is not initialized')
            matches=[n for n in names if ref==known[n]]
            if len(matches)>1:
                raise ValueError('Original language enum singletons alias')
            ordinal=self.values(ref,'java/lang/Enum',dict(ordinal='I'))['ordinal']
            if not 0<=ordinal<128:
                raise ValueError('Original language enum ordinal outside the expected range')
            return dict(state=matches[0] if matches else 'other_stock_enum',ordinal=ordinal)

        out.update(last_request_language=language(ctl['lastRqLanguage']),request_language=language(ctl['rqLanguage']),
                   last_request_skin=signed32(ctl['lastRqSkin']),request_skin=signed32(ctl['rqSkin']),
                   **self.bools(controller,lsc,'isLscChangeRunning','lscQueued'))
        node=ctl['lscHighPrioListenerMediators']
        remaining=None
        visited=set()
        listeners=[]
        while node:
            if node in visited or len(visited)>24:
                raise ValueError('Original high-priority LSC listener list is cyclic or too large')
            visited.add(node)
            self.expect(node,cow)
            record=self.values(node,cow,dict(head='Ljava/lang/Object;',tail=csig,size='I'))
            size=record['size']
            if size>24 or (remaining is not None and size!=remaining):
                raise ValueError('Original LSC listener list size does not descend')
            if not size:
                if record['head'] or record['tail']:
                    raise ValueError('Original LSC listener list has an invalid empty sentinel')
                break
            if not record['head'] or not record['tail']:
                raise ValueError('Original LSC listener list is truncated')
            remaining=size-1
            mediator=record['head']
            med=pkg+'mediator/SimpleLscListenerMediator'
            if self.kname(self.klass(mediator))==med:
                listener=self.values(mediator,med,dict(listener='Lde/vw/mib/lsctng/LscListener;'))['listener']
                if listener and self.kname(self.klass(listener))==pkg+'GuiLscListener':
                    listeners.append(listener)
            node=record['tail']
        if len(listeners)>1:
            raise ValueError('Multiple GuiLscListener instances in original high-priority list')
        out['gui_listener_present']=bool(listeners)
        if not listeners:
            return out
        gui=listeners[0]
        fields=self.values(gui,pkg+'GuiLscListener',dict(currentLanguage=lsig,
                                classloader='L'+cpkg+'SkinClassLoaderInternal;',
                                appLscListener='Lde/vw/mib/lsctng/AppLscListener;'))
        if (not fields['classloader'] or fields['classloader']!=obj['skinClassLoader'] or
                not fields['appLscListener'] or fields['appLscListener']!=obj['appLscListener']):
            raise ValueError('Original GuiLscListener owner or app listener identity differs')
        self.expect(fields['classloader'],loader)
        delegate=self.values(fields['classloader'],loader,dict(
            skinClassLoader='L'+cpkg+'CachableSkinClassLoaderInternal;'))['skinClassLoader']
        kind=self.kname(self.klass(delegate)) if delegate else ''
        if kind not in (cpkg+'JarSkinClassLoader',cpkg+'JxeSkinClassLoader'):
            raise ValueError('Original skin loader delegate class is not supported')
        skin=signed32(self.values(delegate,kind,dict(skinId='I'))['skinId'])
        out.update(current_language=language(fields['currentLanguage']),skin_with_style=skin,
                   loader=kind.rsplit('/',1)[1],
                   **self.bools(gui,pkg+'GuiLscListener','isInitialLanguageChangeFinished','areViewResourcesReleased'))
        return out

    def osgi_state(self,startup,classname):
        obj=self.values(startup,classname,dict(mibOsgi='Lde/vw/mib/osgi/MIBOsgi;'))['mibOsgi']
        cl=OSGI+'MIBOsgiImpl'
        registry=self.values(obj,cl,dict(bundleRegistry='L'+OSGI+'BundleRegistry;'))['bundleRegistry']
        signature='[L'+OSGI+'BundleInfo;'
        a=self.values(registry,OSGI+'BundleRegistry',dict(managedBundles=signature))['managedBundles']
        n,header=self.object_array(a,signature)
                                                                     
                                                                        
        selected={'DisplayManager','RepaintManager','LscTNG','AppFramework','DesktopManager','ASL','ASL-Framework'}
        if not 1<=n<=64:
            raise ValueError('Original managed bundle list geometry differs')
        rows={}
        for index in range(n):
            info=self.ref(a+header+index*self.oop_width)
            if not info:
                continue
            fields=self.values(info,OSGI+'BundleInfo',dict(name='Ljava/lang/String;',id='I',
                       state='I',bundleActivator='Lorg/osgi/framework/BundleActivator;'))
            label=self.match_stock_string(fields['name'],selected)
            if label is None:
                continue
            if label in rows:
                raise ValueError('Duplicate original managed bundle name: '+label)
            activator=fields['bundleActivator']
            row=rows[label]=dict(index=index,id=fields['id'],state=fields['state'],activator_present=bool(activator))
            if label=='ASL' and activator:
                try:
                    row['startup']=self.asl_startup_state(activator)
                except (OSError,ValueError) as exc:
                    row['startup']=dict(unverified=str(exc))
                try:
                    row['root_dsi_osgi']=self.osgi_root_registrations(info)
                except (OSError,ValueError) as exc:
                    row['root_dsi_osgi']=dict(unverified=str(exc))
            if label=='LscTNG' and activator:
                try:
                    row['gui_readiness']=self.lsc_state(activator)
                except (OSError,ValueError) as exc:
                    row['gui_readiness']=dict(status='unverified',reason=str(exc))
            if label=='AppFramework' and activator:
                klass=self.expect(activator,APP_ACT)
                refs=self.values(activator,APP_ACT,APP_GATES)
                row.update(self.bools(activator,APP_ACT,'appLifecycleManagerRegistered'))
                row['missing_required_dependencies']=[name for name,ref in refs.items() if not ref and
                                                      name!='contentProcessor']
                try:
                    wait=self.static_values(klass,dict(WAIT_FOR_CONTENT_PROCESSOR='Z'))['WAIT_FOR_CONTENT_PROCESSOR']
                    if wait not in (0,1):
                        raise ValueError('Original AppFramework content-processor gate is not boolean')
                    row['wait_for_content_processor']=bool(wait)
                    if wait and not refs['contentProcessor']:
                        row['missing_required_dependencies'].append('contentProcessor')
                except (OSError,ValueError) as exc:
                    row['content_processor_gate_unverified']=str(exc)
                package='de/vw/mib/app/framework/internal/'
                obj=self.values(activator,APP_ACT,dict(
                    appFrameworkManager='L'+package+'AppFrameworkManager;'))['appFrameworkManager']
                row['framework_manager_constructed']=bool(obj)
                if obj:
                    lifecycle=self.values(obj,package+'AppFrameworkManager',dict(
                        appLifecycleManager='L'+package+'AppLifecycleManager;'))['appLifecycleManager']
                    row['lifecycle_manager_constructed']=bool(lifecycle)
        return dict(count=n,bundles=rows,unobserved=sorted(selected-rows.keys()))

    def desktop_screen_binding(self, handler):
        if not handler:
            return dict(handler_present=False)
        vh=DESKTOP+'viewhandler/'
        h=self.values(handler,vh+'SmartViewHandler',dict(currentState='I',targetState='I',linked='Z',
                                                         screenAreas='Ljava/util/List;'))
        delegate=self.values(handler,vh+'CacheableViewHandler',dict(
            viewHandler='Lde/vw/mib/viewmanager/internal/ViewHandler;'))['viewHandler']
        if h['linked'] not in (0,1):
            raise ValueError('Original view handler linked value is not boolean')
        result=dict(handler_present=True,delegate_present=bool(delegate),linked=bool(h['linked']),
                    current_state=h['currentState'],target_state=h['targetState'],
                    screen_area_list_present=bool(h['screenAreas']))
        if delegate:
            result['delegate_class']=self.kname(self.klass(delegate)).replace('/','.')
        if h['screenAreas']:
            items=self.values(h['screenAreas'],'java/util/ArrayList',dict(
                size='I',elementData='[Ljava/lang/Object;'))
            cap,header=self.object_array(items['elementData'],'[Ljava/lang/Object;')
            if not items['size']<=min(cap,64):
                raise ValueError('Original desktop screen area count exceeds its bound')
            areas=[]
            for i in range(items['size']):
                ref=self.ref(items['elementData']+header+i*self.oop_width)
                area=self.values(ref,DESKTOP+'screenarea/SmartScreenArea',dict(
                    state='I',screenArea='Lde/vw/mib/widgets/models/ScreenAreaModel;'))
                                                                           
                model=area['screenArea']
                observed=dict(state=area['state'],model_stored=bool(model),
                              model_exposed=bool(model) and area['state']!=2)
                if observed['model_exposed']:
                    try:
                        observed.update(self.desktop_area_children(model))
                    except (OSError,ValueError) as exc:
                        observed['children_unverified']=str(exc)
                areas.append(observed)
            result['areas']=areas
        return result

    def desktop_area_children(self, model):
        widgets='de/vw/mib/widgets/'
        self.expect(model,widgets+'ScreenArea')
        sig='[L'+widgets+'models/WidgetModel;'
        children=self.values(model,widgets+'Container',dict(children=sig))['children']
        if not children:
            return dict(child_slots=None,attached_views=0)
                                                                          
        actual=self.kname(self.klass(children))
        if not actual.startswith('[L'+widgets) or not actual.endswith(';'):
            raise ValueError('Original desktop children array is not an OEM widget array')
        count,header=self.object_array(children,actual)
        if count>128:
            raise ValueError('Original desktop area child count exceeds its bound')
        attached=0
        for i in range(count):
            child=self.ref(children+header+i*self.oop_width)
            if not child:
                continue
            cls=self.klass(child)
            for _ in range(8):
                if not cls:
                    break
                if self.kname(cls)==widgets+'View':
                    attached+=1
                    break
                cls=self.num(cls+self.field('Klass::_super'))
            else:
                raise ValueError('Original desktop widget hierarchy exceeds its bound')
        return dict(child_slots=count,attached_views=attached)

    def consumer_state(self,event):
        if not event:
            return dict(status='not_observed',reason='No retained original dispatcher event')
        consumer=self.values(event,EVENT+'AbstractEvent',dict(
            eventConsumerManager='L'+EVENT+'EventConsumerManager;'))['eventConsumerManager']
        k=self.expect(consumer,EVENT+'EventConsumerManager')
        cp,length,tags=self.pool(k)
                                                                        
                                                                              
        if length<=17 or self.num(tags+17,1)!=7:
            raise ValueError('Original event ServiceManager class constant is not resolved')
        service=self.num(cp+self.cp_size+17*8)
        if self.kname(service)!=EVENT+'ServiceManager':
            raise ValueError('Original event ServiceManager class identity differs')
        specs=dict(viewManager='Lde/vw/mib/event/consumer/ViewManagerEventConsumer;',
                   startupManager='Lde/vw/mib/event/consumer/StartupManagerEventConsumer;',
                   appStartupManager='Lde/vw/mib/event/consumer/AppStartupManagerEventConsumer;')
        objects=self.static_values(service,specs)
        result=dict(status='observed')
        for n,o in objects.items():
            row=result[n]=dict(present=bool(o))
            if o:
                name=self.kname(self.klass(o))
                row['class_name']=name.replace('/','.')
                try:
                    if n=='startupManager' and name=='de/vw/mib/startup/internal/StartupManager':
                        row.update(self.bools(o,name,'bundlesStarted','dispatcherStarted',
                                              'startUpGUIReady','speechUIReady','datapoolUpdatesEnabled'))
                        try:
                            row['managed_bundles']=self.osgi_state(o,name)
                        except (OSError,ValueError) as exc:
                            row['managed_bundles_unverified']=str(exc)
                    if n=='appStartupManager' and name=='de/vw/mib/app/framework/internal/AppLifecycleManager':
                        row.update(self.bools(o,name,'startApps','skinReady','startingApps',
                                              'startupStageDesktopAppFinished','startupStageDesktopAppReadyReached',
                                              'startupStageGuiReadyReached'))
                        row.update(self.values(o,name,dict(startupStage='I')))
                    if n=='viewManager' and name==DESKTOP+'DesktopManagerImpl':
                        row.update(self.bools(o,name,'firstViewShown','repaintEnabled','displayOn',
                                              'subAreaEntered','viewTreeIsDirty','panic'))
                        vs=self.values(o,name,dict(
                            services='L'+DESKTOP+'Services;',screen='Lde/vw/mib/widgets/models/ScreenModel;',
                            currentViewList='Ljava/util/List;'))
                        row.update(screen_present=bool(vs['screen']),view_list_present=bool(vs['currentViewList']))
                        if vs['currentViewList']:
                            try:
                                                                                    
                                                                                       
                                views=vs['currentViewList']
                                self.expect(views,'java/util/ArrayList')
                                size=self.values(views,'java/util/ArrayList',dict(size='I'))['size']
                                if size > 512:
                                    raise ValueError('Unexpected original desktop view count')
                                row['current_view_count']=size
                            except (OSError,ValueError) as exc:
                                row['current_view_count_unverified']=str(exc)
                        try:
                            handler=self.values(o,name,dict(screenViewHandler='L'+DESKTOP+
                                'viewhandler/SmartViewHandler;'))['screenViewHandler']
                            row['screen_binding']=self.desktop_screen_binding(handler)
                        except (OSError,ValueError) as exc:
                            row['screen_binding_unverified']=str(exc)
                        if vs['services']:
                            s=self.values(vs['services'],DESKTOP+'Services',dict(
                                viewCompositor='Lde/vw/mib/view/ViewCompositor;',
                                repaintManager='Lde/vw/mib/repaint/RepaintManager;'))
                            row['repaint_manager_present']=bool(s['repaintManager'])
                            if s['viewCompositor']:
                                gc=self.values(s['viewCompositor'],COMPOSITOR,dict(
                                    surface='Lde/vw/mib/graphics/surface/Surface;',
                                    g3d='Lde/vw/mib/graphics/Graphics3D;',
                                    onscreenFrameBuffer='Lde/vw/mib/graphics/framebuffer/FrameBuffer;'))
                                row['compositor_objects']={k:bool(v) for k,v in gc.items()}
                                                                                                 
                                                                                                
                                try:
                                    gr=self.values(s['viewCompositor'],COMPOSITOR,dict(
                                        config='Lde/vw/mib/graphics/GraphicsConfiguration;',
                                        displaySize='Lde/vw/mib/graphics/Dimension;'))
                                    dims=None
                                    if gr['displaySize']:
                                        dims=self.values(gr['displaySize'],'de/vw/mib/graphics/Dimension',
                                                         dict(width='I',height='I'))
                                    row['compositor_initialization']=dict(display_size=dims,
                                        config_class=self.kname(self.klass(gr['config'])).replace('/','.')
                                                     if gr['config'] else None)
                                except (OSError,ValueError) as exc:
                                    row['compositor_initialization_unverified']=str(exc)
                except (OSError,ValueError) as exc:
                    row['state_unverified']=str(exc)
        return result

    def java_threads(self,needed,*,allow_missing=False):
        limit=self.num(self.field('Threads::_number_of_threads'),4)
        a=self.num(self.field('Threads::_thread_list'))
        if not 0<limit<=192:
            raise ValueError('Original JVM thread count out of scope')
        offsets=[self.field(f) for f in ('JavaThread::_next','JavaThread::_threadObj',
                  'JavaThread::_osthread','OSThread::_thread_id')]
        found={}
        visited=set()
        while a:
            if a in visited or len(visited)>=limit or a&7:
                raise ValueError('Original JVM thread list inconsistent')
            visited.add(a)
            os=self.num(a+offsets[2])
            tid=self.num(os+offsets[3],4)
            if tid in needed:
                if tid in found:
                    raise ValueError('Duplicate original JVM TID')
                found[tid]=self.num(a+offsets[1])
            a=self.num(a+offsets[0])
        if len(visited)!=limit:
            raise ValueError('Original JVM native thread list count differs')
        if not allow_missing and set(found)!=needed:
            raise ValueError('Original JVM native/Java thread identities differ')
        return found

    def object_array(self,elements,classname):
        array_k=self.expect(elements,classname)
        layout=self.num(array_k+self.field('Klass::_layout_helper'),4)
        header=(layout>>16)&0xff
        if layout>>30!=2 or header not in (16,24) or (1<<(layout&63))!=self.oop_width:
            raise ValueError('Original array layout encoding differs')
        length=self.num(elements+self.klass_at+self.klass_width,4)
        return length,header

    def event(self,val):
        event=self.oop_base+(val<<self.oop_shift)
        classname=self.kname(self.klass(event))
        if not classname.startswith('de/vw/mib/'):
            raise ValueError('Original event slot is not an OEM MIB event')
        proof=dict(class_name=classname.replace('/','.'))
        if classname=='de/vw/mib/event/internal/StartupEventImpl':
            proof['startup_event_id']=self.values(event,classname,dict(eventId='I'))['eventId']
        elif classname==EVENT+'SystemEventImpl':
            proof['asl_system_event_id']=self.values(event,classname,dict(aslSystemEventId='I'))['aslSystemEventId']
        elif classname==EVENT+'InvocationEventImpl':
            try:
                wrapper=self.values(event,classname,dict(runnable='Ljava/util/function/Supplier;'))['runnable']
                if wrapper:
                    wrapped=self.kname(self.klass(wrapper))
                    if wrapped.startswith(DISP+'ViewEventDispatcherImpl$$Lambda$'):
                        capture=self.values(wrapper,wrapped,{'arg$1':'Ljava/lang/Runnable;'})['arg$1']
                        if capture:
                            proof['runnable_class']=self.kname(self.klass(capture)).replace('/','.')
            except (OSError,ValueError) as exc:
                proof['runnable_unverified']=str(exc)
        return proof

    def queue(self,queue,owner):
        self.expect(queue,DISP+'EventQueue')
        meta=self.values(queue,DISP+'EventQueue',dict(
            thread='Lde/vw/mib/threads/MIBThread;',internalQueue='Ljava/util/ArrayDeque;',
            messageCountTotal='I',maxQueueSize='I',queueId='B'))
        if meta['thread']!=owner:
            raise ValueError('Original event queue has a different Java thread owner')
        dq=meta['internalQueue']
        self.expect(dq,'java/util/ArrayDeque')
        v=self.values(dq,'java/util/ArrayDeque',dict(elements='[Ljava/lang/Object;',head='I',tail='I'))
        elements=v['elements']
        length,header=self.object_array(elements,'[Ljava/lang/Object;')
        h,t=v['head'],v['tail']
        if not 2<=length<=1<<16 or length&(length-1) or max(h,t)>=length:
            raise ValueError('Original Java ArrayDeque geometry differs')
        depth=(t-h)&(length-1)
        if meta['maxQueueSize']<depth or meta['messageCountTotal']<depth:
            raise ValueError('Original queue counters inconsistent with pending depth')
                                                                                     
        first=None
        if depth:
            val=self.num(elements+header+h*self.oop_width,self.oop_width)
            if not val:
                raise ValueError('Original queue head is null during a mutation')
            first=self.event(val)
        return dict(depth=depth,capacity=length,enqueued_since_creation=meta['messageCountTotal'],
                    max_depth=meta['maxQueueSize'],queue_id=meta['queueId'],first_pending=first)

    def dispatch(self,thread,comm):
        wrapper_name,dispatcher_name,queue_names=ROLES[comm]
        self.expect(thread,'java/lang/Thread')
        owner=self.values(thread,'java/lang/Thread',dict(target='Ljava/lang/Runnable;'))['target']
        self.expect(owner,THREAD+wrapper_name)
        wrapper=self.values(owner,THREAD+'AbstractMIBThread',dict(
            thread='Ljava/lang/Thread;',runnable='Lde/vw/mib/threads/MIBRunnable;',suspended='Z'))
        if wrapper['thread']!=thread or wrapper['suspended'] not in (0,1):
            raise ValueError('Original Java thread wrapper backreference differs')
        dispatcher=wrapper['runnable']
        self.expect(dispatcher,DISP+dispatcher_name)
        if self.values(dispatcher,DISP+'AbstractDispatcher',dict(
                thread='Lde/vw/mib/threads/MIBThread;'))['thread']!=owner:
            raise ValueError('Original dispatcher does not point to its actual owner')
        queues=self.values(dispatcher,DISP+dispatcher_name,{k:'L'+DISP+'EventQueue;' for k in queue_names})
        out=dict(suspended=bool(wrapper['suspended']),queues={k:self.queue(v,owner) for k,v in queues.items()})
        if dispatcher_name=='MainDispatcher':
            try:
                signature='[Lde/vw/mib/event/internal/AbstractEvent;'
                fields=self.values(dispatcher,DISP+dispatcher_name,
                                   dict(lastEvents=signature,lastEventsIndex='I'))
                elements=fields['lastEvents']
                length,header=self.object_array(elements,signature)
                ix=fields['lastEventsIndex']
                if length!=15 or ix>=length:
                    raise ValueError('Original main event history extent differs')
                rows=[]
                retained=0
                for offset in range(length):
                    slot=(ix+offset)%length
                    value=self.num(elements+header+slot*self.oop_width,self.oop_width)
                    if value:
                        rows.append(self.event(value))
                        retained=self.oop_base+(value<<self.oop_shift)
                out['dispatched_type_history']=rows
            except (OSError,ValueError) as exc:
                out['dispatch_history_unverified']=str(exc)
            else:
                try:
                    out['consumer_state']=self.consumer_state(retained)
                except (OSError,ValueError) as exc:
                    out['consumer_state']=dict(status='not_verified',reason=str(exc))
        return out


def inspect_hmi_queues(pgd, maps, threads, read_user):

    bases=[a for a,b,f,file in maps if f&1 and file==('libjvm.so',0)]
    if len(bases)!=1:
        raise ValueError('Original mapped JVM text base is not unique')
    selected={}
    for name in ROLES:
        matches=[r for r in threads if r['comm']==name]
        if len(matches)>4 or len({r['tid'] for r in matches})!=len(matches):
            raise ValueError('Original dispatcher candidate set is inconsistent: '+name)
        if matches:
            selected[name]=matches
    if not selected:
        return dict(status='not_observed',reason='Neither original dispatcher thread is live')
    layout=load_layout()
    jvm=JVM(lambda a:read_user(pgd,a),bases[0],maps,layout)
    listeners=[t for t in threads if t['comm']=='DSIListener']
    listener=listeners[0] if len(listeners)==1 else None
                                                                          
                                                                         
    jt=jvm.java_threads({r['tid'] for rows in selected.values() for r in rows},allow_missing=True)
    result={}
    for name,rows in selected.items():
        matched=[]
        rejected=[]
        for t in rows:
            if t['tid'] not in jt:
                rejected.append('Original TID absent from consistent JVM list')
                continue
            try:
                matched.append(dict(tid=t['tid'],linux_state=t['state'],user_syscall=t.get('user_syscall'),
                                    **jvm.dispatch(jt[t['tid']],name)))
            except (OSError,ValueError) as exc:
                rejected.append(str(exc))
        if len(matched)==1:
            result[name]=dict(matched[0],same_name_candidates=len(rows))
        else:
            result[name]=dict(status='not_verified',same_name_candidates=len(rows),
                              reason='Ambiguous original dispatcher owners' if matched else '; '.join(rejected))
    out=dict(status='observed' if len(result)==len(ROLES) and all('queues' in r for r in result.values()) else 'partial',
             jvm_file_sha256=layout['sha256'],threads=result,
             missing_linux_roles=sorted(set(ROLES)-set(selected)),
             scope='Pinned JVM metadata; owned OEM queues and selected original HMI, ASL and DSI readiness flags')
    if listener:
        try:
            tid=listener['tid']
            worker_ids={t['tid'] for t in threads if t['comm']=='DSIWorker-8'}
            worker=None
            if len(worker_ids)==1:
                try:
                    worker=next(iter(jvm.java_threads(worker_ids).values()))
                except (OSError,ValueError):
                    pass
            out['dsi_listener']=dict(tid=tid,**jvm.dsi_listener_state(jvm.java_threads({tid})[tid],worker))
        except (OSError,ValueError) as exc:
            out['dsi_listener']=dict(unverified=str(exc))
    return out
