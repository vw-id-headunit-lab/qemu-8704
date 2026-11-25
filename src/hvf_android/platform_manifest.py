from pathlib import Path
import io,xml.etree.ElementTree as ET
from dissect.extfs import ExtFS
from media import LogicalImage
class Reader(io.RawIOBase):
 def __init__(self,image):self.image=image;self.pos=0
 def readable(self):return True
 def seekable(self):return True
 def tell(self):return self.pos
 def seek(self,n,whence=0):
  n=n if whence==0 else self.pos+n if whence==1 else self.image.size+n
  if not 0<=n<=self.image.size:raise ValueError('Outside original vendor partition')
  self.pos=n;return n
 def read(self,n=-1):
  n=self.image.size-self.pos if n<0 else min(n,self.image.size-self.pos)
  b=self.image.read(self.pos,n);self.pos+=len(b);return b

def software_graphics_manifest(source):
 image=LogicalImage(source,'vendor_a')
 try:tree=ET.fromstring(ExtFS(Reader(image)).get('/etc/vintf/manifest.xml').open().read())
 finally:image.close()
 for kind,interface in [('allocator','IAllocator'),('mapper','IMapper')]:
  blocks=[h for h in tree.findall('hal')if h.findtext('name')=='android.hardware.graphics.'+kind]
  if len(blocks)!=1:raise ValueError('Expected one original graphics '+kind+' declaration')
  h=blocks[0]
  for child in list(h):
   if child.tag not in ('name','transport'):h.remove(child)
  transport=h.find('transport');transport.text='hwbinder'if kind=='allocator'else'passthrough'
  transport.attrib.clear()
  if kind=='mapper':transport.set('arch','64')
  ET.SubElement(h,'version').text='2.0';iface=ET.SubElement(h,'interface')
  ET.SubElement(iface,'name').text=interface;ET.SubElement(iface,'instance').text='default'
  ET.SubElement(h,'fqname').text='@2.0::'+interface+'/default'
 return ET.tostring(tree,encoding='utf-8',xml_declaration=True)
