"""运行逻辑回归：临时 SQLite、真实图像/模型脚本及本地 Modbus TCP。"""
import asyncio
import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from backend import config, db
from backend.engine import executor as ex
from backend.services import camera as cam, models as ms


def node(id, kind, target=None, params=None, dynamic=None):
    return {'id': id, 'data': {'nodeType': kind, 'params': params or {},
            'ports': {'builtIn': target, 'dynamic': dynamic or []}}}


def content(value):
    return {'type': 'content', 'ref': value}


def ref(value):
    return {'type': 'ref', 'ref': value}


MANIFEST = {'manifestVersion': 2, 'modelId': 'pixel-check', 'name': '像素检测', 'version': '1',
    'script': 'infer.py', 'weights': ['weights/value.json'],
    'tasks': [{'key': 'detection', 'type': 'detection', 'name': '检测', 'labels': [{'label': 'red', 'name': '红色'}]}],
    'params': [{'name': 'threshold', 'type': 'int', 'default': 100, 'min': 0, 'max': 255}]}
SCRIPT = '''import json
state = None
def load(ctx):
    global state
    with open(ctx['weights'][0]) as stream:
        state = json.load(stream)
def run(image, args):
    found = bool(image[0,0,0] > args['threshold'] and state['enabled'])
    regions = [{'label':'red','labelName':'红色','score':1.0,'bbox':[0,0,1,1]}] if found else []
    return {'detection:red:detected':found,'detection:red:count':len(regions),'detection:red:regions':regions}
def unload():
    global state
    state = None
'''


