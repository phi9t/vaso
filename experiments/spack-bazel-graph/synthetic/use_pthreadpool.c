#include <pthreadpool.h>

#include <stdint.h>
#include <stdio.h>

struct work {
  uint32_t values[8];
};

static void fill_square_plus_one(void* arg, size_t index) {
  struct work* work = (struct work*)arg;
  work->values[index] = (uint32_t)(index * index + 1);
}

int main(void) {
  pthreadpool_t pool = pthreadpool_create(2);
  if (pool == NULL) {
    fputs("pthreadpool:create-failed\n", stderr);
    return 1;
  }

  const size_t threads = pthreadpool_get_threads_count(pool);
  struct work work = {0};
  pthreadpool_parallelize_1d(pool, fill_square_plus_one, &work, 8, 0);
  pthreadpool_destroy(pool);

  uint32_t sum = 0;
  for (size_t i = 0; i < 8; ++i) {
    sum += work.values[i];
  }

  printf("pthreadpool:%zu:%u\n", threads, sum);
  return threads == 2 && sum == 148 ? 0 : 1;
}
