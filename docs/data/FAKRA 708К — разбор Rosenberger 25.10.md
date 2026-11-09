# 708К (ЯНДЕКС) — Rosenberger FAKRA / HFM on RTK 031: research notes 25.10.2026

Folders: drive/ = customer drawings from Google Drive; web/ = maker PDFs; png/ = rendered pages; *.txt = extracted text.

## Parts
- 59K13O-1M4A4 (letter O; customer writes "59K13o"): FAKRA-HF straight JACK (female, socket centre contact), insert only.
  Kit = centre contact 59K13D-1M4/20E (stamped & formed, open crimp barrel, 8.7 mm) + outer body 59K13O-1M4/91
  + crimp ferrule 59K102-1M4/41A (Cu/Ni, Ø5 / Ø4.1 / bore Ø3.6, 10 mm). DC–6 GHz. Assy MA_59V059.
  Tools: centre — 11W161-8M4 (hand, ch 0.88±0.03); ferrule — 11W150-000 + insert 11W150-104 (hex 4.30).
- 59Z163-003-Z: waterproof plastic housing, single, code Z water blue RAL 5021, PBT-GF30, green silicone ring,
  BeCu lock ring, 33×13.4×9.4, latch 110 N, max 5 matings. Assy MA_59V111 (NOT FOUND). Needs 59K14B + 59Z163-000/51.
- 59K14B-1M4/50: blue silicone cable seal for RTK031, cable Ø3.2–3.7, bore Ø2.8, Ø9/7.2/6, L 7.8.
- 59Z163-000/51: grey PBT sealing cap Ø12.05×8, locks the seal into 59Z163-003, retention ≥50 N.
- 59Z006-T00-Z: plastic housing for right-angle jack, 360° turnable, POM, with secondary lock; latch 110 N,
  primary 80 N, secondary 60 N.
- 59K25H-1M4A4: FAKRA-HF right-angle JACK 360°, body brass, separate centre pin (crimped, not soldered) + cover.
  Tool 11W150-000 + insert 11W150-504 (outer 4.30 / centre 1.10). Assy MA_59V065.
- AMK11D-1M4Z5-Z: HFM (High-speed FAKRA Mini) straight JACK with QUAD housing = housing AMZ025-000-Z
  + 4× AMK12A-1M4Z5 jack contacts, secondary lock (purple), CPA (red). DC–15 GHz. Assy MA_AMV003.
  Series tools 11WS001_A (support sleeve) / 11WB013_A (centre) / 11WR024_A (outer); hand kit AMW001-SET.
- 2FA1-NZSP-C30E2 (Amphenol RF): FAKRA Gen2 straight PLUG, rear-mount bulkhead, IP67, Z, zinc body, 1/2-28 UNEF,
  hex nut 15.88, panel ≤4 mm, hole Ø13 with flat 11.6; for TFC 302LL / Dacar 302 / RTK 031.
  Centre = solder bullet contact; braid crimp ferrule hex .178" (4.52 mm); adhesive heat shrink over ferrule.
- RTK 031 (Caleb sheet): inner 7×0.27 Cu, FPE dielectric Ø2.1, Al/PET/Al foil + tinned braid 0.1×16×7,
  PVC jacket Ø3.2±0.1, 50±2 Ω, 85 nF/km; 437 dB/km @800 MHz, 881 @3 GHz. Rosenberger now calls group M4 "Dacar 302".

## Strip / crimp numbers (RTK 031, group M4)
- 59K13O straight (MA_59V059): X 9.7±0.2 jacket, Y 4.6±0.2 dielectric+conductor, Z 3.0±0.2 bare conductor.
  Centre crimp cbm 1.34±0.05 / ch 0.88±0.03 (hand) or 0.99±0.03 (applicator), pull >60 N.
  Contact recess after insertion 0.18 +0.5; push-in ≤20 N, hold ≥10 N, check pull ≤5 N.
  Ferrule hex C-C 4.3±0.05 (D-D n/a), gap to body 0.1±0.1, pull >110 N. Insert-in-housing 2.05±0.15.
- 59K25H angled (MA_59V065): X 11.6±0.2, Y 6.4±0.2, Z 1.7±0.2; die 11W150-504; hex 4.3 -0.05; pin square 1.1 -0.03;
  dielectric-to-pin gap ≤0.3; ferrule gap 0.1 +0.1/-0.1; cover F pressed flush.
- AMK12A HFM (MA_AMV003): sheath cut a 13; support sleeve v 3.0×3.05, r 0.65±0.1, s 1.75±0.2, position 3.7–4.7,
  calibration 9±0.2; y 5.55±0.2, z 2.1±0.2, w ≤4.5, v 7.85±0.2; centre x 15.9±0.2, crimp 1.28×0.9, >60 N;
  insert to 1.05±0.15; outer crimps k 3.7×3.65, t 3.45×3.35, l 3.8×3.8, >120 N; gauge Ø3.54 / max Ø3.6;
  contact in housing 2.05 +0.15/-0.10.
- 2FA1 (Amphenol drawing): from tip — conductor 2.7, dielectric 1.0, foil to 8.5 (left on), braid to 13.9.

## Calc 708К findings
- КБ4: block AMK11D×2 + ТУТ + Маркировка duplicated (copy mistake).
- КБ28–КБ40: no "Маркировка" rows although 122663 requires 3 labels.
- White marker rows sit on КБ16 and КБ35; they belong to КБ15 (Camera 3) and КБ34 (Camera 1).
- КБ1 and КБ2 both "Маркер розовый Оранжевый": drawing — КБ1 orange OG, КБ2 pink PK.
- КБ41 = 121501 harness (TZTEK 818018737 quad mini-FAKRA, unidentified).
- КБn sheets plan no operations (only BUY/PREP/OTK/BOX).
- Витя's sheet: RTK 031 "Ø ~2.5 mm" is wrong — 3.2±0.1.
