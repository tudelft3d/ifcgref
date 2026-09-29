"""Project-hosted IFC2X3 georeferencing and legacy file compatibility."""

import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ifcopenshell
import ifcopenshell.api

import georeference_ifc.main as georef
from test_ifc2x3_georeferencing import SCALE, TRANSFORM, make_model


MAP_NAME = "ePSet_MapConversion"
CRS_NAME = "ePSet_ProjectedCRS"
MAP_VALUES = dict(zip(
    ("TargetCRS", "Eastings", "Northings", "OrthogonalHeight",
     "XAxisAbscissa", "XAxisOrdinate", "Scale"), TRANSFORM
))
CRS_VALUES = {"Name": TRANSFORM[0], "MapUnit": "metre"}


def add_pset(model, host, name, values):
    """Create distinct psets, including duplicates the add_pset API reuses."""
    properties = []
    for key, value in values.items():
        measure = "IfcIdentifier" if isinstance(value, str) else "IfcReal"
        properties.append(model.create_entity(
            "IfcPropertySingleValue", Name=key,
            NominalValue=model.create_entity(measure, value),
        ))
    pset = model.create_entity(
        "IfcPropertySet", GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=host.OwnerHistory, Name=name, HasProperties=properties,
    )
    model.create_entity(
        "IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=host.OwnerHistory, RelatedObjects=[host],
        RelatingPropertyDefinition=pset,
    )
    return pset


def add_pair(model, host, names=(MAP_NAME, CRS_NAME), map_values=None, crs_values=None):
    return (
        add_pset(model, host, names[0], MAP_VALUES if map_values is None else map_values),
        add_pset(model, host, names[1], CRS_VALUES if crs_values is None else crs_values),
    )


def attached_psets(model, host):
    return [
        rel.RelatingPropertyDefinition
        for rel in model.by_type("IfcRelDefinesByProperties")
        if host in rel.RelatedObjects and rel.RelatingPropertyDefinition.is_a("IfcPropertySet")
    ]


def georef_psets(model, host):
    return [p for p in attached_psets(model, host)
            if (p.Name or "").lower() in (MAP_NAME.lower(), CRS_NAME.lower())]


def values(pset):
    return {p.Name: p.NominalValue.wrappedValue for p in pset.HasProperties}


def remove_sites(model):
    for site in model.by_type("IfcSite"):
        model.remove(site)


