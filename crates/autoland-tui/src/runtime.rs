use std::io::{self, Stdout};
use std::time::{Duration, Instant};

use crossterm::event::{self, Event, KeyCode};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;

use crate::autoland::{AutolandConfig, AutolandHandle};
use crate::data::{collect_cockpit_with_rollout_cursor, LoopMode};
use crate::logline::{classify_log_line, is_attention};
use crate::settings::Settings;
use crate::ui::{render_cockpit, CockpitTab, PendingAction, UiState};
use crate::Result;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Args {
    pub attach: bool,
    pub once: bool,
    pub ascii: bool,
    pub tab: CockpitTab,
}

impl Args {
    pub fn parse<I, S>(args: I) -> Result<Self>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut parsed = Self {
            attach: false,
            once: false,
            ascii: false,
            tab: CockpitTab::Overview,
        };
        let mut iter = args.into_iter();
        while let Some(arg) = iter.next() {
            match arg.into().as_str() {
                "--attach" => parsed.attach = true,
                "--once" => parsed.once = true,
                "--ascii" => parsed.ascii = true,
                "--tab" => {
                    let value = iter
                        .next()
                        .ok_or_else(|| format!("--tab needs 1, 2, 3, 4 or 5\n{}", usage()))?
                        .into();
                    let number = value
                        .parse::<u8>()
                        .map_err(|_| format!("invalid --tab {value:?}\n{}", usage()))?;
                    parsed.tab = CockpitTab::from_number(number)
                        .ok_or_else(|| format!("invalid --tab {value:?}\n{}", usage()))?;
                }
                "-h" | "--help" => {
                    return Err(usage().into());
                }
                other => return Err(format!("unknown argument {other:?}\n{}", usage()).into()),
            }
        }
        Ok(parsed)
    }
}

fn usage() -> &'static str {
    "usage: autoland-tui [--attach] [--once] [--ascii] [--tab 1|2|3|4|5]"
}

pub fn run(args: Args) -> Result<()> {
    if args.once {
        let text = crate::render_once_for_stdout(&args)?;
        print!("{text}");
        return Ok(());
    }

    let settings = Settings::resolve()?;
    if args.attach {
        run_terminal(settings, args.ascii, None, false)
    } else {
        let child = spawn_loop(settings.clone())?;
        run_terminal(settings, args.ascii, Some(child), true)
    }
}

fn run_terminal(
    settings: Settings,
    ascii: bool,
    child: Option<AutolandHandle>,
    spawn_allowed: bool,
) -> Result<()> {
    let mut stdout = io::stdout();
    enable_raw_mode()?;
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    let result = run_event_loop(&mut terminal, settings, ascii, child, spawn_allowed);
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    result
}

