use std::env;
use std::io;

use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::symbols;
use ratatui::text::{Line, Span};
use ratatui::widgets::{
    Block, BorderType, Borders, LineGauge, List, ListItem, Paragraph, Widget, Wrap,
};
use ratatui::{Frame, Terminal, TerminalOptions, Viewport};

use crate::age::{format_age, format_duration, system_time_from_epoch};
use crate::data::{
    AttentionItem, CockpitData, CommitInfo, DashboardData, FollowerStatus, LoopMode,
};
use crate::logline::LogKind;
use crate::outbox::{Draft, DraftStatus};
use crate::theme::Theme;
use crate::tmux::{AnsiColor, PaneCapture, PaneStatus, TodoState};
use crate::tracker::follower_log_age;
use crate::workers::{WorkerRun, WorkerState};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FocusPane {
    Attention,
    Events,
    Messages,
    Commits,
}

impl FocusPane {
    pub const fn next(self) -> Self {
        match self {
            Self::Attention => Self::Events,
            Self::Events => Self::Messages,
            Self::Messages => Self::Commits,
            Self::Commits => Self::Attention,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Breakpoint {
    Narrow,
    Medium,
    Wide,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CockpitTab {
    Overview,
    Trae,
    Workers,
    Outbox,
    Loop,
}

impl CockpitTab {
    pub const ALL: [Self; 5] = [
        Self::Overview,
        Self::Trae,
        Self::Workers,
        Self::Outbox,
        Self::Loop,
    ];

    pub const fn number(self) -> u8 {
        match self {
            Self::Overview => 1,
            Self::Trae => 2,
            Self::Workers => 3,
            Self::Outbox => 4,
            Self::Loop => 5,
        }
    }

    pub const fn title(self) -> &'static str {
        match self {
            Self::Overview => "Overview",
            Self::Trae => "TRAE",
            Self::Workers => "Workers",
            Self::Outbox => "Outbox",
            Self::Loop => "Loop",
        }
    }

    pub const fn from_number(number: u8) -> Option<Self> {
        match number {
            1 => Some(Self::Overview),
            2 => Some(Self::Trae),
            3 => Some(Self::Workers),
            4 => Some(Self::Outbox),
            5 => Some(Self::Loop),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PendingAction {
    AttentionDetail(usize),
    SendDraft(usize),
    DropDraft(usize),
    StopWorker(usize),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UiState {
    pub tab: CockpitTab,
    pub pending_action: Option<PendingAction>,
    pub focus: FocusPane,
    pub attention_index: usize,
    pub events_scroll: usize,
    pub messages_scroll: usize,
    pub commits_scroll: usize,
    pub autoscroll_events: bool,
    pub autoscroll_messages: bool,
    pub autoscroll_commits: bool,
    pub help: bool,
    pub ascii: bool,
}

impl UiState {
    pub const fn new(ascii: bool) -> Self {
        Self {
            focus: FocusPane::Events,
            tab: CockpitTab::Overview,
            pending_action: None,
            attention_index: 0,
            events_scroll: 0,
            messages_scroll: 0,
            commits_scroll: 0,
            autoscroll_events: true,
            autoscroll_messages: true,
            autoscroll_commits: true,
            help: false,
            ascii,
        }
    }

    pub fn scroll_down(&mut self, amount: usize) {
        match self.focus {
            FocusPane::Attention => {
                self.attention_index = self.attention_index.saturating_add(amount);
            }
            FocusPane::Events => {
                self.events_scroll = self.events_scroll.saturating_sub(amount);
                self.autoscroll_events = self.events_scroll == 0;
            }
            FocusPane::Messages => {
                self.messages_scroll = self.messages_scroll.saturating_sub(amount);
                self.autoscroll_messages = self.messages_scroll == 0;
            }
            FocusPane::Commits => {
                self.commits_scroll = self.commits_scroll.saturating_sub(amount);
                self.autoscroll_commits = self.commits_scroll == 0;
            }
        }
    }

    pub fn scroll_up(&mut self, amount: usize) {
        match self.focus {
            FocusPane::Attention => {
                self.attention_index = self.attention_index.saturating_sub(amount);
            }
            FocusPane::Events => {
                self.events_scroll = self.events_scroll.saturating_add(amount);
                self.autoscroll_events = false;
            }
            FocusPane::Messages => {
                self.messages_scroll = self.messages_scroll.saturating_add(amount);
                self.autoscroll_messages = false;
            }
            FocusPane::Commits => {
                self.commits_scroll = self.commits_scroll.saturating_add(amount);
                self.autoscroll_commits = false;
            }
        }
    }

    pub fn jump_bottom(&mut self) {
        match self.focus {
            FocusPane::Attention => {
                self.attention_index = 0;
            }
            FocusPane::Events => {
                self.events_scroll = 0;
                self.autoscroll_events = true;
            }
            FocusPane::Messages => {
                self.messages_scroll = 0;
                self.autoscroll_messages = true;
            }
            FocusPane::Commits => {
                self.commits_scroll = 0;
                self.autoscroll_commits = true;
            }
        }
    }
}

pub fn breakpoint(width: u16) -> Breakpoint {
    if width < 120 {
        Breakpoint::Narrow
    } else if width < 240 {
        Breakpoint::Medium
    } else {
        Breakpoint::Wide
    }
}

pub fn dashboard_columns(area: Rect) -> Vec<Rect> {
    match breakpoint(area.width) {
        Breakpoint::Narrow => vec![area],
        Breakpoint::Medium => {
            let left = area.width * 54 / 100;
            vec![
                Rect::new(area.x, area.y, left, area.height),
                Rect::new(area.x + left, area.y, area.width - left, area.height),
            ]
        }
        Breakpoint::Wide => {
            let left = area.width.min(360) * 39 / 100;
            let left = left.clamp(92, 140).min(area.width);
            let remaining = area.width.saturating_sub(left);
            let middle = remaining.saturating_mul(58) / 100;
            let middle = middle.clamp(82, 136).min(remaining);
            vec![
                Rect::new(area.x, area.y, left, area.height),
                Rect::new(area.x + left, area.y, middle, area.height),
                Rect::new(
                    area.x + left + middle,
                    area.y,
                    area.width - left - middle,
                    area.height,
                ),
            ]
        }
    }
}

pub fn render_dashboard(frame: &mut Frame<'_>, data: &DashboardData, state: &UiState) {
    let theme = Theme::detect();
    render_dashboard_with_theme(frame, data, state, theme);
}

pub fn render_dashboard_with_theme(
    frame: &mut Frame<'_>,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let area = frame.area();
    frame.render_widget(Block::default().style(Style::default().bg(theme.bg)), area);
    let header_height = 5.min(area.height.saturating_sub(3));
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(header_height),
            Constraint::Min(3),
            Constraint::Length(1),
        ])
        .split(area);
    render_header(frame, chunks[0], data, state, theme);
    render_body(frame, chunks[1], data, state, theme);
    render_footer(frame, chunks[2], state, theme);
    if state.help {
        render_help(frame, area, state, theme);
    }
}

pub fn render_cockpit(frame: &mut Frame<'_>, data: &CockpitData, state: &UiState) {
    let theme = Theme::detect();
    render_cockpit_with_theme(frame, data, state, theme);
}

pub fn render_cockpit_with_theme(
    frame: &mut Frame<'_>,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
) {
    let area = frame.area();
    frame.render_widget(Block::default().style(Style::default().bg(theme.bg)), area);
    let top_height = if area.height < 24 {
        6
    } else if area.height < 40 {
        8
    } else {
        10
    }
    .min(area.height.saturating_sub(2));
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(top_height),
            Constraint::Min(3),
            Constraint::Length(1),
        ])
        .split(area);
    render_cockpit_top(frame, chunks[0], data, state, theme);
    match state.tab {
        CockpitTab::Overview => render_overview_tab(frame, chunks[1], data, state, theme),
        CockpitTab::Trae => render_trae_tab(frame, chunks[1], data, state, theme),
        CockpitTab::Workers => render_workers_tab(frame, chunks[1], data, state, theme),
        CockpitTab::Outbox => render_outbox_tab(frame, chunks[1], data, state, theme),
        CockpitTab::Loop => render_body(frame, chunks[1], &data.dashboard, state, theme),
    }
    render_cockpit_footer(frame, chunks[2], state, theme);
    if state.help {
        render_help(frame, area, state, theme);
    }
    if state.pending_action.is_some() {
        render_confirm_modal(frame, area, data, state, theme);
    }
}

fn render_cockpit_top(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
) {
    let title = format!(
        " COCKPIT  {} {} {} ",
        data.dashboard.settings.target,
        arrow(state.ascii),
        data.dashboard.settings.landing
    );
    let block = panel_block(title, false, theme);
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Min(1),
        ])
        .split(inner);
    frame.render_widget(
        Paragraph::new(tab_line(state, theme)).wrap(Wrap { trim: true }),
        rows[0],
    );
    frame.render_widget(
        Paragraph::new(Line::from(header_spans(&data.dashboard, state, theme)))
            .style(Style::default().fg(theme.text).bg(theme.panel_dim))
            .wrap(Wrap { trim: true }),
        rows[1],
    );
    render_attention_queue(frame, rows[2], &data.attention, state, theme);
}

fn tab_line(state: &UiState, theme: Theme) -> Line<'static> {
    let mut spans = Vec::new();
    for tab in CockpitTab::ALL {
        if !spans.is_empty() {
            spans.push(Span::raw(" "));
        }
        let label = format!(" {} {} ", tab.number(), tab.title());
        let style = if tab == state.tab {
            Style::default()
                .fg(if theme.truecolor {
                    theme.bg
                } else {
                    theme.text
                })
                .bg(theme.accent)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(theme.muted).bg(theme.panel_dim)
        };
        spans.push(Span::styled(label, style));
    }
    Line::from(spans)
}

fn render_attention_queue(
    frame: &mut Frame<'_>,
    area: Rect,
    items: &[AttentionItem],
    state: &UiState,
    theme: Theme,
) {
    let mut lines = vec![Line::from(vec![
        Span::styled(
            " Attention Queue ",
            Style::default()
                .fg(theme.bg)
                .bg(theme.yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(" "),
    ])];
    let visible_count = area.height.saturating_sub(1) as usize;
    for (index, item) in items.iter().take(visible_count).enumerate() {
        let selected = state.focus == FocusPane::Attention && index == state.attention_index;
        let bg = if selected {
            theme.panel
        } else {
            theme.panel_dim
        };
        lines.push(Line::from(vec![
            Span::styled(
                format!("{} ", item.label),
                Style::default()
                    .fg(attention_item_color(item, theme))
                    .bg(bg)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                cap_text(&item.summary),
                Style::default().fg(theme.text).bg(bg),
            ),
        ]));
    }
    frame.render_widget(
        Paragraph::new(lines).style(Style::default().fg(theme.text).bg(theme.panel_dim)),
        area,
    );
}

fn attention_item_color(item: &AttentionItem, theme: Theme) -> ratatui::style::Color {
    match item.label.as_str() {
        "RED" | "REWRITTEN" | "LOOP NOT RUNNING" | "STALE" | "IDLE" => theme.red,
        "UNCOMMITTED_AGE" if item.summary.contains("(RED)") => theme.red,
        "UNCOMMITTED_AGE" => theme.yellow,
        "STALL" | "IO" | "BACKLOG" | "BUSY" | "OUTBOX" | "DECISION" | "COMPACTED"
        | "CONTEXT LOW" => theme.yellow,
        "REVIEW" | "TRACKER" => theme.magenta,
        "GONE" => theme.yellow,
        "OK" => theme.green,
        _ => theme.cyan,
    }
}

fn render_overview_tab(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
) {
    match breakpoint(area.width) {
        Breakpoint::Narrow => {
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Percentage(34),
                    Constraint::Percentage(30),
                    Constraint::Percentage(18),
                    Constraint::Percentage(18),
                ])
                .split(area);
            render_trae_session(frame, rows[0], data, state, theme, true);
            render_events(frame, rows[1], &data.dashboard, state, theme);
            render_workers_summary(frame, rows[2], data, theme);
            render_outbox_summary(frame, rows[3], data, theme);
        }
        Breakpoint::Medium => {
            let cols = dashboard_columns(area);
            render_trae_session(frame, cols[0], data, state, theme, true);
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Percentage(46),
                    Constraint::Percentage(27),
                    Constraint::Percentage(27),
                ])
                .split(cols[1]);
            render_events(frame, rows[0], &data.dashboard, state, theme);
            render_workers_summary(frame, rows[1], data, theme);
            render_outbox_summary(frame, rows[2], data, theme);
        }
        Breakpoint::Wide => {
            let cols = dashboard_columns(area);
            render_trae_session(frame, cols[0], data, state, theme, true);
            render_events(frame, cols[1], &data.dashboard, state, theme);
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Percentage(34),
                    Constraint::Percentage(26),
                    Constraint::Percentage(40),
                ])
                .split(cols[2]);
            render_workers_summary(frame, rows[0], data, theme);
            render_outbox_summary(frame, rows[1], data, theme);
            render_stats(frame, rows[2], &data.dashboard, state, theme);
        }
    }
}

