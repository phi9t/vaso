#include <stdio.h>
#include <string.h>

#include <libpkgconf/libpkgconf.h>

static bool err_handler(const char *msg, const pkgconf_client_t *client, void *data) {
    (void)msg;
    (void)client;
    (void)data;
    return true;
}

int main(void) {
    pkgconf_client_t client;
    pkgconf_cross_personality_t *personality = pkgconf_cross_personality_default();

    memset(&client, 0, sizeof(client));
    pkgconf_client_init(&client, err_handler, NULL, personality);
    printf("libpkgconf %s\n", LIBPKGCONF_VERSION_STR);
    pkgconf_client_deinit(&client);
    return 0;
}
