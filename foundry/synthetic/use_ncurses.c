#include <stdio.h>
#include <curses.h>
#include <term.h>

int main(void) {
  int errret = 0;
  if (setupterm("xterm-256color", 1, &errret) != OK) {
    fprintf(stderr, "setupterm failed: %d\n", errret);
    return 1;
  }

  char *clear = tigetstr("clear");
  int colors = tigetnum("colors");
  if (clear == (char *)-1 || clear == NULL || colors < 8) {
    fprintf(stderr, "terminfo lookup failed\n");
    return 1;
  }

  printf("ncurses colors=%d clear0=%d\n", colors, (unsigned char)clear[0]);
  del_curterm(cur_term);
  return 0;
}
