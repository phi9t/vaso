#include <mpi.h>

#include <stdio.h>

int main(void) {
    int version = 0;
    int subversion = 0;
    MPI_Get_version(&version, &subversion);
    printf("openmpi:mpi-%d.%d\n", version, subversion);
    return (version == 3 && subversion == 1) ? 0 : 1;
}
