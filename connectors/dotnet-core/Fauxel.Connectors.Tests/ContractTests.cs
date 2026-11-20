using System.Net;
using System.Text.Json;
using Fauxel.Connectors;
using Xunit;

namespace Fauxel.Connectors.Tests;

public class DtoTests
{
    // Shapes copied from docs/specs/platform-contract.md
    const string WritebackJson = """
    {"project_id":"7b0c6d86-0000-4000-8000-000000000001","generated_at":"2026-11-20T12:00:00Z","rules_version":1,
     "elements":[{"element_uid":"11111111-1111-4111-8111-111111111111","revit_unique_id":"abc-0001","ifc_global_id":null,"navis_item_guid":null,
       "percent_complete":65,"status":"FRAMED_OR_ROUGHED","last_verified":"2026-11-19T08:00:00Z","confidence":0.91,"evidence_url":"https://x/e.jpg","color":"#eab308"}]}
    """;

    const string ExportJson = """
    {"source":"revit","model_name":"Test","host_version":"2027","units":"m",
     "shared_transform":[1,0,0,10,0,1,0,20,0,0,1,30,0,0,0,1],
     "levels":[{"name":"L1","elevation_m":0.0},{"name":"L2","elevation_m":4.2}],
     "elements":[{"revit_unique_id":"u1","ifc_global_id":"g1","navis_item_guid":null,"name":"Wall","category":"Walls","family_type":"Basic Wall: 200","level":"L1","room_or_zone":null,"trade":"arch_interior","aabb_min":[0,0,0],"aabb_max":[1,2,3],"quantity":6.5,"quantity_unit":"m2"}]}
    """;

    [Fact]
    public void Writeback_parses_contract_shape()
    {
        var p = FauxelJson.Deserialize<WritebackPayload>(WritebackJson);
        Assert.Equal(1, p.RulesVersion);
        var e = Assert.Single(p.Elements);
        Assert.Equal("abc-0001", e.RevitUniqueId);
        Assert.Null(e.IfcGlobalId);
        Assert.Equal(65, e.PercentComplete);
        Assert.Equal("FRAMED_OR_ROUGHED", e.Status);
        Assert.Equal(0.91, e.Confidence, 6);
        Assert.Equal("#eab308", e.Color);
    }

    [Fact]
    public void Writeback_roundtrip_keeps_snake_case_keys()
    {
        var p = FauxelJson.Deserialize<WritebackPayload>(WritebackJson);
        using var doc = JsonDocument.Parse(FauxelJson.Serialize(p));
        var el = doc.RootElement.GetProperty("elements")[0];
        foreach (var k in new[] { "element_uid", "revit_unique_id", "ifc_global_id", "navis_item_guid", "percent_complete", "status", "last_verified", "confidence", "evidence_url", "color" })
            Assert.True(el.TryGetProperty(k, out _), k);
        Assert.Equal(JsonValueKind.Null, el.GetProperty("ifc_global_id").ValueKind);
    }

    [Fact]
    public void BimExport_roundtrip_matches_contract_keys()
    {
        var x = FauxelJson.Deserialize<BimExport>(ExportJson);
        Assert.Equal("revit", x.Source);
        Assert.Equal(16, x.SharedTransform.Length);
        Assert.Equal(2, x.Levels.Count);
        Assert.Equal(4.2, x.Levels[1].ElevationM);
        var e = x.Elements[0];
        Assert.Equal(new[] { 1.0, 2.0, 3.0 }, e.AabbMax);
        Assert.Equal("m2", e.QuantityUnit);

        using var a = JsonDocument.Parse(FauxelJson.Serialize(x));
        using var b = JsonDocument.Parse(ExportJson);
        Assert.Equal(Keys(b.RootElement), Keys(a.RootElement));
        Assert.Equal(Keys(b.RootElement.GetProperty("elements")[0]), Keys(a.RootElement.GetProperty("elements")[0]));
        Assert.Equal(Keys(b.RootElement.GetProperty("levels")[0]), Keys(a.RootElement.GetProperty("levels")[0]));
    }

