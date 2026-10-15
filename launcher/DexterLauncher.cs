using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace Dexter
{
    static class DexterLauncher
    {
        [STAThread]
        static int Main(string[] args)
        {
            try
            {
                string exePath = Process.GetCurrentProcess().MainModule.FileName;
                string appDir = Path.GetDirectoryName(exePath);

                // Find Dexter root directory (contains backend\desktop_app.py)
                string dexterRoot = FindDexterRoot(appDir);
                if (string.IsNullOrEmpty(dexterRoot))
                {
                    MessageBox.Show(
                        "Could not locate Dexter files (backend\\desktop_app.py).\n\n" +
                        "Looked in: " + appDir + "\n\n" +
                        "Please run Dexter.exe from within its installed directory or verify your installation.",
                        "Dexter AI Assistant",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return 1;
                }

                // Resolve pythonw interpreter
                string pythonExe = ResolvePythonInterpreter(dexterRoot);
                if (string.IsNullOrEmpty(pythonExe) || !File.Exists(pythonExe))
                {
                    MessageBox.Show(
                        "Python 3 interpreter was not found.\n\n" +
                        "Dexter requires Python (pythonw.exe). Checked:\n" +
                        " - DEXTER_PYTHON environment variable\n" +
                        " - " + Path.Combine(dexterRoot, @".venv\Scripts\pythonw.exe") + "\n" +
                        " - C:\\Python314\\pythonw.exe\n" +
                        " - pythonw.exe on PATH\n\n" +
                        "Please install Python or set DEXTER_PYTHON to your pythonw.exe path.",
                        "Dexter AI Assistant - Python Missing",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Warning
                    );
                    return 2;
                }

                // Ensure frontend dist/index.html is ready if node is available
                string distIndex = Path.Combine(dexterRoot, @"dist\index.html");
                if (!File.Exists(distIndex))
                {
                    BuildFrontendIfPossible(dexterRoot);
                }

                // Build arguments to forward
                string desktopAppPy = Path.Combine(dexterRoot, @"backend\desktop_app.py");
                string forwardedArgs = "";
                if (args != null && args.Length > 0)
                {
                    foreach (string arg in args)
                    {
                        if (arg.Contains(" "))
                            forwardedArgs += " \"" + arg + "\"";
                        else
                            forwardedArgs += " " + arg;
                    }
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = pythonExe;
                // -X utf8 ensures UTF-8 encoding for stdout/stderr and file I/O
                psi.Arguments = "-X utf8 \"" + desktopAppPy + "\"" + forwardedArgs;
                psi.WorkingDirectory = dexterRoot;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;

                Process.Start(psi);
                return 0;
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Failed to start Dexter:\n\n" + ex.Message + "\n\n" + ex.StackTrace,
                    "Dexter AI Assistant Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 3;
            }
        }

        private static string FindDexterRoot(string startDir)
        {
            string current = startDir;
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

            // Check registry or local app data if installed in a separate launcher dir
            try
            {
                using (var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Buzzcaf\Dexter"))
                {
                    if (key != null)
                    {
                        string installDir = key.GetValue("InstallDir") as string;
                        if (!string.IsNullOrEmpty(installDir) && File.Exists(Path.Combine(installDir, @"backend\desktop_app.py")))
                        {
                            return installDir;
                        }
                    }
                }
            }
            catch { }

            return null;
        }

        private static string ResolvePythonInterpreter(string root)
        {
            // 1. DEXTER_PYTHON env var
            string envPy = Environment.GetEnvironmentVariable("DEXTER_PYTHON");
            if (!string.IsNullOrEmpty(envPy) && File.Exists(envPy))
                return envPy;

            // 2. Project local venv
            string venvPy = Path.Combine(root, @".venv\Scripts\pythonw.exe");
            if (File.Exists(venvPy))
                return venvPy;

            // 3. Known system python paths
            string[] standardPaths = new string[]
            {
                @"C:\Python314\pythonw.exe",
                @"C:\Python313\pythonw.exe",
                @"C:\Python312\pythonw.exe",
                @"C:\Python311\pythonw.exe",
                @"C:\Program Files\Python314\pythonw.exe",
                @"C:\Program Files\Python313\pythonw.exe",
                @"C:\Program Files\Python312\pythonw.exe",
                @"C:\Program Files\Python311\pythonw.exe",
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python314\pythonw.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python313\pythonw.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python312\pythonw.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Python\Python311\pythonw.exe")
            };

            foreach (string p in standardPaths)
            {
                if (File.Exists(p)) return p;
            }

            // 4. PATH lookup
            string pathEnv = Environment.GetEnvironmentVariable("PATH");
            if (!string.IsNullOrEmpty(pathEnv))
            {
                foreach (string folder in pathEnv.Split(';'))
                {
                    string trimmed = folder.Trim();
                    if (string.IsNullOrEmpty(trimmed)) continue;
                    try
                    {
                        string candidate = Path.Combine(trimmed, "pythonw.exe");
                        if (File.Exists(candidate)) return candidate;
                    }
                    catch { }
                }
                // Fallback to python.exe if pythonw.exe was not found
                foreach (string folder in pathEnv.Split(';'))
                {
                    string trimmed = folder.Trim();
                    if (string.IsNullOrEmpty(trimmed)) continue;
                    try
                    {
                        string candidate = Path.Combine(trimmed, "python.exe");
                        if (File.Exists(candidate)) return candidate;
                    }
                    catch { }
                }
            }

            return null;
        }

        private static void BuildFrontendIfPossible(string root)
        {
            try
            {
                ProcessStartInfo npmPsi = new ProcessStartInfo();
                npmPsi.FileName = "cmd.exe";
                npmPsi.Arguments = "/c npm run build";
                npmPsi.WorkingDirectory = root;
                npmPsi.UseShellExecute = false;
                npmPsi.CreateNoWindow = true;
                npmPsi.WindowStyle = ProcessWindowStyle.Hidden;

                using (Process proc = Process.Start(npmPsi))
                {
                    if (proc != null)
                    {
                        proc.WaitForExit(60000);
                    }
                }
            }
            catch { }
        }
    }
}
