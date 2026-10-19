def _sh_rule_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(output = out, content = "#!/usr/bin/env sh\nexit 0\n", is_executable = True)
    return [DefaultInfo(executable = out)]

_sh_rule = rule(
    implementation = _sh_rule_impl,
    executable = True,
)

def sh_binary(name, **_kwargs):
    _sh_rule(name = name)
