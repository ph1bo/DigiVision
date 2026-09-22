"""真实 HTTP/WebSocket + 独立 SQLite 的功能链路测试，不写用户数据。"""
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

import cv2
import numpy as np
from websockets.asyncio.client import connect as websocket_connect

ROOT = Path(__file__).resolve().parents[1]


class ApiLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='digivision-api-test-')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.log = open(Path(cls.temp.name) / 'server.log', 'w+')
        cls.process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(cls.port)], cwd=ROOT,
            env={**os.environ, 'DIGIVISION_DATA_DIR': cls.temp.name, 'DIGIVISION_MOCK_CAMERA': '0', 'DIGIVISION_TOKEN': '', 'PYTHONDONTWRITEBYTECODE': '1'}, stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            try:
                with urlopen(cls.base + '/health', timeout=1):
                    return
            except Exception:
                if cls.process.poll() is not None:
                    break
                time.sleep(.05)
        cls.log.seek(0)
        raise RuntimeError(cls.log.read())

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        cls.temp.cleanup()

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(Request(self.base + '/api/v1' + path, data=data, method=method, headers={'Content-Type': 'application/json'}), timeout=15) as response:
                payload = json.load(response)
        except HTTPError as error:
            detail = error.read().decode()
            raise AssertionError(f'{method} {path}: {error.code} {detail}') from error
        self.assertEqual(payload['code'], 0)
        return payload['data']

    def upload(self, path, name, content):
        boundary = 'digivision-test-boundary'
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                + content + f'\r\n--{boundary}--\r\n'.encode())
        with urlopen(Request(self.base + '/api/v1' + path, data=data, method='POST', headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}), timeout=15) as response:
            return json.load(response)['data']

    def wait_finished(self, run_id):
        for _ in range(150):
            active = self.request('GET', '/runs/active')
            if not active or active['runId'] != run_id:
                return self.request('GET', f'/runs/{run_id}')
            time.sleep(.02)
        self.fail('流程未结束')

    def test_01_empty_database_and_real_sdk_state(self):
        self.assertEqual(self.request('GET', '/flows'), [])
        self.assertEqual(self.request('GET', '/models'), [])
        self.assertEqual(self.request('GET', '/cameras'), [])
        capabilities = self.request('GET', '/runtime/capabilities')
        self.assertTrue(capabilities['database']['ready'])
        self.assertIn('cpuAvailable', capabilities['inference'])
        self.assertIn('vendors', capabilities['camera'])
        result = self.request('POST', '/cameras/discover', {'vendor': 'hikvision'})
        self.assertFalse(any('DEMO' in d['serial'] for d in result['devices']))

    def test_02_image_model_flow_websocket_and_sqlite(self):
        image = np.full((240, 320, 3), 255, np.uint8)
        cv2.rectangle(image, (50, 40), (100, 100), (0, 0, 0), -1)
        _, encoded = cv2.imencode('.png', image)
        uploaded = self.upload('/files/upload', 'actual-image.png', encoded.tobytes())
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as package:
            for path in (ROOT / 'samples/demo_model').rglob('*'):
                if path.is_file() and '__pycache__' not in str(path):
                    package.write(path, path.relative_to(ROOT / 'samples/demo_model').as_posix())
        model = self.upload('/models/import', 'test-model.dvmodel', archive.getvalue())
        self.request('POST', f"/models/{model['id']}/start")
        flow = self.request('POST', '/flows', {'name': '真实链路测试'})
        self.assertEqual(len(flow['nodes']), 1)
        start = flow['nodes'][0]
        start['data']['ports']['builtIn'] = 'capture'
        ref = lambda name: {'type': 'ref', 'ref': name}
        content = lambda value: {'type': 'content', 'ref': value}
        def node(id, kind, params, target=None):
            return {'id': id, 'type': 'workflow', 'position': {'x': 0, 'y': 0}, 'data': {'label': id, 'nodeType': kind, 'params': params, 'ports': {'builtIn': target, 'dynamic': []}}}
        nodes = [start, node('capture', 'capture', {'source': 'file', 'file': {'path': uploaded['path']}}, 'infer'),
            node('infer', 'model', {'modelId': model['id'], 'image': ref('capture.Image'), 'threshold': content(80), 'minArea': content(100)}, 'display'),
            node('display', 'display', {'image': ref('capture.Image'), 'shapes': [{'id': 'region', 'content': ref('infer.detection:defect:regions'), 'visible': content(True), 'status': content(False), 'showLabel': True, 'labelText': content('defect'), 'style': {'okColor': '#22C55E', 'ngColor': '#EF4444', 'lineWidth': 3}}], 'texts': [{'id': 'label', 'content': content('NG'), 'visible': content(True), 'status': content(False), 'x': content(12), 'y': content(22), 'fontSize': 20}]})]
        self.request('PUT', f"/flows/{flow['id']}", {**flow, 'nodes': nodes})
        restored = self.request('GET', f"/flows/{flow['id']}")
        self.assertEqual(restored['nodes'][2]['data']['params']['threshold'], content(80))
        debug = self.request('POST', f"/flows/{flow['id']}/nodes/infer/debug")
        self.assertTrue(debug['outputs']['detection:defect:detected'])
        async def run_and_listen():
            events = []
            async with websocket_connect(f'ws://127.0.0.1:{self.port}/ws') as socket:
                await socket.recv()
                result = await asyncio.to_thread(self.request, 'POST', f"/flows/{flow['id']}/runs", {'mode': 'run'})
                while True:
                    event = json.loads(await asyncio.wait_for(socket.recv(), 10))
                    if event.get('runId') == result['runId']:
                        events.append(event)
                        if event['type'] == 'run.finished':
                            return result['runId'], events
        run_id, events = asyncio.run(run_and_listen())
        record = self.wait_finished(run_id)
        self.assertEqual(record['result'], 'success', record)
        self.assertEqual(record['record']['context']['infer.detection:defect:count'], 1)
        frames = [e for e in events if e['type'] == 'window.frame']
        self.assertTrue(frames)
        with urlopen(self.base + frames[-1]['imageUrl']) as response:
            decoded = cv2.imdecode(np.frombuffer(response.read(), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape[:2], (240, 320))
        overlays = [e for e in events if e['type'] == 'window.overlay' and e.get('annotations')]
        self.assertTrue(overlays)
        self.assertTrue(any(a.get('content') == 'NG' for a in overlays[-1]['annotations']))
        self.request('POST', f"/models/{model['id']}/stop")
        failed = self.request('POST', f"/flows/{flow['id']}/runs", {'mode': 'run'})
        self.assertEqual(self.wait_finished(failed['runId'])['result'], 'failed')
        self.request('DELETE', f"/flows/{flow['id']}")
        self.request('DELETE', f"/models/{model['id']}")

    def test_03_reject_invalid_image_and_archive(self):
        for endpoint, filename in [('/files/upload', 'invalid.png'), ('/models/import', 'invalid.dvmodel')]:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(HTTPError) as failure:
                    self.upload(endpoint, filename, b'not an image or model')
                self.assertEqual(failure.exception.code, 400)

    def test_04_reorder_and_rename_persist(self):
        first = self.request('POST', '/flows', {'name': 'one'})
        second = self.request('POST', '/flows', {'name': 'two'})
        self.request('PUT', '/flows/order', {'ids': [second['id'], first['id']] + [f['id'] for f in self.request('GET', '/flows') if f['id'] not in {first['id'], second['id']} ]})
        self.assertEqual(self.request('GET', '/flows')[0]['id'], second['id'])
        self.request('PUT', f"/flows/{first['id']}", {**first, 'name': 'renamed'})
        self.assertEqual(self.request('GET', f"/flows/{first['id']}")['name'], 'renamed')
        for flow in [first, second]:
            self.request('DELETE', f"/flows/{flow['id']}")

    def test_05_single_step_and_stop_do_not_replay_nodes(self):
        path = Path(self.temp.name) / 'step.png'
        cv2.imwrite(str(path), np.full((12, 16, 3), 127, np.uint8))
        flow = self.request('POST', '/flows', {'name': '单步生命周期'})
        start = flow['nodes'][0]
        start['data']['ports']['builtIn'] = 'a'
        nodes = [start]
        for id, target in [('a', 'b'), ('b', 'c'), ('c', None)]:
            nodes.append({'id': id, 'data': {'nodeType': 'capture', 'label': id,
                'params': {'source': 'file', 'file': {'path': str(path)}}, 'ports': {'builtIn': target, 'dynamic': []}}})
        self.request('PUT', f"/flows/{flow['id']}", {**flow, 'nodes': nodes})
        run = self.request('POST', f"/flows/{flow['id']}/runs", {'mode': 'step'})
        def paused():
            for _ in range(100):
                active = self.request('GET', '/runs/active')
                if active and active['status'] == 'paused': return active
                time.sleep(.01)
            self.fail('单步没有暂停')
        self.assertEqual(paused()['executed'], ['a'])
        self.request('POST', f"/runs/{run['runId']}/step")
        self.assertEqual(paused()['executed'], ['a', 'b'])
        with self.assertRaisesRegex(AssertionError, '409'):
            self.request('PUT', f"/flows/{flow['id']}", {**flow, 'nodes': nodes})
        self.request('POST', f"/runs/{run['runId']}/stop")
        self.assertIsNone(self.request('GET', '/runs/active'))
        self.assertNotIn(run['runId'], [row['id'] for row in self.request('GET', '/runs')])
        self.request('DELETE', f"/flows/{flow['id']}")


if __name__ == '__main__':
    unittest.main()
