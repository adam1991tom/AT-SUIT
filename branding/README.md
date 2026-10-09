# AT-SUIT brand pack

![AT-SUIT](social/readme-banner.png)

The AT-SUIT logo, app icons, social images, colours and brand guide. AT-SUIT is a member of the AT family (AT GROUP, AT SOLUTIONS, AT NET, AT EVENTS, AT HANDYMAN). It uses the same C-shaped gear and lettering as the rest of the family, and its colour is **AT-SUIT Orange #FF7A1A**.

Read the brand guide before using the logo: [`manual/index.html`](manual/index.html) (also available as [`manual/AT-SUIT-brand-guide.pdf`](manual/AT-SUIT-brand-guide.pdf), A4 landscape).

## Colours

| Name | Hex | RGB | Use |
|---|---|---|---|
| AT-SUIT Orange | `#FF7A1A` | 255 122 26 | The gear, primary accent |
| Night Navy | `#0B1020` | 11 16 32 | Brand background, logo plate, banners |
| App Dark | `#0E1116` | 14 17 22 | App UI background |
| Outline Black | `#000000` | 0 0 0 | Logo outlines |
| Letter White | `#FFFFFF` | 255 255 255 | Logo letters and white band |
| Ember | `#C85400` | 200 84 0 | Pressed state, orange text on white |
| Glow | `#FFB27A` | 255 178 122 | Tints on dark UI |

The values can also be read by code from [`colours.json`](colours.json).

## Files

| Folder | File | Use |
|---|---|---|
| `logo/` | `atsuit-wordmark.svg/.png` (2000×760) | Primary logo, transparent background |
| | `atsuit-icon.svg/.png` (1024) | Icon (gear + AT), transparent background |
| | `atsuit-stacked.svg/.png` (1200×1400) | Icon above "AT SUIT" |
| | `*-on-dark.*` / `*-on-light.*` | The same logos on a navy (#0B1020) or white plate |
| | `*-black.*` / `*-white.*` | One-colour versions (icon, wordmark, stacked) |
| | `atsuit-gear.svg/.png` | Gear only, for loaders and watermarks |
| `app/` | `icon.ico` | Windows icon: 16, 24, 32 (simplified mark) and 48, 64, 128, 256 |
| | `icon.png` (512), `icon-1024.png`, `icon-512.png`, `icon-192.png` | PWA, Linux, store listings |
| | `favicon.svg`, `favicon.ico` (16/32/48) | Browser tab (simplified mark) |
| | `apple-touch-icon.png` (180, navy plate) | iOS home screen |
| | `tray-16.png`, `tray-32.png` | System tray (simplified mark) |
| | `icon-small.svg` | Source for the simplified small mark |
| `social/` | `github-social-preview.png` (1280×640) | GitHub → Settings → Social preview |
| | `readme-banner.png` (1600×400) | Top of the README |
| `manual/` | `index.html`, `AT-SUIT-brand-guide.pdf`, `img/` | Brand guide and its illustrations |
| `fonts/` | `saira-800.ttf`, `saira-700.ttf`, `OFL.txt` | Lettering source fonts and their licence |

To add the icons to a web page:

```html
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="alternate icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
```

## Regenerating

Every file in this pack is built from geometry by one script. To change the logo, edit `make_brand.py` and run it again. Don't edit the exported files by hand.

```sh
cd branding
python3 make_brand.py            # rebuilds everything and copies the pack to /mnt/project-files/branding
python3 make_brand.py --no-share # rebuilds without copying
```

The script needs:
- Python 3 with `shapely`, `fontTools` and `Pillow`
- Node with Playwright, loaded from `../node-app/node_modules/playwright`, and the Chromium binary at `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`. Both paths are set in `render_all()` and `pdf_from_html()`.

How it works: the gear is a circle, minus 8 scoop circles at 45° steps, minus the inner circle, minus the C opening. The outline layers are made with `buffer()`: black is the shape grown by 0.027R, white is the shape itself, and orange is the shape shrunk by 0.024R. The letters are Saira outlines, flattened to polygons and spaced optically, with a black outline of 0.026R. Every SVG is pure paths, so no font is needed to display it. Chromium renders the PNGs and the PDF, and Pillow builds the `.ico` files.

## Font licence

The lettering is built from **Saira** ExtraBold (800) and Bold (700), Copyright 2020 The Saira Project Authors (https://github.com/Omnibus-Type/Saira). Saira is licensed under the **SIL Open Font License, Version 1.1**. The full text is in [`fonts/OFL.txt`](fonts/OFL.txt). The OFL allows the font to be used in logos and bundled with software. The logo files themselves contain only outlines and do not embed the font.