    [Fact]
    public void Timeliner_and_ack_shapes()
    {
        var t = FauxelJson.Deserialize<List<TimelinerTaskDto>>(
            """[{"external_id":"A100","name":"Frame L1","actual_start":"2026-11-01","actual_finish":null,"percent_complete":40.5}]""");
        Assert.Equal("A100", t[0].ExternalId);
        Assert.Null(t[0].ActualFinish);
        Assert.Equal("{\"host\":\"revit\",\"applied\":3,\"model\":\"M\"}",
            FauxelJson.Serialize(new WritebackAck { Host = "revit", Applied = 3, Model = "M" }));
    }

    static string[] Keys(JsonElement e) => e.EnumerateObject().Select(p => p.Name).OrderBy(n => n).ToArray();
}

public class PaletteTests
{
    [Theory]
    [InlineData("INSTALLED", 80, "#22c55e")]
    [InlineData("INSULATED_OR_FINISHED", 100, "#22c55e")]
    [InlineData("FRAMED_OR_ROUGHED", 25, "#eab308")]
    [InlineData("NOT_STARTED", 40, "#eab308")]
    [InlineData("NOT_STARTED", 99, "#eab308")]
    [InlineData("NOT_STARTED", 0, "#9ca3af")]
    [InlineData("DEVIATION_FLAGGED", 50, "#ef4444")]
    [InlineData("NOT_STARTED", 100, "#22c55e")]
    public void Colors_follow_contract(string status, double pct, string hex) =>
        Assert.Equal(hex, StatusPalette.For(status, pct).Hex);

    [Fact]
    public void Server_color_wins_and_bad_color_falls_back()
    {
        Assert.Equal("#ef4444", StatusPalette.For(new WritebackElement { Status = "INSTALLED", Color = "#ef4444" }).Hex);
        Assert.Equal("#22c55e", StatusPalette.For(new WritebackElement { Status = "INSTALLED", Color = "zzz" }).Hex);
    }

    [Theory]
    [InlineData("Structural Columns", "structural")]
    [InlineData("Walls", "arch_interior")]
    [InlineData("Curtain Wall Panels", "arch_interior")]
    [InlineData("Ducts", "hvac")]
    [InlineData("Mechanical Equipment", "hvac")]
    [InlineData("Pipe Fittings", "plumbing_fp")]
    [InlineData("Sprinklers", "plumbing_fp")]
    [InlineData("Cable Trays", "electrical")]
    [InlineData("Electrical Equipment", "electrical")]
    [InlineData("Topography", "other")]
    [InlineData(null, "other")]
    public void Trade_by_category(string cat, string trade) => Assert.Equal(trade, TradeClassifier.FromCategory(cat));
}

public class SettingsTests
{
    [Fact]
    public void Save_load_roundtrip_and_defaults()
    {
        var dir = Path.Combine(Path.GetTempPath(), "fauxel-test-" + Guid.NewGuid());
        var path = Path.Combine(dir, "sub", "settings.json");
        try
        {
            Assert.Equal("", FauxelSettings.Load(path).ApiKey);
            new FauxelSettings { ApiUrl = "https://f.example", ApiKey = "k", ProjectId = "p1", TimeoutSeconds = 30 }.Save(path);
            var s = FauxelSettings.Load(path);
            Assert.Equal(("https://f.example", "k", "p1", 30), (s.ApiUrl, s.ApiKey, s.ProjectId, s.TimeoutSeconds));
            Assert.Null(s.Validate());
            File.WriteAllText(path, "{nope");
            Assert.Throws<InvalidDataException>(() => FauxelSettings.Load(path));
        }
        finally { if (Directory.Exists(dir)) Directory.Delete(dir, true); }
    }

