using System.Collections.Generic;
using Autodesk.Revit.DB;

namespace Fauxel.Revit
{
    /// <summary>Categories Fauxel tracks: Structural, Architectural, MEP and Fire Protection.</summary>
    static class FauxelCategories
    {
        public static readonly BuiltInCategory[] All =
        {
            // Structural
            BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_StructuralFoundation,
            // Architectural
            BuiltInCategory.OST_Walls, BuiltInCategory.OST_Floors, BuiltInCategory.OST_Ceilings, BuiltInCategory.OST_Roofs,
            BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows, BuiltInCategory.OST_Stairs, BuiltInCategory.OST_Railings,
            BuiltInCategory.OST_Casework, BuiltInCategory.OST_Columns,
            // Mechanical
            BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_DuctFitting, BuiltInCategory.OST_DuctAccessory,
            BuiltInCategory.OST_FlexDuctCurves, BuiltInCategory.OST_DuctTerminal, BuiltInCategory.OST_MechanicalEquipment,
            // Plumbing + piping
            BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_PipeFitting, BuiltInCategory.OST_PipeAccessory,
            BuiltInCategory.OST_FlexPipeCurves, BuiltInCategory.OST_PlumbingFixtures,
            // Fire protection
            BuiltInCategory.OST_Sprinklers,
            // Electrical
            BuiltInCategory.OST_CableTray, BuiltInCategory.OST_CableTrayFitting, BuiltInCategory.OST_Conduit,
            BuiltInCategory.OST_ConduitFitting, BuiltInCategory.OST_ElectricalEquipment, BuiltInCategory.OST_ElectricalFixtures,
            BuiltInCategory.OST_LightingFixtures, BuiltInCategory.OST_FireAlarmDevices, BuiltInCategory.OST_DataDevices,
        };

        public static IEnumerable<Element> Collect(Document doc) =>
            new FilteredElementCollector(doc)
                .WherePasses(new ElementMulticategoryFilter(All))
                .WhereElementIsNotElementType()
                .ToElements();
    }
}
