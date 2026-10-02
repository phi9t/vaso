pub mod age;
pub mod autoland;
pub mod data;
pub mod logline;
pub mod outbox;
pub mod process;
pub mod rollout;
pub mod runtime;
pub mod settings;
pub mod state;
pub mod theme;
pub mod tmux;
pub mod tracker;
pub mod ui;
pub mod workers;

use std::env;
use std::error::Error;

pub type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

pub fn run_cli() -> Result<()> {
    process::install_signal_handlers()?;
    let args = runtime::Args::parse(env::args().skip(1))?;
    runtime::run(args)
}

pub fn render_once_for_stdout(args: &runtime::Args) -> Result<String> {
    let settings = settings::Settings::resolve()?;
    let mode = if args.attach {
        data::LoopMode::Attached
    } else {
        data::LoopMode::Exited("once check (no child spawned)".to_owned())
    };
    let model = data::collect_cockpit(&settings, mode, &[], true);
    Ok(ui::render_cockpit_to_string(
        &model,
        ui::terminal_width_from_env().unwrap_or(120),
        ui::terminal_height_from_env().unwrap_or(40),
        args.ascii,
        args.tab,
    )?)
}
