import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db
from backend.engine.executor import ExecutionSession, WorkflowManager


class BranchExecutionTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['node', 'node_modules/typescript/bin/tsc', '-p', 'tsconfig.tests.json'], cwd=root, check=True)
        cls.nodes = json.loads(subprocess.check_output(
            ['node', 'node_modules/.tmp/workflow-tests/tests/workflow-fixture.js', '--json'], cwd=root, text=True))

    async def trace(self, values, with_else=True):
        nodes = copy.deepcopy(self.nodes)
        conditions = [n for n in nodes if n['data'].get('branch', {}).get('role') in ('if', 'elseIf')]
        for node, value in zip(conditions, values):
            node['data']['params']['conditions'][0]['value']['ref'] = value
        end = next(n for n in nodes if n['data'].get('branch', {}).get('role') == 'end')
        if not with_else:
            conditions[-1]['data']['ports']['builtIn'] = end['id']
            nodes = [n for n in nodes if n['data'].get('branch', {}).get('role') != 'else' and n['id'] != 'body-3']
        manager = WorkflowManager(None, None)
        # 此测试验证编排路径；真实 PLC 协议由 test_runtime_logic 的 TCP 测试覆盖。
        async def branch_action(session, node_id, params):
            return {'nextId': session.nodes[node_id]['data']['ports']['builtIn'], 'outputs': {}}
        manager._node_plc = branch_action
        manager.validate_flow(nodes)
        session = ExecutionSession('test', 'test', 'run', {n['id']: n for n in nodes})
        current = manager._first_real_node(session)
        visited = []
        while current:
            self.assertNotIn(current, visited)
            visited.append(current)
            result = await manager._execute_node(session, session.nodes[current], debug=True)
            current = result['nextId']
        self.assertEqual(visited[-2:], [end['id'], 'after'])
        self.assertFalse(hasattr(session, 'result'))
        return visited

    async def test_first_match_skips_all_later_arms(self):
        visited = await self.trace([True, True, True])
        self.assertEqual([n for n in visited if n.startswith('body-')], ['body-0'])

    async def test_else_if_is_checked_in_order(self):
        for values, expected in [([False, True, True], 'body-1'), ([False, False, True], 'body-2')]:
            with self.subTest(values=values):
                visited = await self.trace(values)
                self.assertEqual([n for n in visited if n.startswith('body-')], [expected])

    async def test_else_only_runs_when_no_condition_matches(self):
        visited = await self.trace([False, False, False])
        self.assertEqual([n for n in visited if n.startswith('body-')], ['body-3'])

    async def test_no_match_without_else_still_continues(self):
        visited = await self.trace([False, False, False], with_else=False)
        self.assertFalse(any(n.startswith('body-') for n in visited))

    def test_sqlite_roundtrip_preserves_structure_and_ports(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(db, 'DB_PATH', Path(directory) / 'test.db'):
            db.init_db()
            manager = WorkflowManager(None, None)
            manager.save_flow('branch-test', {'name': '分支测试', 'nodes': self.nodes})
            restored = manager.get_flow('branch-test')['nodes']
            expected = copy.deepcopy(self.nodes)
            for node in expected:
                node['data'].pop('status', None)
                node['data'].pop('durationMs', None)
                for output in node['data'].get('outputs', []):
                    output.pop('value', None)
            self.assertEqual(restored, expected)
            self.assertTrue(all('status' not in node['data'] for node in restored))

    def test_default_flow_has_no_standalone_end_node(self):
        nodes = db.default_flow_nodes()
        self.assertFalse(any(node['data'].get('nodeType') == 'end' for node in nodes))


if __name__ == '__main__':
    unittest.main()