fn render_trae_tab(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
) {
    match breakpoint(area.width) {
        Breakpoint::Wide | Breakpoint::Medium => {
            let left = area.width.saturating_mul(68) / 100;
            let cols = [
                Rect::new(area.x, area.y, left, area.height),
                Rect::new(area.x + left, area.y, area.width - left, area.height),
            ];
            render_trae_session(frame, cols[0], data, state, theme, false);
            let right = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
                .split(cols[1]);
            render_todo_panel(frame, right[0], data, theme);
            render_pane_mirror(frame, right[1], data, state, theme, 80);
        }
        Breakpoint::Narrow => {
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Percentage(48),
                    Constraint::Percentage(24),
                    Constraint::Percentage(28),
                ])
                .split(area);
            render_trae_session(frame, rows[0], data, state, theme, false);
            render_todo_panel(frame, rows[1], data, theme);
            render_pane_mirror(frame, rows[2], data, state, theme, 40);
        }
    }
}

fn render_workers_tab(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    _state: &UiState,
    theme: Theme,
) {
    match breakpoint(area.width) {
        Breakpoint::Narrow => {
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Percentage(55), Constraint::Percentage(45)])
                .split(area);
            render_workers_list(frame, rows[0], &data.workers, theme);
            render_worker_digest(frame, rows[1], data.workers.first(), theme);
        }
        _ => {
            let left = area.width.saturating_mul(42) / 100;
            let cols = [
                Rect::new(area.x, area.y, left, area.height),
                Rect::new(area.x + left, area.y, area.width - left, area.height),
            ];
            render_workers_list(frame, cols[0], &data.workers, theme);
            render_worker_digest(frame, cols[1], data.workers.first(), theme);
        }
    }
}

fn render_outbox_tab(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    _state: &UiState,
    theme: Theme,
) {
    match breakpoint(area.width) {
        Breakpoint::Narrow => {
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
                .split(area);
            render_outbox_list(frame, rows[0], &data.outbox, theme);
            render_outbox_preview(frame, rows[1], data.outbox.first(), theme);
        }
        _ => {
            let left = area.width.saturating_mul(36) / 100;
            let cols = [
                Rect::new(area.x, area.y, left, area.height),
                Rect::new(area.x + left, area.y, area.width - left, area.height),
            ];
            render_outbox_list(frame, cols[0], &data.outbox, theme);
            render_outbox_preview(frame, cols[1], data.outbox.first(), theme);
        }
    }
}

fn render_pane_mirror(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
    max_lines: usize,
) {
    let status = data
        .pane
        .as_ref()
        .map(|pane| pane_status_label(pane.status))
        .unwrap_or("NO PANE");
    let title = format!(" Pane Mirror  {status} ");
    render_pane_panel(frame, area, data, state, theme, max_lines, title);
}

fn render_pane_panel(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
    max_lines: usize,
    title: String,
) {
    let lines = pane_render_lines(data.pane.as_ref(), max_lines, theme);
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(title, state.tab == CockpitTab::Trae, theme))
            .wrap(Wrap { trim: false })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn render_trae_session(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
    compact: bool,
) {
    let width = area.width.saturating_sub(4).min(140) as usize;
    let lines = trae_session_lines(&data.dashboard, width, compact, theme);
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(
                " TRAE Session / TRAE Mirror ",
                state.tab == CockpitTab::Trae,
                theme,
            ))
            .wrap(Wrap { trim: false })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn trae_session_lines(
    data: &DashboardData,
    width: usize,
    compact: bool,
    theme: Theme,
) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    let Some(rollout) = &data.rollout else {
        lines.push(Line::styled(
            data.rollout_error
                .as_ref()
                .map(|error| format!("rollout unavailable: {error}"))
                .unwrap_or_else(|| "no rollout file discovered".to_owned()),
            Style::default().fg(theme.muted).bg(theme.panel),
        ));
        lines.push(Line::styled(
            "pane mirror remains available when FOLLOWER_PANE is set",
            Style::default().fg(theme.muted).bg(theme.panel),
        ));
        return lines;
    };
    if let Some(source) = &rollout.source {
        lines.push(Line::styled(
            cap_text(&format!("source: {}", source.display())),
            Style::default().fg(theme.muted).bg(theme.panel),
        ));
    }
    if let Some(turn) = &rollout.turn {
        let started = turn
            .started_at_ms
            .map(|ms| format_epoch_hms(ms / 1_000))
            .unwrap_or_else(|| "?".to_owned());
        let elapsed = turn
            .elapsed_ms
            .map(|ms| format_duration(ms / 1_000))
            .unwrap_or_else(|| "?".to_owned());
        lines.push(Line::from(vec![
            Span::styled("turn ", Style::default().fg(theme.muted).bg(theme.panel)),
            Span::styled(started, Style::default().fg(theme.cyan).bg(theme.panel)),
            Span::raw("  "),
            Span::styled(
                format!("elapsed {elapsed}"),
                Style::default().fg(theme.text).bg(theme.panel),
            ),
        ]));
    }
    if let Some(token) = &rollout.token {
        let used = token.context_used_percent().unwrap_or(0);
        let left = token.context_left_percent().unwrap_or(0);
        lines.push(Line::from(vec![
            Span::styled("tokens ", Style::default().fg(theme.muted).bg(theme.panel)),
            Span::styled(
                format!("context used {used}% / context left {left}%"),
                Style::default()
                    .fg(if left <= 25 {
                        theme.yellow
                    } else {
                        theme.green
                    })
                    .bg(theme.panel),
            ),
            Span::raw(format!(
                "  in={} out={}",
                token.input_tokens, token.output_tokens
            )),
        ]));
    }
    push_section_header(&mut lines, "Agent Messages", theme);
    let message_limit = if compact { 3 } else { 8 };
    for message in rollout
        .agent_messages
        .iter()
        .rev()
        .take(message_limit)
        .rev()
    {
        if let Some(timestamp) = &message.timestamp {
            lines.push(Line::styled(
                timestamp.clone(),
                Style::default().fg(theme.magenta).bg(theme.panel),
            ));
        }
        for body in wrap_text(&message.text, width, "  ") {
            lines.push(Line::styled(
                body,
                Style::default().fg(theme.text).bg(theme.panel),
            ));
        }
        lines.push(Line::raw(""));
    }
    if rollout.agent_messages.is_empty() {
        lines.push(Line::styled(
            "no recent agent messages",
            Style::default().fg(theme.muted).bg(theme.panel),
        ));
    }
    push_section_header(&mut lines, "Commands", theme);
    let command_limit = if compact { 4 } else { 12 };
    for command in rollout.commands.iter().rev().take(command_limit).rev() {
        let rc = command
            .exit_code
            .map(|code| format!("rc={code}"))
            .unwrap_or_else(|| "rc=?".to_owned());
        let color = match command.exit_code {
            Some(0) => theme.green,
            Some(_) => theme.red,
            None => theme.yellow,
        };
        let duration = command
            .duration_ms
            .map(format_millis)
            .unwrap_or_else(|| "?".to_owned());
        lines.push(Line::from(vec![
            Span::styled(
                format!("{rc:<5} "),
                Style::default().fg(color).bg(theme.panel),
            ),
            Span::styled(
                format!("{duration:>6} "),
                Style::default().fg(theme.muted).bg(theme.panel),
            ),
            Span::styled(
                cap_text(&command.command),
                Style::default().fg(theme.text).bg(theme.panel),
            ),
        ]));
    }
    push_section_header(&mut lines, "File Changes", theme);
    let change_limit = if compact { 4 } else { 12 };
    for change in rollout.file_changes.iter().rev().take(change_limit).rev() {
        lines.push(Line::from(vec![
            Span::styled(
                format!("{:<7} ", change.change_type),
                Style::default().fg(theme.cyan).bg(theme.panel),
            ),
            Span::styled(
                short_path(&change.path),
                Style::default().fg(theme.text).bg(theme.panel),
            ),
        ]));
    }
    if !rollout.compactions.is_empty() {
        push_section_header(&mut lines, "Compactions", theme);
        for compaction in rollout.compactions.iter().rev().take(3).rev() {
            lines.push(Line::styled(
                format!(
                    "{} {}",
                    compaction.timestamp.as_deref().unwrap_or("recent"),
                    compaction.summary
                ),
                Style::default().fg(theme.yellow).bg(theme.panel),
            ));
        }
    }
    lines
}

fn push_section_header(lines: &mut Vec<Line<'static>>, title: &str, theme: Theme) {
    lines.push(Line::from(vec![Span::styled(
        format!("-- {title} --"),
        Style::default()
            .fg(theme.accent)
            .bg(theme.panel)
            .add_modifier(Modifier::BOLD),
    )]));
}

fn pane_render_lines(
    pane: Option<&PaneCapture>,
    max_lines: usize,
    theme: Theme,
) -> Vec<Line<'static>> {
    let Some(pane) = pane else {
        return vec![Line::styled(
            "no follower pane (FOLLOWER_PANE unset or unavailable)",
            Style::default().fg(theme.muted),
        )];
    };
    let start = pane.lines.len().saturating_sub(max_lines);
    pane.lines[start..]
        .iter()
        .map(|line| {
            if line.spans.is_empty() {
                return Line::raw("");
            }
            Line::from(
                line.spans
                    .iter()
                    .map(|span| {
                        let mut style = Style::default()
                            .fg(span.fg.map(ansi_color).unwrap_or(theme.text))
                            .bg(span.bg.map(ansi_color).unwrap_or(theme.panel));
                        if span.bold {
                            style = style.add_modifier(Modifier::BOLD);
                        }
                        if span.dim {
                            style = style.add_modifier(Modifier::DIM);
                        }
                        Span::styled(cap_text(&span.text), style)
                    })
                    .collect::<Vec<_>>(),
            )
        })
        .collect()
}

