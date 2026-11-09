# OpenRemote desktop — design QA (v0.6.2)

The final implementation is **native PySide6/Qt**, not a static mockup.
This QA compares the running Linux UI to the two user-approved visual
references for **Devices** and **Settings**. The references are a visual
direction, not an exact macOS window-manager specification.

## Evidence: real UI, privacy-safe sample data

These images were captured from the real Qt widgets with a local, synthetic
UI snapshot. No SSH, real runtime API keys, saved private configuration or
live system data are present in these published images. Online indicators
in these specific screenshots are **illustrative**, not proof of connection.

| Screen | Result | Screenshot |
| --- | --- | --- |
| 1. Devices / light / Russian | Pass: defined cards, real monitor icons, visible state badges, primary Verify button | [Devices screenshot](assets/control-center-devices-ru.png) |
| 2. Settings / light / Russian | Pass: custom RU/EN menu, check controls, aligned connection rows, styled copy button | [Settings screenshot](assets/control-center-settings-ru.png) |
| 3. Settings / dark / English | Pass: consistent theme and typography; no native bright dropdown | [Dark settings screenshot](assets/control-center-settings-dark-en.png) |
| 4. Jobs / light / Russian | Pass: consistent job card, status, copyable ID | Generated in the visual QA set |
| 5. Transfers / light / Russian | Pass: empty state, transfer progress styling | Generated in the visual QA set |

## Concrete fixes compared with the earlier screenshots

1. **Contrast and hierarchy.** White/lavender cards now have a consistent
   border and subtle drop shadow. Cards are not the same color as the
   content background; text is dark enough to remain visually distinct.
2. **Icons.** Replaced placeholder geometric glyphs with Qt-painted vector
   monitor, folder, CPU, server, task, transfer, settings, copy and shield
   icons. No dependency on platform emoji fonts.
3. **Buttons.** Device connection verification is a real, filled purple
   QPushButton with icon, hover, disabled and pressed states.
4. **Settings.** Replaced native-looking QComboBox with a styled
   QPushButton/QMenu language selector; styled checkbox indicators and
   added minimum row heights and fixed-grid columns to avoid overlapping
   labels or text clipping.
5. **Qt style-cascade bug.** Removed inner-widget stylesheets that
   suppressed inherited QPushButton/QFrame styles. This was the root
   cause of "text-only buttons" and invisible cards.
6. **Ghost widgets and scrollbars.** Old Qt controls now detach and hide
   immediately before DeferredDelete. Scrollbar arrows and native groove
   are suppressed; the custom narrow scrollbar matches the palette.
7. **Responsive layout.** Device metrics move from three to two columns
   at smaller window widths; tested at 900 and 1250 logical pixels.
8. **Languages and dark theme.** Russian and English are both supported,
   including language selection without rebuilding the remote agent.

## Behavioral checks

- Keyboard-operable Qt QPushButtons, QCheckBoxes, and QMenu actions.
- User preferences are stored with QSettings, not in the tunnel config.
- Repeated rendering and language/theme switches do not leave overlapping
  QWidget cards after delayed Qt paint events.
- Every screenshot is from actual running PySide6 widgets. All four
  navigation screens were captured and reviewed in light mode. Devices
  and Settings were also reviewed in dark mode and English.
- On a real GNOME Wayland desktop, the tray is available and the GUI
  process remains separate from the working MCP/tunnel services.
- Qt offscreen images are not a substitute for manual keyboard-only
  testing, every screen-reader combination, or all desktop themes.
  WCAG conformance is not claimed.

## Reproduce screenshots

Run on a Linux system with the GUI dependency installed:

```bash
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python \
  scripts/gui_visual_snapshots.py --output /tmp/openremote-ui-qa \
  --width 1400 --height 960
```

The sample generator intentionally makes **no network requests** and never
opens any user's private agent configuration.
