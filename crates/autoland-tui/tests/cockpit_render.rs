use std::env;
use std::fs;
use std::path::Path;

use autoland_tui::data::{attention_items, sample_cockpit, UncommittedAge};
use autoland_tui::rollout::{CompactionEvent, RolloutSummary, TokenUsage};
use autoland_tui::ui::{render_cockpit_to_string, CockpitTab, PendingAction, UiState};

#[test]
fn cockpit_tabs_render_attention_queue_and_tab_specific_content() {
    let data = sample_cockpit();
    let cases = [
        (CockpitTab::Overview, " Overview ", "TRAE Mirror"),
        (CockpitTab::Trae, " TRAE ", "Fix py-protobuf"),
        (CockpitTab::Workers, " Workers ", "traecli-tui"),
        (
            CockpitTab::Outbox,
            " Outbox ",
            "py-protobuf belongs to ticket 09",
        ),
        (CockpitTab::Loop, " Loop ", "Loop Events"),
    ];

    for (tab, tab_label, expected) in cases {
        let text = render_cockpit_to_string(&data, 180, 50, false, tab).expect("render");
        assert!(text.contains("Attention Queue"), "{text}");
        assert!(text.contains(tab_label), "{text}");
        assert!(text.contains(expected), "{text}");
    }
}

#[test]
fn cockpit_send_confirm_modal_shows_exact_draft_text_and_idle_state() {
    let data = sample_cockpit();
    let mut state = UiState::new(false);
    state.tab = CockpitTab::Outbox;
    state.pending_action = Some(PendingAction::SendDraft(0));

    let text = autoland_tui::ui::render_cockpit_state_to_string(&data, 100, 30, &state)
        .expect("render modal");

    assert!(text.contains("Confirm Send"), "{text}");
    assert!(text.contains("Pane idle"), "{text}");
    assert!(text.contains("py-protobuf belongs to ticket 09"), "{text}");
    assert!(text.contains("y/Enter send"), "{text}");
}

#[test]
fn cockpit_attention_detail_modal_shows_full_decision_text() {
    let mut data = sample_cockpit();
    data.dashboard.last_attention_kind = None;
    data.dashboard.last_attention = None;
    data.dashboard.rollout = None;
    data.decisions =
        vec!["D1: short label.\nFull detail line one.\nFull detail line two.".to_owned()];
    data.attention =
        autoland_tui::data::attention_items(&data.dashboard, &data.outbox[..0], &data.decisions);
    let mut state = UiState::new(false);
    state.pending_action = Some(PendingAction::AttentionDetail(0));

    let text = autoland_tui::ui::render_cockpit_state_to_string(&data, 100, 30, &state)
        .expect("render modal");

    assert!(text.contains("Attention Detail"), "{text}");
    assert!(text.contains("D1: short label."), "{text}");
    assert!(text.contains("Full detail line one."), "{text}");
    assert!(text.contains("Full detail line two."), "{text}");
}

#[test]
fn cockpit_attention_queue_shows_only_decision_label() {
    let mut data = sample_cockpit();
    data.dashboard.last_attention_kind = None;
    data.dashboard.last_attention = None;
    data.decisions =
        vec!["D1: short label.\nFull detail line one.\nFull detail line two.".to_owned()];
    data.attention =
        autoland_tui::data::attention_items(&data.dashboard, &data.outbox[..0], &data.decisions);

    let text =
        render_cockpit_to_string(&data, 100, 30, false, CockpitTab::Overview).expect("render");

    assert!(text.contains("DECISION D1: short label."), "{text}");
    assert!(!text.contains("Full detail line one."), "{text}");
}

