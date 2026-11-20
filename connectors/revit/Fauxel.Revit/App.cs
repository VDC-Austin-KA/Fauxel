using System;
using System.IO;
using System.Reflection;
using Autodesk.Revit.UI;

namespace Fauxel.Revit
{
    public sealed class App : IExternalApplication
    {
        public Result OnStartup(UIControlledApplication app)
        {
            try
            {
                const string tab = "Fauxel";
                try { app.CreateRibbonTab(tab); } catch (Autodesk.Revit.Exceptions.ArgumentException) { /* tab exists */ }
                var panel = app.CreateRibbonPanel(tab, "Progress");
                var dll = Assembly.GetExecutingAssembly().Location;

                panel.AddItem(Button(dll, "Settings", "Settings", nameof(Commands.SettingsCommand), "Set API URL, key and project id"));
                panel.AddSeparator();
                panel.AddItem(Button(dll, "ExportModel", "Export\nModel", nameof(Commands.ExportModelCommand), "Send model elements to Fauxel"));
                panel.AddItem(Button(dll, "SyncProgress", "Sync\nProgress", nameof(Commands.SyncProgressCommand), "Write verified field progress into the model"));
                panel.AddItem(Button(dll, "ClearOverrides", "Clear\nOverrides", nameof(Commands.ClearOverridesCommand), "Remove Fauxel colors from the Fauxel Progress view"));
                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                TaskDialog.Show("Fauxel", "Fauxel failed to start: " + ex.Message);
                return Result.Failed;
            }
        }

        public Result OnShutdown(UIControlledApplication app) => Result.Succeeded;

        static PushButtonData Button(string dll, string id, string text, string cls, string tip) =>
            new PushButtonData("Fauxel_" + id, text, dll, "Fauxel.Revit.Commands." + cls) { ToolTip = tip };
    }
}
