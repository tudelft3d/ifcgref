import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ifcopenshell
import ifcopenshell.api

import georeference_ifc.main as georef


SCALE = 0.0010002  # Millimetres to metres, including a survey scale of 1.0002.
TRANSFORM = ("EPSG:7415", 185542.0, 428121.0, 20.0, 1.0, 0.0, SCALE)


def make_model(schema="IFC2X3"):
    model = ifcopenshell.file(schema=schema)
    person = model.create_entity("IfcPerson", FamilyName="Tester")
    organization = model.create_entity("IfcOrganization", Name="Test")
    model.create_entity("IfcPersonAndOrganization", person, organization)
    model.create_entity("IfcApplication", organization, "1", "Test", "Test")
    ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject")
    unit = ifcopenshell.api.run(
        "unit.add_si_unit", model, unit_type="LENGTHUNIT", prefix="MILLI"
    )
    ifcopenshell.api.run("unit.assign_unit", model, units=[unit])
    ifcopenshell.api.run("context.add_context", model, context_type="Model")
    ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite")
    return model


def properties(model, name):
    pset = next(p for p in model.by_type("IfcPropertySet") if p.Name == name)
    return {p.Name: p for p in pset.HasProperties}


class Ifc2x3GeoreferencingTests(unittest.TestCase):
    def test_mapunit_type_and_mixed_scale_survive_step_roundtrip(self):
        for writer in (georef.set_mapconversion_crs, georef.set_mapconversion_crs_ifc2x3):
            for unit in ("metre", "US survey foot"):
                with self.subTest(writer=writer.__name__, unit=unit):
                    model = make_model()
                    writer(model, *TRANSFORM, map_unit=unit)
                    model = ifcopenshell.file.from_string(model.to_string())
                    crs = properties(model, "ePSet_ProjectedCRS")
                    conversion = properties(model, "ePSet_MapConversion")
                    self.assertEqual(crs["Name"].NominalValue.wrappedValue, "EPSG:7415")
                    self.assertEqual(crs["MapUnit"].NominalValue.is_a(), "IfcIdentifier")
                    self.assertEqual(crs["MapUnit"].NominalValue.wrappedValue, unit)
                    self.assertEqual(conversion["Scale"].NominalValue.is_a(), "IfcReal")
                    self.assertEqual(conversion["Scale"].NominalValue.wrappedValue, SCALE)
                    for name, expected in zip(
                        ("Eastings", "Northings", "OrthogonalHeight"), TRANSFORM[1:4]
                    ):
                        self.assertEqual(conversion[name].NominalValue.wrappedValue, expected)
                    read_conversion, read_crs = georef.get_mapconversion_crs(model)
                    self.assertEqual(read_conversion.Scale, SCALE)
                    self.assertEqual(read_crs.MapUnit, unit)

    def test_old_positional_calls_and_explicit_none_omit_mapunit(self):
        for writer in (georef.set_mapconversion_crs, georef.set_mapconversion_crs_ifc2x3):
            for kwargs in ({}, {"map_unit": None}):
                with self.subTest(writer=writer.__name__, kwargs=kwargs):
                    model = make_model()
                    writer(model, *TRANSFORM, **kwargs)
                    crs = properties(model, "ePSet_ProjectedCRS")
                    self.assertEqual(set(crs), {"Name"})
                    self.assertEqual(
                        properties(model, "ePSet_MapConversion")["Scale"].NominalValue.wrappedValue,
                        SCALE,
                    )

    def test_omitted_mapunit_preserves_existing_property(self):
        model = make_model()
        georef.set_mapconversion_crs(model, *TRANSFORM, map_unit="metre")
        for kwargs in ({}, {"map_unit": None}):
            with self.subTest(kwargs=kwargs):
                georef.set_mapconversion_crs(model, *TRANSFORM, **kwargs)
                self.assertEqual(
                    properties(model, "ePSet_ProjectedCRS")["MapUnit"].NominalValue.wrappedValue,
                    "metre",
                )

    def test_integer_scale_uses_template_real_type(self):
        model = make_model()
        georef.set_mapconversion_crs(model, *TRANSFORM[:-1], 1)
        scale = properties(model, "ePSet_MapConversion")["Scale"].NominalValue
        self.assertEqual(scale.is_a(), "IfcReal")
        self.assertEqual(scale.wrappedValue, 1.0)

    def test_template_declares_mapunit_and_scale(self):
        template = ifcopenshell.open(
            str(Path(georef.__file__).with_name("IFC2X3_Geolocation.ifc"))
        )
        psets = {p.Name: p for p in template.by_type("IfcPropertySetTemplate")}
        crs = {p.Name: p for p in psets["ePSet_ProjectedCRS"].HasPropertyTemplates}
        conversion = {p.Name: p for p in psets["ePSet_MapConversion"].HasPropertyTemplates}
        self.assertEqual(crs["MapUnit"].PrimaryMeasureType, "IfcIdentifier")
        self.assertIn(template.by_id(16), psets["ePSet_MapConversion"].HasPropertyTemplates)
        self.assertEqual(conversion["Scale"].PrimaryMeasureType, "IfcReal")

    def test_ifc4_behavior_is_unchanged(self):
        for kwargs in ({}, {"map_unit": "metre"}):
            with self.subTest(kwargs=kwargs):
                model = make_model("IFC4")
                georef.set_mapconversion_crs(model, *TRANSFORM, **kwargs)
                conversion = model.by_type("IfcMapConversion")[0]
                self.assertEqual(conversion.Scale, SCALE)
                self.assertEqual(conversion.TargetCRS.Name, "EPSG:7415")
                self.assertIsNone(conversion.TargetCRS.MapUnit)
                self.assertEqual(model.by_type("IfcPropertySet"), [])


class WorkflowGeoreferencingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Importing app creates upload directories and runs retention cleanup.
        # Keep those effects away from a developer's uploads.
        cls.import_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.import_dir.cleanup)
        with patch.dict(os.environ, {"IFCGREF_UPLOAD_FOLDER": cls.import_dir.name}):
            cls.workflow = importlib.import_module("app")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        (self.folder / "state").mkdir()
        (self.folder / "jobs").mkdir()
        config = patch.dict(self.workflow.app.config, {
            "TESTING": True,
            "UPLOAD_FOLDER": str(self.folder),
            "MODEL_STATE_FOLDER": str(self.folder / "state"),
            "JOB_FOLDER": str(self.folder / "jobs"),
        })
        config.start()
        self.addCleanup(config.stop)
        self.filename = "model.ifc"
        make_model().write(str(self.folder / self.filename))
        self.context = {"coeff": 0.001, "rows": 1, "Refl": False, "target_epsg": 7415}
        self.form = {
            "x0": "0", "y0": "0", "z0": "0",
            "x_prime0": "185542", "y_prime0": "428121", "z_prime0": "20",
        }

    def read_output(self, result):
        return ifcopenshell.open(str(self.folder / result["output_filename"]))

    def test_calculate_carries_stored_unit_through_job_to_output(self):
        for unit in ("metre", "US survey foot", None):
            with self.subTest(unit=unit):
                state = dict(self.context)
                if unit is not None:
                    state["mapunit"] = unit
                self.workflow.write_model_state(self.filename, state)
                with patch.object(self.workflow, "start_job", return_value="test-job") as start:
                    response = self.workflow.app.test_client().post(
                        f"/calc/{self.filename}", data=self.form
                    )
                self.assertEqual(response.status_code, 302)
                job_context = start.call_args.kwargs["context"]
                self.assertEqual(job_context.get("mapunit"), unit)
                result = start.call_args.args[1](
                    Mock(), self.filename, self.form, job_context
                )
                output = self.read_output(result)
                crs = properties(output, "ePSet_ProjectedCRS")
                if unit is None:
                    self.assertNotIn("MapUnit", crs)
                else:
                    self.assertEqual(crs["MapUnit"].NominalValue.wrappedValue, unit)
                self.assertEqual(
                    properties(output, "ePSet_MapConversion")["Scale"].NominalValue.wrappedValue,
                    0.001,
                )

    def test_old_job_context_without_mapunit_still_writes(self):
        result = self.workflow.write_georeferenced_ifc(
            Mock(), self.filename, self.form, self.context
        )
        self.assertNotIn("MapUnit", properties(self.read_output(result), "ePSet_ProjectedCRS"))

    def test_solver_mixed_scale_is_preserved_exactly_in_output(self):
        context = dict(self.context, rows=2, mapunit="metre")
        form = dict(self.form, x1="1000", y1="1000", z1="1000",
                    x_prime1="185543.0002", y_prime1="428122.0002", z_prime1="21.0002")
        # Fix the solver result to test serialization independently of convergence.
        solution = self.workflow.np.array([SCALE, 0.0, 185542.0, 428121.0, 20.0])
        with patch.object(self.workflow, "leastsq", return_value=(solution,)) as solver:
            result = self.workflow.write_georeferenced_ifc(
                Mock(), self.filename, form, context
            )
        solver.assert_called_once()
        output = self.read_output(result)
        scale = properties(output, "ePSet_MapConversion")["Scale"].NominalValue
        self.assertEqual(scale.is_a(), "IfcReal")
        self.assertEqual(scale.wrappedValue, SCALE)
        self.assertEqual(
            properties(output, "ePSet_ProjectedCRS")["MapUnit"].NominalValue.wrappedValue,
            "metre",
        )


if __name__ == "__main__":
    unittest.main()
