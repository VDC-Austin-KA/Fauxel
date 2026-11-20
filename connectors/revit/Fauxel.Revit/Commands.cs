using System;
using System.Threading.Tasks;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Fauxel.Connectors;
using Fauxel.Connectors.UI;

namespace Fauxel.Revit
{
    public static class Commands
    {
        // Revit commands are synchronous; run the HTTP work off the UI thread's context to avoid deadlocks.
        static T Wait<T>(Func<Task<T>> f) => Task.Run(f).GetAwaiter().GetResult();
        static void Wait(Func<Task> f) => Task.Run(f).GetAwaiter().GetResult();

        static FauxelClient Connect(out string error)
        {
            error = null;
            try { return new FauxelClient(FauxelSettings.Load()); }
            catch (Exception ex) { error = ex.Message; return null; }
        }

        static Result Fail(ref string message, string text)
        {
            message = text;
            return Result.Failed;
        }

        [Transaction(TransactionMode.Manual)]
        public class SettingsCommand : IExternalCommand
        {
            public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
            {
                try { SettingsForm.Edit(); return Result.Succeeded; }
                catch (Exception ex) { return Fail(ref message, ex.Message); }
            }
        }

        [Transaction(TransactionMode.Manual)]
        public class ExportModelCommand : IExternalCommand
        {
            public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
            {
                var doc = data.Application.ActiveUIDocument?.Document;
                if (doc == null) return Fail(ref message, "Open a project first.");
                try
                {
                    using (var client = Connect(out var err))
                    {
                        if (client == null) return Fail(ref message, err);
                        var export = ModelExporter.Build(doc, data.Application.Application.VersionNumber);
                        if (export.Elements.Count == 0) return Fail(ref message, "No Structural/Architectural/MEP/Fire Protection elements with geometry found.");
                        Wait((Func<Task>)(() => client.ImportModelAsync(export)));
                        TaskDialog.Show("Fauxel", $"Exported {export.Elements.Count} elements on {export.Levels.Count} levels from \"{export.ModelName}\".");
                    }
                    return Result.Succeeded;
                }
                catch (Exception ex) { return Fail(ref message, ex.Message); }
            }
        }

        [Transaction(TransactionMode.Manual)]
        public class SyncProgressCommand : IExternalCommand
        {
            public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
            {
                var uidoc = data.Application.ActiveUIDocument;
                if (uidoc == null) return Fail(ref message, "Open a project first.");
                var doc = uidoc.Document;
                try
                {
                    using (var client = Connect(out var err))
                    {
                        if (client == null) return Fail(ref message, err);
                        var payload = Wait(() => client.GetWritebackAsync());
                        if (payload.Elements.Count == 0) { TaskDialog.Show("Fauxel", "Nothing to write back yet: no verified elements."); return Result.Succeeded; }

                        var r = ProgressSync.Apply(doc, payload);
                        try { uidoc.ActiveView = r.View; } catch (Exception) { /* view switch is best effort */ }
                        Wait(() => client.AckAsync(new WritebackAck { Host = "revit", Applied = r.Applied, Model = doc.Title }));
                        TaskDialog.Show("Fauxel", $"Updated {r.Applied} elements ({r.Unmatched} not found in this model).\nView: \"{ProgressSync.ViewName}\", schedule: \"{ProgressSync.ScheduleName}\".");
                    }
                    return Result.Succeeded;
                }
                catch (Exception ex) { return Fail(ref message, ex.Message); }
            }
        }

        [Transaction(TransactionMode.Manual)]
        public class ClearOverridesCommand : IExternalCommand
        {
            public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
            {
                var doc = data.Application.ActiveUIDocument?.Document;
                if (doc == null) return Fail(ref message, "Open a project first.");
                try
                {
                    int n = ProgressSync.ClearOverrides(doc);
                    TaskDialog.Show("Fauxel", n < 0 ? $"No \"{ProgressSync.ViewName}\" view in this model." : $"Cleared overrides on {n} elements.");
                    return Result.Succeeded;
                }
                catch (Exception ex) { return Fail(ref message, ex.Message); }
            }
        }
    }
}