fn run_event_loop(
    terminal: &mut Terminal<CrosstermBackend<Stdout>>,
    settings: Settings,
    ascii: bool,
    child: Option<AutolandHandle>,
    spawn_allowed: bool,
) -> Result<()> {
    let mut ui_state = UiState::new(ascii);
    let mut controller = LoopController::new(child);
    let mut child_lines = Vec::<String>::new();
    let mut rollout_cursor = crate::rollout::RolloutCursor::default();
    let mut next_refresh = Instant::now();
    let mut model = collect_cockpit_with_rollout_cursor(
        &settings,
        controller.mode(),
        &child_lines,
        true,
        Some(&mut rollout_cursor),
    );
    let mut dirty = true;

    loop {
        if crate::process::termination_requested() {
            controller.stop_and_wait();
            break;
        }
        let drained = controller.drain_lines();
        if !drained.is_empty() {
            dirty = true;
        }
        for line in drained {
            let entry = classify_log_line(&line);
            if is_attention(entry.kind) {
                model.dashboard.last_attention_kind = Some(entry.kind);
                model.dashboard.last_attention = Some(entry.line.clone());
            }
            model.dashboard.events.push(entry);
            child_lines.push(line);
            if child_lines.len() > 400 {
                child_lines.drain(0..child_lines.len() - 400);
            }
            if model.dashboard.events.len() > 500 {
                model
                    .dashboard
                    .events
                    .drain(0..model.dashboard.events.len() - 500);
            }
        }
        if next_refresh <= Instant::now() {
            controller.poll_exit();
            model = collect_cockpit_with_rollout_cursor(
                &settings,
                controller.mode(),
                &child_lines,
                true,
                Some(&mut rollout_cursor),
            );
            next_refresh = Instant::now() + Duration::from_secs(2);
            dirty = true;
        }
        if dirty {
            terminal.draw(|frame| render_cockpit(frame, &model, &ui_state))?;
            dirty = false;
        }

        if event::poll(Duration::from_millis(50))? {
            match event::read()? {
                Event::Key(key) => match key.code {
                    KeyCode::Esc => {
                        ui_state.pending_action = None;
                        dirty = true;
                    }
                    KeyCode::Enter | KeyCode::Char('y') if ui_state.pending_action.is_some() => {
                        let message = perform_pending_action(&mut ui_state, &model)?;
                        if let Some(message) = message {
                            child_lines.push(message);
                        }
                        next_refresh = Instant::now();
                        dirty = true;
                    }
                    KeyCode::Enter
                        if ui_state.focus == crate::ui::FocusPane::Attention
                            && !model.attention.is_empty() =>
                    {
                        let index = ui_state
                            .attention_index
                            .min(model.attention.len().saturating_sub(1));
                        ui_state.pending_action = Some(PendingAction::AttentionDetail(index));
                        dirty = true;
                    }
                    KeyCode::Char('q') => {
                        controller.stop_and_wait();
                        break;
                    }
                    KeyCode::Char(ch @ '1'..='5') => {
                        let number = ch as u8 - b'0';
                        if let Some(tab) = CockpitTab::from_number(number) {
                            ui_state.tab = tab;
                            ui_state.pending_action = None;
                            dirty = true;
                        }
                    }
                    KeyCode::Char('s') if ui_state.tab == CockpitTab::Outbox => {
                        if let Some(index) = model
                            .outbox
                            .iter()
                            .position(|draft| draft.status == crate::outbox::DraftStatus::Draft)
                        {
                            ui_state.pending_action = Some(PendingAction::SendDraft(index));
                            dirty = true;
                        }
                    }
                    KeyCode::Char('d') if ui_state.tab == CockpitTab::Outbox => {
                        if let Some(index) = model
                            .outbox
                            .iter()
                            .position(|draft| draft.status == crate::outbox::DraftStatus::Draft)
                        {
                            ui_state.pending_action = Some(PendingAction::DropDraft(index));
                            dirty = true;
                        }
                    }
                    KeyCode::Char('x') if ui_state.tab == CockpitTab::Workers => {
                        if let Some(index) = model.workers.iter().position(|run| {
                            matches!(run.state, crate::workers::WorkerState::Running)
                        }) {
                            ui_state.pending_action = Some(PendingAction::StopWorker(index));
                            dirty = true;
                        }
                    }
                    KeyCode::Char('r') if spawn_allowed && controller.can_restart() => {
                        controller = LoopController::new(Some(spawn_loop(settings.clone())?));
                        child_lines.clear();
                        next_refresh = Instant::now();
                    }
                    KeyCode::Tab => {
                        ui_state.focus = ui_state.focus.next();
                        dirty = true;
                    }
                    KeyCode::Char('j') | KeyCode::Down => {
                        ui_state.scroll_down(1);
                        dirty = true;
                    }
                    KeyCode::Char('k') | KeyCode::Up => {
                        ui_state.scroll_up(1);
                        dirty = true;
                    }
                    KeyCode::PageDown => {
                        ui_state.scroll_down(10);
                        dirty = true;
                    }
                    KeyCode::PageUp => {
                        ui_state.scroll_up(10);
                        dirty = true;
                    }
                    KeyCode::Char('G') => {
                        ui_state.jump_bottom();
                        dirty = true;
                    }
                    KeyCode::Char('?') => {
                        ui_state.help = !ui_state.help;
                        dirty = true;
                    }
                    _ => {}
                },
                Event::Resize(_, _) => {
                    next_refresh = Instant::now();
                    dirty = true;
                }
                _ => {}
            }
        }
    }
    Ok(())
}

