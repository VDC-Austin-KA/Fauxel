using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Runtime.CompilerServices;
using Autodesk.Navisworks.Api;
using Autodesk.Navisworks.Api.Timeliner;
using Fauxel.Connectors;

namespace Fauxel.Navisworks
{
    /// <summary>Kept in its own class so NavisCommon.Init() (assembly resolve) runs before any Timeliner type is JIT-bound.</summary>
    static class TimelinerSync
    {
        sealed class Edit
        {
            public List<int> Path;
            public TimelinerTask Copy;
            public string Line;
        }

        /// <summary>Returns human-readable change lines.</summary>
        [MethodImpl(MethodImplOptions.NoInlining)]
        public static List<string> Apply(Document doc, List<TimelinerTaskDto> wanted)
        {
            var timeliner = doc.GetTimeliner();
            var byId = wanted.Where(w => !string.IsNullOrEmpty(w.ExternalId)).GroupBy(w => w.ExternalId).ToDictionary(g => g.Key, g => g.First());
            var byName = wanted.Where(w => !string.IsNullOrEmpty(w.Name)).GroupBy(w => w.Name, StringComparer.OrdinalIgnoreCase).ToDictionary(g => g.Key, g => g.First(), StringComparer.OrdinalIgnoreCase);

            // 1) read: find matching tasks and prepare edited copies. Nothing is modified yet, so index paths stay valid.
            var edits = new List<Edit>();
            Collect(timeliner.TasksRoot, new List<int>(), byId, byName, edits);

            // 2) write: copy-and-replace through the document API (TaskEdit), which is undoable and notifies Navisworks.
            foreach (var e in edits)
            {
                var parent = timeliner.TasksRoot;
                for (int i = 0; i < e.Path.Count - 1; i++) parent = (GroupItem)parent.Children[e.Path[i]];
                timeliner.TaskEdit(parent, e.Path[e.Path.Count - 1], e.Copy);
            }
            return edits.Select(e => e.Line).ToList();
        }

        static void Collect(GroupItem parent, List<int> path, Dictionary<string, TimelinerTaskDto> byId,
                            Dictionary<string, TimelinerTaskDto> byName, List<Edit> edits)
        {
            for (int i = 0; i < parent.Children.Count; i++)
            {
                var task = parent.Children[i] as TimelinerTask;
                if (task == null) continue;
                var here = new List<int>(path) { i };

                TimelinerTaskDto dto = null;
                if (!string.IsNullOrEmpty(task.DisplayId)) byId.TryGetValue(task.DisplayId, out dto);
                if (dto == null && !string.IsNullOrEmpty(task.SynchronizationId)) byId.TryGetValue(task.SynchronizationId, out dto);
                if (dto == null && !string.IsNullOrEmpty(task.DisplayName)) byName.TryGetValue(task.DisplayName, out dto);

                if (dto != null)
                {
                    var copy = (TimelinerTask)task.CreateCopyWithoutChildren();
                    var changes = new List<string>();
                    var start = ParseDate(dto.ActualStart);
                    var end = ParseDate(dto.ActualFinish);
                    if (start.HasValue && task.ActualStartDate != start) { copy.ActualStartDate = start; changes.Add("actual start " + start.Value.ToString("yyyy-MM-dd")); }
                    if (end.HasValue && task.ActualEndDate != end) { copy.ActualEndDate = end; changes.Add("actual end " + end.Value.ToString("yyyy-MM-dd")); }
                    if (task.ProgressPercent == null || Math.Abs(task.ProgressPercent.Value - dto.PercentComplete) > 0.01)
                    { copy.ProgressPercent = dto.PercentComplete; changes.Add($"progress {dto.PercentComplete:0.#}%"); }
                    if (changes.Count > 0)
                    {
                        if (start.HasValue || end.HasValue) copy.IsActualEnabled = true;
                        edits.Add(new Edit { Path = here, Copy = copy, Line = $"{(task.DisplayId ?? "")} {task.DisplayName}: {string.Join(", ", changes)}".Trim() });
                    }
                }
                if (task.Children.Count > 0) Collect(task, here, byId, byName, edits);
            }
        }

        static DateTime? ParseDate(string s) =>
            !string.IsNullOrEmpty(s) && DateTimeOffset.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var d) ? d.UtcDateTime.Date : (DateTime?)null;
    }
}
