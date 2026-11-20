using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;

namespace Fauxel.Connectors
{
    public sealed class FauxelException : Exception
    {
        /// <summary>True for failures that retrying cannot fix (4xx, bad JSON).</summary>
        public bool Permanent { get; }
        public FauxelException(string message, Exception inner = null, bool permanent = false) : base(message, inner) { Permanent = permanent; }
    }

    /// <summary>Thin REST client for the Fauxel API (/api/v1). Retries transient failures (network, timeout, 5xx).</summary>
    public sealed class FauxelClient : IDisposable
    {
        const int MaxAttempts = 3;
        readonly HttpClient _http;
        readonly string _base;
        readonly string _project;

        public TimeSpan RetryDelay { get; set; } = TimeSpan.FromMilliseconds(500);

        public FauxelClient(FauxelSettings settings, HttpMessageHandler handler = null)
        {
            var err = settings.Validate();
            if (err != null) throw new FauxelException(err);
            var b = settings.ApiUrl.Trim().TrimEnd('/');
            _base = b.EndsWith("/api/v1", StringComparison.OrdinalIgnoreCase) ? b : b + "/api/v1";
            _project = Uri.EscapeDataString(settings.ProjectId.Trim());
            _http = handler == null ? new HttpClient() : new HttpClient(handler);
            _http.Timeout = TimeSpan.FromSeconds(Math.Max(5, settings.TimeoutSeconds));
            if (!string.IsNullOrEmpty(settings.ApiKey)) _http.DefaultRequestHeaders.Add("X-Fauxel-Key", settings.ApiKey);
            if (!string.IsNullOrEmpty(settings.CfAccessClientId) && !string.IsNullOrEmpty(settings.CfAccessClientSecret))
            {
                _http.DefaultRequestHeaders.Add("CF-Access-Client-Id", settings.CfAccessClientId.Trim());
                _http.DefaultRequestHeaders.Add("CF-Access-Client-Secret", settings.CfAccessClientSecret.Trim());
            }
            _http.DefaultRequestHeaders.Accept.ParseAdd("application/json");
        }

        public Task<HealthStatus> GetHealthAsync() => SendAsync<HealthStatus>(HttpMethod.Get, "/health", null);

        public Task<WritebackPayload> GetWritebackAsync(string since = null) =>
            SendAsync<WritebackPayload>(HttpMethod.Get,
                $"/projects/{_project}/writeback" + (since == null ? "" : "?since=" + Uri.EscapeDataString(since)), null);

        public Task<List<TimelinerTaskDto>> GetTimelinerAsync() =>
            SendAsync<List<TimelinerTaskDto>>(HttpMethod.Get, $"/projects/{_project}/timeliner", null);

        /// <summary>Checks the API is up and the configured project id exists.</summary>
        public async Task<string> TestConnectionAsync()
        {
            var h = await GetHealthAsync().ConfigureAwait(false);
            await SendAsync<string>(HttpMethod.Get, $"/projects/{_project}", null).ConfigureAwait(false);
            return $"Connected. API status: {h.Status}, db: {h.Db}, storage: {h.Storage}. Project found.";
        }

        /// <summary>Returns the raw JSON of the created/updated model.</summary>
        public Task<string> ImportModelAsync(BimExport export) =>
            SendAsync<string>(HttpMethod.Post, $"/projects/{_project}/models/import", FauxelJson.Serialize(export));

        public Task AckAsync(WritebackAck ack) =>
            SendAsync<string>(HttpMethod.Post, $"/projects/{_project}/writeback/ack", FauxelJson.Serialize(ack));

        async Task<T> SendAsync<T>(HttpMethod method, string path, string jsonBody)
        {
            var url = _base + path;
            Exception last = null;
            for (int attempt = 1; attempt <= MaxAttempts; attempt++)
            {
                try
                {
                    using (var req = new HttpRequestMessage(method, url))
                    {
                        if (jsonBody != null) req.Content = new StringContent(jsonBody, Encoding.UTF8, "application/json");
                        using (var resp = await _http.SendAsync(req).ConfigureAwait(false))
                        {
                            var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                            if (IsAccessLogin(resp.RequestMessage?.RequestUri, resp.Content.Headers.ContentType?.MediaType, (int)resp.StatusCode))
                                throw new FauxelException("Cloudflare Access answered with its login page instead of the Fauxel API. " +
                                    "Enter the Access service token (client id and secret) in Fauxel > Settings.", null, true);
                            if (resp.IsSuccessStatusCode)
                            {
                                if (typeof(T) == typeof(string)) return (T)(object)text;
                                try { return FauxelJson.Deserialize<T>(text); }
                                catch (System.Text.Json.JsonException ex)
                                { throw new FauxelException($"Fauxel API {method} {url} returned unexpected JSON: {ex.Message}", ex, true); }
                            }
                            var msg = Describe(resp.StatusCode, method, url, text);
                            if ((int)resp.StatusCode < 500) throw new FauxelException(msg, null, true);
                            last = new FauxelException(msg);
                        }
                    }
                }
                catch (FauxelException ex) when (ex.Permanent) { throw; }
                catch (FauxelException) { /* 5xx recorded in last, retry */ }
                catch (HttpRequestException ex) { last = new FauxelException($"Cannot reach Fauxel API at {_base}: {ex.Message}. Check the URL, VPN/tunnel and that the API is running.", ex); }
                catch (TaskCanceledException ex) { last = new FauxelException($"Fauxel API {method} {url} timed out after {_http.Timeout.TotalSeconds:0}s.", ex); }

                if (attempt < MaxAttempts) await Task.Delay(TimeSpan.FromTicks(RetryDelay.Ticks * attempt)).ConfigureAwait(false);
            }
            throw last;
        }

        /// <summary>True when the response is Cloudflare Access's sign-in page rather than the API (redirected or HTML).</summary>
        public static bool IsAccessLogin(Uri finalUri, string mediaType, int status)
        {
            if (finalUri != null && finalUri.Host.EndsWith("cloudflareaccess.com", StringComparison.OrdinalIgnoreCase)) return true;
            if (status >= 300 && status < 400) return true;
            return status < 300 && string.Equals(mediaType, "text/html", StringComparison.OrdinalIgnoreCase);
        }

        static string Describe(HttpStatusCode code, HttpMethod method, string url, string body)
        {
            string hint = code == HttpStatusCode.Unauthorized || code == HttpStatusCode.Forbidden ? " Check the API key in Fauxel > Settings."
                : code == HttpStatusCode.NotFound ? " Check the project id in Fauxel > Settings."
                : "";
            var snippet = string.IsNullOrWhiteSpace(body) ? "" : " Response: " + (body.Length > 300 ? body.Substring(0, 300) + "..." : body);
            return $"Fauxel API {method} {url} failed: {(int)code} {code}.{hint}{snippet}";
        }

        public void Dispose() => _http.Dispose();
    }
}