fn render_todo_panel(frame: &mut Frame<'_>, area: Rect, data: &CockpitData, theme: Theme) {
    let mut lines = Vec::new();
    if let Some(pane) = &data.pane {
        lines.push(Line::from(vec![
            Span::styled("state ", Style::default().fg(theme.muted)),
            Span::styled(
                pane_status_label(pane.status),
                Style::default()
                    .fg(status_color(pane.status, theme))
                    .add_modifier(Modifier::BOLD),
            ),
        ]));
        if let Some(prompt) = &pane.prompt_line {
            lines.push(Line::styled(
                format!("prompt: {}", cap_text(prompt)),
                Style::default().fg(theme.muted),
            ));
        }
        lines.push(Line::raw(""));
        for item in &pane.todos {
            let (symbol, color) = match item.state {
                TodoState::Active => (symbol(false, "■", "*"), theme.yellow),
                TodoState::Pending => (symbol(false, "◻", "-"), theme.muted),
            };
            lines.push(Line::from(vec![
                Span::styled(format!("{symbol} "), Style::default().fg(color)),
                Span::styled(cap_text(&item.text), Style::default().fg(theme.text)),
            ]));
        }
        if pane.todos.is_empty() {
            lines.push(Line::styled(
                "no parsed to-do items",
                Style::default().fg(theme.muted),
            ));
        }
    } else {
        lines.push(Line::styled(
            "no pane capture available",
            Style::default().fg(theme.muted),
        ));
        if let Some(error) = &data.pane_error {
            lines.push(Line::styled(
                cap_text(error),
                Style::default().fg(theme.red),
            ));
        }
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(" TRAE To-Do ", true, theme))
            .wrap(Wrap { trim: true })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn render_workers_summary(frame: &mut Frame<'_>, area: Rect, data: &CockpitData, theme: Theme) {
    let running = data
        .workers
        .iter()
        .filter(|run| run.state == WorkerState::Running)
        .count();
    let failed = data
        .workers
        .iter()
        .filter(|run| matches!(run.state, WorkerState::Exited(code) if code != 0))
        .count();
    let mut lines = vec![Line::from(vec![
        Span::styled(
            format!("{} run(s)  ", data.workers.len()),
            Style::default().fg(theme.text),
        ),
        Span::styled(
            format!("{running} running  "),
            Style::default().fg(theme.green),
        ),
        Span::styled(format!("{failed} failed"), Style::default().fg(theme.red)),
    ])];
    for run in data
        .workers
        .iter()
        .take(area.height.saturating_sub(3) as usize)
    {
        lines.push(worker_line(run, theme));
    }
    if data.workers.is_empty() {
        lines.push(Line::styled(
            "no worker runs",
            Style::default().fg(theme.muted),
        ));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(" Workers ", false, theme))
            .wrap(Wrap { trim: true })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn render_workers_list(frame: &mut Frame<'_>, area: Rect, workers: &[WorkerRun], theme: Theme) {
    let items = if workers.is_empty() {
        vec![ListItem::new(Line::styled(
            "no worker runs",
            Style::default().fg(theme.muted),
        ))]
    } else {
        workers
            .iter()
            .map(|run| ListItem::new(worker_line(run, theme)))
            .collect::<Vec<_>>()
    };
    frame.render_widget(
        List::new(items)
            .block(panel_block(" Workers ", true, theme))
            .style(Style::default().fg(theme.text).bg(theme.panel)),
        area,
    );
}

fn render_worker_digest(
    frame: &mut Frame<'_>,
    area: Rect,
    worker: Option<&WorkerRun>,
    theme: Theme,
) {
    let mut lines = Vec::new();
    if let Some(worker) = worker {
        lines.push(Line::from(vec![Span::styled(
            format!("{} / {}", worker.agent, worker.run_id),
            Style::default().fg(theme.cyan).add_modifier(Modifier::BOLD),
        )]));
        if let Some(branch) = &worker.branch {
            lines.push(Line::styled(
                format!("branch: {branch}"),
                Style::default().fg(theme.muted),
            ));
        }
        for commit in &worker.commits {
            lines.push(Line::styled(
                format!("commit: {commit}"),
                Style::default().fg(theme.green),
            ));
        }
        lines.push(Line::raw(""));
        for line in &worker.digest {
            lines.push(Line::styled(
                cap_text(line),
                Style::default().fg(theme.text),
            ));
        }
    } else {
        lines.push(Line::styled(
            "no worker selected",
            Style::default().fg(theme.muted),
        ));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(" Worker Digest ", false, theme))
            .wrap(Wrap { trim: true })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn worker_line(run: &WorkerRun, theme: Theme) -> Line<'static> {
    let (label, color) = match run.state {
        WorkerState::Running => ("RUNNING".to_owned(), theme.green),
        WorkerState::Exited(0) => ("EXITED 0".to_owned(), theme.green),
        WorkerState::Exited(code) => (format!("EXITED {code}"), theme.red),
        WorkerState::Gone => ("GONE".to_owned(), theme.yellow),
        WorkerState::Unknown => ("UNKNOWN".to_owned(), theme.muted),
    };
    let commits = run.commits.len();
    Line::from(vec![
        Span::styled(format!("{label:<9} "), Style::default().fg(color)),
        Span::styled(format!("{} ", run.agent), Style::default().fg(theme.cyan)),
        Span::styled(format!("{} ", run.run_id), Style::default().fg(theme.muted)),
        Span::styled(
            format!("{commits} commit(s)"),
            Style::default().fg(theme.text),
        ),
    ])
}

fn render_outbox_summary(frame: &mut Frame<'_>, area: Rect, data: &CockpitData, theme: Theme) {
    let pending = data
        .outbox
        .iter()
        .filter(|draft| draft.status == DraftStatus::Draft)
        .count();
    let mut lines = vec![Line::from(vec![
        Span::styled(
            format!("{pending} pending  "),
            Style::default().fg(theme.yellow),
        ),
        Span::styled(
            format!("{} total", data.outbox.len()),
            Style::default().fg(theme.text),
        ),
    ])];
    for draft in data
        .outbox
        .iter()
        .take(area.height.saturating_sub(3) as usize)
    {
        lines.push(draft_line(draft, theme));
    }
    if data.outbox.is_empty() {
        lines.push(Line::styled("no drafts", Style::default().fg(theme.muted)));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(" Outbox ", false, theme))
            .wrap(Wrap { trim: true })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn render_outbox_list(frame: &mut Frame<'_>, area: Rect, drafts: &[Draft], theme: Theme) {
    let items = if drafts.is_empty() {
        vec![ListItem::new(Line::styled(
            "no drafts",
            Style::default().fg(theme.muted),
        ))]
    } else {
        drafts
            .iter()
            .map(|draft| ListItem::new(draft_line(draft, theme)))
            .collect::<Vec<_>>()
    };
    frame.render_widget(
        List::new(items)
            .block(panel_block(" Outbox ", true, theme))
            .style(Style::default().fg(theme.text).bg(theme.panel)),
        area,
    );
}

fn render_outbox_preview(frame: &mut Frame<'_>, area: Rect, draft: Option<&Draft>, theme: Theme) {
    let mut lines = Vec::new();
    if let Some(draft) = draft {
        lines.push(Line::from(vec![
            Span::styled(
                draft.title.clone(),
                Style::default().fg(theme.cyan).add_modifier(Modifier::BOLD),
            ),
            Span::raw("  "),
            Span::styled(
                draft
                    .target_pane
                    .clone()
                    .unwrap_or_else(|| "no pane".to_owned()),
                Style::default().fg(theme.muted),
            ),
        ]));
        lines.push(Line::styled(
            format!("status: {}", draft.status_text),
            Style::default().fg(draft_status_color(draft.status, theme)),
        ));
        lines.push(Line::raw(""));
        for line in draft.body.lines() {
            lines.push(Line::styled(
                cap_text(line),
                Style::default().fg(theme.text),
            ));
        }
    } else {
        lines.push(Line::styled(
            "no draft selected",
            Style::default().fg(theme.muted),
        ));
    }
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(" Draft Preview ", false, theme))
            .wrap(Wrap { trim: true })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn draft_line(draft: &Draft, theme: Theme) -> Line<'static> {
    Line::from(vec![
        Span::styled(
            format!("{:<7} ", draft.status_text),
            Style::default().fg(draft_status_color(draft.status, theme)),
        ),
        Span::styled(draft.created.clone(), Style::default().fg(theme.muted)),
        Span::raw(" "),
        Span::styled(cap_text(&draft.title), Style::default().fg(theme.text)),
    ])
}

fn draft_status_color(status: DraftStatus, theme: Theme) -> ratatui::style::Color {
    match status {
        DraftStatus::Draft => theme.yellow,
        DraftStatus::Sent => theme.green,
        DraftStatus::Dropped => theme.muted,
        DraftStatus::Other => theme.magenta,
    }
}

fn render_confirm_modal(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &CockpitData,
    state: &UiState,
    theme: Theme,
) {
    let width = area.width.min(96);
    let height = area.height.min(20);
    let rect = Rect::new(
        area.x + area.width.saturating_sub(width) / 2,
        area.y + area.height.saturating_sub(height) / 2,
        width,
        height,
    );
    frame.render_widget(ratatui::widgets::Clear, rect);
    let (title, lines) = match state.pending_action.as_ref() {
        Some(PendingAction::AttentionDetail(index)) => (
            " Attention Detail ",
            attention_detail_lines(data.attention.get(*index), theme),
        ),
        Some(PendingAction::SendDraft(index)) => (
            " Confirm Send ",
            send_confirm_lines(data.outbox.get(*index), data.pane.as_ref(), theme),
        ),
        Some(PendingAction::DropDraft(index)) => {
            let title = data
                .outbox
                .get(*index)
                .map(|draft| draft.title.clone())
                .unwrap_or_else(|| "draft".to_owned());
            (
                " Confirm Drop ",
                vec![
                    Line::styled("Confirm Drop", Style::default().fg(theme.yellow)),
                    Line::raw(format!("Drop draft: {title}")),
                    Line::raw("y/Enter drop   Esc cancel"),
                ],
            )
        }
        Some(PendingAction::StopWorker(index)) => {
            let label = data
                .workers
                .get(*index)
                .map(|run| format!("{} {}", run.agent, run.run_id))
                .unwrap_or_else(|| "worker".to_owned());
            (
                " Confirm Stop ",
                vec![
                    Line::styled("Confirm Stop", Style::default().fg(theme.yellow)),
                    Line::raw(format!("Stop worker: {label}")),
                    Line::raw("y/Enter stop   Esc cancel"),
                ],
            )
        }
        None => (" Confirm ", Vec::new()),
    };
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(title, true, theme))
            .wrap(Wrap { trim: false })
            .style(Style::default().fg(theme.text).bg(theme.panel)),
        rect,
    );
}

fn attention_detail_lines(item: Option<&AttentionItem>, theme: Theme) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::styled("Attention Detail", Style::default().fg(theme.yellow)),
        Line::raw("Esc close"),
        Line::raw(""),
    ];
    if let Some(item) = item {
        lines.push(Line::from(vec![
            Span::styled(
                item.label.clone(),
                Style::default()
                    .fg(attention_item_color(item, theme))
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(" "),
            Span::styled(item.summary.clone(), Style::default().fg(theme.text)),
        ]));
        lines.push(Line::raw(""));
        for line in item.detail.lines() {
            lines.push(Line::raw(line.to_owned()));
        }
    } else {
        lines.push(Line::styled(
            "no attention item selected",
            Style::default().fg(theme.red),
        ));
    }
    lines
}

fn send_confirm_lines(
    draft: Option<&Draft>,
    pane: Option<&PaneCapture>,
    theme: Theme,
) -> Vec<Line<'static>> {
    let pane_text = match pane.map(|pane| pane.status) {
        Some(PaneStatus::Idle) => "Pane idle",
        Some(PaneStatus::Busy) => "Pane busy",
        Some(PaneStatus::Unknown) => "Pane unknown",
        None => "Pane unavailable",
    };
    let mut lines = vec![
        Line::styled("Confirm Send", Style::default().fg(theme.yellow)),
        Line::styled(pane_text, Style::default().fg(theme.cyan)),
        Line::raw("y/Enter send   Esc cancel"),
        Line::raw(""),
    ];
    if let Some(draft) = draft {
        lines.push(Line::styled(
            draft.title.clone(),
            Style::default().fg(theme.cyan).add_modifier(Modifier::BOLD),
        ));
        for line in draft.body.lines() {
            lines.push(Line::raw(line.to_owned()));
        }
    } else {
        lines.push(Line::styled(
            "no draft selected",
            Style::default().fg(theme.red),
        ));
    }
    lines
}

fn pane_status_label(status: PaneStatus) -> &'static str {
    match status {
        PaneStatus::Idle => "IDLE",
        PaneStatus::Busy => "BUSY",
        PaneStatus::Unknown => "UNKNOWN",
    }
}

fn status_color(status: PaneStatus, theme: Theme) -> ratatui::style::Color {
    match status {
        PaneStatus::Idle => theme.green,
        PaneStatus::Busy => theme.yellow,
        PaneStatus::Unknown => theme.muted,
    }
}

