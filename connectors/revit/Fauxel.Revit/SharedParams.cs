using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Autodesk.Revit.DB;

namespace Fauxel.Revit
{
    /// <summary>The five Fauxel_* shared parameters. GUIDs are fixed and mirror Fauxel_SharedParameters.txt.</summary>
    static class SharedParams
    {
        public static readonly Guid PercentComplete = new Guid("6194b7e0-ec12-5274-b496-e0132f4dba02");
        public static readonly Guid Status = new Guid("b58eee12-b349-519b-8eea-818a4cc948d8");
        public static readonly Guid LastVerified = new Guid("2d7f87b5-b852-5506-b9dd-e34da5350c83");
        public static readonly Guid Confidence = new Guid("2db8d270-1a9c-5e28-83b4-6588de609d2c");
        public static readonly Guid EvidenceUrl = new Guid("f7bb892d-eac0-590b-b219-d9ca4501b886");

        public static readonly string[] Names =
            { "Fauxel_PercentComplete", "Fauxel_Status", "Fauxel_LastVerified", "Fauxel_Confidence", "Fauxel_EvidenceUrl" };

        static string FilePath => Path.Combine(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location), "Fauxel_SharedParameters.txt");

        /// <summary>Creates or extends the instance bindings so every Fauxel category carries all five parameters. Call inside a Transaction.</summary>
        public static void Ensure(Document doc)
        {
            var app = doc.Application;
            var previous = app.SharedParametersFilename;
            DefinitionFile file;
            try
            {
                app.SharedParametersFilename = FilePath;
                file = app.OpenSharedParameterFile() ?? throw new InvalidOperationException("Cannot open " + FilePath);
            }
            finally { app.SharedParametersFilename = previous; }

            var group = file.Groups.get_Item("Fauxel") ?? throw new InvalidOperationException("Group 'Fauxel' missing in " + FilePath);
            var cats = new CategorySet();
            foreach (var bic in FauxelCategories.All)
            {
                var c = doc.Settings.Categories.get_Item(bic);
                if (c != null && c.AllowsBoundParameters) cats.Insert(c);
            }

            var expected = new[] { PercentComplete, Status, LastVerified, Confidence, EvidenceUrl };
            for (int i = 0; i < Names.Length; i++)
            {
                var def = group.Definitions.get_Item(Names[i]) as ExternalDefinition
                    ?? throw new InvalidOperationException($"{Names[i]} missing in {FilePath}");
                if (def.GUID != expected[i]) throw new InvalidOperationException($"{Names[i]} GUID in {FilePath} does not match the plugin.");
                Bind(doc, def, cats);
            }
        }

        static void Bind(Document doc, ExternalDefinition def, CategorySet wanted)
        {
            var map = doc.ParameterBindings;
            var existing = map.get_Item(def) as InstanceBinding;
            if (existing == null)
            {
                map.Insert(def, doc.Application.Create.NewInstanceBinding(wanted), GroupTypeId.Data);
                return;
            }
            var merged = new CategorySet();
            foreach (Category c in existing.Categories) merged.Insert(c);
            foreach (Category c in wanted) merged.Insert(c);
            map.ReInsert(def, doc.Application.Create.NewInstanceBinding(merged), GroupTypeId.Data);
        }
    }
}
