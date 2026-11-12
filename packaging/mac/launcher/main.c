// Meshpool.app/Contents/MacOS/Meshpool
//
// Runs the bundled Python inside this process, so macOS sees a real app: the Dock shows the Meshpool icon and
// name, permissions (Accessibility, Screen Recording) are granted to Meshpool, and the window belongs to it.
//
// With no arguments (a launch from Finder, the Dock or `open`) it starts the desktop app:
//     python -m meshpool_harness app
// With arguments it behaves exactly like the bundled python3, so the app can also run helper processes.
#include <dlfcn.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

typedef int (*py_main_fn)(int, char **);

static void fail(const char *what) {
    fprintf(stderr, "Meshpool could not start: %s\n", what);
    exit(1);
}

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], res[PATH_MAX], home[PATH_MAX], lib[PATH_MAX], tmp[PATH_MAX * 2];
    uint32_t n = sizeof exe;
    if (_NSGetExecutablePath(exe, &n) != 0 || !realpath(exe, real)) fail("cannot find the app folder");
    char *macos = dirname(real);                                   // .../Meshpool.app/Contents/MacOS
    snprintf(tmp, sizeof tmp, "%s/../Resources", macos);
    if (!realpath(tmp, res)) fail("the app's Resources folder is missing");
    snprintf(home, sizeof home, "%s/python", res);
    snprintf(lib, sizeof lib, "%s/lib/libpython3.12.dylib", home);

    // Launched from Finder, macOS may add a -psn_ argument. Anything else means "run as python".
    int app_mode = 1;
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], "-psn_", 5) != 0) app_mode = 0;

    setenv("PYTHONHOME", home, 1);
    setenv("PYTHONUTF8", "1", 1);
    setenv("PYTHONIOENCODING", "utf-8", 0);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);   // bytecode is precompiled inside the signed app
    snprintf(tmp, sizeof tmp, "%s/meshpool", res);
    setenv("MESHPOOL_CLI", tmp, 1);
    snprintf(tmp, sizeof tmp, "%s/bin/python3", home);
    setenv("MESHPOOL_PYTHON", tmp, 1);              // the app hands this to child processes as sys.executable

    void *h = dlopen(lib, RTLD_NOW | RTLD_GLOBAL);
    if (!h) fail(dlerror());
    py_main_fn py_main = (py_main_fn)dlsym(h, "Py_BytesMain");
    if (!py_main) fail("the bundled Python is incomplete");

    if (app_mode) {
        snprintf(tmp, sizeof tmp, "%s/app", res);
        setenv("PYTHONPATH", tmp, 1);
        setenv("PYTHONNOUSERSITE", "1", 1);
        char *args[] = {argv[0], "-m", "meshpool_harness", "app", NULL};
        return py_main(4, args);
    }
    return py_main(argc, argv);
}
