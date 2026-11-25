from pathlib import Path
import ast,json,sys
from html.parser import HTMLParser
r=Path(sys.argv[1]).resolve()if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
try:
 source=(r/'tools/hvf_dashboard.py').read_text();tree=ast.parse(source);page=next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='PAGE'for t in n.targets))
 class Tags(HTMLParser):
  def __init__(self):super().__init__();self.ids=[];self.frames=[]
  def handle_starttag(self,tag,attrs):
   a=dict(attrs)
   if 'id'in a:self.ids.append(a['id'])
   if tag=='iframe':self.frames.append(a)
 tags=Tags();tags.feed(page)
 print(json.dumps(dict(native_kernel_panel_present='native-frame'in tags.ids,primary_iframe_count=len(tags.frames),android_present='android-frame'in tags.ids,ic_present='ic-frame'in tags.ids,hud_present='hud-frame'in tags.ids)))
 assert 'native-frame'not in tags.ids and 'native-frame-note'not in tags.ids and 'originalLinuxFrame'not in page
 assert '/api/native-linux-frame.png'not in page and '/api/native-touch'not in page and 'refreshOriginalLinuxFrame'not in page
 assert len(tags.frames)==1 and tags.frames[0]['src']=='__CENTER__'
 for name in ['android-frame','ic-frame','hud-frame','linux-command','android-command']:assert name in tags.ids
 assert "add_get('/api/native-linux-frame.png',native_linux_frame)"in source and "add_post('/api/native-touch',native_touch)"in source
 print('DASHBOARD_NO_DUPLICATE_LINUX_PASS removed_native_kernel_display=1 no_browser_polling_or_touch_for_removed_panel=1 primary_center_android_ic_hud_dual_consoles_preserved=1 native_diagnostic_APIs_preserved=1');raise SystemExit(0)
except Exception as e:print(json.dumps(dict(exit=2,error=repr(e))));raise SystemExit(2)
