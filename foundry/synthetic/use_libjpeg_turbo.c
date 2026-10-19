#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

#include <jpeglib.h>
#include <turbojpeg.h>

int main(void) {
  if (JPEG_LIB_VERSION != 62) {
    fprintf(stderr, "unexpected JPEG_LIB_VERSION=%d\n", JPEG_LIB_VERSION);
    return 1;
  }
  if (TJ_NUMSAMP != 7 || TJ_NUMCS != 5) {
    fprintf(stderr, "unexpected TurboJPEG surface %d/%d\n", TJ_NUMSAMP, TJ_NUMCS);
    return 1;
  }

  tjhandle handle = tj3Init(TJINIT_COMPRESS);
  if (handle == NULL) {
    fprintf(stderr, "tj3Init failed: %s\n", tjGetErrorStr());
    return 1;
  }
  tj3Destroy(handle);

  struct jpeg_compress_struct cinfo;
  struct jpeg_error_mgr jerr;
  cinfo.err = jpeg_std_error(&jerr);
  jpeg_create_compress(&cinfo);
  jpeg_destroy_compress(&cinfo);

  printf("libjpeg-turbo:%d:samp=%d:cs=%d\n",
         JPEG_LIB_VERSION, TJ_NUMSAMP, TJ_NUMCS);
  return 0;
}
