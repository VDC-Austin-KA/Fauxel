using System;
using System.IO;

namespace Fauxel.Connectors
{
    /// <summary>Plugin settings, stored at %APPDATA%\Fauxel\settings.json (API key is plain text).</summary>
    public sealed class FauxelSettings
    {
        public string ApiUrl { get; set; } = "http://localhost:8000";
        public string ApiKey { get; set; } = "";
        public string ProjectId { get; set; } = "";
        public int TimeoutSeconds { get; set; } = 120;

        public static string DefaultPath =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Fauxel", "settings.json");

        public static FauxelSettings Load(string path = null)
        {
            path = path ?? DefaultPath;
            if (!File.Exists(path)) return new FauxelSettings();
            try
            {
                return FauxelJson.Deserialize<FauxelSettings>(File.ReadAllText(path)) ?? new FauxelSettings();
            }
            catch (System.Text.Json.JsonException ex)
            {
                throw new InvalidDataException($"Fauxel settings file '{path}' is not valid JSON ({ex.Message}). Fix or delete it.", ex);
            }
        }

        public void Save(string path = null)
        {
            path = path ?? DefaultPath;
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, FauxelJson.Serialize(this));
        }

        /// <summary>Returns null when usable, otherwise a message describing what is missing.</summary>
        public string Validate()
        {
            if (string.IsNullOrWhiteSpace(ApiUrl) || !Uri.TryCreate(ApiUrl, UriKind.Absolute, out var u) ||
                (u.Scheme != Uri.UriSchemeHttp && u.Scheme != Uri.UriSchemeHttps))
                return "Fauxel API URL is missing or not an http(s) URL. Open Fauxel > Settings.";
            if (string.IsNullOrWhiteSpace(ProjectId)) return "Fauxel project id is missing. Open Fauxel > Settings.";
            return null;
        }
    }
}