def package(manifest=None, script=SCRIPT, extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest or MANIFEST))
        z.writestr('infer.py', script)
        z.writestr('weights/value.json', '{"enabled":true}')
        for path, value in (extra or {}).items():
            z.writestr(path, value)
    return buf.getvalue()


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('DATA_DIR', 'MODELS_DIR', 'FRAMES_DIR', 'RESULTS_DIR', 'LOGS_DIR', 'UPLOADS_DIR'):
            p = patch.object(config, name, self.root / name.lower())
            p.start(); self.addCleanup(p.stop)
        for module, name, value in ((db, 'DB_PATH', self.root / 'db.sqlite'), (ms, 'MODELS_DIR', config.MODELS_DIR),
                (cam, 'FRAMES_DIR', config.FRAMES_DIR), (ex, 'RESULTS_DIR', config.RESULTS_DIR)):
            p = patch.object(module, name, value); p.start(); self.addCleanup(p.stop)
        db.init_db()
        with db.connect() as c:
            c.execute('DELETE FROM flows'); c.execute('DELETE FROM cameras')
        self.models = ms.ModelService()
        self.camera = cam.CameraService()
        self.manager = ex.WorkflowManager(self.camera, self.models)
        self.events = []
        async def broadcast(channel, event):
            self.events.append(event)
        p = patch.object(ex.event_hub, 'broadcast', broadcast); p.start(); self.addCleanup(p.stop)
        self.img = np.zeros((3, 4, 3), dtype=np.uint8); self.img[0, 0] = [0, 0, 255]
        self.image_path = self.root / 'red.png'; cv2.imwrite(str(self.image_path), self.img)

    def flow(self, nodes):
        self.manager.save_flow('f', {'nodes': nodes})

    def session(self, nodes=()):
        return ex.ExecutionSession('r', 'f', 'debug', {n['id']: n for n in nodes})

    async def test_step_advances_once_and_writes_only_at_end(self):
        self.flow([node('s','start','a'),node('a','storage','b'),node('b','storage')])
        run = await self.manager.start_run('f', 'step'); await self.manager._task
        self.assertEqual(self.manager.active_info()['executed'], ['a'])
        with db.connect() as c: self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
        await self.manager.step(run); await self.manager._task
        self.assertIsNone(self.manager.active_info())
        self.assertEqual([e['nodeId'] for e in self.events if e['type']=='node.started'], ['a','b'])
        self.assertEqual(sum(e['type']=='run.started' for e in self.events), 1)
        with self.assertRaises(RuntimeError): await self.manager.stop(run)

    async def test_stop_before_first_node_writes_no_record(self):
        self.flow([node('s','start','a'),node('a','storage')])
        run = await self.manager.start_run('f'); await self.manager.stop(run); await self.manager._task
        self.assertFalse(any(e['type']=='node.started' for e in self.events))
        self.assertEqual(self.events[-1]['type'], 'run.stopped')
        with db.connect() as c: self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],0)

    async def test_paused_stop_invalidates_run(self):
        self.flow([node('s','start','a'),node('a','storage','b'),node('b','storage')])
        run=await self.manager.start_run('f','step'); await self.manager._task
        await self.manager.stop(run)
        with self.assertRaises(RuntimeError): await self.manager.step(run)
        with db.connect() as c: self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],0)

    def test_graph_rejects_missing_start_target_cycles_duplicates_and_forward_refs(self):
        cases = [[node('s','start','missing')], [node('s','start','a'),node('a','storage','s')],
            [node('s','start','a'),node('a','storage','b'),node('b','storage','a')],
            [node('s','start'),node('s','storage')],
            [node('s','start','m'),node('m','model','c',{'image':ref('c.Image')}),node('c','capture')]]
        for nodes in cases:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError): self.manager.validate_flow(nodes)

    async def test_missing_or_non_bool_branch_reference_is_error(self):
        n=node('b','branch',params={'conditions':[{'id':'yes','value':ref('m.flag')}]},dynamic=[{'id':'yes','targetId':None}])
        for value in ('missing', 1, 'true', None):
            session=self.session([n])
            if value!='missing': session.context['m.flag']=value
            with self.subTest(value=value), self.assertRaises(RuntimeError): await self.manager._node_branch(session,'b',n['data']['params'])

    async def test_branch_does_not_create_business_result(self):
        n=node('b','branch',params={'conditions':[{'id':'yes','value':content(True)}]},dynamic=[{'id':'yes','targetId':None}])
        session=self.session([n]); await self.manager._node_branch(session,'b',n['data']['params'])
        self.assertFalse(hasattr(session,'result'))

    async def test_display_multiple_shapes_text_zero_and_hidden_shape(self):
        s=self.session(); s.objects['c.Image']=self.img
        shape={'content':content([{'bbox':[1,2,3,4],'label':'red'}]),'status':content(False),'style':{'lineWidth':3,'showLabel':True}}
        params={'image':ref('c.Image'),'shapes':[dict(shape,visible=content(False)),shape,dict(shape,status=content(True))],
            'texts':[{'content':content(0),'status':content(True)},{'content':content('NG'),'status':content(False)}]}
        await self.manager._node_display(s,'d',params)
        annotations=s.windows['1']['annotations']
        self.assertEqual(len(annotations),4)
        self.assertEqual(annotations[0]['width'],3)
        self.assertEqual(annotations[0]['lineWidth'],3)
        self.assertEqual(annotations[2]['content'],'0')
        self.assertFalse(hasattr(s,'result'))

    async def test_display_status_only_controls_annotation_color(self):
        s=self.session(); s.objects['c.Image']=self.img
        await self.manager._node_display(s,'d',{'image':ref('c.Image'),'shapes':[], 'texts':[
            {'content':content('用户配置的 NG'),'status':content(False)},
            {'content':content('用户配置的 OK'),'status':content(True)}]})
        self.assertEqual([item['color'] for item in s.windows['1']['annotations']], ['#F76965','#27C346'])
        self.assertFalse(hasattr(s,'result'))

    async def test_storage_record_has_no_business_result(self):
        result=await self.manager._node_storage(self.session(),'save',{})
        payload=json.loads(Path(result['outputs']['path']).read_text(encoding='utf-8'))
        self.assertNotIn('result',payload)

    async def test_end_only_closes_flow_without_business_result(self):
        n=node('end','end',params={'result':'ng'})
        session=self.session([n])
        result=await self.manager._node_end(session,'end',n['data']['params'])
        self.assertEqual(result['outputs'],{})
        self.assertFalse(hasattr(session,'result'))

    async def test_successful_run_writes_success_record(self):
        self.flow([node('s','start','a'),node('a','storage')])
        await self.manager.start_run('f'); await self.manager._task
        self.assertEqual(self.events[-1]['type'],'run.finished')
        self.assertEqual(self.events[-1]['result'],'success')
        self.assertTrue(all(event['status']=='success' for event in self.events if event['type']=='node.finished'))
        with db.connect() as c: self.assertEqual(c.execute('SELECT result FROM runs').fetchone()[0],'success')

    async def test_display_region_uses_status_color_and_model_label(self):
        s=self.session(); s.objects['c.Image']=self.img
        s.context.update({'m.regions':[{'bbox':[1,2,3,4],'label':'bolt','labelName':'螺栓'}], 'm.ok':False})
        base={'content':ref('m.regions'),'visible':content(True),'showLabel':True,
            'labelText':content('不得覆盖模型标签'),'style':{'okColor':'#123456','ngColor':'#ABCDEF','lineWidth':2}}
        await self.manager._node_display(s,'d',{'image':ref('c.Image'),'shapes':[
            {**base,'status':ref('m.ok')},{**base,'status':content(True),'labelText':content('')}], 'texts':[]})
        annotations=s.windows['1']['annotations']
        self.assertEqual([item['color'] for item in annotations],['#ABCDEF','#123456'])
        self.assertEqual([item['label'] for item in annotations],['螺栓','螺栓'])

    async def test_display_text_resolves_value_color_and_top_left_coordinates(self):
        s=self.session(); s.objects['c.Image']=self.img
        s.context.update({'m.message':'NG','m.ok':False})
        await self.manager._node_display(s,'d',{'image':ref('c.Image'),'shapes':[],'texts':[
            {'content':ref('m.message'),'visible':content(True),'status':ref('m.ok'),
             'x':content(37),'y':content(19),'fontSize':24},
            {'content':content('OK'),'visible':content(True),'status':content(True),
             'x':content(0),'y':content(0),'fontSize':16}]})
        annotations=s.windows['1']['annotations']
        self.assertEqual(annotations[0],{'kind':'text','content':'NG','x':37,'y':19,
            'color':'#F76965','fontSize':24})
        self.assertEqual(annotations[1]['color'],'#27C346')

    async def test_display_null_or_empty_item_is_error(self):
        for entry in (None, {}, {'content':ref('missing.regions')}):
            s=self.session(); s.objects['c.Image']=self.img
            with self.subTest(entry=entry), self.assertRaises(RuntimeError):
                await self.manager._node_display(s,'d',{'image':ref('c.Image'),'shapes':[entry]})

    async def test_debug_runs_path_and_returns_real_image_and_outputs(self):
        item=self.models.import_package('p.dvmodel',package()); self.models.start(item['id'])
        self.flow([node('s','start','c'),node('c','capture','m',{'source':'file','file':{'path':str(self.image_path)}}),
            node('m','model',params={'modelId':item['model_id'],'image':ref('c.Image'),'threshold':content(120)})])
        result=await self.manager.debug_node('f','m')
        self.assertEqual(result['executed'],['c','m'])
        self.assertTrue(result['outputs']['detection:red:detected'])
        self.assertTrue(result['imageUrl'].startswith('/api/v1/frames/'))
        with db.connect() as c: self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],0)
        self.models.stop(item['id'])

    async def test_active_snapshot_contains_frame_and_outputs(self):
        self.flow([node('s','start','c'),node('c','capture','a',{'source':'file','file':{'path':str(self.image_path)}}),node('a','storage')])
        run=await self.manager.start_run('f','step'); await self.manager._task
        info=self.manager.active_info()
        self.assertEqual(info['windows']['1']['width'],4)
        self.assertIn('Image',info['nodeOutputs']['c'])
        await self.manager.stop(run)

    def test_sdk_missing_never_discovers_demo_and_unknown_pixels_fail(self):
        self.camera.mvs._import_checked=True; self.camera.mvs._module=None; self.camera.mvs._error='SDK missing'
        self.assertEqual(self.camera.discover('hikvision')['devices'],[])
        self.assertEqual(self.camera.discover('demo')['devices'],[])
        with self.assertRaises(RuntimeError): self.camera.connect({'id':'c','vendor':'demo','serial':'DEMO000001','interface':'USB3'})
        with self.assertRaises(RuntimeError): cam._decode_frame(np.zeros(16,dtype=np.uint8),4,4,999)

    def test_mvs_ipv4_value_is_rendered_for_gige_devices(self):
        self.assertEqual(cam._ipv4_from_mvs(0xC0A80164), '192.168.1.100')
        self.assertEqual(cam._ipv4_from_mvs(0), '')

    def test_mvs_yuv422_yuyv_frame_is_converted_to_bgr(self):
        yuyv = np.array([
            16, 128, 235, 128,
            16, 128, 235, 128,
        ], dtype=np.uint8)

        try:
            frame = cam._decode_frame(yuyv, 2, 2, 0x02100032)
        except RuntimeError as exc:
            self.fail(str(exc))

        expected = np.array([
            [[0, 0, 0], [255, 255, 255]],
            [[0, 0, 0], [255, 255, 255]],
        ], dtype=np.uint8)
        np.testing.assert_array_equal(frame, expected)

    async def test_plc_real_tcp_echo_and_exception(self):
        requests=[]
        async def handle(reader,writer):
            request=await reader.readexactly(12); requests.append(request)
            if len(requests)<=2:
                writer.write(request[:5]); await writer.drain(); writer.write(request[5:])
            else:
                tid,_,_,unit=struct.unpack('>HHHB',request[:7])
                writer.write(struct.pack('>HHHBBB',tid,0,3,unit,0x86,2))
            await writer.drain(); writer.close(); await writer.wait_closed()
        server=await asyncio.start_server(handle,'127.0.0.1',0)
        async with server:
            params={'host':'127.0.0.1','port':server.sockets[0].getsockname()[1],'unitId':2,'address':12,'operation':'register','value':content(123)}
            result=await self.manager._node_plc(self.session(),'p',params)
            self.assertEqual(result['outputs']['value'],123)
            self.assertEqual(requests[0][7:],b'\x06\x00\x0c\x00\x7b')
            legacy = {**params, 'function': params['operation']}
            legacy.pop('operation')
            self.assertEqual((await self.manager._node_plc(self.session(),'p',legacy))['outputs']['function'], 'register')
            with self.assertRaises(RuntimeError): await self.manager._node_plc(self.session(),'p',params)

    def test_model_import_validates_load_and_keeps_running_package(self):
        item=self.models.import_package('p.dvmodel',package()); self.models.start(item['id'])
        before=Path(item['package_path'],'infer.py').read_text()
        with self.assertRaises(ms.ModelPackageError): self.models.import_package('p.dvmodel',package(script='raise RuntimeError("broken")'))
        self.assertEqual(Path(item['package_path'],'infer.py').read_text(),before)
        self.assertEqual(self.models.get(item['id'])['serviceStatus'],'running')
        self.models.stop(item['id'])

    def test_bad_package_not_registered(self):
        for data in (b'bad zip',package(script='raise RuntimeError("broken")'),package(extra={'../escape':'bad'}),
            package(manifest={**MANIFEST,'script':'missing.py'}),package(manifest={**MANIFEST,'modelId':'../escape'})):
            with self.subTest(data=data[:20]),self.assertRaises(ms.ModelPackageError): self.models.import_package('p.dvmodel',data)
        self.assertEqual(self.models.list_models(),[])

    def test_stale_database_running_state_is_not_running(self):
        item=self.models.import_package('p.dvmodel',package())
        with db.connect() as c: c.execute("UPDATE models SET enabled=1,service_status='running' WHERE id=?",(item['id'],))
        self.assertEqual(self.models.get(item['id'])['serviceStatus'],'stopped')
        self.assertEqual(self.models.list_models(status='running'),[])

    def test_output_validation_rejects_boolean_count_and_invalid_region(self):
        for count,regions in ((True,[]),(1,[{'label':'red','score':2,'bbox':[0,0,1,1]}]),(1,[{'label':'red','score':1,'bbox':[0,0,-1,2]}])):
            with self.subTest(count=count,regions=regions),self.assertRaises(ms.ModelPackageError):
                ms._validate_outputs(MANIFEST,{'detection:red:detected':True,'detection:red:count':count,'detection:red:regions':regions})

    def test_output_validation_requires_manifest_label_name_in_region(self):
        valid={'label':'red','labelName':'红色','score':1,'bbox':[0,0,1,1]}
        outputs={'detection:red:detected':True,'detection:red:count':1,'detection:red:regions':[valid]}
        ms._validate_outputs(MANIFEST,outputs)
        del valid['labelName']
        with self.assertRaisesRegex(ms.ModelPackageError,'类别名称'):
            ms._validate_outputs(MANIFEST,outputs)

    def test_save_strips_runtime_fields_and_preserves_sort_order(self):
        first=self.manager.create_flow('一'); second=self.manager.create_flow('二')
        nodes=first['nodes']; nodes[0]['data'].update(status='ok',durationMs=10,outputs=[{'variable':'x','type':'Int','value':42}])
        saved=self.manager.save_flow(first['id'],{'nodes':nodes})
        self.assertNotIn('status',saved['nodes'][0]['data'])
        self.assertNotIn('durationMs',saved['nodes'][0]['data'])
        self.assertNotIn('value',saved['nodes'][0]['data']['outputs'][0])
        self.assertEqual([f['id'] for f in self.manager.list_flows()],[first['id'],second['id']])
        self.assertEqual(nodes[0]['data']['outputs'][0]['value'],42)

    async def test_running_flow_rejects_edits_and_parallel_debug(self):
        nodes=[node('s','start','a'),node('a','storage','b'),node('b','storage')]; self.flow(nodes)
        run=await self.manager.start_run('f','step'); await self.manager._task
        with self.assertRaises(RuntimeError): self.manager.save_flow('f',{'nodes':nodes})
        with self.assertRaises(RuntimeError): self.manager.delete_flow('f')
        with self.assertRaises(RuntimeError): await self.manager.debug_node('f','a')
        with self.assertRaises(RuntimeError): await self.manager.start_run('f')
        await self.manager.stop(run)

    async def test_failed_node_writes_failed_record_and_never_runs_successor(self):
        self.flow([node('s','start','p'),node('p','plc','a'),node('a','storage')])
        await self.manager.start_run('f'); await self.manager._task
        self.assertEqual([e['nodeId'] for e in self.events if e['type']=='node.started'],['p'])
        self.assertEqual(self.events[-1]['result'],'failed')
        with db.connect() as c: self.assertEqual(c.execute('SELECT result FROM runs').fetchone()[0],'failed')

    async def test_debug_target_on_other_branch_is_not_executed(self):
        self.flow([node('s','start','b'),node('b','branch','a',{'conditions':[{'id':'yes','value':content(False)}]},[{'id':'yes','targetId':'p'}]),
                   node('a','storage'),node('p','plc')])
        with self.assertRaisesRegex(RuntimeError,'未到达'): await self.manager.debug_node('f','p')
        self.assertIsNone(self.manager.active_info())

    async def test_unselected_directory_does_not_fall_back_to_all_images(self):
        params={'source':'dir','dir':{'path':str(self.root),'files':[{'path':str(self.image_path),'selected':False}]}}
        with self.assertRaisesRegex(RuntimeError,'没有可用图像'): await self.manager._node_capture(self.session(),'c',params)

    def test_camera_parameters_flip_and_sdk_error_propagation(self):
        class Device:
            def __init__(self): self.settings={}; self.fail=False
            def MV_CC_StopGrabbing(self): return 0
            def MV_CC_StartGrabbing(self): return 0
            def MV_CC_SetEnumValue(self,name,value): self.settings[name]=value; return 0
            def MV_CC_SetFloatValue(self,name,value): self.settings[name]=value; return 7 if self.fail else 0
            def MV_CC_SetIntValue(self,name,value): self.settings[name]=value; return 0
            def MV_CC_SetCommandValue(self,name): self.settings[name]=True; return 0
        device=Device()
        params={'triggerMode':'soft','exposure':{'enabled':True,'value':250},'gain':{'enabled':True,'value':2},'line':{'enabled':True,'value':3}}
        self.camera.mvs._configure(device,params)
        self.assertEqual(device.settings,{'TriggerMode':1,'TriggerSource':7,'ExposureAuto':0,'ExposureTime':250.0,'GainAuto':0,'Gain':2.0,'Height':3,'TriggerSoftware':True})
        device.fail=True
        with self.assertRaisesRegex(RuntimeError,'错误码 7'): self.camera.mvs._configure(device,params)
        self.camera._runtime_connected.add('c')
        with patch.object(self.camera.mvs,'grab',return_value=self.img):
            image=self.camera.grab({'id':'c','serial':'real'},100,{'flipX':True,'flipY':True})
        np.testing.assert_array_equal(image[-1,-1],[0,0,255])
        np.testing.assert_array_equal(image[0,0],[0,0,0])

    async def test_camera_timeout_and_referenced_settings_are_resolved(self):
        session=self.session(); session.context['m.exposure']=300
        settings=[]
        def grab(camera,timeout,params):
            settings.append((timeout,params)); return self.img
        with patch.object(self.manager,'_get_camera',return_value={'id':'c'}),patch.object(self.camera,'grab',side_effect=grab):
            await self.manager._node_capture(session,'c',{'source':'camera','camera':{'cameraId':'c','timeout':{'valueUs':1501},'exposure':{'enabled':True,'value':ref('m.exposure')}}})
        self.assertEqual(settings[0][0],2)
        self.assertEqual(settings[0][1]['exposure']['value'],300)

    def test_stopped_model_replacement_and_dependency_validation(self):
        first=self.models.import_package('p.dvmodel',package())
        updated=self.models.import_package('p.dvmodel',package(manifest={**MANIFEST,'version':'2'}))
        self.assertEqual(first['id'],updated['id']); self.assertEqual(updated['version'],'2')
        with self.assertRaises(ms.ModelPackageError):
            self.models.import_package('p.dvmodel',package(manifest={**MANIFEST,'requires':['definitely-missing-digivision-dependency>=99']}))
        self.assertEqual(self.models.get(first['id'])['version'],'2')

    def test_runtime_capabilities_use_available_providers(self):
        fake = type("Ort", (), {
            "__version__": "1.22.1",
            "get_available_providers": staticmethod(
                lambda: ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            ),
        })
        with patch.dict("sys.modules", {"onnxruntime": fake}):
            result = self.models.runtime_capabilities()
        self.assertTrue(result["cpuAvailable"])
        self.assertEqual(result["recommendedProvider"], "CoreMLExecutionProvider")
        self.assertEqual(result["availableProviders"], [
            "CoreMLExecutionProvider", "CPUExecutionProvider"
        ])

    def test_onnxruntime_requirement_accepts_gpu_distribution(self):
        def installed(name):
            if name == "onnxruntime-gpu":
                return "1.22.1"
            raise ms.importlib.metadata.PackageNotFoundError(name)

        with patch.object(ms.importlib.metadata, "version", side_effect=installed):
            ms._check_requirements(["onnxruntime>=1.22.0"])

    async def test_model_is_protected_while_flow_is_paused(self):
        item=self.models.import_package('p.dvmodel',package()); self.models.start(item['id'])
        self.flow([node('s','start','c'),node('c','capture','m',{'source':'file','file':{'path':str(self.image_path)}}),
                   node('m','model',params={'modelId':item['id'],'image':ref('c.Image')})])
        run=await self.manager.start_run('f','step'); await self.manager._task
        with self.assertRaises(ms.ModelPackageError): self.models.stop(item['id'])
        with self.assertRaises(ms.ModelPackageError): self.models.delete(item['id'])
        await self.manager.stop(run); self.models.stop(item['id'])

    async def test_stop_waits_for_current_node_and_skips_successor(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def slow(*args):
            entered.set(); await release.wait(); return {'nextId':'b','outputs':{}}
        self.flow([node('s','start','a'),node('a','storage','b'),node('b','storage')])
        with patch.object(self.manager,'_node_storage',slow):
            run=await self.manager.start_run('f'); await entered.wait()
            await self.manager.stop(run)
            self.assertEqual(self.manager.active_info()['status'],'running')
            release.set(); await self.manager._task
        self.assertEqual([e['nodeId'] for e in self.events if e['type']=='node.started'],['a'])
        self.assertEqual(self.events[-1]['type'],'run.stopped')
        with db.connect() as c: self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],0)

    async def test_authenticated_event_channel(self):
        channels=[]
        async def broadcast(channel,event): channels.append(channel)
        with patch.object(ex,'AUTH_TOKEN','session-token'),patch.object(ex.event_hub,'broadcast',broadcast):
            await self.manager._emit(ex.ExecutionSession('r','f','run',{}),{'type':'run.started'})
        self.assertEqual(channels,['session-token'])


    def test_orphan_nodes_are_rejected_but_blank_connected_drafts_save(self):
        with self.assertRaisesRegex(ValueError, '孤立|不可达'):
            self.manager.validate_flow([node('s','start'),node('c','capture')])
        self.flow([node('s','start','c'),node('c','capture','m'),node('m','model',params={'image':ref('')})])
        self.assertEqual(len(self.manager.get_flow('f')['nodes']),3)

    async def test_debug_preview_does_not_broadcast_window_events(self):
        self.flow([node('s','start','c'),node('c','capture','d',{'source':'file','file':{'path':str(self.image_path)}}),
            node('d','display',params={'image':ref('c.Image'),'texts':[{'content':content('预览')}],'shapes':[]})])
        snapshots=[]
        execute=self.manager._execute_node
        async def inspect_snapshot(*args,**kwargs):
            result=await execute(*args,**kwargs)
            snapshots.append(self.manager.active_info())
            return result
        with patch.object(self.manager,'_execute_node',side_effect=inspect_snapshot):
            result=await self.manager.debug_node('f','d')
        self.assertEqual(snapshots,[None,None])
        self.assertEqual(self.events,[])
        self.assertTrue(result['imageUrl'])
        self.assertEqual(result['annotations'][0]['content'],'预览')

    async def test_paused_stop_retains_last_session_snapshot(self):
        self.flow([node('s','start','a'),node('a','storage','b'),node('b','storage')])
        run=await self.manager.start_run('f','step'); await self.manager._task
        await self.manager.stop(run)
        self.assertEqual(self.manager._last_session.status,'stopped')
        self.assertEqual(self.manager._last_session.executed,['a'])


if __name__=='__main__': unittest.main()


class DraftRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_broken_references_can_be_repaired_one_node_at_a_time(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(db, 'DB_PATH', Path(directory) / 'draft.db'):
            db.init_db()
            manager = ex.WorkflowManager(None, None)
            nodes = [node('s', 'start', 'first'), node('first', 'display', 'second', {'image': ref('removed.Image')}), node('second', 'display', params={'image': ref('removed.Image')})]
            saved = manager.save_flow('repair', {'name': 'repair', 'nodes': nodes})
            self.assertEqual(saved['nodes'][1]['data']['params']['image']['ref'], 'removed.Image')
            with self.assertRaisesRegex(ValueError, '确定上游'):
                await manager.start_run('repair')
            # 第一处可以先清空保存，另一处仍保留失效引用以供后续修复。
            nodes[1]['data']['params']['image'] = ref('')
            manager.save_flow('repair', {'nodes': nodes})
            self.assertEqual(manager.get_flow('repair')['nodes'][2]['data']['params']['image']['ref'], 'removed.Image')
