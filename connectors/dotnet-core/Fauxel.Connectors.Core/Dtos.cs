using System.Collections.Generic;

namespace Fauxel.Connectors
{
    // JSON shapes from docs/specs/platform-contract.md (writeback + bim-export + timeliner + ack).
    // Serialized with snake_case names by FauxelJson.

    public sealed class WritebackPayload
    {
        public string ProjectId { get; set; }
        public string GeneratedAt { get; set; }
        public int RulesVersion { get; set; }
        public List<WritebackElement> Elements { get; set; } = new List<WritebackElement>();
    }

    public sealed class WritebackElement
    {
        public string ElementUid { get; set; }
        public string RevitUniqueId { get; set; }
        public string IfcGlobalId { get; set; }
        public string NavisItemGuid { get; set; }
        public double PercentComplete { get; set; }
        public string Status { get; set; }
        public string LastVerified { get; set; }
        public double Confidence { get; set; }
        public string EvidenceUrl { get; set; }
        public string Color { get; set; }
    }

    public sealed class BimExport
    {
        public string Source { get; set; }          // revit | navisworks
        public string ModelName { get; set; }
        public string HostVersion { get; set; }
        public string Units { get; set; } = "m";
        public double[] SharedTransform { get; set; } // 16 floats, row-major, internal -> shared, meters
        public List<BimLevel> Levels { get; set; } = new List<BimLevel>();
        public List<BimElement> Elements { get; set; } = new List<BimElement>();
    }

    public sealed class BimLevel
    {
        public string Name { get; set; }
        public double ElevationM { get; set; }
    }

    public sealed class BimElement
    {
        public string RevitUniqueId { get; set; }
        public string IfcGlobalId { get; set; }
        public string NavisItemGuid { get; set; }
        public string Name { get; set; }
        public string Category { get; set; }
        public string FamilyType { get; set; }
        public string Level { get; set; }
        public string RoomOrZone { get; set; }
        public string Trade { get; set; }
        public double[] AabbMin { get; set; }
        public double[] AabbMax { get; set; }
        public double Quantity { get; set; }
        public string QuantityUnit { get; set; }    // m | m2 | m3 | ea
    }

    public sealed class TimelinerTaskDto
    {
        public string ExternalId { get; set; }
        public string Name { get; set; }
        public string ActualStart { get; set; }
        public string ActualFinish { get; set; }
        public double PercentComplete { get; set; }
    }

    public sealed class WritebackAck
    {
        public string Host { get; set; }            // revit | navisworks
        public int Applied { get; set; }
        public string Model { get; set; }
    }

    public sealed class HealthStatus
    {
        public string Status { get; set; }
        // Component states may be strings or booleans depending on the API build; kept loose on purpose.
        public object Db { get; set; }
        public object Storage { get; set; }
        public object Gpu { get; set; }
    }
}
