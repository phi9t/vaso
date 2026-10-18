#include <Python.h>

#include <stdio.h>

int main(void) {
    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.site_import = 0;

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    PyObject *sys = PyImport_ImportModule("sys");
    if (sys == NULL) {
        PyErr_Print();
        Py_Finalize();
        return 1;
    }
    PyObject *version_info = PyObject_GetAttrString(sys, "version_info");
    Py_DECREF(sys);
    if (version_info == NULL) {
        PyErr_Print();
        Py_Finalize();
        return 1;
    }
    long major = PyLong_AsLong(PyTuple_GetItem(version_info, 0));
    long minor = PyLong_AsLong(PyTuple_GetItem(version_info, 1));
    long micro = PyLong_AsLong(PyTuple_GetItem(version_info, 2));
    Py_DECREF(version_info);
    if (PyErr_Occurred()) {
        PyErr_Print();
        Py_Finalize();
        return 1;
    }

    printf("%ld.%ld.%ld\n", major, minor, micro);
    Py_Finalize();
    return major == 3 && minor == 13 && micro == 13 ? 0 : 1;
}