fn ansi_color(color: AnsiColor) -> ratatui::style::Color {
    match color {
        AnsiColor::Black => ratatui::style::Color::Black,
        AnsiColor::Red => ratatui::style::Color::Red,
        AnsiColor::Green => ratatui::style::Color::Green,
        AnsiColor::Yellow => ratatui::style::Color::Yellow,
        AnsiColor::Blue => ratatui::style::Color::Blue,
        AnsiColor::Magenta => ratatui::style::Color::Magenta,
        AnsiColor::Cyan => ratatui::style::Color::Cyan,
        AnsiColor::White => ratatui::style::Color::White,
        AnsiColor::BrightBlack => ratatui::style::Color::DarkGray,
        AnsiColor::BrightRed => ratatui::style::Color::LightRed,
        AnsiColor::BrightGreen => ratatui::style::Color::LightGreen,
        AnsiColor::BrightYellow => ratatui::style::Color::LightYellow,
        AnsiColor::BrightBlue => ratatui::style::Color::LightBlue,
        AnsiColor::BrightMagenta => ratatui::style::Color::LightMagenta,
        AnsiColor::BrightCyan => ratatui::style::Color::LightCyan,
        AnsiColor::BrightWhite => ratatui::style::Color::Gray,
        AnsiColor::Rgb(r, g, b) => ratatui::style::Color::Rgb(r, g, b),
    }
}

fn render_cockpit_footer(frame: &mut Frame<'_>, area: Rect, state: &UiState, theme: Theme) {
    let context = match state.tab {
        CockpitTab::Overview => "overview",
        CockpitTab::Trae => "trae mirror",
        CockpitTab::Workers => "enter digest  x stop",
        CockpitTab::Outbox => "s send  n new  e edit  d drop",
        CockpitTab::Loop => "loop control",
    };
    let text = Line::from(vec![
        key_hint("1-5", theme),
        Span::styled(
            " tabs  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("s", theme),
        Span::styled(
            " send confirm  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("q", theme),
        Span::styled(
            " quit  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("r", theme),
        Span::styled(
            " restart  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("?", theme),
        Span::styled(
            format!(" help  {context}"),
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
    ]);
    frame.render_widget(
        Paragraph::new(text)
            .style(Style::default().fg(theme.text).bg(theme.panel_dim))
            .alignment(Alignment::Left),
        area,
    );
}

fn render_body(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    match breakpoint(area.width) {
        Breakpoint::Narrow => {
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Percentage(36),
                    Constraint::Percentage(28),
                    Constraint::Percentage(20),
                    Constraint::Percentage(16),
                ])
                .split(area);
            render_events(frame, rows[0], data, state, theme);
            render_messages(frame, rows[1], data, state, theme);
            render_log_tail(frame, rows[2], data, state, theme);
            render_commits_and_stats(frame, rows[3], data, state, theme);
        }
        Breakpoint::Medium => {
            let cols = dashboard_columns(area);
            render_events(frame, cols[0], data, state, theme);
            let rows = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Percentage(42),
                    Constraint::Percentage(30),
                    Constraint::Percentage(28),
                ])
                .split(cols[1]);
            render_messages(frame, rows[0], data, state, theme);
            render_log_tail(frame, rows[1], data, state, theme);
            render_commits_and_stats(frame, rows[2], data, state, theme);
        }
        Breakpoint::Wide => {
            let cols = dashboard_columns(area);
            render_events(frame, cols[0], data, state, theme);
            let mid = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Percentage(48), Constraint::Percentage(52)])
                .split(cols[1]);
            render_messages(frame, mid[0], data, state, theme);
            render_log_tail(frame, mid[1], data, state, theme);
            render_commits_and_stats(frame, cols[2], data, state, theme);
        }
    }
}

fn render_header(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let title = if matches!(breakpoint(area.width), Breakpoint::Wide) {
        format!(
            " AUTOLAND  {} -> {} ",
            data.settings.target, data.settings.landing
        )
    } else {
        " AUTOLAND ".to_owned()
    };
    let block = panel_block(title, false, theme);
    let inner = block.inner(area);
    frame.render_widget(block, area);

    let top = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Length(1),
        ])
        .split(inner);
    frame.render_widget(
        Paragraph::new(Line::from(header_spans(data, state, theme)))
            .wrap(Wrap { trim: true })
            .style(Style::default().fg(theme.text).bg(theme.panel_dim)),
        top[0],
    );

    let gauges = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(50),
            Constraint::Length(2),
            Constraint::Percentage(50),
        ])
        .split(top[1]);
    frame.render_widget(
        LineGauge::default()
            .ratio(idle_ratio(data))
            .line_set(if state.ascii {
                symbols::line::NORMAL
            } else {
                symbols::line::THICK
            })
            .style(Style::default().fg(theme.text).bg(theme.panel_dim))
            .filled_style(
                Style::default()
                    .fg(idle_color(data, theme))
                    .bg(theme.panel_dim),
            )
            .unfilled_style(Style::default().fg(theme.muted).bg(theme.panel_dim))
            .label(idle_gauge_label(data)),
        gauges[0],
    );
    frame.render_widget(
        LineGauge::default()
            .ratio(review_ratio(data))
            .line_set(if state.ascii {
                symbols::line::NORMAL
            } else {
                symbols::line::THICK
            })
            .style(Style::default().fg(theme.text).bg(theme.panel_dim))
            .filled_style(Style::default().fg(theme.magenta).bg(theme.panel_dim))
            .unfilled_style(Style::default().fg(theme.muted).bg(theme.panel_dim))
            .label(format!("review in {}", review_label(data))),
        gauges[2],
    );
    if top[2].height > 0 {
        let (label, attention, color) = attention_status(data, theme);
        frame.render_widget(
            Paragraph::new(Line::from(vec![
                pill(&label, color, theme),
                Span::raw(" "),
                Span::styled(attention, Style::default().fg(theme.muted)),
            ])),
            top[2],
        );
    }
}

fn header_spans(data: &DashboardData, state: &UiState, theme: Theme) -> Vec<Span<'static>> {
    let (loop_label, loop_detail, loop_color) = loop_status(data, theme);
    let (heartbeat_label, heartbeat_detail, heartbeat_color) = heartbeat_status(data, theme);
    let mut spans = vec![
        pill(&loop_label, loop_color, theme),
        Span::raw(" "),
        Span::styled(loop_detail, Style::default().fg(theme.text)),
        Span::raw("  "),
        pill(&heartbeat_label, heartbeat_color, theme),
        Span::raw(" "),
        Span::styled(heartbeat_detail, heartbeat_style(data, theme)),
        Span::raw("  "),
    ];
    let (follower_label, follower_detail, color) = follower_label(data, theme);
    spans.push(pill(&follower_label, color, theme));
    spans.push(Span::raw(format!(" {follower_detail}")));
    spans.push(Span::raw("  "));
    spans.push(pill("REFS", theme.cyan, theme));
    spans.push(Span::raw(format!(
        " {} {} {} ({})",
        data.refs.target,
        arrow(state.ascii),
        data.refs.landing,
        data.refs.pending
    )));
    spans
}

fn render_events(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let height = area.height.saturating_sub(2) as usize;
    let width = area.width.saturating_sub(4).min(140) as usize;
    let lines = event_lines(data, width, theme);
    let visible = tail_window(&lines, height, state.events_scroll);
    let items: Vec<ListItem<'_>> = visible.iter().cloned().map(ListItem::new).collect();
    frame.render_widget(
        List::new(items)
            .block(panel_block(
                " Loop Events ",
                state.focus == FocusPane::Events,
                theme,
            ))
            .style(Style::default().fg(theme.text).bg(theme.panel)),
        area,
    );
}

fn event_lines(data: &DashboardData, width: usize, theme: Theme) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    let mut current_day = String::new();
    for entry in &data.events {
        if let Some(timestamp) = &entry.timestamp {
            let day = timestamp.get(0..10).unwrap_or(timestamp);
            if day != current_day {
                current_day = day.to_owned();
                lines.push(Line::from(Span::styled(
                    format!("── {current_day} ──"),
                    Style::default()
                        .fg(theme.muted)
                        .bg(theme.panel)
                        .add_modifier(Modifier::DIM),
                )));
            }
        }
        let line = event_display_line(entry, theme);
        for wrapped in wrap_spans(line, width, "  ") {
            lines.push(wrapped);
        }
    }
    lines
}

fn event_display_line(entry: &crate::logline::LogEntry, theme: Theme) -> Line<'static> {
    let style = event_style(entry.kind, theme);
    let text = if entry.kind == LogKind::Relay {
        format!("  {}", entry.line)
    } else if let Some(timestamp) = &entry.timestamp {
        let time = timestamp.get(11..19).unwrap_or(timestamp.as_str());
        let rest = entry
            .line
            .strip_prefix(timestamp)
            .map(str::trim_start)
            .unwrap_or(&entry.line);
        format!("{time} {rest}")
    } else {
        entry.line.clone()
    };
    Line::from(Span::styled(text, style))
}

fn render_messages(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let width = area.width.saturating_sub(4).min(140) as usize;
    let lines = message_lines(data, width, theme);
    let visible = window_from_top(
        lines,
        area.height.saturating_sub(2) as usize,
        state.messages_scroll,
    );
    frame.render_widget(
        Paragraph::new(visible)
            .block(panel_block(
                " Messages ",
                state.focus == FocusPane::Messages,
                theme,
            ))
            .wrap(Wrap { trim: true })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn message_lines(data: &DashboardData, width: usize, theme: Theme) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    for (index, comment) in data.comments.iter().enumerate() {
        if index > 0 {
            lines.push(Line::styled(
                subtle_rule(width),
                Style::default()
                    .fg(theme.muted)
                    .bg(theme.panel)
                    .add_modifier(Modifier::DIM),
            ));
        }
        lines.push(Line::from(vec![
            Span::styled(
                comment.timestamp.clone(),
                Style::default()
                    .fg(theme.magenta)
                    .bg(theme.panel)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw("  "),
            Span::styled(
                comment.file.clone(),
                Style::default().fg(theme.cyan).bg(theme.panel),
            ),
            Span::raw("  "),
            Span::styled(
                format!("({})", comment.author),
                Style::default().fg(theme.yellow).bg(theme.panel),
            ),
        ]));
        let mut body_lines = wrap_text(&comment.text, width, "  ");
        let truncated = body_lines.len() > 12;
        body_lines.truncate(12);
        if truncated {
            if let Some(last) = body_lines.last_mut() {
                last.push_str(" …");
            }
        }
        for body in body_lines {
            lines.push(highlight_code_spans(&body, theme));
        }
    }
    if lines.is_empty() {
        lines.push(Line::styled(
            "no tracker comments yet",
            Style::default().fg(theme.muted).bg(theme.panel),
        ));
    }
    lines
}

fn highlight_code_spans(text: &str, theme: Theme) -> Line<'static> {
    let mut spans = Vec::new();
    let mut rest = text;
    let mut code = false;
    loop {
        let Some(index) = rest.find('`') else {
            if !rest.is_empty() {
                spans.push(styled_comment_span(rest.to_owned(), code, theme));
            }
            break;
        };
        let (before, after_tick) = rest.split_at(index);
        if !before.is_empty() {
            spans.push(styled_comment_span(before.to_owned(), code, theme));
        }
        code = !code;
        rest = &after_tick[1..];
    }
    Line::from(spans)
}

fn styled_comment_span(text: String, code: bool, theme: Theme) -> Span<'static> {
    let style = if code {
        Style::default()
            .fg(theme.cyan)
            .bg(theme.panel)
            .add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(theme.text).bg(theme.panel)
    };
    Span::styled(text, style)
}

fn subtle_rule(width: usize) -> String {
    let width = width.clamp(8, 48);
    "─".repeat(width)
}

fn render_log_tail(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let mut lines = Vec::new();
    if let Some(log) = &data.follower_log {
        for line in &log.lines {
            lines.push(Line::styled(
                cap_text(line),
                Style::default().fg(theme.muted),
            ));
        }
    } else {
        lines.push(Line::styled(
            "no follower log files",
            Style::default().fg(theme.muted),
        ));
    }
    let visible = window_from_top(
        lines,
        area.height.saturating_sub(2) as usize,
        state.messages_scroll,
    );
    frame.render_widget(
        Paragraph::new(visible)
            .block(panel_block(
                log_tail_title(data),
                state.focus == FocusPane::Messages,
                theme,
            ))
            .wrap(Wrap { trim: true })
            .style(Style::default().bg(theme.panel)),
        area,
    );
}

