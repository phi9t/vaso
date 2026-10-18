#include <hwloc.h>

#include <stdio.h>

int main(void) {
  hwloc_topology_t topology;
  if (hwloc_topology_init(&topology) != 0) {
    fprintf(stderr, "hwloc_topology_init failed\n");
    return 1;
  }
  if (hwloc_topology_set_synthetic(topology, "pack:1 core:2 pu:1") != 0) {
    fprintf(stderr, "hwloc_topology_set_synthetic failed\n");
    hwloc_topology_destroy(topology);
    return 2;
  }
  if (hwloc_topology_load(topology) != 0) {
    fprintf(stderr, "hwloc_topology_load failed\n");
    hwloc_topology_destroy(topology);
    return 3;
  }

  int cores = hwloc_get_nbobjs_by_type(topology, HWLOC_OBJ_CORE);
  int pus = hwloc_get_nbobjs_by_type(topology, HWLOC_OBJ_PU);
  int depth = hwloc_topology_get_depth(topology);
  printf("hwloc:api=0x%08x:depth=%d:cores=%d:pus=%d\n",
         HWLOC_API_VERSION, depth, cores, pus);

  hwloc_topology_destroy(topology);
  return cores == 2 && pus == 2 ? 0 : 4;
}
