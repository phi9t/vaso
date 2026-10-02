#include <gssapi/gssapi.h>
#include <krb5.h>

#include <stdio.h>
#include <string.h>

int main(void) {
    krb5_context ctx = NULL;
    krb5_error_code code = krb5_init_context(&ctx);
    if (code != 0 || ctx == NULL) {
        fprintf(stderr, "krb5_init_context failed: %ld\n", (long)code);
        return 1;
    }

    krb5_data realm = {0};
    code = krb5_get_default_realm(ctx, &realm.data);
    if (code == 0) {
        krb5_free_default_realm(ctx, realm.data);
    }

    OM_uint32 major = 0;
    OM_uint32 minor = 0;
    gss_OID_set mechanisms = GSS_C_NO_OID_SET;
    major = gss_indicate_mechs(&minor, &mechanisms);
    if (major != GSS_S_COMPLETE) {
        fprintf(stderr, "gss_indicate_mechs failed: %u/%u\n", major, minor);
        krb5_free_context(ctx);
        return 2;
    }
    gss_release_oid_set(&minor, &mechanisms);

    krb5_free_context(ctx);
    printf("krb5:1.22.2:gssapi-ok\n");
    return 0;
}
