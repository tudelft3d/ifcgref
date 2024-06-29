import os
import ifcopenshell
import ifcopenshell.util.unit
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.pset
from ifcopenshell.util.element import get_psets
import math
from collections import OrderedDict



def set_mapconversion_crs(ifc_file: ifcopenshell.file,
                          target_crs_epsg_code: str,
                          eastings: float,
                          northings: float,
                          orthogonal_height: float,
                          x_axis_abscissa: float,
                          x_axis_ordinate: float,
                          scale: float) -> None:
    """
    This method adds IFC map conversion information to an IfcOpenShell file.
    IFC map conversion information indicates how the local coordinate reference system of the IFC file
    can be converted into a global coordinate reference system. The latter is the IfcProjectedCRS.

    The method detects whether the schema of the IFC file is IFC2X3 or IFC4.
    In case of IFC4, an IfcMapConversion and IfcProjectedCRS are created and associated with IfcProject.
    For IFC2X3, corresponding property sets are created and associated with IfcSite.

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
    :param scale: float, optional
        indicates the conversion factor to be used, to convert the units of the local coordinate
        system into the units of the target CRS (often expressed in metres).
        If omitted, a value of 1.0 is assumed.
    """
    if ifc_file.schema[:4] == 'IFC4':
        set_mapconversion_crs_ifc4(ifc_file, target_crs_epsg_code, eastings, northings, orthogonal_height,
                                   x_axis_abscissa,
                                   x_axis_ordinate, scale)
    if ifc_file.schema == 'IFC2X3':
        set_mapconversion_crs_ifc2x3(ifc_file, target_crs_epsg_code, eastings, northings, orthogonal_height,
                                     x_axis_abscissa, x_axis_ordinate, scale)


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
                                 scale: float) -> None:
    # Open the IFC property set template provided by OSarch.org on https://wiki.osarch.org/index.php?title=File:IFC2X3_Geolocation.ifc
    ifc_template = ifcopenshell.open(os.path.join(os.path.dirname(__file__), './IFC2X3_Geolocation.ifc'))
    map_conversion_template = \
        [t for t in ifc_template.by_type('IfcPropertySetTemplate') if t.Name == 'EPset_MapConversion'][0]
    crs_template = [t for t in ifc_template.by_type('IfcPropertySetTemplate') if t.Name == 'EPset_ProjectedCRS'][0]

    site = ifc_file.by_type("IfcSite")[0]  # we assume that the IfcProject only has one IfcSite entity.
    pset0 = ifcopenshell.api.run("pset.add_pset", ifc_file, product=site, name="ePset_MapConversion")
    ifcopenshell.api.run("pset.edit_pset", ifc_file, pset=pset0, properties={'TargetCRS':target_crs_epsg_code,
                                                                            'Eastings': eastings,
                                                                            'Northings': northings,
                                                                            'OrthogonalHeight': orthogonal_height,
                                                                            'XAxisAbscissa': x_axis_abscissa,
                                                                            'XAxisOrdinate': x_axis_ordinate,
                                                                            'Scale': scale},
                         pset_template=map_conversion_template)
    pset1 = ifcopenshell.api.run("pset.add_pset", ifc_file, product=site, name="ePset_ProjectedCRS")
    ifcopenshell.api.run("pset.edit_pset", ifc_file, pset=pset1, properties={'Name': target_crs_epsg_code},
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
        site = ifc_file.by_type("IfcSite")[0]
        psets = get_psets(site)
        if 'ePset_MapConversion' in psets.keys() and 'ePset_ProjectedCRS' in psets.keys():
            # Move the last property to the first place
            mapconversion_properties = OrderedDict(list(psets['ePset_MapConversion'].items())[-1:] + list(psets['ePset_MapConversion'].items())[:-1])
            crs_properties = OrderedDict(list(psets['ePset_ProjectedCRS'].items())[-1:] + list(psets['ePset_ProjectedCRS'].items())[:-1])
            return Struct(**mapconversion_properties), Struct(**crs_properties)
        
        if 'ePSet_MapConversion' in psets.keys() and 'ePSet_ProjectedCRS' in psets.keys():
            # Move the last property to the first place
            mapconversion_properties = OrderedDict(list(psets['ePSet_MapConversion'].items())[-1:] + list(psets['ePSet_MapConversion'].items())[:-1])
            crs_properties = OrderedDict(list(psets['ePSet_ProjectedCRS'].items())[-1:] + list(psets['ePSet_ProjectedCRS'].items())[:-1])
            return Struct(**mapconversion_properties), Struct(**crs_properties)
        
    return mapconversion, crs

def get_rotation(mapconversion) -> float:
    """
    This method calculates the rotation (in degrees) for a given mapconversion data structure,
    from its XAxisAbscissa and XAxisOrdinate properties.

        :returns the rotation in degrees along the Z-axis (the axis orthogonal to the earth's surface). For a right-handed
        coordinate reference system (as required) a postitive rotation angle implies a counter-clockwise rotation.
    """
    return math.degrees(math.atan2(mapconversion.XAxisOrdinate, mapconversion.XAxisAbscissa))