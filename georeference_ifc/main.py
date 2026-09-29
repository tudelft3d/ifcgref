import os
import ifcopenshell
import ifcopenshell.util.unit
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.pset
import math
from typing import Optional


_GEOREFERENCE_PSETS = ('ePSet_MapConversion', 'ePSet_ProjectedCRS')


def _georeferencing_psets(hosts):
    """Collect direct assignments without hiding duplicate or legacy names."""
    result = {name: [] for name in _GEOREFERENCE_PSETS}
    names = {name.lower(): name for name in result}
    for host in hosts:
        for relation in getattr(host, 'IsDefinedBy', ()):
            if not relation.is_a('IfcRelDefinesByProperties'):
                continue
            pset = relation.RelatingPropertyDefinition
            if not pset.is_a('IfcPropertySet'):
                continue
            name = names.get((pset.Name or '').lower())
            if name and pset not in result[name]:
                result[name].append(pset)
    return result


def _merged_properties(psets):
    """Merge compatible metadata; refuse ambiguous values before editing."""
    properties = {}
    for pset in psets:
        for prop in pset.HasProperties or ():
            previous = properties.get(prop.Name)
            if previous is not None and previous != prop:
                # Compare the complete single-value property, including type,
                # description and explicit Unit. Other property kinds must be
                # the same entity to be safely consolidated.
                same = (
                    previous.is_a('IfcPropertySingleValue')
                    and prop.is_a('IfcPropertySingleValue')
                    and previous.get_info(include_identifier=False, recursive=True)
                    == prop.get_info(include_identifier=False, recursive=True)
                )
                if not same:
                    raise ValueError(
                        f"Conflicting IFC2X3 georeferencing property '{prop.Name}'. "
                        "Reconcile the project/site property sets before rewriting."
                    )
            if previous is None:
                properties[prop.Name] = prop
    return properties


def _project_georeferencing_psets(ifc_file):
    """Consolidate legacy assignments onto the single project without data loss."""
    projects = ifc_file.by_type('IfcProject')
    if len(projects) != 1:
        raise ValueError('IFC2X3 georeferencing requires exactly one IfcProject.')
    project = projects[0]
    sites = ifc_file.by_type('IfcSite')
    hosts = [project] + sites
    existing = _georeferencing_psets(hosts)

    # Validate both sets before changing either one, including metadata that
    # would otherwise be lost when duplicates are consolidated.
    merged = {name: _merged_properties(psets) for name, psets in existing.items()}
    descriptions = {}
    for name, psets in existing.items():
        values = {pset.Description for pset in psets if pset.Description}
        if len(values) > 1:
            raise ValueError(f"Conflicting IFC2X3 georeferencing descriptions for {name}.")
        descriptions[name] = next(iter(values), None)

    result = {}
    for name, psets in existing.items():
        if psets:
            pset = psets[0]
            other_users = ifcopenshell.util.element.get_elements_by_pset(pset) - set(hosts)
            if other_users:
                pset = ifcopenshell.util.element.copy(ifc_file, pset)
            owned_properties = set(pset.HasProperties or ())
            # Isolate shared property entities so edit_pset retains their
            # descriptions and explicit units without editing another user's data.
            pset.HasProperties = tuple(
                ifcopenshell.util.element.copy(ifc_file, prop)
                if prop not in owned_properties or ifc_file.get_total_inverses(prop) > 1 else prop
                for prop in merged[name].values()
            )
            pset.Name = name
            pset.Description = descriptions[name]
            ifcopenshell.api.run('pset.assign_pset', ifc_file, products=[project], pset=pset)
            ifcopenshell.api.run('pset.unassign_pset', ifc_file, products=sites, pset=pset)
        else:
            pset = ifcopenshell.api.run('pset.add_pset', ifc_file, product=project, name=name)
        for old in psets:
            if old == pset:
                continue
            ifcopenshell.api.run('pset.unassign_pset', ifc_file, products=hosts, pset=old)
            if not ifc_file.get_inverse(old):
                ifcopenshell.util.element.remove_deep2(ifc_file, old)
        result[name] = pset
    return result


