// Usage: autoland-tui [--attach] [--once] [--ascii] [--tab 1|2|3|4|5]
// Runs the autoland loop by default, or attaches to an existing loop with
// --attach. Use --once for a headless single-frame plain-text render.

fn main() {
    if let Err(error) = autoland_tui::run_cli() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