    [Fact]
    public void Default_path_ends_with_Fauxel_settings_json() =>
        Assert.EndsWith(Path.Combine("Fauxel", "settings.json"), FauxelSettings.DefaultPath);

    [Theory]
    [InlineData("", "p", false)]
    [InlineData("ftp://x", "p", false)]
    [InlineData("http://localhost:8000", "", false)]
    [InlineData("http://localhost:8000", "p", true)]
    public void Validate(string url, string pid, bool ok) =>
        Assert.Equal(ok, new FauxelSettings { ApiUrl = url, ProjectId = pid }.Validate() == null);
}

public class ClientTests
{
    class Stub : HttpMessageHandler
    {
        public List<HttpRequestMessage> Requests = new();
        public List<string> Bodies = new();
        public Queue<Func<HttpResponseMessage>> Responses = new();
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken ct)
        {
            Requests.Add(r);
            Bodies.Add(r.Content?.ReadAsStringAsync().Result);
            return Task.FromResult(Responses.Dequeue()());
        }
    }

    static HttpResponseMessage Json(string body, HttpStatusCode c = HttpStatusCode.OK) =>
        new(c) { Content = new StringContent(body) };

    static FauxelClient Make(Stub s, string url = "http://h:8000") =>
        new(new FauxelSettings { ApiUrl = url, ApiKey = "secret", ProjectId = "proj 1" }, s) { RetryDelay = TimeSpan.FromMilliseconds(1) };

    [Fact]
    public async Task Writeback_sends_key_and_builds_url()
    {
        var s = new Stub();
        s.Responses.Enqueue(() => Json("""{"project_id":"p","generated_at":"x","rules_version":2,"elements":[]}"""));
        var p = await Make(s).GetWritebackAsync("2026-01-01T00:00:00Z");
        Assert.Equal(2, p.RulesVersion);
        var r = s.Requests[0];
        Assert.Equal("http://h:8000/api/v1/projects/proj%201/writeback?since=2026-01-01T00%3A00%3A00Z", r.RequestUri.AbsoluteUri);
        Assert.Equal("secret", r.Headers.GetValues("X-Fauxel-Key").Single());
    }

    [Fact]
    public async Task Base_url_with_api_v1_is_not_doubled()
    {
        var s = new Stub();
        s.Responses.Enqueue(() => Json("[]"));
        await Make(s, "https://f.example/api/v1/").GetTimelinerAsync();
        Assert.Equal("https://f.example/api/v1/projects/proj%201/timeliner", s.Requests[0].RequestUri.AbsoluteUri);
    }

    [Fact]
    public async Task Post_import_and_ack_send_json_bodies()
    {
        var s = new Stub();
        s.Responses.Enqueue(() => Json("""{"id":"m1"}"""));
        s.Responses.Enqueue(() => Json("{}"));
        var c = Make(s);
        var raw = await c.ImportModelAsync(new BimExport { Source = "revit", ModelName = "M" });
        await c.AckAsync(new WritebackAck { Host = "revit", Applied = 2, Model = "M" });
        Assert.Contains("m1", raw);
        Assert.EndsWith("/models/import", s.Requests[0].RequestUri.AbsolutePath);
        Assert.EndsWith("/writeback/ack", s.Requests[1].RequestUri.AbsolutePath);
        Assert.Equal(HttpMethod.Post, s.Requests[1].Method);
        Assert.Contains("\"source\":\"revit\"", s.Bodies[0]);
    }

    [Fact]
    public async Task Test_connection_checks_health_and_project()
    {
        var s = new Stub();
        s.Responses.Enqueue(() => Json("""{"status":"ok","db":"up","storage":"up","gpu":false}"""));
        s.Responses.Enqueue(() => Json("{}"));
        Assert.StartsWith("Connected", await Make(s).TestConnectionAsync());
        Assert.EndsWith("/health", s.Requests[0].RequestUri.AbsolutePath);
        Assert.EndsWith("/projects/proj%201", s.Requests[1].RequestUri.AbsoluteUri);
    }

    [Fact]
    public async Task Retries_5xx_then_succeeds()
    {
        var s = new Stub();
        s.Responses.Enqueue(() => Json("boom", HttpStatusCode.BadGateway));
        s.Responses.Enqueue(() => Json("""{"status":"ok"}"""));
        Assert.Equal("ok", (await Make(s).GetHealthAsync()).Status);
        Assert.Equal(2, s.Requests.Count);
    }

    [Fact]
    public async Task Gives_up_after_three_5xx()
    {
        var s = new Stub();
        for (int i = 0; i < 3; i++) s.Responses.Enqueue(() => Json("down", HttpStatusCode.ServiceUnavailable));
        var ex = await Assert.ThrowsAsync<FauxelException>(() => Make(s).GetHealthAsync());
        Assert.Contains("503", ex.Message);
        Assert.Equal(3, s.Requests.Count);
    }

    [Fact]
    public async Task Auth_error_is_not_retried_and_hints_at_key()
    {
        var s = new Stub();
        s.Responses.Enqueue(() => Json("nope", HttpStatusCode.Unauthorized));
        var ex = await Assert.ThrowsAsync<FauxelException>(() => Make(s).GetHealthAsync());
        Assert.Contains("API key", ex.Message);
        Assert.Single(s.Requests);
    }

    [Fact]
    public void Missing_settings_fail_fast() =>
        Assert.Throws<FauxelException>(() => new FauxelClient(new FauxelSettings { ProjectId = "" }));
}

