"""Result messages and viewer-cache behavior across existing Scale branches."""

import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import georeference_ifc.main as georef
from test_ifc2x3_georeferencing import SCALE, TRANSFORM, make_model, properties


WARNING = (
    "There is a conflict between Scale factor and unit conversion. "
    "(Yet to be decided by buildingSmart.)"
)


class ResultRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # App import creates directories and runs retention cleanup.
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        with patch.dict(os.environ, {"IFCGREF_UPLOAD_FOLDER": directory.name}):
            cls.workflow = importlib.import_module("app")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        (self.folder / "state").mkdir()
        self.enterContext(patch.dict(self.workflow.app.config, {
            "TESTING": True,
            "UPLOAD_FOLDER": str(self.folder),
            "MODEL_STATE_FOLDER": str(self.folder / "state"),
        }))
        self.model = make_model()
        georef.set_mapconversion_crs(self.model, *TRANSFORM, map_unit="metre")
        self.scale_property = properties(self.model, "ePSet_MapConversion")["Scale"]
        self.filename = "model.ifc"
        self.info_result = ([("IFC length unit", "millimetre")], "Unit details")
        self.open_file = self.enterContext(patch.object(
            self.workflow, "fileOpener", return_value=self.model))
        self.info = self.enterContext(patch.object(
            self.workflow, "infoExt", return_value=self.info_result))
        self.cache = self.enterContext(patch.object(
            self.workflow, "cache_map_context", autospec=True))
        self.render = self.enterContext(patch.object(
            self.workflow, "render_template", return_value="rendered"))

    def test_messages_scale_and_cache_for_source_and_generated_files(self):
        cases = (
            # coefficient, Scale, warning, previously stored scaleError
            (None, SCALE, False, False),
            (0.001, None, True, False),
            (0.001, 1.0, True, False),
            (0.001, SCALE, False, False),
            (1.0, None, False, False),
            # Preserve the existing integer comparisons, including truncation.
            (1.5, None, False, False),
            (0.001, 1.5, True, False),
            (0.001, SCALE, False, True),
        )
        for generated in (False, True):
            source = "model_georeferenced.ifc" if generated else self.filename
            if generated:
                (self.folder / source).touch()
                self.workflow.write_model_state(self.filename, {"coeff": 99})
            for coeff, scale, warning, previous_flag in cases:
                with self.subTest(generated=generated, coeff=coeff, scale=scale,
                                  previous_flag=previous_flag):
                    for mock in (self.open_file, self.info, self.cache, self.render):
                        mock.reset_mock()
                    self.scale_property.NominalValue = (
                        None if scale is None else self.model.createIfcReal(scale)
                    )
                    self.workflow.write_model_state(source, {
                        "coeff": coeff, "scaleError": previous_flag,
                    })
                    before = self.model.to_string()
                    base_message = self.workflow.georef(self.model)[0]
                    with self.workflow.app.test_request_context('/result/model.ifc'):
                        self.assertEqual(
                            self.workflow.render_georef_result(self.filename), "rendered")
                        self.assertEqual(
                            self.workflow.session.get('scaleError'), True if warning else None)

                    self.open_file.assert_called_once_with(source)
                    self.info.assert_called_once_with(source, 7415)
                    self.render.assert_called_once()
                    self.assertEqual(self.render.call_args.args, ('result.html',))
                    rendered = self.render.call_args.kwargs
                    expected_message = self.info_result if coeff is None else base_message
                    if warning:
                        expected_message += WARNING
                    self.assertEqual(rendered['message'], expected_message)
                    self.assertEqual(rendered['filename'], self.filename)
                    self.assertEqual(rendered['download_available'], generated)
                    self.assertIn('metre', rendered['table_f'])
                    self.assertIn('Scale', rendered['table_g'])
                    if coeff is None:
                        self.cache.assert_not_called()
                    else:
                        self.cache.assert_called_once()
                        self.assertEqual(self.cache.call_args.args, (source, self.model))
                        self.assertEqual(self.cache.call_args.kwargs['eff'], coeff)
                        self.assertEqual(
                            self.cache.call_args.kwargs.get('scale_error', False), warning)
                    state = self.workflow.read_model_state(source)
                    self.assertEqual(state['coeff'], coeff)
                    self.assertEqual(state['scaleError'], warning or previous_flag)
                    if generated:
                        self.assertEqual(
                            self.workflow.read_model_state(self.filename)['coeff'], 99)
                        self.assertNotIn(
                            'scaleError', self.workflow.read_model_state(self.filename))
                    self.assertEqual(self.model.to_string(), before)
                    self.assertEqual(georef.get_mapconversion_crs(self.model)[0].Scale, scale)

    def test_unit_lookup_failures_render_original_message_without_caching(self):
        for helper in ('get_epsg_from_projected_crs', 'infoExt', 'workflow_value'):
            with self.subTest(helper=helper):
                self.render.reset_mock()
                self.cache.reset_mock()
                with patch.object(self.workflow, helper, side_effect=ValueError('Lookup failed')):
                    with self.workflow.app.test_request_context('/result/model.ifc'):
                        self.workflow.render_georef_result(self.filename)
                        self.assertNotIn('scaleError', self.workflow.session)
                self.assertEqual(
                    self.render.call_args.kwargs['message'], self.workflow.georef(self.model)[0])
                self.cache.assert_not_called()
                self.assertFalse(list((self.folder / 'state').iterdir()))

    def test_invalid_coefficient_is_not_swallowed_as_a_lookup_failure(self):
        self.workflow.write_model_state(self.filename, {'coeff': 'invalid'})
        with self.workflow.app.test_request_context('/result/model.ifc'):
            with self.assertRaises(ValueError):
                self.workflow.render_georef_result(self.filename)
        self.cache.assert_not_called()
        self.render.assert_not_called()

    def test_viewer_cache_failure_is_not_swallowed_as_a_lookup_failure(self):
        self.workflow.write_model_state(self.filename, {'coeff': 0.001})
        self.cache.side_effect = RuntimeError('Viewer cache failed')
        with self.workflow.app.test_request_context('/result/model.ifc'):
            with self.assertRaisesRegex(RuntimeError, 'Viewer cache failed'):
                self.workflow.render_georef_result(self.filename)
        self.render.assert_not_called()


if __name__ == '__main__':
    unittest.main()
