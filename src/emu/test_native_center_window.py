
import argparse
import copy
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import runpy
import statistics
import tempfile
import time
import unittest

import native_center_window as window


class WindowTests(unittest.TestCase):
 def test_letterbox(self):
  self.assertEqual(window.panel_point(0,0,1560,736),None)
  self.assertEqual(window.panel_point(0,36,1560,736),(0,0))
  self.assertEqual(window.panel_point(1559,735,1560,736),(1559,699))
  self.assertEqual(window.panel_point(-10,1000,1560,736,True),(0,699))
  self.assertIsNone(window.panel_point(10,10,0,0))
  x,y,ww,hh=window.viewport(1200,900)
  self.assertEqual(window.panel_point(x+ww//2,y+hh//2,1200,900),(780,350))
 def test_clock(self):
  clock=window.PresenterClock()
  for i in range(31):clock.presented(str(i),i/30,True)
  self.assertEqual(len(clock.times),31)
  clock.presented('30',1.01,True);clock.presented('hidden',1.02,False)
  self.assertEqual(len(clock.times),31)
  self.assertEqual(clock.fps(4),0)
 def test_stride_and_alpha(self):
  row=bytes([11,22,33,255])*1560;proof={'width':1560,'height':700,'pitch':6256,'format':'AR24'}
  self.assertEqual(window.dib_pixels((row+bytes(16))*700,proof),row*700)
  with self.assertRaises(ValueError):window.dib_pixels(bytes(6256*700),proof)
  self.assertEqual(window.dib_pixels(bytes(6256*700),proof,True),bytes(6240*700))
  proof['original_wod_blend']={'verified':True,'plane_alpha8':254}
  with self.assertRaises(ValueError):window.dib_pixels((row+bytes(16))*700,proof,True)
 def test_original_touch(self):
  events=[(0,0,'down',0),(780,350,'move',10),(1559,699,'up',20)]
  _,commands=window.touch.gesture_commands(events,'abcdef')
  self.assertEqual(len(commands),4)
  for x,y,phase,tick in events:self.assertEqual(len(window.touch.packet(x,y,phase,tick)),30)


def reader_fixture(path):
 spec=importlib.util.spec_from_file_location('checked_reader',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 supports='raw_pixels'in inspect.signature(module.NativeCenterReader.sample).parameters
 with tempfile.TemporaryDirectory()as directory:
  session=Path(directory);(session/'native-runtime-state.json').write_text(json.dumps({'running':True,'qemu_pid':123}))
  reader=object.__new__(module.NativeCenterReader);reader.session=session;reader.pid=123;reader.last_raw=None;reader.last_image=None
  proof={'status':'active_scanout_pages_verified','framebuffer':123,'format':'XR24'}
  calls={'encode':0};raw=b'\x01\x02\x03\xff';reader.proof=lambda:copy.deepcopy(proof);reader.physical=None
  def render(a,b):calls['encode']+=1;return b'PNG',{'png_sha256':'fixture'}
  reader.scan={'read_active_raster':lambda a,b:raw,'render_active_raster':render}
  first=reader.sample(raw_pixels=True)if supports else reader.sample()
  raw_result={'transport':'BGRX'if first[0]==raw else 'PNG','encode_calls':calls['encode']}
  calls['encode']=0;default=reader.sample();assert default[0]==b'PNG'
  reads=iter([raw,b'race']);reader.scan['read_active_raster']=lambda a,b:next(reads)
  try:reader.sample(raw_pixels=True)if supports else reader.sample()
  except ValueError:race_rejected=True
  else:race_rejected=False
  assert race_rejected
  (session/'integrity.json').write_text('{}')
  try:reader.sample(raw_pixels=True)if supports else reader.sample()
  except ValueError:terminal_rejected=True
  else:terminal_rejected=False
  assert terminal_rejected
  reader.handle=None
  return dict(raw_result,default_transport='PNG',racing_raster_rejected=race_rejected,terminal_session_rejected=terminal_rejected)


def benchmark(path):
 from PIL import Image
 with Image.open(path)as image:rgb=image.convert('RGB');raw=rgb.tobytes('raw','BGRX')
 proof={'width':1560,'height':700,'pitch':6240,'format':'XR24'}
 scan=runpy.run_path(str(window.NativeCenterReader.__init__.__globals__['BASE']/'native-linux-scanout.py'))
 encoded,_=scan['render_active_raster'](proof,raw)
 import io
 with Image.open(io.BytesIO(encoded))as check:assert check.tobytes()==rgb.tobytes()
 assert Image.frombytes('RGB',(1560,700),window.dib_pixels(raw,proof),'raw','BGRX').tobytes()==rgb.tobytes()
 samples={}
 for name,fn in [('png_encoding',lambda:scan['render_active_raster'](proof,raw)),('raw_dib_preparation',lambda:window.dib_pixels(raw,proof))]:
  times=[]
  for _ in range(30):start=time.perf_counter();fn();times.append((time.perf_counter()-start)*1000)
  samples[name+'_median_ms']=statistics.median(times)
 return dict(samples,input=str(path.resolve()),png_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
  identical_rgb=True,scope='Retained original static frame; preparation CPU only, not live FPS or total pipeline latency')


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reader',type=Path);parser.add_argument('--image',type=Path);args=parser.parse_args()
 if args.reader:print(json.dumps(reader_fixture(args.reader)));return
 if args.image:print(json.dumps(benchmark(args.image)));return
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(WindowTests))
 raise SystemExit(0 if result.wasSuccessful()else 1)


if __name__=='__main__':main()
