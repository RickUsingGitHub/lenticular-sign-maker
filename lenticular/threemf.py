"""3MF export: the sign as one object made of a part per filament.

A 3MF file is a zip archive holding an XML model.  The sign is written as one
object built from components - one mesh for each filament - which slicers
open as a single object with several parts.

For the Bambu Studio / OrcaSlicer family (Flash Studio, Orca-Flashforge, ...)
the file also carries ``Metadata/model_settings.config``, the list those
slicers read part names and filament numbers from, so every part arrives
already set to its filament.  It holds no printer or print settings, so
nothing in the slicer is changed by opening the file.
"""

from __future__ import annotations

import zipfile

import numpy as np

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="config" ContentType="text/xml"/>\n'
    '</Types>\n')

_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    '</Relationships>\n')

_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(1.0, c)) * 255)):02X}" for c in rgb)


def _object_xml(obj_id: int, name: str, verts: np.ndarray, faces: np.ndarray,
                material: str = "") -> str:
    v = verts.astype(np.float64)
    # numpy string formatting is much faster than a Python loop for big meshes
    vs = np.char.add(np.char.add('<vertex x="', np.char.mod("%.5f", v[:, 0])), '" y="')
    vs = np.char.add(np.char.add(vs, np.char.mod("%.5f", v[:, 1])), '" z="')
    vs = np.char.add(np.char.add(vs, np.char.mod("%.5f", v[:, 2])), '"/>')
    ts = np.char.add(np.char.add('<triangle v1="', np.char.mod("%d", faces[:, 0])), '" v2="')
    ts = np.char.add(np.char.add(ts, np.char.mod("%d", faces[:, 1])), '" v3="')
    ts = np.char.add(np.char.add(ts, np.char.mod("%d", faces[:, 2])), '"/>')
    return (f' <object id="{obj_id}" type="model" name="{_xml_escape(name)}"{material}>\n'
            f'  <mesh>\n   <vertices>\n' + "\n".join(vs.tolist()) + '\n   </vertices>\n'
            f'   <triangles>\n' + "\n".join(ts.tolist()) + '\n   </triangles>\n  </mesh>\n'
            f' </object>\n')


def write_3mf(path: str, name: str, parts, position=(0.0, 0.0, 0.0)):
    """Write one object made of several single-filament parts.

    parts: list of (part name, verts (V,3), faces (F,3), filament number from 1,
    display colour (r, g, b) in 0..1).  Vertices are stored as given; the
    position goes into the build transform, so the object sits at its place on
    the plate when the file is opened.
    """
    resources = [' <basematerials id="1">\n'
                 + "".join(f'  <base name="{_xml_escape(nm)}" displaycolor="{_hex(rgb)}"/>\n'
                           for nm, _, _, _, rgb in parts)
                 + ' </basematerials>\n']
    ids = []
    for k, (nm, verts, faces, _, _) in enumerate(parts):
        ids.append(k + 2)
        resources.append(_object_xml(k + 2, nm, np.asarray(verts), np.asarray(faces),
                                     f' pid="1" pindex="{k}"'))
    item = len(parts) + 2
    comps = "".join(f'   <component objectid="{i}"/>\n' for i in ids)
    resources.append(f' <object id="{item}" type="model" name="{_xml_escape(name)}">\n'
                     f'  <components>\n{comps}  </components>\n </object>\n')
    x, y, z = (float(c) for c in position)
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        f' <metadata name="Title">{_xml_escape(name)}</metadata>\n'
        ' <metadata name="Application">Lenticular Sign Maker</metadata>\n'
        ' <resources>\n' + "".join(resources) + ' </resources>\n'
        ' <build>\n'
        f'  <item objectid="{item}" transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} {z:.4f}"/>\n'
        ' </build>\n'
        '</model>\n')
    # the part list Orca-family slicers read names and filament numbers from
    settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
        f' <object id="{item}">\n'
        f'  <metadata key="name" value="{_xml_escape(name)}"/>\n'
        f'  <metadata key="extruder" value="{int(parts[0][3])}"/>\n'
        + "".join(f'  <part id="{i}" subtype="normal_part">\n'
                  f'   <metadata key="name" value="{_xml_escape(nm)}"/>\n'
                  f'   <metadata key="extruder" value="{int(fil)}"/>\n'
                  f'  </part>\n'
                  for i, (nm, _, _, fil, _) in zip(ids, parts))
        + ' </object>\n</config>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", model)
        zf.writestr("Metadata/model_settings.config", settings)


def read_3mf(path: str):
    """Read back a 3MF written by :func:`write_3mf` (used by the tests).

    Returns a list of (part name, verts, faces, filament number, (x, y, z)).
    """
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("3D/3dmodel.model"))
        config = ET.fromstring(zf.read("Metadata/model_settings.config"))
    assert root.get("unit") == "millimeter"
    fils = {part.get("id"): int(part.find("metadata[@key='extruder']").get("value"))
            for part in config.iter("part")}
    meshes, groups = {}, {}
    for obj in root.find("m:resources", _NS).findall("m:object", _NS):
        mesh = obj.find("m:mesh", _NS)
        if mesh is None:
            groups[obj.get("id")] = [c.get("objectid") for c in
                                     obj.find("m:components", _NS).findall("m:component", _NS)]
            continue
        v = np.array([[float(e.get(a)) for a in "xyz"]
                      for e in mesh.find("m:vertices", _NS).findall("m:vertex", _NS)])
        f = np.array([[int(e.get(a)) for a in ("v1", "v2", "v3")]
                      for e in mesh.find("m:triangles", _NS).findall("m:triangle", _NS)])
        meshes[obj.get("id")] = (obj.get("name"), v, f)
    out = []
    for item in root.find("m:build", _NS).findall("m:item", _NS):
        t = [float(x) for x in item.get("transform").split()]
        for oid in groups[item.get("objectid")]:
            name, v, f = meshes[oid]
            out.append((name, v, f, fils[oid], tuple(t[9:12])))
    return out
