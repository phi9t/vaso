#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local marker="$1"
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*/+${marker}+${marker}/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix perl_data_dumper_native)"
PERL_PREFIX="$(find_native_prefix perl_native)"
MODULE_DIR="$PREFIX/lib/perl5/x86_64-linux-thread-multi"

test -f "$MODULE_DIR/Data/Dumper.pm" || { echo "missing Dumper.pm under $MODULE_DIR" >&2; exit 1; }
test -f "$MODULE_DIR/auto/Data/Dumper/Dumper.so" || { echo "missing Dumper.so under $MODULE_DIR" >&2; exit 1; }
test -f "$MODULE_DIR/auto/Data/Dumper/.packlist" || { echo "missing .packlist under $MODULE_DIR" >&2; exit 1; }
test -f "$PREFIX/lib/perl5/x86_64-linux-thread-multi/perllocal.pod" || { echo "missing perllocal.pod under $PREFIX" >&2; exit 1; }

export PERL5LIB="$MODULE_DIR"
export LD_LIBRARY_PATH="$MODULE_DIR/auto/Data/Dumper:$PERL_PREFIX/lib:$PERL_PREFIX/lib/5.42.0/x86_64-linux-thread-multi/CORE${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

loaded="$("$PERL_PREFIX/bin/perl" -MData::Dumper -e 'print $INC{"Data/Dumper.pm"}')"
[[ "$loaded" == "$MODULE_DIR/Data/Dumper.pm" ]] || { echo "loaded $loaded, expected $MODULE_DIR/Data/Dumper.pm" >&2; exit 1; }

version="$("$PERL_PREFIX/bin/perl" -MData::Dumper -e 'print $Data::Dumper::VERSION, "\n";')"
[[ "$version" == "2.173" ]] || { echo "Data::Dumper version $version, expected 2.173" >&2; exit 1; }
dump="$("$PERL_PREFIX/bin/perl" -MData::Dumper -e 'print Dumper([1, 2, "x"]);')"
grep -q '\$VAR1' <<<"$dump" || { echo "unexpected Dumper output: $dump" >&2; exit 1; }

echo "perl-data-dumper:2.173:ok"
