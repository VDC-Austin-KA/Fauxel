using System.Linq;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Plugins;
using Fauxel.Connectors;
using Fauxel.Connectors.UI;

namespace Fauxel.Navisworks
{
    // Developer id FXEL. Each command appears under Add-ins > External Tools.

    [Plugin("Fauxel.Settings", "FXEL", DisplayName = "Fauxel: Settings", ToolTip = "API URL, key and project id")]
    [AddInPlugin(AddInLocation.AddIn)]
    public class SettingsCommand : AddInPlugin
    {
        public override int Execute(params string[] parameters) => NavisCommon.Run(() => SettingsForm.Edit());
    }

    [Plugin("Fauxel.ExportTree", "FXEL", DisplayName = "Fauxel: Export Selection Tree", ToolTip = "Send model elements to Fauxel")]
    [AddInPlugin(AddInLocation.AddIn)]
    public class ExportTreeCommand : AddInPlugin
    {
        public override int Execute(params string[] parameters) => NavisCommon.Run(() =>
        {
            var doc = NavisCommon.ActiveDoc();
            using (var client = NavisCommon.Connect())
            {
                var export = ExportTree.Build(doc);
                if (export.Elements.Count == 0) { NavisCommon.Error("No elements with geometry found."); return; }
                NavisCommon.Wait((System.Func<System.Threading.Tasks.Task>)(() => client.ImportModelAsync(export)));
                NavisCommon.Info($"Exported {export.Elements.Count} elements on {export.Levels.Count} levels.");
            }
        });
    }

    [Plugin("Fauxel.SyncTimeliner", "FXEL", DisplayName = "Fauxel: Sync TimeLiner", ToolTip = "Write actual dates and progress into TimeLiner tasks")]
    [AddInPlugin(AddInLocation.AddIn)]
    public class SyncTimelinerCommand : AddInPlugin
    {
        public override int Execute(params string[] parameters) => NavisCommon.Run(() =>
        {
            var doc = NavisCommon.ActiveDoc();
            using (var client = NavisCommon.Connect())
            {
                var tasks = NavisCommon.Wait(() => client.GetTimelinerAsync());
                var lines = TimelinerSync.Apply(doc, tasks);
                NavisCommon.Wait((System.Func<System.Threading.Tasks.Task>)(() =>
                    client.AckAsync(new WritebackAck { Host = "navisworks", Applied = lines.Count, Model = doc.Title })));
                var shown = string.Join("\n", lines.Take(25));
                NavisCommon.Info(lines.Count == 0
                    ? $"TimeLiner already up to date ({tasks.Count} tasks from Fauxel)."
                    : $"Updated {lines.Count} of {tasks.Count} tasks:\n{shown}" + (lines.Count > 25 ? $"\n... and {lines.Count - 25} more" : ""));
            }
        });
    }

    [Plugin("Fauxel.ApplyColors", "FXEL", DisplayName = "Fauxel: Apply Status Colors", ToolTip = "Color elements by verified status")]
    [AddInPlugin(AddInLocation.AddIn)]
    public class ApplyColorsCommand : AddInPlugin
    {
        public override int Execute(params string[] parameters) => NavisCommon.Run(() =>
        {
            var doc = NavisCommon.ActiveDoc();
            using (var client = NavisCommon.Connect())
            {
                var payload = NavisCommon.Wait(() => client.GetWritebackAsync());
                var (applied, unmatched) = StatusColors.Apply(doc, payload);
                NavisCommon.Wait((System.Func<System.Threading.Tasks.Task>)(() =>
                    client.AckAsync(new WritebackAck { Host = "navisworks", Applied = applied, Model = doc.Title })));
                NavisCommon.Info($"Colored {applied} elements ({unmatched} not found in this model).");
            }
        });
    }

    [Plugin("Fauxel.ResetColors", "FXEL", DisplayName = "Fauxel: Reset Colors", ToolTip = "Remove all permanent color/transparency overrides")]
    [AddInPlugin(AddInLocation.AddIn)]
    public class ResetColorsCommand : AddInPlugin
    {
        public override int Execute(params string[] parameters) => NavisCommon.Run(() =>
        {
            StatusColors.Reset(NavisCommon.ActiveDoc());
            NavisCommon.Info("Permanent color and transparency overrides cleared (all of them, not only Fauxel's).");
        });
    }
}
