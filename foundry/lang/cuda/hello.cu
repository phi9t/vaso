#include <cstdio>
__global__ void fill(int *out, int val) { out[threadIdx.x] = val + threadIdx.x; }
int main() {
    const int n = 8; int host[n]; int *dev = nullptr;
    cudaError_t e = cudaMalloc(&dev, n * sizeof(int));
    if (e != cudaSuccess) { printf("{\"lang\":\"cuda\",\"ok\":false,\"stage\":\"malloc\",\"err\":\"%s\"}\n", cudaGetErrorString(e)); return 1; }
    fill<<<1, n>>>(dev, 42);
    e = cudaGetLastError();
    if (e != cudaSuccess) { printf("{\"lang\":\"cuda\",\"ok\":false,\"stage\":\"launch\",\"err\":\"%s\"}\n", cudaGetErrorString(e)); return 1; }
    e = cudaDeviceSynchronize();
    if (e != cudaSuccess) { printf("{\"lang\":\"cuda\",\"ok\":false,\"stage\":\"sync\",\"err\":\"%s\"}\n", cudaGetErrorString(e)); return 1; }
    cudaMemcpy(host, dev, n * sizeof(int), cudaMemcpyDeviceToHost);
    cudaFree(dev);
    bool ok = host[0] == 42 && host[n-1] == 42 + n - 1;
    printf("{\"lang\":\"cuda\",\"first\":%d,\"last\":%d,\"ok\":%s}\n", host[0], host[n-1], ok?"true":"false");
    return ok?0:1;
}
