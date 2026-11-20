using System;

namespace Fauxel.Connectors
{
    /// <summary>Maps a Revit/Navisworks category name to a contract trade.</summary>
    public static class TradeClassifier
    {
        public static string FromCategory(string category)
        {
            var c = (category ?? "").ToLowerInvariant();
            if (c.Length == 0) return "other";
            if (Has(c, "structural", "foundation", "rebar", "truss", "beam", "girder", "slab")) return "structural";
            if (Has(c, "duct", "air terminal", "mechanical", "hvac", "vav", "diffuser")) return "hvac";
            if (Has(c, "pipe", "plumbing", "sprinkler", "fire protection", "fire suppression")) return "plumbing_fp";
            if (Has(c, "electrical", "conduit", "cable tray", "lighting", "fire alarm", "data device", "communication", "telephone", "security device", "wire"))
                return "electrical";
            if (Has(c, "wall", "floor", "ceiling", "roof", "door", "window", "stair", "railing", "casework", "furniture", "partition", "curtain", "column", "room"))
                return "arch_interior";
            return "other";
        }

        static bool Has(string s, params string[] keys)
        {
            foreach (var k in keys) if (s.IndexOf(k, StringComparison.Ordinal) >= 0) return true;
            return false;
        }
    }
}
