"""Viewer anchors follow absolute body vertices, independent of IFC storage."""

import importlib
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import tempfile
import unittest
from unittest.mock import patch

import ifcopenshell.api
import ifcopenshell.geom
import ifcopenshell.util.geolocation
import ifcopenshell.util.unit
import numpy as np
from pyproj import CRS, Transformer

import georeference_ifc

from test_ifc2x3_georeferencing import make_model


CUBE_VERTICES = np.array([
    (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
    (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
], dtype=float)
CUBE_FACES = [
    (0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
    (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
]


def create_body_model(placement_xyz, geometry_xyz, schema='IFC4', prefix=None):
    """Create a one-metre cube; both offsets are SI metres, for either unit prefix."""
    model = make_model(schema)
    model.by_type('IfcSIUnit')[0].Prefix = prefix
    context = model.by_type('IfcGeometricRepresentationContext')[0]
    body = ifcopenshell.api.run(
        'context.add_context', model, context_type='Model',
        context_identifier='Body', target_view='MODEL_VIEW', parent=context,
    )
    product = ifcopenshell.api.run(
        'root.create_entity', model, ifc_class='IfcBuildingElementProxy',
    )
    matrix = np.eye(4)
    matrix[:3, 3] = placement_xyz
    ifcopenshell.api.run(
        'geometry.edit_object_placement', model, product=product, matrix=matrix,
    )
    representation = ifcopenshell.api.run(
        'geometry.add_mesh_representation', model, context=body,
        vertices=[(CUBE_VERTICES + geometry_xyz).tolist()], faces=[CUBE_FACES],
    )
    ifcopenshell.api.run(
        'geometry.assign_representation', model, product=product,
        representation=representation,
    )
    return model


class ViewerOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        with patch.dict(os.environ, {'IFCGREF_UPLOAD_FOLDER': directory.name}):
            cls.workflow = importlib.import_module('app')

    def origin_without_mutation(self, model):
        before = model.to_string()
        origin = np.asarray(self.workflow.get_viewer_origin(model), dtype=float)
        self.assertEqual(origin.shape, (3,))
        self.assertTrue(np.isfinite(origin).all())
        self.assertEqual(model.to_string(), before)
        return origin

    def assert_body_vertex(self, model, expected_vertices):
        origin = self.origin_without_mutation(model)
        # Any tessellated cube corner is valid; ordering is an engine detail.
        matches = np.isclose(expected_vertices, origin, rtol=0, atol=1e-6).all(axis=1)
        self.assertTrue(matches.any(), f'{origin} is not an absolute body vertex')

    def test_placement_and_representation_offsets_in_metres_and_millimetres(self):
        cases = (
            ((2, 3, 4), (5, 6, 7)),
            ((600000, 4000000, 30), (2, 3, 4)),
            ((2, 3, 4), (600000, 4000000, 30)),
            ((600000, 4000000, 30), (-600000, -4000000, -30)),
            ((600000, 4000000, 30), (500000, 3000000, 20)),
        )
        for schema in ('IFC2X3', 'IFC4', 'IFC4X3'):
            for prefix in (None, 'MILLI'):
                for placement, geometry in cases:
                    with self.subTest(schema=schema, prefix=prefix,
                                      placement=placement, geometry=geometry):
                        model = create_body_model(placement, geometry, schema, prefix)
                        self.assert_body_vertex(model, CUBE_VERTICES + placement + geometry)

    def test_nested_placement_rotation_is_applied_to_geometry(self):
        placement, geometry = (10, 20, 1), (2, 3, 4)
        parent = np.array([
            [0., -1., 0., 1000.], [1., 0., 0., 2000.],
            [0., 0., 1., 3.], [0., 0., 0., 1.],
        ])
        for schema in ('IFC2X3', 'IFC4'):
            with self.subTest(schema=schema):
                model = create_body_model(placement, geometry, schema, 'MILLI')
                site = model.by_type('IfcSite')[0]
                ifcopenshell.api.run(
                    'geometry.edit_object_placement', model, product=site, matrix=parent,
                )
                product = model.by_type('IfcBuildingElementProxy')[0]
                product.ObjectPlacement.PlacementRelTo = site.ObjectPlacement
                expected = (CUBE_VERTICES + placement + geometry) @ parent[:3, :3].T
                self.assert_body_vertex(model, expected + parent[:3, 3])

    def test_mapped_representation_offset_is_applied(self):
        placement, geometry, mapped_offset = (10, 20, 1), (2, 3, 4), (50, -30, 2)
        for schema in ('IFC2X3', 'IFC4'):
            with self.subTest(schema=schema):
                model = create_body_model(placement, geometry, schema, 'MILLI')
                product = model.by_type('IfcBuildingElementProxy')[0]
                mapped = ifcopenshell.api.run(
                    'geometry.map_representation', model,
                    representation=product.Representation.Representations[0],
                )
                unit_scale = ifcopenshell.util.unit.calculate_unit_scale(model)
                mapped.Items[0].MappingTarget.LocalOrigin.Coordinates = tuple(
                    value / unit_scale for value in mapped_offset
                )
                product.Representation.Representations = (mapped,)
                expected = CUBE_VERTICES + placement + geometry + mapped_offset
                self.assert_body_vertex(model, expected)

    def test_body_takes_priority_over_an_axis_representation(self):
        model = create_body_model((10, 20, 30), (40, 50, 60))
        product = model.by_type('IfcBuildingElementProxy')[0]
        context = model.by_type('IfcGeometricRepresentationContext')[0]
        axis_context = ifcopenshell.api.run(
            'context.add_context', model, context_type='Model',
            context_identifier='Axis', target_view='GRAPH_VIEW', parent=context,
        )
        axis = ifcopenshell.api.run(
            'geometry.add_axis_representation', model, context=axis_context,
            axis=((900000., 800000., 0.), (900001., 800000., 0.)),
        )
        product.Representation.Representations = (
            axis, *product.Representation.Representations,
        )
        self.assert_body_vertex(model, CUBE_VERTICES + (50, 70, 90))

    def test_no_body_and_empty_body_return_zero(self):
        for schema in ('IFC2X3', 'IFC4'):
            empty = create_body_model((600000, 4000000, 30), (0, 0, 0), schema)
            empty.by_type('IfcBuildingElementProxy')[0].Representation.Representations[0].Items = ()
            for name, model in (('no body', make_model(schema)), ('empty body', empty)):
                with self.subTest(schema=schema, model=name):
                    np.testing.assert_array_equal(self.origin_without_mutation(model), (0, 0, 0))

    def test_two_dimensional_body_returns_zero(self):
        model = make_model('IFC4')
        context = ifcopenshell.api.run('context.add_context', model, context_type='Plan')
        body = ifcopenshell.api.run(
            'context.add_context', model, context_type='Plan',
            context_identifier='Body', target_view='PLAN_VIEW', parent=context,
        )
        product = ifcopenshell.api.run(
            'root.create_entity', model, ifc_class='IfcBuildingElementProxy',
        )
        representation = ifcopenshell.api.run(
            'geometry.add_axis_representation', model, context=body,
            axis=((600000., 4000000.), (600001., 4000000.)),
        )
        ifcopenshell.api.run(
            'geometry.assign_representation', model, product=product,
            representation=representation,
        )
        np.testing.assert_array_equal(self.origin_without_mutation(model), (0, 0, 0))

    def test_geometry_failure_returns_zero_without_using_placement(self):
        model = create_body_model((600000, 4000000, 30), (0, 0, 0))
        with patch.object(ifcopenshell.geom, 'create_shape', side_effect=RuntimeError('Bad body')) as create:
            np.testing.assert_array_equal(self.origin_without_mutation(model), (0, 0, 0))
        create.assert_called()

    def test_equivalent_geometry_has_the_same_map_anchor(self):
        contexts = []
        for placement, geometry in (
            ((155000, 463000, 30), (0, 0, 0)),
            ((0, 0, 0), (155000, 463000, 30)),
            ((-1000000, -1000000, 0), (1155000, 1463000, 30)),
        ):
            model = create_body_model(placement, geometry)
            georeference_ifc.set_mapconversion_crs(
                model, 'EPSG:28992', 0., 0., 0., 1., 0., 1.,
            )
            contexts.append(self.workflow.build_map_context(model, 'model.ifc'))
        to_geographic = Transformer.from_crs('EPSG:28992', 'EPSG:4326', always_xy=True)
        for context in contexts:
            expected = to_geographic.transform(*context['origin'][:2])
            np.testing.assert_allclose(
                (context['Longitude'], context['Latitude']), expected, rtol=0, atol=1e-10,
            )
            self.assertTrue(155000 <= context['origin'][0] <= 155001)
            self.assertTrue(463000 <= context['origin'][1] <= 463001)

    def test_rebasing_preserves_mapped_vertices_rotation_units_and_mixed_scale(self):
        for schema in ('IFC2X3', 'IFC4'):
            for prefix, epsg, scale in (
                (None, 28992, 1.0002),
                ('MILLI', 28992, 0.0010002),
                ('MILLI', 2263, 0.0010002 / (1200 / 3937)),
                (None, 5070, 1.0002),
            ):
                with self.subTest(schema=schema, prefix=prefix, epsg=epsg):
                    model = create_body_model((200, 300, 4), (500, 600, 2), schema, prefix)
                    georeference_ifc.set_mapconversion_crs(
                        model, f'EPSG:{epsg}', 155000., 463000., 20., .6, .8, scale,
                    )
                    before = model.to_string()
                    context = self.workflow.build_map_context(model, 'model.ifc')
                    anchor = np.array(context['origin'])
                    unit = ifcopenshell.util.unit.calculate_unit_scale(model)
                    map_unit = CRS.from_epsg(epsg).axis_info[0].unit_conversion_factor
                    mapped_anchor = np.array(ifcopenshell.util.geolocation.xyz2enh(
                        *(anchor / unit), 155000., 463000., 20., .6, .8, scale,
                    ))[:2]
                    to_geographic = Transformer.from_crs(epsg, 'EPSG:4326', always_xy=True)
                    np.testing.assert_allclose(
                        (context['Longitude'], context['Latitude']),
                        to_geographic.transform(*mapped_anchor), rtol=0, atol=1e-10,
                    )
                    rotation = np.array([[.6, -.8], [.8, .6]])
                    to_mercator = Transformer.from_crs(epsg, 'EPSG:3857', always_xy=True)
                    circumference = 2 * np.pi * CRS.from_epsg(3857).ellipsoid.semi_major_metre
                    anchor_mercator = np.array([
                        (context['Longitude'] + 180) / 360,
                        (1 - np.log(np.tan(np.pi / 4 + np.radians(context['Latitude']) / 2)) / np.pi) / 2,
                    ])
                    for vertex in CUBE_VERTICES + (700, 900, 6):
                        expected = ifcopenshell.util.geolocation.xyz2enh(
                            *(vertex / unit), 155000., 463000., 20., .6, .8, scale,
                        )
                        # Viewer geometry is in metres; Scale converts metres to map metres.
                        rebased = vertex[:2] - anchor[:2]
                        actual = mapped_anchor + rotation @ rebased * context['Scale'] / map_unit
                        np.testing.assert_allclose(actual, expected[:2], rtol=0, atol=1e-5)
                        # The actual browser basis must include projection scale and convergence.
                        rendered = anchor_mercator + rebased @ np.array(context['MapAxes'])
                        projected = np.array(to_mercator.transform(*expected[:2]))
                        expected_mercator = .5 + projected * (1, -1) / circumference
                        np.testing.assert_allclose(rendered, expected_mercator, rtol=0, atol=1e-10)
                    conversion, _ = georeference_ifc.get_mapconversion_crs(model)
                    self.assertEqual(conversion.Scale, scale)
                    self.assertEqual(model.to_string(), before)

    def test_projection_error_keeps_result_tables_and_download_available(self):
        model = create_body_model((0, 0, 0), (1e9, 1e9, 0))
        georeference_ifc.set_mapconversion_crs(
            model, 'EPSG:32631', 0., 0., 0., 1., 0., 1.,
        )
        with self.assertRaisesRegex(ValueError, 'projection domain'):
            self.workflow.build_map_context(model, 'model.ifc')

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'state').mkdir()
            output = folder / 'model_georeferenced.ifc'
            model.write(str(output))
            original_bytes = output.read_bytes()
            with patch.dict(self.workflow.app.config, {
                'TESTING': True,
                'UPLOAD_FOLDER': str(folder),
                'MODEL_STATE_FOLDER': str(folder / 'state'),
            }):
                client = self.workflow.app.test_client()
                response = client.get('/result/model.ifc')
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                for expected in (
                    'Could not prepare the map viewer',
                    'IFCProjectedCRS Data', 'EPSG:32631',
                    'IFCMapConversion Data', '<td>Scale</td>',
                    'href="/download/model.ifc"',
                ):
                    self.assertIn(expected, body)
                download = client.get('/download/model.ifc')
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.data, original_bytes)
                download.close()
            self.assertEqual(output.read_bytes(), original_bytes)

    def test_map_provider_keys_control_viewer_options(self):
        model = make_model('IFC4')
        georeference_ifc.set_mapconversion_crs(
            model, 'EPSG:28992', 155000., 463000., 0., 1., 0., .001,
        )
        for carto_key, maptiler_key in (
            ('', ''), ('fake-carto-key', ''), ('', 'fake&+maptiler-key'),
            ('fake-carto-key', 'fake&+maptiler-key'),
        ):
            with self.subTest(carto=bool(carto_key), maptiler=bool(maptiler_key)):
                with patch.dict(self.workflow.app.config, {
                    'CARTO_KEY': carto_key, 'MAPTILER_KEY': maptiler_key,
                }):
                    context = self.workflow.build_map_context(model, 'model.ifc')
                self.assertEqual(context['CartoKey'], carto_key)
                self.assertEqual(bool(context['MapTilerStyles']), bool(maptiler_key))
                for style in context['MapTilerStyles']:
                    query = parse_qs(urlparse(style['style']).query)
                    self.assertEqual(query['key'], [maptiler_key])

    def test_legacy_scale_error_uses_the_same_transform_for_anchor_and_mesh(self):
        model = create_body_model((200, 300, 4), (500, 600, 2), prefix='MILLI')
        georeference_ifc.set_mapconversion_crs(
            model, 'EPSG:28992', 155000000., 463000000., 20000., .6, .8, 1.0002,
        )
        before = model.to_string()
        context = self.workflow.build_map_context(model, 'model.ifc', eff=.001, scale_error=True)
        to_geographic = Transformer.from_crs('EPSG:28992', 'EPSG:4326', always_xy=True)
        expected = ifcopenshell.util.geolocation.xyz2enh(
            *context['origin'], 155000., 463000., 20., .6, .8, 1.0002,
        )
        np.testing.assert_allclose(
            (context['Longitude'], context['Latitude']),
            to_geographic.transform(*expected[:2]), rtol=0, atol=1e-10,
        )
        self.assertEqual(context['Scale'], 1.0002)
        self.assertEqual(model.to_string(), before)


if __name__ == '__main__':
    unittest.main()