#[test]
fn overview_summarizes_structured_rollout_before_the_pane_mirror() {
    let mut data = sample_cockpit();
    data.dashboard.rollout = Some(RolloutSummary::from_lines(
        include_str!("fixtures/trae-rollout-small.jsonl").lines(),
    ));
    data.attention = attention_items(&data.dashboard, &data.outbox, &data.decisions);

    let text =
        render_cockpit_to_string(&data, 180, 50, false, CockpitTab::Overview).expect("render");

    assert!(text.contains("TRAE Session"), "{text}");
    assert!(text.contains("py-tqdm guard failure"), "{text}");
    assert!(text.contains("cargo test --offline"), "{text}");
    assert!(text.contains("rc=1"), "{text}");
    assert!(text.contains("src/rollout.rs"), "{text}");
    assert!(text.contains("context left 20%"), "{text}");
}

#[test]
fn trae_tab_shows_rollout_details_and_keeps_pane_mirror_secondary() {
    let mut data = sample_cockpit();
    data.dashboard.rollout = Some(RolloutSummary::from_lines(
        include_str!("fixtures/trae-rollout-small.jsonl").lines(),
    ));
    data.attention = attention_items(&data.dashboard, &data.outbox, &data.decisions);

    let text = render_cockpit_to_string(&data, 180, 50, false, CockpitTab::Trae).expect("render");

    assert!(text.contains("TRAE Session"), "{text}");
    assert!(text.contains("Agent Messages"), "{text}");
    assert!(text.contains("Commands"), "{text}");
    assert!(text.contains("File Changes"), "{text}");
    assert!(text.contains("Compactions"), "{text}");
    assert!(text.contains("Pane Mirror"), "{text}");
}

#[test]
fn attention_queue_shows_compacted_context_low_and_uncommitted_age() {
    let mut data = sample_cockpit();
    let now_ms = autoland_tui::age::unix_time(data.dashboard.now) * 1_000;
    let mut rollout = RolloutSummary::default();
    rollout.token = Some(TokenUsage {
        timestamp: Some("2026-09-29T12:30:00.000Z".to_owned()),
        input_tokens: 160_000,
        output_tokens: 500,
        reasoning_output_tokens: 100,
        total_tokens: 160_500,
        model_context_window: 200_000,
        auto_compact_token_limit: Some(170_000),
    });
    rollout.compactions.push(CompactionEvent {
        timestamp: Some("2026-09-29T12:35:00.000Z".to_owned()),
        completed_at_ms: Some(now_ms.saturating_sub(5 * 60 * 1_000)),
        summary: "context compacted".to_owned(),
    });
    data.dashboard.rollout = Some(rollout);
    data.dashboard.uncommitted = Some(UncommittedAge {
        count: 2,
        oldest_minutes: 190,
        newest_minutes: 70,
        threshold_minutes: 60,
    });
    data.dashboard.last_attention_kind = None;
    data.dashboard.last_attention = None;
    data.attention = attention_items(&data.dashboard, &data.outbox[..0], &[]);

    let text =
        render_cockpit_to_string(&data, 180, 50, false, CockpitTab::Overview).expect("render");

    assert!(text.contains("COMPACTED"), "{text}");
    assert!(text.contains("CONTEXT LOW"), "{text}");
    assert!(text.contains("UNCOMMITTED_AGE"), "{text}");
    assert!(text.contains("2 files, oldest 190 min"), "{text}");
}

#[test]
fn cockpit_snapshots_cover_each_tab_at_three_sizes() {
    let data = sample_cockpit();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("snapshots");
    for tab in CockpitTab::ALL {
        for (width, height) in [(100, 30), (180, 50), (360, 100)] {
            let text = render_cockpit_to_string(&data, width, height, false, tab).expect("render");
            assert!(text.contains("COCKPIT"), "{text}");
            assert!(text.contains("Attention Queue"), "{text}");
            let name = format!("tab{}-{}x{}.txt", tab.number(), width, height);
            let path = root.join(name);
            if env::var_os("AUTOLAND_TUI_UPDATE_SNAPSHOTS").is_some() {
                fs::write(path, format!("{text}\n")).expect("write snapshot");
            } else {
                let saved = fs::read_to_string(&path).expect("read snapshot");
                assert_eq!(text, saved.trim_end_matches('\n'), "{}", path.display());
            }
        }
    }
}
