// Shared WinForms settings dialog, compiled into the Revit and Navisworks hosts (linked, not a project of its own).
using System;
using System.Drawing;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace Fauxel.Connectors.UI
{
    public sealed class SettingsForm : Form
    {
        readonly TextBox _url = new TextBox(), _key = new TextBox(), _project = new TextBox();
        readonly Label _status = new Label();

        SettingsForm(FauxelSettings s)
        {
            Text = "Fauxel Settings";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = MinimizeBox = false;
            ClientSize = new Size(460, 215);

            AddRow("API URL", _url, 15, s.ApiUrl);
            AddRow("API key", _key, 55, s.ApiKey);
            _key.UseSystemPasswordChar = true;
            AddRow("Project id", _project, 95, s.ProjectId);

            var test = new Button { Text = "Test connection", Left = 110, Top = 135, Width = 120 };
            var ok = new Button { Text = "Save", Left = 285, Top = 175, Width = 80, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = "Cancel", Left = 370, Top = 175, Width = 80, DialogResult = DialogResult.Cancel };
            _status.SetBounds(15, 168, 260, 40);
            test.Click += (o, e) => Test();
            Controls.AddRange(new Control[] { test, ok, cancel, _status });
            AcceptButton = ok; CancelButton = cancel;
        }

        void AddRow(string label, TextBox box, int top, string value)
        {
            Controls.Add(new Label { Text = label, Left = 15, Top = top + 3, Width = 90 });
            box.SetBounds(110, top, 335, 24);
            box.Text = value ?? "";
            Controls.Add(box);
        }

        FauxelSettings Current() => new FauxelSettings
        {
            ApiUrl = _url.Text.Trim(), ApiKey = _key.Text.Trim(), ProjectId = _project.Text.Trim(),
            TimeoutSeconds = FauxelSettings.Load().TimeoutSeconds,
        };

        void Test()
        {
            Cursor = Cursors.WaitCursor;
            try
            {
                using (var c = new FauxelClient(Current()))
                    _status.Text = Task.Run(() => c.TestConnectionAsync()).GetAwaiter().GetResult();
                _status.ForeColor = Color.DarkGreen;
            }
            catch (Exception ex)
            {
                _status.Text = ex.Message.Length > 220 ? ex.Message.Substring(0, 220) + "..." : ex.Message;
                _status.ForeColor = Color.Firebrick;
            }
            finally { Cursor = Cursors.Default; }
        }

        /// <summary>Shows the dialog; saves to %APPDATA%\Fauxel\settings.json on OK. Returns true if saved.</summary>
        public static bool Edit()
        {
            using (var f = new SettingsForm(FauxelSettings.Load()))
            {
                if (f.ShowDialog() != DialogResult.OK) return false;
                f.Current().Save();
                return true;
            }
        }
    }
}
