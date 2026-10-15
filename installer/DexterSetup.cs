using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using Microsoft.Win32;

namespace DexterInstaller
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            bool isUninstall = false;
            bool isSilent = false;

            if (args != null)
            {
                foreach (string a in args)
                {
                    string lower = a.ToLowerInvariant().Trim('-', '/');
                    if (lower == "uninstall" || lower == "u") isUninstall = true;
                    if (lower == "silent" || lower == "s" || lower == "q") isSilent = true;
                }
            }

            if (isUninstall)
            {
                DoUninstall(isSilent);
                return;
            }

            if (isSilent)
            {
                DoInstall(GetDexterRoot(), true, true, true, false);
                return;
            }

            Application.Run(new InstallerForm());
        }

        public static string GetDexterRoot()
        {
            string exeDir = Path.GetDirectoryName(Process.GetCurrentProcess().MainModule.FileName);
            string current = exeDir;
            while (!string.IsNullOrEmpty(current))
            {
                if (File.Exists(Path.Combine(current, @"backend\desktop_app.py")))
                {
                    return current;
                }
                DirectoryInfo parent = Directory.GetParent(current);
                if (parent == null) break;
                current = parent.FullName;
            }
            return exeDir;
        }

        public static void CreateShortcut(string shortcutPath, string targetExe, string workingDir, string iconPath, string arguments = "")
        {
            try
            {
                string dir = Path.GetDirectoryName(shortcutPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);

                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType != null)
                {
                    dynamic shell = Activator.CreateInstance(shellType);
                    dynamic shortcut = shell.CreateShortcut(shortcutPath);
                    shortcut.TargetPath = targetExe;
                    shortcut.WorkingDirectory = workingDir;
                    shortcut.Description = "Dexter AI Super Assistant";
                    if (!string.IsNullOrEmpty(arguments)) shortcut.Arguments = arguments;
                    if (!string.IsNullOrEmpty(iconPath) && File.Exists(iconPath))
                        shortcut.IconLocation = iconPath + ",0";
                    shortcut.Save();
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("Shortcut creation error: " + ex.Message);
            }
        }

        public static void DoInstall(string installDir, bool createDesktop, bool createStartMenu, bool registerUninstall, bool autoStart)
        {
            string exePath = Path.Combine(installDir, "Dexter.exe");
            string iconPath = Path.Combine(installDir, @"assets\icon.ico");

            // 1. Desktop shortcut
            if (createDesktop)
            {
                string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string shortcutPath = Path.Combine(desktop, "Dexter AI Assistant.lnk");
                CreateShortcut(shortcutPath, exePath, installDir, iconPath);
            }

            // 2. Start Menu shortcut
            if (createStartMenu)
            {
                string startMenu = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                string buzzFolder = Path.Combine(startMenu, "Buzzcaf Media");
                string shortcutPath = Path.Combine(buzzFolder, "Dexter AI Assistant.lnk");
                CreateShortcut(shortcutPath, exePath, installDir, iconPath);
            }

            // 3. Autostart in registry
            if (autoStart)
            {
                try
                {
                    using (RegistryKey runKey = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                    {
                        if (runKey != null)
                        {
                            runKey.SetValue("DexterTray", "\"" + exePath + "\" --tray");
                        }
                    }
                }
                catch { }
            }

            // 4. Register in Windows Add/Remove Programs
            if (registerUninstall)
            {
                try
                {
                    string uninstallerPath = Path.Combine(installDir, "Uninstall-Dexter.exe");
                    string installerSelf = Process.GetCurrentProcess().MainModule.FileName;
                    if (File.Exists(installerSelf) && !installerSelf.Equals(uninstallerPath, StringComparison.OrdinalIgnoreCase))
                    {
                        try { File.Copy(installerSelf, uninstallerPath, true); } catch { }
                    }

                    string uninstallCmd = File.Exists(uninstallerPath) 
                        ? "\"" + uninstallerPath + "\" /uninstall" 
                        : "\"" + exePath + "\"";

                    using (RegistryKey appKey = Registry.CurrentUser.CreateSubKey(@"Software\Buzzcaf\Dexter"))
                    {
                        if (appKey != null)
                        {
                            appKey.SetValue("InstallDir", installDir);
                        }
                    }

                    using (RegistryKey key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Dexter"))
                    {
                        if (key != null)
                        {
                            key.SetValue("DisplayName", "Dexter AI Super Assistant");
                            key.SetValue("DisplayVersion", "1.0.0");
                            key.SetValue("Publisher", "Buzzcaf Media");
                            key.SetValue("DisplayIcon", iconPath);
                            key.SetValue("InstallLocation", installDir);
                            key.SetValue("UninstallString", uninstallCmd);
                            key.SetValue("HelpLink", "https://github.com/sudhansu0609/dexter");
                            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                        }
                    }
                }
                catch { }
            }
        }

        public static void DoUninstall(bool silent)
        {
            if (!silent)
            {
                DialogResult confirm = MessageBox.Show(
                    "Are you sure you want to uninstall Dexter AI Assistant shortcuts and registry entries?",
                    "Uninstall Dexter",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question
                );
                if (confirm != DialogResult.Yes) return;
            }

            try
            {
                // Remove Desktop shortcut
                string desktopShortcut = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                    "Dexter AI Assistant.lnk"
                );
                if (File.Exists(desktopShortcut)) File.Delete(desktopShortcut);

                // Remove Start Menu shortcut
                string startMenuFolder = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs),
                    "Buzzcaf Media"
                );
                string startMenuShortcut = Path.Combine(startMenuFolder, "Dexter AI Assistant.lnk");
                if (File.Exists(startMenuShortcut)) File.Delete(startMenuShortcut);
                if (Directory.Exists(startMenuFolder) && Directory.GetFiles(startMenuFolder).Length == 0)
                {
                    Directory.Delete(startMenuFolder);
                }

                // Remove Autostart
                using (RegistryKey runKey = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                {
                    if (runKey != null)
                    {
                        try { runKey.DeleteValue("DexterTray", false); } catch { }
                    }
                }

                // Remove Uninstall Key
                Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Dexter", false);
                Registry.CurrentUser.DeleteSubKeyTree(@"Software\Buzzcaf\Dexter", false);

                if (!silent)
                {
                    MessageBox.Show(
                        "Dexter has been successfully uninstalled from your system shortcuts and applications list.",
                        "Uninstall Complete",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information
                    );
                }
            }
            catch (Exception ex)
            {
                if (!silent)
                {
                    MessageBox.Show("Uninstall encountered an error: " + ex.Message, "Uninstall Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }
    }

    public class InstallerForm : Form
    {
        private TextBox txtInstallDir;
        private CheckBox chkDesktop;
        private CheckBox chkStartMenu;
        private CheckBox chkAutostart;
        private CheckBox chkLaunchNow;
        private Button btnInstall;
        private Button btnCancel;
        private string detectedRoot;

        public InstallerForm()
        {
            detectedRoot = Program.GetDexterRoot();
            InitializeComponent();
        }

        private void InitializeComponent()
        {
            this.Text = "Dexter AI Super Assistant Setup";
            this.Size = new Size(520, 440);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(15, 23, 42); // slate-900
            this.ForeColor = Color.FromArgb(248, 250, 252);
            this.Font = new Font("Segoe UI", 9.5F, FontStyle.Regular);

            string iconPath = Path.Combine(detectedRoot, @"assets\icon.ico");
            if (File.Exists(iconPath))
            {
                try { this.Icon = new Icon(iconPath); } catch { }
            }

            // Header Panel
            Panel pnlHeader = new Panel();
            pnlHeader.Dock = DockStyle.Top;
            pnlHeader.Height = 85;
            pnlHeader.BackColor = Color.FromArgb(9, 13, 22);

            Label lblTitle = new Label();
            lblTitle.Text = "Dexter AI Super Assistant";
            lblTitle.Font = new Font("Segoe UI", 15F, FontStyle.Bold);
            lblTitle.ForeColor = Color.White;
            lblTitle.Location = new Point(24, 16);
            lblTitle.AutoSize = true;
            pnlHeader.Controls.Add(lblTitle);

            Label lblSubtitle = new Label();
            lblSubtitle.Text = "Setup and install desktop integration on Windows";
            lblSubtitle.Font = new Font("Segoe UI", 9F, FontStyle.Regular);
            lblSubtitle.ForeColor = Color.FromArgb(148, 163, 184);
            lblSubtitle.Location = new Point(26, 48);
            lblSubtitle.AutoSize = true;
            pnlHeader.Controls.Add(lblSubtitle);

            this.Controls.Add(pnlHeader);

            // Body content
            int top = 105;

            Label lblDir = new Label();
            lblDir.Text = "Dexter Location:";
            lblDir.Location = new Point(24, top);
            lblDir.AutoSize = true;
            lblDir.ForeColor = Color.FromArgb(226, 232, 240);
            this.Controls.Add(lblDir);

            top += 25;
            txtInstallDir = new TextBox();
            txtInstallDir.Text = detectedRoot;
            txtInstallDir.Location = new Point(26, top);
            txtInstallDir.Width = 370;
            txtInstallDir.ReadOnly = true;
            txtInstallDir.BackColor = Color.FromArgb(30, 41, 59);
            txtInstallDir.ForeColor = Color.White;
            txtInstallDir.BorderStyle = BorderStyle.FixedSingle;
            this.Controls.Add(txtInstallDir);

            Button btnBrowse = new Button();
            btnBrowse.Text = "Browse...";
            btnBrowse.Location = new Point(404, top - 1);
            btnBrowse.Size = new Size(80, 26);
            btnBrowse.FlatStyle = FlatStyle.Flat;
            btnBrowse.BackColor = Color.FromArgb(51, 65, 85);
            btnBrowse.ForeColor = Color.White;
            btnBrowse.Click += (s, e) =>
            {
                using (FolderBrowserDialog fbd = new FolderBrowserDialog())
                {
                    fbd.SelectedPath = txtInstallDir.Text;
                    if (fbd.ShowDialog() == DialogResult.OK)
                    {
                        txtInstallDir.Text = fbd.SelectedPath;
                    }
                }
            };
            this.Controls.Add(btnBrowse);

            top += 45;
            Label lblOptions = new Label();
            lblOptions.Text = "Installation Tasks:";
            lblOptions.Location = new Point(24, top);
            lblOptions.AutoSize = true;
            lblOptions.Font = new Font("Segoe UI", 9.5F, FontStyle.Bold);
            lblOptions.ForeColor = Color.FromArgb(226, 232, 240);
            this.Controls.Add(lblOptions);

            top += 28;
            chkDesktop = new CheckBox();
            chkDesktop.Text = "Create a Desktop shortcut";
            chkDesktop.Checked = true;
            chkDesktop.Location = new Point(28, top);
            chkDesktop.AutoSize = true;
            chkDesktop.ForeColor = Color.FromArgb(203, 213, 225);
            this.Controls.Add(chkDesktop);

            top += 28;
            chkStartMenu = new CheckBox();
            chkStartMenu.Text = "Create Start Menu shortcut (Buzzcaf Media)";
            chkStartMenu.Checked = true;
            chkStartMenu.Location = new Point(28, top);
            chkStartMenu.AutoSize = true;
            chkStartMenu.ForeColor = Color.FromArgb(203, 213, 225);
            this.Controls.Add(chkStartMenu);

            top += 28;
            chkAutostart = new CheckBox();
            chkAutostart.Text = "Launch Dexter automatically on Windows logon (Tray mode)";
            chkAutostart.Checked = false;
            chkAutostart.Location = new Point(28, top);
            chkAutostart.AutoSize = true;
            chkAutostart.ForeColor = Color.FromArgb(203, 213, 225);
            this.Controls.Add(chkAutostart);

            top += 28;
            chkLaunchNow = new CheckBox();
            chkLaunchNow.Text = "Launch Dexter immediately after setup";
            chkLaunchNow.Checked = true;
            chkLaunchNow.Location = new Point(28, top);
            chkLaunchNow.AutoSize = true;
            chkLaunchNow.ForeColor = Color.FromArgb(129, 140, 248);
            this.Controls.Add(chkLaunchNow);

            // Bottom Buttons
            Panel pnlBottom = new Panel();
            pnlBottom.Dock = DockStyle.Bottom;
            pnlBottom.Height = 60;
            pnlBottom.BackColor = Color.FromArgb(9, 13, 22);

            btnInstall = new Button();
            btnInstall.Text = "Install";
            btnInstall.DialogResult = DialogResult.OK;
            btnInstall.Location = new Point(300, 16);
            btnInstall.Size = new Size(95, 32);
            btnInstall.FlatStyle = FlatStyle.Flat;
            btnInstall.BackColor = Color.FromArgb(79, 70, 229); // Indigo-600
            btnInstall.ForeColor = Color.White;
            btnInstall.Font = new Font("Segoe UI", 9.5F, FontStyle.Bold);
            btnInstall.Click += BtnInstall_Click;
            pnlBottom.Controls.Add(btnInstall);

            btnCancel = new Button();
            btnCancel.Text = "Cancel";
            btnCancel.DialogResult = DialogResult.Cancel;
            btnCancel.Location = new Point(405, 16);
            btnCancel.Size = new Size(80, 32);
            btnCancel.FlatStyle = FlatStyle.Flat;
            btnCancel.BackColor = Color.FromArgb(51, 65, 85);
            btnCancel.ForeColor = Color.White;
            btnCancel.Click += (s, e) => this.Close();
            pnlBottom.Controls.Add(btnCancel);

            this.Controls.Add(pnlBottom);
        }

        private void BtnInstall_Click(object sender, EventArgs e)
        {
            string targetDir = txtInstallDir.Text.Trim();
            if (!Directory.Exists(targetDir) || !File.Exists(Path.Combine(targetDir, @"backend\desktop_app.py")))
            {
                MessageBox.Show(
                    "The selected directory does not appear to contain Dexter files (backend\\desktop_app.py missing).",
                    "Invalid Directory",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
                return;
            }

            try
            {
                btnInstall.Enabled = false;
                btnCancel.Enabled = false;
                btnInstall.Text = "Installing...";
                Application.DoEvents();

                Program.DoInstall(
                    targetDir,
                    chkDesktop.Checked,
                    chkStartMenu.Checked,
                    true, // register in Add/Remove Programs
                    chkAutostart.Checked
                );

                if (chkLaunchNow.Checked)
                {
                    string exePath = Path.Combine(targetDir, "Dexter.exe");
                    if (File.Exists(exePath))
                    {
                        Process.Start(new ProcessStartInfo
                        {
                            FileName = exePath,
                            WorkingDirectory = targetDir
                        });
                    }
                }

                MessageBox.Show(
                    "Dexter AI Super Assistant has been successfully installed!\n\n" +
                    (chkDesktop.Checked ? " - Desktop shortcut created\n" : "") +
                    (chkStartMenu.Checked ? " - Start Menu entry created\n" : "") +
                    " - Windows Installed Apps registration complete\n" +
                    (chkAutostart.Checked ? " - Configured to auto-start with Windows\n" : ""),
                    "Setup Successful",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );

                this.Close();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "An error occurred during setup:\n\n" + ex.Message,
                    "Installation Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                btnInstall.Enabled = true;
                btnCancel.Enabled = true;
                btnInstall.Text = "Install";
            }
        }
    }
}