public class TransformTests
{
    [Fact]
    public void Slam_point_to_revit_shared_feet_matches_python_alignment_example()
    {
        // `python3 -m fauxel_capture.alignment` prints:
        //   IFC Position (m): [ 4.7598 -3.1914  1.45  ]
        //   Revit Shared Position (ft): [1012.5583 1986.0076  504.7572]
        var slamToBim = Matrix4.Translation(5.10, -8.30, 1.45) * Matrix4.RotationZ(37.5) * Matrix4.Scale(1.42);
        var ifc = slamToBim.Apply(2, 3, 0);
        Assert.Equal(4.7598, ifc[0], 3);
        Assert.Equal(-3.1914, ifc[1], 3);
        Assert.Equal(1.45, ifc[2], 3);

        var feet = Matrix4.Scale(Matrix4.MetersToFeet).Apply(ifc);
        var shared = Matrix4.InternalToShared(new[] { 0.0, 0, 0 }, new[] { 1000.0, 2000, 500 }, 14.25).Apply(feet);
        Assert.InRange(shared[0], 1012.5583 - 1e-3, 1012.5583 + 1e-3);
        Assert.InRange(shared[1], 1986.0076 - 1e-3, 1986.0076 + 1e-3);
        Assert.InRange(shared[2], 504.7572 - 1e-3, 504.7572 + 1e-3);
    }

    [Fact]
    public void Meter_feet_constants_roundtrip() =>
        Assert.Equal(1.0, Matrix4.MetersToFeet * Matrix4.FeetToMeters, 12);

    [Fact]
    public void Translation_scaling_and_array_roundtrip()
    {
        var m = Matrix4.RotationZ(90) * Matrix4.Translation(3, 0, 0);
        var p = m.Apply(1, 0, 0);
        Assert.Equal(0, p[0], 12); Assert.Equal(4, p[1], 12);
        var scaled = new Matrix4(Matrix4.Translation(1, 2, 3).ToArray()).WithTranslationScaled(0.3048).ToArray();
        Assert.Equal(0.3048, scaled[3], 12); Assert.Equal(0.6096, scaled[7], 12); Assert.Equal(0.9144, scaled[11], 12);
        Assert.Throws<ArgumentException>(() => new Matrix4(new double[3]));
    }
}
