use ratatui::style::Color;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Theme {
    pub truecolor: bool,
    pub bg: Color,
    pub panel: Color,
    pub panel_dim: Color,
    pub text: Color,
    pub muted: Color,
    pub accent: Color,
    pub green: Color,
    pub blue: Color,
    pub cyan: Color,
    pub red: Color,
    pub yellow: Color,
    pub magenta: Color,
}

impl Theme {
    pub fn detect() -> Self {
        let truecolor = std::env::var("COLORTERM")
            .map(|value| value == "truecolor" || value == "24bit")
            .unwrap_or(false);
        Self::new(truecolor)
    }

    pub const fn new(truecolor: bool) -> Self {
        if truecolor {
            Self {
                truecolor,
                bg: Color::Rgb(17, 17, 27),
                panel: Color::Rgb(24, 24, 37),
                panel_dim: Color::Rgb(30, 30, 46),
                text: Color::Rgb(205, 214, 244),
                muted: Color::Rgb(127, 132, 156),
                accent: Color::Rgb(137, 180, 250),
                green: Color::Rgb(166, 227, 161),
                blue: Color::Rgb(137, 180, 250),
                cyan: Color::Rgb(148, 226, 213),
                red: Color::Rgb(243, 139, 168),
                yellow: Color::Rgb(249, 226, 175),
                magenta: Color::Rgb(203, 166, 247),
            }
        } else {
            Self {
                truecolor,
                bg: Color::Black,
                panel: Color::Black,
                panel_dim: Color::DarkGray,
                text: Color::White,
                muted: Color::DarkGray,
                accent: Color::Blue,
                green: Color::Green,
                blue: Color::Blue,
                cyan: Color::Cyan,
                red: Color::Red,
                yellow: Color::Yellow,
                magenta: Color::Magenta,
            }
        }
    }
}
