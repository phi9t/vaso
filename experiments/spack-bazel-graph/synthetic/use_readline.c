#include <stdio.h>
#include <stdlib.h>

#include <readline/history.h>
#include <readline/readline.h>

int main(void) {
    using_history();
    add_history("alpha");
    add_history("beta");

    HIST_ENTRY *entry = history_get(1);
    if (entry == NULL || entry->line == NULL) {
        fprintf(stderr, "missing first history entry\n");
        return 1;
    }

    printf("readline=%s history=%d first=%s editing=%d\n",
           rl_library_version, history_length, entry->line,
           rl_variable_bind("editing-mode", "emacs"));

    HIST_ENTRY *removed;
    while ((removed = remove_history(0)) != NULL) {
        free_history_entry(removed);
    }
    return 0;
}
