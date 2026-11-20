using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.IFC;
using Fauxel.Connectors;

namespace Fauxel.Revit
{
    static class ModelExporter
    {
        /// <summary>Internal-to-shared transform with the translation in meters (rotation unchanged).</summary>
        public static Matrix4 SharedTransformMeters(Document doc)
        {
            var t = doc.ActiveProjectLocation.GetTotalTransform();
            return new Matrix4(new[]
            {
                t.BasisX.X, t.BasisY.X, t.BasisZ.X, t.Origin.X,
                t.BasisX.Y, t.BasisY.Y, t.BasisZ.Y, t.Origin.Y,
                t.BasisX.Z, t.BasisY.Z, t.BasisZ.Z, t.Origin.Z,
                0, 0, 0, 1,
            }).WithTranslationScaled(Matrix4.FeetToMeters);
        }

        public static BimExport Build(Document doc, string hostVersion)
        {
            var toShared = doc.ActiveProjectLocation.GetTotalTransform();
            var export = new BimExport
            {
                Source = "revit",
                ModelName = string.IsNullOrEmpty(doc.Title) ? "Untitled" : doc.Title,
                HostVersion = hostVersion,
                SharedTransform = SharedTransformMeters(doc).ToArray(),
            };

            foreach (var lv in new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>().OrderBy(l => l.Elevation))
                export.Levels.Add(new BimLevel { Name = lv.Name, ElevationM = Meters(toShared.OfPoint(new XYZ(0, 0, lv.Elevation)).Z) });

            foreach (var el in FauxelCategories.Collect(doc))
            {
                var bb = el.get_BoundingBox(null);
                if (bb == null || el.Category == null) continue;     // no model geometry (e.g. hidden helper / empty family)
                var (min, max) = SharedBox(bb, toShared);
                var (qty, unit) = Quantity(el);
                var catName = el.Category.Name;
                export.Elements.Add(new BimElement
                {
                    RevitUniqueId = el.UniqueId,
                    IfcGlobalId = IfcGuid(el),
                    Name = SafeName(el),
                    Category = catName,
                    FamilyType = FamilyType(doc, el),
                    Level = LevelName(doc, el),
                    RoomOrZone = RoomName(el),
                    Trade = TradeClassifier.FromCategory(catName),
                    AabbMin = min, AabbMax = max,
                    Quantity = Math.Round(qty, 4), QuantityUnit = unit,
                });
            }
            return export;
        }

        static double Meters(double feet) => feet * Matrix4.FeetToMeters;

        /// <summary>Axis-aligned box (meters, shared coords) around the 8 transformed corners.</summary>
        static (double[] min, double[] max) SharedBox(BoundingBoxXYZ bb, Transform toShared)
        {
            double[] min = { double.MaxValue, double.MaxValue, double.MaxValue }, max = { double.MinValue, double.MinValue, double.MinValue };
            foreach (var x in new[] { bb.Min.X, bb.Max.X })
                foreach (var y in new[] { bb.Min.Y, bb.Max.Y })
                    foreach (var z in new[] { bb.Min.Z, bb.Max.Z })
                    {
                        var p = toShared.OfPoint(bb.Transform.OfPoint(new XYZ(x, y, z)));
                        var v = new[] { Meters(p.X), Meters(p.Y), Meters(p.Z) };
                        for (int i = 0; i < 3; i++) { min[i] = Math.Min(min[i], v[i]); max[i] = Math.Max(max[i], v[i]); }
                    }
            return (min.Select(v => Math.Round(v, 4)).ToArray(), max.Select(v => Math.Round(v, 4)).ToArray());
        }

        static string IfcGuid(Element el)
        {
            var stored = el.get_Parameter(BuiltInParameter.IFC_GUID)?.AsString();
            if (!string.IsNullOrEmpty(stored)) return stored;
            try { return ExporterIFCUtils.CreateAlternateGUID(el); }   // same GUID the IFC exporter would assign
            catch (Exception) { return null; }
        }

        static string SafeName(Element el)
        {
            try { return el.Name; } catch (Exception) { return el.Category?.Name; }
        }

        static string FamilyType(Document doc, Element el)
        {
            var type = doc.GetElement(el.GetTypeId()) as ElementType;
            if (type == null) return "";
            return string.IsNullOrEmpty(type.FamilyName) ? type.Name : type.FamilyName + ": " + type.Name;
        }

        static readonly BuiltInParameter[] LevelParams =
        {
            BuiltInParameter.FAMILY_LEVEL_PARAM, BuiltInParameter.SCHEDULE_LEVEL_PARAM, BuiltInParameter.WALL_BASE_CONSTRAINT,
            BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM, BuiltInParameter.RBS_START_LEVEL_PARAM,
        };

        static string LevelName(Document doc, Element el)
        {
            if (el.LevelId != ElementId.InvalidElementId) return (doc.GetElement(el.LevelId) as Level)?.Name ?? "";
            foreach (var bip in LevelParams)
            {
                var p = el.get_Parameter(bip);
                if (p != null && p.StorageType == StorageType.ElementId && p.AsElementId() != ElementId.InvalidElementId)
                    return (doc.GetElement(p.AsElementId()) as Level)?.Name ?? "";
            }
            return "";
        }

        static string RoomName(Element el)
        {
            var fi = el as FamilyInstance;
            if (fi == null) return null;
            try { return fi.Room?.Name ?? fi.Space?.Name; } catch (Exception) { return null; }
        }

        /// <summary>Walls/floors/ceilings/roofs by area, linear MEP and framing by length, structural by volume, rest by count.</summary>
        static (double, string) Quantity(Element el)
        {
            var cat = el.Category.BuiltInCategory;
            if (cat == BuiltInCategory.OST_Walls || cat == BuiltInCategory.OST_Floors || cat == BuiltInCategory.OST_Ceilings || cat == BuiltInCategory.OST_Roofs)
            {
                var a = el.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED);
                if (a != null && a.AsDouble() > 0) return (UnitUtils.ConvertFromInternalUnits(a.AsDouble(), UnitTypeId.SquareMeters), "m2");
            }
            if (el.Location is LocationCurve lc)
                return (UnitUtils.ConvertFromInternalUnits(lc.Curve.Length, UnitTypeId.Meters), "m");
            if (cat == BuiltInCategory.OST_StructuralColumns || cat == BuiltInCategory.OST_StructuralFoundation)
            {
                var v = el.get_Parameter(BuiltInParameter.HOST_VOLUME_COMPUTED);
                if (v != null && v.AsDouble() > 0) return (UnitUtils.ConvertFromInternalUnits(v.AsDouble(), UnitTypeId.CubicMeters), "m3");
            }
            return (1, "ea");
        }
    }
}
