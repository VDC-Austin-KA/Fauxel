using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using Autodesk.Navisworks.Api;

namespace Fauxel.Navisworks
{
    sealed class NavisElement
    {
        public ModelItem Item;
        public string ItemGuid, RevitUniqueId, IfcGlobalId;
    }

    static class ModelTree
    {
        static readonly Regex RevitUid = new Regex("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}-[0-9a-fA-F]{8}$", RegexOptions.Compiled);
        static readonly Regex IfcGuid = new Regex("^[0-9A-Za-z_$]{22}$", RegexOptions.Compiled);

        /// <summary>
        /// Walks the selection tree and yields element nodes: a node that owns geometry, or an object whose children own geometry.
        /// Hidden-by-nothing, no filtering by search sets: the whole federated model is exported.
        /// </summary>
        public static IEnumerable<ModelItem> Elements(Document doc)
        {
            foreach (var root in doc.Models.RootItems)
                foreach (var e in Walk(root)) yield return e;
        }

        static IEnumerable<ModelItem> Walk(ModelItem n)
        {
            if (n.HasGeometry) { yield return n; yield break; }
            var kids = n.Children.ToList();
            if (kids.Any(c => c.HasGeometry)) { yield return n; yield break; }
            foreach (var k in kids)
                foreach (var e in Walk(k)) yield return e;
        }

        public static NavisElement Describe(ModelItem item)
        {
            var e = new NavisElement { Item = item };
            if (item.InstanceGuid != Guid.Empty) e.ItemGuid = item.InstanceGuid.ToString();
            // Ids live on the object node; geometry leaves usually inherit them from the parent.
            foreach (var node in new[] { item, item.Parent })
            {
                if (node == null) continue;
                foreach (var (cat, name, value) in Props(node))
                {
                    if (e.RevitUniqueId == null && RevitUid.IsMatch(value)) e.RevitUniqueId = value;
                    if (e.IfcGlobalId == null && IfcGuid.IsMatch(value) && (cat.StartsWith("IFC", StringComparison.OrdinalIgnoreCase) || IsIfcName(name)))
                        e.IfcGlobalId = value;
                }
            }
            return e;
        }

        static bool IsIfcName(string n) =>
            n.Equals("GlobalId", StringComparison.OrdinalIgnoreCase) || n.Equals("IfcGUID", StringComparison.OrdinalIgnoreCase) || n.Equals("IFC GUID", StringComparison.OrdinalIgnoreCase);

        /// <summary>All (category, property, display value) triples of an item; unreadable values are skipped.</summary>
        public static IEnumerable<(string cat, string name, string value)> Props(ModelItem item)
        {
            foreach (PropertyCategory cat in item.PropertyCategories)
                foreach (DataProperty p in cat.Properties)
                {
                    string v = null;
                    try { v = p.Value.IsDisplayString || p.Value.IsIdentifierString ? p.Value.ToDisplayString() : p.Value.ToString(); } catch (Exception) { }
                    if (!string.IsNullOrWhiteSpace(v)) yield return (cat.DisplayName, p.DisplayName, v);
                }
        }

        /// <summary>First property whose name matches (any category when category is null).</summary>
        public static string Prop(ModelItem item, string category, params string[] names)
        {
            foreach (var (cat, name, value) in Props(item))
            {
                if (category != null && !cat.Equals(category, StringComparison.OrdinalIgnoreCase)) continue;
                if (names.Any(n => name.Equals(n, StringComparison.OrdinalIgnoreCase))) return value;
            }
            return null;
        }
    }
}
