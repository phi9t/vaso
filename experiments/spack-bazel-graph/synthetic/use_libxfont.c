#include <stdio.h>
#include <string.h>

typedef unsigned long Atom;

extern Atom MakeAtom(const char *string, unsigned len, int makeit);
extern int ValidAtom(Atom atom);
extern char *NameForAtom(Atom atom);

int main(void) {
    const char *name = "libxfont-native-smoke";
    Atom atom = MakeAtom(name, (unsigned)strlen(name), 1);
    const char *roundtrip = NameForAtom(atom);
    if (!ValidAtom(atom) || roundtrip == NULL || strcmp(roundtrip, name) != 0) {
        fprintf(stderr, "unexpected atom roundtrip: %lu %s\n", atom, roundtrip ? roundtrip : "(null)");
        return 1;
    }
    printf("libxfont:%lu:%s\n", atom, roundtrip);
    return 0;
}