fn log_tail_title(data: &DashboardData) -> String {
    data.follower_log
        .as_ref()
        .map(|log| {
            cap_text(&format!(
                " Follower Log Tail  {}  {} ago ",
                log.name,
                follower_log_age(data.now, log)
            ))
        })
        .unwrap_or_else(|| " Follower Log Tail ".to_owned())
}

fn render_commits_and_stats(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    if area.height >= 30 {
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Percentage(50),
                Constraint::Length(8),
                Constraint::Min(9),
            ])
            .split(area);
        render_commits(frame, rows[0], data, state, theme);
        render_stats(frame, rows[1], data, state, theme);
        render_commit_history(frame, rows[2], data, state, theme);
    } else {
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Percentage(62), Constraint::Percentage(38)])
            .split(area);
        render_commits(frame, rows[0], data, state, theme);
        render_stats(frame, rows[1], data, state, theme);
    }
}

fn render_commits(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let height = area.height.saturating_sub(2) as usize;
    let visible = window_from_top_slice(&data.commits, height, state.commits_scroll);
    let items: Vec<ListItem<'_>> = visible
        .iter()
        .map(|commit| commit_item(commit, data, theme))
        .collect();
    frame.render_widget(
        List::new(items)
            .block(panel_block(
                " Recent Target Commits ",
                state.focus == FocusPane::Commits,
                theme,
            ))
            .style(Style::default().fg(theme.text).bg(theme.panel)),
        area,
    );
}

fn render_stats(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let block = panel_block(" Stats ", state.focus == FocusPane::Commits, theme);
    let inner = block.inner(area);
    frame.render_widget(block, area);
    let text = vec![
        Line::from(vec![
            Span::styled(
                symbol(state.ascii, "✓", "+"),
                Style::default().fg(theme.green),
            ),
            Span::raw(format!(
                " lands (log): {}",
                land_count_since_first_start(data)
            )),
        ]),
        Line::from(vec![
            Span::styled(
                symbol(state.ascii, "●", "*"),
                Style::default().fg(theme.cyan),
            ),
            Span::raw(format!(" Last land: {}", last_land_age(data))),
        ]),
        Line::from(vec![
            Span::styled(
                symbol(state.ascii, "●", "*"),
                Style::default().fg(theme.blue),
            ),
            Span::raw(format!(
                " Last target commit: {}",
                last_target_commit_time(data)
            )),
        ]),
        Line::from(vec![
            Span::styled(
                symbol(state.ascii, "▲", "^"),
                Style::default().fg(theme.yellow),
            ),
            Span::raw(format!(
                " Follower commits 1h: {}",
                target_commits_last_hour(data)
            )),
        ]),
        Line::from(vec![
            Span::styled(
                symbol(state.ascii, "✗", "x"),
                Style::default().fg(theme.red),
            ),
            Span::raw(format!(" REDs: {}", red_count(data))),
        ]),
    ];
    frame.render_widget(
        Paragraph::new(text)
            .style(Style::default().fg(theme.text).bg(theme.panel))
            .wrap(Wrap { trim: true }),
        inner,
    );
}

fn render_commit_history(
    frame: &mut Frame<'_>,
    area: Rect,
    data: &DashboardData,
    state: &UiState,
    theme: Theme,
) {
    let block = panel_block(
        " Follower Commits / Hour ",
        state.focus == FocusPane::Commits,
        theme,
    );
    let inner = block.inner(area);
    frame.render_widget(block, area);
    frame.render_widget(
        HourlyCommitChart {
            values: &data.target_commits_per_hour,
            now: crate::age::unix_time(data.now),
            ascii: state.ascii,
            theme,
        },
        inner,
    );
}

fn render_footer(frame: &mut Frame<'_>, area: Rect, state: &UiState, theme: Theme) {
    let focus = match state.focus {
        FocusPane::Attention => "attention",
        FocusPane::Events => "events",
        FocusPane::Messages => "messages",
        FocusPane::Commits => "commits",
    };
    let text = Line::from(vec![
        key_hint("q", theme),
        Span::styled(
            " quit  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("r", theme),
        Span::styled(
            " restart  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("?", theme),
        Span::styled(
            " help  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("Tab", theme),
        Span::styled(
            format!(" focus:{focus}  "),
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("j/k", theme),
        Span::styled(
            " scroll  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("PgUp/PgDn", theme),
        Span::styled(
            " page  ",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
        key_hint("G", theme),
        Span::styled(
            " bottom",
            Style::default().fg(theme.text).bg(theme.panel_dim),
        ),
    ]);
    frame.render_widget(
        Paragraph::new(text)
            .style(Style::default().fg(theme.text).bg(theme.panel_dim))
            .alignment(Alignment::Left),
        area,
    );
}

fn key_hint(label: &str, theme: Theme) -> Span<'static> {
    Span::styled(
        format!(" {label} "),
        Style::default()
            .fg(if theme.truecolor {
                theme.bg
            } else {
                theme.text
            })
            .bg(theme.accent)
            .add_modifier(Modifier::BOLD),
    )
}

fn render_help(frame: &mut Frame<'_>, area: Rect, state: &UiState, theme: Theme) {
    let width = area.width.min(84);
    let height = 11.min(area.height);
    let rect = Rect::new(
        area.x + area.width.saturating_sub(width) / 2,
        area.y + area.height.saturating_sub(height) / 2,
        width,
        height,
    );
    frame.render_widget(ratatui::widgets::Clear, rect);
    let glyph = symbol(state.ascii, "⟳", "r");
    let lines = vec![
        Line::styled(
            "Keys",
            Style::default()
                .fg(theme.accent)
                .add_modifier(Modifier::BOLD),
        ),
        Line::raw("q quit and stop the child loop"),
        Line::raw("r restart after the loop exits"),
        Line::raw("tab cycle focused pane"),
        Line::raw("j/k and PgUp/PgDn scroll the focused pane"),
        Line::raw("G jump to bottom and restore autoscroll"),
        Line::raw(format!("? close help   {glyph} refreshes every 2s")),
    ];
    frame.render_widget(
        Paragraph::new(lines)
            .block(panel_block(" Help ", true, theme))
            .wrap(Wrap { trim: true })
            .style(Style::default().fg(theme.text).bg(theme.panel)),
        rect,
    );
}

fn panel_block(title: impl Into<Line<'static>>, focused: bool, theme: Theme) -> Block<'static> {
    let border = if focused { theme.accent } else { theme.muted };
    Block::default()
        .title(title)
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(Style::default().fg(border))
        .style(Style::default().fg(theme.text).bg(if focused {
            theme.panel
        } else {
            theme.panel_dim
        }))
}

fn pill(label: &str, color: ratatui::style::Color, theme: Theme) -> Span<'static> {
    Span::styled(
        format!(" {label} "),
        Style::default()
            .fg(if theme.truecolor {
                theme.bg
            } else {
                theme.text
            })
            .bg(color)
            .add_modifier(Modifier::BOLD),
    )
}

fn event_style(kind: LogKind, theme: Theme) -> Style {
    let base = Style::default().bg(theme.panel);
    let color = event_color(kind, theme);
    match kind {
        LogKind::Landed | LogKind::Red | LogKind::Rewritten => {
            base.fg(color).add_modifier(Modifier::BOLD)
        }
        LogKind::Relay => base.fg(color).add_modifier(Modifier::DIM),
        _ => base.fg(color),
    }
}

fn event_color(kind: LogKind, theme: Theme) -> ratatui::style::Color {
    match kind {
        LogKind::Landed => theme.green,
        LogKind::Moved => theme.cyan,
        LogKind::Red | LogKind::Rewritten => theme.red,
        LogKind::Stall | LogKind::Gone | LogKind::Io | LogKind::Backlog | LogKind::Uncommitted => {
            theme.yellow
        }
        LogKind::Review => theme.magenta,
        LogKind::Start | LogKind::Relay => theme.muted,
    }
}

fn attention_label(kind: LogKind) -> &'static str {
    match kind {
        LogKind::Landed => "LANDED",
        LogKind::Moved => "MOVED",
        LogKind::Red => "RED",
        LogKind::Rewritten => "REWRITTEN",
        LogKind::Stall => "STALL",
        LogKind::Gone => "GONE",
        LogKind::Io => "IO",
        LogKind::Backlog => "BACKLOG",
        LogKind::Uncommitted => "UNCOMMITTED",
        LogKind::Review => "REVIEW",
        LogKind::Start => "START",
        LogKind::Relay => "RELAY",
    }
}

fn attention_status(data: &DashboardData, theme: Theme) -> (String, String, ratatui::style::Color) {
    if loop_needs_attention(data) {
        if is_loop_not_running(data) {
            if let (Some(kind), Some(attention)) =
                (data.last_attention_kind, data.last_attention.as_ref())
            {
                return (
                    attention_label(kind).to_owned(),
                    cap_text(attention),
                    event_color(kind, theme),
                );
            }
            return (
                "LOOP NOT RUNNING".to_owned(),
                loop_not_running_detail(data),
                theme.red,
            );
        }
        if heartbeat_is_stale(data) {
            return ("STALE".to_owned(), stale_detail(data), theme.red);
        }
    }

    if data.pane_status == Some(PaneStatus::Busy) {
        return ("BUSY".to_owned(), busy_detail(data), theme.yellow);
    }
    if idle_past_limit(data) {
        return ("IDLE".to_owned(), idle_detail(data), theme.red);
    }
    if idle_past_warning(data) {
        return ("IDLE".to_owned(), idle_detail(data), theme.yellow);
    }
    (
        "OK".to_owned(),
        "current conditions nominal".to_owned(),
        theme.green,
    )
}

fn loop_needs_attention(data: &DashboardData) -> bool {
    is_loop_not_running(data) || heartbeat_is_stale(data)
}

fn is_loop_not_running(data: &DashboardData) -> bool {
    data.loop_pid_alive == Some(false) || matches!(data.mode, LoopMode::Exited(_))
}

fn loop_not_running_detail(data: &DashboardData) -> String {
    let pid = data
        .state
        .as_ref()
        .map(|state| state.pid.to_string())
        .unwrap_or_else(|| "?".to_owned());
    let heartbeat = data
        .heartbeat_age()
        .map(|age| format_duration(age.as_secs()))
        .unwrap_or_else(|| "missing".to_owned());
    format!(
        "last pid {pid}; heartbeat {heartbeat} ago; {}",
        idle_detail(data)
    )
}

fn stale_detail(data: &DashboardData) -> String {
    data.heartbeat_age()
        .map(|age| {
            format!(
                "heartbeat {} ago; {}",
                format_duration(age.as_secs()),
                idle_detail(data)
            )
        })
        .unwrap_or_else(|| format!("heartbeat missing; {}", idle_detail(data)))
}

fn idle_detail(data: &DashboardData) -> String {
    if data.pane_status == Some(PaneStatus::Busy) {
        return busy_detail(data);
    }
    format!(
        "idle {} / {}m stall limit",
        idle_label(data),
        stall_minutes(data)
    )
}

fn busy_detail(data: &DashboardData) -> String {
    data.pane_busy_elapsed
        .as_ref()
        .map(|elapsed| format!("{elapsed} TRAE turn active"))
        .unwrap_or_else(|| "TRAE turn active".to_owned())
}

fn commit_item<'a>(commit: &'a CommitInfo, data: &DashboardData, theme: Theme) -> ListItem<'a> {
    let age = format_age(data.now, system_time_from_epoch(commit.timestamp));
    ListItem::new(Line::from(vec![
        Span::styled(format!("{} ", commit.sha), Style::default().fg(theme.cyan)),
        Span::styled(format!("{age:>7} "), Style::default().fg(theme.muted)),
        Span::styled(cap_text(&commit.subject), Style::default().fg(theme.text)),
    ]))
}

fn land_count_since_first_start(data: &DashboardData) -> usize {
    let start = data
        .events
        .iter()
        .position(|entry| entry.kind == LogKind::Start)
        .unwrap_or(0);
    data.events[start..]
        .iter()
        .filter(|entry| entry.kind == LogKind::Landed)
        .count()
}

fn last_land_age(data: &DashboardData) -> String {
    data.events
        .iter()
        .rev()
        .filter(|entry| entry.kind == LogKind::Landed)
        .find_map(|entry| {
            entry
                .timestamp
                .as_deref()
                .and_then(crate::age::parse_utc_timestamp)
        })
        .map(|timestamp| format_age(data.now, system_time_from_epoch(timestamp)))
        .unwrap_or_else(|| "none".to_owned())
}

fn last_target_commit_time(data: &DashboardData) -> String {
    data.commits
        .first()
        .map(|commit| format_utc_hms(commit.timestamp))
        .unwrap_or_else(|| "none".to_owned())
}

fn format_utc_hms(timestamp: u64) -> String {
    let seconds = timestamp % 86_400;
    format!(
        "{:02}:{:02}:{:02}Z",
        seconds / 3_600,
        (seconds % 3_600) / 60,
        seconds % 60
    )
}

fn format_epoch_hms(timestamp: u64) -> String {
    format_utc_hms(timestamp)
}

fn format_millis(ms: u64) -> String {
    if ms >= 1_000 {
        format!("{:.1}s", ms as f64 / 1_000.0)
    } else {
        format!("{ms}ms")
    }
}

fn short_path(path: &str) -> String {
    let parts = path.rsplit('/').take(3).collect::<Vec<_>>();
    parts.into_iter().rev().collect::<Vec<_>>().join("/")
}

fn red_count(data: &DashboardData) -> usize {
    data.events
        .iter()
        .filter(|entry| entry.kind == LogKind::Red)
        .count()
}

fn target_commits_last_hour(data: &DashboardData) -> usize {
    let now = crate::age::unix_time(data.now);
    data.commits
        .iter()
        .filter(|commit| commit.timestamp <= now && now - commit.timestamp < 3_600)
        .count()
}

fn tail_window<T>(items: &[T], height: usize, scroll_from_bottom: usize) -> &[T] {
    if height == 0 || items.is_empty() {
        return &items[0..0];
    }
    let end = items
        .len()
        .saturating_sub(scroll_from_bottom)
        .max(height.min(items.len()));
    let start = end.saturating_sub(height);
    &items[start..end]
}

fn window_from_top_slice<T>(items: &[T], height: usize, scroll_from_top: usize) -> &[T] {
    if height == 0 || items.is_empty() {
        return &items[0..0];
    }
    let start = scroll_from_top.min(items.len().saturating_sub(1));
    let end = (start + height).min(items.len());
    &items[start..end]
}

fn wrap_spans(line: Line<'static>, width: usize, continuation_indent: &str) -> Vec<Line<'static>> {
    let text = line
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect::<Vec<_>>()
        .join("");
    if display_width(&text) <= width {
        return vec![line];
    }
    let style = line
        .spans
        .first()
        .map(|span| span.style)
        .unwrap_or_default();
    wrap_text(&text, width, continuation_indent)
        .into_iter()
        .map(|text| Line::from(Span::styled(text, style)))
        .collect()
}

fn wrap_text(text: &str, width: usize, continuation_indent: &str) -> Vec<String> {
    let width = width.max(8);
    let mut lines = Vec::new();
    for paragraph in text.split('\n') {
        if paragraph.is_empty() {
            lines.push(String::new());
            continue;
        }
        let words = paragraph.split_whitespace().collect::<Vec<_>>();
        let mut current = String::new();
        let mut current_width = width;
        for word in words {
            let separator = usize::from(!current.is_empty());
            if display_width(&current) + separator + display_width(word) <= current_width {
                if !current.is_empty() {
                    current.push(' ');
                }
                current.push_str(word);
                continue;
            }
            if !current.is_empty() {
                lines.push(current);
                current = continuation_indent.to_owned();
                current_width = width;
            }
            if display_width(&current) + display_width(word) <= current_width {
                current.push_str(word);
            } else {
                for chunk in
                    split_long_word(word, current_width.saturating_sub(display_width(&current)))
                {
                    if !current.trim().is_empty() {
                        lines.push(current);
                        current = continuation_indent.to_owned();
                    }
                    current.push_str(&chunk);
                }
            }
        }
        if !current.is_empty() {
            lines.push(current);
        }
    }
    lines
}

fn split_long_word(word: &str, width: usize) -> Vec<String> {
    let width = width.max(1);
    let mut out = Vec::new();
    let mut current = String::new();
    for ch in word.chars() {
        if current.chars().count() >= width {
            out.push(current);
            current = String::new();
        }
        current.push(ch);
    }
    if !current.is_empty() {
        out.push(current);
    }
    out
}

fn display_width(text: &str) -> usize {
    text.chars().count()
}

fn window_from_top(
    lines: Vec<Line<'static>>,
    height: usize,
    scroll_from_top: usize,
) -> Vec<Line<'static>> {
    if height == 0 || lines.is_empty() {
        return Vec::new();
    }
    let start = scroll_from_top.min(lines.len().saturating_sub(1));
    let end = (start + height).min(lines.len());
    lines[start..end].to_vec()
}

fn cap_text(text: &str) -> String {
    const MAX: usize = 140;
    let mut out = String::new();
    for ch in text.chars().take(MAX) {
        out.push(ch);
    }
    if text.chars().count() > MAX {
        out.push('…');
    }
    out
}

fn symbol(ascii: bool, unicode: &'static str, ascii_text: &'static str) -> &'static str {
    if ascii {
        ascii_text
    } else {
        unicode
    }
}

fn arrow(ascii: bool) -> &'static str {
    symbol(ascii, "→", "->")
}

