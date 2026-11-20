using System.Text.Json;

namespace Fauxel.Connectors
{
    public static class FauxelJson
    {
        public static readonly JsonSerializerOptions Options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            PropertyNameCaseInsensitive = true,
            WriteIndented = false,
        };

        public static string Serialize<T>(T value) => JsonSerializer.Serialize(value, Options);

        public static T Deserialize<T>(string json) => JsonSerializer.Deserialize<T>(json, Options);
    }
}