def _read_ifc2x3_georeferencing(ifc_file):
    """Prefer complete project definitions, falling back to legacy site pairs."""
    for host_type in ('IfcProject', 'IfcSite'):
        complete = []
        for host in ifc_file.by_type(host_type):
            sets = _georeferencing_psets([host])
            if all(sets.values()):
                complete.append(host)
        if not complete:
            continue
        sets = _georeferencing_psets(complete)
        result = []
        for name in _GEOREFERENCE_PSETS:
            props = _merged_properties(sets[name])
            values = {'id': sets[name][0].id()}
            values.update(ifcopenshell.util.element.get_properties(list(props.values())))
            result.append(values)
        return result
    return None



def set_mapconversion_crs(ifc_file: ifcopenshell.file,
                          target_crs_epsg_code: str,
                          eastings: float,
                          northings: float,
                          orthogonal_height: float,
                          x_axis_abscissa: float,
                          x_axis_ordinate: float,
                          scale: float,
                          map_unit: Optional[str] = None) -> None:
    """
    This method adds IFC map conversion information to an IfcOpenShell file.
    IFC map conversion information indicates how the local coordinate reference system of the IFC file
    can be converted into a global coordinate reference system. The latter is the IfcProjectedCRS.

    The method detects whether the schema of the IFC file is IFC2X3 or IFC4.
    In case of IFC4, an IfcMapConversion and IfcProjectedCRS are created and associated with IfcProject.
    For IFC2X3, corresponding property sets are created and associated with IfcProject.
    Compatible legacy site property sets are migrated; conflicting definitions raise ValueError.

    :param ifc_file: ifcopenshell.file
        the IfcOpenShell file object in which IfcMapConversion information is inserted.
    :param target_crs_epsg_code: str
        the EPSG-code of the target coordinate reference system formatted as a string (e.g. EPSG:2169).
        According to the IFC specification, only a projected coordinate reference system
        with cartesian coordinates can be used.
    :param eastings: float
        the coordinate shift (translation) that is added to an x-coordinate to convert it
        into the Eastings of the target reference system.
    :param northings: float
        the coordinate shift (translation) that is added to an y-coordinate to convert it
        into the Northings of the target reference system.
    :param orthogonal_height: float
        the coordinate shift (translation) that is applied to a z-coordinate to convert it
        into a height relative to the vertical datum of the target reference system.
    :param x_axis_abscissa: float
        defines a rotation (together with x_axis_ordinate) around the z-axis of the local coordinate system
        to orient it according to the target reference system.
        x_axis_abscissa is the component of a unit vector along the x-axis of the local reference system projected
        on the Eastings axis of the target reference system.
    :param x_axis_ordinate: float
        defines a rotation (together with x_axis_abscissa) around the z-axis of the local coordinate system
        to orient it  according to the target reference system.
        x_axis_abscissa is the component of a unit vector along the x-axis of the local reference system projected
        on the Northings axis of the target reference system.
    :param scale: float
        indicates the conversion factor to be used, to convert the units of the local coordinate
        system into the units of the target CRS (often expressed in metres).
        May also include a geometric scale factor; the supplied value is written unchanged.
    :param map_unit: str, optional
        name of the target CRS axis unit, written to ePSet_ProjectedCRS.MapUnit for IFC2X3.
        If None, MapUnit is left untouched. This argument does not affect IFC4 output.
    """
    if ifc_file.schema[:4] == 'IFC4':
        set_mapconversion_crs_ifc4(ifc_file, target_crs_epsg_code, eastings, northings, orthogonal_height,
                                   x_axis_abscissa,
                                   x_axis_ordinate, scale)
    if ifc_file.schema == 'IFC2X3':
        set_mapconversion_crs_ifc2x3(ifc_file, target_crs_epsg_code, eastings, northings, orthogonal_height,
                                     x_axis_abscissa, x_axis_ordinate, scale, map_unit)


def set_si_units(ifc_file: ifcopenshell.file):
    """
    This method adds standardized units to an IFC file.

    :param ifc_file:
    """
    lengthunit = ifcopenshell.api.run("unit.add_si_unit", ifc_file, unit_type="LENGTHUNIT", name="METRE", prefix=None)
    areaunit = ifcopenshell.api.run("unit.add_si_unit", ifc_file, unit_type="AREAUNIT", name="SQUARE_METRE",
                                    prefix=None)
    volumeunit = ifcopenshell.api.run("unit.add_si_unit", ifc_file, unit_type="VOLUMEUNIT", name="CUBIC_METRE",
                                      prefix=None)
    ifcopenshell.api.run("unit.assign_unit", ifc_file, units=[lengthunit, areaunit, volumeunit])