fn loop_status(data: &DashboardData, theme: Theme) -> (String, String, ratatui::style::Color) {
    if data.loop_pid_alive == Some(false) {
        let pid = data
            .state
            .as_ref()
            .map(|state| format!("last pid {}", state.pid))
            .unwrap_or_else(|| "last pid unknown".to_owned());
        return ("LOOP NOT RUNNING".to_owned(), pid, theme.red);
    }
    (
        loop_label(&data.mode).to_owned(),
        loop_detail(&data.mode),
        loop_color(&data.mode, theme),
    )
}

fn heartbeat_status(data: &DashboardData, theme: Theme) -> (String, String, ratatui::style::Color) {
    let stale = heartbeat_is_stale(data);
    (
        if stale { "STALE" } else { "HEARTBEAT" }.to_owned(),
        data.heartbeat_age()
            .map(|age| format!("{} ago", format_duration(age.as_secs())))
            .unwrap_or_else(|| "missing".to_owned()),
        if stale { theme.red } else { theme.green },
    )
}

fn heartbeat_is_stale(data: &DashboardData) -> bool {
    data.heartbeat_age()
        .map(|age| age.as_secs() > data.settings.interval * 3)
        .unwrap_or(true)
}

fn loop_label(mode: &LoopMode) -> &'static str {
    match mode {
        LoopMode::Attached => "ATTACHED",
        LoopMode::Running(_) => "RUNNING",
        LoopMode::Exited(_) => "EXITED",
    }
}

fn loop_detail(mode: &LoopMode) -> String {
    match mode {
        LoopMode::Attached => "following autoland.log and autoland.state".to_owned(),
        LoopMode::Running(pid) => format!("pid {pid}"),
        LoopMode::Exited(reason) => reason.clone(),
    }
}

fn loop_color(mode: &LoopMode, theme: Theme) -> ratatui::style::Color {
    match mode {
        LoopMode::Attached => theme.blue,
        LoopMode::Running(_) => theme.green,
        LoopMode::Exited(_) => theme.yellow,
    }
}

fn heartbeat_style(data: &DashboardData, theme: Theme) -> Style {
    Style::default().fg(if heartbeat_is_stale(data) {
        theme.red
    } else {
        theme.text
    })
}

fn follower_label(data: &DashboardData, theme: Theme) -> (String, String, ratatui::style::Color) {
    if data.pane_status == Some(PaneStatus::Busy) {
        return ("BUSY".to_owned(), busy_detail(data), theme.yellow);
    }
    match &data.follower {
        FollowerStatus::Gone => ("GONE".to_owned(), "not running".to_owned(), theme.yellow),
        FollowerStatus::Unknown => ("FOLLOWER".to_owned(), "pid unknown".to_owned(), theme.muted),
        FollowerStatus::Alive { pid, busy } => {
            let detail = if busy.is_empty() {
                format!("alive pid {pid}")
            } else {
                let jobs = busy
                    .iter()
                    .map(|(name, count)| format!("{count}x{name}"))
                    .collect::<Vec<_>>()
                    .join(" ");
                format!("busy {jobs}")
            };
            ("ALIVE".to_owned(), detail, theme.green)
        }
    }
}

fn idle_ratio(data: &DashboardData) -> f64 {
    if data.pane_status == Some(PaneStatus::Busy) {
        return 0.0;
    }
    let Some(idle) = data.idle_seconds() else {
        return 0.0;
    };
    let stall = stall_minutes(data) * 60;
    if stall == 0 {
        0.0
    } else {
        (idle as f64 / stall as f64).clamp(0.0, 1.0)
    }
}

fn idle_color(data: &DashboardData, theme: Theme) -> ratatui::style::Color {
    if data.pane_status == Some(PaneStatus::Busy) {
        theme.yellow
    } else if idle_past_limit(data) {
        theme.red
    } else if idle_past_warning(data) {
        theme.yellow
    } else {
        theme.green
    }
}

fn idle_past_limit(data: &DashboardData) -> bool {
    if data.pane_status == Some(PaneStatus::Busy) {
        return false;
    }
    let Some(idle) = data.idle_seconds() else {
        return false;
    };
    let stall = stall_minutes(data) * 60;
    stall > 0 && idle >= stall
}

fn idle_past_warning(data: &DashboardData) -> bool {
    if data.pane_status == Some(PaneStatus::Busy) {
        return false;
    }
    let Some(idle) = data.idle_seconds() else {
        return false;
    };
    let stall = stall_minutes(data) * 60;
    stall > 0 && idle * 4 >= stall * 3
}

fn idle_label(data: &DashboardData) -> String {
    if data.pane_status == Some(PaneStatus::Busy) {
        return data
            .pane_busy_elapsed
            .as_ref()
            .map(|elapsed| format!("BUSY {elapsed}"))
            .unwrap_or_else(|| "BUSY".to_owned());
    }
    data.idle_seconds()
        .map(format_duration)
        .unwrap_or_else(|| "?".to_owned())
}

fn idle_gauge_label(data: &DashboardData) -> String {
    if data.pane_status == Some(PaneStatus::Busy) {
        return format!("TRAE {} ", idle_label(data));
    }
    format!("idle {} / {}m ", idle_label(data), stall_minutes(data))
}

fn stall_minutes(data: &DashboardData) -> u64 {
    data.settings.stall_min
}

fn review_ratio(data: &DashboardData) -> f64 {
    let Some(state) = &data.state else {
        return 0.0;
    };
    let total = state.batch_hours * 3_600;
    if total == 0 {
        return 0.0;
    }
    let elapsed = crate::age::unix_time(data.now).saturating_sub(state.start);
    (elapsed as f64 / total as f64).clamp(0.0, 1.0)
}

fn review_label(data: &DashboardData) -> String {
    data.review_seconds_left()
        .map(format_duration)
        .unwrap_or_else(|| "?".to_owned())
}

struct HourlyCommitChart<'a> {
    values: &'a [u64],
    now: u64,
    ascii: bool,
    theme: Theme,
}