class Ifc2x3ProjectGeoreferencingTests(unittest.TestCase):
    def assert_project_pair(self, model):
        project = model.by_type("IfcProject")[0]
        psets = georef_psets(model, project)
        self.assertEqual(len(psets), 2)
        self.assertEqual({p.Name for p in psets}, {MAP_NAME, CRS_NAME})
        for site in model.by_type("IfcSite"):
            self.assertEqual(georef_psets(model, site), [])
        return {p.Name: p for p in psets}

    def test_new_writes_use_project_and_canonical_names_without_a_site(self):
        for writer in (georef.set_mapconversion_crs, georef.set_mapconversion_crs_ifc2x3):
            with self.subTest(writer=writer.__name__):
                model = make_model()
                remove_sites(model)
                writer(model, *TRANSFORM, map_unit="metre")
                model = ifcopenshell.file.from_string(model.to_string())
                psets = self.assert_project_pair(model)
                self.assertEqual(values(psets[CRS_NAME]), CRS_VALUES)
                self.assertEqual(values(psets[MAP_NAME]), MAP_VALUES)
                conversion, crs = georef.get_mapconversion_crs(model)
                self.assertEqual(conversion.Scale, SCALE)
                self.assertEqual(crs.MapUnit, "metre")

    def test_templates_use_project_and_canonical_names_with_scale(self):
        template = ifcopenshell.open(
            str(Path(georef.__file__).with_name("IFC2X3_Geolocation.ifc"))
        )
        psets = {p.Name: p for p in template.by_type("IfcPropertySetTemplate")}
        self.assertIn(MAP_NAME, psets)
        self.assertIn(CRS_NAME, psets)
        for name in (MAP_NAME, CRS_NAME):
            self.assertEqual(psets[name].ApplicableEntity, "IfcProject")
        self.assertIn(template.by_id(16), psets[MAP_NAME].HasPropertyTemplates)
        self.assertEqual(template.by_id(16).PrimaryMeasureType, "IfcReal")

    def test_reader_accepts_project_and_site_casing_variants(self):
        for host_type in ("IfcProject", "IfcSite"):
            for prefix in ("ePSet", "ePset", "EPset", "EPSET", "epset"):
                with self.subTest(host=host_type, prefix=prefix):
                    model = make_model()
                    host = model.by_type(host_type)[0]
                    add_pair(model, host, (prefix + "_MapConversion", prefix + "_ProjectedCRS"))
                    if host_type == "IfcProject":
                        remove_sites(model)
                    conversion, crs = georef.get_mapconversion_crs(model)
                    self.assertEqual(conversion.Scale, SCALE)
                    self.assertEqual(crs.Name, TRANSFORM[0])
                    self.assertEqual(crs.MapUnit, "metre")

    def test_reader_accepts_mixed_casing_within_one_pair(self):
        model = make_model()
        add_pair(model, model.by_type("IfcSite")[0],
                 ("EPset_MapConversion", "ePSet_ProjectedCRS"))
        conversion, crs = georef.get_mapconversion_crs(model)
        self.assertEqual(conversion.Scale, SCALE)
        self.assertEqual(crs.Name, TRANSFORM[0])

    def test_reader_prefers_complete_project_pair_over_different_site_pair(self):
        model = make_model()
        add_pair(model, model.by_type("IfcProject")[0])
        add_pair(model, model.by_type("IfcSite")[0],
                 map_values=dict(MAP_VALUES, Eastings=900.0))
        conversion, crs = georef.get_mapconversion_crs(model)
        self.assertEqual(conversion.Eastings, TRANSFORM[1])
        self.assertEqual(crs.MapUnit, "metre")

    def test_reader_falls_back_to_complete_site_pair_when_project_is_partial(self):
        model = make_model()
        add_pset(model, model.by_type("IfcProject")[0], MAP_NAME,
                 dict(MAP_VALUES, Eastings=900.0))
        add_pair(model, model.by_type("IfcSite")[0])
        conversion, crs = georef.get_mapconversion_crs(model)
        self.assertEqual(conversion.Eastings, TRANSFORM[1])
        self.assertEqual(crs.Name, TRANSFORM[0])

    def test_reader_never_combines_partial_pairs_across_hosts(self):
        for project_name, project_values, site_name, site_values in (
            (MAP_NAME, MAP_VALUES, CRS_NAME, CRS_VALUES),
            (CRS_NAME, CRS_VALUES, MAP_NAME, MAP_VALUES),
        ):
            with self.subTest(project_name=project_name):
                model = make_model()
                add_pset(model, model.by_type("IfcProject")[0], project_name, project_values)
                add_pset(model, model.by_type("IfcSite")[0], site_name, site_values)
                self.assertEqual(georef.get_mapconversion_crs(model), (None, None))

        model = make_model()
        other_site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite")
        add_pset(model, model.by_type("IfcSite")[0], MAP_NAME, MAP_VALUES)
        add_pset(model, other_site, CRS_NAME, CRS_VALUES)
        self.assertEqual(georef.get_mapconversion_crs(model), (None, None))

    def test_empty_model_and_partial_pair_return_no_georeference(self):
        for with_site in (True, False):
            with self.subTest(with_site=with_site):
                model = make_model()
                if not with_site:
                    remove_sites(model)
                self.assertEqual(georef.get_mapconversion_crs(model), (None, None))
                add_pset(model, model.by_type("IfcProject")[0], MAP_NAME, MAP_VALUES)
                self.assertEqual(georef.get_mapconversion_crs(model), (None, None))

    def test_reader_rejects_conflicting_duplicates_at_selected_host(self):
        for host_type in ("IfcProject", "IfcSite"):
            with self.subTest(host=host_type):
                model = make_model()
                host = model.by_type(host_type)[0]
                add_pair(model, host)
                add_pset(model, host, "EPset_MapConversion", dict(MAP_VALUES, Scale=1.0))
                with self.assertRaises(ValueError):
                    georef.get_mapconversion_crs(model)

    def test_reader_rejects_conflicting_complete_site_pairs(self):
        model = make_model()
        add_pair(model, model.by_type("IfcSite")[0])
        second_site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite")
        add_pair(model, second_site, map_values=dict(MAP_VALUES, Scale=1.0))
        with self.assertRaises(ValueError):
            georef.get_mapconversion_crs(model)

    def test_migration_preserves_metadata_unit_and_unrelated_properties(self):
        model = make_model()
        site = model.by_type("IfcSite")[0]
        legacy_map, legacy_crs = add_pair(
            model, site, ("ePset_MapConversion", "EPset_ProjectedCRS"),
            map_values=dict(MAP_VALUES, SurveyReference="survey-2026"),
            crs_values=dict(CRS_VALUES, Description="Dutch grid", GeodeticDatum="Amersfoort"),
        )
        legacy_crs.Description = "CRS metadata retained during migration"
        unrelated = add_pset(model, site, "Pset_SiteCommon", {"LandTitleNumber": "ABC"})
        near_match = add_pset(model, site, MAP_NAME + "Backup", {"Scale": 3.0})
        untouched = {p.id(): p.to_string() for p in (unrelated, near_match)}
        georef.set_mapconversion_crs(model, *TRANSFORM)
        psets = self.assert_project_pair(model)
        self.assertEqual(values(psets[MAP_NAME])["SurveyReference"], "survey-2026")
        self.assertEqual(values(psets[CRS_NAME])["MapUnit"], "metre")
        self.assertEqual(values(psets[CRS_NAME])["Description"], "Dutch grid")
        self.assertEqual(values(psets[CRS_NAME])["GeodeticDatum"], "Amersfoort")
        self.assertEqual(psets[CRS_NAME].Description, "CRS metadata retained during migration")
        for entity_id, serialized in untouched.items():
            self.assertEqual(model.by_id(entity_id).to_string(), serialized)
            self.assertIn(model.by_id(entity_id), attached_psets(model, site))
        model = ifcopenshell.file.from_string(model.to_string())
        conversion, crs = georef.get_mapconversion_crs(model)
        self.assertEqual(conversion.Scale, SCALE)
        self.assertEqual(crs.MapUnit, "metre")

    def test_migration_without_mapunit_keeps_property_absent(self):
        model = make_model()
        add_pair(model, model.by_type("IfcSite")[0], crs_values={"Name": TRANSFORM[0]})
        georef.set_mapconversion_crs(model, *TRANSFORM)
        self.assertNotIn("MapUnit", values(self.assert_project_pair(model)[CRS_NAME]))

    def test_repeated_writes_leave_one_project_pair_and_update_values(self):
        model = make_model()
        add_pair(model, model.by_type("IfcSite")[0])
        georef.set_mapconversion_crs(model, *TRANSFORM, map_unit="metre")
        changed = ("EPSG:28992", 22.0, *TRANSFORM[2:])
        for _ in range(3):
            georef.set_mapconversion_crs(model, *changed, map_unit="US survey foot")
        psets = self.assert_project_pair(model)
        self.assertEqual(len(model.by_type("IfcPropertySet")), 2)
        self.assertEqual(values(psets[MAP_NAME])["Eastings"], 22.0)
        self.assertEqual(values(psets[MAP_NAME])["TargetCRS"], "EPSG:28992")
        self.assertEqual(values(psets[MAP_NAME])["Scale"], SCALE)
        self.assertEqual(values(psets[CRS_NAME])["Name"], "EPSG:28992")
        self.assertEqual(values(psets[CRS_NAME])["MapUnit"], "US survey foot")

    def test_equal_duplicate_pairs_across_sites_are_readable_and_migrate_once(self):
        model = make_model()
        first_site = model.by_type("IfcSite")[0]
        second_site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite")
        add_pair(model, first_site, ("ePset_MapConversion", "ePset_ProjectedCRS"))
        add_pair(model, second_site, ("EPset_MapConversion", "EPset_ProjectedCRS"))
        conversion, crs = georef.get_mapconversion_crs(model)
        self.assertEqual(conversion.Scale, SCALE)
        self.assertEqual(crs.MapUnit, "metre")
        georef.set_mapconversion_crs(model, *TRANSFORM)
        self.assert_project_pair(model)
        self.assertEqual(len(model.by_type("IfcPropertySet")), 2)

    def test_equal_duplicate_names_on_one_host_are_consolidated(self):
        model = make_model()
        project = model.by_type("IfcProject")[0]
        add_pair(model, project)
        add_pair(model, project, ("EPset_MapConversion", "EPset_ProjectedCRS"))
        conversion, crs = georef.get_mapconversion_crs(model)
        self.assertEqual(conversion.Scale, SCALE)
        self.assertEqual(crs.MapUnit, "metre")
        georef.set_mapconversion_crs(model, *TRANSFORM)
        self.assert_project_pair(model)
        self.assertEqual(len(model.by_type("IfcPropertySet")), 2)

    def test_legacy_integer_scale_is_replaced_with_real_without_truncation(self):
        model = make_model()
        conversion, _ = add_pair(model, model.by_type("IfcSite")[0])
        scale = next(p for p in conversion.HasProperties if p.Name == "Scale")
        scale.NominalValue = model.create_entity("IfcInteger", 1)
        georef.set_mapconversion_crs(model, *TRANSFORM)
        model = ifcopenshell.file.from_string(model.to_string())
        pset = self.assert_project_pair(model)[MAP_NAME]
        scale = next(p for p in pset.HasProperties if p.Name == "Scale")
        self.assertEqual(scale.NominalValue.is_a(), "IfcReal")
        self.assertEqual(scale.NominalValue.wrappedValue, SCALE)

    def test_shared_legacy_psets_leave_other_objects_unchanged(self):
        for separate_relationship in (False, True):
            with self.subTest(separate_relationship=separate_relationship):
                model = make_model()
                site = model.by_type("IfcSite")[0]
                building = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding")
                legacy = add_pair(model, site, ("ePset_MapConversion", "ePset_ProjectedCRS"))
                for pset in legacy:
                    if separate_relationship:
                        model.create_entity(
                            "IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(),
                            OwnerHistory=building.OwnerHistory, RelatedObjects=[building],
                            RelatingPropertyDefinition=pset,
                        )
                    else:
                        relationship = next(rel for rel in model.by_type("IfcRelDefinesByProperties")
                                            if rel.RelatingPropertyDefinition == pset)
                        relationship.RelatedObjects = [site, building]
                before = {p.id(): p.to_string() for p in legacy}
                georef.set_mapconversion_crs(
                    model, *TRANSFORM[:-1], SCALE * 2, map_unit="US survey foot"
                )
                project_pair = self.assert_project_pair(model)
                self.assertEqual(values(project_pair[MAP_NAME])["Scale"], SCALE * 2)
                self.assertEqual(values(project_pair[CRS_NAME])["MapUnit"], "US survey foot")
                self.assertEqual({p.id() for p in attached_psets(model, building)}, set(before))
                for entity_id, serialized in before.items():
                    self.assertEqual(model.by_id(entity_id).to_string(), serialized)
                self.assertEqual(values(legacy[0])["Scale"], SCALE)
                self.assertEqual(values(legacy[1])["MapUnit"], "metre")

    def test_shared_properties_preserve_metadata_without_changing_other_pset(self):
        model = make_model()
        site = model.by_type("IfcSite")[0]
        conversion, _ = add_pair(model, site)
        easting = next(p for p in conversion.HasProperties if p.Name == "Eastings")
        easting.Description = "Surveyed easting with its supplied unit"
        easting.Unit = ifcopenshell.api.run("unit.add_si_unit", model, unit_type="LENGTHUNIT")
        unrelated = add_pset(model, site, "Pset_SurveyArchive", {"Reference": "kept"})
        unrelated.HasProperties = list(unrelated.HasProperties) + [easting]
        original = easting.to_string()
        unit_id = easting.Unit.id()
        changed = (TRANSFORM[0], 123.0, *TRANSFORM[2:])
        georef.set_mapconversion_crs(model, *changed)
        pset = self.assert_project_pair(model)[MAP_NAME]
        migrated = next(p for p in pset.HasProperties if p.Name == "Eastings")
        self.assertEqual(migrated.NominalValue.wrappedValue, 123.0)
        self.assertEqual(migrated.Description, "Surveyed easting with its supplied unit")
        self.assertEqual(migrated.Unit.id(), unit_id)
        self.assertNotEqual(migrated.id(), easting.id())
        self.assertEqual(easting.to_string(), original)
        self.assertIn(easting, unrelated.HasProperties)
        self.assertIn(unrelated, attached_psets(model, site))

    def test_writer_rejects_conflicts_before_modifying_any_entities(self):
        for conflict in ("same_host", "second_site", "project_site", "mapunit"):
            with self.subTest(conflict=conflict):
                model = make_model()
                site = model.by_type("IfcSite")[0]
                add_pair(model, site)
                if conflict == "same_host":
                    add_pset(model, site, "EPset_MapConversion", dict(MAP_VALUES, Scale=1.0))
                elif conflict == "second_site":
                    second_site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite")
                    add_pair(model, second_site, map_values=dict(MAP_VALUES, Eastings=9.0))
                elif conflict == "project_site":
                    add_pair(model, model.by_type("IfcProject")[0], map_values=dict(MAP_VALUES, Scale=1.0))
                else:
                    add_pset(model, site, "EPset_ProjectedCRS", dict(CRS_VALUES, MapUnit="foot"))
                before = model.to_string()
                with self.assertRaises(ValueError):
                    georef.set_mapconversion_crs(model, *TRANSFORM, map_unit="metre")
                self.assertEqual(model.to_string(), before)

    def test_project_and_shared_site_duplicates_preserve_property_metadata(self):
        model = make_model()
        project = model.by_type('IfcProject')[0]
        site = model.by_type('IfcSite')[0]
        building = ifcopenshell.api.run('root.create_entity', model, ifc_class='IfcBuilding')
        project_map, _ = add_pair(model, project, crs_values={'Name': TRANSFORM[0]})
        site_map, site_crs = add_pair(model, site)
        unit = ifcopenshell.api.run('unit.add_si_unit', model, unit_type='LENGTHUNIT')
        for pset in (project_map, site_map):
            easting = next(p for p in pset.HasProperties if p.Name == 'Eastings')
            easting.Description = 'Survey easting'
            easting.Unit = unit
        map_unit = next(p for p in site_crs.HasProperties if p.Name == 'MapUnit')
        map_unit.Description = 'Resolved axis unit'
        for pset in (site_map, site_crs):
            ifcopenshell.api.run('pset.assign_pset', model, products=[building], pset=pset)
        changed = (TRANSFORM[0], 123.0, *TRANSFORM[2:])
        georef.set_mapconversion_crs(model, *changed, map_unit='US survey foot')
        psets = self.assert_project_pair(model)
        easting = next(p for p in psets[MAP_NAME].HasProperties if p.Name == 'Eastings')
        self.assertEqual(easting.NominalValue.wrappedValue, 123.0)
        self.assertEqual(easting.Description, 'Survey easting')
        self.assertEqual(easting.Unit, unit)
        migrated_unit = next(p for p in psets[CRS_NAME].HasProperties if p.Name == 'MapUnit')
        self.assertEqual(migrated_unit.Description, 'Resolved axis unit')
        self.assertEqual(migrated_unit.NominalValue.wrappedValue, 'US survey foot')
        self.assertEqual(values(site_map)['Eastings'], TRANSFORM[1])
        self.assertEqual(map_unit.NominalValue.wrappedValue, 'metre')
        self.assertEqual(set(attached_psets(model, building)), {site_map, site_crs})

    def test_writer_requires_one_project_before_migration(self):
        for project_count in (0, 2):
            with self.subTest(project_count=project_count):
                model = make_model()
                add_pair(model, model.by_type('IfcSite')[0])
                if project_count == 0:
                    model.remove(model.by_type('IfcProject')[0])
                else:
                    ifcopenshell.api.run('root.create_entity', model, ifc_class='IfcProject')
                before = model.to_string()
                with self.assertRaisesRegex(ValueError, 'exactly one IfcProject'):
                    georef.set_mapconversion_crs(model, *TRANSFORM)
                self.assertEqual(model.to_string(), before)

    def test_conflicting_descriptions_or_units_leave_model_untouched(self):
        for conflict in ('pset_description', 'property_description', 'unit'):
            with self.subTest(conflict=conflict):
                model = make_model()
                first, _ = add_pair(model, model.by_type('IfcProject')[0])
                second, _ = add_pair(model, model.by_type('IfcSite')[0])
                if conflict == 'pset_description':
                    first.Description = 'Project survey'
                    second.Description = 'Site survey'
                else:
                    prop = next(p for p in second.HasProperties if p.Name == 'Eastings')
                    if conflict == 'property_description':
                        prop.Description = 'Conflicting annotation'
                    else:
                        prop.Unit = ifcopenshell.api.run(
                            'unit.add_si_unit', model, unit_type='LENGTHUNIT'
                        )
                before = model.to_string()
                with self.assertRaises(ValueError):
                    georef.set_mapconversion_crs(model, *TRANSFORM)
                self.assertEqual(model.to_string(), before)


class WorkflowProjectGeoreferencingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.import_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.import_dir.cleanup)
        with patch.dict(os.environ, {"IFCGREF_UPLOAD_FOLDER": cls.import_dir.name}):
            cls.workflow = importlib.import_module("app")

    def test_detection_accepts_project_pair_without_site(self):
        model = make_model()
        add_pair(model, model.by_type("IfcProject")[0])
        remove_sites(model)
        message, detected = self.workflow.georef(model)
        self.assertTrue(detected)
        self.assertIn("IFC file is georeferenced", message)

    def test_detection_accepts_legacy_uppercase_site_pair(self):
        model = make_model()
        add_pair(model, model.by_type("IfcSite")[0],
                 ("EPset_MapConversion", "EPset_ProjectedCRS"))
        self.assertTrue(self.workflow.georef(model)[1])

    def test_detection_rejects_partial_pair_on_separate_hosts(self):
        model = make_model()
        add_pset(model, model.by_type("IfcProject")[0], MAP_NAME, MAP_VALUES)
        add_pset(model, model.by_type("IfcSite")[0], CRS_NAME, CRS_VALUES)
        self.assertFalse(self.workflow.georef(model)[1])

    def test_result_page_reports_conflicting_georeferences(self):
        model = make_model()
        project = model.by_type('IfcProject')[0]
        add_pair(model, project)
        add_pset(model, project, 'EPset_MapConversion', dict(MAP_VALUES, Scale=1.0))
        with self.workflow.app.test_request_context('/result/model.ifc'):
            with patch.object(self.workflow, 'fileOpener', return_value=model):
                with patch.object(self.workflow, 'render_template', return_value='conflict') as render:
                    result = self.workflow.render_georef_result('model.ifc')
        self.assertEqual(result, 'conflict')
        self.assertEqual(render.call_args.args[0], 'convert.html')
        self.assertIn('Conflicting IFC2X3 georeferencing', render.call_args.kwargs['message'])


if __name__ == "__main__":
    unittest.main()
