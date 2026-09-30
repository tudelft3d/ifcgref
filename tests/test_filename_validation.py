"""Reject unsafe IFC identifiers before processing files or workflow state."""

from contextlib import ExitStack
import importlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from test_ifc2x3_georeferencing import make_model


class FilenameValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        with patch.dict(os.environ, {'IFCGREF_UPLOAD_FOLDER': directory.name}):
            cls.workflow = importlib.import_module('app')

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.uploads = self.folder / 'uploads'
        self.uploads.mkdir()
        for name in ('state', 'jobs'):
            (self.uploads / name).mkdir()
        self.enterContext(patch.dict(self.workflow.app.config, {
            'TESTING': True,
            'UPLOAD_FOLDER': str(self.uploads),
            'MODEL_STATE_FOLDER': str(self.uploads / 'state'),
            'JOB_FOLDER': str(self.uploads / 'jobs'),
        }))
        self.enterContext(patch.dict(self.workflow.map_context_cache, clear=True))
        self.client = self.workflow.app.test_client()

    def test_result_rejects_traversal_before_rendering(self):
        with patch.object(self.workflow, 'render_georef_result', return_value='unvalidated') as render:
            response = self.client.get('/result/..%5Coutside.ifc')
        self.assertEqual(response.status_code, 404)
        render.assert_not_called()

    def test_workflow_routes_reject_invalid_names_before_processing(self):
        routes = {
            'result': ('GET',), 'show': ('GET', 'POST'),
            'convert': ('GET', 'POST'), 'survey': ('GET', 'POST'),
            'calc': ('GET', 'POST'), 'download': ('GET',), 'uploads': ('GET',),
        }
        invalid_names = (
            '..\\outside.ifc', 'C:\\outside.ifc', 'C:outside.ifc',
            '\\\\server\\share\\model.ifc', '../outside.ifc',
            'model one.ifc', 'model.ifc:stream', 'model.txt', '.', '..', '.ifc',
            'model.ifc.', '%2e%2e%5coutside.ifc', '\x00model.ifc',
        )
        # A matching sanitized upload must not make a malformed URL an alias.
        (self.uploads / 'model_one.ifc').write_bytes(b'unchanged inside fixture')
        (self.folder / 'outside.ifc').write_bytes(b'unchanged outside fixture')
        before = {p: p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch.object(self.workflow, name, side_effect=
                     AssertionError('Unsafe filename reached ' + name))) for name in (
                'render_georef_result', 'infoExt', 'workflow_value', 'workflow_context',
                'persist_workflow_values', 'start_job', 'cache_map_context', 'send_from_directory',
            )]
            mocks.append(stack.enter_context(patch.object(
                self.workflow.ifcopenshell, 'open', side_effect=AssertionError('IFC opened'))))
            mocks.append(stack.enter_context(patch.object(
                self.workflow.os.path, 'getmtime', side_effect=AssertionError('File stat requested'))))
            for route, methods in routes.items():
                for method in methods:
                    for name in invalid_names:
                        with self.subTest(route=route, method=method, filename=name):
                            response = self.client.open(
                                '/' + route + '/' + quote(name, safe=''), method=method,
                                data={'epsg_code': '7415', 'Num': '1'},
                            )
                            self.assertEqual(response.status_code, 404)
            for mock in mocks:
                mock.assert_not_called()
        after = {p: p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        self.assertEqual(after, before)

    def test_result_keeps_canonical_names_unchanged(self):
        for name in ('model.ifc', 'MODEL.IFC', 'model_georeferenced.ifc', 'abc123_model.ifc'):
            with self.subTest(filename=name):
                with patch.object(self.workflow, 'render_georef_result', return_value='result') as render:
                    response = self.client.get('/result/' + name)
                self.assertEqual(response.status_code, 200)
                render.assert_called_once_with(name)

    def test_convert_survey_and_calc_keep_valid_workflow(self):
        filename = 'MODEL.IFC'
        self.workflow.write_model_state(filename, {
            'target_epsg': 7415, 'coeff': 0.001, 'rows': 1, 'Refl': False,
            'ifcunit': 'millimetre', 'mapunit': 'metre',
        })
        with patch.object(self.workflow, 'infoExt', return_value=([], '')) as info:
            self.assertEqual(self.client.get('/convert/' + filename).status_code, 200)
            response = self.client.post('/convert/' + filename, data={'epsg_code': '7415'})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.location.endswith('/survey/' + filename))
            for method in ('GET', 'POST'):
                response = self.client.open('/survey/' + filename, method=method, data={'Num': '1'})
                self.assertEqual(response.status_code, 200)
            self.assertTrue(all(call.args == (filename, 7415) for call in info.call_args_list))
        form = {'x0': '0', 'y0': '0', 'z0': '0',
                'x_prime0': '185542', 'y_prime0': '428121', 'z_prime0': '20'}
        with patch.object(self.workflow, 'start_job', return_value='test-job') as start:
            for method in ('GET', 'POST'):
                start.reset_mock()
                response = self.client.open('/calc/' + filename, method=method, data=form)
                self.assertEqual(response.status_code, 302)
                start.assert_called_once()
                self.assertEqual(start.call_args.kwargs['filename'], filename)

    def test_viewer_keeps_source_and_generated_file_selection(self):
        filename = 'model.ifc'
        model = make_model()
        model.write(str(self.uploads / filename))
        for generated in (False, True):
            source = 'model_georeferenced.ifc' if generated else filename
            if generated:
                model.write(str(self.uploads / source))
            for method in ('GET', 'POST'):
                with self.subTest(generated=generated, method=method):
                    with patch.object(self.workflow.ifcopenshell, 'open', return_value=model) as opened:
                        with patch.object(self.workflow, 'cache_map_context', return_value={'filename': source}) as cache:
                            with patch.object(self.workflow, 'render_template', return_value='viewer'):
                                response = self.client.open('/show/' + filename, method=method)
                    self.assertEqual(response.status_code, 200)
                    opened.assert_called_once_with(str(self.uploads / source))
                    self.assertEqual(cache.call_args.args, (source, model))

    def test_uploads_reject_names_that_lose_extension_without_saving(self):
        for endpoint in ('upload', 'devs'):
            for name in ('.ifc', '..ifc', '模型.ifc', 'model.ifc.'):
                with self.subTest(endpoint=endpoint, filename=name):
                    with patch.object(self.workflow, 'start_job') as start:
                        with patch.object(self.workflow, 'fileOpener') as opened:
                            response = self.client.post('/' + endpoint, data={
                                'file': (BytesIO(b'rejected content'), name),
                            })
                    self.assertEqual(response.status_code, 400)
                    start.assert_not_called()
                    opened.assert_not_called()
                    self.assertFalse(any(p.is_file() for p in self.uploads.rglob('*')))

    def test_uploads_still_normalize_valid_original_names(self):
        for name, normalized in (('model one.ifc', 'model_one.ifc'), ('MODEL.IFC', 'MODEL.IFC')):
            for endpoint in ('upload', 'devs'):
                with self.subTest(endpoint=endpoint, filename=name):
                    with patch.object(self.workflow, 'start_job', return_value='test-job') as start:
                        with patch.object(self.workflow, 'fileOpener', return_value=object()) as opened:
                            with patch.object(self.workflow, 'georef', return_value=('', False)):
                                response = self.client.post('/' + endpoint, data={
                                    'file': (BytesIO(b'uploaded content'), name),
                                })
                    if endpoint == 'upload':
                        self.assertEqual(response.status_code, 302)
                        stored = start.call_args.kwargs['filename']
                        self.assertRegex(stored, r'^[0-9a-f]{32}_' + normalized + '$')
                        opened.assert_not_called()
                    else:
                        self.assertEqual(response.status_code, 200)
                        stored = normalized
                        opened.assert_called_once_with(stored)
                        start.assert_not_called()
                    self.assertTrue(self.workflow.is_safe_upload_filename(stored))
                    self.assertEqual((self.uploads / stored).read_bytes(), b'uploaded content')
                    self.assertEqual(self.workflow.read_model_state(stored)['original_filename'], name)


if __name__ == '__main__':
    unittest.main()
