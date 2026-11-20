using System;

namespace Fauxel.Connectors
{
    /// <summary>Row-major 4x4 affine matrix (translation in elements 3, 7, 11). Immutable.</summary>
    public sealed class Matrix4
    {
        public const double MetersToFeet = 3.280839895013123;   // 1 / 0.3048
        public const double FeetToMeters = 0.3048;

        readonly double[] _m;
        public Matrix4(double[] rowMajor16)
        {
            if (rowMajor16 == null || rowMajor16.Length != 16) throw new ArgumentException("Matrix4 needs 16 values");
            _m = (double[])rowMajor16.Clone();
        }

        public static Matrix4 Identity { get; } = new Matrix4(new double[] { 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 });

        public static Matrix4 Translation(double x, double y, double z) =>
            new Matrix4(new double[] { 1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1 });

        /// <summary>Counter-clockwise rotation about +Z (right-handed), degrees.</summary>
        public static Matrix4 RotationZ(double degrees)
        {
            double a = degrees * Math.PI / 180.0, c = Math.Cos(a), s = Math.Sin(a);
            return new Matrix4(new double[] { c, -s, 0, 0, s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1 });
        }

        public static Matrix4 Scale(double k) =>
            new Matrix4(new double[] { k, 0, 0, 0, 0, k, 0, 0, 0, 0, k, 0, 0, 0, 0, 1 });

        public double this[int row, int col] => _m[row * 4 + col];
        public double[] ToArray() => (double[])_m.Clone();

        public static Matrix4 operator *(Matrix4 a, Matrix4 b)
        {
            var r = new double[16];
            for (int i = 0; i < 4; i++)
                for (int j = 0; j < 4; j++)
                {
                    double v = 0;
                    for (int k = 0; k < 4; k++) v += a[i, k] * b[k, j];
                    r[i * 4 + j] = v;
                }
            return new Matrix4(r);
        }

        public double[] Apply(double x, double y, double z) => new[]
        {
            _m[0] * x + _m[1] * y + _m[2] * z + _m[3],
            _m[4] * x + _m[5] * y + _m[6] * z + _m[7],
            _m[8] * x + _m[9] * y + _m[10] * z + _m[11],
        };

        public double[] Apply(double[] p) => Apply(p[0], p[1], p[2]);

        /// <summary>Same matrix with the translation column scaled (rotation untouched), e.g. feet to meters.</summary>
        public Matrix4 WithTranslationScaled(double k)
        {
            var r = ToArray();
            r[3] *= k; r[7] *= k; r[11] *= k;
            return new Matrix4(r);
        }

        /// <summary>
        /// Revit internal origin (feet) to shared/survey coordinates (feet), matching
        /// BimCoordinateTransformer.to_revit_shared_feet: p_shared = Rz(-trueNorth) * (p - projectBasePoint) + surveyPoint.
        /// The Revit plugin itself uses ProjectLocation.GetTotalTransform(); this mirrors the Python math for tests.
        /// </summary>
        public static Matrix4 InternalToShared(double[] projectBasePointFt, double[] surveyPointFt, double angleToTrueNorthDeg) =>
            Translation(surveyPointFt[0], surveyPointFt[1], surveyPointFt[2])
            * RotationZ(-angleToTrueNorthDeg)
            * Translation(-projectBasePointFt[0], -projectBasePointFt[1], -projectBasePointFt[2]);
    }
}
