#include <sqlite3.h>
#include <stdio.h>
#include <string.h>

static int fail(sqlite3 *db, const char *context) {
    fprintf(stderr, "%s: %s\n", context, sqlite3_errmsg(db));
    return 1;
}

int main(void) {
    sqlite3 *db = NULL;
    if (sqlite3_open(":memory:", &db) != SQLITE_OK) {
        fprintf(stderr, "sqlite3_open failed\n");
        return 1;
    }

    const char *sql =
        "create table base(id integer primary key, name text);"
        "insert into base(name) values('vaso');"
        "create virtual table docs using fts5(body);"
        "insert into docs(body) values('hermetic sqlite fts');"
        "create virtual table boxes using rtree(id, x1, x2, y1, y2);"
        "insert into boxes values(1, 0, 1, 0, 1);";
    if (sqlite3_exec(db, sql, NULL, NULL, NULL) != SQLITE_OK) {
        return fail(db, "sqlite setup failed");
    }

    sqlite3_stmt *stmt = NULL;
    if (sqlite3_prepare_v2(
            db,
            "select name from base where id = 1",
            -1,
            &stmt,
            NULL) != SQLITE_OK) {
        return fail(db, "sqlite prepare failed");
    }
    if (sqlite3_step(stmt) != SQLITE_ROW) {
        sqlite3_finalize(stmt);
        return fail(db, "sqlite step failed");
    }
    const unsigned char *name = sqlite3_column_text(stmt, 0);
    int ok = name != NULL && strcmp((const char *)name, "vaso") == 0;
    sqlite3_finalize(stmt);
    sqlite3_close(db);

    if (!ok) {
        fprintf(stderr, "sqlite query mismatch\n");
        return 1;
    }

    printf(
        "sqlite=%s column_metadata=%d fts5=%d rtree=%d ok=1\n",
        sqlite3_libversion(),
        sqlite3_compileoption_used("ENABLE_COLUMN_METADATA"),
        sqlite3_compileoption_used("ENABLE_FTS5"),
        sqlite3_compileoption_used("ENABLE_RTREE"));
    return 0;
}
