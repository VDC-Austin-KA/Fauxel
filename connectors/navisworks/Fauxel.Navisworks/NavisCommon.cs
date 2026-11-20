using System;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;
using Autodesk.Navisworks.Api;
using Fauxel.Connectors;

namespace Fauxel.Navisworks
{
    static class NavisCommon
    {
        static bool _init;

        /// <summary>
        /// Call before touching TimeLiner types. The TimeLiner reference is the 2025 build; on 2026/2027 map any
        /// Autodesk.Navisworks.* request to the assembly Navisworks already loaded (or its install folder).
        /// </summary>
        public static void Init()
        {
            if (_init) return;
            _init = true;
            AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
            {
                var name = new AssemblyName(e.Name).Name;
                if (IsBundledDependency(name))
                {
                    // net48 plugins get no binding redirects: System.Memory etc. ask for older versions of
                    // System.Runtime.CompilerServices.Unsafe than the one we ship. Serve whatever version sits next to us.
                    foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
                        if (a.GetName().Name == name) return a;
                    var own = Path.Combine(Path.GetDirectoryName(typeof(NavisCommon).Assembly.Location), name + ".dll");
                    return File.Exists(own) ? Assembly.LoadFrom(own) : null;
                }
                if (!name.StartsWith("Autodesk.Navisworks.", StringComparison.Ordinal)) return null;
                foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
                    if (a.GetName().Name == name) return a;
                var dir = Path.GetDirectoryName(typeof(Autodesk.Navisworks.Api.Application).Assembly.Location);
                var path = Path.Combine(dir, name + ".dll");
                return File.Exists(path) ? Assembly.LoadFrom(path) : null;
            };
        }

        /// <summary>Dependencies shipped in the bundle folder that may be requested at another version.</summary>
        public static bool IsBundledDependency(string name) =>
            name.StartsWith("System.", StringComparison.Ordinal) || name.StartsWith("Microsoft.Bcl.", StringComparison.Ordinal);

        public static Document ActiveDoc()
        {
            var doc = Autodesk.Navisworks.Api.Application.ActiveDocument;
            if (doc == null || doc.IsClear) throw new InvalidOperationException("Open a model first.");
            return doc;
        }

        public static FauxelClient Connect() => new FauxelClient(FauxelSettings.Load());

        public static T Wait<T>(Func<Task<T>> f) => Task.Run(f).GetAwaiter().GetResult();
        public static void Wait(Func<Task> f) => Task.Run(f).GetAwaiter().GetResult();

        public static void Info(string text) => MessageBox.Show(text, "Fauxel", MessageBoxButtons.OK, MessageBoxIcon.Information);
        public static void Error(string text) => MessageBox.Show(text, "Fauxel", MessageBoxButtons.OK, MessageBoxIcon.Error);

        /// <summary>Runs a command body, turning every exception into a dialog. Returns 0 (Navisworks ignores the value).</summary>
        public static int Run(Action body)
        {
            try { Init(); body(); }
            catch (Exception ex) { Error(ex.Message); }
            return 0;
        }
    }
}