fn perform_pending_action(
    ui_state: &mut UiState,
    model: &crate::data::CockpitData,
) -> Result<Option<String>> {
    let Some(action) = ui_state.pending_action.take() else {
        return Ok(None);
    };
    match action {
        PendingAction::AttentionDetail(_) => Ok(None),
        PendingAction::SendDraft(index) => {
            let Some(draft) = model.outbox.get(index) else {
                return Ok(Some("OUTBOX no draft selected".to_owned()));
            };
            match crate::outbox::send_draft(
                &draft.path,
                None,
                model.dashboard.settings.follower_pane.as_deref(),
                &model.dashboard.settings.agent_root,
                &model.dashboard.settings.vaso_estate_root,
            ) {
                Ok(path) => Ok(Some(format!("OUTBOX SENT SENT {}", path.display()))),
                Err(error) => Ok(Some(format!("OUTBOX REFUSED {error}"))),
            }
        }
        PendingAction::DropDraft(index) => {
            let Some(draft) = model.outbox.get(index) else {
                return Ok(Some("OUTBOX no draft selected".to_owned()));
            };
            match crate::outbox::drop_draft(
                &draft.path,
                &model.dashboard.settings.agent_root,
                &model.dashboard.settings.vaso_estate_root,
            ) {
                Ok(path) => Ok(Some(format!("OUTBOX DROPPED DROPPED {}", path.display()))),
                Err(error) => Ok(Some(format!("OUTBOX REFUSED {error}"))),
            }
        }
        PendingAction::StopWorker(index) => {
            let Some(run) = model.workers.get(index) else {
                return Ok(Some("WORKER no run selected".to_owned()));
            };
            let result = crate::workers::stop_run(&run.path, Duration::from_secs(2))?;
            Ok(Some(match result {
                crate::workers::StopResult::NotRunning => "WORKER STOPPED NOT_RUNNING".to_owned(),
                crate::workers::StopResult::Terminated { pid } => {
                    format!("WORKER STOPPED SIGTERM sent to process group {pid}")
                }
                crate::workers::StopResult::Killed { pid } => {
                    format!("WORKER STOPPED SIGKILL sent to process group {pid}")
                }
            }))
        }
    }
}

struct LoopController {
    child: Option<AutolandHandle>,
    exit_reason: Option<String>,
}

impl LoopController {
    fn new(child: Option<AutolandHandle>) -> Self {
        Self {
            child,
            exit_reason: None,
        }
    }

    fn mode(&self) -> LoopMode {
        match (&self.child, &self.exit_reason) {
            (Some(child), _) => LoopMode::Running(child.pid()),
            (None, Some(reason)) => LoopMode::Exited(reason.clone()),
            (None, None) => LoopMode::Attached,
        }
    }

    fn drain_lines(&mut self) -> Vec<String> {
        self.child
            .as_mut()
            .map(AutolandHandle::drain_lines)
            .unwrap_or_default()
    }

    fn poll_exit(&mut self) {
        let Some(child) = &mut self.child else {
            return;
        };
        if let Some(reason) = child.poll_exit() {
            self.exit_reason = Some(reason);
            self.child = None;
        }
    }

    fn can_restart(&self) -> bool {
        self.child.is_none()
    }

    fn stop_and_wait(&mut self) {
        if let Some(mut child) = self.child.take() {
            self.exit_reason = Some(child.stop_and_wait());
        }
    }
}

fn spawn_loop(settings: Settings) -> Result<AutolandHandle> {
    let config = AutolandConfig::from_env(&settings)?;
    Ok(AutolandHandle::spawn(settings, config))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_cli_flags() {
        let args = Args::parse(["--attach", "--once", "--ascii", "--tab", "4"]).expect("args");
        assert!(args.attach);
        assert!(args.once);
        assert!(args.ascii);
        assert_eq!(args.tab, CockpitTab::Outbox);
    }
}