def set_mapconversion_crs_ifc4(ifc_file: ifcopenshell.file,
                               target_crs_epsg_code: str,
                               eastings: float,
                               northings: float,
                               orthogonal_height: float,
                               x_axis_abscissa: float,
                               x_axis_ordinate: float,
                               scale: float) -> None:
    # we assume that the IFC file only has one IfcProject entity.
    source_crs = ifc_file.by_type('IfcProject')[0].RepresentationContexts[0]
    target_crs = ifc_file.createIfcProjectedCRS(
        Name=target_crs_epsg_code
    )
    ifc_file.createIfcMapConversion(
        SourceCRS=source_crs,
        TargetCRS=target_crs,
        Eastings=eastings,
        Northings=northings,
        OrthogonalHeight=orthogonal_height,
        XAxisAbscissa=x_axis_abscissa,
        XAxisOrdinate=x_axis_ordinate,
        Scale=scale
    )


def set_mapconversion_crs_ifc2x3(ifc_file: ifcopenshell.file,
                                 target_crs_epsg_code: str,
                                 eastings: float,
                                 northings: float,
                                 orthogonal_height: float,
                                 x_axis_abscissa: float,
                                 x_axis_ordinate: float,
                                 scale: float,
                                 map_unit: Optional[str] = None) -> None:
    # Open the IFC property set template provided by OSarch.org on https://wiki.osarch.org/index.php?title=File:IFC2X3_Geolocation.ifc
    ifc_template = ifcopenshell.open(os.path.join(os.path.dirname(__file__), './IFC2X3_Geolocation.ifc'))
    map_conversion_template = \
        [t for t in ifc_template.by_type('IfcPropertySetTemplate') if t.Name == 'ePSet_MapConversion'][0]
    crs_template = [t for t in ifc_template.by_type('IfcPropertySetTemplate') if t.Name == 'ePSet_ProjectedCRS'][0]

    psets = _project_georeferencing_psets(ifc_file)
    pset0 = psets['ePSet_MapConversion']
    ifcopenshell.api.run("pset.edit_pset", ifc_file, pset=pset0, properties={'TargetCRS':target_crs_epsg_code,
                                                                            'Eastings': eastings,
                                                                            'Northings': northings,
                                                                            'OrthogonalHeight': orthogonal_height,
                                                                            'XAxisAbscissa': x_axis_abscissa,
                                                                            'XAxisOrdinate': x_axis_ordinate,
                                                                            'Scale': ifc_file.createIfcReal(scale)},
                         pset_template=map_conversion_template)
    pset1 = psets['ePSet_ProjectedCRS']
    crs_properties = {'Name': target_crs_epsg_code}
    if map_unit is not None:
        crs_properties['MapUnit'] = ifc_file.createIfcIdentifier(map_unit)
    ifcopenshell.api.run("pset.edit_pset", ifc_file, pset=pset1, properties=crs_properties,
                         pset_template=crs_template)

def get_mapconversion_crs(ifc_file: ifcopenshell.file) -> (object, object):
    class Struct:
        def __init__(self, **entries):
            self.__dict__.update(entries)

    mapconversion = None
    crs = None

    if ifc_file.schema [:4] == 'IFC4':
        project = ifc_file.by_type("IfcProject")[0]
        for c in (m for c in project.RepresentationContexts for m in c.HasCoordinateOperation):
            return c, c.TargetCRS
    if ifc_file.schema == 'IFC2X3':
        values = _read_ifc2x3_georeferencing(ifc_file)
        if values is not None:
            return Struct(**values[0]), Struct(**values[1])
        
    return mapconversion, crs

def get_rotation(mapconversion) -> float:
    """
    This method calculates the rotation (in degrees) for a given mapconversion data structure,
    from its XAxisAbscissa and XAxisOrdinate properties.

        :returns the rotation in degrees along the Z-axis (the axis orthogonal to the earth's surface). For a right-handed
        coordinate reference system (as required) a postitive rotation angle implies a counter-clockwise rotation.
    """
    return math.degrees(math.atan2(mapconversion.XAxisOrdinate, mapconversion.XAxisAbscissa))