using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;
using Fauxel.Connectors;

namespace Fauxel.Revit
{
    sealed class SyncResult
    {
        public int Applied, Unmatched;
        public View3D View;
    }

    static class ProgressSync
    {
        public const string ViewName = "Fauxel Progress";
        public const string ScheduleName = "Fauxel Progress Schedule";

        /// <summary>One TransactionGroup (single undo step): bind parameters, write values, color the view, build the schedule.</summary>
        public static SyncResult Apply(Document doc, WritebackPayload payload)
        {
            var result = new SyncResult();
            using (var group = new TransactionGroup(doc, "Fauxel: sync progress"))
            {
                group.Start();

                using (var t = new Transaction(doc, "Fauxel: shared parameters"))
                {
                    t.Start();
                    SharedParams.Ensure(doc);
                    t.Commit();
                }

                var matched = new List<(Element el, WritebackElement wb)>();
                using (var t = new Transaction(doc, "Fauxel: write values"))
                {
                    t.Start();
                    matched = Match(doc, payload.Elements);
                    foreach (var (el, wb) in matched) WriteValues(el, wb);
                    t.Commit();
                }
                result.Applied = matched.Count;
                result.Unmatched = payload.Elements.Count - matched.Count;

                using (var t = new Transaction(doc, "Fauxel: view and schedule"))
                {
                    t.Start();
                    result.View = EnsureView(doc);
                    ColorView(doc, result.View, matched);
                    EnsureSchedule(doc);
                    t.Commit();
                }
                group.Assimilate();
            }
            return result;
        }

        /// <summary>Resets the overrides Fauxel applied (all tracked elements) in the Fauxel Progress view.</summary>
        public static int ClearOverrides(Document doc)
        {
            var view = FindView(doc);
            if (view == null) return -1;
            int n = 0;
            using (var t = new Transaction(doc, "Fauxel: clear overrides"))
            {
                t.Start();
                var blank = new OverrideGraphicSettings();
                foreach (var el in FauxelCategories.Collect(doc))
                {
                    try { view.SetElementOverrides(el.Id, blank); n++; } catch (Autodesk.Revit.Exceptions.ArgumentException) { }
                }
                t.Commit();
            }
            return n;
        }

        // Matching: UniqueId first, then IFC GUID for the rest.
        static List<(Element, WritebackElement)> Match(Document doc, List<WritebackElement> items)
        {
            var found = new List<(Element, WritebackElement)>();
            var rest = new List<WritebackElement>();
            foreach (var wb in items)
            {
                var el = string.IsNullOrEmpty(wb.RevitUniqueId) ? null : doc.GetElement(wb.RevitUniqueId);
                if (el != null) found.Add((el, wb)); else if (!string.IsNullOrEmpty(wb.IfcGlobalId)) rest.Add(wb);
            }
            if (rest.Count > 0)
            {
                var byIfc = new Dictionary<string, Element>();
                foreach (var el in FauxelCategories.Collect(doc))
                {
                    var g = el.get_Parameter(BuiltInParameter.IFC_GUID)?.AsString();
                    if (!string.IsNullOrEmpty(g) && !byIfc.ContainsKey(g)) byIfc[g] = el;
                }
                foreach (var wb in rest) if (byIfc.TryGetValue(wb.IfcGlobalId, out var el)) found.Add((el, wb));
            }
            return found;
        }

        static void WriteValues(Element el, WritebackElement wb)
        {
            Set(el, SharedParams.PercentComplete, wb.PercentComplete);
            Set(el, SharedParams.Status, wb.Status ?? "");
            Set(el, SharedParams.LastVerified, wb.LastVerified ?? "");
            Set(el, SharedParams.Confidence, wb.Confidence);
            Set(el, SharedParams.EvidenceUrl, wb.EvidenceUrl ?? "");
        }

        static void Set(Element el, Guid guid, double v) { var p = el.get_Parameter(guid); if (p != null && !p.IsReadOnly) p.Set(v); }
        static void Set(Element el, Guid guid, string v) { var p = el.get_Parameter(guid); if (p != null && !p.IsReadOnly) p.Set(v); }

        static View3D FindView(Document doc) =>
            new FilteredElementCollector(doc).OfClass(typeof(View3D)).Cast<View3D>().FirstOrDefault(v => !v.IsTemplate && v.Name == ViewName);

        static View3D EnsureView(Document doc)
        {
            var view = FindView(doc);
            if (view != null) return view;
            var vft = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>()
                .First(v => v.ViewFamily == ViewFamily.ThreeDimensional);
            view = View3D.CreateIsometric(doc, vft.Id);
            view.Name = ViewName;
            return view;
        }

        static void ColorView(Document doc, View3D view, List<(Element el, WritebackElement wb)> matched)
        {
            var solid = new FilteredElementCollector(doc).OfClass(typeof(FillPatternElement)).Cast<FillPatternElement>()
                .FirstOrDefault(f => f.GetFillPattern().IsSolidFill && f.GetFillPattern().Target == FillPatternTarget.Drafting);
            var cache = new Dictionary<string, OverrideGraphicSettings>();
            foreach (var (el, wb) in matched)
            {
                var rgb = StatusPalette.For(wb);
                if (!cache.TryGetValue(rgb.Hex, out var ogs))
                {
                    var color = new Color(rgb.R, rgb.G, rgb.B);
                    ogs = new OverrideGraphicSettings();
                    if (solid != null)
                    {
                        ogs.SetSurfaceForegroundPatternId(solid.Id);
                        ogs.SetSurfaceForegroundPatternColor(color);
                        ogs.SetCutForegroundPatternId(solid.Id);
                        ogs.SetCutForegroundPatternColor(color);
                    }
                    ogs.SetProjectionLineColor(color);
                    cache[rgb.Hex] = ogs;
                }
                try { view.SetElementOverrides(el.Id, ogs); }
                catch (Autodesk.Revit.Exceptions.ArgumentException) { /* category hidden or not overridable in this view */ }
            }
        }

        static void EnsureSchedule(Document doc)
        {
            if (new FilteredElementCollector(doc).OfClass(typeof(ViewSchedule)).Cast<ViewSchedule>().Any(s => s.Name == ScheduleName)) return;
            var sched = ViewSchedule.CreateSchedule(doc, ElementId.InvalidElementId);   // multi-category
            sched.Name = ScheduleName;
            var def = sched.Definition;
            def.IsItemized = true;
            var fields = def.GetSchedulableFields();

            void Add(Func<SchedulableField, bool> pick)
            {
                var f = fields.FirstOrDefault(pick);
                if (f != null) def.AddField(f);
            }
            Add(f => f.ParameterId.Equals(new ElementId(BuiltInParameter.ELEM_CATEGORY_PARAM)));
            Add(f => f.ParameterId.Equals(new ElementId(BuiltInParameter.ELEM_FAMILY_AND_TYPE_PARAM)));
            Add(f => f.ParameterId.Equals(new ElementId(BuiltInParameter.SCHEDULE_LEVEL_PARAM)));
            foreach (var name in SharedParams.Names) Add(f => f.GetName(doc) == name);

            var status = Enumerable.Range(0, def.GetFieldCount()).Select(def.GetField).FirstOrDefault(f => f.GetName() == "Fauxel_Status");
            if (status != null) def.AddSortGroupField(new ScheduleSortGroupField(status.FieldId));
        }
    }
}
