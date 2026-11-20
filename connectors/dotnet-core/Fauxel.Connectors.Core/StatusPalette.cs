using System;

namespace Fauxel.Connectors
{
    public readonly struct Rgb
    {
        public byte R { get; }
        public byte G { get; }
        public byte B { get; }
        public Rgb(byte r, byte g, byte b) { R = r; G = g; B = b; }
        public string Hex => $"#{R:x2}{G:x2}{B:x2}";
        public static Rgb FromHex(string hex)
        {
            var h = (hex ?? "").TrimStart('#');
            if (h.Length != 6) throw new FormatException($"Bad color '{hex}'");
            return new Rgb(Convert.ToByte(h.Substring(0, 2), 16), Convert.ToByte(h.Substring(2, 2), 16), Convert.ToByte(h.Substring(4, 2), 16));
        }
    }

    /// <summary>Status colors from platform-contract.md.</summary>
    public static class StatusPalette
    {
        public static readonly Rgb Installed = Rgb.FromHex("#22c55e");
        public static readonly Rgb InProgress = Rgb.FromHex("#eab308");
        public static readonly Rgb Deviation = Rgb.FromHex("#ef4444");
        public static readonly Rgb NotStarted = Rgb.FromHex("#9ca3af");

        public static Rgb For(string status, double percent)
        {
            switch ((status ?? "").ToUpperInvariant())
            {
                case "DEVIATION_FLAGGED": return Deviation;
                case "INSTALLED":
                case "INSULATED_OR_FINISHED": return Installed;
                case "FRAMED_OR_ROUGHED": return InProgress;
            }
            if (percent >= 100) return Installed;
            if (percent >= 1) return InProgress;
            return NotStarted;
        }

        /// <summary>Prefer the server-supplied color when valid, else derive it.</summary>
        public static Rgb For(WritebackElement e)
        {
            try { if (!string.IsNullOrEmpty(e.Color)) return Rgb.FromHex(e.Color); } catch (FormatException) { }
            return For(e.Status, e.PercentComplete);
        }
    }
}
