using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Navisworks.Api;
using Fauxel.Connectors;

namespace Fauxel.Navisworks
{
    static class StatusColors
    {
        /// <summary>Overrides permanent color (and transparency for not-started) on matched items. Returns (applied, unmatched).</summary>
        public static (int applied, int unmatched) Apply(Document doc, WritebackPayload payload)
        {
            var byNavis = new Dictionary<string, List<ModelItem>>(StringComparer.OrdinalIgnoreCase);
            var byRevit = new Dictionary<string, List<ModelItem>>(StringComparer.OrdinalIgnoreCase);
            var byIfc = new Dictionary<string, List<ModelItem>>(StringComparer.Ordinal);
            void Index(Dictionary<string, List<ModelItem>> map, string key, ModelItem it)
            {
                if (string.IsNullOrEmpty(key)) return;
                if (!map.TryGetValue(key, out var l)) map[key] = l = new List<ModelItem>();
                l.Add(it);
            }
            foreach (var item in ModelTree.Elements(doc))
            {
                var d = ModelTree.Describe(item);
                Index(byNavis, d.ItemGuid, item); Index(byRevit, d.RevitUniqueId, item); Index(byIfc, d.IfcGlobalId, item);
            }

            var groups = new Dictionary<string, (Rgb rgb, bool faded, List<ModelItem> items)>();
            int applied = 0, unmatched = 0;
            foreach (var wb in payload.Elements)
            {
                List<ModelItem> hit = null;
                if (wb.NavisItemGuid != null) byNavis.TryGetValue(wb.NavisItemGuid, out hit);
                if (hit == null && wb.RevitUniqueId != null) byRevit.TryGetValue(wb.RevitUniqueId, out hit);
                if (hit == null && wb.IfcGlobalId != null) byIfc.TryGetValue(wb.IfcGlobalId, out hit);
                if (hit == null) { unmatched++; continue; }

                var rgb = StatusPalette.For(wb);
                bool faded = rgb.Hex == StatusPalette.NotStarted.Hex;
                var key = rgb.Hex + (faded ? "/t" : "");
                if (!groups.TryGetValue(key, out var g)) groups[key] = g = (rgb, faded, new List<ModelItem>());
                g.items.AddRange(hit);
                applied++;
            }

            foreach (var g in groups.Values)
            {
                var col = Autodesk.Navisworks.Api.Color.FromByteRGB(g.rgb.R, g.rgb.G, g.rgb.B);
                doc.Models.OverridePermanentColor(g.items, col);
                doc.Models.OverridePermanentTransparency(g.items, g.faded ? 0.5 : 0.0);
            }
            return (applied, unmatched);
        }

        public static void Reset(Document doc) => doc.Models.ResetAllPermanentMaterials();
    }
}
