using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Navisworks.Api;
using Fauxel.Connectors;

namespace Fauxel.Navisworks
{
    static class ExportTree
    {
        public static BimExport Build(Document doc)
        {
            double k = UnitConversion.ScaleFactor(doc.Units, Units.Meters);
            var export = new BimExport
            {
                Source = "navisworks",
                ModelName = string.IsNullOrEmpty(doc.Title) ? "Navisworks model" : doc.Title,
                HostVersion = HostYear(),
                SharedTransform = Matrix4.Identity.ToArray(),   // Navisworks coordinates are already the shared frame, only scaled to meters below
            };
            var levelMinZ = new Dictionary<string, double>();
            var seen = new HashSet<string>();

            foreach (var item in ModelTree.Elements(doc))
            {
                var bb = item.BoundingBox();
                if (bb == null || bb.IsEmpty) continue;
                var d = ModelTree.Describe(item);
                var key = d.ItemGuid ?? d.RevitUniqueId ?? d.IfcGlobalId;
                if (key != null && !seen.Add(key)) continue;

                var category = ModelTree.Prop(item, "Element", "Category") ?? ModelTree.Prop(item, null, "Category") ?? item.ClassDisplayName ?? "";
                var level = ModelTree.Prop(item, "Element", "Level") ?? ModelTree.Prop(item, null, "Level") ?? LayerName(item);
                var min = new[] { Round(bb.Min.X * k), Round(bb.Min.Y * k), Round(bb.Min.Z * k) };
                var max = new[] { Round(bb.Max.X * k), Round(bb.Max.Y * k), Round(bb.Max.Z * k) };
                if (!string.IsNullOrEmpty(level) && (!levelMinZ.TryGetValue(level, out var z) || min[2] < z)) levelMinZ[level] = min[2];

                export.Elements.Add(new BimElement
                {
                    NavisItemGuid = d.ItemGuid,
                    RevitUniqueId = d.RevitUniqueId,
                    IfcGlobalId = d.IfcGlobalId,
                    Name = item.DisplayName ?? "",
                    Category = category,
                    FamilyType = ModelTree.Prop(item, null, "Type Name", "Type", "Family and Type") ?? item.Parent?.DisplayName ?? "",
                    Level = level ?? "",
                    RoomOrZone = ModelTree.Prop(item, null, "Room Name", "Room"),
                    Trade = TradeClassifier.FromCategory(category),
                    AabbMin = min, AabbMax = max,
                    Quantity = 1, QuantityUnit = "ea",
                });
            }
            // Navisworks has no level objects: infer each level's elevation from the lowest element in it.
            export.Levels = levelMinZ.OrderBy(p => p.Value).Select(p => new BimLevel { Name = p.Key, ElevationM = p.Value }).ToList();
            return export;
        }

        static double Round(double v) => Math.Round(v, 4);

        /// <summary>Nearest ancestor flagged as a layer (Revit levels / DWG layers land here).</summary>
        static string LayerName(ModelItem item) => item.Ancestors.FirstOrDefault(a => a.IsLayer)?.DisplayName;

        static string HostYear()
        {
#if NAVIS2025
            return "2025";
#elif NAVIS2026
            return "2026";
#else
            return "2027";
#endif
        }
    }
}