impl Widget for HourlyCommitChart<'_> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        if area.width == 0 || area.height == 0 {
            return;
        }
        buf.set_style(
            area,
            Style::default().fg(self.theme.text).bg(self.theme.panel),
        );
        if area.width < 16 || area.height < 5 {
            write_text(
                buf,
                area.x,
                area.y,
                "need more room",
                Style::default().fg(self.theme.muted).bg(self.theme.panel),
                area.width,
            );
            return;
        }

        let max = self.values.iter().copied().max().unwrap_or(0).max(1);
        let axis_width = 6_u16.min(area.width.saturating_sub(8)).max(4);
        let plot_x = area.x + axis_width;
        let plot_width = area.width.saturating_sub(axis_width);
        let plot_height = area.height.saturating_sub(3);
        let chart_top = area.y + 1;
        let marker_y = area.y + area.height.saturating_sub(2);
        let label_y = area.y + area.height.saturating_sub(1);
        let axis_style = Style::default().fg(self.theme.muted).bg(self.theme.panel);

        write_text(
            buf,
            area.x,
            area.y,
            &format!("max {max}"),
            axis_style,
            axis_width,
        );
        write_text(
            buf,
            area.x + axis_width.saturating_sub(2),
            chart_top,
            if self.ascii { "|" } else { "│" },
            axis_style,
            1,
        );
        write_text(
            buf,
            area.x + axis_width.saturating_sub(2),
            marker_y,
            if self.ascii { "+" } else { "└" },
            axis_style,
            1,
        );
        write_text(
            buf,
            area.x,
            marker_y,
            "0",
            axis_style,
            axis_width.saturating_sub(2),
        );

        let slot_step = hour_label_step(plot_width);
        let current_hour = (self.now / 3_600) % 24;
        let values = hourly_values(self.values);
        for (index, value) in values.iter().enumerate() {
            let start = index as u16 * plot_width / 24;
            let end = (index as u16 + 1) * plot_width / 24;
            if end <= start {
                continue;
            }
            let slot_width = end - start;
            let bar_width = slot_width.saturating_sub(1).max(1);
            let bar_x = plot_x + start;
            let bar_height = if *value == 0 {
                0
            } else {
                (*value * u64::from(plot_height)).div_ceil(max)
            } as u16;
            let bar_style = Style::default()
                .fg(hour_bar_color(index, self.theme))
                .bg(self.theme.panel);
            for y_offset in 0..bar_height.min(plot_height) {
                let y = chart_top + plot_height - 1 - y_offset;
                for x_offset in 0..bar_width {
                    write_text(
                        buf,
                        bar_x + x_offset,
                        y,
                        if self.ascii { "#" } else { "█" },
                        bar_style,
                        1,
                    );
                }
            }

            let hour = bucket_hour(current_hour, index);
            if index == 23 {
                let marker = if self.ascii { "^" } else { "▲" };
                write_text(
                    buf,
                    bar_x,
                    marker_y,
                    marker,
                    Style::default().fg(self.theme.green).bg(self.theme.panel),
                    1,
                );
                let label = format!("now {hour:02}");
                write_text(
                    buf,
                    plot_x + plot_width.saturating_sub(label.chars().count() as u16),
                    label_y,
                    &label,
                    Style::default().fg(self.theme.green).bg(self.theme.panel),
                    label.chars().count() as u16,
                );
            } else if index % slot_step == 0 {
                write_text(
                    buf,
                    bar_x,
                    label_y,
                    &format!("{hour:02}"),
                    axis_style,
                    slot_width.min(2),
                );
            }
        }
    }
}

fn hourly_values(values: &[u64]) -> [u64; 24] {
    let mut out = [0_u64; 24];
    let start = values.len().saturating_sub(24);
    for (index, value) in values[start..].iter().enumerate() {
        out[24 - values[start..].len() + index] = *value;
    }
    out
}

fn hour_label_step(plot_width: u16) -> usize {
    if plot_width >= 72 {
        3
    } else {
        6
    }
}

fn bucket_hour(current_hour: u64, index: usize) -> u64 {
    (current_hour + 24 - (23 - index as u64)) % 24
}

fn hour_bar_color(index: usize, theme: Theme) -> ratatui::style::Color {
    if index >= 21 {
        theme.green
    } else if index >= 18 {
        theme.cyan
    } else if index >= 12 {
        theme.blue
    } else {
        theme.muted
    }
}

fn write_text(buf: &mut Buffer, x: u16, y: u16, text: &str, style: Style, max_width: u16) {
    for (offset, ch) in text.chars().take(max_width as usize).enumerate() {
        buf[(x + offset as u16, y)]
            .set_symbol(&ch.to_string())
            .set_style(style);
    }
}

pub fn render_to_string(
    data: &DashboardData,
    width: u16,
    height: u16,
    ascii: bool,
) -> io::Result<String> {
    let backend = TestBackend::new(width, height);
    let mut terminal = Terminal::with_options(
        backend,
        TerminalOptions {
            viewport: Viewport::Fixed(Rect::new(0, 0, width, height)),
        },
    )?;
    let ui_state = UiState::new(ascii);
    let theme = Theme::new(true);
    terminal.draw(|frame| render_dashboard_with_theme(frame, data, &ui_state, theme))?;
    Ok(buffer_to_string(terminal.backend().buffer()))
}

pub fn render_cockpit_to_string(
    data: &CockpitData,
    width: u16,
    height: u16,
    ascii: bool,
    tab: CockpitTab,
) -> io::Result<String> {
    let mut state = UiState::new(ascii);
    state.tab = tab;
    render_cockpit_state_to_string(data, width, height, &state)
}

pub fn render_cockpit_state_to_string(
    data: &CockpitData,
    width: u16,
    height: u16,
    state: &UiState,
) -> io::Result<String> {
    let backend = TestBackend::new(width, height);
    let mut terminal = Terminal::with_options(
        backend,
        TerminalOptions {
            viewport: Viewport::Fixed(Rect::new(0, 0, width, height)),
        },
    )?;
    let theme = Theme::new(true);
    terminal.draw(|frame| render_cockpit_with_theme(frame, data, state, theme))?;
    Ok(buffer_to_string(terminal.backend().buffer()))
}

pub fn buffer_to_string(buffer: &Buffer) -> String {
    let area = *buffer.area();
    let mut lines = Vec::with_capacity(area.height as usize);
    for y in area.y..area.y + area.height {
        let mut line = String::new();
        for x in area.x..area.x + area.width {
            #[allow(deprecated)]
            line.push_str(buffer.get(x, y).symbol());
        }
        lines.push(line.trim_end().to_owned());
    }
    lines.join("\n")
}

pub fn terminal_width_from_env() -> Option<u16> {
    env::var("COLUMNS").ok()?.parse().ok()
}

pub fn terminal_height_from_env() -> Option<u16> {
    env::var("LINES").ok()?.parse().ok()
}

#[cfg(test)]
mod tests {
    use ratatui::style::{Color, Modifier};

    use super::*;
    use crate::data::{sample_cockpit, sample_dashboard};

    #[test]
    fn breakpoints_have_expected_column_counts() {
        assert_eq!(dashboard_columns(Rect::new(0, 0, 100, 30)).len(), 1);
        assert_eq!(dashboard_columns(Rect::new(0, 0, 180, 50)).len(), 2);
        let wide = dashboard_columns(Rect::new(0, 0, 360, 100));
        assert_eq!(wide.len(), 3);
        assert!(wide[0].width <= 140);
        assert!(wide[1].width <= 136);
    }

