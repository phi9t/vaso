#include <histedit.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    HistEvent ev;
    History *hist = history_init();
    if (hist == NULL) {
        fprintf(stderr, "history_init failed\n");
        return 1;
    }

    if (history(hist, &ev, H_SETSIZE, 8) == -1) {
        fprintf(stderr, "H_SETSIZE failed: %s\n", ev.str ? ev.str : "");
        history_end(hist);
        return 1;
    }
    if (history(hist, &ev, H_ENTER, "alpha") == -1 ||
        history(hist, &ev, H_ENTER, "beta") == -1) {
        fprintf(stderr, "H_ENTER failed: %s\n", ev.str ? ev.str : "");
        history_end(hist);
        return 1;
    }
    if (history(hist, &ev, H_FIRST) == -1 || ev.str == NULL ||
        strcmp(ev.str, "beta") != 0) {
        fprintf(stderr, "unexpected first history entry: %s\n",
                ev.str ? ev.str : "");
        history_end(hist);
        return 1;
    }

    printf("libedit:%d:%s\n", ev.num, ev.str);
    history_end(hist);
    return 0;
}
