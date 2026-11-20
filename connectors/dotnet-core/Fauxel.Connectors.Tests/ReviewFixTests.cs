namespace Fauxel.Connectors.Tests;

    public class ReviewFixTests
    {
        [Xunit.Theory]
        [Xunit.InlineData("https://ackerworks.cloudflareaccess.com/cdn-cgi/access/login", "text/html", 200, true)]
        [Xunit.InlineData("https://fauxel.ackerworks.com/api/v1/health", "text/html", 200, true)]
        [Xunit.InlineData("https://fauxel.ackerworks.com/api/v1/health", "application/json", 200, false)]
        [Xunit.InlineData("https://fauxel.ackerworks.com/api/v1/health", null, 302, true)]
        [Xunit.InlineData("https://fauxel.ackerworks.com/api/v1/x", "application/json", 404, false)]
        public void Detects_cloudflare_access_login(string url, string media, int status, bool expected) =>
            Xunit.Assert.Equal(expected, FauxelClient.IsAccessLogin(new System.Uri(url), media, status));

        [Xunit.Fact]
        public async System.Threading.Tasks.Task Sends_access_service_token_headers()
        {
            System.Net.Http.HttpRequestMessage seen = null;
            var h = new StubHandler(r => { seen = r; return new System.Net.Http.HttpResponseMessage(System.Net.HttpStatusCode.OK)
                { Content = new System.Net.Http.StringContent("{\"status\":\"ok\"}", System.Text.Encoding.UTF8, "application/json") }; });
            var s = new FauxelSettings { ApiUrl = "https://fauxel.ackerworks.com", ApiKey = "k", ProjectId = "p",
                CfAccessClientId = "id.access", CfAccessClientSecret = "sec" };
            using (var c = new FauxelClient(s, h)) await c.GetHealthAsync();
            Xunit.Assert.Equal("id.access", string.Join("", seen.Headers.GetValues("CF-Access-Client-Id")));
            Xunit.Assert.Equal("sec", string.Join("", seen.Headers.GetValues("CF-Access-Client-Secret")));
        }

        sealed class StubHandler : System.Net.Http.HttpMessageHandler
        {
            readonly System.Func<System.Net.Http.HttpRequestMessage, System.Net.Http.HttpResponseMessage> _f;
            public StubHandler(System.Func<System.Net.Http.HttpRequestMessage, System.Net.Http.HttpResponseMessage> f) => _f = f;
            protected override System.Threading.Tasks.Task<System.Net.Http.HttpResponseMessage> SendAsync(
                System.Net.Http.HttpRequestMessage r, System.Threading.CancellationToken ct)
            { var resp = _f(r); resp.RequestMessage = r; return System.Threading.Tasks.Task.FromResult(resp); }
        }
    }