    #[test]
    fn render_100x30_header_red_and_comment() {
        let text = render_to_string(&sample_dashboard(), 100, 30, false).expect("render");
        assert!(text.contains("RUNNING"), "{text}");
        assert!(text.contains("package-scoped re-seat"), "{text}");

        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(Rect::new(0, 0, 100, 30)),
            },
        )
        .expect("terminal");
        let ui_state = UiState::new(false);
        let theme = Theme::new(true);
        terminal
            .draw(|frame| render_dashboard_with_theme(frame, &sample_dashboard(), &ui_state, theme))
            .expect("draw");
        let buffer = terminal.backend().buffer();
        let found_red = buffer.content().iter().any(|cell| {
            cell.symbol() == "R"
                && cell.fg == Color::Rgb(243, 139, 168)
                && cell.modifier.contains(Modifier::BOLD)
        });
        assert!(found_red, "RED line was not styled red/bold");
    }

    #[test]
    fn header_uses_single_status_row_and_flags_stale_loop() {
        let mut data = sample_dashboard();
        data.mode = LoopMode::Attached;
        if let Some(state) = &mut data.state {
            state.beat = crate::age::unix_time(data.now) - data.settings.interval * 4;
        }

        let text = render_to_string(&data, 360, 100, false).expect("wide render");
        let header = text.lines().take(5).collect::<Vec<_>>().join("\n");

        assert_eq!(header.matches("ATTACHED").count(), 1, "{header}");
        assert_eq!(header.matches("REFS").count(), 1, "{header}");
        assert_eq!(header.matches(&data.refs.target).count(), 1, "{header}");
        assert!(header.contains("STALE"), "{header}");
        assert!(
            header
                .lines()
                .filter(|line| line.starts_with('│'))
                .all(|line| !line.trim_matches(['│', ' ']).is_empty()),
            "{header}"
        );
    }

    #[test]
    fn header_flags_dead_loop_pid_even_when_heartbeat_is_fresh() {
        let mut data = sample_dashboard();
        if let Some(state) = &mut data.state {
            state.pid = u32::MAX;
            state.beat = crate::age::unix_time(data.now);
        }
        data.loop_pid_alive = Some(false);

        let text = render_to_string(&data, 180, 50, false).expect("medium render");
        let header = text.lines().take(5).collect::<Vec<_>>().join("\n");

        assert!(header.contains("LOOP NOT RUNNING"), "{header}");
    }

    #[test]
    fn messages_render_multiline_comment_bodies_and_highlight_code_spans() {
        let mut data = sample_dashboard();
        data.comments = vec![crate::tracker::TrackerComment {
            timestamp: "2026-09-29T12:34Z".to_owned(),
            file: "08-reseat-python-bound-natives.md".to_owned(),
            author: "TRAE".to_owned(),
            text: "package-scoped re-seat is `py-pathspec`.\nVerified prefix parity and kept the proof log in FOLLOWER_LOGS.\nNext package remains queued for the same lane.".to_owned(),
        }];

        let text = render_to_string(&data, 180, 50, false).expect("medium render");
        assert!(text.contains("2026-09-29T12:34Z"), "{text}");
        assert!(text.contains("(TRAE)"), "{text}");
        assert!(text.contains("Verified prefix parity"), "{text}");
        assert!(text.contains("Next package remains queued"), "{text}");
        assert!(text.contains("─"), "{text}");

        let backend = TestBackend::new(180, 50);
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(Rect::new(0, 0, 180, 50)),
            },
        )
        .expect("terminal");
        let ui_state = UiState::new(false);
        let theme = Theme::new(true);
        terminal
            .draw(|frame| render_dashboard_with_theme(frame, &data, &ui_state, theme))
            .expect("draw");
        let buffer = terminal.backend().buffer();
        let rendered = buffer_to_string(buffer);
        let (row, col) = rendered
            .lines()
            .enumerate()
            .find_map(|(row, line)| line.find("py-pathspec").map(|col| (row as u16, col as u16)))
            .expect("code span rendered");
        let cell = &buffer[(col, row)];
        assert_eq!(cell.fg, theme.cyan, "code span should use accent color");
        assert!(
            cell.modifier.contains(Modifier::BOLD),
            "code span should be bold"
        );
    }

    #[test]
    fn wide_layout_shows_populated_stats_and_target_commit_history() {
        let data = sample_dashboard();
        let text = render_to_string(&data, 360, 100, false).expect("wide render");

        assert!(text.contains(" Stats "), "{text}");
        assert!(text.contains("lands (log)"), "{text}");
        assert!(text.contains("Last land"), "{text}");
        assert!(text.contains("Last target commit: 12:40:00Z"), "{text}");
        assert!(text.contains("REDs"), "{text}");
        assert!(text.contains("Follower commits 1h"), "{text}");
        assert!(text.contains(" Follower Commits / Hour "), "{text}");
        assert!(text.contains("max "), "{text}");
        assert!(text.contains("now 12"), "{text}");
        assert!(
            text.contains("Follower Log Tail  20260929-1234.log  5m0s ago"),
            "{text}"
        );
        assert!(
            text.matches("Commit fixture").count() >= 24,
            "wide commit panel should fill with many commits:\n{text}"
        );
    }

    #[test]
    fn attention_line_reports_dead_loop_before_idle_and_idle_gauge_turns_red() {
        let mut data = sample_dashboard();
        data.mode = LoopMode::Attached;
        data.loop_pid_alive = Some(false);
        data.last_attention_kind = None;
        data.last_attention = None;
        if let Some(state) = &mut data.state {
            state.pid = u32::MAX;
            state.beat = crate::age::unix_time(data.now);
            state.stall_min = 45;
        }
        data.last_activity = Some(crate::age::unix_time(data.now) - 57 * 60);

        let backend = TestBackend::new(360, 100);
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(Rect::new(0, 0, 360, 100)),
            },
        )
        .expect("terminal");
        let ui_state = UiState::new(false);
        let theme = Theme::new(true);
        terminal
            .draw(|frame| render_dashboard_with_theme(frame, &data, &ui_state, theme))
            .expect("draw");
        let buffer = terminal.backend().buffer();
        let text = buffer_to_string(buffer);
        let attention = text.lines().nth(3).expect("attention row");

        assert!(attention.contains("LOOP NOT RUNNING"), "{attention}");
        assert!(!attention.contains(" OK "), "{attention}");
        assert!(!attention.contains("no attention event"), "{attention}");
        assert!(text.contains("idle 57m0s / 45m"), "{text}");
        assert!(
            buffer
                .content()
                .iter()
                .any(|cell| cell.symbol() == "━" && cell.fg == theme.red),
            "idle gauge should turn red past the stall limit"
        );
    }

    #[test]
    fn idle_gauge_uses_settings_stall_limit_not_stale_state_value() {
        let mut data = sample_dashboard();
        data.last_attention_kind = None;
        data.last_attention = None;
        data.settings.stall_min = 45;
        if let Some(state) = &mut data.state {
            state.stall_min = 120;
        }
        data.last_activity = Some(crate::age::unix_time(data.now) - 57 * 60);

        let text = render_to_string(&data, 360, 100, false).expect("wide render");

        assert!(text.contains("idle 57m0s / 45m"), "{text}");
        assert!(!text.contains("/ 120m"), "{text}");
    }

    #[test]
    fn attention_line_shows_last_exit_event_when_loop_has_stopped() {
        let mut data = sample_dashboard();
        data.mode = LoopMode::Attached;
        data.loop_pid_alive = Some(false);
        if let Some(state) = &mut data.state {
            state.beat = crate::age::unix_time(data.now);
        }
        data.last_activity = Some(crate::age::unix_time(data.now) - 57 * 60);

        let text = render_to_string(&data, 360, 100, false).expect("wide render");
        let attention = text.lines().nth(3).expect("attention row");

        assert!(attention.contains(" RED "), "{attention}");
        assert!(
            attention.contains("2026-09-29T12:35:00Z RED VERIFY_FAILED"),
            "{attention}"
        );
    }

    #[test]
    fn attention_line_reports_stale_before_idle_when_loop_pid_is_alive() {
        let mut data = sample_dashboard();
        data.last_attention_kind = None;
        data.last_attention = None;
        if let Some(state) = &mut data.state {
            state.beat = crate::age::unix_time(data.now) - data.settings.interval * 4;
        }
        data.last_activity = Some(crate::age::unix_time(data.now) - 57 * 60);

        let text = render_to_string(&data, 360, 100, false).expect("wide render");
        let attention = text.lines().nth(3).expect("attention row");

        assert!(attention.contains("STALE"), "{attention}");
        assert!(!attention.contains(" OK "), "{attention}");
    }

    #[test]
    fn backlog_attention_item_is_yellow() {
        let item = AttentionItem {
            label: "BACKLOG".to_owned(),
            summary: "BACKLOG 4 commits, oldest 37 min".to_owned(),
            detail: "2026-09-29T12:00:00Z BACKLOG 4 commits, oldest 37 min".to_owned(),
        };
        let mut data = sample_cockpit();
        data.attention = vec![item];
        let mut ui_state = UiState::new(false);
        ui_state.focus = FocusPane::Attention;
        let theme = Theme::new(true);
        let backend = TestBackend::new(360, 100);
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(Rect::new(0, 0, 360, 100)),
            },
        )
        .expect("terminal");

        terminal
            .draw(|frame| render_cockpit_with_theme(frame, &data, &ui_state, theme))
            .expect("draw");
        let buffer = terminal.backend().buffer();

        assert!(
            buffer.content().iter().any(|cell| {
                cell.symbol() == "BACKLOG" || (cell.symbol() == "B" && cell.fg == theme.yellow)
            }),
            "rendered backlog label should be present"
        );
        assert!(
            buffer
                .content()
                .iter()
                .any(|cell| cell.symbol() == "B" && cell.fg == theme.yellow),
            "backlog label should use the yellow attention color"
        );
    }

    #[test]
    fn attention_line_warns_when_idle_passes_three_quarters_of_stall_limit() {
        let mut data = sample_dashboard();
        data.last_attention_kind = None;
        data.last_attention = None;
        if let Some(state) = &mut data.state {
            state.stall_min = 45;
        }
        data.last_activity = Some(crate::age::unix_time(data.now) - 34 * 60);

        let backend = TestBackend::new(360, 100);
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(Rect::new(0, 0, 360, 100)),
            },
        )
        .expect("terminal");
        let ui_state = UiState::new(false);
        let theme = Theme::new(true);
        terminal
            .draw(|frame| render_dashboard_with_theme(frame, &data, &ui_state, theme))
            .expect("draw");
        let buffer = terminal.backend().buffer();
        let text = buffer_to_string(buffer);
        let attention = text.lines().nth(3).expect("attention row");

        assert!(attention.contains("IDLE"), "{attention}");
        assert!(attention.contains("34m0s / 45m"), "{attention}");
        assert!(
            buffer
                .content()
                .iter()
                .any(|cell| cell.symbol() == "━" && cell.fg == theme.yellow),
            "idle gauge should turn yellow past 75% of the stall limit"
        );
    }

    #[test]
    fn stats_count_lands_from_log_even_when_hour_buckets_are_empty() {
        let mut data = sample_dashboard();
        data.lands_per_hour = vec![0; 24];

        let text = render_to_string(&data, 360, 100, false).expect("wide render");

        assert!(text.contains("lands (log): 5"), "{text}");
    }

    #[test]
    fn commit_history_spreads_hourly_bars_across_the_panel_with_axis_and_now_marker() {
        let data = sample_dashboard();
        let backend = TestBackend::new(360, 100);
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(Rect::new(0, 0, 360, 100)),
            },
        )
        .expect("terminal");
        let ui_state = UiState::new(false);
        let theme = Theme::new(true);
        terminal
            .draw(|frame| render_dashboard_with_theme(frame, &data, &ui_state, theme))
            .expect("draw");
        let buffer = terminal.backend().buffer();
        let text = buffer_to_string(buffer);

        assert!(text.contains("max 2"), "{text}");
        assert!(text.contains("13"), "{text}");
        assert!(text.contains("16"), "{text}");
        assert!(text.contains("19"), "{text}");
        assert!(text.contains("22"), "{text}");
        assert!(text.contains("01"), "{text}");
        assert!(text.contains("04"), "{text}");
        assert!(text.contains("07"), "{text}");
        assert!(text.contains("10"), "{text}");
        assert!(text.contains("now 12"), "{text}");

        let bar_columns = text
            .lines()
            .filter(|line| line.contains('█'))
            .flat_map(|line| {
                line.char_indices()
                    .filter_map(|(col, ch)| (ch == '█').then_some(col))
            })
            .collect::<Vec<_>>();
        let left = bar_columns.iter().min().copied().expect("bar min column");
        let right = bar_columns.iter().max().copied().expect("bar max column");
        assert!(
            right.saturating_sub(left) >= 70,
            "bars should use the full chart panel width, got columns {left}..{right}\n{text}"
        );
        assert!(
            buffer
                .content()
                .iter()
                .any(|cell| cell.symbol() == "█" && cell.fg == theme.green),
            "recent hour bars should be brighter"
        );
    }

    #[test]
    fn messages_cap_long_comment_bodies() {
        let mut data = sample_dashboard();
        data.comments = vec![crate::tracker::TrackerComment {
            timestamp: "2026-09-29T12:34Z".to_owned(),
            file: "08-reseat-python-bound-natives.md".to_owned(),
            author: "TRAE".to_owned(),
            text: (1..=20)
                .map(|index| format!("body line {index:02}"))
                .collect::<Vec<_>>()
                .join("\n"),
        }];

        let text = render_to_string(&data, 180, 50, false).expect("medium render");

        assert!(text.contains("body line 01"), "{text}");
        assert!(text.contains("body line 12"), "{text}");
        assert!(!text.contains("body line 13"), "{text}");
        assert!(text.contains("…"), "{text}");
    }

    #[test]
    fn events_use_day_separators_short_times_and_indented_relay_blocks() {
        let data = sample_dashboard();
        let text = render_to_string(&data, 180, 50, false).expect("medium render");

        assert!(text.contains("2026-09-29"), "{text}");
        assert!(text.contains("12:05:00 MOVED a000005 -> beef001"), "{text}");
        assert!(!text.contains("2026-09-29T12:05:00Z MOVED"), "{text}");
        assert!(
            text.lines()
                .any(|line| line.contains("  REBASED (detached)")),
            "{text}"
        );
        assert!(
            text.lines().any(|line| line.contains("  VERIFY_PASSED")),
            "{text}"
        );
    }

    #[test]
    fn footer_is_full_width_key_hint_bar() {
        let data = sample_dashboard();
        let backend = TestBackend::new(100, 30);
        let mut terminal = Terminal::with_options(
            backend,
            TerminalOptions {
                viewport: Viewport::Fixed(Rect::new(0, 0, 100, 30)),
            },
        )
        .expect("terminal");
        let ui_state = UiState::new(false);
        let theme = Theme::new(true);
        terminal
            .draw(|frame| render_dashboard_with_theme(frame, &data, &ui_state, theme))
            .expect("draw");
        let buffer = terminal.backend().buffer();
        let text = buffer_to_string(buffer);
        let footer = text.lines().last().expect("footer line");

        assert!(footer.starts_with(" q "), "{footer:?}");
        assert!(footer.contains("Tab"), "{footer:?}");
        assert_eq!(buffer[(1, 29)].fg, theme.bg);
        assert_eq!(buffer[(1, 29)].bg, theme.accent);
        assert!(buffer[(1, 29)].modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn render_snapshots_cover_all_breakpoints() {
        let data = sample_dashboard();
        let narrow = render_to_string(&data, 100, 30, false).expect("narrow");
        let medium = render_to_string(&data, 180, 50, false).expect("medium");
        let wide = render_to_string(&data, 360, 100, false).expect("wide");
        if std::env::var_os("AUTOLAND_TUI_UPDATE_SNAPSHOTS").is_some() {
            let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("snapshots");
            std::fs::write(root.join("100x30.txt"), format!("{narrow}\n"))
                .expect("write narrow snapshot");
            std::fs::write(root.join("180x50.txt"), format!("{medium}\n"))
                .expect("write medium snapshot");
            std::fs::write(root.join("360x100.txt"), format!("{wide}\n"))
                .expect("write wide snapshot");
            return;
        }
        let saved_narrow = include_str!("../snapshots/100x30.txt");
        let saved_medium = include_str!("../snapshots/180x50.txt");
        let saved_wide = include_str!("../snapshots/360x100.txt");
        assert!(narrow.contains("Loop Events"));
        assert!(medium.contains("Messages"));
        assert!(wide.contains("Stats"));
        assert!(wide.contains("Follower Commits / Hour"));
        assert_eq!(body_column_count(&narrow), 1, "{narrow}");
        assert_eq!(body_column_count(&medium), 2, "{medium}");
        assert_eq!(body_column_count(&wide), 3, "{wide}");
        assert_eq!(narrow, saved_narrow.trim_end_matches('\n'));
        assert_eq!(medium, saved_medium.trim_end_matches('\n'));
        assert_eq!(wide, saved_wide.trim_end_matches('\n'));
    }

    fn body_column_count(rendered: &str) -> usize {
        rendered
            .lines()
            .find(|line| line.contains("Loop Events"))
            .expect("body title row")
            .matches('╭')
            .count()
    }
}
